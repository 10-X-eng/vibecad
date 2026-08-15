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

#include "DimensionTextBuilder.h"

#include <cctype>
#include <set>

#include <App/Document.h>
#include <Base/Exception.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawViewDimension.h>


namespace
{

using TechDrawGui::DrawingDimensionTextOperation;
using TechDrawGui::DrawingDimensionTextPlan;

constexpr std::size_t MaximumDimensionTargets = 64;

std::string insertionPrefix(
    DrawingDimensionTextOperation operation,
    const std::string& repetitionText)
{
    switch (operation) {
        case DrawingDimensionTextOperation::InsertDiameter:
            return "⌀";
        case DrawingDimensionTextOperation::InsertSquare:
            return "□";
        case DrawingDimensionTextOperation::InsertRepetition:
            return repetitionText + "× ";
        default:
            return {};
    }
}

DrawingDimensionTextPlan planOne(
    TechDraw::DrawViewDimension* dimension,
    DrawingDimensionTextOperation operation,
    const std::string& repetitionText)
{
    const std::string before = dimension->FormatSpec.getStrValue();
    std::string after = before;
    std::string prefix;
    std::string reason;
    int decimalBefore = -1;
    int decimalAfter = -1;

    if (operation == DrawingDimensionTextOperation::InsertDiameter
        || operation == DrawingDimensionTextOperation::InsertSquare
        || operation == DrawingDimensionTextOperation::InsertRepetition) {
        prefix = insertionPrefix(operation, repetitionText);
        after.insert(0, prefix);
    }
    else if (operation == DrawingDimensionTextOperation::RemovePrefix) {
        const std::size_t marker = before.find("%.");
        if (marker == std::string::npos) {
            reason = "the dimension format has no precision marker";
        }
        else if (marker == 0) {
            reason = "the dimension format has no prefix before its precision marker";
        }
        else {
            after = before.substr(marker);
        }
    }
    else {
        const std::size_t marker = before.find("%.");
        if (marker == std::string::npos || marker + 2 >= before.size()
            || !std::isdigit(static_cast<unsigned char>(before[marker + 2]))) {
            reason = "the dimension format has no single-digit precision marker";
        }
        else {
            decimalBefore = before[marker + 2] - '0';
            const int delta =
                operation == DrawingDimensionTextOperation::IncreaseDecimals
                ? 1
                : -1;
            decimalAfter = decimalBefore + delta;
            if (decimalAfter < 0 || decimalAfter > 9) {
                reason = delta > 0
                    ? "the dimension precision is already at the maximum of 9"
                    : "the dimension precision is already at the minimum of 0";
                decimalAfter = decimalBefore;
            }
            else {
                after[marker + 2] = static_cast<char>('0' + decimalAfter);
            }
        }
    }

    const bool changed = after != before;
    if (!changed && reason.empty()) {
        reason = "the requested dimension-text operation has no effect";
    }
    return {
        dimension,
        operation,
        dimension->getNameInDocument()
            ? dimension->getNameInDocument()
            : "",
        before,
        after,
        prefix,
        decimalBefore,
        decimalAfter,
        changed,
        reason};
}

}  // namespace

std::vector<DrawingDimensionTextPlan>
TechDrawGui::validateDrawingDimensionText(
    const std::vector<TechDraw::DrawViewDimension*>& dimensions,
    DrawingDimensionTextOperation operation,
    const std::string& repetitionText)
{
    if (dimensions.empty() || dimensions.size() > MaximumDimensionTargets) {
        throw Base::ValueError(
            "Dimension text changes require 1 to 64 exact Drawing dimensions");
    }
    if (operation != DrawingDimensionTextOperation::InsertRepetition
        && !repetitionText.empty()) {
        throw Base::ValueError(
            "Repetition text is accepted only by the repetition-prefix operation");
    }

    App::Document* document = nullptr;
    std::set<TechDraw::DrawViewDimension*> unique;
    std::vector<DrawingDimensionTextPlan> result;
    result.reserve(dimensions.size());
    for (auto* dimension : dimensions) {
        auto* currentPage = dimension ? dimension->findParentPage() : nullptr;
        if (!dimension || !dimension->getDocument() || !currentPage
            || currentPage->getDocument() != dimension->getDocument()) {
            throw Base::ValueError(
                "Every dimension-text target must be a live Drawing dimension on a page");
        }
        if (!unique.insert(dimension).second) {
            throw Base::ValueError(
                "A dimension-text change cannot repeat the same dimension target");
        }
        if (!document) {
            document = dimension->getDocument();
        }
        else if (dimension->getDocument() != document) {
            throw Base::ValueError(
                "All dimension-text targets must belong to the same document");
        }
        result.push_back(planOne(dimension, operation, repetitionText));
    }
    return result;
}

std::vector<DrawingDimensionTextPlan>
TechDrawGui::changeDrawingDimensionText(
    const std::vector<TechDraw::DrawViewDimension*>& dimensions,
    DrawingDimensionTextOperation operation,
    const std::string& repetitionText)
{
    auto plans = validateDrawingDimensionText(
        dimensions,
        operation,
        repetitionText);
    for (const auto& plan : plans) {
        if (plan.changed) {
            plan.dimension->FormatSpec.setValue(plan.formatSpecAfter);
        }
    }
    return plans;
}
