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

#include "CosmeticLineBuilder.h"

#include <cmath>
#include <memory>
#include <regex>

#include <Base/Exception.h>
#include <Mod/TechDraw/App/Cosmetic.h>
#include <Mod/TechDraw/App/CosmeticVertex.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Geometry.h>


namespace
{

using TechDrawGui::DrawingCosmeticLineConstruction;
using TechDrawGui::DrawingCosmeticLinePlan;
using TechDrawGui::DrawingPersistentCosmeticLineState;

constexpr double MaximumCoordinateMm = 1'000'000'000.0;
const std::regex EdgeNamePattern("^Edge(?:0|[1-9][0-9]*)$");
const std::regex VertexNamePattern("^Vertex(?:0|[1-9][0-9]*)$");

void requireLiveView(TechDraw::DrawViewPart* view)
{
    auto* page = view ? view->findParentPage() : nullptr;
    if (!view || !view->getDocument() || !page
        || page->getDocument() != view->getDocument()) {
        throw Base::ValueError(
            "Cosmetic parallel and perpendicular lines require a live Drawing view on a page");
    }
    const double scale = view->getScale();
    if (!std::isfinite(scale) || scale <= Base::Vector3d::epsilon()) {
        throw Base::ValueError("The Drawing view has an invalid scale");
    }
}

bool finitePoint(const Base::Vector3d& point)
{
    return std::isfinite(point.x) && std::isfinite(point.y)
        && std::isfinite(point.z) && std::abs(point.x) <= MaximumCoordinateMm
        && std::abs(point.y) <= MaximumCoordinateMm
        && std::abs(point.z) <= MaximumCoordinateMm;
}

void requirePoint(const Base::Vector3d& point, const char* noun)
{
    if (!finitePoint(point) || std::abs(point.z) > Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            std::string("The cosmetic line ") + noun
            + " exceeds supported two-dimensional coordinates");
    }
}

Base::Vector3d conventionalPoint(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& projectedPoint)
{
    const Base::Vector3d result = TechDraw::DrawUtil::invertY(
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            projectedPoint));
    requirePoint(result, "point");
    return result;
}

struct ExactSources
{
    std::string edgeName;
    std::string vertexName;
};

ExactSources exactSources(const std::vector<std::string>& names)
{
    if (names.size() != 2 || names.at(0) == names.at(1)) {
        throw Base::ValueError(
            "Select exactly one projected straight EdgeN and one projected VertexN");
    }
    ExactSources result;
    for (const auto& name : names) {
        if (std::regex_match(name, EdgeNamePattern) && result.edgeName.empty()) {
            result.edgeName = name;
        }
        else if (std::regex_match(name, VertexNamePattern)
                 && result.vertexName.empty()) {
            result.vertexName = name;
        }
        else {
            throw Base::ValueError(
                "Select exactly one projected straight EdgeN and one projected VertexN");
        }
    }
    if (result.edgeName.empty() || result.vertexName.empty()) {
        throw Base::ValueError(
            "Select exactly one projected straight EdgeN and one projected VertexN");
    }
    return result;
}

std::string selectionNameForTag(
    TechDraw::DrawViewPart* view,
    const std::string& tag)
{
    std::string result;
    const auto geometry = view->getEdgeGeometry();
    for (std::size_t index = 0; index < geometry.size(); ++index) {
        const auto& edge = geometry.at(index);
        if (!edge || edge->source() != TechDraw::SourceType::COSMETICEDGE
            || edge->getCosmeticTag() != tag) {
            continue;
        }
        if (!result.empty()) {
            throw Base::RuntimeError(
                "A cosmetic line resolves to multiple projected edges");
        }
        result = "Edge" + std::to_string(index);
    }
    if (result.empty()) {
        throw Base::RuntimeError(
            "A cosmetic line is missing from projected geometry");
    }
    return result;
}

DrawingPersistentCosmeticLineState persistentState(
    TechDraw::DrawViewPart* view,
    TechDraw::CosmeticEdge* edge)
{
    if (!edge || !edge->m_geometry
        || edge->m_geometry->getGeomType() != TechDraw::GeomType::GENERIC) {
        throw Base::ValueError(
            "The exact persistent cosmetic line target is unavailable");
    }
    const Base::Vector3d start =
        TechDraw::DrawUtil::invertY(edge->permaStart);
    const Base::Vector3d end =
        TechDraw::DrawUtil::invertY(edge->permaEnd);
    const double length = (end - start).Length();
    requirePoint(start, "start point");
    requirePoint(end, "end point");
    if (!std::isfinite(length) || length <= Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            "The persistent cosmetic line has invalid or zero-length geometry");
    }
    const std::string tag = edge->getTagAsString();
    if (tag.empty()) {
        throw Base::RuntimeError(
            "A persistent cosmetic line has no durable tag");
    }
    return {
        tag,
        selectionNameForTag(view, tag),
        start,
        end,
        length,
        edge->m_format};
}

void finishMutation(TechDraw::DrawViewPart* view)
{
    view->CosmeticEdges.setValues(view->CosmeticEdges.getValues());
    view->refreshCEGeoms();
    view->requestPaint();
}

}  // namespace

TechDrawGui::DrawingCosmeticLinePlan
TechDrawGui::validateDrawingCosmeticLine(
    TechDraw::DrawViewPart* view,
    DrawingCosmeticLineConstruction construction,
    const std::vector<std::string>& sourceSelectionNames)
{
    requireLiveView(view);
    const ExactSources sources = exactSources(sourceSelectionNames);
    const auto geometry = view->getGeomByIndex(
        TechDraw::DrawUtil::getIndexFromName(sources.edgeName));
    const auto line = std::dynamic_pointer_cast<TechDraw::Generic>(geometry);
    const auto vertex = view->getProjVertexByIndex(
        TechDraw::DrawUtil::getIndexFromName(sources.vertexName));
    if (!line || line->points.size() < 2 || !vertex) {
        throw Base::ValueError(
            "The cosmetic-line reference must be a valid projected straight edge and vertex");
    }

    const Base::Vector3d referenceStart =
        conventionalPoint(view, line->points.at(0));
    const Base::Vector3d referenceEnd =
        conventionalPoint(view, line->points.at(1));
    const Base::Vector3d throughPoint =
        conventionalPoint(view, vertex->point());
    Base::Vector3d halfVector = (referenceEnd - referenceStart) / 2.0;
    const double length = 2.0 * halfVector.Length();
    if (!std::isfinite(length) || length <= Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            "The selected reference line has no usable length");
    }
    if (construction == DrawingCosmeticLineConstruction::Perpendicular) {
        halfVector = Base::Vector3d(halfVector.y, -halfVector.x, 0.0);
    }
    const Base::Vector3d start = throughPoint + halfVector;
    const Base::Vector3d end = throughPoint - halfVector;
    requirePoint(start, "start point");
    requirePoint(end, "end point");
    return {
        construction,
        sources.edgeName,
        sources.vertexName,
        referenceStart,
        referenceEnd,
        throughPoint,
        start,
        end,
        length,
        TechDraw::LineFormat::getCurrentLineFormat()};
}

TechDrawGui::DrawingCosmeticLineResult
TechDrawGui::createDrawingCosmeticLine(
    TechDraw::DrawViewPart* view,
    DrawingCosmeticLineConstruction construction,
    const std::vector<std::string>& sourceSelectionNames)
{
    const DrawingCosmeticLinePlan plan = validateDrawingCosmeticLine(
        view,
        construction,
        sourceSelectionNames);
    const auto created = createDrawingCosmeticLineSegment(
        view, plan.startInViewMm, plan.endInViewMm, plan.format);
    return {plan, created.lineTag};
}

TechDrawGui::DrawingCosmeticLineSegmentPlan
TechDrawGui::validateDrawingCosmeticLineSegment(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& startInViewMm,
    const Base::Vector3d& endInViewMm,
    const TechDraw::LineFormat& format)
{
    requireLiveView(view);
    requirePoint(startInViewMm, "start point");
    requirePoint(endInViewMm, "end point");
    const double length = (endInViewMm - startInViewMm).Length();
    if (!std::isfinite(length) || length <= Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            "A cosmetic line requires two different points");
    }
    return {startInViewMm, endInViewMm, length, format};
}

TechDrawGui::DrawingCosmeticLineSegmentResult
TechDrawGui::createDrawingCosmeticLineSegment(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& startInViewMm,
    const Base::Vector3d& endInViewMm,
    const TechDraw::LineFormat& format)
{
    const DrawingCosmeticLineSegmentPlan plan =
        validateDrawingCosmeticLineSegment(
            view, startInViewMm, endInViewMm, format);
    const auto stored = TechDraw::CosmeticEdge::makeLineFromCanonicalPoints(
        TechDraw::DrawUtil::invertY(plan.startInViewMm),
        TechDraw::DrawUtil::invertY(plan.endInViewMm));
    const std::string tag = view->addCosmeticEdge(stored);
    auto* edge = tag.empty() ? nullptr : view->getCosmeticEdge(tag);
    if (!edge) {
        throw Base::RuntimeError(
            "A persistent cosmetic line could not be created");
    }
    edge->m_format = plan.format;
    finishMutation(view);
    if (!view->getCosmeticEdge(tag)) {
        throw Base::RuntimeError(
            "The cosmetic line did not retain its durable tag");
    }
    return {plan, tag};
}

TechDrawGui::DrawingTwoPointCosmeticLinePlan
TechDrawGui::validateDrawingTwoPointCosmeticLine(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceVertexNames)
{
    requireLiveView(view);
    if (sourceVertexNames.size() != 2
        || sourceVertexNames.at(0) == sourceVertexNames.at(1)
        || !std::regex_match(sourceVertexNames.at(0), VertexNamePattern)
        || !std::regex_match(sourceVertexNames.at(1), VertexNamePattern)) {
        throw Base::ValueError(
            "A two-point cosmetic line requires exactly two different projected VertexN targets");
    }
    const auto first = view->getProjVertexByIndex(
        TechDraw::DrawUtil::getIndexFromName(sourceVertexNames.at(0)));
    const auto second = view->getProjVertexByIndex(
        TechDraw::DrawUtil::getIndexFromName(sourceVertexNames.at(1)));
    if (!first || !second) {
        throw Base::ValueError(
            "A projected cosmetic-line vertex is unavailable");
    }
    const Base::Vector3d start = conventionalPoint(view, first->point());
    const Base::Vector3d end = conventionalPoint(view, second->point());
    return {
        sourceVertexNames,
        validateDrawingCosmeticLineSegment(
            view, start, end, TechDraw::LineFormat::getCurrentLineFormat())};
}

TechDrawGui::DrawingTwoPointCosmeticLineResult
TechDrawGui::createDrawingTwoPointCosmeticLine(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceVertexNames)
{
    const DrawingTwoPointCosmeticLinePlan plan =
        validateDrawingTwoPointCosmeticLine(view, sourceVertexNames);
    const auto created = createDrawingCosmeticLineSegment(
        view,
        plan.segment.startInViewMm,
        plan.segment.endInViewMm,
        plan.segment.format);
    return {plan, created.lineTag};
}

TechDrawGui::DrawingPersistentCosmeticLineState
TechDrawGui::drawingPersistentCosmeticLineState(
    TechDraw::DrawViewPart* view,
    const std::string& tag)
{
    requireLiveView(view);
    return persistentState(
        view,
        tag.empty() ? nullptr : view->getCosmeticEdge(tag));
}

std::vector<TechDrawGui::DrawingPersistentCosmeticLineState>
TechDrawGui::drawingCosmeticLineStates(TechDraw::DrawViewPart* view)
{
    requireLiveView(view);
    std::vector<DrawingPersistentCosmeticLineState> result;
    for (auto* edge : view->CosmeticEdges.getValues()) {
        if (edge && edge->m_geometry
            && edge->m_geometry->getGeomType() == TechDraw::GeomType::GENERIC) {
            result.push_back(persistentState(view, edge));
        }
    }
    return result;
}
