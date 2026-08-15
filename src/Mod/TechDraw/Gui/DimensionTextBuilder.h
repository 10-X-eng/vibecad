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

#include <Mod/TechDraw/TechDrawGlobal.h>


namespace TechDraw
{
class DrawViewDimension;
}

namespace TechDrawGui
{

enum class DrawingDimensionTextOperation
{
    InsertDiameter,
    InsertSquare,
    InsertRepetition,
    RemovePrefix,
    IncreaseDecimals,
    DecreaseDecimals,
};

struct TechDrawGuiExport DrawingDimensionTextPlan
{
    TechDraw::DrawViewDimension* dimension;
    DrawingDimensionTextOperation operation;
    std::string objectName;
    std::string formatSpecBefore;
    std::string formatSpecAfter;
    std::string insertedPrefix;
    int decimalPlacesBefore;
    int decimalPlacesAfter;
    bool changed;
    std::string inapplicableReason;
};

TechDrawGuiExport std::vector<DrawingDimensionTextPlan>
validateDrawingDimensionText(
    const std::vector<TechDraw::DrawViewDimension*>& dimensions,
    DrawingDimensionTextOperation operation,
    const std::string& repetitionText = {});

TechDrawGuiExport std::vector<DrawingDimensionTextPlan>
changeDrawingDimensionText(
    const std::vector<TechDraw::DrawViewDimension*>& dimensions,
    DrawingDimensionTextOperation operation,
    const std::string& repetitionText = {});

}  // namespace TechDrawGui
