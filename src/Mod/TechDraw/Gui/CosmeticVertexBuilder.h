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
class DrawViewPart;
}

namespace TechDrawGui
{

struct TechDrawGuiExport DrawingCosmeticVertexFormat
{
    Base::Color color;
    double size;
    int style;
    bool visible;
};

struct TechDrawGuiExport DrawingCosmeticVertexPointPlan
{
    Base::Vector3d pointInViewMm;
    DrawingCosmeticVertexFormat format;
};

struct TechDrawGuiExport DrawingCosmeticVertexPointResult
{
    DrawingCosmeticVertexPointPlan plan;
    std::string vertexTag;
};

struct TechDrawGuiExport DrawingVertexIntersectionPlan
{
    std::vector<std::string> sourceSelectionNames;
    std::vector<DrawingCosmeticVertexPointPlan> vertices;
};

struct TechDrawGuiExport DrawingVertexIntersectionResult
{
    DrawingVertexIntersectionPlan plan;
    std::vector<std::string> vertexTags;
};

struct TechDrawGuiExport DrawingMidpointVertexPlan
{
    std::string sourceSelectionName;
    DrawingCosmeticVertexPointPlan vertex;
};

struct TechDrawGuiExport DrawingMidpointVerticesPlan
{
    std::vector<DrawingMidpointVertexPlan> midpoints;
};

struct TechDrawGuiExport DrawingMidpointVerticesResult
{
    DrawingMidpointVerticesPlan plan;
    std::vector<std::string> vertexTags;
};

struct TechDrawGuiExport DrawingQuadrantVertexPlan
{
    std::string sourceSelectionName;
    std::vector<DrawingCosmeticVertexPointPlan> vertices;
};

struct TechDrawGuiExport DrawingQuadrantVerticesPlan
{
    std::vector<DrawingQuadrantVertexPlan> sources;
};

struct TechDrawGuiExport DrawingQuadrantVerticesResult
{
    DrawingQuadrantVerticesPlan plan;
    std::vector<std::string> vertexTags;
};

struct TechDrawGuiExport DrawingOffsetVertexPlan
{
    std::string sourceSelectionName;
    Base::Vector3d sourcePointInViewMm;
    Base::Vector3d offsetInViewMm;
    DrawingCosmeticVertexPointPlan vertex;
};

struct TechDrawGuiExport DrawingOffsetVertexResult
{
    DrawingOffsetVertexPlan plan;
    std::string vertexTag;
};

struct TechDrawGuiExport DrawingPersistentCosmeticVertexState
{
    std::string tag;
    std::string selectionName;
    Base::Vector3d pointInViewMm;
    DrawingCosmeticVertexFormat format;
};

TechDrawGuiExport DrawingCosmeticVertexPointPlan
validateDrawingCosmeticVertexPoint(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& pointInViewMm);

TechDrawGuiExport DrawingCosmeticVertexPointResult
createDrawingCosmeticVertexPoint(
    TechDraw::DrawViewPart* view,
    const Base::Vector3d& pointInViewMm);

TechDrawGuiExport DrawingVertexIntersectionPlan validateDrawingVertexIntersections(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingVertexIntersectionResult createDrawingVertexIntersections(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingMidpointVerticesPlan validateDrawingMidpointVertices(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingMidpointVerticesResult createDrawingMidpointVertices(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingQuadrantVerticesPlan validateDrawingQuadrantVertices(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingQuadrantVerticesResult createDrawingQuadrantVertices(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& sourceSelectionNames);

TechDrawGuiExport DrawingOffsetVertexPlan validateDrawingOffsetVertex(
    TechDraw::DrawViewPart* view,
    const std::string& sourceSelectionName,
    const Base::Vector3d& offsetInViewMm);

TechDrawGuiExport DrawingOffsetVertexResult createDrawingOffsetVertex(
    TechDraw::DrawViewPart* view,
    const std::string& sourceSelectionName,
    const Base::Vector3d& offsetInViewMm);

TechDrawGuiExport DrawingPersistentCosmeticVertexState
drawingPersistentCosmeticVertexState(
    TechDraw::DrawViewPart* view,
    const std::string& tag);

TechDrawGuiExport std::vector<DrawingPersistentCosmeticVertexState>
drawingCosmeticVertexStates(TechDraw::DrawViewPart* view);

}  // namespace TechDrawGui
