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

#include "ThreadRepresentationBuilder.h"

#include <cmath>
#include <numbers>
#include <regex>
#include <set>
#include <utility>

#include <Base/Exception.h>
#include <Mod/TechDraw/App/Cosmetic.h>
#include <Mod/TechDraw/App/CosmeticVertex.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Geometry.h>


namespace
{

using TechDrawGui::DrawingThreadBottomPlan;
using TechDrawGui::DrawingThreadLinePlan;
using TechDrawGui::DrawingThreadRepresentationKind;
using TechDrawGui::DrawingThreadSidePlan;

constexpr std::size_t MaximumThreadBottomTargets = 32;
constexpr double HoleThreadFactor = 1.176;
constexpr double BoltThreadFactor = 0.85;
constexpr double ThreadArcStartDegrees = 15.0;
constexpr double ThreadArcEndDegrees = 285.0;
constexpr double MaximumCoordinateMm = 1'000'000'000.0;
constexpr int SolidLineStyle = 1;
const std::regex EdgeNamePattern("^Edge(?:0|[1-9][0-9]*)$");

void requireLiveView(TechDraw::DrawViewPart* view)
{
    auto* page = view ? view->findParentPage() : nullptr;
    if (!view || !view->getDocument() || !page
        || page->getDocument() != view->getDocument()) {
        throw Base::ValueError(
            "Thread representation requires a live Drawing view on a page");
    }
    const double scale = view->getScale();
    if (!std::isfinite(scale) || scale <= Base::Vector3d::epsilon()) {
        throw Base::ValueError("The drawing view has an invalid scale");
    }
}

bool finitePoint(const Base::Vector3d& point)
{
    return std::isfinite(point.x) && std::isfinite(point.y)
        && std::isfinite(point.z) && std::abs(point.x) <= MaximumCoordinateMm
        && std::abs(point.y) <= MaximumCoordinateMm
        && std::abs(point.z) <= MaximumCoordinateMm;
}

bool isSideKind(DrawingThreadRepresentationKind kind)
{
    return kind == DrawingThreadRepresentationKind::HoleSide
        || kind == DrawingThreadRepresentationKind::BoltSide;
}

bool isBottomKind(DrawingThreadRepresentationKind kind)
{
    return kind == DrawingThreadRepresentationKind::HoleBottom
        || kind == DrawingThreadRepresentationKind::BoltBottom;
}

double threadFactor(DrawingThreadRepresentationKind kind)
{
    switch (kind) {
        case DrawingThreadRepresentationKind::HoleSide:
        case DrawingThreadRepresentationKind::HoleBottom:
            return HoleThreadFactor;
        case DrawingThreadRepresentationKind::BoltSide:
        case DrawingThreadRepresentationKind::BoltBottom:
            return BoltThreadFactor;
    }
    throw Base::ValueError("The Drawing thread representation kind is invalid");
}

TechDraw::LineFormat threadFormat(bool graphicWeight)
{
    const TechDraw::LineFormat& active =
        TechDraw::LineFormat::getCurrentLineFormat();
    TechDraw::LineFormat format;
    format.setStyle(SolidLineStyle);
    format.setWidth(TechDraw::DrawUtil::getDefaultLineWeight(
        graphicWeight ? "Graphic" : "Thin"));
    format.setColor(active.getColor());
    format.setVisible(active.getVisible());
    format.setLineNumber(SolidLineStyle);
    return format;
}

TechDraw::BaseGeomPtr exactEdge(
    TechDraw::DrawViewPart* view,
    const std::string& sourceSelectionName)
{
    if (!std::regex_match(sourceSelectionName, EdgeNamePattern)) {
        throw Base::ValueError("A thread source must be an exact projected EdgeN");
    }
    return view->getGeomByIndex(
        TechDraw::DrawUtil::getIndexFromName(sourceSelectionName));
}

DrawingThreadBottomPlan bottomPlan(
    TechDraw::DrawViewPart* view,
    DrawingThreadRepresentationKind kind,
    const std::string& sourceSelectionName)
{
    const auto geometry = exactEdge(view, sourceSelectionName);
    if (!geometry || geometry->getGeomType() != TechDraw::GeomType::CIRCLE) {
        throw Base::ValueError(
            "Thread bottom representation requires projected full circles");
    }
    const auto circle = std::static_pointer_cast<TechDraw::Circle>(geometry);
    const Base::Vector3d center =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            circle->center);
    const double sourceRadius = circle->radius / view->getScale();
    const double factor = threadFactor(kind);
    const double radius = sourceRadius * factor;
    if (!finitePoint(center) || !std::isfinite(sourceRadius)
        || sourceRadius <= Base::Vector3d::epsilon()
        || !std::isfinite(radius) || radius <= Base::Vector3d::epsilon()
        || radius > MaximumCoordinateMm) {
        throw Base::ValueError(
            "The projected thread-circle source has invalid geometry");
    }
    return {
        kind,
        sourceSelectionName,
        center,
        sourceRadius,
        factor,
        radius,
        ThreadArcStartDegrees,
        ThreadArcEndDegrees,
        threadFormat(false)};
}

std::string cosmeticSelectionName(
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
                "A persistent cosmetic thread arc resolves to multiple projected edges");
        }
        result = "Edge" + std::to_string(index);
    }
    if (result.empty()) {
        throw Base::RuntimeError(
            "The persistent cosmetic thread arc is missing from projected geometry");
    }
    return result;
}

void finishCosmeticMutation(TechDraw::DrawViewPart* view)
{
    view->CosmeticEdges.setValues(view->CosmeticEdges.getValues());
    view->refreshCEGeoms();
    view->refreshCLGeoms();
    view->requestPaint();
}

}  // namespace

TechDrawGui::DrawingThreadSidePlan TechDrawGui::validateDrawingThreadSide(
    TechDraw::DrawViewPart* view,
    DrawingThreadRepresentationKind kind,
    const std::vector<std::string>& sourceSelectionNames)
{
    requireLiveView(view);
    if (!isSideKind(kind)) {
        throw Base::ValueError("The requested thread representation is not a side view");
    }
    if (sourceSelectionNames.size() != 2) {
        throw Base::ValueError("Select two straight, parallel edges");
    }
    if (sourceSelectionNames.at(0) == sourceSelectionNames.at(1)) {
        throw Base::ValueError("Select two different straight, parallel edges");
    }
    const auto firstGeometry = exactEdge(view, sourceSelectionNames.at(0));
    const auto secondGeometry = exactEdge(view, sourceSelectionNames.at(1));
    if (!firstGeometry || !secondGeometry
        || firstGeometry->getGeomType() != TechDraw::GeomType::GENERIC
        || secondGeometry->getGeomType() != TechDraw::GeomType::GENERIC) {
        throw Base::ValueError("Select 2 straight lines");
    }

    const auto first = std::static_pointer_cast<TechDraw::Generic>(firstGeometry);
    const auto second = std::static_pointer_cast<TechDraw::Generic>(secondGeometry);
    Base::Vector3d firstStart =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            first->getStartPoint());
    Base::Vector3d firstEnd =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            first->getEndPoint());
    Base::Vector3d secondStart =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            second->getStartPoint());
    Base::Vector3d secondEnd =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            second->getEndPoint());
    const Base::Vector3d firstDirection = firstEnd - firstStart;
    const Base::Vector3d secondDirection = secondEnd - secondStart;
    if (firstDirection.Length() <= Base::Vector3d::epsilon()
        || secondDirection.Length() <= Base::Vector3d::epsilon()
        || firstDirection.Cross(secondDirection).Length()
            > 1.0e-6 * firstDirection.Length() * secondDirection.Length()) {
        throw Base::ValueError(
            "Select two nonzero parallel straight lines");
    }
    if (TechDraw::DrawUtil::circulation(firstStart, firstEnd, secondStart)
        != TechDraw::DrawUtil::circulation(firstEnd, secondEnd, secondStart)) {
        std::swap(secondStart, secondEnd);
    }
    const Base::Vector3d connector = secondStart - firstStart;
    const double diameter = connector.Length();
    if (!std::isfinite(diameter)
        || diameter <= Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            "The selected lines do not define a thread diameter");
    }
    const double factor = threadFactor(kind);
    const Base::Vector3d delta = connector
        * (((diameter * factor - diameter) / 2.0) / diameter);
    const TechDraw::LineFormat thinFormat = threadFormat(false);

    DrawingThreadSidePlan result;
    result.kind = kind;
    result.threadFactor = factor;
    result.sourceDiameterMm = diameter;
    result.sourceSelectionNames = sourceSelectionNames;
    result.firstStartInViewMm = firstStart;
    result.firstEndInViewMm = firstEnd;
    result.secondStartInViewMm = secondStart;
    result.secondEndInViewMm = secondEnd;
    result.lines = {
        {"first_thread_boundary", firstStart - delta, firstEnd - delta, thinFormat},
        {"second_thread_boundary", secondStart + delta, secondEnd + delta, thinFormat}};
    if (kind == DrawingThreadRepresentationKind::HoleSide) {
        result.lines.push_back({
            "thread_end",
            firstEnd - delta,
            secondEnd + delta,
            threadFormat(true)});
    }
    for (const auto& line : result.lines) {
        if (!finitePoint(line.startInViewMm) || !finitePoint(line.endInViewMm)
            || (line.endInViewMm - line.startInViewMm).Length()
                <= Base::Vector3d::epsilon()) {
            throw Base::ValueError(
                "The thread side representation exceeds supported coordinates");
        }
    }
    return result;
}

TechDrawGui::DrawingThreadSideResult TechDrawGui::createDrawingThreadSide(
    TechDraw::DrawViewPart* view,
    DrawingThreadRepresentationKind kind,
    const std::vector<std::string>& sourceSelectionNames)
{
    const DrawingThreadSidePlan plan =
        validateDrawingThreadSide(view, kind, sourceSelectionNames);
    std::vector<std::string> tags;
    tags.reserve(plan.lines.size());
    for (const auto& line : plan.lines) {
        const std::string tag =
            view->addCosmeticEdge(line.startInViewMm, line.endInViewMm);
        auto* persistent = view->getCosmeticEdge(tag);
        if (!persistent || tag.empty()) {
            throw Base::RuntimeError(
                "A persistent thread side line could not be created");
        }
        persistent->m_format = line.format;
        tags.push_back(tag);
    }
    finishCosmeticMutation(view);
    for (const auto& tag : tags) {
        if (!view->getCosmeticEdge(tag)) {
            throw Base::RuntimeError(
                "A thread side line did not retain its persistent tag");
        }
    }
    return {plan, tags};
}

std::vector<TechDrawGui::DrawingThreadBottomPlan>
TechDrawGui::validateDrawingThreadBottom(
    TechDraw::DrawViewPart* view,
    DrawingThreadRepresentationKind kind,
    const std::vector<std::string>& sourceSelectionNames)
{
    requireLiveView(view);
    if (!isBottomKind(kind)) {
        throw Base::ValueError("The requested thread representation is not a bottom view");
    }
    if (sourceSelectionNames.empty()
        || sourceSelectionNames.size() > MaximumThreadBottomTargets) {
        throw Base::ValueError(
            "Thread bottom representation requires 1 to 32 projected full circles");
    }
    std::set<std::string> seen;
    std::vector<DrawingThreadBottomPlan> result;
    result.reserve(sourceSelectionNames.size());
    for (const auto& source : sourceSelectionNames) {
        if (!seen.insert(source).second) {
            throw Base::ValueError(
                "A thread bottom source was provided more than once");
        }
        result.push_back(bottomPlan(view, kind, source));
    }
    return result;
}

std::vector<TechDrawGui::DrawingThreadBottomResult>
TechDrawGui::createDrawingThreadBottom(
    TechDraw::DrawViewPart* view,
    DrawingThreadRepresentationKind kind,
    const std::vector<std::string>& sourceSelectionNames)
{
    const auto plans =
        validateDrawingThreadBottom(view, kind, sourceSelectionNames);
    std::vector<DrawingThreadBottomResult> result;
    result.reserve(plans.size());
    for (const auto& plan : plans) {
        const TechDraw::BaseGeomPtr arc = std::make_shared<TechDraw::AOC>(
            plan.centerInViewMm,
            plan.threadRadiusMm,
            plan.startAngleDegrees,
            plan.endAngleDegrees);
        const std::string tag = view->addCosmeticEdge(arc);
        auto* persistent = view->getCosmeticEdge(tag);
        if (!persistent || tag.empty()) {
            throw Base::RuntimeError(
                "A persistent thread bottom arc could not be created");
        }
        persistent->m_format = plan.format;
        result.push_back({plan, tag});
    }
    finishCosmeticMutation(view);
    for (const auto& created : result) {
        if (!view->getCosmeticEdge(created.arcTag)) {
            throw Base::RuntimeError(
                "A thread bottom arc did not retain its persistent tag");
        }
    }
    return result;
}

TechDrawGui::DrawingPersistentCosmeticArcState
TechDrawGui::drawingPersistentCosmeticArcState(
    TechDraw::DrawViewPart* view,
    const std::string& tag)
{
    requireLiveView(view);
    auto* edge = tag.empty() ? nullptr : view->getCosmeticEdge(tag);
    if (!edge || !edge->m_geometry
        || edge->m_geometry->getGeomType()
            != TechDraw::GeomType::ARCOFCIRCLE) {
        throw Base::ValueError(
            "The exact persistent cosmetic arc target is unavailable");
    }
    const auto arc = std::static_pointer_cast<TechDraw::AOC>(edge->m_geometry);
    const double radiansToDegrees = 180.0 / std::numbers::pi;
    const double startDegrees = arc->getStartAngle() * radiansToDegrees;
    const double endDegrees = arc->getEndAngle() * radiansToDegrees;
    if (!finitePoint(edge->permaStart) || !std::isfinite(edge->permaRadius)
        || edge->permaRadius <= Base::Vector3d::epsilon()
        || edge->permaRadius > MaximumCoordinateMm
        || !std::isfinite(startDegrees) || !std::isfinite(endDegrees)) {
        throw Base::ValueError(
            "The persistent cosmetic arc has invalid geometry");
    }
    return {
        tag,
        cosmeticSelectionName(view, tag),
        edge->permaStart,
        edge->permaRadius,
        startDegrees,
        endDegrees,
        arc->clockwiseAngle(),
        edge->m_format};
}
