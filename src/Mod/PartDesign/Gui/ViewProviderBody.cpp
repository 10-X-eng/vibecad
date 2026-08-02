// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2011 Juergen Riegel <FreeCAD@juergen-riegel.net>        *
 *                                                                         *
 *   This file is part of the FreeCAD CAx development system.              *
 *                                                                         *
 *   This library is free software; you can redistribute it and/or         *
 *   modify it under the terms of the GNU Library General Public           *
 *   License as published by the Free Software Foundation; either          *
 *   version 2 of the License, or (at your option) any later version.      *
 *                                                                         *
 *   This library  is distributed in the hope that it will be useful,      *
 *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
 *   GNU Library General Public License for more details.                  *
 *                                                                         *
 *   You should have received a copy of the GNU Library General Public     *
 *   License along with this library; see the file COPYING.LIB. If not,    *
 *   write to the Free Software Foundation, Inc., 59 Temple Place,         *
 *   Suite 330, Boston, MA  02111-1307, USA                                *
 *                                                                         *
 ***************************************************************************/


#include <algorithm>
#include <map>
#include <set>
#include <string>
#include <string_view>

#include <Inventor/actions/SoGetBoundingBoxAction.h>
#include <QMenu>

#include <App/Application.h>
#include <App/Document.h>
#include <App/Origin.h>
#include <App/Part.h>
#include <App/VarSet.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Tools.h>
#include <Gui/ActionFunction.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/MDIView.h>
#include <Gui/ViewProviderDatum.h>
#include <Gui/ViewProviderDocumentObject.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/DesignFeature.h>
#include <Mod/PartDesign/App/FeatureSketchBased.h>
#include <Mod/PartDesign/App/FeatureBase.h>
#include <Mod/PartDesign/App/ShapeBinder.h>

#include "ViewProviderBody.h"
#include "Utils.h"
#include "ViewProvider.h"


using namespace PartDesignGui;

namespace
{
struct BodyPresentationState
{
    fastsignals::scoped_connection finishEditConnection;
    fastsignals::scoped_connection documentStableConnection;
    fastsignals::scoped_connection documentRestoredConnection;
    bool adjustingResultVisibility {false};
};

std::map<const ViewProviderBody*, BodyPresentationState>&
bodyPresentationStates()
{
    // ViewProviderBody is an exported class used by external modules. Keep
    // VibeCAD-only connection and recursion state out of its instance layout.
    static auto* states =
        new std::map<const ViewProviderBody*, BodyPresentationState>;
    return *states;
}
}  // namespace

const char* PartDesignGui::ViewProviderBody::BodyModeEnum[] = {"Through", "Tip", nullptr};

PROPERTY_SOURCE_WITH_EXTENSIONS(PartDesignGui::ViewProviderBody, PartGui::ViewProviderPart)

ViewProviderBody::ViewProviderBody()
{
    ADD_PROPERTY(DisplayModeBody, ((long)0));
    DisplayModeBody.setEnums(BodyModeEnum);

    sPixmap = "PartDesign_Body.svg";

    Gui::ViewProviderOriginGroupExtension::initExtension(this);
}

ViewProviderBody::~ViewProviderBody()
{
    bodyPresentationStates().erase(this);
}

void ViewProviderBody::attach(App::DocumentObject* pcFeat)
{
    // call parent attach method
    ViewProviderPart::attach(pcFeat);

    // set default display mode
    onChanged(&DisplayModeBody);

    if (App::Document* doc = pcFeat->getDocument()) {
        m_RecomputedConn = doc->signalRecomputed.connect(
            [this](const App::Document& doc, const std::vector<App::DocumentObject*>& recomputedObjs) {
                this->afterRecompute(doc, recomputedObjs);
            }
        );
        auto& presentationState = bodyPresentationStates()[this];
        presentationState.documentStableConnection =
            doc->signalBecameStable.connect(
                [this](const App::Document& document) {
                    this->normalizeResultPresentation(document, false);
                }
            );
        presentationState.documentRestoredConnection =
            App::GetApplication().signalFinishRestoreDocument.connect(
                [this](const App::Document& document) {
                    this->normalizeResultPresentation(document, true);
                }
            );
    }
    if (Gui::Application::Instance) {
        bodyPresentationStates()[this].finishEditConnection =
            Gui::Application::Instance->signalFinishEdit.connect(
                [this](const Gui::Document& guiDocument,
                       bool /*cancelled*/,
                       bool transactionFinished) {
                    auto* body = this->getObject<PartDesign::Body>();
                    if (!transactionFinished || !body
                        || !body->isAttachedToDocument()
                        || !body->getDocument()
                        || this->getDocument() != &guiDocument) {
                        return;
                    }
                    this->normalizeResultPresentation(
                        *body->getDocument(),
                        false
                    );
                }
            );
    }
    m_ChangedConn = Gui::Application::Instance->signalChangedObject.connect(
        [this](const Gui::ViewProvider& vp, const App::Property& prop) {
            this->onChangedObject(vp, prop);
        }
    );
}

void ViewProviderBody::onChangedObject(const Gui::ViewProvider& vp, const App::Property& prop)
{
    const char* propertyName = prop.getName();
    if (!propertyName || std::string_view(propertyName) != "Visibility") {
        return;
    }
    auto* vpd = dynamic_cast<const Gui::ViewProviderDocumentObject*>(&vp);
    if (!vpd) {
        return;
    }
    auto* changedObj = vpd->getObject();
    if (!changedObj) {
        return;
    }

    auto* body = this->getObject<PartDesign::Body>();
    if (!body || !body->isAttachedToDocument()) {
        return;
    }
    const auto& features = body->Group.getValues();
    const bool isRelevantChange = (changedObj == body)
        || (std::ranges::find(features, changedObj) != features.end());

    if (isRelevantChange) {
        // Outside a native task preview, history results are never independent
        // viewport objects. If a generic command or script tries to show an
        // older result, restore the Body contract immediately: its logical eye
        // controls exactly the current Tip.
        const bool isResult = changedObj != body
            && PartDesign::Body::isResultFeature(changedObj);
        if (isResult
            && !bodyPresentationStates()[this].adjustingResultVisibility
            && body->getDocument()) {
            normalizeResultPresentation(*body->getDocument(), false);
        }
        refreshOverlays();
    }
}

void ViewProviderBody::normalizeResultPresentation(
    const App::Document& document,
    bool documentRestoreFinished
)
{
    auto* body = getObject<PartDesign::Body>();
    if (!body || body->getDocument() != &document || isRestoring()) {
        return;
    }

    // signalFinishRestoreDocument is emitted after every object and view
    // provider has finished restoring, but immediately before the document's
    // Restoring bit is cleared. Other stability signals received while that
    // bit is set are too early: a later child restore could overwrite the
    // normalized state.
    if (document.isPerformingTransaction()
        || document.testStatus(App::Document::Recomputing)
        || (!documentRestoreFinished
            && (document.hasPendingTransaction()
                || document.isTransactionLocked()))
        || (!documentRestoreFinished
            && document.testStatus(App::Document::Restoring))) {
        return;
    }

    auto* guiDocument = getDocument();
    auto* editingView =
        guiDocument
        ? dynamic_cast<Gui::ViewProviderDocumentObject*>(
              guiDocument->getEditViewProvider()
          )
        : nullptr;
    auto* editingObject = editingView ? editingView->getObject() : nullptr;
    if (editingObject
        && (editingObject == body
            || PartDesign::Body::findBodyOf(editingObject) == body)) {
        // Sketcher TempoVis and feature task panels own their preview until
        // resetEdit. Normalizing here would make a consumed sketch or base
        // result reappear in the middle of an edit.
        return;
    }

    // The Body's physical Coin branch is a permanent container. Its persisted
    // Visibility is instead the logical visibility of the final result.
    // Re-mount the child branch first so an independently visible sketch or
    // datum remains drawable even when the result is hidden.
    useChildSceneMode();
    Gui::ViewProvider::show();
    setResultVisibility(Visibility.getValue());
}

void ViewProviderBody::afterRecompute(const App::Document& /* doc */, const std::vector<App::DocumentObject*>& /* recomputedObjs */)
{
    refreshOverlays();
}

void ViewProviderBody::refreshOverlays()
{
    auto* body = getObject<PartDesign::Body>();
    if (!body) {
        return;
    }
    for (auto* obj : body->Group.getValues()) {
        Gui::ViewProvider* vpBase = Gui::Application::Instance->getViewProvider(obj);
        if (auto* vpPartDesign = dynamic_cast<PartDesignGui::ViewProvider*>(vpBase)) {
            vpPartDesign->updateOverlay();
        }
    }
}

// TODO on activating the body switch to the "Through" mode (2015-09-05, Fat-Zer)
// TODO different icon in tree if mode is Through (2015-09-05, Fat-Zer)
// TODO drag&drop (2015-09-05, Fat-Zer)
// TODO Add activate () call (2015-09-08, Fat-Zer)

void ViewProviderBody::setDisplayMode(const char* ModeName)
{
    // DisplayMode is propagated to result children by onChanged(). Record the
    // requested mode without selecting the Body's copied Shape branch: the
    // current Tip child is VibeCAD's one and only viewport result.
    Gui::ViewProvider::setDisplayMode(ModeName);
    useChildSceneMode();
}

void ViewProviderBody::setOverrideMode(const std::string& mode)
{
    // The Body container must never select its own geometry mask, including
    // for DrawStyle overrides. Clear the base override switch, retain the
    // logical override value, and apply it to the actual result children.
    Gui::ViewProvider::setOverrideMode("As Is");
    overrideMode = mode;
    useChildSceneMode();

    // Propagate the override mode to child features. When the Body is an
    // external link, the global viewport loop won't reach these children.
    if (pcObject && !isRestoring()) {
        Gui::Document* gdoc = Gui::Application::Instance->getDocument(pcObject->getDocument());
        if (gdoc) {
            PartDesign::Body* body = static_cast<PartDesign::Body*>(getObject());
            auto features = body->Group.getValues();
            for (auto feature : features) {
                if (feature && PartDesign::Body::isResultFeature(feature)) {
                    if (Gui::ViewProvider* vp = gdoc->getViewProvider(feature)) {
                        vp->setOverrideMode(mode);
                    }
                }
            }
            if (App::DocumentObject* base = body->BaseFeature.getValue()) {
                if (Gui::ViewProvider* vp = gdoc->getViewProvider(base)) {
                    vp->setOverrideMode(mode);
                }
            }
        }
    }
}

void ViewProviderBody::setupContextMenu(QMenu* menu, QObject* receiver, const char* member)
{
    Q_UNUSED(receiver);
    Q_UNUSED(member);
    Gui::ActionFunction* func = new Gui::ActionFunction(menu);

    QAction* act = menu->addAction(tr("Active Body"));
    act->setCheckable(true);
    act->setChecked(isActiveBody());
    func->trigger(act, [this]() { this->toggleActiveBody(); });

    Gui::ViewProviderGeometryObject::setupContextMenu(menu, receiver, member);  // clazy:exclude=skipped-base-method
}

bool ViewProviderBody::isActiveBody()
{
    auto activeDoc = Gui::Application::Instance->activeDocument();
    if (!activeDoc) {
        activeDoc = getDocument();
    }
    auto activeView = activeDoc->setActiveView(this);
    if (!activeView) {
        return false;
    }

    if (activeView->isActiveObject(getObject(), PDBODYKEY)) {
        return true;
    }
    else {
        return false;
    }
}

void ViewProviderBody::toggleActiveBody()
{
    if (isActiveBody()) {
        // active body double-clicked. Deactivate.
        Gui::Command::doCommand(
            Gui::Command::Gui,
            "Gui.ActiveDocument.ActiveView.setActiveObject('%s', None)",
            PDBODYKEY
        );
    }
    else {

        // assure the PartDesign workbench
        if (App::GetApplication()
                .GetUserParameter()
                .GetGroup("BaseApp")
                ->GetGroup("Preferences")
                ->GetGroup("Mod/PartDesign")
                ->GetBool("SwitchToWB", true)) {
            Gui::Command::assureWorkbench("PartDesignWorkbench");
        }

        // and set correct active objects
        auto* part = App::Part::getPartOfObject(getObject());
        if (part && !isActiveBody()) {
            Gui::Command::doCommand(
                Gui::Command::Gui,
                "Gui.ActiveDocument.ActiveView.setActiveObject('%s',%s)",
                PARTKEY,
                Gui::Command::getObjectCmd(part).c_str()
            );
        }

        Gui::Command::doCommand(
            Gui::Command::Gui,
            "Gui.ActiveDocument.ActiveView.setActiveObject('%s',%s)",
            PDBODYKEY,
            Gui::Command::getObjectCmd(getObject()).c_str()
        );
    }
}

bool ViewProviderBody::doubleClicked()
{
    toggleActiveBody();
    return true;
}

App::DocumentObject* ViewProviderBody::documentTimelineOperationDeleteTarget() const
{
    auto* body = getObject<PartDesign::Body>();
    auto* publication = PartDesign::findDesignBodyPublication(body);
    if (!body || !publication) {
        return nullptr;
    }

    auto* state = freecad_cast<PartDesign::DesignBodyState*>(
        publication->CurrentState.getValue()
    );
    if (!state) {
        // A purely legacy/imported Body has no Design operation lifecycle yet.
        return nullptr;
    }

    std::set<PartDesign::DesignBodyState*> visited;
    while (auto* previous = freecad_cast<PartDesign::DesignBodyState*>(
               state->PreviousState.getValue()
           )) {
        if (!visited.insert(state).second) {
            throw Base::RuntimeError(
                "This Design Body has a cyclic state chain and cannot be deleted"
            );
        }
        state = previous;
    }
    if (state->PreviousState.getValue()) {
        throw Base::RuntimeError(
            "This imported Body participates in global History and cannot be "
            "deleted as a legacy container. Remove its History operations first."
        );
    }

    auto* operation = state->Operation.getValue();
    auto* properties =
        dynamic_cast<PartDesign::DesignOperationProperties*>(operation);
    const auto outputBodyIds =
        properties ? properties->OutputBodyIds.getValues()
                   : std::vector<std::string> {};
    if (!operation || operation->getDocument() != body->getDocument()
        || !properties || state->BodyId.getValueStr() != body->VibeCADBodyId.getValueStr()
        || outputBodyIds.size() != 1
        || outputBodyIds.front() != body->VibeCADBodyId.getValueStr()) {
        throw Base::RuntimeError(
            "This Body is not the sole output of one valid creating History "
            "operation. Delete the complete operation from History instead."
        );
    }
    return operation;
}

// TODO To be deleted (2015-09-08, Fat-Zer)
// void ViewProviderBody::updateTree()
//{
//    if (ActiveGuiDoc == NULL) return;
//
//    // Highlight active body and all its features
//    //Base::Console().error("ViewProviderBody::updateTree()\n");
//    PartDesign::Body* body = getObject<PartDesign::Body>();
//    bool active = body->IsActive.getValue();
//    //Base::Console().error("Body is %s\n", active ? "active" : "inactive");
//    ActiveGuiDoc->signalHighlightObject(*this, Gui::Blue, active);
//    std::vector<App::DocumentObject*> features = body->Group.getValues();
//    bool highlight = true;
//    App::DocumentObject* tip = body->Tip.getValue();
//    for (std::vector<App::DocumentObject*>::const_iterator f = features.begin(); f !=
//    features.end(); f++) {
//        //Base::Console().error("Highlighting %s: %s\n", (*f)->getNameInDocument(), highlight ?
//        "true" : "false"); Gui::ViewProviderDocumentObject* vp =
//        dynamic_cast<Gui::ViewProviderDocumentObject*>(Gui::Application::Instance->getViewProvider(*f));
//        if (vp != NULL)
//            ActiveGuiDoc->signalHighlightObject(*vp, Gui::LightBlue, active ? highlight : false);
//        if (highlight && (tip == *f))
//            highlight = false;
//    }
//}

bool ViewProviderBody::onDelete(const std::vector<std::string>&)
{
    // TODO May be do it conditionally? (2015-09-05, Fat-Zer)
    FCMD_OBJ_CMD(getObject(), "removeObjectsFromDocument()");
    return true;
}

void ViewProviderBody::updateData(const App::Property* prop)
{
    PartDesign::Body* body = getObject<PartDesign::Body>();

    if (prop == &body->Group || prop == &body->BaseFeature) {
        // ensure all model features are in visual body mode
        setVisualBodyMode(true);
    }
    if (prop == &body->Group && body->getDocument()) {
        // Adoption, deletion, and direct insertion can change the set of
        // result children without changing Tip. Apply the same stable-state
        // invariant as a Tip change so a newly grouped result cannot remain
        // drawn beside the Body's current result.
        normalizeResultPresentation(*body->getDocument(), false);
    }

    if (prop == &body->Tip) {
        // We changed Tip
        App::DocumentObject* tip = body->Tip.getValue();

        auto features = body->Group.getValues();

        // restore icons
        for (auto feature : features) {
            Gui::ViewProvider* vp = Gui::Application::Instance->getViewProvider(feature);
            if (vp && vp->isDerivedFrom<PartDesignGui::ViewProvider>()) {
                static_cast<PartDesignGui::ViewProvider*>(vp)->setTipIcon(feature == tip);
            }
        }

        // Moving the end-of-part marker changes which native result represents
        // the Body. Visibility is part of that atomic state change: the new Tip
        // replaces the old Tip instead of being drawn on top of it. During
        // undo/redo, Cancel rollback, restore, and recompute, property replay
        // owns the object graph; signalBecameStable/finishRestore performs the
        // same normalization once replay is complete.
        if (const auto* document = body->getDocument()) {
            normalizeResultPresentation(*document, false);
        }
    }

    PartGui::ViewProviderPart::updateData(prop);

    // ViewProviderDocumentObject::updateView() temporarily removes a visible
    // scene node while updating all properties. A logically hidden Body keeps
    // its container mounted so independently enabled sketches and references
    // can draw, so restore that container after every Body update.
    if (!Visibility.getValue()) {
        useChildSceneMode();
        Gui::ViewProvider::show();
    }
}

void ViewProviderBody::onChanged(const App::Property* prop)
{

    if (prop == &DisplayModeBody) {
        // Keep the legacy property readable for document compatibility, but
        // both values use the child scene in VibeCAD. Rendering the Body's
        // copied Shape would duplicate the Tip and would gate sketches when
        // the Body result is hidden.
        useChildSceneMode();

        // #0002559: Body becomes visible upon changing DisplayModeBody
        Visibility.touch();
    }
    else {
        unifyVisualProperty(prop);
    }

    // When changing transparency then adjust the ShapeAppearance inside onChanged()
    // of the base class but don't notify its container again. This breaks the chain of
    // notification and avoids the call of onChanged() with the ShapeAppearance as argument
    // This fixes issue https://github.com/FreeCAD/FreeCAD/issues/18075
    if (prop == &Transparency) {
        ShapeAppearance.enableNotify(false);
    }

    PartGui::ViewProviderPartExt::onChanged(prop);

    if (prop == &Transparency) {
        ShapeAppearance.enableNotify(true);
    }
}

void ViewProviderBody::unifyVisualProperty(const App::Property* prop)
{

    if (!pcObject || isRestoring()) {
        return;
    }

    if (prop == &Visibility || prop == &Selectable || prop == &DisplayModeBody
        || prop == &PointColorArray || prop == &ShowPlacement || prop == &LineColorArray) {
        return;
    }

    // Fixes issue 11197. In case of affected projects where the bounding box of a sub-feature
    // is shown allow it to hide it
    if (prop == &BoundingBox) {
        if (BoundingBox.getValue()) {
            return;
        }
    }

    Gui::Document* gdoc = Gui::Application::Instance->getDocument(pcObject->getDocument());

    PartDesign::Body* body = static_cast<PartDesign::Body*>(getObject());
    auto features = body->Group.getValues();
    for (auto feature : features) {

        if (!PartDesign::Body::isResultFeature(feature)) {
            continue;
        }

        // copy over the properties data
        if (Gui::ViewProvider* vp = gdoc->getViewProvider(feature)) {
            if (auto fprop = vp->getPropertyByName(prop->getName())) {
                fprop->Paste(*prop);
            }
        }
    }
}

std::map<std::string, Base::Color> ViewProviderBody::getElementColors(const char* element) const
{
    // A PartDesign Body doesn't really have element colors on its own: it's a sort of container,
    // and its subshapes are the ones that have actual colors. If you query a body's ViewProvider
    // for its element colors, what you are really asking for is the element colors of its tip.
    PartDesign::Body* body = static_cast<PartDesign::Body*>(getObject());
    if (App::DocumentObject* tip = body->Tip.getValue()) {
        Gui::Document* guiDoc = Gui::Application::Instance->getDocument(tip->getDocument());
        Gui::ViewProvider* vp = guiDoc->getViewProvider(tip);
        return vp->getElementColors(element);
    }
    return ViewProviderPart::getElementColors(element);
}


void ViewProviderBody::setVisualBodyMode(bool bodymode)
{

    Gui::Document* gdoc = Gui::Application::Instance->getDocument(pcObject->getDocument());

    PartDesign::Body* body = static_cast<PartDesign::Body*>(getObject());
    auto features = body->Group.getValues();
    for (auto feature : features) {

        if (!feature->isDerivedFrom<PartDesign::Feature>()) {
            continue;
        }

        auto* vp = static_cast<PartDesignGui::ViewProvider*>(gdoc->getViewProvider(feature));
        if (vp) {
            vp->setBodyMode(bodymode);
        }
    }
}

void ViewProviderBody::useChildSceneMode()
{
    // A stale base override selects one of the Body's own shape masks even
    // after "Group" is requested. Clear only that physical switch while
    // preserving the logical override that result children consume.
    const std::string mode = getOverrideMode();
    if (mode != "As Is") {
        Gui::ViewProvider::setOverrideMode("As Is");
        overrideMode = mode;
    }

    setDisplayMaskMode("Group");
    if (auto* body = getObject<PartDesign::Body>()) {
        body->setShowTip(false);
    }
}

std::vector<std::string> ViewProviderBody::getDisplayModes() const
{

    // we get all display modes and remove the "Group" mode, as this is what we use for "Through"
    // body display mode
    std::vector<std::string> modes = ViewProviderPart::getDisplayModes();
    modes.erase(modes.begin());
    return modes;
}

PartDesign::Feature* ViewProviderBody::getShownFeature() const
{
    auto body = static_cast<PartDesign::Body*>(getObject());
    auto features = body->Group.getValues();

    for (auto feature : features) {
        if (!feature->isDerivedFrom<PartDesign::Feature>()) {
            continue;
        }

        if (feature->Visibility.getValue()) {
            return static_cast<PartDesign::Feature*>(feature);
        }
    }

    return nullptr;
}

Gui::ViewProvider* ViewProviderBody::getShownViewProvider() const
{
    auto body = static_cast<PartDesign::Body*>(getObject());
    for (auto* feature : body->Group.getValues()) {
        if (feature && feature->Visibility.getValue()
            && PartDesign::Body::isResultFeature(feature)) {
            return Gui::Application::Instance->getViewProvider(feature);
        }
    }

    return nullptr;
}

bool ViewProviderBody::canDropObjects() const
{
    // if the BaseFeature property is marked as hidden or read-only then
    // it's not allowed to modify it.
    auto* body = getObject<PartDesign::Body>();
    if (body->BaseFeature.testStatus(App::Property::Status::Hidden)
        || body->BaseFeature.testStatus(App::Property::Status::ReadOnly)) {
        return false;
    }
    return true;
}

bool ViewProviderBody::canDropObject(App::DocumentObject* obj) const
{
    if (obj->isDerivedFrom<App::VarSet>()) {
        return true;
    }
    else if (obj->isDerivedFrom<App::DatumElement>()) {
        // accept only datums that are not part of a LCS.
        auto* lcs = static_cast<App::DatumElement*>(obj)->getLCS();
        return !lcs;
    }
    else if (obj->isDerivedFrom<App::LocalCoordinateSystem>()) {
        return !obj->isDerivedFrom<App::Origin>();
    }
    else if (obj->isDerivedFrom<PartDesign::SubShapeBinder>()) {
        return true;
    }
    else if (obj->isDerivedFrom<Part::Part2DObject>()) {
        return true;
    }
    else if (!obj->isDerivedFrom<Part::Feature>()) {
        return false;
    }
    else if (PartDesign::Body::findBodyOf(obj)) {
        return false;
    }
    else if (obj->isDerivedFrom(Part::BodyBase::getClassTypeId())) {
        return false;
    }

    App::Part* actPart = PartDesignGui::getActivePart();
    App::Part* partOfBaseFeature = App::Part::getPartOfObject(obj);
    if (partOfBaseFeature && partOfBaseFeature != actPart) {
        return false;
    }

    return true;
}

void ViewProviderBody::dropObject(App::DocumentObject* obj)
{
    auto* body = getObject<PartDesign::Body>();
    if (obj->isDerivedFrom<Part::Part2DObject>() || obj->isDerivedFrom<App::DatumElement>()
        || obj->isDerivedFrom<App::LocalCoordinateSystem>()) {
        body->addObject(obj);
    }
    else if (PartDesign::Body::isAllowed(obj) && PartDesignGui::isFeatureMovable(obj)) {
        std::vector<App::DocumentObject*> move;
        move.push_back(obj);
        std::vector<App::DocumentObject*> deps = PartDesignGui::collectMovableDependencies(move);
        move.insert(std::end(move), std::begin(deps), std::end(deps));

        PartDesign::Body* source = PartDesign::Body::findBodyOf(obj);
        if (source) {
            source->removeObjects(move);
        }
        try {
            body->addObjects(move);
        }
        catch (const Base::Exception& e) {
            e.reportException();
        }
    }
    else if (!body->BaseFeature.getValue()) {
        body->BaseFeature.setValue(obj);
    }

    App::Document* doc = body->getDocument();
    doc->recompute();

    // check if a proxy object has been created for the base feature
    std::vector<App::DocumentObject*> links = body->Group.getValues();
    for (auto it : links) {
        if (it->isDerivedFrom<PartDesign::FeatureBase>()) {
            PartDesign::FeatureBase* base = static_cast<PartDesign::FeatureBase*>(it);
            if (base && base->BaseFeature.getValue() == obj) {
                Gui::Application::Instance->hideViewProvider(obj);
                break;
            }
        }
    }
}

bool ViewProviderBody::canDragObjectToTarget(App::DocumentObject* obj, App::DocumentObject* target) const
{
    if (PartDesign::Body::isAllowed(obj)) {
        return target && target->is<PartDesign::Body>();
    }

    return ViewProviderPart::canDragObjectToTarget(obj, target);
}

void ViewProviderBody::show()
{
    // The Body's scene container owns sketches, datums, and result features.
    // Keep it mounted, then expose exactly the current Tip as the one solid
    // represented by the Body row.
    useChildSceneMode();
    PartGui::ViewProviderPart::show();

    // The regular show path may reject a child whose enclosing component is
    // hidden. The physical container must nevertheless remain mounted so an
    // independently visible sketch or datum can still draw. Normalize the
    // result only at the same stable boundary used by undo/redo and native
    // task completion; show() is also reached synchronously while those
    // owners replay Visibility properties.
    if (!Visibility.getValue()) {
        Gui::ViewProvider::show();
    }
    if (auto* body = getObject<PartDesign::Body>();
        body && body->getDocument()) {
        normalizeResultPresentation(*body->getDocument(), false);
    }
}

void ViewProviderBody::hide()
{
    // Let the regular implementation synchronize the persisted Visibility
    // property, then remount only the scene container. Its result children
    // remain independently addressable. Stable-state normalization hides the
    // result Tip; if this call is part of task teardown or transaction replay,
    // it deliberately waits for that owner to finish before changing any
    // child Visibility property.
    PartGui::ViewProviderPart::hide();
    useChildSceneMode();
    Gui::ViewProvider::show();
    if (auto* body = getObject<PartDesign::Body>();
        body && body->getDocument()) {
        normalizeResultPresentation(*body->getDocument(), false);
    }
}

bool ViewProviderBody::isShow() const
{
    // The physical scene container may be mounted while the solid is hidden.
    // Report the logical state used by the Body eye and visibility commands.
    return Visibility.getValue();
}

void ViewProviderBody::setResultVisibility(bool visible)
{
    auto& presentationState = bodyPresentationStates()[this];
    if (presentationState.adjustingResultVisibility) {
        return;
    }
    Base::FlagToggler<> guard(
        presentationState.adjustingResultVisibility
    );

    auto* body = getObject<PartDesign::Body>();
    if (!body) {
        return;
    }

    App::DocumentObject* tip = body->Tip.getValue();
    for (auto* feature : body->Group.getValues()) {
        if (!feature || !PartDesign::Body::isResultFeature(feature)) {
            continue;
        }
        const bool shouldShow = visible && feature == tip;
        auto* viewProvider = Gui::Application::Instance
            ? Gui::Application::Instance->getViewProvider(feature)
            : nullptr;
        if (viewProvider) {
            // Visibility properties can already have the requested value while
            // the corresponding Coin branch is stale (for example after
            // adoption, restore, or transaction rollback). Reapply the
            // imperative view-provider state every time so normalization is
            // idempotent in both the document and the scene graph.
            if (shouldShow) {
                viewProvider->show();
            }
            else {
                viewProvider->hide();
            }
        }
        else if (feature->Visibility.getValue() != shouldShow) {
            feature->Visibility.setValue(shouldShow);
        }
    }
}
