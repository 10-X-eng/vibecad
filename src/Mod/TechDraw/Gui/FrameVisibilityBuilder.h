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

#include <cstddef>
#include <string>

#include <Mod/TechDraw/TechDrawGlobal.h>

namespace TechDraw
{
class DrawViewPart;
}

namespace TechDrawGui
{

class ViewProviderPage;

struct TechDrawGuiExport DrawingFrameVisibilityPlan
{
    ViewProviderPage* pageProvider;
    std::string pageName;
    bool previousVisible;
    bool visible;
    bool changed;
    std::size_t graphicalViewCount;
};

TechDrawGuiExport DrawingFrameVisibilityPlan
inspectDrawingFrameVisibility(ViewProviderPage* pageProvider);

TechDrawGuiExport DrawingFrameVisibilityPlan
validateDrawingFrameVisibility(ViewProviderPage* pageProvider, bool visible);

TechDrawGuiExport DrawingFrameVisibilityPlan
changeDrawingFrameVisibility(ViewProviderPage* pageProvider, bool visible);

struct TechDrawGuiExport DrawingGridVisibilityPlan
{
    ViewProviderPage* pageProvider;
    std::string pageName;
    bool previousVisible;
    bool visible;
    bool changed;
};

TechDrawGuiExport DrawingGridVisibilityPlan
inspectDrawingGridVisibility(ViewProviderPage* pageProvider);

TechDrawGuiExport DrawingGridVisibilityPlan
validateDrawingGridVisibility(ViewProviderPage* pageProvider, bool visible);

TechDrawGuiExport DrawingGridVisibilityPlan
changeDrawingGridVisibility(ViewProviderPage* pageProvider, bool visible);

struct TechDrawGuiExport DrawingHiddenEdgeVisibilityPlan
{
    std::string pageName;
    std::string viewName;
    bool previousVisible;
    bool visible;
    bool changed;
};

TechDrawGuiExport DrawingHiddenEdgeVisibilityPlan
inspectDrawingHiddenEdgeVisibility(TechDraw::DrawViewPart* view);

TechDrawGuiExport DrawingHiddenEdgeVisibilityPlan
validateDrawingHiddenEdgeVisibility(TechDraw::DrawViewPart* view, bool visible);

TechDrawGuiExport DrawingHiddenEdgeVisibilityPlan
changeDrawingHiddenEdgeVisibility(TechDraw::DrawViewPart* view, bool visible);

}  // namespace TechDrawGui
