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

#include "BalloonBuilder.h"

#include <BRepAdaptor_Curve.hxx>
#include <BRepGProp.hxx>
#include <GCPnts_AbscissaPoint.hxx>
#include <GProp_GProps.hxx>
#include <Precision.hxx>

#include <cmath>
#include <limits>
#include <set>
#include <vector>

#include <App/Document.h>
#include <Base/Color.h>
#include <Base/Exception.h>
#include <Base/Interpreter.h>
#include <Base/Quantity.h>
#include <Base/UnitsApi.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawUtil.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <Mod/TechDraw/App/DrawViewBalloon.h>
#include <Mod/TechDraw/App/DrawViewDimension.h>
#include <Mod/TechDraw/App/DrawViewPart.h>
#include <Mod/TechDraw/App/LineGroup.h>
#include <Mod/TechDraw/App/Preferences.h>

#include "ViewProviderBalloon.h"
#include "ViewProviderDimension.h"


using TechDraw::DrawPage;
using TechDraw::DrawView;
using TechDraw::DrawViewBalloon;
using TechDraw::DrawViewPart;

namespace
{

bool finitePoint(const Base::Vector3d& point)
{
    return std::isfinite(point.x) && std::isfinite(point.y) && std::isfinite(point.z);
}

void requireLiveSource(DrawView* sourceView)
{
    if (!sourceView || !sourceView->getDocument()) {
        throw Base::ValueError("A balloon requires a live Drawing source view");
    }
    DrawPage* page = sourceView->findParentPage();
    if (!page || page->getDocument() != sourceView->getDocument()) {
        throw Base::ValueError("The balloon source view is not attached to a page");
    }
    const double scale = sourceView->getScale();
    if (!std::isfinite(scale) || std::abs(scale) <= std::numeric_limits<double>::epsilon()) {
        throw Base::ValueError("The balloon source view has no valid scale");
    }
}

const char* measurementKindName(TechDrawGui::MeasurementAnnotationKind kind)
{
    switch (kind) {
        case TechDrawGui::MeasurementAnnotationKind::Area:
            return "Area";
        case TechDrawGui::MeasurementAnnotationKind::ArcLength:
            return "ArcLength";
    }
    throw Base::ValueError("The projected measurement kind is unsupported");
}

void requireMeasurementElements(const std::vector<std::string>& elements)
{
    constexpr std::size_t maximumElements = 64;
    if (elements.empty() || elements.size() > maximumElements) {
        throw Base::ValueError(
            "A projected measurement requires 1 to 64 exact elements");
    }
    const std::set<std::string> unique(elements.begin(), elements.end());
    if (unique.size() != elements.size()) {
        throw Base::ValueError(
            "A projected measurement cannot repeat an element");
    }
}

TechDrawGui::ViewProviderBalloon* balloonViewProvider(DrawViewBalloon* balloon)
{
    auto* provider = Gui::Application::Instance
        ? Gui::Application::Instance->getViewProvider(balloon)
        : nullptr;
    auto* balloonProvider = dynamic_cast<TechDrawGui::ViewProviderBalloon*>(provider);
    if (!balloonProvider) {
        throw Base::RuntimeError(
            "The projected measurement annotation has no display provider");
    }
    return balloonProvider;
}

}  // namespace

TechDrawGui::ProjectedBalloonAnchor TechDrawGui::validateProjectedBalloonAnchor(
    DrawViewPart* view,
    const std::string& elementName)
{
    requireLiveSource(view);
    const std::string elementType = TechDraw::DrawUtil::getGeomTypeFromName(elementName);
    const int index = TechDraw::DrawUtil::getIndexFromName(elementName);
    if (index < 0 || (elementType != "Edge" && elementType != "Vertex")) {
        throw Base::ValueError("A projected balloon anchor must be one exact EdgeN or VertexN");
    }

    Base::Vector3d rawPoint;
    if (elementType == "Edge") {
        const TechDraw::BaseGeomPtr edge = view->getGeomByIndex(index);
        if (!edge) {
            throw Base::ValueError("The projected balloon anchor edge is unavailable");
        }
        rawPoint = edge->getMidPoint();
    }
    else {
        const TechDraw::VertexPtr vertex = view->getProjVertexByIndex(index);
        if (!vertex) {
            throw Base::ValueError("The projected balloon anchor vertex is unavailable");
        }
        rawPoint = Base::Vector3d(vertex->x(), vertex->y(), 0.0);
    }

    const Base::Vector3d pointInViewMm = TechDraw::DrawUtil::invertY(rawPoint);
    const Base::Vector3d pointInSourceMm = pointInViewMm / view->getScale();
    if (!finitePoint(pointInViewMm) || !finitePoint(pointInSourceMm)) {
        throw Base::RuntimeError("The projected balloon anchor has invalid coordinates");
    }
    return {elementType, pointInViewMm, pointInSourceMm};
}

DrawViewBalloon* TechDrawGui::createBalloonFeature(
    DrawView* sourceView,
    const Base::Vector3d& anchorInSourceMm,
    const Base::Vector3d& bubbleInSourceMm,
    const std::optional<std::string>& text,
    const std::optional<std::string>& anchorElement,
    const std::optional<std::string>& label)
{
    requireLiveSource(sourceView);
    if (!finitePoint(anchorInSourceMm) || !finitePoint(bubbleInSourceMm)) {
        throw Base::ValueError("Balloon coordinates must be finite");
    }
    if (anchorElement && anchorElement->empty()) {
        throw Base::ValueError("A balloon anchor element cannot be empty");
    }

    App::Document* document = sourceView->getDocument();
    DrawPage* page = sourceView->findParentPage();
    const std::string featureName = document->getUniqueObjectName("Balloon");
    const std::string documentName = Base::InterpreterSingleton::strToPython(document->getName());
    const QString balloonFactory =
        QStringLiteral(
            "App.getDocument('%1').addObject('TechDraw::DrawViewBalloon', '%2')")
            .arg(QString::fromStdString(documentName), QString::fromStdString(featureName));
    auto* balloon = dynamic_cast<DrawViewBalloon*>(
        Gui::Command::runDocumentObjectCommand(
            Gui::Command::Doc,
            *document,
            balloonFactory.toUtf8(),
            DrawViewBalloon::getClassTypeId()));
    if (!balloon) {
        throw Base::TypeError("The balloon object could not be created");
    }

    balloon->translateLabel("DrawViewBalloon", "Balloon", balloon->getNameInDocument());
    if (label) {
        balloon->Label.setValue(label->c_str());
    }
    balloon->SourceView.setValue(sourceView);
    balloon->setOrigin(anchorInSourceMm);
    balloon->setPosition(bubbleInSourceMm.x, bubbleInSourceMm.y);
    if (anchorElement) {
        balloon->AnchorSource.setValue(
            sourceView,
            std::vector<std::string>{*anchorElement});
    }
    if (text) {
        balloon->Text.setValue(text->c_str());
    }
    else {
        balloon->Text.setValue(std::to_string(page->getNextBalloonIndex()).c_str());
    }

    const std::string pageCommand = Gui::Command::getObjectCmd(page);
    const std::string balloonCommand = Gui::Command::getObjectCmd(balloon);
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s.addView(%s)",
        pageCommand.c_str(),
        balloonCommand.c_str());
    balloon->recomputeFeature();
    if (balloon->isError()) {
        throw Base::RuntimeError("The balloon could not be generated");
    }
    return balloon;
}

DrawViewBalloon* TechDrawGui::createProjectedBalloonFeature(
    DrawViewPart* sourceView,
    const std::string& elementName,
    const std::string& text,
    const std::string& label,
    const Base::Vector3d& bubbleOffsetInViewMm)
{
    const ProjectedBalloonAnchor anchor =
        TechDrawGui::validateProjectedBalloonAnchor(sourceView, elementName);
    if (!finitePoint(bubbleOffsetInViewMm)) {
        throw Base::ValueError("The balloon bubble offset must be finite");
    }
    const Base::Vector3d bubbleInSourceMm =
        anchor.pointInSourceMm + bubbleOffsetInViewMm / sourceView->getScale();
    return TechDrawGui::createBalloonFeature(
        sourceView,
        anchor.pointInSourceMm,
        bubbleInSourceMm,
        text,
        elementName,
        label);
}

TechDrawGui::ProjectedMeasurementAnnotation
TechDrawGui::validateProjectedMeasurementAnnotation(
    DrawViewPart* view,
    MeasurementAnnotationKind kind,
    const std::vector<std::string>& elements)
{
    requireLiveSource(view);
    requireMeasurementElements(elements);
    const double scale = view->getScale();

    if (kind == MeasurementAnnotationKind::Area) {
        Base::Vector3d weightedCenter;
        double areaInViewMm2 = 0.0;
        for (const std::string& name : elements) {
            if (TechDraw::DrawUtil::getGeomTypeFromName(name) != "Face") {
                throw Base::ValueError(
                    "An area annotation requires only exact FaceN elements");
            }
            const TechDraw::FacePtr face = view->getFace(name);
            if (!face) {
                throw Base::ValueError(
                    "An area annotation face is unavailable in the projected view");
            }
            GProp_GProps properties;
            BRepGProp::SurfaceProperties(face->toOccFace(), properties);
            const double area = properties.Mass();
            if (!std::isfinite(area) || area <= Precision::Confusion()) {
                throw Base::ValueError(
                    "An area annotation face has no measurable area");
            }
            areaInViewMm2 += area;
            weightedCenter +=
                area * Base::convertTo<Base::Vector3d>(properties.CentreOfMass());
        }
        if (!std::isfinite(areaInViewMm2)
            || areaInViewMm2 <= Precision::Confusion()) {
            throw Base::ValueError(
                "The selected faces have no measurable area");
        }
        const Base::Vector3d centerInViewMm =
            TechDraw::DrawUtil::invertY(weightedCenter / areaInViewMm2);
        const Base::Vector3d centerInSourceMm = centerInViewMm / scale;
        const double areaMm2 = areaInViewMm2 / (scale * scale);
        if (!finitePoint(centerInViewMm) || !finitePoint(centerInSourceMm)
            || !std::isfinite(areaMm2) || areaMm2 <= 0.0) {
            throw Base::RuntimeError(
                "The projected area annotation has invalid measurement data");
        }
        Base::Quantity quantity;
        quantity.setValue(areaMm2);
        quantity.setUnit(Base::Unit::Area);
        return {
            kind,
            elements,
            areaMm2,
            centerInViewMm,
            centerInSourceMm,
            Base::UnitsApi::toUnicodeSuperscript(quantity.getUserString()),
        };
    }

    if (kind == MeasurementAnnotationKind::ArcLength) {
        std::vector<TechDraw::BaseGeomPtr> edges;
        std::vector<double> cumulativeLengths;
        double lengthInViewMm = 0.0;
        edges.reserve(elements.size());
        cumulativeLengths.reserve(elements.size());
        for (const std::string& name : elements) {
            if (TechDraw::DrawUtil::getGeomTypeFromName(name) != "Edge") {
                throw Base::ValueError(
                    "An arc-length annotation requires only exact EdgeN elements");
            }
            const TechDraw::BaseGeomPtr edge = view->getEdge(name);
            if (!edge) {
                throw Base::ValueError(
                    "An arc-length annotation edge is unavailable in the projected view");
            }
            GProp_GProps properties;
            BRepGProp::LinearProperties(edge->getOCCEdge(), properties);
            const double edgeLength = properties.Mass();
            if (!std::isfinite(edgeLength) || edgeLength <= Precision::Confusion()) {
                throw Base::ValueError(
                    "An arc-length annotation edge has no measurable length");
            }
            lengthInViewMm += edgeLength;
            edges.push_back(edge);
            cumulativeLengths.push_back(lengthInViewMm);
        }
        if (!std::isfinite(lengthInViewMm)
            || lengthInViewMm <= Precision::Confusion()) {
            throw Base::ValueError(
                "The selected edges have no measurable length");
        }

        const double halfLength = lengthInViewMm * 0.5;
        std::size_t edgeIndex = 0;
        while (edgeIndex < cumulativeLengths.size()
               && cumulativeLengths[edgeIndex] < halfLength) {
            ++edgeIndex;
        }
        if (edgeIndex >= edges.size()) {
            edgeIndex = edges.size() - 1;
        }
        double lengthOnEdge = halfLength;
        if (edgeIndex > 0) {
            lengthOnEdge -= cumulativeLengths[edgeIndex - 1];
        }

        BRepAdaptor_Curve curve(edges[edgeIndex]->getOCCEdge());
        gp_Pnt midpoint;
        curve.D0(curve.LastParameter(), midpoint);
        GCPnts_AbscissaPoint abscissa(
            Precision::Confusion(),
            curve,
            lengthOnEdge,
            curve.FirstParameter());
        if (abscissa.IsDone()) {
            curve.D0(abscissa.Parameter(), midpoint);
        }
        const Base::Vector3d anchorInViewMm = TechDraw::DrawUtil::invertY(
            Base::convertTo<Base::Vector3d>(midpoint));
        const Base::Vector3d anchorInSourceMm = anchorInViewMm / scale;
        const double lengthMm = lengthInViewMm / scale;
        if (!finitePoint(anchorInViewMm) || !finitePoint(anchorInSourceMm)
            || !std::isfinite(lengthMm) || lengthMm <= 0.0) {
            throw Base::RuntimeError(
                "The projected arc-length annotation has invalid measurement data");
        }
        TechDraw::DrawViewDimension formatter;
        using Format = TechDraw::DimensionFormatter::Format;
        const std::string formatted = formatter.formatValue(
            lengthMm,
            QString::fromUtf8(formatter.FormatSpec.getStrValue().data()),
            formatter.isMultiValueSchema() ? Format::UNALTERED : Format::FORMATTED);
        return {
            kind,
            elements,
            lengthMm,
            anchorInViewMm,
            anchorInSourceMm,
            "◠ " + formatted,
        };
    }

    throw Base::ValueError(
        "The projected measurement kind is unsupported");
}

DrawViewBalloon* TechDrawGui::createProjectedMeasurementAnnotationFeature(
    DrawViewPart* sourceView,
    MeasurementAnnotationKind kind,
    const std::vector<std::string>& elements,
    const std::optional<std::string>& label)
{
    const ProjectedMeasurementAnnotation measurement =
        validateProjectedMeasurementAnnotation(sourceView, kind, elements);
    Base::Vector3d bubbleInSourceMm = measurement.anchorInSourceMm;
    if (kind == MeasurementAnnotationKind::ArcLength) {
        const double textOffsetInSourceMm = 20.0 / sourceView->getScale();
        bubbleInSourceMm += Base::Vector3d(
            textOffsetInSourceMm, textOffsetInSourceMm, 0.0);
    }
    DrawViewBalloon* balloon = createBalloonFeature(
        sourceView,
        measurement.anchorInSourceMm,
        bubbleInSourceMm,
        measurement.text,
        std::nullopt,
        label);
    balloon->MeasurementKind.setValue(measurementKindName(kind));
    balloon->MeasurementSource.setValue(sourceView, measurement.elements);
    balloon->MeasurementValue.setValue(measurement.value);

    auto* provider = balloonViewProvider(balloon);
    if (kind == MeasurementAnnotationKind::Area) {
        balloon->BubbleShape.setValue("Rectangle");
        balloon->EndType.setValue("None");
        balloon->KinkLength.setValue(0.0);
        balloon->ScaleType.setValue("Page");
        provider->Fontsize.setValue(2.0);
        provider->LineWidth.setValue(
            TechDraw::LineGroup::getDefaultWidth("Graphic"));
        provider->LineVisible.setValue(false);
        provider->Color.setValue(Base::Color(1.0, 0.0, 0.0));
    }
    else {
        const int standardStyle =
            TechDraw::Preferences::getPreferenceGroup("Dimensions")->GetInt(
                "StandardAndStyle",
                TechDrawGui::ViewProviderDimension::STD_STYLE_ISO_ORIENTED);
        const bool asmeStyle =
            standardStyle
                == TechDrawGui::ViewProviderDimension::STD_STYLE_ASME_INLINED
            || standardStyle
                == TechDrawGui::ViewProviderDimension::STD_STYLE_ASME_REFERENCING;
        balloon->BubbleShape.setValue(asmeStyle ? "None" : "Line");
        balloon->EndType.setValue(
            TechDraw::Preferences::getPreferenceGroup("Dimensions")->GetInt(
                "ArrowStyle", 0));
        balloon->KinkLength.setValue(
            (asmeStyle ? 12.0 : 1.0) * provider->LineWidth.getValue());
    }
    balloon->recomputeFeature();
    if (balloon->isError()) {
        throw Base::RuntimeError(
            "The projected measurement annotation could not be generated");
    }
    return balloon;
}
