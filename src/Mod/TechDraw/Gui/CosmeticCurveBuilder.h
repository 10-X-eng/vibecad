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

enum class DrawingCosmeticCurveKind
{
    OnePointCircle,
    TwoPointCircle,
    ThreePointCircle,
    CenterStartEndArc,
};

struct TechDrawGuiExport DrawingCosmeticCurvePlan
{
    DrawingCosmeticCurveKind kind;
    std::vector<std::string> sourceSelectionNames;
    std::vector<Base::Vector3d> sourcePointsInViewMm;
    Base::Vector3d centerInViewMm;
    double radiusMm;
    double startAngleDegrees;
    double endAngleDegrees;
    bool clockwise;
    TechDraw::LineFormat format;
};

struct TechDrawGuiExport DrawingCosmeticCurveResult
{
    DrawingCosmeticCurvePlan plan;
    std::string curveTag;
};

struct TechDrawGuiExport DrawingPersistentCosmeticCurveState
{
    std::string tag;
    std::string selectionName;
    bool circularArc;
    Base::Vector3d centerInViewMm;
    double radiusMm;
    double startAngleDegrees;
    double endAngleDegrees;
    bool clockwise;
    TechDraw::LineFormat format;
};

TechDrawGuiExport DrawingCosmeticCurvePlan validateDrawingCosmeticCurve(
    TechDraw::DrawViewPart* view,
    DrawingCosmeticCurveKind kind,
    const std::vector<std::string>& sourceSelectionNames,
    double explicitRadiusMm = 0.0);

TechDrawGuiExport DrawingCosmeticCurveResult createDrawingCosmeticCurve(
    TechDraw::DrawViewPart* view,
    DrawingCosmeticCurveKind kind,
    const std::vector<std::string>& sourceSelectionNames,
    double explicitRadiusMm = 0.0);

TechDrawGuiExport DrawingCosmeticCurveResult createDrawingCosmeticCircleAtCenter(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& centerInViewMm,
    double radiusMm);

TechDrawGuiExport DrawingCosmeticCurveResult createDrawingCosmeticArcAtCenter(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& centerInViewMm,
    double radiusMm,
    double startAngleDegrees,
    double endAngleDegrees);

TechDrawGuiExport DrawingPersistentCosmeticCurveState
drawingPersistentCosmeticCurveState(
    TechDraw::DrawViewPart* view,
    const std::string& tag);

TechDrawGuiExport std::vector<DrawingPersistentCosmeticCurveState>
drawingCosmeticCurveStates(TechDraw::DrawViewPart* view);

}  // namespace TechDrawGui
