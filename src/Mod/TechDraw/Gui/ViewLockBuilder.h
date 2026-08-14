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

#include <vector>

#include <Mod/TechDraw/TechDrawGlobal.h>


namespace TechDraw
{
class DrawPage;
class DrawViewPart;
}

namespace TechDrawGui
{

struct TechDrawGuiExport DrawingViewLockRequest
{
    TechDraw::DrawViewPart* view;
    bool locked;
};

struct TechDrawGuiExport DrawingViewLockState
{
    TechDraw::DrawViewPart* view;
    bool locked;
};

TechDrawGuiExport std::vector<DrawingViewLockState> changeDrawingViewLocks(
    TechDraw::DrawPage* page,
    const std::vector<DrawingViewLockRequest>& requests);

}  // namespace TechDrawGui
