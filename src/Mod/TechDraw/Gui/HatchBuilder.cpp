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

#include "HatchBuilder.h"

#include <algorithm>
#include <cmath>
#include <regex>
#include <set>

#include <App/Document.h>
#include <Base/Exception.h>
#include <Base/FileInfo.h>
#include <Base/Interpreter.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/ViewProvider.h>
#include <Mod/TechDraw/App/DrawGeomHatch.h>
#include <Mod/TechDraw/App/DrawHatch.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/HatchLine.h>
#include <Mod/TechDraw/App/LineGroup.h>

#include "ViewProviderGeomHatch.h"
#include "ViewProviderHatch.h"


namespace
{

constexpr std::size_t MaximumFaces = 64;
constexpr std::size_t MaximumPatternNames = 256;
constexpr double MaximumScale = 1000.0;
constexpr double MaximumRotationDegrees = 360.0;
constexpr double MaximumOffsetMm = 1'000'000.0;
constexpr double MaximumLineWidthMm = 100.0;
const std::regex FaceNamePattern("^Face(?:0|[1-9][0-9]*)$");

void requireLiveView(TechDraw::DrawViewPart* view)
{
    auto* page = view ? view->findParentPage() : nullptr;
    if (!view || !view->getDocument() || !page
        || page->getDocument() != view->getDocument()) {
        throw Base::ValueError(
            "A Drawing hatch requires a live projected view on a page");
    }
}

std::vector<std::string> exactFaces(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces)
{
    if (faces.empty() || faces.size() > MaximumFaces) {
        throw Base::ValueError(
            "A Drawing hatch requires 1 to 64 exact projected faces");
    }
    const std::set<std::string> unique(faces.begin(), faces.end());
    if (unique.size() != faces.size()) {
        throw Base::ValueError("A Drawing hatch cannot repeat a projected face");
    }
    for (const auto& face : faces) {
        if (!std::regex_match(face, FaceNamePattern) || !view->getFace(face)) {
            throw Base::ValueError(
                "A Drawing hatch face must be an available exact FaceN");
        }
    }
    return faces;
}

void requireFiniteStyle(
    double scale,
    double rotationDegrees,
    const Base::Vector3d& offsetMm,
    const Base::Color& color)
{
    if (!std::isfinite(scale) || scale <= 0.0 || scale > MaximumScale) {
        throw Base::ValueError("Drawing hatch scale must be greater than 0 and at most 1000");
    }
    if (!std::isfinite(rotationDegrees)
        || std::abs(rotationDegrees) > MaximumRotationDegrees) {
        throw Base::ValueError("Drawing hatch rotation must be from -360 through 360 degrees");
    }
    if (!std::isfinite(offsetMm.x) || !std::isfinite(offsetMm.y)
        || !std::isfinite(offsetMm.z)
        || std::abs(offsetMm.x) > MaximumOffsetMm
        || std::abs(offsetMm.y) > MaximumOffsetMm
        || std::abs(offsetMm.z) > Base::Vector3d::epsilon()) {
        throw Base::ValueError(
            "Drawing hatch offset must be a finite two-dimensional point");
    }
    if (!std::isfinite(color.r) || !std::isfinite(color.g)
        || !std::isfinite(color.b)) {
        throw Base::ValueError("Drawing hatch color must be finite");
    }
}

std::string imagePatternKind(const std::string& patternFile)
{
    Base::FileInfo file(patternFile);
    if (!file.isReadable()) {
        throw Base::ValueError("The Drawing image hatch pattern is not readable");
    }
    if (file.hasExtension("svg")) {
        return "svg";
    }
    if (file.hasExtension({"bmp", "png", "jpg", "jpeg"})) {
        return "bitmap";
    }
    throw Base::ValueError(
        "A Drawing image hatch pattern must be SVG, PNG, BMP, JPG, or JPEG");
}

std::vector<std::string> geometricPatternNames(const std::string& patternFile)
{
    Base::FileInfo file(patternFile);
    if (!file.isReadable() || !file.hasExtension("pat")) {
        throw Base::ValueError(
            "The Drawing geometric hatch pattern must be a readable PAT file");
    }
    std::string mutableFile(patternFile);
    auto names = TechDraw::PATLineSpec::getPatternList(mutableFile);
    if (names.empty() || names.size() > MaximumPatternNames) {
        throw Base::ValueError(
            "The Drawing geometric hatch PAT file has no bounded pattern catalog");
    }
    const std::set<std::string> unique(names.begin(), names.end());
    if (unique.size() != names.size()
        || std::ranges::any_of(names, [](const std::string& name) {
               return name.empty() || name.size() > 128;
           })) {
        throw Base::ValueError(
            "The Drawing geometric hatch PAT catalog is malformed");
    }
    return names;
}

void requireUnhatchedImageFaces(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces)
{
    const auto hatches = view->getActiveHatches();
    for (const auto& face : faces) {
        const int index = TechDraw::DrawUtil::getIndexFromName(face);
        if (TechDraw::DrawHatch::faceIsHatched(index, hatches)) {
            throw Base::ValueError(
                "A selected face already has an image hatch; replace or remove it first");
        }
    }
}

template<typename HatchType>
HatchType* createHatchObject(
    App::Document* document,
    const char* typeName,
    const char* baseName)
{
    const std::string featureName = document->getUniqueObjectName(baseName);
    const std::string documentName =
        Base::InterpreterSingleton::strToPython(document->getName());
    const QString factory =
        QStringLiteral("App.getDocument('%1').addObject('%2', '%3')")
            .arg(
                QString::fromStdString(documentName),
                QString::fromLatin1(typeName),
                QString::fromStdString(featureName));
    auto* hatch = dynamic_cast<HatchType*>(
        Gui::Command::runDocumentObjectCommand(
            Gui::Command::Doc,
            *document,
            factory.toUtf8(),
            HatchType::getClassTypeId()));
    if (!hatch) {
        throw Base::RuntimeError("The Drawing hatch factory returned an incompatible object");
    }
    return hatch;
}

TechDrawGui::ViewProviderHatch* imageProvider(TechDraw::DrawHatch* hatch)
{
    auto* provider = Gui::Application::Instance
        ? Gui::Application::Instance->getViewProvider(hatch)
        : nullptr;
    auto* result = dynamic_cast<TechDrawGui::ViewProviderHatch*>(provider);
    if (!result) {
        throw Base::RuntimeError("The image hatch has no compatible view provider");
    }
    return result;
}

TechDrawGui::ViewProviderGeomHatch* geometricProvider(
    TechDraw::DrawGeomHatch* hatch)
{
    auto* provider = Gui::Application::Instance
        ? Gui::Application::Instance->getViewProvider(hatch)
        : nullptr;
    auto* result = dynamic_cast<TechDrawGui::ViewProviderGeomHatch*>(provider);
    if (!result) {
        throw Base::RuntimeError(
            "The geometric hatch has no compatible view provider");
    }
    return result;
}

}  // namespace

TechDrawGui::DrawingHatchDefaults TechDrawGui::drawingHatchDefaults()
{
    const std::string imageFile = TechDraw::DrawHatch::prefSvgHatch();
    const std::string geometricFile = TechDraw::DrawGeomHatch::prefGeomHatchFile();
    const auto names = geometricPatternNames(geometricFile);
    const std::string preferredName = TechDraw::DrawGeomHatch::prefGeomHatchName();
    if (std::ranges::find(names, preferredName) == names.end()) {
        throw Base::ValueError(
            "The preferred geometric hatch pattern is missing from its PAT file");
    }
    imagePatternKind(imageFile);
    return {
        imageFile,
        Base::FileInfo(imageFile).fileName(),
        TechDraw::DrawHatch::prefSvgHatchColor(),
        geometricFile,
        Base::FileInfo(geometricFile).fileName(),
        preferredName,
        names,
        TechDraw::DrawGeomHatch::prefGeomHatchColor(),
        TechDraw::LineGroup::getDefaultWidth("Graphic")};
}

TechDrawGui::DrawingImageHatchPlan TechDrawGui::validateDrawingImageHatch(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces,
    const std::string& patternFile,
    const DrawingImageHatchStyle& style)
{
    requireLiveView(view);
    const auto exact = exactFaces(view, faces);
    requireFiniteStyle(
        style.scale,
        style.rotationDegrees,
        style.offsetMm,
        style.color);
    requireUnhatchedImageFaces(view, exact);
    return {
        view,
        view->findParentPage(),
        exact,
        patternFile,
        Base::FileInfo(patternFile).fileName(),
        imagePatternKind(patternFile),
        style};
}

TechDraw::DrawHatch* TechDrawGui::createDrawingImageHatch(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces,
    const std::string& patternFile,
    const DrawingImageHatchStyle& style)
{
    const auto plan = validateDrawingImageHatch(view, faces, patternFile, style);
    auto* hatch = createHatchObject<TechDraw::DrawHatch>(
        view->getDocument(),
        "TechDraw::DrawHatch",
        "Hatch");
    hatch->translateLabel("DrawHatch", "Hatch", hatch->getNameInDocument());
    hatch->Source.setValue(view, plan.faces);
    hatch->HatchPattern.setValue(plan.patternFile);
    auto* provider = imageProvider(hatch);
    provider->HatchScale.setValue(plan.style.scale);
    provider->HatchRotation.setValue(plan.style.rotationDegrees);
    provider->HatchOffset.setValue(plan.style.offsetMm);
    provider->HatchColor.setValue(plan.style.color);
    hatch->recomputeFeature();
    if (hatch->isError()) {
        throw Base::RuntimeError("The image hatch could not be generated");
    }
    view->requestPaint();
    return hatch;
}

TechDrawGui::DrawingGeometricHatchPlan
TechDrawGui::validateDrawingGeometricHatch(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces,
    const std::string& patternFile,
    const std::string& patternName,
    const DrawingGeometricHatchStyle& style)
{
    requireLiveView(view);
    const auto exact = exactFaces(view, faces);
    requireFiniteStyle(
        style.scale,
        style.rotationDegrees,
        style.offsetMm,
        style.color);
    if (!std::isfinite(style.lineWidthMm) || style.lineWidthMm < 0.0
        || style.lineWidthMm > MaximumLineWidthMm) {
        throw Base::ValueError(
            "Geometric hatch line width must be from 0 through 100 mm");
    }
    const auto names = geometricPatternNames(patternFile);
    if (std::ranges::find(names, patternName) == names.end()) {
        throw Base::ValueError(
            "The requested geometric hatch pattern is not in the PAT catalog");
    }
    return {
        view,
        view->findParentPage(),
        exact,
        patternFile,
        Base::FileInfo(patternFile).fileName(),
        patternName,
        style};
}

TechDraw::DrawGeomHatch* TechDrawGui::createDrawingGeometricHatch(
    TechDraw::DrawViewPart* view,
    const std::vector<std::string>& faces,
    const std::string& patternFile,
    const std::string& patternName,
    const DrawingGeometricHatchStyle& style)
{
    const auto plan = validateDrawingGeometricHatch(
        view,
        faces,
        patternFile,
        patternName,
        style);
    auto* hatch = createHatchObject<TechDraw::DrawGeomHatch>(
        view->getDocument(),
        "TechDraw::DrawGeomHatch",
        "GeomHatch");
    hatch->translateLabel(
        "DrawGeomHatch",
        "GeomHatch",
        hatch->getNameInDocument());
    hatch->FilePattern.setValue(plan.patternFile);
    hatch->NamePattern.setValue(plan.patternName);
    hatch->ScalePattern.setValue(plan.style.scale);
    hatch->PatternRotation.setValue(plan.style.rotationDegrees);
    hatch->PatternOffset.setValue(plan.style.offsetMm);
    hatch->Source.setValue(view, plan.faces);
    auto* provider = geometricProvider(hatch);
    provider->WeightPattern.setValue(plan.style.lineWidthMm);
    provider->ColorPattern.setValue(plan.style.color);
    hatch->recomputeFeature();
    if (hatch->isError()) {
        throw Base::RuntimeError("The geometric hatch could not be generated");
    }
    view->requestPaint();
    return hatch;
}
