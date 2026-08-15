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

#include <string>
#include <vector>

#include <Base/Vector3D.h>
#include <Mod/TechDraw/App/LineFormat.h>
#include <Mod/TechDraw/TechDrawGlobal.h>


namespace TechDraw
{
class DrawViewPart;
}

namespace TechDrawGui
{

enum class DrawingThreadRepresentationKind
{
    HoleSide,
    HoleBottom,
    BoltSide,
    BoltBottom,
};

struct TechDrawGuiExport DrawingThreadLinePlan
{
    std::string role;
    Base::Vector3d startInViewMm;
    Base::Vector3d endInViewMm;
    TechDraw::LineFormat format;
};

struct TechDrawGuiExport DrawingThreadSidePlan
{
    DrawingThreadRepresentationKind kind;
    double threadFactor;
    double sourceDiameterMm;
    std::vector<std::string> sourceSelectionNames;
    Base::Vector3d firstStartInViewMm;
    Base::Vector3d firstEndInViewMm;
    Base::Vector3d secondStartInViewMm;
    Base::Vector3d secondEndInViewMm;
    std::vector<DrawingThreadLinePlan> lines;
};

struct TechDrawGuiExport DrawingThreadSideResult
{
    DrawingThreadSidePlan plan;
    std::vector<std::string> lineTags;
};

struct TechDrawGuiExport DrawingThreadBottomPlan
{
    DrawingThreadRepresentationKind kind;
    std::string sourceSelectionName;
    Base::Vector3d centerInViewMm;
    double sourceRadiusMm;
    double threadFactor;
    double threadRadiusMm;
    double startAngleDegrees;
    double endAngleDegrees;
    TechDraw::LineFormat format;
};

struct TechDrawGuiExport DrawingThreadBottomResult
{
    DrawingThreadBottomPlan plan;
    std::string arcTag;
};

struct TechDrawGuiExport DrawingPersistentCosmeticArcState
{
    std::string tag;
    std::string selectionName;
    Base::Vector3d centerInViewMm;
    double radiusMm;
    double startAngleDegrees;
    double endAngleDegrees;
    bool clockwise;
    TechDraw::LineFormat format;
};

TechDrawGuiExport DrawingThreadSidePlan validateDrawingThreadSide(
    TechDraw::DrawViewPart* view,
    DrawingThreadRepresentationKind kind,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingThreadSideResult createDrawingThreadSide(
    TechDraw::DrawViewPart* view,
    DrawingThreadRepresentationKind kind,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport std::vector<DrawingThreadBottomPlan>
validateDrawingThreadBottom(
    TechDraw::DrawViewPart* view,
    DrawingThreadRepresentationKind kind,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport std::vector<DrawingThreadBottomResult>
createDrawingThreadBottom(
    TechDraw::DrawViewPart* view,
    DrawingThreadRepresentationKind kind,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingPersistentCosmeticArcState
drawingPersistentCosmeticArcState(
    TechDraw::DrawViewPart* view,
    const std::string& tag);

}  // namespace TechDrawGui
