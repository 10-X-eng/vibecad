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

#include "CircleCenterLineBuilder.h"

#include <algorithm>
#include <cmath>
#include <regex>
#include <set>

#include <Base/Exception.h>
#include <Mod/TechDraw/App/Cosmetic.h>
#include <Mod/TechDraw/App/CosmeticVertex.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Geometry.h>
#include <Mod/TechDraw/App/Preferences.h>
#include <Mod/Part/App/Geometry2d.h>


namespace
{

constexpr std::size_t MaximumCircleTargets = 32;
constexpr double OutsideCircleMm = 2.0;
constexpr double HoleCenterLineExtensionFactor = 1.1;
constexpr double PatternRadiusAbsoluteToleranceMm = 1.0e-8;
constexpr double PatternRadiusRelativeTolerance = 1.0e-8;
constexpr double MaximumCoordinateMm = 1'000'000'000.0;
const std::regex EdgeNamePattern("^Edge(?:0|[1-9][0-9]*)$");

void requireLiveView(TechDraw::DrawViewPart* view)
{
    auto* page = view ? view->findParentPage() : nullptr;
    if (!view || !view->getDocument() || !page
        || page->getDocument() != view->getDocument()) {
        throw Base::ValueError(
            "Circle centerlines require a live Drawing view on a page");
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

TechDraw::LineFormat centerLineFormat()
{
    TechDraw::LineFormat format = TechDraw::LineFormat::getCurrentLineFormat();
    format.setLineNumber(TechDraw::Preferences::CenterLineStyle());
    return format;
}

struct CircleSource
{
    std::string sourceSelectionName;
    std::string geometryConfiguration;
    Base::Vector3d projectedCenter;
    Base::Vector3d centerInViewMm;
    double radiusMm;
};

CircleSource circleSource(
    TechDraw::DrawViewPart* view,
    const std::string& sourceSelectionName)
{
    if (!std::regex_match(sourceSelectionName, EdgeNamePattern)) {
        throw Base::ValueError(
            "A circle centerline source must be an exact projected EdgeN");
    }
    const int geometryIndex =
        TechDraw::DrawUtil::getIndexFromName(sourceSelectionName);
    TechDraw::BaseGeomPtr geometry = view->getGeomByIndex(geometryIndex);
    if (!geometry) {
        throw Base::ValueError(
            "The exact projected circle centerline source is unavailable");
    }
    const auto geometryType = geometry->getGeomType();
    if (geometryType != TechDraw::GeomType::CIRCLE
        && geometryType != TechDraw::GeomType::ARCOFCIRCLE) {
        throw Base::ValueError(
            "Circle centerlines require projected circles or circular arcs");
    }

    const auto circle = std::static_pointer_cast<TechDraw::Circle>(geometry);
    const double radius = circle->radius / view->getScale();
    const Base::Vector3d center =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            circle->center);
    if (!std::isfinite(radius) || radius <= Base::Vector3d::epsilon()
        || radius > MaximumCoordinateMm || !finitePoint(circle->center)
        || !finitePoint(center)) {
        throw Base::ValueError(
            "The projected circle centerline source has invalid geometry");
    }
    return {
        sourceSelectionName,
        geometryType == TechDraw::GeomType::CIRCLE ? "circle" : "circular_arc",
        circle->center,
        center,
        radius};
}

TechDrawGui::DrawingCircleCenterLinePlan planForSource(
    TechDraw::DrawViewPart* view,
    const std::string& sourceSelectionName,
    const TechDraw::LineFormat& format)
{
    const CircleSource source = circleSource(view, sourceSelectionName);

    TechDrawGui::DrawingCircleCenterLinePlan result;
    result.sourceSelectionName = sourceSelectionName;
    result.geometryConfiguration = source.geometryConfiguration;
    result.centerInViewMm = source.centerInViewMm;
    result.radiusMm = source.radiusMm;
    result.outsideExtensionMm = OutsideCircleMm;
    result.horizontalStartInViewMm = Base::Vector3d(
        source.centerInViewMm.x + source.radiusMm + OutsideCircleMm,
        source.centerInViewMm.y,
        0.0);
    result.horizontalEndInViewMm = Base::Vector3d(
        source.centerInViewMm.x - source.radiusMm - OutsideCircleMm,
        source.centerInViewMm.y,
        0.0);
    result.verticalStartInViewMm = Base::Vector3d(
        source.centerInViewMm.x,
        source.centerInViewMm.y + source.radiusMm + OutsideCircleMm,
        0.0);
    result.verticalEndInViewMm = Base::Vector3d(
        source.centerInViewMm.x,
        source.centerInViewMm.y - source.radiusMm - OutsideCircleMm,
        0.0);
    result.format = format;
    if (!finitePoint(result.horizontalStartInViewMm)
        || !finitePoint(result.horizontalEndInViewMm)
        || !finitePoint(result.verticalStartInViewMm)
        || !finitePoint(result.verticalEndInViewMm)) {
        throw Base::ValueError(
            "The projected circle centerlines exceed supported coordinates");
    }
    return result;
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
                "A persistent cosmetic circle resolves to multiple projected edges");
        }
        result = "Edge" + std::to_string(index);
    }
    if (result.empty()) {
        throw Base::RuntimeError(
            "The persistent cosmetic circle is missing from projected geometry");
    }
    return result;
}

}  // namespace

std::vector<TechDrawGui::DrawingCircleCenterLinePlan>
TechDrawGui::validateDrawingCircleCenterLines(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    requireLiveView(view);
    if (sourceSelectionNames.empty()
        || sourceSelectionNames.size() > MaximumCircleTargets) {
        throw Base::ValueError(
            "Circle centerlines require 1 to 32 exact projected circles or arcs");
    }
    const TechDraw::LineFormat format = centerLineFormat();
    std::set<std::string> seen;
    std::vector<DrawingCircleCenterLinePlan> result;
    result.reserve(sourceSelectionNames.size());
    for (const auto& source : sourceSelectionNames) {
        if (!seen.insert(source).second) {
            throw Base::ValueError(
                "A circle centerline source was provided more than once");
        }
        result.push_back(planForSource(view, source, format));
    }
    return result;
}

std::vector<TechDrawGui::DrawingCircleCenterLineResult>
TechDrawGui::createDrawingCircleCenterLines(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    const auto plans =
        validateDrawingCircleCenterLines(view, sourceSelectionNames);
    std::vector<DrawingCircleCenterLineResult> result;
    result.reserve(plans.size());
    for (const auto& plan : plans) {
        const std::string horizontalTag = view->addCosmeticEdge(
            plan.horizontalStartInViewMm,
            plan.horizontalEndInViewMm);
        const std::string verticalTag = view->addCosmeticEdge(
            plan.verticalStartInViewMm,
            plan.verticalEndInViewMm);
        auto* horizontal = view->getCosmeticEdge(horizontalTag);
        auto* vertical = view->getCosmeticEdge(verticalTag);
        if (!horizontal || !vertical || horizontalTag.empty()
            || verticalTag.empty() || horizontalTag == verticalTag) {
            throw Base::RuntimeError(
                "The persistent circle centerline pair could not be created");
        }
        horizontal->m_format = plan.format;
        vertical->m_format = plan.format;
        result.push_back({plan, horizontalTag, verticalTag});
    }

    // Record the final formats in the owning property and rebuild both shared
    // projected cosmetic sequences so every returned tag has one current EdgeN.
    view->CosmeticEdges.setValues(view->CosmeticEdges.getValues());
    view->refreshCEGeoms();
    view->refreshCLGeoms();
    view->requestPaint();

    for (const auto& created : result) {
        if (!view->getCosmeticEdge(created.horizontalTag)
            || !view->getCosmeticEdge(created.verticalTag)) {
            throw Base::RuntimeError(
                "A created circle centerline did not retain its persistent tag");
        }
    }
    return result;
}

TechDrawGui::DrawingBoltCircleCenterLinePlan
TechDrawGui::validateDrawingBoltCircleCenterLines(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    requireLiveView(view);
    if (sourceSelectionNames.size() < 3
        || sourceSelectionNames.size() > MaximumCircleTargets) {
        throw Base::ValueError(
            "Bolt-circle centerlines require 3 to 32 exact projected circles or arcs");
    }

    std::set<std::string> seen;
    std::vector<CircleSource> sources;
    sources.reserve(sourceSelectionNames.size());
    for (const auto& sourceName : sourceSelectionNames) {
        if (!seen.insert(sourceName).second) {
            throw Base::ValueError(
                "A bolt-circle source was provided more than once");
        }
        sources.push_back(circleSource(view, sourceName));
    }

    const Base::Vector2d first(
        sources.at(0).projectedCenter.x,
        sources.at(0).projectedCenter.y);
    const Base::Vector2d second(
        sources.at(1).projectedCenter.x,
        sources.at(1).projectedCenter.y);
    const Base::Vector2d third(
        sources.at(2).projectedCenter.x,
        sources.at(2).projectedCenter.y);
    const Base::Vector2d projectedCenter2d =
        Part::Geom2dCircle::getCircleCenter(first, second, third);
    const Base::Vector3d projectedCenter(
        projectedCenter2d.x,
        projectedCenter2d.y,
        0.0);
    const Base::Vector3d patternCenter =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            projectedCenter);
    const double patternRadius =
        (sources.at(0).projectedCenter - projectedCenter).Length()
        / view->getScale();
    if (!finitePoint(projectedCenter) || !finitePoint(patternCenter)
        || !std::isfinite(patternRadius)
        || patternRadius <= Base::Vector3d::epsilon()
        || patternRadius > MaximumCoordinateMm) {
        throw Base::ValueError(
            "The first three bolt-circle centers do not define a supported circle");
    }

    DrawingBoltCircleCenterLinePlan result;
    result.patternCenterInViewMm = patternCenter;
    result.patternRadiusMm = patternRadius;
    result.maximumPatternRadiusDeviationMm = 0.0;
    result.patternRadiusToleranceMm = std::max(
        PatternRadiusAbsoluteToleranceMm,
        patternRadius * PatternRadiusRelativeTolerance);
    result.allCentersOnPattern = true;
    result.holeCenterLineExtensionFactor = HoleCenterLineExtensionFactor;
    result.format = TechDraw::LineFormat::getCurrentLineFormat();
    result.holes.reserve(sources.size());

    for (const auto& source : sources) {
        const Base::Vector3d radial = source.centerInViewMm - patternCenter;
        const double radiusAtCenter = radial.Length();
        if (!std::isfinite(radiusAtCenter)
            || radiusAtCenter <= Base::Vector3d::epsilon()) {
            throw Base::ValueError(
                "A bolt-circle center lies at the pattern center");
        }
        const double deviation = radiusAtCenter - patternRadius;
        const double absoluteDeviation = std::abs(deviation);
        result.maximumPatternRadiusDeviationMm = std::max(
            result.maximumPatternRadiusDeviationMm,
            absoluteDeviation);
        const Base::Vector3d delta = radial
            * (source.radiusMm * HoleCenterLineExtensionFactor
               / radiusAtCenter);
        const Base::Vector3d start = source.centerInViewMm + delta;
        const Base::Vector3d end = source.centerInViewMm - delta;
        if (!finitePoint(start) || !finitePoint(end)) {
            throw Base::ValueError(
                "A bolt-circle centerline exceeds supported coordinates");
        }
        result.holes.push_back({
            source.sourceSelectionName,
            source.geometryConfiguration,
            source.centerInViewMm,
            source.radiusMm,
            radiusAtCenter,
            deviation,
            start,
            end});
    }
    result.allCentersOnPattern =
        result.maximumPatternRadiusDeviationMm
        <= result.patternRadiusToleranceMm;
    return result;
}

TechDrawGui::DrawingBoltCircleCenterLineResult
TechDrawGui::createDrawingBoltCircleCenterLines(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    const DrawingBoltCircleCenterLinePlan plan =
        validateDrawingBoltCircleCenterLines(view, sourceSelectionNames);
    const TechDraw::BaseGeomPtr patternCircle =
        std::make_shared<TechDraw::Circle>(
            plan.patternCenterInViewMm,
            plan.patternRadiusMm);
    const std::string patternCircleTag = view->addCosmeticEdge(patternCircle);
    auto* persistentPattern = view->getCosmeticEdge(patternCircleTag);
    if (!persistentPattern || patternCircleTag.empty()) {
        throw Base::RuntimeError(
            "The persistent bolt-pattern circle could not be created");
    }
    persistentPattern->m_format = plan.format;

    std::vector<std::string> holeCenterLineTags;
    holeCenterLineTags.reserve(plan.holes.size());
    for (const auto& hole : plan.holes) {
        const std::string tag = view->addCosmeticEdge(
            hole.centerLineStartInViewMm,
            hole.centerLineEndInViewMm);
        auto* persistentLine = view->getCosmeticEdge(tag);
        if (!persistentLine || tag.empty()) {
            throw Base::RuntimeError(
                "A persistent bolt-hole centerline could not be created");
        }
        persistentLine->m_format = plan.format;
        holeCenterLineTags.push_back(tag);
    }

    view->CosmeticEdges.setValues(view->CosmeticEdges.getValues());
    view->refreshCEGeoms();
    view->refreshCLGeoms();
    view->requestPaint();

    if (!view->getCosmeticEdge(patternCircleTag)) {
        throw Base::RuntimeError(
            "The created bolt-pattern circle did not retain its persistent tag");
    }
    for (const auto& tag : holeCenterLineTags) {
        if (!view->getCosmeticEdge(tag)) {
            throw Base::RuntimeError(
                "A created bolt-hole centerline did not retain its persistent tag");
        }
    }
    return {plan, patternCircleTag, holeCenterLineTags};
}

TechDrawGui::DrawingPersistentCosmeticCircleState
TechDrawGui::drawingPersistentCosmeticCircleState(
    TechDraw::DrawViewPart* view,
    const std::string& tag)
{
    requireLiveView(view);
    auto* edge = tag.empty() ? nullptr : view->getCosmeticEdge(tag);
    if (!edge || !edge->m_geometry
        || edge->m_geometry->getGeomType() != TechDraw::GeomType::CIRCLE) {
        throw Base::ValueError(
            "The exact persistent cosmetic circle target is unavailable");
    }
    if (!finitePoint(edge->permaStart) || !std::isfinite(edge->permaRadius)
        || edge->permaRadius <= Base::Vector3d::epsilon()
        || edge->permaRadius > MaximumCoordinateMm) {
        throw Base::ValueError(
            "The persistent cosmetic circle has invalid geometry");
    }
    return {
        tag,
        cosmeticSelectionName(view, tag),
        edge->permaStart,
        edge->permaRadius,
        edge->m_format};
}
