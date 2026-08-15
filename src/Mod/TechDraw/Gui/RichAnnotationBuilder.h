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

#include <Base/Color.h>
#include <Mod/TechDraw/TechDrawGlobal.h>


namespace TechDraw
{
class DrawPage;
class DrawRichAnno;
class DrawView;
}

namespace TechDrawGui
{

enum class DrawingRichAnnotationContentKind
{
    PlainText,
    SafeHtml,
    HumanEditorHtml,
};

struct TechDrawGuiExport DrawingRichAnnotationContent
{
    std::string inputKind;
    std::string canonicalHtml;
    std::string storedHtmlSha256;
    std::string plainTextSha256;
    std::string plainTextPreview;
    std::size_t plainTextCharacters;
    std::size_t blockCount;
    std::size_t fragmentCount;
    std::size_t linkCount;
    bool hasRichFormatting;
};

struct TechDrawGuiExport DrawingRichAnnotationFrameStyle
{
    bool visible;
    double lineWidthMm;
    int lineStyle;
    Base::Color lineColor;
};

struct TechDrawGuiExport DrawingRichAnnotationDefaults
{
    double maximumWidthMm;
    DrawingRichAnnotationFrameStyle frame;
};

struct TechDrawGuiExport DrawingRichAnnotationPlan
{
    TechDraw::DrawPage* page;
    TechDraw::DrawView* owner;
    std::string objectName;
    std::string label;
    DrawingRichAnnotationContent content;
    double xMm;
    double yMm;
    double maximumWidthMm;
    DrawingRichAnnotationFrameStyle frame;
};

TechDrawGuiExport DrawingRichAnnotationDefaults drawingRichAnnotationDefaults();

TechDrawGuiExport DrawingRichAnnotationContent inspectDrawingRichAnnotationContent(
    const std::string& storedHtml);

TechDrawGuiExport DrawingRichAnnotationPlan validateDrawingRichAnnotation(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    DrawingRichAnnotationContentKind contentKind,
    const std::string& content,
    const std::string& preferredLabel,
    double xMm,
    double yMm,
    double maximumWidthMm,
    const DrawingRichAnnotationFrameStyle& frame);

TechDrawGuiExport TechDraw::DrawRichAnno* createDrawingRichAnnotation(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    DrawingRichAnnotationContentKind contentKind,
    const std::string& content,
    const std::string& preferredLabel,
    double xMm,
    double yMm,
    double maximumWidthMm,
    const DrawingRichAnnotationFrameStyle& frame,
    DrawingRichAnnotationPlan* appliedPlan = nullptr);

}  // namespace TechDrawGui
