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

#include "DimensionBuilder.h"

#include <array>
#include <cmath>
#include <optional>
#include <regex>
#include <unordered_set>
#include <utility>

#include <BRepGProp.hxx>
#include <GProp_GProps.hxx>
#include <QString>

#include <App/Document.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Base/Tools.h>
#include <Gui/Command.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawDimHelper.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawViewDimension.h>
#include <Mod/TechDraw/App/DrawViewDimExtent.h>
#include <Mod/TechDraw/App/DrawViewPart.h>

#include "DimensionValidators.h"


using TechDraw::DimensionGeometry;
using TechDraw::DrawViewDimension;
using TechDraw::DrawViewPart;
using TechDraw::ReferenceVector;

namespace
{

struct DimensionPolicy
{
    std::string type;
    StringVector acceptableGeometry;
    std::vector<int> minimumCounts;
    std::vector<DimensionGeometry> acceptableConfigurations;
    std::size_t exactReferenceCount{0};
    std::size_t maximumReferenceCount{0};
};

const DimensionPolicy& policyFor(const std::string& dimensionType)
{
    static const std::array<DimensionPolicy, 8> policies = {
        DimensionPolicy{"Distance",
                        {"Edge", "Vertex"},
                        {1, 2},
                        {DimensionGeometry::isVertical,
                         DimensionGeometry::isHorizontal,
                         DimensionGeometry::isDiagonal,
                         DimensionGeometry::isHybrid},
                        0,
                        2},
        DimensionPolicy{"DistanceX",
                        {"Edge", "Vertex"},
                        {1, 2},
                        {DimensionGeometry::isHorizontal,
                         DimensionGeometry::isDiagonal,
                         DimensionGeometry::isHybrid,
                         DimensionGeometry::isMultiEdge},
                        0,
                        2},
        DimensionPolicy{"DistanceY",
                        {"Edge", "Vertex"},
                        {1, 2},
                        {DimensionGeometry::isVertical,
                         DimensionGeometry::isDiagonal,
                         DimensionGeometry::isHybrid,
                         DimensionGeometry::isMultiEdge},
                        0,
                        2},
        DimensionPolicy{"Radius",
                        {"Edge"},
                        {1},
                        {DimensionGeometry::isCircle,
                         DimensionGeometry::isEllipse,
                         DimensionGeometry::isBSplineCircle,
                         DimensionGeometry::isBSpline},
                        1,
                        1},
        DimensionPolicy{"Diameter",
                        {"Edge"},
                        {1},
                        {DimensionGeometry::isCircle,
                         DimensionGeometry::isEllipse,
                         DimensionGeometry::isBSplineCircle,
                         DimensionGeometry::isBSpline},
                        1,
                        1},
        DimensionPolicy{"Angle",
                        {"Edge"},
                        {2},
                        {DimensionGeometry::isAngle},
                        2,
                        2},
        DimensionPolicy{"Angle3Pt",
                        {"Vertex"},
                        {3},
                        {DimensionGeometry::isAngle3Pt},
                        3,
                        3},
        DimensionPolicy{"Area",
                        {"Face"},
                        {1},
                        {DimensionGeometry::isFace},
                        1,
                        1},
    };
    for (const auto& policy : policies) {
        if (policy.type == dimensionType) {
            return policy;
        }
    }
    throw Base::ValueError("Unsupported projected dimension type");
}

std::string geometryName(DimensionGeometry geometry)
{
    switch (geometry) {
        case DimensionGeometry::isHorizontal:
            return "horizontal";
        case DimensionGeometry::isVertical:
            return "vertical";
        case DimensionGeometry::isDiagonal:
            return "diagonal";
        case DimensionGeometry::isCircle:
            return "circle";
        case DimensionGeometry::isEllipse:
            return "ellipse";
        case DimensionGeometry::isBSplineCircle:
            return "b_spline_circle";
        case DimensionGeometry::isBSpline:
            return "b_spline";
        case DimensionGeometry::isAngle:
            return "angle";
        case DimensionGeometry::isAngle3Pt:
            return "three_point_angle";
        case DimensionGeometry::isMultiEdge:
            return "multiple_edges";
        case DimensionGeometry::isHybrid:
            return "edge_vertex";
        case DimensionGeometry::isFace:
            return "face";
        case DimensionGeometry::isZLimited:
            return "z_limited";
        default:
            return "invalid";
    }
}

DrawViewDimension* createProjectedArcLength(
    DrawViewPart* view,
    const std::string& edgeName,
    const std::optional<Base::Vector3d>& labelPosition)
{
    const auto validation = TechDrawGui::validateProjectedArcLength(view, edgeName);
    TechDraw::BaseGeomPtr edge = view->getEdge(edgeName);
    if (!edge) {
        throw Base::RuntimeError("The projected circular arc is unavailable");
    }

    const ReferenceVector references = {
        TechDraw::ReferenceEntry(view, edgeName),
    };
    auto* dimension = TechDrawGui::createDimensionFeature(
        view, "Distance", references, {});
    const TechDraw::pointPair points = dimension->getLinearPoints();
    const Base::Vector3d automaticPosition =
        Base::Vector3d(
            (points.first().x + points.second().x) / 2.0,
            -(points.first().y + points.second().y) / 2.0,
            0.0);
    const Base::Vector3d& position =
        labelPosition ? *labelPosition : automaticPosition;
    dimension->X.setValue(position.x);
    dimension->Y.setValue(position.y);
    dimension->ArcLengthSource.setValue(
        view, std::vector<std::string>{edgeName});
    dimension->ArcLengthValue.setValue(validation.arcLengthMm);
    dimension->Arbitrary.setValue(false);
    dimension->FormatSpec.setValue(
        "◠ " + dimension->getDefaultFormatSpec());
    dimension->recomputeFeature();
    if (dimension->isError()) {
        throw Base::RuntimeError("The projected arc-length dimension could not be generated");
    }
    return dimension;
}

void validateRepairTarget(DrawViewDimension* dimension, DrawViewPart* view)
{
    if (!dimension || !view || !dimension->getDocument()
        || view->getDocument() != dimension->getDocument()) {
        throw Base::ValueError(
            "Dimension repair requires a live dimension and drawing view in one document");
    }
    auto* dimensionPage = dimension->findParentPage();
    auto* viewPage = view->findParentPage();
    if (!dimensionPage || dimensionPage != viewPage) {
        throw Base::ValueError(
            "The replacement view must belong to the dimension's exact drawing page");
    }
    if (!dimension->MeasureType.isValue("Projected")) {
        throw Base::ValueError("Only projected dimensions can replace projected references");
    }
}

void replaceProjectedReferences(
    DrawViewDimension* dimension,
    const ReferenceVector& references)
{
    dimension->setReferences2d(references);
    dimension->setReferences3d({});
    dimension->recomputeFeature();
    if (dimension->isError()) {
        throw Base::RuntimeError(
            "The replacement references did not produce a valid dimension");
    }
}

std::string chamferFormatBase(const std::string& formatSpec)
{
    static const std::regex suffix(R"( x[0-9]+\xC2\xB0$)");
    return std::regex_replace(formatSpec, suffix, std::string());
}

}  // namespace

TechDrawGui::ProjectedDimensionValidation TechDrawGui::validateProjectedDimension(
    DrawViewPart* view,
    const std::string& dimensionType,
    const ReferenceVector& references,
    bool allowApproximate)
{
    if (!view || !view->getDocument() || !view->findParentPage()) {
        throw Base::ValueError("A projected dimension requires a drawing view on a page");
    }
    const auto& policy = policyFor(dimensionType);
    if (references.empty() || references.size() > policy.maximumReferenceCount
        || (policy.exactReferenceCount != 0
            && references.size() != policy.exactReferenceCount)) {
        throw Base::ValueError("The projected dimension has the wrong number of references");
    }
    for (const auto& reference : references) {
        if (reference.getObject() != view || reference.getDocument() != view->getDocument()
            || reference.getSubName().empty()) {
            throw Base::ValueError(
                "Every projected dimension reference must belong to the exact drawing view");
        }
    }
    std::unordered_set<std::string> uniqueReferences;
    for (const auto& reference : references) {
        if (!uniqueReferences.insert(reference.getSubName()).second) {
            throw Base::ValueError(
                "A projected dimension cannot repeat the same reference");
        }
    }
    const DimensionGeometry geometry = TechDraw::validateDimSelection(
        references,
        policy.acceptableGeometry,
        policy.minimumCounts,
        policy.acceptableConfigurations);
    if (geometry == DimensionGeometry::isInvalid
        || geometry == DimensionGeometry::isViewReference) {
        throw Base::ValueError(
            "The projected references cannot create the requested dimension type");
    }
    if ((dimensionType == "Radius" || dimensionType == "Diameter")
        && geometry == DimensionGeometry::isBSpline) {
        throw Base::ValueError(
            "A non-circular B-spline cannot create a radius or diameter dimension");
    }
    const bool approximate = geometry == DimensionGeometry::isEllipse
        || geometry == DimensionGeometry::isBSplineCircle;
    if (approximate && !allowApproximate) {
        throw Base::ValueError(
            "This radius or diameter is approximate; set allow_approximate=true to accept it");
    }
    return {geometryName(geometry), approximate};
}

DrawViewDimension* TechDrawGui::createDimensionFeature(
    DrawViewPart* view,
    const std::string& dimensionType,
    const ReferenceVector& references2d,
    const ReferenceVector& references3d)
{
    if (!view || !view->getDocument()) {
        throw Base::ValueError("A dimension requires a live drawing view");
    }
    TechDraw::DrawPage* page = view->findParentPage();
    App::Document* document = view->getDocument();
    if (!page || page->getDocument() != document) {
        throw Base::ValueError("The dimension view is not attached to a drawing page");
    }

    const std::string dimensionName = document->getUniqueObjectName("Dimension");
    const std::string documentName =
        Base::InterpreterSingleton::strToPython(document->getName());
    const QString dimensionFactory =
        QStringLiteral(
            "App.getDocument('%1').addObject"
            "('TechDraw::DrawViewDimension', '%2')")
            .arg(QString::fromStdString(documentName),
                 QString::fromStdString(dimensionName));
    auto* dimension = dynamic_cast<DrawViewDimension*>(
        Gui::Command::runDocumentObjectCommand(
            Gui::Command::Doc,
            *document,
            dimensionFactory.toUtf8(),
            DrawViewDimension::getClassTypeId()));
    if (!dimension) {
        throw Base::TypeError("The dimension object could not be created");
    }
    dimension->translateLabel(
        "DrawViewDimension", "Dimension", dimension->getNameInDocument());
    const std::string dimensionCommand = Gui::Command::getObjectCmd(dimension);
    const std::string pageCommand = Gui::Command::getObjectCmd(page);
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.Type = '%s'",
        dimensionCommand.c_str(),
        dimensionType.c_str());
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.MeasureType = 'Projected'",
        dimensionCommand.c_str());
    dimension->setReferences2d(references2d);
    dimension->setReferences3d(references3d);
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.addView(%s)",
        pageCommand.c_str(),
        dimensionCommand.c_str());
    dimension->recomputeFeature();
    if (dimension->isError()) {
        throw Base::RuntimeError("The dimension could not be generated");
    }
    return dimension;
}

DrawViewDimension* TechDrawGui::createProjectedDimensionFeature(
    DrawViewPart* view,
    const std::string& dimensionType,
    const ReferenceVector& references,
    bool allowApproximate,
    const Base::Vector3d& labelPosition)
{
    validateProjectedDimension(
        view, dimensionType, references, allowApproximate);
    DrawViewDimension* dimension = createDimensionFeature(
        view, dimensionType, references, {});
    dimension->X.setValue(labelPosition.x);
    dimension->Y.setValue(labelPosition.y);
    dimension->recomputeFeature();
    if (dimension->isError()) {
        throw Base::RuntimeError("The positioned dimension could not be generated");
    }
    return dimension;
}

TechDrawGui::ProjectedDimensionValidation TechDrawGui::validateProjectedExtent(
    DrawViewPart* view,
    const std::string& dimensionType,
    const ReferenceVector& references)
{
    if (!view || !view->getDocument() || !view->findParentPage()) {
        throw Base::ValueError("A projected extent requires a drawing view on a page");
    }
    if (dimensionType != "DistanceX" && dimensionType != "DistanceY") {
        throw Base::ValueError("A projected extent type must be DistanceX or DistanceY");
    }
    if (references.size() > 64) {
        throw Base::ValueError("A projected extent accepts at most 64 exact edges");
    }
    if (references.empty()) {
        return {"whole_view", false};
    }

    std::unordered_set<std::string> uniqueReferences;
    for (const auto& reference : references) {
        if (reference.getObject() != view || reference.getDocument() != view->getDocument()
            || TechDraw::DrawUtil::getGeomTypeFromName(reference.getSubName()) != "Edge") {
            throw Base::ValueError(
                "Every projected extent reference must be an exact edge from the drawing view");
        }
        if (!uniqueReferences.insert(reference.getSubName()).second) {
            throw Base::ValueError("A projected extent cannot repeat the same edge");
        }
    }

    const DimensionGeometry geometry = TechDraw::validateDimSelection(
        references,
        {"Edge"},
        {1},
        {DimensionGeometry::isMultiEdge,
         DimensionGeometry::isHorizontal,
         DimensionGeometry::isVertical,
         DimensionGeometry::isDiagonal,
         DimensionGeometry::isCircle,
         DimensionGeometry::isEllipse,
         DimensionGeometry::isBSplineCircle,
         DimensionGeometry::isBSpline,
         DimensionGeometry::isZLimited});
    if (geometry == DimensionGeometry::isInvalid
        || geometry == DimensionGeometry::isViewReference) {
        throw Base::ValueError(
            "The projected edges cannot create the requested extent dimension");
    }
    return {geometryName(geometry), false};
}

DrawViewDimension* TechDrawGui::createProjectedExtentFeature(
    DrawViewPart* view,
    const std::string& dimensionType,
    const ReferenceVector& references,
    const Base::Vector3d& labelPosition)
{
    validateProjectedExtent(view, dimensionType, references);
    auto* dimension = TechDraw::DrawDimHelper::makeExtentDim(
        view, dimensionType, references);
    if (!dimension) {
        throw Base::RuntimeError("The projected extent could not be created");
    }
    dimension->X.setValue(labelPosition.x);
    dimension->Y.setValue(labelPosition.y);
    dimension->recomputeFeature();
    if (dimension->isError()) {
        throw Base::RuntimeError("The positioned projected extent could not be generated");
    }
    return dimension;
}

TechDrawGui::ProjectedDimensionValidation TechDrawGui::validateProjectedChamfer(
    DrawViewPart* view,
    const std::string& dimensionType,
    const ReferenceVector& references)
{
    if (dimensionType != "DistanceX" && dimensionType != "DistanceY") {
        throw Base::ValueError("A projected chamfer type must be DistanceX or DistanceY");
    }
    if (references.size() != 2) {
        throw Base::ValueError("A projected chamfer requires exactly two vertices");
    }
    for (const auto& reference : references) {
        if (TechDraw::DrawUtil::getGeomTypeFromName(reference.getSubName()) != "Vertex") {
            throw Base::ValueError(
                "Every projected chamfer reference must be an exact vertex");
        }
    }
    return validateProjectedDimension(
        view, dimensionType, references, false);
}

DrawViewDimension* TechDrawGui::createProjectedChamferFeature(
    DrawViewPart* view,
    const std::string& dimensionType,
    const ReferenceVector& references,
    const Base::Vector3d& labelPosition)
{
    validateProjectedChamfer(view, dimensionType, references);
    auto* dimension = createProjectedDimensionFeature(
        view, dimensionType, references, false, labelPosition);
    const TechDraw::pointPair points = dimension->getLinearPoints();
    const double dx = points.first().x - points.second().x;
    const double dy = points.first().y - points.second().y;
    const int angle = static_cast<int>(std::round(Base::toDegrees(std::abs(
        dimensionType == "DistanceY" ? std::atan2(dx, dy) : std::atan2(dy, dx)))));
    std::string formatSpec = dimension->FormatSpec.getStrValue();
    formatSpec += " x" + std::to_string(angle) + "°";
    dimension->FormatSpec.setValue(formatSpec);
    dimension->recomputeFeature();
    if (dimension->isError()) {
        throw Base::RuntimeError("The projected chamfer dimension could not be generated");
    }
    return dimension;
}

TechDrawGui::ProjectedArcLengthValidation TechDrawGui::validateProjectedArcLength(
    DrawViewPart* view,
    const std::string& edgeName)
{
    if (!view || !view->getDocument() || !view->findParentPage()) {
        throw Base::ValueError("A projected arc length requires a drawing view on a page");
    }
    if (edgeName.empty()
        || TechDraw::DrawUtil::getGeomTypeFromName(edgeName) != "Edge") {
        throw Base::ValueError("A projected arc length requires one exact edge");
    }
    const int geometryIndex = TechDraw::DrawUtil::getIndexFromName(edgeName);
    const TechDraw::BaseGeomPtr geometry = view->getGeomByIndex(geometryIndex);
    if (!geometry || geometry->getGeomType() != TechDraw::GeomType::ARCOFCIRCLE) {
        throw Base::ValueError("The projected edge is not an open circular arc");
    }
    TechDraw::BaseGeomPtr edge = view->getEdge(edgeName);
    if (!edge || edge->closed()) {
        throw Base::ValueError("The projected edge is not an open circular arc");
    }
    GProp_GProps edgeProperties;
    BRepGProp::LinearProperties(edge->getOCCEdge(), edgeProperties);
    const double scale = view->getScale();
    if (!std::isfinite(scale) || std::abs(scale) <= Base::Vector3d::epsilon()) {
        throw Base::ValueError("The projected circular arc has no usable scale");
    }
    const double arcLength = edgeProperties.Mass() / scale;
    if (!std::isfinite(arcLength) || arcLength <= Base::Vector3d::epsilon()) {
        throw Base::ValueError("The projected circular arc has no usable length");
    }
    return {"circular_arc", arcLength};
}

DrawViewDimension* TechDrawGui::createProjectedArcLengthFeature(
    DrawViewPart* view,
    const std::string& edgeName)
{
    return createProjectedArcLength(view, edgeName, std::nullopt);
}

DrawViewDimension* TechDrawGui::createProjectedArcLengthFeature(
    DrawViewPart* view,
    const std::string& edgeName,
    const Base::Vector3d& labelPosition)
{
    return createProjectedArcLength(view, edgeName, labelPosition);
}

DrawViewDimension* TechDrawGui::repairProjectedDimensionFeature(
    DrawViewDimension* dimension,
    DrawViewPart* view,
    const ReferenceVector& references,
    bool allowApproximate)
{
    validateRepairTarget(dimension, view);
    if (dynamic_cast<TechDraw::DrawViewDimExtent*>(dimension)) {
        throw Base::TypeError("An extent dimension requires the extent repair path");
    }
    const std::string dimensionType = dimension->Type.getValueAsString();
    validateProjectedDimension(view, dimensionType, references, allowApproximate);
    replaceProjectedReferences(dimension, references);
    return dimension;
}

DrawViewDimension* TechDrawGui::repairProjectedExtentFeature(
    DrawViewDimension* dimension,
    DrawViewPart* view,
    const ReferenceVector& references)
{
    validateRepairTarget(dimension, view);
    auto* extent = dynamic_cast<TechDraw::DrawViewDimExtent*>(dimension);
    if (!extent) {
        throw Base::TypeError("The target is not an extent dimension");
    }
    const std::string dimensionType = extent->Type.getValueAsString();
    const int expectedDirection = dimensionType == "DistanceX" ? 0
        : dimensionType == "DistanceY"                       ? 1
                                                              : -1;
    if (expectedDirection < 0 || extent->DirExtent.getValue() != expectedDirection) {
        throw Base::ValueError("The extent type and direction are inconsistent");
    }
    validateProjectedExtent(view, dimensionType, references);
    std::vector<std::string> edgeNames;
    edgeNames.reserve(references.size());
    for (const auto& reference : references) {
        edgeNames.push_back(reference.getSubName());
    }
    ReferenceVector effectiveReferences = references;
    if (effectiveReferences.empty()) {
        effectiveReferences.emplace_back(view, std::string());
    }
    extent->Source.setValue(view, edgeNames);
    extent->Source3d.setValue(nullptr, nullptr);
    replaceProjectedReferences(extent, effectiveReferences);
    return extent;
}

DrawViewDimension* TechDrawGui::repairProjectedChamferFeature(
    DrawViewDimension* dimension,
    DrawViewPart* view,
    const ReferenceVector& references)
{
    validateRepairTarget(dimension, view);
    if (dynamic_cast<TechDraw::DrawViewDimExtent*>(dimension)) {
        throw Base::TypeError("An extent dimension cannot be repaired as a chamfer");
    }
    const std::string dimensionType = dimension->Type.getValueAsString();
    validateProjectedChamfer(view, dimensionType, references);
    replaceProjectedReferences(dimension, references);
    const TechDraw::pointPair points = dimension->getLinearPoints();
    const double dx = points.first().x - points.second().x;
    const double dy = points.first().y - points.second().y;
    const int angle = static_cast<int>(std::round(Base::toDegrees(std::abs(
        dimensionType == "DistanceY" ? std::atan2(dx, dy) : std::atan2(dy, dx)))));
    const std::string base = chamferFormatBase(dimension->FormatSpec.getStrValue());
    dimension->FormatSpec.setValue(base + " x" + std::to_string(angle) + "°");
    dimension->recomputeFeature();
    if (dimension->isError()) {
        throw Base::RuntimeError("The repaired chamfer dimension is invalid");
    }
    return dimension;
}

DrawViewDimension* TechDrawGui::repairProjectedArcLengthFeature(
    DrawViewDimension* dimension,
    DrawViewPart* view,
    const std::string& edgeName)
{
    validateRepairTarget(dimension, view);
    if (dynamic_cast<TechDraw::DrawViewDimExtent*>(dimension)
        || !dimension->Type.isValue("Distance")) {
        throw Base::TypeError("The target is not an arc-length dimension");
    }
    const auto validation = validateProjectedArcLength(view, edgeName);
    const ReferenceVector references = {TechDraw::ReferenceEntry(view, edgeName)};
    replaceProjectedReferences(dimension, references);
    dimension->ArcLengthSource.setValue(view, std::vector<std::string>{edgeName});
    dimension->ArcLengthValue.setValue(validation.arcLengthMm);
    dimension->Arbitrary.setValue(false);
    dimension->FormatSpec.setValue(
        "◠ " + dimension->getDefaultFormatSpec());
    dimension->recomputeFeature();
    if (dimension->isError()) {
        throw Base::RuntimeError("The repaired arc-length dimension is invalid");
    }
    return dimension;
}

std::string TechDrawGui::defaultDimensionFormatSpec(DrawViewDimension* dimension)
{
    if (!dimension || !dimension->getDocument()) {
        throw Base::ValueError("A live dimension is required");
    }
    return dimension->getDefaultFormatSpec();
}
