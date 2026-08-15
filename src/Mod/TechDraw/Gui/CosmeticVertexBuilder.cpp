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

#include "CosmeticVertexBuilder.h"

#include <algorithm>
#include <cmath>
#include <regex>
#include <utility>

#include <Base/Exception.h>
#include <Mod/TechDraw/App/CosmeticVertex.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Geometry.h>


namespace
{

using TechDrawGui::DrawingCosmeticVertexFormat;
using TechDrawGui::DrawingCosmeticVertexPointPlan;
using TechDrawGui::DrawingPersistentCosmeticVertexState;

constexpr double MaximumCoordinateMm = 1'000'000'000.0;
constexpr std::size_t MaximumDerivedVertices = 64;
const std::regex EdgeNamePattern("^Edge(?:0|[1-9][0-9]*)$");
const std::regex VertexNamePattern("^Vertex(?:0|[1-9][0-9]*)$");

void requireLiveView(TechDraw::DrawViewPart* view)
{
    auto* page = view ? view->findParentPage() : nullptr;
    if (!view || !view->getDocument() || !page
        || page->getDocument() != view->getDocument()) {
        throw Base::ValueError(
            "Cosmetic vertices require a live Drawing view on a page");
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

DrawingCosmeticVertexFormat defaultFormat()
{
    return {Base::Color(), 1.0, 1, true};
}

TechDraw::BaseGeomPtr exactEdge(
    TechDraw::DrawViewPart* view,
    const std::string& selectionName)
{
    if (!std::regex_match(selectionName, EdgeNamePattern)) {
        throw Base::ValueError(
            "A derived cosmetic-vertex source must be an exact projected EdgeN");
    }
    return view->getGeomByIndex(
        TechDraw::DrawUtil::getIndexFromName(selectionName));
}

TechDraw::VertexPtr exactVertex(
    TechDraw::DrawViewPart* view,
    const std::string& selectionName)
{
    if (!std::regex_match(selectionName, VertexNamePattern)) {
        throw Base::ValueError(
            "An offset source must be an exact projected VertexN");
    }
    return view->getProjVertexByIndex(
        TechDraw::DrawUtil::getIndexFromName(selectionName));
}

std::string selectionNameForTag(
    TechDraw::DrawViewPart* view,
    const std::string& tag)
{
    std::string result;
    const auto vertices = view->getVertexGeometry();
    for (std::size_t index = 0; index < vertices.size(); ++index) {
        const auto& vertex = vertices.at(index);
        if (!vertex || vertex->getCosmeticTag() != tag) {
            continue;
        }
        if (!result.empty()) {
            throw Base::RuntimeError(
                "A persistent cosmetic vertex resolves to multiple projected vertices");
        }
        result = "Vertex" + std::to_string(index);
    }
    if (result.empty()) {
        throw Base::RuntimeError(
            "The persistent cosmetic vertex is missing from projected geometry");
    }
    return result;
}

DrawingPersistentCosmeticVertexState persistentState(
    TechDraw::DrawViewPart* view,
    TechDraw::CosmeticVertex* vertex)
{
    if (!vertex || !finitePoint(vertex->permaPoint)
        || !std::isfinite(vertex->size) || vertex->size < 0.0
        || vertex->size > MaximumCoordinateMm || vertex->style < 0) {
        throw Base::ValueError(
            "A persistent cosmetic vertex has invalid geometry or style");
    }
    const std::string tag = vertex->getTagAsString();
    if (tag.empty()) {
        throw Base::RuntimeError(
            "A persistent cosmetic vertex has no durable tag");
    }
    return {
        tag,
        selectionNameForTag(view, tag),
        TechDraw::DrawUtil::invertY(vertex->permaPoint),
        {vertex->color, vertex->size, vertex->style, vertex->visible}};
}

std::string createVertex(
    TechDraw::DrawViewPart* view,
    const DrawingCosmeticVertexPointPlan& plan)
{
    const std::string tag = view->addCosmeticVertex(
        TechDraw::DrawUtil::invertY(plan.pointInViewMm),
        false);
    auto* vertex = tag.empty() ? nullptr : view->getCosmeticVertex(tag);
    if (!vertex) {
        throw Base::RuntimeError(
            "A persistent cosmetic vertex could not be created");
    }
    vertex->color = plan.format.color;
    vertex->size = plan.format.size;
    vertex->style = plan.format.style;
    vertex->visible = plan.format.visible;
    return tag;
}

void finishMutation(TechDraw::DrawViewPart* view)
{
    view->CosmeticVertexes.setValues(view->CosmeticVertexes.getValues());
    view->refreshCVGeoms();
    view->requestPaint();
}

}  // namespace

TechDrawGui::DrawingCosmeticVertexPointPlan
TechDrawGui::validateDrawingCosmeticVertexPoint(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& pointInViewMm)
{
    requireLiveView(view);
    if (!finitePoint(pointInViewMm)
        || std::abs(pointInViewMm.z) > Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            "A Drawing cosmetic vertex requires a finite two-dimensional point");
    }
    return {pointInViewMm, defaultFormat()};
}

TechDrawGui::DrawingCosmeticVertexPointResult
TechDrawGui::createDrawingCosmeticVertexPoint(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& pointInViewMm)
{
    const DrawingCosmeticVertexPointPlan plan =
        validateDrawingCosmeticVertexPoint(view, pointInViewMm);
    const std::string tag = createVertex(view, plan);
    finishMutation(view);
    if (!view->getCosmeticVertex(tag)) {
        throw Base::RuntimeError(
            "The cosmetic vertex did not retain its durable tag");
    }
    return {plan, tag};
}

TechDrawGui::DrawingVertexIntersectionPlan
TechDrawGui::validateDrawingVertexIntersections(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    requireLiveView(view);
    if (sourceSelectionNames.size() != 2
        || sourceSelectionNames.at(0) == sourceSelectionNames.at(1)) {
        throw Base::ValueError("Select exactly two different edges");
    }
    const auto first = exactEdge(view, sourceSelectionNames.at(0));
    const auto second = exactEdge(view, sourceSelectionNames.at(1));
    if (!first || !second) {
        throw Base::ValueError(
            "The selected edge geometry is unavailable");
    }
    const auto intersections = first->intersection(second);
    if (intersections.empty()) {
        throw Base::ValueError("The selected edges do not intersect");
    }
    DrawingVertexIntersectionPlan result;
    result.sourceSelectionNames = sourceSelectionNames;
    result.vertices.reserve(intersections.size());
    const DrawingCosmeticVertexFormat format = defaultFormat();
    for (const auto& point : intersections) {
        const Base::Vector3d canonicalInverted =
            TechDraw::CosmeticVertex::makeCanonicalPointInverted(view, point);
        const Base::Vector3d conventional =
            TechDraw::DrawUtil::invertY(canonicalInverted);
        if (!finitePoint(conventional)) {
            throw Base::ValueError(
                "An edge intersection exceeds supported coordinates");
        }
        result.vertices.push_back({conventional, format});
    }
    return result;
}

TechDrawGui::DrawingVertexIntersectionResult
TechDrawGui::createDrawingVertexIntersections(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    const DrawingVertexIntersectionPlan plan =
        validateDrawingVertexIntersections(view, sourceSelectionNames);
    std::vector<std::string> tags;
    tags.reserve(plan.vertices.size());
    for (const auto& vertex : plan.vertices) {
        tags.push_back(createVertex(view, vertex));
    }
    finishMutation(view);
    for (const auto& tag : tags) {
        if (!view->getCosmeticVertex(tag)) {
            throw Base::RuntimeError(
                "A cosmetic intersection vertex did not retain its durable tag");
        }
    }
    return {plan, tags};
}

TechDrawGui::DrawingMidpointVerticesPlan
TechDrawGui::validateDrawingMidpointVertices(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    requireLiveView(view);
    if (sourceSelectionNames.empty()
        || sourceSelectionNames.size() > MaximumDerivedVertices) {
        throw Base::ValueError(
            "Select between one and 64 edges for midpoint vertices");
    }
    std::vector<std::string> uniqueNames;
    uniqueNames.reserve(sourceSelectionNames.size());
    DrawingMidpointVerticesPlan result;
    result.midpoints.reserve(sourceSelectionNames.size());
    const DrawingCosmeticVertexFormat format = defaultFormat();
    for (const auto& source : sourceSelectionNames) {
        if (std::find(uniqueNames.begin(), uniqueNames.end(), source)
            != uniqueNames.end()) {
            throw Base::ValueError(
                "A midpoint source edge cannot be repeated");
        }
        uniqueNames.push_back(source);
        const auto edge = exactEdge(view, source);
        if (!edge) {
            throw Base::ValueError(
                "The selected midpoint edge geometry is unavailable");
        }
        Base::Vector3d midpoint =
            TechDraw::DrawUtil::invertY(edge->getMidPoint());
        midpoint = TechDraw::CosmeticVertex::makeCanonicalPoint(view, midpoint);
        if (!finitePoint(midpoint)
            || std::abs(midpoint.z) > Base::Vector3d::epsilon()) {
            throw Base::ValueError(
                "An edge midpoint exceeds supported Drawing-view coordinates");
        }
        result.midpoints.push_back({source, {midpoint, format}});
    }
    return result;
}

TechDrawGui::DrawingMidpointVerticesResult
TechDrawGui::createDrawingMidpointVertices(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    const DrawingMidpointVerticesPlan plan =
        validateDrawingMidpointVertices(view, sourceSelectionNames);
    std::vector<std::string> tags;
    tags.reserve(plan.midpoints.size());
    for (const auto& midpoint : plan.midpoints) {
        tags.push_back(createVertex(view, midpoint.vertex));
    }
    finishMutation(view);
    for (const auto& tag : tags) {
        if (!view->getCosmeticVertex(tag)) {
            throw Base::RuntimeError(
                "A midpoint vertex did not retain its durable tag");
        }
    }
    return {plan, tags};
}

TechDrawGui::DrawingQuadrantVerticesPlan
TechDrawGui::validateDrawingQuadrantVertices(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    requireLiveView(view);
    if (sourceSelectionNames.empty()
        || sourceSelectionNames.size() > MaximumDerivedVertices) {
        throw Base::ValueError(
            "Select between one and 64 edges for quadrant vertices");
    }
    std::vector<std::string> uniqueNames;
    uniqueNames.reserve(sourceSelectionNames.size());
    DrawingQuadrantVerticesPlan result;
    result.sources.reserve(sourceSelectionNames.size());
    const DrawingCosmeticVertexFormat format = defaultFormat();
    for (const auto& source : sourceSelectionNames) {
        if (std::find(uniqueNames.begin(), uniqueNames.end(), source)
            != uniqueNames.end()) {
            throw Base::ValueError(
                "A quadrant source edge cannot be repeated");
        }
        uniqueNames.push_back(source);
        const auto edge = exactEdge(view, source);
        if (!edge) {
            throw Base::ValueError(
                "The selected quadrant edge geometry is unavailable");
        }
        const auto quarterParameterPoints = edge->getQuads();
        if (quarterParameterPoints.size() != 3) {
            throw Base::RuntimeError(
                "TechDraw did not derive three ordered quarter-parameter points");
        }
        DrawingQuadrantVertexPlan sourcePlan;
        sourcePlan.sourceSelectionName = source;
        sourcePlan.vertices.reserve(quarterParameterPoints.size());
        for (const auto& point : quarterParameterPoints) {
            Base::Vector3d canonical = TechDraw::DrawUtil::invertY(point);
            canonical = TechDraw::CosmeticVertex::makeCanonicalPoint(view, canonical);
            if (!finitePoint(canonical)
                || std::abs(canonical.z) > Base::Vector3d::epsilon()) {
                throw Base::ValueError(
                    "A quadrant point exceeds supported Drawing-view coordinates");
            }
            sourcePlan.vertices.push_back({canonical, format});
        }
        result.sources.push_back(std::move(sourcePlan));
    }
    return result;
}

TechDrawGui::DrawingQuadrantVerticesResult
TechDrawGui::createDrawingQuadrantVertices(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames)
{
    const DrawingQuadrantVerticesPlan plan =
        validateDrawingQuadrantVertices(view, sourceSelectionNames);
    std::vector<std::string> tags;
    tags.reserve(plan.sources.size() * 3);
    for (const auto& source : plan.sources) {
        for (const auto& vertex : source.vertices) {
            tags.push_back(createVertex(view, vertex));
        }
    }
    finishMutation(view);
    for (const auto& tag : tags) {
        if (!view->getCosmeticVertex(tag)) {
            throw Base::RuntimeError(
                "A quadrant vertex did not retain its durable tag");
        }
    }
    return {plan, tags};
}

TechDrawGui::DrawingOffsetVertexPlan TechDrawGui::validateDrawingOffsetVertex(
    TechDraw::DrawViewPart* view,
    const std::string& sourceSelectionName,
    const Base::Vector3d& offsetInViewMm)
{
    requireLiveView(view);
    const auto source = exactVertex(view, sourceSelectionName);
    if (!source) {
        throw Base::ValueError(
            "The selected projected vertex is unavailable");
    }
    if (!finitePoint(offsetInViewMm) || std::abs(offsetInViewMm.z)
        > Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            "A Drawing offset vertex requires a finite two-dimensional offset");
    }
    const Base::Vector3d canonicalInverted =
        TechDraw::CosmeticVertex::makeCanonicalPointInverted(
            view,
            source->point());
    const Base::Vector3d sourcePoint =
        TechDraw::DrawUtil::invertY(canonicalInverted);
    const Base::Vector3d resultPoint = sourcePoint + offsetInViewMm;
    if (!finitePoint(sourcePoint) || !finitePoint(resultPoint)) {
        throw Base::ValueError(
            "The offset vertex exceeds supported coordinates");
    }
    return {
        sourceSelectionName,
        sourcePoint,
        offsetInViewMm,
        {resultPoint, defaultFormat()}};
}

TechDrawGui::DrawingOffsetVertexResult TechDrawGui::createDrawingOffsetVertex(
    TechDraw::DrawViewPart* view,
    const std::string& sourceSelectionName,
    const Base::Vector3d& offsetInViewMm)
{
    const DrawingOffsetVertexPlan plan =
        validateDrawingOffsetVertex(view, sourceSelectionName, offsetInViewMm);
    const std::string tag = createVertex(view, plan.vertex);
    finishMutation(view);
    if (!view->getCosmeticVertex(tag)) {
        throw Base::RuntimeError(
            "The offset vertex did not retain its durable tag");
    }
    return {plan, tag};
}

TechDrawGui::DrawingPersistentCosmeticVertexState
TechDrawGui::drawingPersistentCosmeticVertexState(
    TechDraw::DrawViewPart* view,
    const std::string& tag)
{
    requireLiveView(view);
    auto* vertex = tag.empty() ? nullptr : view->getCosmeticVertex(tag);
    if (!vertex) {
        throw Base::ValueError(
            "The exact persistent cosmetic vertex target is unavailable");
    }
    return persistentState(view, vertex);
}

std::vector<TechDrawGui::DrawingPersistentCosmeticVertexState>
TechDrawGui::drawingCosmeticVertexStates(TechDraw::DrawViewPart* view)
{
    requireLiveView(view);
    std::vector<DrawingPersistentCosmeticVertexState> result;
    const auto vertices = view->CosmeticVertexes.getValues();
    result.reserve(vertices.size());
    for (auto* vertex : vertices) {
        result.push_back(persistentState(view, vertex));
    }
    return result;
}
