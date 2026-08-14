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

struct TechDrawGuiExport DrawingCircleCenterLinePlan
{
    std::string sourceSelectionName;
    std::string geometryConfiguration;
    Base::Vector3d centerInViewMm;
    double radiusMm;
    double outsideExtensionMm;
    Base::Vector3d horizontalStartInViewMm;
    Base::Vector3d horizontalEndInViewMm;
    Base::Vector3d verticalStartInViewMm;
    Base::Vector3d verticalEndInViewMm;
    TechDraw::LineFormat format;
};

struct TechDrawGuiExport DrawingCircleCenterLineResult
{
    DrawingCircleCenterLinePlan plan;
    std::string horizontalTag;
    std::string verticalTag;
};

struct TechDrawGuiExport DrawingBoltCircleHolePlan
{
    std::string sourceSelectionName;
    std::string geometryConfiguration;
    Base::Vector3d centerInViewMm;
    double radiusMm;
    double patternRadiusAtCenterMm;
    double patternRadiusDeviationMm;
    Base::Vector3d centerLineStartInViewMm;
    Base::Vector3d centerLineEndInViewMm;
};

struct TechDrawGuiExport DrawingBoltCircleCenterLinePlan
{
    Base::Vector3d patternCenterInViewMm;
    double patternRadiusMm;
    double maximumPatternRadiusDeviationMm;
    double patternRadiusToleranceMm;
    bool allCentersOnPattern;
    double holeCenterLineExtensionFactor;
    TechDraw::LineFormat format;
    std::vector<DrawingBoltCircleHolePlan> holes;
};

struct TechDrawGuiExport DrawingBoltCircleCenterLineResult
{
    DrawingBoltCircleCenterLinePlan plan;
    std::string patternCircleTag;
    std::vector<std::string> holeCenterLineTags;
};

struct TechDrawGuiExport DrawingPersistentCosmeticCircleState
{
    std::string tag;
    std::string selectionName;
    Base::Vector3d centerInViewMm;
    double radiusMm;
    TechDraw::LineFormat format;
};

TechDrawGuiExport std::vector<DrawingCircleCenterLinePlan>
validateDrawingCircleCenterLines(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport std::vector<DrawingCircleCenterLineResult>
createDrawingCircleCenterLines(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingBoltCircleCenterLinePlan
validateDrawingBoltCircleCenterLines(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingBoltCircleCenterLineResult
createDrawingBoltCircleCenterLines(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingPersistentCosmeticCircleState
drawingPersistentCosmeticCircleState(
    TechDraw::DrawViewPart* view,
    const std::string& tag);

}  // namespace TechDrawGui
