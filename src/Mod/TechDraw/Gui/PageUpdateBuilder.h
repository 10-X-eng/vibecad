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
}

namespace TechDrawGui
{

struct TechDrawGuiExport DrawingKeepUpdatedPlan
{
    TechDraw::DrawPage* page;
    std::string pageName;
    bool previousKeepUpdated;
    bool keepUpdated;
    bool changed;
};

TechDrawGuiExport DrawingKeepUpdatedPlan inspectDrawingKeepUpdated(
    TechDraw::DrawPage* page);
TechDrawGuiExport DrawingKeepUpdatedPlan validateDrawingKeepUpdated(
    TechDraw::DrawPage* page,
    bool keepUpdated);
TechDrawGuiExport DrawingKeepUpdatedPlan changeDrawingKeepUpdated(
    TechDraw::DrawPage* page,
    bool keepUpdated);

}  // namespace TechDrawGui
