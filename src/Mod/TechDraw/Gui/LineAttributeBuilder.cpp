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

#include "LineAttributeBuilder.h"

#include <algorithm>
#include <cmath>
#include <set>
#include <tuple>

#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Mod/TechDraw/App/CenterLine.h>
#include <Mod/TechDraw/App/Cosmetic.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/Geometry.h>

#include "ViewProviderViewPart.h"


namespace
{

using TechDrawGui::DrawingLineAttributeState;
using TechDrawGui::DrawingLineKind;
using TechDrawGui::DrawingLineTarget;

void requireLiveView(TechDraw::DrawViewPart* view)
{
    if (!view || !view->getDocument() || !view->findParentPage()) {
        throw Base::ValueError("Drawing line attributes require a live view on a page");
    }
}

void requireFormat(const TechDraw::LineFormat& format)
{
    const Base::Color color = format.getColor();
    if (format.getLineNumber() <= 0 || format.getStyle() <= 0
        || !std::isfinite(format.getWidth()) || format.getWidth() < 0.0
        || format.getWidth() > 1000.0 || !std::isfinite(color.r)
        || !std::isfinite(color.g) || !std::isfinite(color.b)
        || color.r < 0.0F || color.r > 1.0F || color.g < 0.0F
        || color.g > 1.0F || color.b < 0.0F || color.b > 1.0F) {
        throw Base::ValueError("Drawing line attributes contain an invalid format");
    }
}

std::string kindName(DrawingLineKind kind)
{
    switch (kind) {
        case DrawingLineKind::ProjectedEdge:
            return "projected edge";
        case DrawingLineKind::CosmeticEdge:
            return "cosmetic edge";
        case DrawingLineKind::CenterLine:
            return "centerline";
    }
    throw Base::ValueError("Unsupported Drawing line target kind");
}

TechDrawGui::ViewProviderViewPart* viewProviderFor(TechDraw::DrawViewPart* view)
{
    auto* document = view ? view->getDocument() : nullptr;
    auto* guiDocument = document
        ? Gui::Application::Instance->getDocument(document)
        : nullptr;
    auto* provider = guiDocument
        ? dynamic_cast<TechDrawGui::ViewProviderViewPart*>(
            guiDocument->getViewProvider(view))
        : nullptr;
    if (!provider) {
        throw Base::ValueError(
            "The Drawing view has no live graphical view provider");
    }
    return provider;
}

TechDraw::BaseGeomPtr projectedGeometry(
    TechDraw::DrawViewPart* view,
    const std::string& subelementName)
{
    if (TechDraw::DrawUtil::getGeomTypeFromName(subelementName) != "Edge") {
        return {};
    }
    TechDraw::BaseGeomPtr geometry;
    try {
        geometry = view->getGeomByIndex(
            TechDraw::DrawUtil::getIndexFromName(subelementName));
    }
    catch (const Base::Exception&) {
        return {};
    }
    return geometry && !geometry->getCosmetic() ? geometry : TechDraw::BaseGeomPtr{};
}

TechDraw::LineFormat projectedDefaultFormat(TechDraw::DrawViewPart* view)
{
    auto* provider = viewProviderFor(view);
    return TechDraw::LineFormat(
        Qt::SolidLine,
        provider->LineWidth.getValue(),
        TechDraw::LineFormat::getDefEdgeColor(),
        true);
}

TechDraw::LineFormat* resolveFormat(
    TechDraw::DrawViewPart* view,
    const DrawingLineTarget& target,
    bool create = false,
    std::string* createdFormatTag = nullptr)
{
    if (target.tag.empty()) {
        throw Base::ValueError("A Drawing line target requires an exact reference");
    }
    if (target.kind == DrawingLineKind::ProjectedEdge) {
        if (!projectedGeometry(view, target.tag)) {
            return nullptr;
        }
        auto* format = view->getGeomFormatBySelection(target.tag);
        if (!format && create) {
            const TechDraw::LineFormat defaultFormat = projectedDefaultFormat(view);
            TechDraw::GeomFormat geometryFormat(
                TechDraw::DrawUtil::getIndexFromName(target.tag),
                defaultFormat);
            const std::string formatTag = view->addGeomFormat(&geometryFormat);
            if (createdFormatTag) {
                *createdFormatTag = formatTag;
            }
            format = view->getGeomFormat(formatTag);
        }
        return format ? &format->m_format : nullptr;
    }
    if (target.kind == DrawingLineKind::CosmeticEdge) {
        auto* edge = view->getCosmeticEdge(target.tag);
        return edge ? &edge->m_format : nullptr;
    }
    auto* line = view->getCenterLine(target.tag);
    return line ? &line->m_format : nullptr;
}

void requireTarget(TechDraw::DrawViewPart* view, const DrawingLineTarget& target)
{
    if (target.kind == DrawingLineKind::ProjectedEdge) {
        if (!projectedGeometry(view, target.tag)) {
            throw Base::ValueError(
                "The exact Drawing projected edge target is unavailable");
        }
        return;
    }
    if (!resolveFormat(view, target)) {
        throw Base::ValueError(
            "The exact Drawing " + kindName(target.kind) + " target is unavailable");
    }
}

std::string selectionName(
    TechDraw::DrawViewPart* view,
    const DrawingLineTarget& target)
{
    if (target.kind == DrawingLineKind::ProjectedEdge) {
        return target.tag;
    }
    const auto geometry = view->getEdgeGeometry();
    const TechDraw::SourceType expectedSource =
        target.kind == DrawingLineKind::CosmeticEdge
        ? TechDraw::SourceType::COSMETICEDGE
        : TechDraw::SourceType::CENTERLINE;
    for (std::size_t index = 0; index < geometry.size(); ++index) {
        const auto& edge = geometry.at(index);
        if (edge && edge->source() == expectedSource
            && edge->getCosmeticTag() == target.tag) {
            return "Edge" + std::to_string(index);
        }
    }
    return {};
}

DrawingLineAttributeState state(
    TechDraw::DrawViewPart* view,
    const DrawingLineTarget& target)
{
    TechDraw::LineFormat fallback;
    TechDraw::LineFormat* format = resolveFormat(view, target);
    if (!format && target.kind == DrawingLineKind::ProjectedEdge) {
        fallback = projectedDefaultFormat(view);
        format = &fallback;
    }
    if (!format) {
        throw Base::ValueError(
            "The exact Drawing " + kindName(target.kind) + " target is unavailable");
    }
    return {target, selectionName(view, target), *format};
}

std::vector<DrawingLineTarget> uniqueTargets(
    TechDraw::DrawViewPart* view,
    const std::vector<DrawingLineTarget>& targets)
{
    if (targets.empty()) {
        throw Base::ValueError("At least one exact Drawing line target is required");
    }
    std::set<std::tuple<int, std::string>> seen;
    std::vector<DrawingLineTarget> result;
    result.reserve(targets.size());
    for (const auto& target : targets) {
        requireTarget(view, target);
        const auto key = std::make_tuple(static_cast<int>(target.kind), target.tag);
        if (!seen.insert(key).second) {
            throw Base::ValueError("A Drawing line target was provided more than once");
        }
        result.push_back(target);
    }
    return result;
}

}  // namespace

TechDraw::LineFormat* TechDrawGui::drawingLineFormatFromSelection(
    TechDraw::DrawViewPart* view,
    const std::string& subelementName,
    bool create,
    std::string* createdFormatTag)
{
    requireLiveView(view);
    TechDraw::BaseGeomPtr geometry;
    try {
        geometry = view->getGeomByIndex(
            TechDraw::DrawUtil::getIndexFromName(subelementName));
    }
    catch (const Base::Exception&) {
        return nullptr;
    }
    if (!geometry) {
        return nullptr;
    }
    if (!geometry->getCosmetic()) {
        return resolveFormat(
            view,
            {DrawingLineKind::ProjectedEdge, subelementName},
            create,
            createdFormatTag);
    }
    if (geometry->source() == TechDraw::SourceType::COSMETICEDGE) {
        return resolveFormat(
            view,
            {DrawingLineKind::CosmeticEdge, geometry->getCosmeticTag()});
    }
    if (geometry->source() == TechDraw::SourceType::CENTERLINE) {
        return resolveFormat(
            view,
            {DrawingLineKind::CenterLine, geometry->getCosmeticTag()});
    }
    return nullptr;
}

std::vector<TechDrawGui::DrawingLineTarget>
TechDrawGui::drawingLineTargetsFromSelection(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& subelementNames)
{
    requireLiveView(view);
    std::vector<DrawingLineTarget> result;
    std::set<std::tuple<int, std::string>> seen;
    for (const auto& name : subelementNames) {
        TechDraw::BaseGeomPtr geometry;
        try {
            geometry = view->getGeomByIndex(TechDraw::DrawUtil::getIndexFromName(name));
        }
        catch (const Base::Exception&) {
            continue;
        }
        if (!geometry || !geometry->getCosmetic() || geometry->getCosmeticTag().empty()) {
            if (geometry && !geometry->getCosmetic()) {
                DrawingLineTarget target{DrawingLineKind::ProjectedEdge, name};
                const auto key = std::make_tuple(
                    static_cast<int>(target.kind),
                    target.tag);
                if (seen.insert(key).second) {
                    result.push_back(std::move(target));
                }
            }
            continue;
        }
        DrawingLineKind kind;
        if (geometry->source() == TechDraw::SourceType::COSMETICEDGE) {
            kind = DrawingLineKind::CosmeticEdge;
        }
        else if (geometry->source() == TechDraw::SourceType::CENTERLINE) {
            kind = DrawingLineKind::CenterLine;
        }
        else {
            continue;
        }
        DrawingLineTarget target{kind, geometry->getCosmeticTag()};
        requireTarget(view, target);
        const auto key = std::make_tuple(static_cast<int>(kind), target.tag);
        if (seen.insert(key).second) {
            result.push_back(std::move(target));
        }
    }
    return result;
}

std::vector<TechDrawGui::DrawingLineAttributeState>
TechDrawGui::drawingLineAttributeStates(TechDraw::DrawViewPart* view)
{
    requireLiveView(view);
    std::vector<DrawingLineAttributeState> result;
    const auto geometry = view->getEdgeGeometry();
    const auto cosmeticEdges = view->CosmeticEdges.getValues();
    const auto centerLines = view->CenterLines.getValues();
    result.reserve(geometry.size() + cosmeticEdges.size() + centerLines.size());
    for (std::size_t index = 0; index < geometry.size(); ++index) {
        const auto& edge = geometry.at(index);
        if (edge && !edge->getCosmetic()) {
            result.push_back(state(
                view,
                {DrawingLineKind::ProjectedEdge, "Edge" + std::to_string(index)}));
        }
    }
    for (const auto* edge : cosmeticEdges) {
        if (edge) {
            result.push_back(state(
                view,
                {DrawingLineKind::CosmeticEdge, edge->getTagAsString()}));
        }
    }
    for (const auto* line : centerLines) {
        if (line) {
            result.push_back(state(
                view,
                {DrawingLineKind::CenterLine, line->getTagAsString()}));
        }
    }
    return result;
}

std::vector<TechDrawGui::DrawingLineAttributeState>
TechDrawGui::changeDrawingLineAttributes(
    TechDraw::DrawViewPart* view,
    const std::vector<DrawingLineTarget>& targets,
    const TechDraw::LineFormat& format)
{
    requireLiveView(view);
    requireFormat(format);
    const auto exactTargets = uniqueTargets(view, targets);
    const bool changesCosmeticEdges = std::any_of(
        exactTargets.begin(),
        exactTargets.end(),
        [](const auto& target) { return target.kind == DrawingLineKind::CosmeticEdge; });
    const bool changesCenterLines = std::any_of(
        exactTargets.begin(),
        exactTargets.end(),
        [](const auto& target) { return target.kind == DrawingLineKind::CenterLine; });
    const bool changesProjectedEdges = std::any_of(
        exactTargets.begin(),
        exactTargets.end(),
        [](const auto& target) { return target.kind == DrawingLineKind::ProjectedEdge; });

    if (changesProjectedEdges) {
        view->GeomFormats.setValues(view->GeomFormats.getValues());
    }
    if (changesCosmeticEdges) {
        view->CosmeticEdges.setValues(view->CosmeticEdges.getValues());
    }
    if (changesCenterLines) {
        view->CenterLines.setValues(view->CenterLines.getValues());
    }
    for (const auto& target : exactTargets) {
        TechDraw::LineFormat* targetFormat = resolveFormat(view, target, true);
        if (!targetFormat) {
            throw Base::RuntimeError("A validated Drawing line target disappeared");
        }
        *targetFormat = format;
    }
    if (changesCosmeticEdges) {
        view->CosmeticEdges.setValues(view->CosmeticEdges.getValues());
        view->refreshCEGeoms();
    }
    if (changesCenterLines) {
        view->CenterLines.setValues(view->CenterLines.getValues());
        view->refreshCLGeoms();
    }
    if (changesProjectedEdges) {
        view->GeomFormats.setValues(view->GeomFormats.getValues());
    }
    view->requestPaint();

    std::vector<DrawingLineAttributeState> result;
    result.reserve(exactTargets.size());
    for (const auto& target : exactTargets) {
        result.push_back(state(view, target));
    }
    return result;
}
