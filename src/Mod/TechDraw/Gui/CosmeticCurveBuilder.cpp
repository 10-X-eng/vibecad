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

#include "CosmeticCurveBuilder.h"

#include <cmath>
#include <memory>
#include <numbers>
#include <regex>
#include <set>
#include <utility>

#include <Base/Exception.h>
#include <Mod/Part/App/Geometry2d.h>
#include <Mod/TechDraw/App/Cosmetic.h>
#include <Mod/TechDraw/App/CosmeticVertex.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Geometry.h>


namespace
{

using TechDrawGui::DrawingCosmeticCurveKind;
using TechDrawGui::DrawingCosmeticCurvePlan;
using TechDrawGui::DrawingPersistentCosmeticCurveState;

constexpr double MaximumCoordinateMm = 1'000'000'000.0;
constexpr double RadiansToDegrees = 180.0 / std::numbers::pi;
const std::regex VertexNamePattern("^Vertex(?:0|[1-9][0-9]*)$");

void requireLiveView(TechDraw::DrawViewPart* view)
{
    auto* page = view ? view->findParentPage() : nullptr;
    if (!view || !view->getDocument() || !page
        || page->getDocument() != view->getDocument()) {
        throw Base::ValueError(
            "Cosmetic curves require a live Drawing view on a page");
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

void requirePoint(const Base::Vector3d& point, const char* noun)
{
    if (!finitePoint(point) || std::abs(point.z) > Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            std::string("The cosmetic curve ") + noun
            + " exceeds supported two-dimensional coordinates");
    }
}

void requireRadius(double radiusMm)
{
    if (!std::isfinite(radiusMm)
        || radiusMm <= Base::Vector3d::epsilon()
        || radiusMm > MaximumCoordinateMm) {
        throw Base::ValueError(
            "A cosmetic circle or arc requires a finite positive radius");
    }
}

std::size_t requiredVertexCount(DrawingCosmeticCurveKind kind)
{
    switch (kind) {
        case DrawingCosmeticCurveKind::OnePointCircle:
            return 1;
        case DrawingCosmeticCurveKind::TwoPointCircle:
            return 2;
        case DrawingCosmeticCurveKind::ThreePointCircle:
        case DrawingCosmeticCurveKind::CenterStartEndArc:
            return 3;
    }
    throw Base::ValueError("The cosmetic curve kind is invalid");
}

std::vector<std::string> exactVertexNames(
    DrawingCosmeticCurveKind kind,
    const std::vector<std::string>& sourceSelectionNames)
{
    std::vector<std::string> result;
    std::set<std::string> seen;
    for (const auto& name : sourceSelectionNames) {
        if (!std::regex_match(name, VertexNamePattern)) {
            continue;
        }
        if (!seen.insert(name).second) {
            throw Base::ValueError(
                "A cosmetic curve source vertex was provided more than once");
        }
        result.push_back(name);
    }
    const std::size_t count = requiredVertexCount(kind);
    if (result.size() != count) {
        throw Base::ValueError(
            "The cosmetic curve requires exactly " + std::to_string(count)
            + " projected vertices in the documented order");
    }
    return result;
}

std::vector<Base::Vector3d> projectedVertexPoints(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& names)
{
    std::vector<Base::Vector3d> result;
    result.reserve(names.size());
    for (const auto& name : names) {
        const auto vertex = view->getProjVertexByIndex(
            TechDraw::DrawUtil::getIndexFromName(name));
        if (!vertex || !finitePoint(vertex->point())) {
            throw Base::ValueError(
                "An exact projected cosmetic-curve vertex is unavailable");
        }
        result.push_back(vertex->point());
    }
    return result;
}

Base::Vector3d conventionalPoint(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& projectedPoint)
{
    const Base::Vector3d canonicalInverted =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            projectedPoint);
    const Base::Vector3d result = TechDraw::DrawUtil::invertY(canonicalInverted);
    requirePoint(result, "point");
    return result;
}

double angleDegrees(
    const Base::Vector3d& center,
    const Base::Vector3d& point)
{
    double result = std::fmod(
        TechDraw::DrawUtil::angleWithX(point - center) * RadiansToDegrees,
        360.0);
    if (result < 0.0) {
        result += 360.0;
    }
    if (std::abs(result) <= Base::Vector3d::epsilon()
        || std::abs(result - 360.0) <= Base::Vector3d::epsilon()) {
        return 0.0;
    }
    return result;
}

double positiveAngleDelta(double startDegrees, double endDegrees)
{
    double result = std::fmod(endDegrees - startDegrees, 360.0);
    if (result < 0.0) {
        result += 360.0;
    }
    return result;
}

bool clockwiseArc(
    const Base::Vector3d& center,
    const Base::Vector3d& start,
    const Base::Vector3d& middle,
    const Base::Vector3d& end)
{
    const double startDegrees = angleDegrees(center, start);
    const double middleDegrees = angleDegrees(center, middle);
    const double endDegrees = angleDegrees(center, end);
    const double counterClockwiseSpan =
        positiveAngleDelta(startDegrees, endDegrees);
    const double counterClockwiseMiddle =
        positiveAngleDelta(startDegrees, middleDegrees);
    if (counterClockwiseSpan <= Base::Vector3d::epsilon()
        || counterClockwiseMiddle <= Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            "The persistent cosmetic arc has degenerate angular geometry");
    }
    return counterClockwiseMiddle > counterClockwiseSpan;
}

DrawingCosmeticCurvePlan circlePlan(
    DrawingCosmeticCurveKind kind,
    std::vector<std::string> sources,
    std::vector<Base::Vector3d> sourcePoints,
    const Base::Vector3d& center,
    double radius)
{
    requirePoint(center, "center");
    requireRadius(radius);
    return {
        kind,
        std::move(sources),
        std::move(sourcePoints),
        center,
        radius,
        0.0,
        360.0,
        false,
        TechDraw::LineFormat::getCurrentLineFormat()};
}

DrawingCosmeticCurvePlan arcPlan(
    std::vector<std::string> sources,
    std::vector<Base::Vector3d> sourcePoints,
    const Base::Vector3d& center,
    double radius,
    double startAngleDegrees,
    double endAngleDegrees)
{
    requirePoint(center, "center");
    requireRadius(radius);
    if (!std::isfinite(startAngleDegrees) || !std::isfinite(endAngleDegrees)) {
        throw Base::ValueError(
            "A cosmetic arc requires finite start and end angles");
    }
    return {
        DrawingCosmeticCurveKind::CenterStartEndArc,
        std::move(sources),
        std::move(sourcePoints),
        center,
        radius,
        startAngleDegrees,
        endAngleDegrees,
        false,
        TechDraw::LineFormat::getCurrentLineFormat()};
}

TechDraw::BaseGeomPtr storedGeometry(const DrawingCosmeticCurvePlan& plan)
{
    if (plan.kind != DrawingCosmeticCurveKind::CenterStartEndArc) {
        return std::make_shared<TechDraw::Circle>(
            TechDraw::DrawUtil::invertY(plan.centerInViewMm),
            plan.radiusMm);
    }
    TechDraw::BaseGeomPtr conventional = std::make_shared<TechDraw::AOC>(
        plan.centerInViewMm,
        plan.radiusMm,
        plan.startAngleDegrees,
        plan.endAngleDegrees);
    return conventional->inverted();
}

void finishMutation(TechDraw::DrawViewPart* view)
{
    view->CosmeticEdges.setValues(view->CosmeticEdges.getValues());
    view->refreshCEGeoms();
    view->requestPaint();
}

TechDrawGui::DrawingCosmeticCurveResult createPlan(
    TechDraw::DrawViewPart* view,
    const DrawingCosmeticCurvePlan& plan)
{
    const std::string tag = view->addCosmeticEdge(storedGeometry(plan));
    auto* edge = tag.empty() ? nullptr : view->getCosmeticEdge(tag);
    if (!edge) {
        throw Base::RuntimeError(
            "A persistent cosmetic circle or arc could not be created");
    }
    edge->m_format = plan.format;
    finishMutation(view);
    if (!view->getCosmeticEdge(tag)) {
        throw Base::RuntimeError(
            "The cosmetic circle or arc did not retain its durable tag");
    }
    return {plan, tag};
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
                "A cosmetic curve resolves to multiple projected edges");
        }
        result = "Edge" + std::to_string(index);
    }
    if (result.empty()) {
        throw Base::RuntimeError(
            "A cosmetic curve is missing from projected geometry");
    }
    return result;
}

DrawingPersistentCosmeticCurveState persistentState(
    TechDraw::DrawViewPart* view,
    TechDraw::CosmeticEdge* edge)
{
    if (!edge || !edge->m_geometry) {
        throw Base::ValueError(
            "The exact persistent cosmetic curve target is unavailable");
    }
    const auto geometryType = edge->m_geometry->getGeomType();
    const bool isArc = geometryType == TechDraw::GeomType::ARCOFCIRCLE;
    if (geometryType != TechDraw::GeomType::CIRCLE && !isArc) {
        throw Base::ValueError(
            "The exact persistent cosmetic curve target is unavailable");
    }
    const TechDraw::BaseGeomPtr conventional = edge->m_geometry->inverted();
    if (!conventional
        || conventional->getGeomType() != geometryType) {
        throw Base::RuntimeError(
            "The persistent cosmetic curve cannot be restored to Drawing coordinates");
    }
    const auto circle = std::static_pointer_cast<TechDraw::Circle>(conventional);
    const Base::Vector3d startPoint =
        isArc ? conventional->getStartPoint() : Base::Vector3d();
    const Base::Vector3d middlePoint =
        isArc ? conventional->getMidPoint() : Base::Vector3d();
    const Base::Vector3d endPoint =
        isArc ? conventional->getEndPoint() : Base::Vector3d();
    const double start = isArc
        ? angleDegrees(circle->center, startPoint)
        : 0.0;
    const double end = isArc
        ? angleDegrees(circle->center, endPoint)
        : 360.0;
    if (!finitePoint(circle->center) || !std::isfinite(circle->radius)
        || circle->radius <= Base::Vector3d::epsilon()
        || circle->radius > MaximumCoordinateMm || !std::isfinite(start)
        || !std::isfinite(end)
        || (isArc
            && (!finitePoint(startPoint) || !finitePoint(middlePoint)
                || !finitePoint(endPoint)))) {
        throw Base::ValueError(
            "The persistent cosmetic curve has invalid geometry");
    }
    const std::string tag = edge->getTagAsString();
    if (tag.empty()) {
        throw Base::RuntimeError(
            "A persistent cosmetic curve has no durable tag");
    }
    return {
        tag,
        selectionNameForTag(view, tag),
        isArc,
        circle->center,
        circle->radius,
        start,
        end,
        isArc
            && clockwiseArc(
                circle->center,
                startPoint,
                middlePoint,
                endPoint),
        edge->m_format};
}

}  // namespace

TechDrawGui::DrawingCosmeticCurvePlan
TechDrawGui::validateDrawingCosmeticCurve(
    TechDraw::DrawViewPart* view,
    DrawingCosmeticCurveKind kind,
    const std::vector<std::string>& sourceSelectionNames,
    double explicitRadiusMm)
{
    requireLiveView(view);
    const auto names = exactVertexNames(kind, sourceSelectionNames);
    const auto projected = projectedVertexPoints(view, names);
    std::vector<Base::Vector3d> points;
    points.reserve(projected.size());
    for (const auto& point : projected) {
        points.push_back(conventionalPoint(view, point));
    }

    if (kind == DrawingCosmeticCurveKind::OnePointCircle) {
        return circlePlan(kind, names, points, points.front(), explicitRadiusMm);
    }
    if (kind == DrawingCosmeticCurveKind::TwoPointCircle) {
        return circlePlan(
            kind,
            names,
            points,
            points.at(0),
            (projected.at(1) - projected.at(0)).Length() / view->getScale());
    }
    if (kind == DrawingCosmeticCurveKind::ThreePointCircle) {
        const Base::Vector2d first(projected.at(0).x, projected.at(0).y);
        const Base::Vector2d second(projected.at(1).x, projected.at(1).y);
        const Base::Vector2d third(projected.at(2).x, projected.at(2).y);
        const Base::Vector2d projectedCenter =
            Part::Geom2dCircle::getCircleCenter(first, second, third);
        const Base::Vector3d projectedCenter3d(
            projectedCenter.x,
            projectedCenter.y,
            0.0);
        return circlePlan(
            kind,
            names,
            points,
            conventionalPoint(view, projectedCenter3d),
            (projected.at(0) - projectedCenter3d).Length() / view->getScale());
    }

    const Base::Vector3d& center = points.at(0);
    const Base::Vector3d& start = points.at(1);
    const Base::Vector3d& end = points.at(2);
    const double radius = (start - center).Length();
    requireRadius(radius);
    const double startAngle = angleDegrees(center, start);
    const double endAngle = angleDegrees(center, end);
    if (!std::isfinite(startAngle) || !std::isfinite(endAngle)) {
        throw Base::ValueError(
            "The selected vertices do not define valid cosmetic-arc angles");
    }
    return arcPlan(names, points, center, radius, startAngle, endAngle);
}

TechDrawGui::DrawingCosmeticCurveResult
TechDrawGui::createDrawingCosmeticCurve(
    TechDraw::DrawViewPart* view,
    DrawingCosmeticCurveKind kind,
    const std::vector<std::string>& sourceSelectionNames,
    double explicitRadiusMm)
{
    return createPlan(
        view,
        validateDrawingCosmeticCurve(
            view,
            kind,
            sourceSelectionNames,
            explicitRadiusMm));
}

TechDrawGui::DrawingCosmeticCurveResult
TechDrawGui::createDrawingCosmeticCircleAtCenter(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& centerInViewMm,
    double radiusMm)
{
    requireLiveView(view);
    return createPlan(
        view,
        circlePlan(
            DrawingCosmeticCurveKind::OnePointCircle,
            {},
            {},
            centerInViewMm,
            radiusMm));
}

TechDrawGui::DrawingCosmeticCurveResult
TechDrawGui::createDrawingCosmeticArcAtCenter(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& centerInViewMm,
    double radiusMm,
    double startAngleDegrees,
    double endAngleDegrees)
{
    requireLiveView(view);
    return createPlan(
        view,
        arcPlan(
            {},
            {},
            centerInViewMm,
            radiusMm,
            startAngleDegrees,
            endAngleDegrees));
}

TechDrawGui::DrawingPersistentCosmeticCurveState
TechDrawGui::drawingPersistentCosmeticCurveState(
    TechDraw::DrawViewPart* view,
    const std::string& tag)
{
    requireLiveView(view);
    return persistentState(
        view,
        tag.empty() ? nullptr : view->getCosmeticEdge(tag));
}

std::vector<TechDrawGui::DrawingPersistentCosmeticCurveState>
TechDrawGui::drawingCosmeticCurveStates(TechDraw::DrawViewPart* view)
{
    requireLiveView(view);
    std::vector<DrawingPersistentCosmeticCurveState> result;
    for (auto* edge : view->CosmeticEdges.getValues()) {
        if (!edge || !edge->m_geometry) {
            continue;
        }
        const auto geometryType = edge->m_geometry->getGeomType();
        if (geometryType == TechDraw::GeomType::CIRCLE
            || geometryType == TechDraw::GeomType::ARCOFCIRCLE) {
            result.push_back(persistentState(view, edge));
        }
    }
    return result;
}
