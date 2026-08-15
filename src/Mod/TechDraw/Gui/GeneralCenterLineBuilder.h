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
#include <Mod/TechDraw/App/CenterLine.h>
#include <Mod/TechDraw/App/LineFormat.h>
#include <Mod/TechDraw/TechDrawGlobal.h>


namespace TechDraw
{
class DrawViewPart;
}

namespace TechDrawGui
{

enum class DrawingGeneralCenterLineKind
{
    Face,
    BetweenEdges,
    BetweenVertices,
};

struct TechDrawGuiExport DrawingGeneralCenterLineSettings
{
    TechDraw::CenterLine::Mode mode;
    double horizontalShiftMm;
    double verticalShiftMm;
    double rotationDegrees;
    double extensionMm;
    bool flip;
    TechDraw::LineFormat format;
};

struct TechDrawGuiExport DrawingGeneralCenterLinePlan
{
    DrawingGeneralCenterLineKind kind;
    std::vector<std::string> sourceSelectionNames;
    DrawingGeneralCenterLineSettings settings;
    Base::Vector3d startInViewMm;
    Base::Vector3d endInViewMm;
    double lengthMm;
};

struct TechDrawGuiExport DrawingGeneralCenterLineResult
{
    DrawingGeneralCenterLinePlan plan;
    std::string centerLineTag;
};

struct TechDrawGuiExport DrawingPersistentGeneralCenterLineState
{
    std::string tag;
    std::string selectionName;
    DrawingGeneralCenterLinePlan plan;
};

TechDrawGuiExport DrawingGeneralCenterLineSettings
drawingGeneralCenterLineDefaultSettings(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingGeneralCenterLinePlan validateDrawingGeneralCenterLine(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames,
    const DrawingGeneralCenterLineSettings& settings);

TechDrawGuiExport DrawingGeneralCenterLineResult createDrawingGeneralCenterLine(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames,
    const DrawingGeneralCenterLineSettings& settings);

TechDrawGuiExport DrawingGeneralCenterLinePlan
validateDrawingGeneralCenterLineWithDefaults(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingGeneralCenterLineResult
createDrawingGeneralCenterLineWithDefaults(
    TechDraw::DrawViewPart* view,
    DrawingGeneralCenterLineKind kind,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingPersistentGeneralCenterLineState
drawingPersistentGeneralCenterLineState(
    TechDraw::DrawViewPart* view,
    const std::string& tag);

TechDrawGuiExport std::vector<DrawingPersistentGeneralCenterLineState>
drawingGeneralCenterLineStates(TechDraw::DrawViewPart* view);

}  // namespace TechDrawGui
