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

#include <Base/Color.h>
#include <Base/Vector3D.h>
#include <Mod/TechDraw/TechDrawGlobal.h>


namespace TechDraw
{
class DrawLeaderLine;
class DrawPage;
class DrawView;
}

namespace TechDrawGui
{

struct TechDrawGuiExport DrawingLeaderStyle
{
    int startSymbol;
    int endSymbol;
    bool scalable;
    bool autoHorizontal;
    bool rotatesWithParent;
    double lineWidthMm;
    int lineStyle;
    Base::Color lineColor;
};

struct TechDrawGuiExport DrawingLeaderDefaults
{
    DrawingLeaderStyle style;
};

struct TechDrawGuiExport DrawingLeaderPlan
{
    TechDraw::DrawPage* page;
    TechDraw::DrawView* owner;
    std::string objectName;
    std::string label;
    std::vector<Base::Vector3d> requestedPointsOnPageMm;
    Base::Vector3d ownerPositionOnPageMm;
    double ownerScale;
    double ownerRotationDegrees;
    Base::Vector3d anchorInOwnerMm;
    std::vector<Base::Vector3d> storedWayPoints;
    std::vector<Base::Vector3d> renderedPointsOnPageMm;
    DrawingLeaderStyle style;
};

TechDrawGuiExport DrawingLeaderDefaults drawingLeaderDefaults();

TechDrawGuiExport DrawingLeaderPlan validateDrawingLeaderLine(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    const std::vector<Base::Vector3d>& pointsOnPageMm,
    const std::string& preferredLabel,
    const DrawingLeaderStyle& style);

TechDrawGuiExport TechDraw::DrawLeaderLine* createDrawingLeaderLine(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    const std::vector<Base::Vector3d>& pointsOnPageMm,
    const std::string& preferredLabel,
    const DrawingLeaderStyle& style,
    DrawingLeaderPlan* appliedPlan = nullptr);

}  // namespace TechDrawGui
