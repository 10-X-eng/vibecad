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

#include "PageUpdateBuilder.h"

#include <App/Document.h>
#include <Base/Exception.h>
#include <Mod/TechDraw/App/DrawPage.h>


TechDrawGui::DrawingKeepUpdatedPlan
TechDrawGui::inspectDrawingKeepUpdated(TechDraw::DrawPage* page)
{
    if (!page || !page->getDocument() || !page->getDocument()->containsObject(page)) {
        throw Base::ValueError(
            "Drawing update policy requires a live page in its document");
    }
    const bool previous = page->KeepUpdated.getValue();
    return {
        page,
        page->getNameInDocument() ? page->getNameInDocument() : "",
        previous,
        previous,
        false};
}

TechDrawGui::DrawingKeepUpdatedPlan
TechDrawGui::validateDrawingKeepUpdated(
    TechDraw::DrawPage* page,
    bool keepUpdated)
{
    auto plan = inspectDrawingKeepUpdated(page);
    plan.keepUpdated = keepUpdated;
    plan.changed = plan.previousKeepUpdated != keepUpdated;
    return plan;
}

TechDrawGui::DrawingKeepUpdatedPlan
TechDrawGui::changeDrawingKeepUpdated(
    TechDraw::DrawPage* page,
    bool keepUpdated)
{
    const auto plan = validateDrawingKeepUpdated(page, keepUpdated);
    if (plan.changed) {
        page->KeepUpdated.setValue(keepUpdated);
    }
    if (page->KeepUpdated.getValue() != keepUpdated) {
        throw Base::RuntimeError(
            "The Drawing page did not retain its requested update policy");
    }
    return plan;
}

