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

#include "FormatBuilder.h"

#include <QString>

#include <App/DocumentObject.h>
#include <Base/Exception.h>
#include <Mod/TechDraw/App/DrawViewBalloon.h>
#include <Mod/TechDraw/App/DrawViewDimension.h>


using TechDraw::DrawViewBalloon;
using TechDraw::DrawViewDimension;

namespace
{

void requireLivePageTarget(App::DocumentObject* object)
{
    if (!object || !object->getDocument()) {
        throw Base::ValueError("A live Drawing dimension or balloon is required");
    }
    if (auto* dimension = dynamic_cast<DrawViewDimension*>(object)) {
        if (!dimension->findParentPage()) {
            throw Base::ValueError("The Drawing dimension is not attached to a live page");
        }
        return;
    }
    if (auto* balloon = dynamic_cast<DrawViewBalloon*>(object)) {
        if (!balloon->findParentPage()) {
            throw Base::ValueError("The Drawing balloon is not attached to a live page");
        }
        return;
    }
    throw Base::TypeError("Customize Format requires a dimension or balloon");
}

}  // namespace

TechDrawGui::DrawingFormatCustomization TechDrawGui::validateDrawingFormatCustomization(
    App::DocumentObject* object,
    const std::string& value)
{
    requireLivePageTarget(object);
    if (auto* dimension = dynamic_cast<DrawViewDimension*>(object)) {
        const QString format = QString::fromUtf8(value.data(), static_cast<qsizetype>(value.size()));
        const std::string preview = dimension->formatValue(
            dimension->getDimValue(),
            format,
            TechDraw::DimensionFormatter::Format::FORMATTED,
            true);
        if (preview.empty() && !format.isEmpty()) {
            throw Base::ValueError(
                "Invalid dimension format; use one numeric placeholder such as %f, %.2f, %g, %w, or %r");
        }
        return {"dimension", value, preview};
    }

    return {"balloon", value, value};
}

TechDrawGui::DrawingFormatCustomization TechDrawGui::applyDrawingFormatCustomization(
    App::DocumentObject* object,
    const std::string& value)
{
    DrawingFormatCustomization result = validateDrawingFormatCustomization(object, value);
    if (auto* dimension = dynamic_cast<DrawViewDimension*>(object)) {
        dimension->FormatSpec.setValue(value);
        dimension->recomputeFeature();
    }
    else if (auto* balloon = dynamic_cast<DrawViewBalloon*>(object)) {
        balloon->Text.setValue(value);
        balloon->recomputeFeature();
    }
    return result;
}
