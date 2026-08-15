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

#include "ViewLockBuilder.h"

#include <set>

#include <Base/Exception.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawViewPart.h>


namespace
{

constexpr std::size_t MaximumViewLockTargets = 32;

void validateRequests(
    TechDraw::DrawPage* page,
    const std::vector<TechDrawGui::DrawingViewLockRequest>& requests)
{
    if (!page || !page->getDocument()) {
        throw Base::ValueError("Drawing view locking requires a live page");
    }
    if (requests.empty() || requests.size() > MaximumViewLockTargets) {
        throw Base::ValueError("Drawing view locking requires 1 to 32 exact views");
    }

    std::set<TechDraw::DrawViewPart*> seen;
    for (const auto& request : requests) {
        auto* view = request.view;
        if (!view || view->getDocument() != page->getDocument()
            || view->findParentPage() != page) {
            throw Base::ValueError(
                "Every Drawing view lock target must belong to the exact page");
        }
        if (!seen.insert(view).second) {
            throw Base::ValueError(
                "A Drawing view lock target was provided more than once");
        }
        if (view->LockPosition.getValue() == request.locked) {
            throw Base::ValueError(
                "A Drawing view already has the requested lock state");
        }
    }
}

}  // namespace

std::vector<TechDrawGui::DrawingViewLockState>
TechDrawGui::changeDrawingViewLocks(
    TechDraw::DrawPage* page,
    const std::vector<DrawingViewLockRequest>& requests)
{
    validateRequests(page, requests);
    for (const auto& request : requests) {
        request.view->LockPosition.setValue(request.locked);
    }

    std::vector<DrawingViewLockState> result;
    result.reserve(requests.size());
    for (const auto& request : requests) {
        const bool locked = request.view->LockPosition.getValue();
        if (locked != request.locked) {
            throw Base::RuntimeError(
                "A Drawing view did not retain its requested lock state");
        }
        result.push_back({request.view, locked});
    }
    return result;
}
