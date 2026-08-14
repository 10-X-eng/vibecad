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

#include <Base/Vector3D.h>
#include <Mod/TechDraw/TechDrawGlobal.h>

#include "LineAttributeBuilder.h"


namespace TechDrawGui
{

enum class DrawingLineLengthOperation
{
    Extend,
    Shorten,
};

struct TechDrawGuiExport DrawingLineLengthState
{
    DrawingLineTarget target;
    std::string selectionName;
    Base::Vector3d startInViewMm;
    Base::Vector3d endInViewMm;
    double lengthMm;
    bool hasCenterLineExtension;
    double centerLineExtensionMm;
};

TechDrawGuiExport std::vector<DrawingLineLengthState> drawingLineLengthStates(
    TechDraw::DrawViewPart* view);

TechDrawGuiExport DrawingLineLengthState changeDrawingLineLength(
    TechDraw::DrawViewPart* view,
    const DrawingLineTarget& target,
    DrawingLineLengthOperation operation,
    double deltaDistanceMm);

}  // namespace TechDrawGui
