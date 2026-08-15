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

#include "GeneralCenterLineBuilder.h"

#include <algorithm>
#include <cmath>
#include <memory>
#include <regex>

#include <Base/Exception.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Geometry.h>
#include <Mod/TechDraw/App/Preferences.h>


namespace
{

using Kind = TechDrawGui::DrawingGeneralCenterLineKind;
using Mode = TechDraw::CenterLine::Mode;
using Plan = TechDrawGui::DrawingGeneralCenterLinePlan;
using Settings = TechDrawGui::DrawingGeneralCenterLineSettings;

constexpr std::size_t MaximumFaceTargets = 64;
constexpr double MaximumCoordinateMm = 1'000'000'000.0;
const std::regex FaceNamePattern("^Face(?:0|[1-9][0-9]*)$");
const std::regex EdgeNamePattern("^Edge(?:0|[1-9][0-9]*)$");
const std::regex VertexNamePattern("^Vertex(?:0|[1-9][0-9]*)$");

void requireLiveView(TechDraw::DrawViewPart* view)
{
    auto* page = view ? view->findParentPage() : nullptr;
    if (!view || !view->getDocument() || !page
        || page->getDocument() != view->getDocument()) {
        throw Base::ValueError(
            "Centerlines require a live Drawing view on a page");
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

void requireFiniteSetting(double value, const char* noun)
{
    if (!std::isfinite(value) || std::abs(value) > MaximumCoordinateMm) {
        throw Base::ValueError(
            std::string("The centerline ") + noun + " is outside the supported range");
    }
}

void validateSources(Kind kind, const std::vector<std::string>& names)
{
    if (kind == Kind::Face) {
        if (names.empty() || names.size() > MaximumFaceTargets) {
            throw Base::ValueError(
                "A face centerline requires between one and 64 exact FaceN targets");
        }
    }
    else if (names.size() != 2 || names.at(0) == names.at(1)) {
        throw Base::ValueError(
            "A between-geometry centerline requires exactly two different targets");
    }
    const std::regex& pattern = kind == Kind::Face
        ? FaceNamePattern
        : kind == Kind::BetweenEdges ? EdgeNamePattern : VertexNamePattern;
    std::vector<std::string> seen;
    seen.reserve(names.size());
    for (const auto& name : names) {
        if (!std::regex_match(name, pattern)) {
            throw Base::ValueError(
                "Centerline sources must use exact same-kind FaceN, EdgeN, or VertexN names");
        }
        if (std::find(seen.begin(), seen.end(), name) != seen.end()) {
            throw Base::ValueError("A centerline source cannot be repeated");
        }
        seen.push_back(name);
    }
}

Mode resolvedMode(
    TechDraw::DrawViewPart* view,
    Kind kind,
    const std::vector<std::string>& names,
    Mode requested)
{
    if (kind == Kind::Face) {
        return requested == Mode::ALIGNED ? Mode::VERTICAL : requested;
    }
    if (kind == Kind::BetweenEdges) {
        const auto first = view->getEdge(names.at(0));
        const auto second = view->getEdge(names.at(1));
        if (!first || !second) {
            throw Base::ValueError(
                "An exact projected edge centerline source is unavailable");
        }
        const auto firstEnds = first->findEndPoints();
        const auto secondEnds = second->findEndPoints();
        if (firstEnds.size() < 2 || secondEnds.size() < 2) {
            throw Base::ValueError(
                "A projected edge cannot provide centerline endpoints");
        }
        const bool bothVertical =
            TechDraw::DrawUtil::fpCompare(firstEnds.front().x, firstEnds.back().x, EWTOLERANCE)
            && TechDraw::DrawUtil::fpCompare(secondEnds.front().x, secondEnds.back().x, EWTOLERANCE);
        const bool bothHorizontal =
            TechDraw::DrawUtil::fpCompare(firstEnds.front().y, firstEnds.back().y, EWTOLERANCE)
            && TechDraw::DrawUtil::fpCompare(secondEnds.front().y, secondEnds.back().y, EWTOLERANCE);
        if (bothVertical) {
            return Mode::VERTICAL;
        }
        if (bothHorizontal) {
            return Mode::HORIZONTAL;
        }
        return requested;
    }
    const auto first = view->getVertex(names.at(0));
    const auto second = view->getVertex(names.at(1));
    if (!first || !second) {
        throw Base::ValueError(
            "An exact projected vertex centerline source is unavailable");
    }
    if (TechDraw::DrawUtil::fpCompare(
            first->point().x, second->point().x, EWTOLERANCE)) {
        return Mode::HORIZONTAL;
    }
    if (TechDraw::DrawUtil::fpCompare(
            first->point().y, second->point().y, EWTOLERANCE)) {
        return Mode::VERTICAL;
    }
    return requested;
}

std::pair<Base::Vector3d, Base::Vector3d> calculateEndpoints(
    TechDraw::DrawViewPart* view,
    Kind kind,
    const std::vector<std::string>& names,
    const Settings& settings)
{
    if (kind == Kind::Face) {
        return TechDraw::CenterLine::calcEndPoints(
            view,
            names,
            settings.mode,
            settings.extensionMm,
            settings.horizontalShiftMm,
            settings.verticalShiftMm,
            settings.rotationDegrees);
    }
    if (kind == Kind::BetweenEdges) {
        return TechDraw::CenterLine::calcEndPoints2Lines(
            view,
            names,
            settings.mode,
            settings.extensionMm,
            settings.horizontalShiftMm,
            settings.verticalShiftMm,
            settings.rotationDegrees,
            settings.flip);
    }
    return TechDraw::CenterLine::calcEndPoints2Points(
        view,
        names,
        settings.mode,
        settings.extensionMm,
        settings.horizontalShiftMm,
        settings.verticalShiftMm,
        settings.rotationDegrees,
        settings.flip);
}

void applySettings(TechDraw::CenterLine* line, const Settings& settings)
{
    line->m_mode = settings.mode;
    line->setShifts(settings.horizontalShiftMm, settings.verticalShiftMm);
    line->setRotate(settings.rotationDegrees);
    line->setExtend(settings.extensionMm);
    line->setFlip(settings.flip);
    line->m_format = settings.format;
}

std::string selectionNameForTag(
    TechDraw::DrawViewPart* view,
    const std::string& tag)
{
    std::string result;
    const auto geometry = view->getEdgeGeometry();
    for (std::size_t index = 0; index < geometry.size(); ++index) {
        const auto& edge = geometry.at(index);
        if (!edge || edge->source() != TechDraw::SourceType::CENTERLINE
            || edge->getCosmeticTag() != tag) {
            continue;
        }
        if (!result.empty()) {
            throw Base::RuntimeError(
                "A persistent centerline resolves to multiple projected edges");
        }
        result = "Edge" + std::to_string(index);
    }
    if (result.empty()) {
        throw Base::RuntimeError(
            "The persistent centerline is missing from projected geometry");
    }
    return result;
}

Kind kindForLine(const TechDraw::CenterLine* line)
{
    if (!line) {
        throw Base::ValueError("The exact persistent centerline is unavailable");
    }
    if (line->m_type == TechDraw::CenterLine::Type::FACE) {
        return Kind::Face;
    }
    if (line->m_type == TechDraw::CenterLine::Type::EDGE) {
        return Kind::BetweenEdges;
    }
    return Kind::BetweenVertices;
}

const std::vector<std::string>& sourcesForLine(const TechDraw::CenterLine* line)
{
    if (line->m_type == TechDraw::CenterLine::Type::FACE) {
        return line->m_faces;
    }
    if (line->m_type == TechDraw::CenterLine::Type::EDGE) {
        return line->m_edges;
    }
    return line->m_verts;
}

}  // namespace

TechDrawGui::DrawingGeneralCenterLineSettings
TechDrawGui::drawingGeneralCenterLineDefaultSettings(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames)
{
    requireLiveView(view);
    validateSources(kind, sourceSelectionNames);
    TechDraw::LineFormat format = TechDraw::LineFormat::getCurrentLineFormat();
    format.setLineNumber(TechDraw::Preferences::CenterLineStyle());
    format.setVisible(true);
    Settings result {
        Mode::VERTICAL,
        0.0,
        0.0,
        0.0,
        TechDraw::Preferences::getPreferenceGroup("Decorations")
            ->GetFloat("CosmoCLExtend", 3.0),
        false,
        format};
    result.mode = resolvedMode(view, kind, sourceSelectionNames, result.mode);
    return result;
}

TechDrawGui::DrawingGeneralCenterLinePlan
TechDrawGui::validateDrawingGeneralCenterLine(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames,
    const DrawingGeneralCenterLineSettings& settings)
{
    requireLiveView(view);
    validateSources(kind, sourceSelectionNames);
    Settings exactSettings = settings;
    exactSettings.mode = resolvedMode(
        view, kind, sourceSelectionNames, settings.mode);
    requireFiniteSetting(exactSettings.horizontalShiftMm, "horizontal shift");
    requireFiniteSetting(exactSettings.verticalShiftMm, "vertical shift");
    requireFiniteSetting(exactSettings.rotationDegrees, "rotation");
    requireFiniteSetting(exactSettings.extensionMm, "extension");
    if (exactSettings.extensionMm < 0.0) {
        throw Base::ValueError("The centerline extension cannot be negative");
    }
    std::unique_ptr<TechDraw::CenterLine> candidate(
        TechDraw::CenterLine::CenterLineBuilder(
            view,
            sourceSelectionNames,
            exactSettings.mode,
            exactSettings.flip));
    if (!candidate) {
        throw Base::ValueError(
            "The exact projected geometry cannot produce a centerline");
    }
    applySettings(candidate.get(), exactSettings);
    const auto endpoints = calculateEndpoints(
        view, kind, sourceSelectionNames, exactSettings);
    const double length = (endpoints.second - endpoints.first).Length();
    if (!finitePoint(endpoints.first) || !finitePoint(endpoints.second)
        || !std::isfinite(length) || length <= Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            "The centerline has invalid or zero-length Drawing-view geometry");
    }
    return {
        kind,
        sourceSelectionNames,
        exactSettings,
        endpoints.first,
        endpoints.second,
        length};
}

TechDrawGui::DrawingGeneralCenterLineResult
TechDrawGui::createDrawingGeneralCenterLine(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames,
    const DrawingGeneralCenterLineSettings& settings)
{
    const Plan plan = validateDrawingGeneralCenterLine(
        view, kind, sourceSelectionNames, settings);
    std::unique_ptr<TechDraw::CenterLine> line(
        TechDraw::CenterLine::CenterLineBuilder(
            view,
            sourceSelectionNames,
            plan.settings.mode,
            plan.settings.flip));
    if (!line) {
        throw Base::RuntimeError(
            "The validated centerline could not be rebuilt for persistence");
    }
    applySettings(line.get(), plan.settings);
    const std::string tag = view->addCenterLine(line.release());
    if (tag.empty() || !view->getCenterLine(tag)) {
        throw Base::RuntimeError("The persistent centerline could not be created");
    }
    view->recomputeFeature();
    view->refreshCLGeoms();
    view->requestPaint();
    if (selectionNameForTag(view, tag).empty()) {
        throw Base::RuntimeError(
            "The centerline did not retain its exact projected identity");
    }
    return {plan, tag};
}

TechDrawGui::DrawingGeneralCenterLinePlan
TechDrawGui::validateDrawingGeneralCenterLineWithDefaults(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames)
{
    return validateDrawingGeneralCenterLine(
        view,
        kind,
        sourceSelectionNames,
        drawingGeneralCenterLineDefaultSettings(
            view, kind, sourceSelectionNames));
}

TechDrawGui::DrawingGeneralCenterLineResult
TechDrawGui::createDrawingGeneralCenterLineWithDefaults(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames)
{
    return createDrawingGeneralCenterLine(
        view,
        kind,
        sourceSelectionNames,
        drawingGeneralCenterLineDefaultSettings(
            view, kind, sourceSelectionNames));
}

TechDrawGui::DrawingPersistentGeneralCenterLineState
TechDrawGui::drawingPersistentGeneralCenterLineState(
    TechDraw::DrawViewPart* view,
    const std::string& tag)
{
    requireLiveView(view);
    auto* line = tag.empty() ? nullptr : view->getCenterLine(tag);
    const Kind kind = kindForLine(line);
    const auto& sources = sourcesForLine(line);
    const Settings settings {
        line->m_mode,
        line->m_hShift,
        line->m_vShift,
        line->m_rotate,
        line->m_extendBy,
        line->m_flip2Line,
        line->m_format};
    return {
        tag,
        selectionNameForTag(view, tag),
        validateDrawingGeneralCenterLine(view, kind, sources, settings)};
}

std::vector<TechDrawGui::DrawingPersistentGeneralCenterLineState>
TechDrawGui::drawingGeneralCenterLineStates(TechDraw::DrawViewPart* view)
{
    requireLiveView(view);
    std::vector<DrawingPersistentGeneralCenterLineState> result;
    for (auto* line : view->CenterLines.getValues()) {
        if (line) {
            result.push_back(drawingPersistentGeneralCenterLineState(
                view, line->getTagAsString()));
        }
    }
    return result;
}
