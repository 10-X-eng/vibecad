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
class DrawGeomHatch;
class DrawHatch;
class DrawPage;
class DrawViewPart;
}

namespace TechDrawGui
{

struct TechDrawGuiExport DrawingImageHatchStyle
{
    double scale;
    double rotationDegrees;
    Base::Vector3d offsetMm;
    Base::Color color;
};

struct TechDrawGuiExport DrawingGeometricHatchStyle
{
    double scale;
    double rotationDegrees;
    Base::Vector3d offsetMm;
    double lineWidthMm;
    Base::Color color;
};

struct TechDrawGuiExport DrawingImageHatchPlan
{
    TechDraw::DrawViewPart* view;
    TechDraw::DrawPage* page;
    std::vector<std::string> faces;
    std::string patternFile;
    std::string patternFileName;
    std::string patternKind;
    DrawingImageHatchStyle style;
};

struct TechDrawGuiExport DrawingGeometricHatchPlan
{
    TechDraw::DrawViewPart* view;
    TechDraw::DrawPage* page;
    std::vector<std::string> faces;
    std::string patternFile;
    std::string patternFileName;
    std::string patternName;
    DrawingGeometricHatchStyle style;
};

struct TechDrawGuiExport DrawingHatchDefaults
{
    std::string imagePatternFile;
    std::string imagePatternFileName;
    Base::Color imageColor;
    std::string geometricPatternFile;
    std::string geometricPatternFileName;
    std::string geometricPatternName;
    std::vector<std::string> geometricPatternNames;
    Base::Color geometricColor;
    double geometricLineWidthMm;
};

TechDrawGuiExport DrawingHatchDefaults drawingHatchDefaults();

TechDrawGuiExport DrawingImageHatchPlan validateDrawingImageHatch(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces,
    const std::string& patternFile,
    const DrawingImageHatchStyle& style);

TechDrawGuiExport TechDraw::DrawHatch* createDrawingImageHatch(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces,
    const std::string& patternFile,
    const DrawingImageHatchStyle& style);

TechDrawGuiExport DrawingGeometricHatchPlan validateDrawingGeometricHatch(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces,
    const std::string& patternFile,
    const std::string& patternName,
    const DrawingGeometricHatchStyle& style);

TechDrawGuiExport TechDraw::DrawGeomHatch* createDrawingGeometricHatch(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces,
    const std::string& patternFile,
    const std::string& patternName,
    const DrawingGeometricHatchStyle& style);

}  // namespace TechDrawGui
