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

#include <Mod/TechDraw/App/LineFormat.h>
#include <Mod/TechDraw/TechDrawGlobal.h>


namespace TechDraw
{
class DrawViewPart;
}

namespace TechDrawGui
{

enum class DrawingLineKind
{
    ProjectedEdge,
    CosmeticEdge,
    CenterLine,
};

struct TechDrawGuiExport DrawingLineTarget
{
    DrawingLineKind kind;
    std::string tag;
};

struct TechDrawGuiExport DrawingLineAttributeState
{
    DrawingLineTarget target;
    std::string selectionName;
    TechDraw::LineFormat format;
};

TechDrawGuiExport std::vector<DrawingLineTarget> drawingLineTargetsFromSelection(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& subelementNames);

TechDrawGuiExport TechDraw::LineFormat* drawingLineFormatFromSelection(
    TechDraw::DrawViewPart* view,
    const std::string& subelementName,
    bool create,
    std::string* createdFormatTag = nullptr);

TechDrawGuiExport std::vector<DrawingLineAttributeState> drawingLineAttributeStates(
    TechDraw::DrawViewPart* view);

TechDrawGuiExport std::vector<DrawingLineAttributeState> changeDrawingLineAttributes(
    TechDraw::DrawViewPart* view,
    const std::vector<DrawingLineTarget>& targets,
    const TechDraw::LineFormat& format);

}  // namespace TechDrawGui
