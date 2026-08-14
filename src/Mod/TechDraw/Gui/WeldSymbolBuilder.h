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
class DrawLeaderLine;
class DrawPage;
class DrawWeldSymbol;
}

namespace TechDrawGui
{

struct TechDrawGuiExport DrawingWeldCatalogItem
{
    std::string key;
    std::string relativePath;
    std::string svgSha256;
};

struct TechDrawGuiExport DrawingWeldTileSpec
{
    std::string leftText;
    std::string centerText;
    std::string rightText;
    // Human callers may use a readable SVG path. Native callers resolve one
    // bounded catalog key before entering this shared builder.
    std::string symbolFile;
};

struct TechDrawGuiExport DrawingWeldSymbolSpec
{
    bool allAround;
    bool fieldWeld;
    bool alternatingWeld;
    std::string tailText;
    DrawingWeldTileSpec arrowSide;
    DrawingWeldTileSpec otherSide;
    std::string preferredLabel;
};

struct TechDrawGuiExport DrawingWeldSymbolPlan
{
    TechDraw::DrawPage* page;
    TechDraw::DrawLeaderLine* leader;
    TechDraw::DrawWeldSymbol* existingSymbol;
    std::string objectName;
    std::string label;
    DrawingWeldSymbolSpec spec;
};

TechDrawGuiExport std::vector<DrawingWeldCatalogItem> drawingWeldSymbolCatalog();
TechDrawGuiExport std::string drawingWeldSymbolCatalogHash();
TechDrawGuiExport std::string drawingWeldSymbolFileForCatalogKey(const std::string& key);

TechDrawGuiExport DrawingWeldSymbolPlan validateDrawingWeldSymbolCreation(
    TechDraw::DrawLeaderLine* leader,
    const DrawingWeldSymbolSpec& spec);

TechDrawGuiExport DrawingWeldSymbolPlan validateDrawingWeldSymbolChange(
    TechDraw::DrawWeldSymbol* symbol,
    const DrawingWeldSymbolSpec& spec);

TechDrawGuiExport TechDraw::DrawWeldSymbol* createDrawingWeldSymbol(
    TechDraw::DrawLeaderLine* leader,
    const DrawingWeldSymbolSpec& spec,
    DrawingWeldSymbolPlan* appliedPlan = nullptr);

TechDrawGuiExport TechDraw::DrawWeldSymbol* changeDrawingWeldSymbol(
    TechDraw::DrawWeldSymbol* symbol,
    const DrawingWeldSymbolSpec& spec,
    DrawingWeldSymbolPlan* appliedPlan = nullptr);

}  // namespace TechDrawGui
