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

#include <Mod/TechDraw/TechDrawGlobal.h>


namespace TechDraw
{
class DrawPage;
class DrawView;
class DrawViewSymbol;
}

namespace TechDrawGui
{

enum class DrawingSurfaceFinishStandard
{
    ISO,
    ASME,
};

enum class DrawingSurfaceFinishType
{
    AnyMethod,
    RemovalProhibited,
    RemovalRequired,
    AnyMethodAllAround,
    RemovalProhibitedAllAround,
    RemovalRequiredAllAround,
};

struct TechDrawGuiExport DrawingSurfaceFinishSpec
{
    DrawingSurfaceFinishStandard standard;
    DrawingSurfaceFinishType symbolType;
    std::string method;
    std::string machiningAllowance;
    std::string lay;
    std::string isoRoughness;
    std::string samplingLength;
    std::string minimumRoughnessGrade;
    std::string maximumRoughnessGrade;
    double rotationDegrees;
    std::string preferredLabel;
};

struct TechDrawGuiExport DrawingSurfaceFinishPlan
{
    TechDraw::DrawPage* page;
    TechDraw::DrawView* owner;
    std::string objectName;
    std::string label;
    double xMm;
    double yMm;
    DrawingSurfaceFinishSpec spec;
    std::string svg;
    std::string svgSha256;
};

TechDrawGuiExport DrawingSurfaceFinishPlan validateDrawingSurfaceFinishSymbol(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    double xMm,
    double yMm,
    const DrawingSurfaceFinishSpec& spec);

TechDrawGuiExport TechDraw::DrawViewSymbol* createDrawingSurfaceFinishSymbol(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    double xMm,
    double yMm,
    const DrawingSurfaceFinishSpec& spec,
    DrawingSurfaceFinishPlan* appliedPlan = nullptr);

}  // namespace TechDrawGui
