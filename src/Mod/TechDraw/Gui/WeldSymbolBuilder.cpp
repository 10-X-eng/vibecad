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

#include "WeldSymbolBuilder.h"

#include <array>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <ranges>
#include <string_view>

#include <QCryptographicHash>

#include <App/Application.h>
#include <App/Document.h>
#include <App/DocumentTimeline.h>
#include <Base/Exception.h>
#include <Base/FileInfo.h>
#include <Mod/TechDraw/App/DrawLeaderLine.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawTileWeld.h>
#include <Mod/TechDraw/App/DrawWeldSymbol.h>


namespace
{

constexpr std::size_t MaximumTextBytes = 256;
constexpr std::size_t MaximumLabelBytes = 512;

struct CatalogDefinition
{
    std::string_view key;
    std::string_view path;
};

constexpr auto Catalog = std::to_array<CatalogDefinition>({
    {"blank", "blankTile.svg"},
    {"aws_square_down", "AWS/SquareDown.svg"},
    {"aws_square_up", "AWS/SquareUp.svg"},
    {"aws_v_down", "AWS/VDown.svg"},
    {"aws_v_up", "AWS/VUp.svg"},
    {"aws_bead_down", "AWS/beadDown.svg"},
    {"aws_bead_up", "AWS/beadUp.svg"},
    {"aws_fillet_down", "AWS/filletDown.svg"},
    {"aws_fillet_up", "AWS/filletUp.svg"},
    {"aws_plug", "AWS/plug.svg"},
    {"gost_edge_weld", "GOST/edge-weld.svg"},
    {"gost_flanging", "GOST/flanging.svg"},
    {"gost_flare_bevel_groove", "GOST/flare-bevel-groove.svg"},
    {"gost_flare_v_groove", "GOST/flare-v-groove.svg"},
    {"gost_seam_weld", "GOST/seam-weld.svg"},
    {"gost_single_bevel_cjp_groove", "GOST/single-bevel-cjp-groove-weld.svg"},
    {"gost_single_bevel_broad_root", "GOST/single-bevel-groove-weld-with-broad-root-face.svg"},
    {"gost_single_bevel_groove", "GOST/single-bevel-groove-weld.svg"},
    {"gost_single_j_groove", "GOST/single-j-groove-weld.svg"},
    {"gost_single_u_groove", "GOST/single-u-groove-weld.svg"},
    {"gost_single_v_cjp_groove", "GOST/single-v-cjp-groove-weld.svg"},
    {"gost_single_v_broad_root", "GOST/single-v-groove-weld-with-broad-root-face.svg"},
    {"gost_single_v_groove", "GOST/single-v-groove-weld.svg"},
    {"gost_spile_weld", "GOST/spile-weld.svg"},
    {"gost_square_groove", "GOST/square-groove-weld.svg"},
    {"gost_surfacing", "GOST/surfacing.svg"},
});

std::filesystem::path catalogRoot()
{
    return std::filesystem::path(App::Application::getResourceDir())
        / "Mod" / "TechDraw" / "Symbols" / "Welding";
}

std::string fileSha256(const std::filesystem::path& path)
{
    std::ifstream stream(path, std::ios::binary);
    if (!stream) {
        throw Base::RuntimeError("A configured weld-symbol catalog asset is unreadable");
    }
    const std::string bytes{
        std::istreambuf_iterator<char>(stream),
        std::istreambuf_iterator<char>()};
    return QCryptographicHash::hash(
               QByteArray(bytes.data(), static_cast<qsizetype>(bytes.size())),
               QCryptographicHash::Sha256)
        .toHex()
        .toStdString();
}

std::string canonicalSymbolFile(const std::string& value)
{
    if (value.empty()) {
        throw Base::ValueError("A weld tile requires one SVG symbol file");
    }
    const Base::FileInfo info(value);
    if (!info.isReadable()) {
        throw Base::ValueError("A weld tile symbol file is not readable");
    }
    std::error_code error;
    const std::filesystem::path canonical = std::filesystem::weakly_canonical(value, error);
    if (error || canonical.extension() != ".svg") {
        throw Base::ValueError("A weld tile symbol must be one readable SVG file");
    }
    return canonical.string();
}

void requireText(const std::string& value, const char* noun)
{
    if (value.size() > MaximumTextBytes) {
        throw Base::ValueError(
            std::string("Weld-symbol ") + noun + " exceeds 256 UTF-8 bytes");
    }
}

TechDraw::DrawPage* requireLeader(TechDraw::DrawLeaderLine* leader)
{
    if (!leader || !leader->getDocument()) {
        throw Base::TypeError("A weld symbol requires one live Drawing leader");
    }
    auto* page = leader->findParentPage();
    if (!page || page->getDocument() != leader->getDocument()) {
        throw Base::ValueError("The weld-symbol leader is not attached to a Drawing page");
    }
    return page;
}

TechDrawGui::DrawingWeldSymbolSpec normalizedSpec(
    const TechDrawGui::DrawingWeldSymbolSpec& spec)
{
    TechDrawGui::DrawingWeldSymbolSpec result = spec;
    requireText(result.tailText, "tail text");
    for (auto* tile : {&result.arrowSide, &result.otherSide}) {
        requireText(tile->leftText, "left text");
        requireText(tile->centerText, "center text");
        requireText(tile->rightText, "right text");
        tile->symbolFile = canonicalSymbolFile(tile->symbolFile);
    }
    if (result.preferredLabel.empty() || result.preferredLabel.size() > MaximumLabelBytes) {
        throw Base::ValueError("A weld-symbol label requires 1 to 512 UTF-8 bytes");
    }
    return result;
}

std::pair<TechDraw::DrawTileWeld*, TechDraw::DrawTileWeld*>
exactTiles(TechDraw::DrawWeldSymbol* symbol)
{
    TechDraw::DrawTileWeld* arrow = nullptr;
    TechDraw::DrawTileWeld* other = nullptr;
    const auto tiles = symbol ? symbol->getTiles() : std::vector<TechDraw::DrawTileWeld*>{};
    if (tiles.size() != 2) {
        throw Base::RuntimeError("A weld symbol must retain exactly two generated tiles");
    }
    for (auto* tile : tiles) {
        if (!tile || tile->getDocument() != symbol->getDocument()
            || tile->TileParent.getValue() != symbol) {
            throw Base::RuntimeError("A weld symbol has an invalid generated tile");
        }
        if (tile->TileRow.getValue() == 0 && !arrow) {
            arrow = tile;
        }
        else if (tile->TileRow.getValue() == -1 && !other) {
            other = tile;
        }
        else {
            throw Base::RuntimeError("A weld symbol has duplicate or invalid tile rows");
        }
    }
    return {arrow, other};
}

void applyTile(TechDraw::DrawTileWeld* tile,
               const TechDrawGui::DrawingWeldTileSpec& spec,
               int row)
{
    tile->TileRow.setValue(row);
    tile->TileColumn.setValue(0);
    tile->LeftText.setValue(spec.leftText.c_str());
    tile->CenterText.setValue(spec.centerText.c_str());
    tile->RightText.setValue(spec.rightText.c_str());
    tile->SymbolFile.setValue(spec.symbolFile.c_str());
    if (std::string(tile->SymbolIncluded.getValue()).empty()) {
        throw Base::RuntimeError("The weld tile did not embed its SVG symbol");
    }
}

void applyPlan(TechDraw::DrawWeldSymbol* symbol,
               const TechDrawGui::DrawingWeldSymbolPlan& plan)
{
    symbol->Label.setValue(plan.label.c_str());
    symbol->AllAround.setValue(plan.spec.allAround);
    symbol->FieldWeld.setValue(plan.spec.fieldWeld);
    symbol->AlternatingWeld.setValue(plan.spec.alternatingWeld);
    symbol->TailText.setValue(plan.spec.tailText.c_str());
    symbol->Leader.setValue(plan.leader);
    auto [arrow, other] = exactTiles(symbol);
    applyTile(arrow, plan.spec.arrowSide, 0);
    applyTile(other, plan.spec.otherSide, -1);
}

void requireTransaction(App::Document* document)
{
    if (!document || document->getBookedTransactionID() == App::NullTransaction) {
        throw Base::RuntimeError(
            "Weld-symbol editing requires a caller-owned document transaction");
    }
}

}  // namespace

std::vector<TechDrawGui::DrawingWeldCatalogItem>
TechDrawGui::drawingWeldSymbolCatalog()
{
    std::vector<DrawingWeldCatalogItem> result;
    result.reserve(Catalog.size());
    const auto root = catalogRoot();
    for (const auto& definition : Catalog) {
        const auto file = root / std::string(definition.path);
        result.push_back({
            std::string(definition.key),
            std::string(definition.path),
            fileSha256(file),
        });
    }
    return result;
}

std::string TechDrawGui::drawingWeldSymbolCatalogHash()
{
    QByteArray canonical;
    for (const auto& item : drawingWeldSymbolCatalog()) {
        canonical.append(item.key.data(), static_cast<qsizetype>(item.key.size()));
        canonical.append('\0');
        canonical.append(
            item.relativePath.data(), static_cast<qsizetype>(item.relativePath.size()));
        canonical.append('\0');
        canonical.append(
            item.svgSha256.data(), static_cast<qsizetype>(item.svgSha256.size()));
        canonical.append('\n');
    }
    return QCryptographicHash::hash(canonical, QCryptographicHash::Sha256)
        .toHex().toStdString();
}

std::string TechDrawGui::drawingWeldSymbolFileForCatalogKey(const std::string& key)
{
    const auto found = std::ranges::find_if(
        Catalog, [&key](const auto& item) { return item.key == key; });
    if (found == Catalog.end()) {
        throw Base::ValueError("The weld-symbol catalog key is unsupported");
    }
    return canonicalSymbolFile((catalogRoot() / std::string(found->path)).string());
}

TechDrawGui::DrawingWeldSymbolPlan
TechDrawGui::validateDrawingWeldSymbolCreation(
    TechDraw::DrawLeaderLine* leader,
    const DrawingWeldSymbolSpec& spec)
{
    auto* page = requireLeader(leader);
    const auto normalized = normalizedSpec(spec);
    const std::string baseName{"WeldSymbol"};
    const std::string objectName =
        page->getDocument()->getUniqueObjectName(baseName.c_str());
    const std::string suffix = objectName.rfind(baseName, 0) == 0
        ? objectName.substr(baseName.size())
        : objectName;
    return {
        page, leader, nullptr, objectName, normalized.preferredLabel + suffix, normalized,
    };
}

TechDrawGui::DrawingWeldSymbolPlan
TechDrawGui::validateDrawingWeldSymbolChange(
    TechDraw::DrawWeldSymbol* symbol,
    const DrawingWeldSymbolSpec& spec)
{
    if (!symbol || !symbol->getDocument()) {
        throw Base::TypeError("Weld-symbol editing requires one live weld symbol");
    }
    auto* leader = dynamic_cast<TechDraw::DrawLeaderLine*>(symbol->Leader.getValue());
    auto* page = requireLeader(leader);
    if (symbol->findParentPage() != page || symbol->getDocument() != page->getDocument()) {
        throw Base::ValueError("The weld symbol and its leader must share one Drawing page");
    }
    exactTiles(symbol);
    const auto normalized = normalizedSpec(spec);
    return {
        page, leader, symbol, symbol->getNameInDocument(), normalized.preferredLabel, normalized,
    };
}

TechDraw::DrawWeldSymbol* TechDrawGui::createDrawingWeldSymbol(
    TechDraw::DrawLeaderLine* leader,
    const DrawingWeldSymbolSpec& spec,
    DrawingWeldSymbolPlan* appliedPlan)
{
    DrawingWeldSymbolPlan plan = validateDrawingWeldSymbolCreation(leader, spec);
    auto* document = plan.page->getDocument();
    requireTransaction(document);
    auto* symbol = document->addObject<TechDraw::DrawWeldSymbol>(plan.objectName.c_str());
    if (!symbol || plan.objectName != symbol->getNameInDocument()) {
        throw Base::RuntimeError("The weld-symbol factory returned an incompatible identity");
    }
    applyPlan(symbol, plan);
    plan.page->addView(symbol);
    auto [arrow, other] = exactTiles(symbol);
    auto* timeline = App::DocumentTimeline::get(document);
    if (!timeline) {
        throw Base::RuntimeError("The weld symbol could not access document History");
    }
    for (auto* tile : {arrow, other}) {
        if (App::DocumentTimeline::timelineOwner(tile) != symbol
            || !timeline->isProvisionallyEnrolledByCurrentTransaction(tile)) {
            throw Base::RuntimeError(
                "The weld symbol did not retain its generated History resources");
        }
    }
    timeline->finalizeProvisionalOperationBlock(symbol, {arrow, other, symbol});
    plan.page->touch();
    symbol->recomputeFeature();
    if (symbol->isError() || !symbol->isValid()) {
        throw Base::RuntimeError("The weld symbol could not produce a valid Drawing result");
    }
    symbol->requestPaint();
    if (appliedPlan) {
        *appliedPlan = std::move(plan);
    }
    return symbol;
}

TechDraw::DrawWeldSymbol* TechDrawGui::changeDrawingWeldSymbol(
    TechDraw::DrawWeldSymbol* symbol,
    const DrawingWeldSymbolSpec& spec,
    DrawingWeldSymbolPlan* appliedPlan)
{
    DrawingWeldSymbolPlan plan = validateDrawingWeldSymbolChange(symbol, spec);
    requireTransaction(symbol->getDocument());
    applyPlan(symbol, plan);
    plan.page->touch();
    symbol->recomputeFeature();
    if (symbol->isError() || !symbol->isValid()) {
        throw Base::RuntimeError("The weld symbol could not retain a valid Drawing result");
    }
    symbol->requestPaint();
    if (appliedPlan) {
        *appliedPlan = std::move(plan);
    }
    return symbol;
}
