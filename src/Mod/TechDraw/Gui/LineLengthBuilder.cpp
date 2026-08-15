/***************************************************************************
 *   Copyright (c) 2026 VibeCAD contributors                              *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public License   *
 *   as published by the Free Software Foundation; either version 2 of     *
 *   the License, or (at your option) any later version.                    *
 ***************************************************************************/

#include "LineLengthBuilder.h"

#include <cmath>
#include <optional>
#include <string>

#include <Base/Exception.h>
#include <Mod/TechDraw/App/CenterLine.h>
#include <Mod/TechDraw/App/Cosmetic.h>
#include <Mod/TechDraw/App/CosmeticVertex.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Geometry.h>


namespace
{

using TechDrawGui::DrawingLineKind;
using TechDrawGui::DrawingLineLengthOperation;
using TechDrawGui::DrawingLineLengthState;
using TechDrawGui::DrawingLineTarget;

constexpr double MaximumDeltaDistanceMm = 1'000'000.0;
constexpr double MaximumCoordinateMm = 1'000'000'000.0;

void requireLiveView(TechDraw::DrawViewPart* view)
{
    if (!view || !view->getDocument() || !view->findParentPage()) {
        throw Base::ValueError("Drawing line length requires a live view on a page");
    }
}

std::string kindName(DrawingLineKind kind)
{
    return kind == DrawingLineKind::CosmeticEdge ? "cosmetic edge" : "centerline";
}

TechDraw::SourceType sourceType(DrawingLineKind kind)
{
    return kind == DrawingLineKind::CosmeticEdge
        ? TechDraw::SourceType::COSMETICEDGE
        : TechDraw::SourceType::CENTERLINE;
}

bool persistentTargetExists(
    TechDraw::DrawViewPart* view,
    const DrawingLineTarget& target)
{
    return target.kind == DrawingLineKind::CosmeticEdge
        ? view->getCosmeticEdge(target.tag) != nullptr
        : view->getCenterLine(target.tag) != nullptr;
}

struct ProjectedLine
{
    TechDraw::BaseGeomPtr geometry;
    std::string selectionName;
};

ProjectedLine projectedLine(
    TechDraw::DrawViewPart* view,
    const DrawingLineTarget& target,
    bool required)
{
    if (target.tag.empty() || !persistentTargetExists(view, target)) {
        if (required) {
            throw Base::ValueError(
                "The exact Drawing " + kindName(target.kind) + " target is unavailable");
        }
        return {};
    }
    ProjectedLine result;
    const auto geometry = view->getEdgeGeometry();
    for (std::size_t index = 0; index < geometry.size(); ++index) {
        const auto& edge = geometry.at(index);
        if (!edge || edge->source() != sourceType(target.kind)
            || edge->getCosmeticTag() != target.tag) {
            continue;
        }
        if (result.geometry) {
            throw Base::RuntimeError(
                "The exact Drawing line target resolves to multiple projected edges");
        }
        result.geometry = edge;
        result.selectionName = "Edge" + std::to_string(index);
    }
    if ((!result.geometry || result.geometry->getGeomType() != TechDraw::GeomType::GENERIC
         || !result.geometry->getCosmetic())
        && required) {
        throw Base::ValueError(
            "The exact Drawing target must be a projected straight cosmetic line or centerline");
    }
    if (!result.geometry || result.geometry->getGeomType() != TechDraw::GeomType::GENERIC
        || !result.geometry->getCosmetic()) {
        return {};
    }
    return result;
}

bool finitePoint(const Base::Vector3d& point)
{
    return std::isfinite(point.x) && std::isfinite(point.y) && std::isfinite(point.z)
        && std::abs(point.x) <= MaximumCoordinateMm
        && std::abs(point.y) <= MaximumCoordinateMm
        && std::abs(point.z) <= MaximumCoordinateMm;
}

std::optional<DrawingLineLengthState> lineState(
    TechDraw::DrawViewPart* view,
    const DrawingLineTarget& target,
    bool required)
{
    const ProjectedLine projected = projectedLine(view, target, required);
    if (!projected.geometry) {
        return std::nullopt;
    }
    const Base::Vector3d start =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            projected.geometry->getStartPoint());
    const Base::Vector3d end =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            projected.geometry->getEndPoint());
    const double length = (end - start).Length();
    if (!finitePoint(start) || !finitePoint(end) || !std::isfinite(length)
        || length <= Base::Vector3d::epsilon()) {
        if (required) {
            throw Base::ValueError("The exact Drawing line has invalid or zero-length geometry");
        }
        return std::nullopt;
    }
    const bool centerLine = target.kind == DrawingLineKind::CenterLine;
    const auto* center = centerLine ? view->getCenterLine(target.tag) : nullptr;
    if (centerLine && (!center || !std::isfinite(center->m_extendBy))) {
        if (required) {
            throw Base::ValueError("The exact Drawing centerline has invalid extension state");
        }
        return std::nullopt;
    }
    return DrawingLineLengthState {
        target,
        projected.selectionName,
        start,
        end,
        length,
        centerLine,
        center ? center->m_extendBy : 0.0,
    };
}

void requireDelta(double deltaDistanceMm)
{
    if (!std::isfinite(deltaDistanceMm) || deltaDistanceMm <= 0.0
        || deltaDistanceMm > MaximumDeltaDistanceMm) {
        throw Base::ValueError(
            "Drawing line delta distance must be greater than 0 and at most 1000000 mm");
    }
}

}  // namespace

std::vector<TechDrawGui::DrawingLineLengthState>
TechDrawGui::drawingLineLengthStates(TechDraw::DrawViewPart* view)
{
    requireLiveView(view);
    std::vector<DrawingLineLengthState> result;
    const auto cosmeticEdges = view->CosmeticEdges.getValues();
    const auto centerLines = view->CenterLines.getValues();
    result.reserve(cosmeticEdges.size() + centerLines.size());
    for (const auto* edge : cosmeticEdges) {
        if (!edge) {
            continue;
        }
        auto state = lineState(
            view,
            {DrawingLineKind::CosmeticEdge, edge->getTagAsString()},
            false);
        if (state) {
            result.push_back(std::move(*state));
        }
    }
    for (const auto* line : centerLines) {
        if (!line) {
            continue;
        }
        auto state = lineState(
            view,
            {DrawingLineKind::CenterLine, line->getTagAsString()},
            false);
        if (state) {
            result.push_back(std::move(*state));
        }
    }
    return result;
}

TechDrawGui::DrawingLineLengthState TechDrawGui::changeDrawingLineLength(
    TechDraw::DrawViewPart* view,
    const DrawingLineTarget& target,
    DrawingLineLengthOperation operation,
    double deltaDistanceMm)
{
    requireLiveView(view);
    requireDelta(deltaDistanceMm);
    const DrawingLineLengthState before = *lineState(view, target, true);
    if (operation == DrawingLineLengthOperation::Shorten
        && 2.0 * deltaDistanceMm >= before.lengthMm) {
        throw Base::ValueError(
            "Drawing line shortening distance must be less than half the current line length");
    }

    Base::Vector3d direction = before.endInViewMm - before.startInViewMm;
    direction.Normalize();
    const Base::Vector3d delta = direction * deltaDistanceMm;
    const bool extend = operation == DrawingLineLengthOperation::Extend;
    const Base::Vector3d newStart =
        extend ? before.startInViewMm - delta : before.startInViewMm + delta;
    const Base::Vector3d newEnd =
        extend ? before.endInViewMm + delta : before.endInViewMm - delta;
    if (!finitePoint(newStart) || !finitePoint(newEnd)) {
        throw Base::ValueError("Drawing line extension would exceed supported coordinates");
    }

    if (target.kind == DrawingLineKind::CosmeticEdge) {
        view->CosmeticEdges.setValues(view->CosmeticEdges.getValues());
        auto* edge = view->getCosmeticEdge(target.tag);
        if (!edge) {
            throw Base::RuntimeError("The validated cosmetic line disappeared");
        }
        edge->permaStart = newStart;
        edge->permaEnd = newEnd;
        edge->m_geometry = TechDraw::CosmeticEdge::makeLineFromCanonicalPoints(
            newStart,
            newEnd);
        view->CosmeticEdges.setValues(view->CosmeticEdges.getValues());
    }
    else {
        view->CenterLines.setValues(view->CenterLines.getValues());
        auto* centerLine = view->getCenterLine(target.tag);
        if (!centerLine) {
            throw Base::RuntimeError("The validated centerline disappeared");
        }
        const double signedDelta = extend ? deltaDistanceMm : -deltaDistanceMm;
        if (!std::isfinite(centerLine->m_extendBy + signedDelta)
            || std::abs(centerLine->m_extendBy + signedDelta) > MaximumCoordinateMm) {
            throw Base::ValueError("Drawing centerline extension would exceed supported bounds");
        }
        centerLine->m_extendBy += signedDelta;
        view->CenterLines.setValues(view->CenterLines.getValues());
    }
    // Both persistent lists share one projected EdgeN sequence.  Rebuild them
    // together so resizing either kind cannot renumber the non-target kind.
    view->refreshCEGeoms();
    view->refreshCLGeoms();
    view->requestPaint();
    return *lineState(view, target, true);
}
