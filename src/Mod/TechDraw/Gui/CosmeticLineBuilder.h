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

enum class DrawingCosmeticLineConstruction
{
    Parallel,
    Perpendicular,
};

struct TechDrawGuiExport DrawingCosmeticLineSegmentPlan
{
    Base::Vector3d startInViewMm;
    Base::Vector3d endInViewMm;
    double lengthMm;
    TechDraw::LineFormat format;
};

struct TechDrawGuiExport DrawingCosmeticLineSegmentResult
{
    DrawingCosmeticLineSegmentPlan plan;
    std::string lineTag;
};

struct TechDrawGuiExport DrawingTwoPointCosmeticLinePlan
{
    std::vector<std::string> sourceVertexNames;
    DrawingCosmeticLineSegmentPlan segment;
};

struct TechDrawGuiExport DrawingTwoPointCosmeticLineResult
{
    DrawingTwoPointCosmeticLinePlan plan;
    std::string lineTag;
};

struct TechDrawGuiExport DrawingCosmeticLinePlan
{
    DrawingCosmeticLineConstruction construction;
    std::string referenceEdgeName;
    std::string throughVertexName;
    Base::Vector3d referenceStartInViewMm;
    Base::Vector3d referenceEndInViewMm;
    Base::Vector3d throughPointInViewMm;
    Base::Vector3d startInViewMm;
    Base::Vector3d endInViewMm;
    double lengthMm;
    TechDraw::LineFormat format;
};

struct TechDrawGuiExport DrawingCosmeticLineResult
{
    DrawingCosmeticLinePlan plan;
    std::string lineTag;
};

struct TechDrawGuiExport DrawingPersistentCosmeticLineState
{
    std::string tag;
    std::string selectionName;
    Base::Vector3d startInViewMm;
    Base::Vector3d endInViewMm;
    double lengthMm;
    TechDraw::LineFormat format;
};

TechDrawGuiExport DrawingCosmeticLinePlan validateDrawingCosmeticLine(
    TechDraw::DrawViewPart* view,
    DrawingCosmeticLineConstruction construction,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingCosmeticLineResult createDrawingCosmeticLine(
    TechDraw::DrawViewPart* view,
    DrawingCosmeticLineConstruction construction,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingCosmeticLineSegmentPlan validateDrawingCosmeticLineSegment(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& startInViewMm,
    const Base::Vector3d& endInViewMm,
    const TechDraw::LineFormat& format);

TechDrawGuiExport DrawingCosmeticLineSegmentResult createDrawingCosmeticLineSegment(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& startInViewMm,
    const Base::Vector3d& endInViewMm,
    const TechDraw::LineFormat& format);

TechDrawGuiExport DrawingTwoPointCosmeticLinePlan
validateDrawingTwoPointCosmeticLine(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceVertexNames);

TechDrawGuiExport DrawingTwoPointCosmeticLineResult
createDrawingTwoPointCosmeticLine(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceVertexNames);

TechDrawGuiExport DrawingPersistentCosmeticLineState
drawingPersistentCosmeticLineState(
    TechDraw::DrawViewPart* view,
    const std::string& tag);

TechDrawGuiExport std::vector<DrawingPersistentCosmeticLineState>
drawingCosmeticLineStates(TechDraw::DrawViewPart* view);

}  // namespace TechDrawGui
