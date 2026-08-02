// SPDX-License-Identifier: LGPL-2.1-or-later

/**************************************************************************
 *   Copyright (c) 2022 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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

#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <algorithm>
#include <boost/signals2.hpp>
#include <exception>
#include <iterator>
#include <map>
#include <sstream>
#include <string>
#include <utility>
#include <vector>
#include <QByteArray>
#include <QApplication>
#include <QMessageBox>


#include "SketchWorkflow.h"
#include "DlgActiveBody.h"
#include "TaskFeaturePick.h"
#include "Utils.h"
#include "ViewProviderBody.h"
#include "WorkflowManager.h"
#include "ui_DlgReference.h"
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/DatumPlane.h>
#include <Mod/PartDesign/App/ShapeBinder.h>
#include <Mod/Part/App/AttachExtension.h>
#include <Mod/Part/App/Attacher.h>
#include <Mod/Part/App/Part2DObject.h>
#include <Mod/Part/App/TopoShape.h>
#include <Mod/Part/Gui/ModelingSelection.h>
#include <Mod/Sketcher/App/SketchObject.h>
#include <Mod/Sketcher/Gui/ViewProviderSketch.h>

#include <App/Application.h>
#include <App/Document.h>
#include <App/Link.h>
#include <App/Origin.h>
#include <App/Datums.h>
#include <App/Part.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/ViewParams.h>
#include <Gui/ViewProviderPlane.h>
#include <Gui/Selection/SelectionFilter.h>

using namespace PartDesignGui;

namespace
{
struct ObjectIdentity
{
    std::string documentName;
    std::string objectName;
    long objectId {-1};
};

ObjectIdentity identityOf(const App::DocumentObject* object)
{
    if (!object || !object->isAttachedToDocument()) {
        return {};
    }
    return {
        object->getDocument()->getName(),
        object->getNameInDocument(),
        object->getID(),
    };
}

App::DocumentObject* resolveObject(const ObjectIdentity& identity)
{
    if (identity.documentName.empty() || identity.objectName.empty()
        || identity.objectId < 0) {
        return nullptr;
    }
    App::Document* document = nullptr;
    try {
        document = App::GetApplication().getDocument(
            identity.documentName.c_str()
        );
    }
    catch (...) {
    }
    auto* object = document
        ? document->getObject(identity.objectName.c_str())
        : nullptr;
    return object && object->getID() == identity.objectId
        ? object
        : nullptr;
}

struct LinkedBodyPlacementIntent
{
    bool requested {false};
    ObjectIdentity body;
    ObjectIdentity occurrence;
};

LinkedBodyPlacementIntent linkedBodyPlacementIntent(
    PartDesign::Body* body,
    App::DocumentObject* occurrence
)
{
    return {
        occurrence != nullptr,
        identityOf(body),
        identityOf(occurrence),
    };
}

bool applyLinkedBodyPlacement(
    const LinkedBodyPlacementIntent& intent,
    int transactionId
)
{
    if (!intent.requested) {
        return true;
    }
    if (transactionId == App::NullTransaction
        || !App::GetApplication().transactionIsActive(transactionId)) {
        return false;
    }

    auto* body = freecad_cast<PartDesign::Body*>(
        resolveObject(intent.body)
    );
    auto* occurrence = dynamic_cast<App::Link*>(
        resolveObject(intent.occurrence)
    );
    if (!body || !occurrence) {
        return false;
    }

    auto* bodyDocument = body->getDocument();
    const int bodyTransactionId = bodyDocument->getBookedTransactionID();
    if (bodyTransactionId != App::NullTransaction
        && bodyTransactionId != transactionId) {
        return false;
    }
    if (!bodyDocument->hasPendingTransaction()
        && bodyDocument->getUndoMode() == 0) {
        return false;
    }

    try {
        // A linked Body definition can live in another document. Enlist that
        // document in the exact sketch transaction before changing it.
        if (bodyTransactionId == App::NullTransaction
            && bodyDocument->setActiveTransaction(
                   App::TransactionName {
                       .name = App::GetApplication().getTransactionName(
                           transactionId
                       ),
                       .temporary = false,
                   },
                   transactionId
               ) != transactionId) {
            return false;
        }

        const auto occurrencePlacement =
            occurrence->Placement.getValue();
        if (body->Placement.getValue() == occurrencePlacement) {
            return bodyDocument->getBookedTransactionID()
                    == transactionId
                && App::GetApplication().transactionIsActive(
                    transactionId
                );
        }

        body->Placement.setValue(occurrencePlacement);
        return bodyDocument->hasPendingTransaction()
            && bodyDocument->getBookedTransactionID() == transactionId
            && App::GetApplication().transactionIsActive(transactionId);
    }
    catch (...) {
        return false;
    }
}

struct RejectException
{
};

struct WrongSelectionException
{
};

struct WrongSupportException
{
};

struct SupportNotPlanarException
{
};

struct MissingPlanesException
{
};

Sketcher::SketchObject* createSketchExact(
    PartDesign::Body* body,
    const std::string& featureName
)
{
    if (!body || !body->isAttachedToDocument()
        || !body->getNameInDocument() || featureName.empty()) {
        return nullptr;
    }

    auto* document = body->getDocument();
    if (!document || document->getObject(body->getNameInDocument()) != body
        || document->getObjectByID(body->getID()) != body) {
        return nullptr;
    }

    try {
        std::ostringstream factory;
        factory << Gui::Command::getObjectCmd(body)
                << ".newObject('Sketcher::SketchObject','"
                << featureName << "')";
        auto* sketch = freecad_cast<Sketcher::SketchObject*>(
            Gui::Command::runDocumentObjectCommand(
                Gui::Command::Doc,
                *document,
                QByteArray(factory.str().c_str()),
                Sketcher::SketchObject::getClassTypeId()
            )
        );
        if (!sketch || sketch->getDocument() != document
            || !sketch->getNameInDocument()
            || document->getObject(sketch->getNameInDocument()) != sketch
            || document->getObjectByID(sketch->getID()) != sketch
            || !body->hasObject(sketch)) {
            Base::Console().error(
                "The Part Design sketch factory did not return the exact "
                "Sketch owned by its Body\n"
            );
            return nullptr;
        }
        return sketch;
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not create the exact Part Design Sketch: %s\n",
            error.what()
        );
    }
    catch (const std::exception& error) {
        Base::Console().error(
            "Could not create the exact Part Design Sketch: %s\n",
            error.what()
        );
    }
    catch (...) {
        Base::Console().error(
            "Could not create the exact Part Design Sketch\n"
        );
    }
    return nullptr;
}

class SupportFaceValidator
{
public:
    explicit SupportFaceValidator(Gui::SelectionObject faceSelection)
        : faceSelection(faceSelection)
    {}

    void handleSelectedBody(PartDesign::Body* activeBody)
    {
        const auto original = faceSelection.getObject();
        auto resolved =
            PartGui::resolveModelingSelectionForBody(faceSelection, activeBody);
        if (resolved.getObject() && resolved.getObject() != original
            && resolved.getSubNames().size() == 1) {
            faceSelection = std::move(resolved);
            setThroughModeOfBody(activeBody);
        }
    }

    void throwIfInvalid()
    {
        App::DocumentObject* object = faceSelection.getObject();
        std::vector<std::string> elements = faceSelection.getSubNames();

        Part::Feature* partobject = dynamic_cast<Part::Feature*>(object);
        if (!partobject) {
            throw WrongSelectionException();
        }

        if (elements.size() != 1) {
            throw WrongSelectionException();
        }

        // get the selected sub shape (a Face)
        const Part::TopoShape& shape = partobject->Shape.getValue();
        Part::TopoShape subshape(shape.getSubShape(elements[0].c_str()));
        if (subshape.isNull()) {
            throw WrongSupportException();
        }

        if (!subshape.isPlanar(Attacher::AttachEnginePlane::planarPrecision())) {
            throw SupportNotPlanarException();
        }
    }

    std::string getSupport() const
    {
        return faceSelection.getAsPropertyLinkSubString();
    }

    App::DocumentObject* getObject() const
    {
        return faceSelection.getObject();
    }

private:
    void setThroughModeOfBody(PartDesign::Body* activeBody)
    {
        // automatically switch to 'Through' mode
        PartDesignGui::ViewProviderBody* vpBody = dynamic_cast<PartDesignGui::ViewProviderBody*>(
            Gui::Application::Instance->getViewProvider(activeBody)
        );
        if (vpBody) {
            vpBody->DisplayModeBody.setValue("Through");
        }
    }

private:
    mutable Gui::SelectionObject faceSelection;
};

class SupportPlaneValidator
{
public:
    explicit SupportPlaneValidator(Gui::SelectionObject faceSelection)
        : faceSelection(faceSelection)
    {}

    std::string getSupport() const
    {
        return faceSelection.getAsPropertyLinkSubString();
    }

    App::DocumentObject* getObject() const
    {
        return faceSelection.getObject();
    }

private:
    mutable Gui::SelectionObject faceSelection;
};

class SketchPreselection
{
public:
    SketchPreselection(
        Gui::Document* guidocument,
        PartDesign::Body* activeBody,
        std::tuple<Gui::SelectionFilter, Gui::SelectionFilter, Gui::SelectionFilter> filter,
        LinkedBodyPlacementIntent linkedBodyPlacement
    )
        : guidocument(guidocument)
        , activeBody(activeBody)
        , faceFilter(std::get<0>(filter))
        , planeFilter(std::get<1>(filter))
        , sketchFilter(std::get<2>(filter))
        , linkedBodyPlacement(std::move(linkedBodyPlacement))
    {}

    bool matches()
    {
        return faceFilter.match() || planeFilter.match() || sketchFilter.match();
    }

    // True only when a single planar face or datum plane is selected (not a sketch).
    // Used for the fast-path that skips the attachment dialog.
    bool isSingleFaceOrPlane()
    {
        return (faceFilter.match() || planeFilter.match()) && !sketchFilter.match();
    }

    std::string getSupport() const
    {
        return supportString;
    }

    void createSupport()
    {
        createBodyOrThrow();

        // get the selected object
        App::DocumentObject* selectedObject {};

        if (faceFilter.match()) {
            Gui::SelectionObject faceSelObject = faceFilter.Result[0][0];
            SupportFaceValidator validator {faceSelObject};
            validator.handleSelectedBody(activeBody);
            validator.throwIfInvalid();

            selectedObject = validator.getObject();
            supportString = validator.getSupport();
        }
        else if (planeFilter.match()) {
            SupportPlaneValidator validator(
                PartGui::resolveModelingSelectionForBody(
                    planeFilter.Result[0][0],
                    activeBody
                )
            );
            selectedObject = validator.getObject();
            supportString = validator.getSupport();
        }
        else {
            // For a sketch, the support is the object itself with no sub-element.
            Gui::SelectionObject sketchSelObject =
                PartGui::resolveModelingSelectionForBody(
                    sketchFilter.Result[0][0],
                    activeBody
                );
            selectedObject = sketchSelObject.getObject();
            supportString = sketchSelObject.getAsPropertyLinkSubString();
        }

        handleIfSupportOutOfBody(selectedObject);
    }

    void createSketchOnSupport(const std::string& supportString)
    {
        // create Sketch on Face or Plane
        App::Document* appdocument = guidocument->getDocument();
        std::string FeatName = appdocument->getUniqueObjectName("Sketch");

        const int transactionId =
            guidocument->openCommand(
                QT_TRANSLATE_NOOP("Command", "Sketch on Face")
            );
        if (!applyLinkedBodyPlacement(
                linkedBodyPlacement,
                transactionId
            )) {
            guidocument->abortCommand();
            throw RejectException();
        }
        auto* Feat = createSketchExact(activeBody, FeatName);
        if (!Feat) {
            guidocument->abortCommand();
            throw RejectException();
        }
        FCMD_OBJ_CMD(Feat, "AttachmentSupport = " << supportString);
        if (sketchFilter.match()) {
            FCMD_OBJ_CMD(
                Feat,
                "MapMode = '" << Attacher::AttachEngine::getModeName(Attacher::mmObjectXY) << "'"
            );
        }
        else {  // For Face or Plane
            FCMD_OBJ_CMD(
                Feat,
                "MapMode = '" << Attacher::AttachEngine::getModeName(Attacher::mmFlatFace) << "'"
            );
        }
        Gui::Command::updateActive();
        PartDesignGui::setEdit(Feat, activeBody);
    }

private:
    void createBodyOrThrow()
    {
        if (!activeBody) {
            activeBody = PartDesignGui::getBody(/* messageIfNot = */ true);
            if (activeBody) {
                tryAddNewBodyToActivePart();
            }
            else {
                throw RejectException();
            }
        }
    }

    void tryAddNewBodyToActivePart()
    {
        App::Part* activePart = PartDesignGui::getActivePart();
        if (activePart) {
            activePart->addObject(activeBody);
        }
    }

    void handleIfSupportOutOfBody(App::DocumentObject* selectedObject)
    {
        if (!activeBody->hasObject(selectedObject)) {
            if (!selectedObject->isDerivedFrom(App::Plane::getClassTypeId())) {
                // TODO check here if the plane associated with right part/body (2015-09-01, Fat-Zer)

                // check the prerequisites for the selected objects
                // the user has to decide which option we should take if external references are used
                //  TODO share this with UnifiedDatumCommand() (2015-10-20, Fat-Zer)
                QDialog dia(Gui::getMainWindow());
                PartDesignGui::Ui_DlgReference dlg;
                dlg.setupUi(&dia);
                dia.setModal(true);
                int result = dia.exec();
                if (result == QDialog::Rejected) {
                    throw RejectException();
                }

                if (!dlg.radioXRef->isChecked()) {
                    guidocument->openCommand(QT_TRANSLATE_NOOP("Command", "Make copy"));
                    auto copy = makeCopy(selectedObject, dlg.radioIndependent->isChecked());
                    if (!copy) {
                        guidocument->abortCommand();
                        QMessageBox::warning(
                            Gui::getMainWindow(),
                            QObject::tr("Copy failed"),
                            QObject::tr(
                                "The selected sketch support could not be "
                                "copied into this body."
                            )
                        );
                        throw RejectException();
                    }
                    supportString = supportFromCopy(copy);
                    guidocument->commitCommand();
                }
            }
        }
    }

    App::DocumentObject* makeCopy(App::DocumentObject* selectedObject, bool independent)
    {
        std::string sub;
        if (faceFilter.match()) {
            sub = faceFilter.Result[0][0].getSubNames()[0];
        }
        auto copy = PartDesignGui::TaskFeaturePick::makeCopy(
            selectedObject,
            sub,
            independent,
            activeBody ? activeBody->getDocument() : guidocument->getDocument()
        );

        if (copy) {
            addToBodyOrPart(copy);
        }

        return copy;
    }

    std::string supportFromCopy(App::DocumentObject* copy)
    {
        std::string supportString;
        if (planeFilter.match()) {
            supportString = Gui::Command::getObjectCmd(copy, "(", ",'')");
        }
        else {
            // it is ensured that only a single face is selected, hence it must always be Face1 of
            // the shapebinder
            supportString = Gui::Command::getObjectCmd(copy, "(", ",'Face1')");
        }
        return supportString;
    }

    void addToBodyOrPart(App::DocumentObject* object)
    {
        auto activePart = PartDesignGui::getPartFor(activeBody, false);
        if (activeBody) {
            activeBody->addObject(object);
        }
        else if (activePart) {
            activePart->addObject(object);
        }
    }

private:
    Gui::Document* guidocument;
    PartDesign::Body* activeBody;
    Gui::SelectionFilter faceFilter;
    Gui::SelectionFilter planeFilter;
    Gui::SelectionFilter sketchFilter;
    LinkedBodyPlacementIntent linkedBodyPlacement;
    std::string supportString;
};

class PlaneFinder
{
public:
    PlaneFinder(App::Document* appdocument, PartDesign::Body* activeBody)
        : appdocument(appdocument)
        , activeBody(activeBody)
    {}

    std::vector<App::DocumentObject*> getPlanes() const
    {
        return planes;
    }

    std::vector<PartDesignGui::TaskFeaturePick::featureStatus> getStatus() const
    {
        return status;
    }

    unsigned countValidPlanes() const
    {
        return validPlaneCount;
    }

    void findBasePlanes()
    {
        try {
            tryFindBasePlanes();
        }
        catch (const Base::Exception& ex) {
            Base::Console().error("%s\n", ex.what());
        }
    }

    void findDatumPlanes()
    {
        App::GeoFeatureGroupExtension* geoGroup = getGroupExtensionOfBody();
        const std::vector<Base::Type> types
            = {PartDesign::Plane::getClassTypeId(), App::Plane::getClassTypeId()};
        auto datumPlanes = appdocument->getObjectsOfType(types);

        for (auto plane : datumPlanes) {
            if (std::find(planes.begin(), planes.end(), plane) != planes.end()) {
                continue;  // Skip if already in planes (for base planes)
            }

            planes.push_back(plane);
            // Check whether this plane belongs to the active body
            if (activeBody->hasObject(plane, true)) {
                if (!activeBody->isAfterInsertPoint(plane)) {
                    validPlaneCount++;
                    status.push_back(PartDesignGui::TaskFeaturePick::validFeature);
                }
                else {
                    status.push_back(PartDesignGui::TaskFeaturePick::afterTip);
                }
            }
            else {
                PartDesign::Body* planeBody = PartDesign::Body::findBodyOf(plane);
                if (planeBody) {
                    if ((geoGroup && geoGroup->hasObject(planeBody, true))
                        || !App::GeoFeatureGroupExtension::getGroupOfObject(planeBody)) {
                        status.push_back(PartDesignGui::TaskFeaturePick::otherBody);
                    }
                    else {
                        status.push_back(PartDesignGui::TaskFeaturePick::otherPart);
                    }
                }
                else {
                    if ((geoGroup && geoGroup->hasObject(plane, true))
                        || App::GeoFeatureGroupExtension::getGroupOfObject(plane)) {
                        status.push_back(PartDesignGui::TaskFeaturePick::otherPart);
                    }
                    else {
                        status.push_back(PartDesignGui::TaskFeaturePick::notInBody);
                    }
                }
            }
        }
    }

    void findShapeBinderPlanes()
    {

        // Collect also shape binders consisting of a single planar face
        auto shapeBinders(appdocument->getObjectsOfType(PartDesign::ShapeBinder::getClassTypeId()));
        auto binders(appdocument->getObjectsOfType(PartDesign::SubShapeBinder::getClassTypeId()));
        shapeBinders.insert(shapeBinders.end(), binders.begin(), binders.end());
        for (auto binder : shapeBinders) {
            // Check whether this plane belongs to the active body
            if (activeBody->hasObject(binder)) {
                Part::TopoShape shape = static_cast<Part::Feature*>(binder)->Shape.getShape();
                if (shape.isPlanar()) {
                    if (!activeBody->isAfterInsertPoint(binder)) {
                        validPlaneCount++;
                        planes.push_back(binder);
                        status.push_back(PartDesignGui::TaskFeaturePick::validFeature);
                    }
                }
            }
        }
    }

private:
    void tryFindBasePlanes()
    {
        auto* origin = activeBody->getOrigin();
        for (auto plane : origin->planes()) {
            planes.push_back(plane);
            status.push_back(PartDesignGui::TaskFeaturePick::basePlane);
            validPlaneCount++;
        }
    }

    App::GeoFeatureGroupExtension* getGroupExtensionOfBody() const
    {
        App::GeoFeatureGroupExtension* geoGroup {nullptr};
        if (activeBody) {
            auto group(App::GeoFeatureGroupExtension::getGroupOfObject(activeBody));
            if (group) {
                geoGroup = group->getExtensionByType<App::GeoFeatureGroupExtension>();
            }
        }

        return geoGroup;
    }

private:
    App::Document* appdocument;
    PartDesign::Body* activeBody;
    unsigned validPlaneCount = 0;
    std::vector<App::DocumentObject*> planes;
    std::vector<PartDesignGui::TaskFeaturePick::featureStatus> status;
};

class SketchRequestSelection
{
public:
    SketchRequestSelection(
        Gui::Document* guidocument,
        PartDesign::Body* activeBody,
        LinkedBodyPlacementIntent linkedBodyPlacement
    )
        : guidocument(guidocument)
        , activeBody(activeBody)
        , linkedBodyPlacement(std::move(linkedBodyPlacement))
    {}

    void findSupport()
    {
        try {
            // Start command early, so undo will undo any Body creation
            const int transactionId =
                guidocument->openCommand(
                    QT_TRANSLATE_NOOP("Command", "New Sketch")
                );
            if (!applyLinkedBodyPlacement(
                    linkedBodyPlacement,
                    transactionId
                )) {
                throw RejectException();
            }
            tryFindSupport();
        }
        catch (const RejectException&) {
            guidocument->abortCommand();
            throw;
        }
        catch (const MissingPlanesException&) {
            guidocument->abortCommand();
            throw;
        }
    }

private:
    void tryFindSupport()
    {
        createBodyOrThrow();

        createSketchAndShowAttachment();
    }

    void createBodyOrThrow()
    {
        if (!activeBody) {
            App::Document* appdocument = guidocument->getDocument();
            activeBody = PartDesignGui::makeBody(appdocument);
            if (activeBody) {
                tryAddNewBodyToActivePart();
            }
            else {
                throw RejectException();
            }
        }
    }

    void tryAddNewBodyToActivePart()
    {
        App::Part* activePart = PartDesignGui::getActivePart();
        if (activePart) {
            activePart->addObject(activeBody);
        }
    }

    void setOriginTemporaryVisibility()
    {
        auto* origin = activeBody->getOrigin();
        auto* vpo = dynamic_cast<Gui::ViewProviderCoordinateSystem*>(
            Gui::Application::Instance->getViewProvider(origin)
        );
        if (vpo) {
            vpo->setTemporaryVisibility(Gui::DatumElement::Planes | Gui::DatumElement::Axes);
            vpo->setPlaneLabelVisibility(true);
        }
    }

    void createSketchAndShowAttachment()
    {
        setOriginTemporaryVisibility();

        // Capture selection before clearing it to pre-populate the attachment dialog.
        // This mirrors UnifiedDatumCommand: use attacher to find the best fit mode.
        App::PropertyLinkSubList support;
        Gui::Selection().getAsPropertyLinkSubList(support);
        PartGui::resolveModelingReferencesForBody(support, activeBody);

        // Don't pre-populate when the selection contains sketches. A sketch selected
        // from prior work should not automatically become the attachment reference —
        // the user can choose a face or plane in the dialog.
        bool hasSketch = std::ranges::any_of(support.getValues(), [](App::DocumentObject* obj) {
            return obj && obj->isDerivedFrom<Part::Part2DObject>();
        });

        // Create sketch
        App::Document* doc = activeBody->getDocument();
        std::string FeatName = doc->getUniqueObjectName("Sketch");
        auto* sketch = createSketchExact(activeBody, FeatName);
        if (!sketch) {
            throw RejectException();
        }

        if (!hasSketch && support.getSize() > 0) {
            if (auto* pcAttach = sketch->getExtensionByType<Part::AttachExtension>()) {
                pcAttach->attacher().setReferences(support);
                Attacher::SuggestResult sugr;
                pcAttach->attacher().suggestMapModes(sugr);
                if (sugr.message == Attacher::SuggestResult::srOK) {
                    FCMD_OBJ_CMD(sketch, "AttachmentSupport = " << support.getPyReprString());
                    FCMD_OBJ_CMD(
                        sketch,
                        "MapMode = '" << Attacher::AttachEngine::getModeName(sugr.bestFitMode) << "'"
                    );
                    Gui::Command::updateDocument(doc);
                }
            }
        }

        const ObjectIdentity bodyIdentity = identityOf(activeBody);
        const ObjectIdentity sketchIdentity = identityOf(sketch);
        auto onAccept = [bodyIdentity, sketchIdentity]() {
            auto* partDesignBody = freecad_cast<PartDesign::Body*>(
                resolveObject(bodyIdentity)
            );
            auto* currentSketch = resolveObject(sketchIdentity);
            resetOriginVisibility(partDesignBody);
            if (!partDesignBody || !currentSketch
                || currentSketch->getDocument()
                    != partDesignBody->getDocument()) {
                return;
            }
            Gui::Selection().clearSelection(
                partDesignBody->getDocument()->getName()
            );
            PartDesignGui::setEdit(currentSketch, partDesignBody);
        };
        auto onReject = [bodyIdentity]() {
            auto* partDesignBody = freecad_cast<PartDesign::Body*>(
                resolveObject(bodyIdentity)
            );
            resetOriginVisibility(partDesignBody);
        };

        Gui::Selection().clearSelection(doc->getName());

        // Open attachment dialog
        auto* vps = dynamic_cast<SketcherGui::ViewProviderSketch*>(
            Gui::Application::Instance->getViewProvider(sketch)
        );
        if (!vps) {
            resetOriginVisibility(activeBody);
            throw RejectException();
        }
        vps->showAttachmentEditor(onAccept, onReject);
    }

    static void resetOriginVisibility(PartDesign::Body* partDesignBody)
    {
        if (!partDesignBody || !partDesignBody->isAttachedToDocument()) {
            return;
        }
        auto* origin = partDesignBody->getOrigin();
        auto* vpo = dynamic_cast<Gui::ViewProviderCoordinateSystem*>(
            Gui::Application::Instance->getViewProvider(origin)
        );
        if (vpo) {
            vpo->resetTemporaryVisibility();
            vpo->resetTemporarySize();
            vpo->setPlaneLabelVisibility(false);
        }
    }

    void findAndSelectPlane()
    {
        App::Document* appdocument = guidocument->getDocument();
        PlaneFinder planeFinder {appdocument, activeBody};

        planeFinder.findBasePlanes();
        planeFinder.findDatumPlanes();
        planeFinder.findShapeBinderPlanes();

        std::vector<App::DocumentObject*> planes = planeFinder.getPlanes();
        std::vector<PartDesignGui::TaskFeaturePick::featureStatus> status = planeFinder.getStatus();
        unsigned validPlaneCount = planeFinder.countValidPlanes();

        for (auto& plane : planes) {
            auto* planeViewProvider
                = Gui::Application::Instance->getViewProvider<Gui::ViewProviderPlane>(plane);

            // skip updating planes from coordinate systems
            if (!planeViewProvider || !planeViewProvider->getRole().empty()) {
                continue;
            }

            planeViewProvider->setLabelVisibility(true);
            planeViewProvider->setTemporaryScale(
                Gui::ViewParams::instance()->getDatumTemporaryScaleFactor()
            );
        }

        //
        // Lambda definitions
        //
        App::Document* documentOfBody = appdocument;
        PartDesign::Body* partDesignBody = activeBody;

        std::vector<ObjectIdentity> planeIdentities;
        planeIdentities.reserve(planes.size());
        std::ranges::transform(
            planes,
            std::back_inserter(planeIdentities),
            identityOf
        );
        auto restorePlaneVisibility = [planeIdentities]() {
            for (const auto& identity : planeIdentities) {
                auto* plane = resolveObject(identity);
                auto* planeViewProvider
                    = Gui::Application::Instance->getViewProvider<Gui::ViewProviderPlane>(plane);
                if (!planeViewProvider) {
                    continue;
                }

                planeViewProvider->resetTemporarySize();
                planeViewProvider->setLabelVisibility(false);
            }
        };

        // Determines if user made a valid selection in dialog
        auto acceptFunction =
            [restorePlaneVisibility](const std::vector<App::DocumentObject*>& features) -> bool {
            restorePlaneVisibility();
            return !features.empty();
        };

        // Called by dialog when user hits "OK" and accepter returns true
        const ObjectIdentity bodyIdentity = identityOf(partDesignBody);
        auto processFunction = [bodyIdentity](
                                   const std::vector<App::DocumentObject*>& features
                               ) {
            auto* currentBody = freecad_cast<PartDesign::Body*>(
                resolveObject(bodyIdentity)
            );
            if (!currentBody) {
                return;
            }
            SketchRequestSelection::createSketch(
                currentBody->getDocument(),
                currentBody,
                features
            );
        };

        // Called by dialog for "Cancel", or "OK" if accepter returns false
        std::string docname = documentOfBody->getName();
        auto rejectFunction = [docname, restorePlaneVisibility]() {
            restorePlaneVisibility();
            Gui::Document* document = Gui::Application::Instance->getDocument(docname.c_str());
            if (document) {
                document->abortCommand();
            }
        };

        //
        // End of lambda definitions
        //

        if (validPlaneCount == 0) {
            throw MissingPlanesException();
        }
        else if (validPlaneCount == 1) {
            for (std::size_t index = 0; index < status.size(); ++index) {
                if (status[index]
                        == PartDesignGui::TaskFeaturePick::validFeature
                    || status[index]
                        == PartDesignGui::TaskFeaturePick::basePlane) {
                    processFunction({planes[index]});
                    break;
                }
            }
        }
        else if (validPlaneCount > 1) {
            checkForShownDialog();
            Gui::Selection().clearSelection(
                documentOfBody->getName()
            );

            // Show dialog and let user pick plane
            Gui::Control().showDialog(new PartDesignGui::TaskDlgFeaturePick(
                planes,
                status,
                acceptFunction,
                processFunction,
                true,
                rejectFunction,
                partDesignBody
            ), documentOfBody);
        }
    }

    void checkForShownDialog()
    {
        App::Document* appdocument = guidocument->getDocument();
        Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog(appdocument);
        PartDesignGui::TaskDlgFeaturePick* pickDlg
            = qobject_cast<PartDesignGui::TaskDlgFeaturePick*>(dlg);
        if (dlg && !pickDlg) {
            QMessageBox msgBox(Gui::getMainWindow());
            msgBox.setText(QObject::tr("A dialog is already open in the task panel"));
            msgBox.setInformativeText(QObject::tr("Close this dialog?"));
            msgBox.setStandardButtons(QMessageBox::Yes | QMessageBox::No);
            msgBox.setDefaultButton(QMessageBox::Yes);
            int ret = msgBox.exec();
            if (ret == QMessageBox::Yes) {
                Gui::Control().closeDialog(appdocument);
            }
            else {
                throw RejectException();
            }
        }

        if (dlg) {
            Gui::Control().closeDialog(appdocument);
        }
    }

    static void createSketch(
        App::Document* documentOfBody,
        PartDesign::Body* partDesignBody,
        const std::vector<App::DocumentObject*>& features
    )
    {
        // may happen when the user switched to an empty document while the
        // dialog is open
        if (features.empty()) {
            return;
        }
        std::string FeatName = documentOfBody->getUniqueObjectName("Sketch");
        auto* support = features.front();
        if (!support || !support->isAttachedToDocument()) {
            return;
        }
        auto* plane = freecad_cast<App::Plane*>(support);
        auto* lcs = plane ? plane->getLCS() : nullptr;

        std::string supportString;
        if (plane && lcs) {
            supportString = Gui::Command::getObjectCmd(lcs, "(") + ",['"
                + plane->getNameInDocument() + "'])";
        }
        else if (plane) {
            supportString = Gui::Command::getObjectCmd(plane, "(", ",[''])");
        }
        else {
            // Plane-shaped binders expose their single planar face as the
            // attachment support.
            supportString =
                Gui::Command::getObjectCmd(support, "(", ",['Face1'])");
        }

        App::Document* doc = partDesignBody->getDocument();
        bool openedTransaction = false;
        if (!doc->hasPendingTransaction()) {
            doc->openTransaction(QT_TRANSLATE_NOOP("Command", "New Sketch"));
            openedTransaction = true;
        }

        auto* Feat = createSketchExact(partDesignBody, FeatName);
        if (!Feat) {
            if (openedTransaction) {
                doc->abortTransaction();
            }
            return;
        }
        FCMD_OBJ_CMD(Feat, "AttachmentSupport = " << supportString);
        FCMD_OBJ_CMD(
            Feat,
            "MapMode = '" << Attacher::AttachEngine::getModeName(Attacher::mmFlatFace) << "'"
        );
        // Make sure the AttachmentSupport's Placement property is updated in
        // the document that owns the new sketch, even if another tab became
        // active while the plane picker was open.
        Gui::Command::updateDocument(doc);
        PartDesignGui::setEdit(Feat, partDesignBody);
    }

private:
    Gui::Document* guidocument;
    PartDesign::Body* activeBody;
    LinkedBodyPlacementIntent linkedBodyPlacement;
};

}  // namespace

SketchWorkflow::SketchWorkflow(Gui::Document* document)
    : guidocument(document)
{
    appdocument = guidocument->getDocument();
}

void SketchWorkflow::createSketch()
{
    try {
        tryCreateSketch();
    }
    catch (const RejectException&) {
    }
    catch (const WrongSelectionException&) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Several sub-elements selected"),
            QObject::tr("Select a single face as support for a sketch!")
        );
    }
    catch (const WrongSupportException&) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("No support face selected"),
            QObject::tr("Select a face as support for a sketch!")
        );
    }
    catch (const SupportNotPlanarException&) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("No planar support"),
            QObject::tr("Need a planar face as support for a sketch!")
        );
    }
    catch (const MissingPlanesException&) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("No valid planes in this document"),
            QObject::tr("Create a plane first or select a face to sketch on")
        );
    }
}

void SketchWorkflow::tryCreateSketch()
{
    auto result = shouldCreateBody();
    auto shouldMakeBody = std::get<0>(result);
    activeBody = std::get<1>(result);
    if (shouldAbort(shouldMakeBody)) {
        return;
    }

    bool useAttachment = App::GetApplication()
                             .GetParameterGroupByPath(
                                 "User parameter:BaseApp/Preferences/Mod/PartDesign"
                             )
                             ->GetBool("NewSketchUseAttachmentDialog", false);

    bool shiftHeld = QApplication::queryKeyboardModifiers() & Qt::ShiftModifier;

    auto filters = getFilters();
    const auto linkedBodyPlacement =
        linkedBodyPlacementIntent(activeBody, linkedBodyOccurrence);
    SketchPreselection sketchOnFace {
        guidocument,
        activeBody,
        filters,
        linkedBodyPlacement,
    };

    // Fast path: single face or datum plane, preference off, Shift not held.
    // If the face turns out to be non-planar or otherwise invalid, fall through
    // to the attachment dialog instead of showing an error.
    // A selected sketch, multiple references, no selection, Shift, or preference on
    // all go through the attachment dialog.
    if (!useAttachment && !shiftHeld && sketchOnFace.isSingleFaceOrPlane()) {
        try {
            sketchOnFace.createSupport();
            sketchOnFace.createSketchOnSupport(sketchOnFace.getSupport());
            return;
        }
        catch (const WrongSupportException&) {
            // Fall through to attachment dialog
        }
        catch (const WrongSelectionException&) {
            // Fall through to attachment dialog
        }
        catch (const SupportNotPlanarException&) {
            // Fall through to attachment dialog
        }
    }

    SketchRequestSelection requestSelection {
        guidocument,
        activeBody,
        linkedBodyPlacement,
    };
    requestSelection.findSupport();
}

std::tuple<bool, PartDesign::Body*> SketchWorkflow::shouldCreateBody()
{
    auto shouldMakeBody {false};
    linkedBodyOccurrence = nullptr;

    // We need either an active Body, or for there to be no Body
    // objects (in which case, just make one) to make a new sketch.
    // If we are inside a link, we need to use its placement.
    App::DocumentObject* topParent = nullptr;
    PartDesign::Body* pdBody
        = PartDesignGui::getBody(/* messageIfNot = */ false, true, true, &topParent);
    if (pdBody && topParent && topParent->isLink()) {
        // Capture only. Applying the occurrence placement here would put it
        // outside the sketch command and make Cancel unable to roll it back.
        linkedBodyOccurrence = topParent;
    }
    if (!pdBody) {
        if (appdocument->countObjectsOfType<PartDesign::Body>() == 0) {
            shouldMakeBody = true;
        }
        else {
            PartDesignGui::DlgActiveBody dia(Gui::getMainWindow(), appdocument);
            if (dia.exec() == QDialog::Accepted) {
                pdBody = dia.getActiveBody();
            }
        }
    }

    return std::make_tuple(shouldMakeBody, pdBody);
}

bool SketchWorkflow::shouldAbort(bool shouldMakeBody) const
{
    return !shouldMakeBody && !activeBody;
}

std::tuple<Gui::SelectionFilter, Gui::SelectionFilter, Gui::SelectionFilter> SketchWorkflow::getFilters() const
{
    // Hint:
    // The behaviour of this command has changed with respect to a selected sketch:
    // It doesn't try any more to edit a selected sketch but always tries to create
    // a new sketch.
    // See https://forum.freecad.org/viewtopic.php?f=3&t=44070

    Gui::SelectionFilter FaceFilter("SELECT Part::Feature SUBELEMENT Face COUNT 1");
    Gui::SelectionFilter PlaneFilter("SELECT App::Plane COUNT 1", activeBody);
    Gui::SelectionFilter PlaneFilter2("SELECT PartDesign::Plane COUNT 1", activeBody);
    Gui::SelectionFilter SketchFilter("SELECT Part::Part2DObject COUNT 1", activeBody);

    if (PlaneFilter2.match()) {
        PlaneFilter = PlaneFilter2;
    }

    return std::make_tuple(FaceFilter, PlaneFilter, SketchFilter);
}
