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

#include "SurfaceFinishSymbolBuilder.h"

#include <algorithm>
#include <array>
#include <cmath>
#include <ranges>
#include <sstream>
#include <string_view>

#include <QCryptographicHash>

#include <App/Document.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawView.h>
#include <Mod/TechDraw/App/DrawViewSymbol.h>

#include "QGIView.h"
#include "ViewProviderSymbol.h"
#include "ZVALUE.h"


namespace
{

constexpr std::size_t MaximumTextBytes = 128;
constexpr std::size_t MaximumLabelBytes = 512;
constexpr double MaximumCoordinateMm = 1.0e6;

constexpr auto IsoRoughnessValues = std::to_array<std::string_view>({
    "Ra50", "Ra25", "Ra12, 5", "Ra6, 3", "Ra3, 2", "Ra1, 6",
    "Ra0, 8", "Ra0, 4", "Ra0, 2", "Ra0, 1", "Ra0, 05", "Ra0, 025",
});
constexpr auto LayValues = std::to_array<std::string_view>({"", "=", "⟂", "X", "M", "C", "R"});
constexpr auto RoughnessGrades = std::to_array<std::string_view>({
    "", "N1", "N2", "N3", "N4", "N5", "N6", "N7", "N8", "N9", "N10", "N11",
});

bool contains(const auto& values, const std::string& value)
{
    return std::ranges::find(values, value) != values.end();
}

void requireText(const std::string& value, const char* noun)
{
    if (value.size() > MaximumTextBytes) {
        throw Base::ValueError(
            std::string("Surface-finish ") + noun + " exceeds 128 UTF-8 bytes");
    }
}

std::string escaped(const std::string& value)
{
    std::string result;
    result.reserve(value.size());
    for (const char character : value) {
        switch (character) {
        case '&': result += "&amp;"; break;
        case '<': result += "&lt;"; break;
        case '>': result += "&gt;"; break;
        case '\"': result += "&quot;"; break;
        case '\'': result += "&apos;"; break;
        default: result += character; break;
        }
    }
    return result;
}

void addLine(std::ostringstream& svg, int x1, int y1, int x2, int y2)
{
    svg << "<path stroke='#000' stroke-width='1' d='M" << x1 << ',' << y1
        << " L" << x2 << ',' << y2 << "'/>\n";
}

void addCircle(std::ostringstream& svg, int x, int y, int radius)
{
    svg << "<circle cx='" << x << "' cy='" << y << "' r='" << radius
        << "' fill='none' stroke='#000' stroke-width='1'/>\n";
}

void addText(std::ostringstream& svg, int x, int y, const std::string& text)
{
    svg << "<text x='" << x << "' y='" << y
        << "' style='font-size:18px'>" << escaped(text) << "</text>\n";
}

bool removalProhibited(TechDrawGui::DrawingSurfaceFinishType type)
{
    using Type = TechDrawGui::DrawingSurfaceFinishType;
    return type == Type::RemovalProhibited || type == Type::RemovalProhibitedAllAround;
}

bool removalRequired(TechDrawGui::DrawingSurfaceFinishType type)
{
    using Type = TechDrawGui::DrawingSurfaceFinishType;
    return type == Type::RemovalRequired || type == Type::RemovalRequiredAllAround;
}

bool allAround(TechDrawGui::DrawingSurfaceFinishType type)
{
    using Type = TechDrawGui::DrawingSurfaceFinishType;
    return type == Type::AnyMethodAllAround
        || type == Type::RemovalProhibitedAllAround
        || type == Type::RemovalRequiredAllAround;
}

std::string makeSvg(const TechDrawGui::DrawingSurfaceFinishSpec& spec)
{
    std::ostringstream svg;
    svg << "<?xml version='1.0'?>\n<svg xmlns='http://www.w3.org/2000/svg' "
           "width='150' height='64' viewBox='-25 0 175 64'>\n";
    addLine(svg, 0, 44, 12, 64);
    addLine(svg, 12, 64, 42, 14);
    if (removalProhibited(spec.symbolType)) {
        addCircle(svg, 12, 46, 9);
    }
    if (removalRequired(spec.symbolType)) {
        addLine(svg, 0, 44, 24, 44);
    }
    const int textOffset = allAround(spec.symbolType) ? 5 : 0;
    if (allAround(spec.symbolType)) {
        addCircle(svg, 42, 14, 6);
    }
    addText(svg, 42 + textOffset, 11, spec.method);
    const std::string secondary =
        spec.standard == TechDrawGui::DrawingSurfaceFinishStandard::ISO
        ? spec.isoRoughness
        : spec.samplingLength;
    addText(svg, 42 + textOffset, 30, secondary);
    const auto maximumLength = std::max(spec.method.size(), secondary.size());
    const int lineEnd = 42 + textOffset
        + static_cast<int>(std::max<std::size_t>(1, maximumLength) * 10);
    addLine(svg, 42, 14, lineEnd, 14);
    if (spec.standard == TechDrawGui::DrawingSurfaceFinishStandard::ASME) {
        addText(svg, -10, 35, spec.minimumRoughnessGrade);
        addText(svg, -10, 20, spec.maximumRoughnessGrade);
    }
    addText(svg, 20, 60, spec.lay);
    addText(svg, -25, 60, spec.machiningAllowance);
    svg << "</svg>\n";
    return svg.str();
}

void requireLiveTargets(TechDraw::DrawPage* page, TechDraw::DrawView* owner)
{
    if (!page || !page->getDocument()) {
        throw Base::TypeError("A surface-finish symbol requires one live Drawing page");
    }
    if (owner && (owner->getDocument() != page->getDocument()
                  || owner->findParentPage() != page)) {
        throw Base::ValueError(
            "The surface-finish owner must belong to the target Drawing page");
    }
}

void requireSpec(const TechDrawGui::DrawingSurfaceFinishSpec& spec,
                 double xMm,
                 double yMm)
{
    if (!std::isfinite(xMm) || !std::isfinite(yMm)
        || std::abs(xMm) > MaximumCoordinateMm || std::abs(yMm) > MaximumCoordinateMm
        || !std::isfinite(spec.rotationDegrees)
        || std::abs(spec.rotationDegrees) > 360000.0) {
        throw Base::ValueError(
            "Surface-finish placement and rotation must be finite and within documented limits");
    }
    for (const auto& [value, noun] : std::array{
             std::pair{&spec.method, "method"},
             std::pair{&spec.machiningAllowance, "machining allowance"},
             std::pair{&spec.samplingLength, "sampling length"},
         }) {
        requireText(*value, noun);
    }
    if (!contains(LayValues, spec.lay)) {
        throw Base::ValueError("Surface-finish lay is not a supported ISO/ASME symbol");
    }
    if (spec.standard == TechDrawGui::DrawingSurfaceFinishStandard::ISO) {
        if (!contains(IsoRoughnessValues, spec.isoRoughness)) {
            throw Base::ValueError("Surface-finish ISO roughness is unsupported");
        }
    }
    else if (!contains(RoughnessGrades, spec.minimumRoughnessGrade)
             || !contains(RoughnessGrades, spec.maximumRoughnessGrade)) {
        throw Base::ValueError("Surface-finish ASME roughness grades are unsupported");
    }
    if (spec.preferredLabel.empty() || spec.preferredLabel.size() > MaximumLabelBytes) {
        throw Base::ValueError(
            "A surface-finish symbol label requires 1 to 512 UTF-8 bytes");
    }
}

}  // namespace

TechDrawGui::DrawingSurfaceFinishPlan
TechDrawGui::validateDrawingSurfaceFinishSymbol(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    double xMm,
    double yMm,
    const DrawingSurfaceFinishSpec& spec)
{
    requireLiveTargets(page, owner);
    requireSpec(spec, xMm, yMm);
    const std::string objectBaseName{"SurfaceSymbol"};
    const std::string objectName =
        page->getDocument()->getUniqueObjectName(objectBaseName.c_str());
    const std::string suffix = objectName.rfind(objectBaseName, 0) == 0
        ? objectName.substr(objectBaseName.size())
        : objectName;
    const std::string svg = makeSvg(spec);
    const QByteArray bytes(svg.data(), static_cast<qsizetype>(svg.size()));
    return {
        page,
        owner,
        objectName,
        spec.preferredLabel + suffix,
        xMm,
        yMm,
        spec,
        svg,
        QCryptographicHash::hash(bytes, QCryptographicHash::Sha256).toHex().toStdString(),
    };
}

TechDraw::DrawViewSymbol* TechDrawGui::createDrawingSurfaceFinishSymbol(
    TechDraw::DrawPage* page,
    TechDraw::DrawView* owner,
    double xMm,
    double yMm,
    const DrawingSurfaceFinishSpec& spec,
    DrawingSurfaceFinishPlan* appliedPlan)
{
    DrawingSurfaceFinishPlan plan = validateDrawingSurfaceFinishSymbol(
        page, owner, xMm, yMm, spec);
    auto* document = page->getDocument();
    if (document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Surface-finish symbol creation requires a caller-owned document transaction");
    }
    auto* symbol = document->addObject<TechDraw::DrawViewSymbol>(plan.objectName.c_str());
    if (!symbol || plan.objectName != symbol->getNameInDocument()) {
        throw Base::RuntimeError(
            "The surface-finish symbol factory returned an incompatible identity");
    }
    symbol->Label.setValue(plan.label.c_str());
    symbol->Symbol.setValue(plan.svg.c_str());
    symbol->Rotation.setValue(plan.spec.rotationDegrees);
    symbol->Owner.setValue(owner);
    symbol->X.setValue(plan.xMm);
    symbol->Y.setValue(plan.yMm);
    page->addView(symbol);

    if (Gui::Application::Instance) {
        auto* provider = dynamic_cast<TechDrawGui::ViewProviderSymbol*>(
            TechDrawGui::QGIView::getViewProvider(symbol));
        if (provider) {
            provider->StackOrder.setValue(ZVALUE::DIMENSION);
        }
    }
    document->publishProvisionalTimelineOperationBlock(symbol, {}, {});
    page->touch();
    symbol->recomputeFeature();
    if (symbol->isError() || !symbol->isValid()) {
        throw Base::RuntimeError(
            "The surface-finish symbol could not produce a valid Drawing result");
    }
    symbol->requestPaint();
    if (appliedPlan) {
        *appliedPlan = std::move(plan);
    }
    return symbol;
}
