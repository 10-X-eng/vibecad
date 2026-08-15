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

#include "FrameVisibilityBuilder.h"

#include <App/Document.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Mod/TechDraw/App/DrawPage.h>
#include <Mod/TechDraw/App/DrawViewPart.h>

#include "PreferencesGui.h"
#include "QGSPage.h"
#include "MDIViewPage.h"
#include "ViewProviderPage.h"
#include "ViewProviderViewPart.h"


namespace
{

TechDrawGui::ViewProviderPage* requireLivePageProvider(
    TechDrawGui::ViewProviderPage* pageProvider)
{
    auto* page = pageProvider ? pageProvider->getDrawPage() : nullptr;
    if (!pageProvider || !page || !page->getDocument()) {
        throw Base::ValueError(
            "Drawing presentation requires a live page in its document");
    }
    return pageProvider;
}

TechDrawGui::ViewProviderPage* requireActivePageProvider(
    TechDrawGui::ViewProviderPage* pageProvider)
{
    pageProvider = requireLivePageProvider(pageProvider);
    auto* pageWindow = pageProvider->getMDIViewPage();
    if (!pageWindow || Gui::getMainWindow()->activeWindow() != pageWindow) {
        throw Base::ValueError(
            "Drawing presentation requires the exact page to be human-active");
    }
    return pageProvider;
}

TechDrawGui::ViewProviderPage* activePageProviderForView(
    TechDraw::DrawViewPart* view)
{
    auto* page = view ? view->findParentPage() : nullptr;
    auto* window = dynamic_cast<TechDrawGui::MDIViewPage*>(
        Gui::getMainWindow()->activeWindow());
    auto* pageProvider = window ? window->getViewProviderPage() : nullptr;
    if (!view || !view->getDocument() || !page || !pageProvider
        || pageProvider->getDrawPage() != page
        || page->getDocument() != view->getDocument()) {
        throw Base::ValueError(
            "Hidden-edge presentation requires a view on the human-active Drawing page");
    }
    return pageProvider;
}

TechDrawGui::ViewProviderViewPart* viewProviderFor(
    TechDraw::DrawViewPart* view)
{
    auto* document = view ? view->getDocument() : nullptr;
    auto* page = view ? view->findParentPage() : nullptr;
    auto* guiDocument = document
        ? Gui::Application::Instance->getDocument(document)
        : nullptr;
    auto* provider = guiDocument
        ? dynamic_cast<TechDrawGui::ViewProviderViewPart*>(
            guiDocument->getViewProvider(view))
        : nullptr;
    if (!page || !provider) {
        throw Base::ValueError(
            "The Drawing view has no live graphical view provider");
    }
    return provider;
}

}  // namespace


TechDrawGui::DrawingFrameVisibilityPlan
TechDrawGui::inspectDrawingFrameVisibility(ViewProviderPage* pageProvider)
{
    auto* page = pageProvider ? pageProvider->getDrawPage() : nullptr;
    auto* scene = pageProvider ? pageProvider->getQGSPage() : nullptr;
    auto* pageWindow = pageProvider ? pageProvider->getMDIViewPage() : nullptr;
    if (!pageProvider || !page || !page->getDocument() || !scene || !pageWindow) {
        throw Base::ValueError(
            "Frame visibility requires a live Drawing page open in its page view");
    }
    if (PreferencesGui::getViewFrameMode() != ViewFrameMode::Manual) {
        throw Base::ValueError(
            "Frame visibility is available only when View Frames Visibility is Manual");
    }

    const bool previous = pageProvider->getFrameState();
    return {
        pageProvider,
        page->getNameInDocument() ? page->getNameInDocument() : "",
        previous,
        previous,
        false,
        scene->getViews().size()};
}

TechDrawGui::DrawingFrameVisibilityPlan
TechDrawGui::validateDrawingFrameVisibility(
    ViewProviderPage* pageProvider,
    bool visible)
{
    auto plan = inspectDrawingFrameVisibility(pageProvider);
    if (Gui::getMainWindow()->activeWindow() != pageProvider->getMDIViewPage()) {
        throw Base::ValueError(
            "Frame visibility requires the exact Drawing page to be human-active");
    }
    plan.visible = visible;
    plan.changed = plan.previousVisible != visible;
    return plan;
}

TechDrawGui::DrawingFrameVisibilityPlan
TechDrawGui::changeDrawingFrameVisibility(
    ViewProviderPage* pageProvider,
    bool visible)
{
    const auto plan = validateDrawingFrameVisibility(pageProvider, visible);
    if (plan.changed) {
        pageProvider->toggleFrameState();
    }
    if (pageProvider->getFrameState() != visible) {
        throw Base::RuntimeError(
            "The Drawing page did not retain the requested frame visibility");
    }
    return plan;
}

TechDrawGui::DrawingGridVisibilityPlan
TechDrawGui::inspectDrawingGridVisibility(ViewProviderPage* pageProvider)
{
    pageProvider = requireLivePageProvider(pageProvider);
    auto* page = pageProvider->getDrawPage();
    const bool previous = pageProvider->ShowGrid.getValue();
    return {
        pageProvider,
        page->getNameInDocument() ? page->getNameInDocument() : "",
        previous,
        previous,
        false};
}

TechDrawGui::DrawingGridVisibilityPlan
TechDrawGui::validateDrawingGridVisibility(
    ViewProviderPage* pageProvider,
    bool visible)
{
    auto plan = inspectDrawingGridVisibility(pageProvider);
    requireActivePageProvider(pageProvider);
    plan.visible = visible;
    plan.changed = plan.previousVisible != visible;
    return plan;
}

TechDrawGui::DrawingGridVisibilityPlan
TechDrawGui::changeDrawingGridVisibility(
    ViewProviderPage* pageProvider,
    bool visible)
{
    const auto plan = validateDrawingGridVisibility(pageProvider, visible);
    if (plan.changed) {
        pageProvider->ShowGrid.setValue(visible);
    }
    if (pageProvider->ShowGrid.getValue() != visible) {
        throw Base::RuntimeError(
            "The Drawing page did not retain the requested grid visibility");
    }
    return plan;
}

TechDrawGui::DrawingHiddenEdgeVisibilityPlan
TechDrawGui::inspectDrawingHiddenEdgeVisibility(TechDraw::DrawViewPart* view)
{
    auto* page = view ? view->findParentPage() : nullptr;
    auto* provider = viewProviderFor(view);
    const bool previous = provider->ShowAllEdges.getValue();
    return {
        page && page->getNameInDocument() ? page->getNameInDocument() : "",
        view->getNameInDocument() ? view->getNameInDocument() : "",
        previous,
        previous,
        false};
}

TechDrawGui::DrawingHiddenEdgeVisibilityPlan
TechDrawGui::validateDrawingHiddenEdgeVisibility(
    TechDraw::DrawViewPart* view,
    bool visible)
{
    auto plan = inspectDrawingHiddenEdgeVisibility(view);
    activePageProviderForView(view);
    plan.visible = visible;
    plan.changed = plan.previousVisible != visible;
    return plan;
}

TechDrawGui::DrawingHiddenEdgeVisibilityPlan
TechDrawGui::changeDrawingHiddenEdgeVisibility(
    TechDraw::DrawViewPart* view,
    bool visible)
{
    const auto plan = validateDrawingHiddenEdgeVisibility(view, visible);
    auto* provider = viewProviderFor(view);
    if (plan.changed) {
        provider->ShowAllEdges.setValue(visible);
        view->requestPaint();
    }
    if (provider->ShowAllEdges.getValue() != visible) {
        throw Base::RuntimeError(
            "The Drawing view did not retain the requested hidden-edge visibility");
    }
    return plan;
}
