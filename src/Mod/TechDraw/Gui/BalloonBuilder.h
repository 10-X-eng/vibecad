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

#pragma once

#include <optional>
#include <string>
#include <vector>

#include <Base/Vector3D.h>
#include <Mod/TechDraw/TechDrawGlobal.h>


namespace TechDraw
{
class DrawView;
class DrawViewBalloon;
class DrawViewPart;
}

namespace TechDrawGui
{

struct TechDrawGuiExport ProjectedBalloonAnchor
{
    std::string elementType;
    Base::Vector3d pointInViewMm;
    Base::Vector3d pointInSourceMm;
};

enum class MeasurementAnnotationKind
{
    Area,
    ArcLength,
};

struct TechDrawGuiExport ProjectedMeasurementAnnotation
{
    MeasurementAnnotationKind kind;
    std::vector<std::string> elements;
    double value;
    Base::Vector3d anchorInViewMm;
    Base::Vector3d anchorInSourceMm;
    std::string text;
};

TechDrawGuiExport ProjectedBalloonAnchor validateProjectedBalloonAnchor(
    TechDraw::DrawViewPart* view,
    const std::string& elementName);

TechDrawGuiExport TechDraw::DrawViewBalloon* createBalloonFeature(
    TechDraw::DrawView* sourceView,
    const Base::Vector3d& anchorInSourceMm,
    const Base::Vector3d& bubbleInSourceMm,
    const std::optional<std::string>& text,
    const std::optional<std::string>& anchorElement,
    const std::optional<std::string>& label);

TechDrawGuiExport TechDraw::DrawViewBalloon* createProjectedBalloonFeature(
    TechDraw::DrawViewPart* sourceView,
    const std::string& elementName,
    const std::string& text,
    const std::string& label,
    const Base::Vector3d& bubbleOffsetInViewMm);

TechDrawGuiExport ProjectedMeasurementAnnotation validateProjectedMeasurementAnnotation(
    TechDraw::DrawViewPart* view,
    MeasurementAnnotationKind kind,
    const std::vector<std::string>& elements);

TechDrawGuiExport TechDraw::DrawViewBalloon* createProjectedMeasurementAnnotationFeature(
    TechDraw::DrawViewPart* sourceView,
    MeasurementAnnotationKind kind,
    const std::vector<std::string>& elements,
    const std::optional<std::string>& label);

}  // namespace TechDrawGui
