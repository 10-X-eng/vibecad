// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2008 Jürgen Riegel <juergen.riegel@web.de>              *
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


#include <BRep_Tool.hxx>
#include <BRepAdaptor_Surface.hxx>
#include <exception>
#include <GeomLib_IsPlanarSurface.hxx>
#include <iterator>
#include <list>
#include <QByteArray>
#include <QMessageBox>
#include <string_view>
#include <type_traits>
#include <TopExp_Explorer.hxx>
#include <TopLoc_Location.hxx>
#include <TopoDS.hxx>
#include <TopoDS_Face.hxx>
#include <utility>


#include <App/Application.h>
#include <App/DocumentTimeline.h>
#include <App/Origin.h>
#include <App/GeoFeature.h>
#include <App/GeoFeatureGroupExtension.h>
#include <App/GroupExtension.h>
#include <App/Link.h>
#include <App/Part.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Base/Placement.h>
#include <Base/Tools.h>
#include <Base/Uuid.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/MDIView.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Selection/SelectionObject.h>
#include <Gui/ViewProviderLink.h>
#include <Mod/Part/App/Part2DObject.h>
#include <Mod/Part/Gui/ModelingSelection.h>
#include <Mod/Sketcher/App/SketchObject.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/FeatureAddSub.h>
#include <Mod/PartDesign/App/FeatureBase.h>
#include <Mod/PartDesign/App/FeatureBoolean.h>
#include <Mod/PartDesign/App/FeatureGroove.h>
#include <Mod/PartDesign/App/FeatureMultiTransform.h>
#include <Mod/PartDesign/App/FeatureRevolution.h>
#include <Mod/PartDesign/App/FeatureTransformed.h>
#include <Mod/PartDesign/App/DatumLine.h>
#include <Mod/PartDesign/App/DatumPlane.h>
#include <Mod/PartDesign/App/DatumPoint.h>
#include <Mod/PartDesign/App/DesignFeature.h>
#include <Mod/PartDesign/App/DesignModel.h>
#include <Mod/PartDesign/App/FeatureDressUp.h>
#include <Mod/PartDesign/App/ShapeBinder.h>

#include "DlgActiveBody.h"
#include "ReferenceSelection.h"
#include "SketchWorkflow.h"
#include "TaskFeaturePick.h"
#include "Utils.h"
#include "WorkflowManager.h"
#include "ViewProvider.h"
#include "ViewProviderBody.h"


// TODO Remove this header after fixing code so it won;t be needed here (2015-10-20, Fat-Zer)
#include "ui_DlgReference.h"

FC_LOG_LEVEL_INIT("PartDesign", true, true)

using namespace std;
using namespace Attacher;

namespace
{
struct BodyIdentity
{
    App::Document* document {};
    std::string documentName;
    std::string documentUid;
    std::string objectName;
    long objectId {-1};
};

BodyIdentity bodyIdentity(const PartDesign::Body* body)
{
    if (!body || !body->isAttachedToDocument()) {
        return {};
    }
    return {
        body->getDocument(),
        body->getDocument()->getName(),
        body->getDocument()->Uid.getValueStr(),
        body->getNameInDocument(),
        body->getID(),
    };
}

PartDesign::Body* resolveBody(const BodyIdentity& identity)
{
    if (!identity.document || identity.documentName.empty() || identity.documentUid.empty()
        || identity.objectName.empty() || identity.objectId < 0) {
        return nullptr;
    }
    App::Document* document = nullptr;
    try {
        document = App::GetApplication().getDocument(identity.documentName.c_str());
    }
    catch (...) {
    }
    auto* body = document == identity.document && document->Uid.getValueStr() == identity.documentUid
        ? freecad_cast<PartDesign::Body*>(document->getObjectByID(identity.objectId))
        : nullptr;
    return body && body->getNameInDocument() && identity.objectName == body->getNameInDocument()
        ? body
        : nullptr;
}

App::DocumentObject* createBodyFeatureExact(
    PartDesign::Body* body,
    const std::string& typeName,
    const std::string& featureName
)
{
    if (!body || !body->isAttachedToDocument() || typeName.empty() || featureName.empty()) {
        throw Base::ValueError(
            "Creating a Part Design feature requires one exact Body, type, and name"
        );
    }
    const BodyIdentity expectedBody = bodyIdentity(body);
    const Base::Type expectedType = Base::Type::fromName(typeName.c_str());
    if (expectedType.isBad()) {
        throw Base::TypeError("The requested Part Design feature type is not registered");
    }

    std::ostringstream expression;
    expression << Gui::Command::getObjectCmd(body) << ".newObject('" << typeName << "','"
               << featureName << "')";
    auto* result = Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        *body->getDocument(),
        QByteArray(expression.str().c_str()),
        expectedType
    );
    auto* currentBody = resolveBody(expectedBody);
    if (!currentBody || PartDesign::Body::findBodyOf(result) != currentBody) {
        throw Base::RuntimeError(
            "The exact Part Design feature was not retained by its requested Body"
        );
    }
    return result;
}

App::DocumentObject* createDocumentFeatureExact(
    App::Document* document,
    const std::string& typeName,
    const std::string& featureName
)
{
    if (!document || typeName.empty() || featureName.empty()) {
        throw Base::ValueError(
            "Creating a Part Design object requires one exact document, type, and name"
        );
    }
    const Base::Type expectedType = Base::Type::fromName(typeName.c_str());
    if (expectedType.isBad()) {
        throw Base::TypeError("The requested Part Design object type is not registered");
    }
    std::ostringstream expression;
    expression << "App.getDocument('" << document->getName() << "').addObject('" << typeName
               << "','" << featureName << "')";
    return Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        *document,
        QByteArray(expression.str().c_str()),
        expectedType
    );
}

void resolveGlobalDefinitionReferences(
    App::PropertyLinkSubList& references,
    App::DocumentObject& definition
)
{
    std::vector<App::DocumentObject*> objects = references.getValues();
    std::vector<std::string> subElements = references.getSubValues();
    if (objects.size() != subElements.size()) {
        throw Base::RuntimeError("A definition selection has inconsistent object and subelement "
                                 "references");
    }
    for (std::size_t index = 0; index < objects.size(); ++index) {
        auto*& object = objects[index];
        if (!object) {
            throw Base::ValueError("A definition selection contains a missing object");
        }
        auto exact =
            PartDesign::DesignModel::resolveDefinitionSubelementReference(
                definition,
                *object,
                {subElements[index]}
            );
        if (!exact.object || exact.subelements.size() != 1) {
            throw Base::RuntimeError(
                "A definition selection did not resolve to one exact "
                "subelement reference"
            );
        }
        object = exact.object;
        subElements[index] = std::move(exact.subelements.front());
    }
    references.setValues(std::move(objects), std::move(subElements));
}
}  // namespace


//===========================================================================
// PartDesign_Datum
//===========================================================================

/**
 * @brief UnifiedDatumCommand is a common routine called by datum plane, line and point commands
 * @param cmd (i/o) command, to have shortcuts to doCommand, etc.
 * @param type (input)
 * @param name (input). Is used to generate new name for an object, and to fill undo messages.
 *
 */
void UnifiedDatumCommand(Gui::Command& cmd, Base::Type type, std::string name)
{
    App::Document* transactionDocument = nullptr;
    bool transactionOpened = false;
    const auto abortCreation = [&]() noexcept {
        if (transactionOpened && transactionDocument
            && transactionDocument->getBookedTransactionID() != App::NullTransaction) {
            transactionDocument->abortTransaction();
        }
    };
    try {
        const auto rawSelection = Gui::Selection().getSelectionEx();
        if (!std::ranges::all_of(rawSelection, [](const Gui::SelectionObject& selected) {
                return PartGui::isModelingObjectActive(selected.getObject());
            })) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Selection is not in the current History state"),
                QObject::tr("Move History after the selected object or choose an active reference.")
            );
            return;
        }

        std::string fullTypeName(type.getName());

        App::PropertyLinkSubList support;
        cmd.getSelection().getAsPropertyLinkSubList(support);

        bool bEditSelected = false;
        if (support.getSize() == 1 && support.getValue()) {
            if (support.getValue()->isDerivedFrom(type)) {
                bEditSelected = true;
            }
        }

        if (bEditSelected) {
            transactionDocument = support.getValue()->getDocument();
            transactionDocument->openTransaction(std::string(std::string("Edit ") + name).c_str()
            );  // Will be closed in the edit dialog accept/reject
            transactionOpened = true;
            if (!PartDesignGui::setEdit(support.getValue())) {
                throw Base::RuntimeError("The selected datum could not enter edit mode");
            }
        }
        else {
            transactionDocument = cmd.getDocument();
            if (!transactionDocument) {
                throw Base::RuntimeError("Create or activate a document before adding a datum");
            }

            const std::string featureName = transactionDocument->getUniqueObjectName(name.c_str());
            transactionDocument->openTransaction(std::string("Create ") + name);
            transactionOpened = true;
            auto* feature = createDocumentFeatureExact(transactionDocument, fullTypeName, featureName);
            PartDesign::DesignModel::initializeDefinition(*feature);
            resolveGlobalDefinitionReferences(support, *feature);

            if (support.getSize() > 0) {
                auto* datum = feature->getExtensionByType<Part::AttachExtension>();
                datum->attacher().setReferences(support);
                SuggestResult suggested;
                datum->attacher().suggestMapModes(suggested);
                if (suggested.message != Attacher::SuggestResult::srOK) {
                    throw Base::ValueError("The selected objects do not define a valid datum "
                                           "attachment");
                }
                FCMD_OBJ_CMD(feature, "AttachmentSupport = " << support.getPyReprString());
                FCMD_OBJ_CMD(
                    feature,
                    "MapMode = '" << AttachEngine::getModeName(suggested.bestFitMode) << "'"
                );
            }
            transactionDocument->recomputeFeature(feature, true);
            if (!feature->isValid()) {
                throw Base::RuntimeError(feature->getStatusString());
            }
            if (!PartDesignGui::setEdit(feature)) {
                throw Base::RuntimeError("The new datum could not enter edit mode");
            }
        }
    }
    catch (Base::Exception& e) {
        abortCreation();
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Error"),
            QApplication::translate("Exception", e.what())
        );
    }
    catch (Standard_Failure& e) {
        abortCreation();
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Error"),
            QString::fromLatin1(e.GetMessageString())
        );
    }
    catch (const std::exception& e) {
        abortCreation();
        QMessageBox::warning(Gui::getMainWindow(), QObject::tr("Error"), QString::fromUtf8(e.what()));
    }
    catch (...) {
        abortCreation();
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Error"),
            QObject::tr("An unexpected error prevented creation of the reference "
                        "geometry.")
        );
    }
}

/* Datum feature commands =======================================================*/

DEF_STD_CMD_A(CmdPartDesignPlane)

CmdPartDesignPlane::CmdPartDesignPlane()
    : Command("PartDesign_Plane")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Datum Plane");
    sToolTipText = QT_TR_NOOP("Creates a new datum plane");
    sWhatsThis = "PartDesign_Plane";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Plane";
}

void CmdPartDesignPlane::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    UnifiedDatumCommand(*this, Base::Type::fromName("PartDesign::Plane"), "DatumPlane");
}

bool CmdPartDesignPlane::isActive()
{
    return PartDesignGui::canStartModelingCommand();
}

DEF_STD_CMD_A(CmdPartDesignLine)

CmdPartDesignLine::CmdPartDesignLine()
    : Command("PartDesign_Line")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Datum Line");
    sToolTipText = QT_TR_NOOP("Creates a new datum line");
    sWhatsThis = "PartDesign_Line";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Line";
}

void CmdPartDesignLine::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    UnifiedDatumCommand(*this, Base::Type::fromName("PartDesign::Line"), "DatumLine");
}

bool CmdPartDesignLine::isActive()
{
    return PartDesignGui::canStartModelingCommand();
}

DEF_STD_CMD_A(CmdPartDesignPoint)

CmdPartDesignPoint::CmdPartDesignPoint()
    : Command("PartDesign_Point")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Datum Point");
    sToolTipText = QT_TR_NOOP("Creates a new datum point");
    sWhatsThis = "PartDesign_Point";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Point";
}

void CmdPartDesignPoint::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    UnifiedDatumCommand(*this, Base::Type::fromName("PartDesign::Point"), "DatumPoint");
}

bool CmdPartDesignPoint::isActive()
{
    return PartDesignGui::canStartModelingCommand();
}

DEF_STD_CMD_A(CmdPartDesignCS)

CmdPartDesignCS::CmdPartDesignCS()
    : Command("PartDesign_CoordinateSystem")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Local Coordinate System");
    sToolTipText = QT_TR_NOOP("Creates a new local coordinate system");
    sWhatsThis = "PartDesign_CoordinateSystem";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_CoordinateSystem";
}

void CmdPartDesignCS::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    UnifiedDatumCommand(*this, Base::Type::fromName("PartDesign::CoordinateSystem"), "Local_CS");
}

bool CmdPartDesignCS::isActive()
{
    return PartDesignGui::canStartModelingCommand();
}

//===========================================================================
// PartDesign_ShapeBinder
//===========================================================================

namespace
{
bool hasSubShapeBinderSourceSelection();
}

DEF_STD_CMD_A(CmdPartDesignShapeBinder)

CmdPartDesignShapeBinder::CmdPartDesignShapeBinder()
    : Command("PartDesign_ShapeBinder")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Shape Binder");
    sToolTipText = QT_TR_NOOP("Creates a new shape binder");
    sWhatsThis = "PartDesign_ShapeBinder";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_ShapeBinder";
}

void CmdPartDesignShapeBinder::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    Gui::Application::Instance->commandManager().runCommandByName("PartDesign_SubShapeBinder");
}

bool CmdPartDesignShapeBinder::isActive()
{
    return PartDesignGui::canStartModelingCommand() && hasSubShapeBinderSourceSelection();
}

//===========================================================================
// PartDesign_SubShapeBinder
//===========================================================================

DEF_STD_CMD_A(CmdPartDesignSubShapeBinder)

namespace
{
bool hasSubShapeBinderSourceSelection()
{
    App::Document* document = nullptr;
    bool hasSource = false;
    for (const auto& selected : Gui::Selection().getCompleteSelection(Gui::ResolveMode::NoResolve)) {
        auto* object = selected.pObject;
        if (!object) {
            continue;
        }
        if (!PartGui::isModelingObjectActive(object) || freecad_cast<App::Link*>(object)
            || freecad_cast<App::LinkElement*>(object)) {
            return false;
        }
        if (!document) {
            document = object->getDocument();
        }
        if (object->getDocument() != document) {
            return false;
        }
        hasSource = true;
    }
    return hasSource;
}
}  // namespace

CmdPartDesignSubShapeBinder::CmdPartDesignSubShapeBinder()
    : Command("PartDesign_SubShapeBinder")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Reference");
    sToolTipText = QT_TR_NOOP("Creates one reusable Design reference from selected bodies, faces, "
                              "edges, vertices, sketches, or datums");
    sWhatsThis = "PartDesign_SubShapeBinder";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_SubShapeBinder";
}

void CmdPartDesignSubShapeBinder::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    if (Gui::Control().activeDialog()) {
        return;
    }

    std::map<App::DocumentObject*, std::vector<std::string>> values;
    for (auto& sel : Gui::Selection().getCompleteSelection(Gui::ResolveMode::NoResolve)) {
        if (!sel.pObject) {
            continue;
        }
        auto& subs = values[sel.pObject];
        if (sel.SubName && sel.SubName[0]) {
            subs.emplace_back(sel.SubName);
        }
    }

    if (values.empty()) {
        return;
    }
    auto* destinationDocument = values.begin()->first->getDocument();
    if (!destinationDocument) {
        return;
    }
    if (std::ranges::any_of(values, [destinationDocument](const auto& entry) {
            return !entry.first || entry.first->getDocument() != destinationDocument;
        })) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Reference source required"),
            QObject::tr("Select geometry from one document. Assembly occurrences "
                        "cannot become modeling references.")
        );
        return;
    }

    PartDesign::SubShapeBinder* binder = nullptr;
    const int transactionId
        = openCommand(destinationDocument, QT_TRANSLATE_NOOP("Command", "Create Reference"));
    if (transactionId == App::NullTransaction) {
        resetTransactionID();
        return;
    }
    try {
        binder = freecad_cast<PartDesign::SubShapeBinder*>(createDocumentFeatureExact(
            destinationDocument,
            "PartDesign::SubShapeBinder",
            destinationDocument->getUniqueObjectName("Reference")
        ));
        if (!binder) {
            throw Base::RuntimeError("Could not create the Design reference");
        }
        binder->Label.setValue("Reference");
        PartDesign::DesignModel::initializeDefinition(*binder);

        decltype(values) exactValues;
        for (auto& [object, subElements] : values) {
            auto exact =
                PartDesign::DesignModel::resolveDefinitionSubelementReference(
                    *binder,
                    *object,
                    subElements
                );
            auto& destination = exactValues[exact.object];
            for (const auto& subElement : exact.subelements) {
                if (std::ranges::find(destination, subElement) == destination.end()) {
                    destination.push_back(subElement);
                }
            }
        }
        binder->setLinks(std::move(exactValues));
        updateActive();
        if (!binder->isValid() || binder->Shape.getShape().isNull()
            || !binder->Shape.getShape().isValid()) {
            const char* status = binder->getStatusString();
            throw Base::RuntimeError(
                status && *status ? status : "The selected source did not produce valid binder geometry"
            );
        }
        PartDesign::DesignModel::finalizeDefinition(*binder);
        Gui::Selection().clearSelection(destinationDocument->getName());
        Gui::Selection().addSelection(destinationDocument->getName(), binder->getNameInDocument());
        commitCommand();
    }
    catch (Base::Exception& e) {
        abortCommand(transactionId);
        resetTransactionID();
        e.reportException();
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Sub-shape binder"),
            QApplication::translate("Exception", e.what())
        );
    }
    catch (const std::exception& e) {
        abortCommand(transactionId);
        resetTransactionID();
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Reference failed"),
            QString::fromUtf8(e.what())
        );
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Reference failed"),
            QObject::tr("An unexpected error prevented reference creation.")
        );
    }
}

bool CmdPartDesignSubShapeBinder::isActive()
{
    return PartDesignGui::canStartModelingCommand() && hasSubShapeBinderSourceSelection();
}

//===========================================================================
// PartDesign_Clone
//===========================================================================

DEF_STD_CMD_A(CmdPartDesignClone)

CmdPartDesignClone::CmdPartDesignClone()
    : Command("PartDesign_Clone")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Clone");
    sToolTipText = QT_TR_NOOP(
        "Creates a new Body from the selected Body's exact current History state"
    );
    sWhatsThis = "PartDesign_Clone";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Clone";
}

namespace
{
struct CloneObjectIdentity
{
    long objectId {-1};
    std::string objectName;
};

struct CloneInteractionState
{
    App::Document* document {};
    std::string documentName;
    std::string documentUid;
    CloneObjectIdentity activeObject;
    Gui::MDIView* activeView {};
    bool hadActiveBody {};
    CloneObjectIdentity activeBodyRoot;
    std::string activeBodySubname;
    std::vector<Gui::SelectionObject> selection;
    std::vector<std::pair<CloneObjectIdentity, bool>> visibility;
};

CloneObjectIdentity cloneObjectIdentity(const App::DocumentObject* object)
{
    return object && object->isAttachedToDocument()
        ? CloneObjectIdentity {
              .objectId = object->getID(),
              .objectName = object->getNameInDocument(),
          }
        : CloneObjectIdentity {};
}

App::Document* resolveCloneStateDocument(const CloneInteractionState& state) noexcept
{
    if (!state.document || state.documentName.empty() || state.documentUid.empty()) {
        return nullptr;
    }
    App::Document* document = nullptr;
    try {
        document = App::GetApplication().getDocument(state.documentName.c_str());
    }
    catch (...) {
    }
    return document == state.document && document->Uid.getValueStr() == state.documentUid ? document
                                                                                          : nullptr;
}

App::DocumentObject* resolveCloneStateObject(
    App::Document* document,
    const CloneObjectIdentity& identity
) noexcept
{
    if (!document || identity.objectId <= 0 || identity.objectName.empty()) {
        return nullptr;
    }
    auto* object = document->getObjectByID(identity.objectId);
    return object && object->isAttachedToDocument() && object->getDocument() == document
            && object->getNameInDocument() && identity.objectName == object->getNameInDocument()
        ? object
        : nullptr;
}

CloneInteractionState captureCloneInteractionState(App::Document* document)
{
    CloneInteractionState state;
    state.document = document;
    state.documentName = document->getName();
    state.documentUid = document->Uid.getValueStr();
    state.activeObject = cloneObjectIdentity(document->getActiveObject());
    state.selection = Gui::Selection().getSelectionEx(
        document->getName(),
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::NoResolve
    );
    state.visibility.reserve(document->getObjects().size());
    for (auto* object : document->getObjects()) {
        state.visibility.emplace_back(cloneObjectIdentity(object), object->Visibility.getValue());
    }

    state.activeView = Gui::Application::Instance->activeView();
    if (state.activeView && state.activeView->getAppDocument() == document) {
        state.hadActiveBody = state.activeView->hasActiveObject(PDBODYKEY);
        if (state.hadActiveBody) {
            App::DocumentObject* activeBodyRoot = nullptr;
            state.activeView->getActiveObject<App::DocumentObject*>(
                PDBODYKEY,
                &activeBodyRoot,
                &state.activeBodySubname
            );
            state.activeBodyRoot = cloneObjectIdentity(activeBodyRoot);
        }
    }
    else {
        state.activeView = nullptr;
    }
    return state;
}

void restoreCloneInteractionState(const CloneInteractionState& state) noexcept
{
    auto* document = resolveCloneStateDocument(state);
    if (!document) {
        return;
    }

    try {
        for (const auto& [identity, visible] : state.visibility) {
            document = resolveCloneStateDocument(state);
            auto* object = resolveCloneStateObject(document, identity);
            if (object) {
                object->Visibility.setValue(visible);
            }
        }
        document = resolveCloneStateDocument(state);
        if (!document) {
            return;
        }
        auto* activeObject = resolveCloneStateObject(document, state.activeObject);
        if (state.activeObject.objectId <= 0 || activeObject) {
            document->setActiveObject(activeObject);
        }

        Gui::SelectionLogDisabler selectionLogDisabler(true);
        Gui::Selection().clearSelection(document->getName());
        for (const auto& selected : state.selection) {
            if (selected.getObject()) {
                Gui::Selection().addSelection(selected);
            }
        }

        document = resolveCloneStateDocument(state);
        auto* guiDocument = document && Gui::Application::Instance
            ? Gui::Application::Instance->getDocument(document)
            : nullptr;
        const auto views = guiDocument ? guiDocument->getMDIViews(true) : std::list<Gui::MDIView*> {};
        if (state.activeView && std::ranges::find(views, state.activeView) != views.end()) {
            auto* activeBodyRoot = resolveCloneStateObject(document, state.activeBodyRoot);
            if (state.hadActiveBody && activeBodyRoot) {
                state.activeView
                    ->setActiveObject(activeBodyRoot, PDBODYKEY, state.activeBodySubname.c_str());
            }
            else {
                state.activeView->setActiveObject(nullptr, PDBODYKEY);
            }
        }
    }
    catch (const Base::Exception& error) {
        error.reportException();
    }
    catch (const std::exception& error) {
        Base::Console().error("Failed to restore Clone interaction state: %s\n", error.what());
    }
    catch (...) {
        Base::Console().error("Failed to restore Clone interaction state\n");
    }
}

}  // namespace

void CmdPartDesignClone::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto selected = Gui::Selection().getSelectionEx(
        nullptr,
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::NoResolve
    );
    auto* selectedObject = selected.size() == 1 ? selected.front().getObject() : nullptr;
    if (selected.size() != 1 || freecad_cast<App::Link*>(selectedObject)
        || freecad_cast<App::LinkElement*>(selectedObject)
        || !PartGui::isModelingObjectActive(selectedObject)) {
        return;
    }

    auto* sourceBody = freecad_cast<PartDesign::Body*>(PartGui::findModelingBody(selectedObject));
    auto* document = sourceBody ? sourceBody->getDocument() : nullptr;
    if (!document || !PartDesign::designBodyStateBefore(sourceBody, nullptr)
        || Gui::Control().activeDialog(document)) {
        return;
    }

    const CloneInteractionState interactionState = captureCloneInteractionState(document);
    const int transactionId = openCommand(document, QT_TRANSLATE_NOOP("Command", "Create Clone"));
    if (transactionId == App::NullTransaction) {
        resetTransactionID();
        return;
    }

    try {
        auto* operation = freecad_cast<PartDesign::DesignClone*>(createDocumentFeatureExact(
            document,
            "PartDesign::DesignClone",
            document->getUniqueObjectName("Clone")
        ));
        if (!operation) {
            throw Base::TypeError("The Design Clone factory returned an incompatible object");
        }

        operation->Label.setValue((std::string("Clone ") + sourceBody->Label.getValue()).c_str());
        auto edit = PartDesign::DesignModel::beginOperationEdit(*operation);
        PartDesign::DesignModel::setCloneSource(edit, *sourceBody);
        auto outputs = PartDesign::DesignModel::finalizeOperation(edit);
        if (outputs.size() != 1 || !outputs.front()) {
            throw Base::RuntimeError("Clone did not publish exactly one new Body");
        }

        auto* outputBody = outputs.front();
        outputBody->Label.setValue((std::string(sourceBody->Label.getValue()) + " Copy").c_str());
        outputBody->ShapeMaterial.setValue(sourceBody->ShapeMaterial.getValue());
        PartDesignGui::copyShapeVisualProperties(*outputBody, *sourceBody);

        Gui::Selection().clearSelection(document->getName());
        Gui::Selection().addSelection(document->getName(), outputBody->getNameInDocument());
        commitCommand();
    }
    catch (const Base::Exception& error) {
        abortCommand(transactionId);
        resetTransactionID();
        restoreCloneInteractionState(interactionState);
        error.reportException();
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Clone failed"),
            QApplication::translate("Exception", error.what())
        );
    }
    catch (const std::exception& error) {
        abortCommand(transactionId);
        resetTransactionID();
        restoreCloneInteractionState(interactionState);
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Clone failed"),
            QString::fromUtf8(error.what())
        );
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        restoreCloneInteractionState(interactionState);
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Clone failed"),
            QObject::tr("An unexpected error prevented clone creation.")
        );
    }
}

bool CmdPartDesignClone::isActive()
{
    if (!PartDesignGui::canStartModelingCommand()) {
        return false;
    }
    const auto selected = Gui::Selection().getSelectionEx(
        nullptr,
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::NoResolve
    );
    auto* selectedObject = selected.size() == 1 ? selected.front().getObject() : nullptr;
    if (selected.size() != 1 || freecad_cast<App::Link*>(selectedObject)
        || freecad_cast<App::LinkElement*>(selectedObject)
        || !PartGui::isModelingObjectActive(selectedObject)) {
        return false;
    }
    auto* body = freecad_cast<PartDesign::Body*>(PartGui::findModelingBody(selectedObject));
    return body && PartDesign::designBodyStateBefore(body, nullptr);
}

//===========================================================================
// PartDesign_Sketch
//===========================================================================

/* Sketch commands =======================================================*/
DEF_STD_CMD_A(CmdPartDesignNewSketch)

CmdPartDesignNewSketch::CmdPartDesignNewSketch()
    : Command("PartDesign_NewSketch")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("New Sketch");
    sToolTipText = QT_TR_NOOP("Creates a new sketch");
    sWhatsThis = "PartDesign_NewSketch";
    sStatusTip = sToolTipText;
    sPixmap = "Sketcher_NewSketch";
}


void CmdPartDesignNewSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    PartDesignGui::SketchWorkflow creator(getActiveGuiDocument());
    creator.createSketch();
}

bool CmdPartDesignNewSketch::isActive()
{
    return PartDesignGui::canStartModelingCommand();
}

//===========================================================================
// Common utility functions for all features creating solids
//===========================================================================

static void finishFeature(
    Gui::Command* cmd,
    App::DocumentObject* feature,
    App::DocumentObject* prevSolidFeature = nullptr,
    const bool hidePrevSolid = true,
    const bool updateDocument = true
)
{
    if (!feature || !feature->isAttachedToDocument()) {
        cmd->abortCommand();
        return;
    }
    App::Document* document = feature->getDocument();
    PartDesign::Body* activeBody = nullptr;

    if (prevSolidFeature) {
        // insert into the same body as the given previous one
        activeBody = PartDesign::Body::findBodyOf(prevSolidFeature);
    }
    else {
        activeBody = PartDesign::Body::findBodyOf(feature);
    }
    if (!activeBody || activeBody->getDocument() != document) {
        Base::Console().error(
            "Cannot finish Part Design feature '%s': its owning Body is unavailable.\n",
            feature->getNameInDocument() ? feature->getNameInDocument() : "<detached>"
        );
        cmd->abortCommand();
        return;
    }

    if (hidePrevSolid && prevSolidFeature) {
        FCMD_OBJ_HIDE(prevSolidFeature);
    }

    if (updateDocument) {
        cmd->updateDocument(document);
    }

    auto base = dynamic_cast<PartDesign::Feature*>(feature);
    if (base) {
        base = dynamic_cast<PartDesign::Feature*>(base->getBaseObject(true));
    }
    App::DocumentObject* obj = base;
    if (!obj) {
        obj = activeBody;
    }

    // Do this before calling setEdit to avoid to override the 'Shape preview' mode (#0003621)
    if (obj) {
        cmd->copyVisual(feature, "ShapeAppearance", obj);
        cmd->copyVisual(feature, "LineColor", obj);
        cmd->copyVisual(feature, "PointColor", obj);
        cmd->copyVisual(feature, "Transparency", obj);
        cmd->copyVisual(feature, "DisplayMode", obj);
    }

    PartDesignGui::setEdit(feature, activeBody);
    Gui::Selection().clearSelection(document->getName());
}

//===========================================================================
// Common utility functions for ProfileBased features
//===========================================================================

// Take a list of Part2DObjects and classify them for creating a
// ProfileBased feature. FirstFreeSketch is the first free sketch in the same body
// or sketches.end() if non available. The returned number is the amount of free sketches
unsigned validateSketches(
    std::vector<App::DocumentObject*>& sketches,
    std::vector<PartDesignGui::TaskFeaturePick::featureStatus>& status,
    std::vector<App::DocumentObject*>::iterator& firstFreeSketch,
    PartDesign::Body* pcActiveBody
)
{
    // TODO Review the function for non-part bodies (2015-09-04, Fat-Zer)
    App::Part* pcActivePart = PartDesignGui::getPartFor(pcActiveBody, false);

    // TODO: If the user previously opted to allow multiple use of sketches or use of sketches from
    // other bodies, then count these as valid sketches!
    unsigned freeSketches = 0;
    firstFreeSketch = sketches.end();

    for (std::vector<App::DocumentObject*>::iterator s = sketches.begin(); s != sketches.end(); s++) {
        if (!PartGui::isModelingObjectActive(*s)) {
            status.push_back(PartDesignGui::TaskFeaturePick::afterTip);
            continue;
        }

        if (!pcActiveBody) {
            // We work in the old style outside any body
            if (PartDesign::Body::findBodyOf(*s)) {
                status.push_back(PartDesignGui::TaskFeaturePick::otherPart);
                continue;
            }
        }
        else if (!pcActiveBody->hasObject(*s)) {
            // Check whether this plane belongs to a body of the same part
            PartDesign::Body* b = PartDesign::Body::findBodyOf(*s);
            if (!b) {
                status.push_back(PartDesignGui::TaskFeaturePick::notInBody);
            }
            else if (pcActivePart && pcActivePart->hasObject(b, true)) {
                status.push_back(PartDesignGui::TaskFeaturePick::otherBody);
            }
            else {
                status.push_back(PartDesignGui::TaskFeaturePick::otherPart);
            }

            continue;
        }

        // Base::Console().error("Checking sketch %s\n", (*s)->getNameInDocument());
        //  Check whether this sketch is already being used by another feature
        //  Body features don't count...
        std::vector<App::DocumentObject*> inList = (*s)->getInList();
        std::vector<App::DocumentObject*>::iterator o = inList.begin();
        while (o != inList.end()) {
            // Base::Console().error("Inlist: %s\n", (*o)->getNameInDocument());
            if ((*o)->isDerivedFrom<PartDesign::Body>()) {
                o = inList.erase(o);  // ignore bodies
            }
            else if (!((*o)->isDerivedFrom<PartDesign::Feature>())) {
                o = inList.erase(o);  // ignore non-partDesign
            }
            else {
                ++o;
            }
        }
        if (!inList.empty()) {
            status.push_back(PartDesignGui::TaskFeaturePick::isUsed);
            continue;
        }

        if (pcActiveBody && pcActiveBody->isAfterInsertPoint(*s)) {
            status.push_back(PartDesignGui::TaskFeaturePick::afterTip);
            continue;
        }

        // Check whether the sketch shape is valid
        Part::Part2DObject* sketch = static_cast<Part::Part2DObject*>(*s);
        const TopoDS_Shape& shape = sketch->Shape.getValue();
        if (shape.IsNull()) {
            status.push_back(PartDesignGui::TaskFeaturePick::invalidShape);
            continue;
        }

        // count free wires
        int ctWires = 0;
        TopExp_Explorer ex;
        for (ex.Init(shape, TopAbs_WIRE); ex.More(); ex.Next()) {
            ctWires++;
        }
        if (ctWires == 0) {
            status.push_back(PartDesignGui::TaskFeaturePick::noWire);
            continue;
        }

        // All checks passed - found a valid sketch
        if (firstFreeSketch == sketches.end()) {
            firstFreeSketch = s;
        }
        freeSketches++;
        status.push_back(PartDesignGui::TaskFeaturePick::validFeature);
    }

    return freeSketches;
}

/**
 *  Partially pulled from Linkstage3 importExternalObjects for toponaming element map
 *  compatibility with sketches that contain point objects.  By adding an empty
 *  subobject when appropriate, we allow those sketches to be used as profiles without error.
 *
 * @param prop  The property ( generally a Profile link )
 * @param _sobjs    Subobjects to use
 * @param report    True if we should raise a dialog, otherwise raise and exception
 * @return  True if elements were found
 */
bool importExternalElements(App::PropertyLinkSub& prop, std::vector<App::SubObjectT> _sobjs)
{
    if (!prop.getName() || !prop.getName()[0]) {
        FC_THROWM(Base::RuntimeError, "Invalid property");
    }
    auto editObj = freecad_cast<App::DocumentObject*>(prop.getContainer());
    if (!editObj) {
        FC_THROWM(Base::RuntimeError, "Editing object not found");
    }
    auto body = PartDesign::Body::findBodyOf(editObj);
    if (!body) {
        FC_THROWM(Base::RuntimeError, "No body for editing object: " << editObj->getNameInDocument());
    }
    std::map<App::DocumentObject*, std::vector<std::string>> links;
    std::vector<App::SubObjectT> sobjs;
    auto docName = editObj->getDocument()->getName();
    auto inList = editObj->getInListEx(true);
    auto inListProp = editObj->getInListExProp(true);
    for (auto sobjT : _sobjs) {
        auto sobj = sobjT.getSubObject();
        if (sobj == editObj) {
            continue;
        }
        if (!sobj) {
            FC_THROWM(Base::RuntimeError, "Object not found: " << sobjT.getSubObjectFullName(docName));
        }
        if (App::GetApplication().isFineGrainedRecomputeEnabled()) {
            // Fully mimics the else block except for taking into account input properties.
            for (const auto& [fromObj, fromProp, toObj, toProp] : inListProp) {
                if (fromObj == sobj && !toObj->isInputProperty(toProp)) {
                    FC_THROWM(
                        Base::RuntimeError,
                        "Cyclic dependency on object " << sobjT.getSubObjectFullName(docName)
                    );
                }
            }
        }
        else {
            if (inList.count(sobj)) {
                FC_THROWM(
                    Base::RuntimeError,
                    "Cyclic dependency on object " << sobjT.getSubObjectFullName(docName)
                );
            }
        }
        sobjT.normalized();
        // Make sure that if a subelement is chosen for some object,
        // we exclude whole object reference for that object.
        auto& subs = links[sobj];
        std::string element = sobjT.getOldElementName();
        if (element.size()) {
            if (subs.size() == 1 && subs.front().empty()) {
                for (auto it = sobjs.begin(); it != sobjs.end();) {
                    if (it->getSubObject() == sobj) {
                        sobjs.erase(it);
                        break;
                    }
                }
            }
        }
        else if (subs.size() > 0) {
            continue;
        }
        subs.push_back(std::move(element));
        sobjs.push_back(sobjT);
    }

    int import = 0;
    App::DocumentObject* obj = nullptr;
    std::vector<std::string> subs;
    for (const auto& sobjT : sobjs) {
        auto sobj = sobjT.getSubObject();
        if (PartDesign::Body::findBodyOf(sobj) != body) {
            import = 1;
            break;
        }
        if (!obj) {
            obj = sobj;
        }
        else if (obj != sobj) {
            if (!import) {
                import = -1;
            }
            break;
        }
        subs.push_back(sobjT.getOldElementName());
    }
    if (!import) {
        if (subs.empty()) {
            subs.emplace_back();
        }
        if (obj == prop.getValue() && prop.getSubValues() == subs) {
            return false;
        }
        prop.setValue(obj, std::move(subs));
        return true;
    }
    return false;
}

/**
 * Body owns the rendered result in the consolidated model view, so a viewport pick can be
 * reported as Body.FaceN/EdgeN even though that topology is supplied by Body::Tip. A feature must
 * link to the Tip rather than to its own Body, which would create a circular dependency.
 */
App::DocumentObject* resolveBodyResultSelection(
    const Gui::SelectionObject& selection,
    const PartDesign::Body* body
)
{
    return const_cast<App::DocumentObject*>(
        PartGui::resolveModelingObjectForBody(selection.getObject(), body)
    );
}

Part::Feature* usableSolidTip(PartDesign::Body* body)
{
    auto* tip = body ? freecad_cast<Part::Feature*>(body->Tip.getValue()) : nullptr;
    if (!tip || !tip->isValid()) {
        return nullptr;
    }

    const auto& shape = tip->Shape.getShape();
    if (shape.isNull() || shape.countSubShapes(TopAbs_SOLID) == 0) {
        return nullptr;
    }
    return tip;
}

enum class ProfileCommandInput
{
    Single,
    Loft,
    Pipe,
};

bool featureCommandBody(PartDesign::Body*& body)
{
    body = nullptr;
    if (!PartDesignGui::canStartModelingCommand()) {
        return false;
    }

    body = PartDesignGui::getBodyForCommandState();
    return body != nullptr;
}

bool selectionBelongsToBody(const Gui::SelectionObject& selection, const PartDesign::Body* body)
{
    return PartGui::findModelingBody(selection.getObject()) == body;
}

bool isValidProfileSelection(const Gui::SelectionObject& selection, const PartDesign::Body* body)
{
    auto* profileObject = resolveBodyResultSelection(selection, body);
    if (profileObject && profileObject->isDerivedFrom<Part::Part2DObject>()) {
        return true;
    }
    if (!profileObject || !profileObject->isDerivedFrom<Part::Feature>()
        || selection.getSubNames().size() != 1) {
        return false;
    }

    try {
        const auto selectedShape = Part::Feature::getTopoShape(
                                       profileObject,
                                       Part::ShapeOption::NeedSubElement
                                           | Part::ShapeOption::ResolveLink
                                           | Part::ShapeOption::Transform,
                                       selection.getSubNames().front().c_str()
        )
                                       .getShape();
        if (selectedShape.IsNull() || selectedShape.ShapeType() != TopAbs_FACE) {
            return false;
        }
        const auto surface = BRep_Tool::Surface(TopoDS::Face(selectedShape));
        return !surface.IsNull() && GeomLib_IsPlanarSurface(surface).IsPlanar();
    }
    catch (const Base::Exception&) {
        return false;
    }
    catch (const Standard_Failure&) {
        return false;
    }
}

bool hasAvailableProfile(PartDesign::Body* body)
{
    auto sketches = body->getDocument()->getObjectsOfType(Part::Part2DObject::getClassTypeId());
    if (sketches.empty()) {
        return false;
    }

    std::vector<PartDesignGui::TaskFeaturePick::featureStatus> status;
    auto firstFreeSketch = sketches.end();
    return validateSketches(sketches, status, firstFreeSketch, body) > 0;
}

bool isValidSecondaryProfileInput(
    const Gui::SelectionObject& selection,
    ProfileCommandInput input,
    const PartDesign::Body* body
)
{
    auto* object = resolveBodyResultSelection(selection, body);
    if (!object || !object->isDerivedFrom<Part::Feature>()) {
        return false;
    }

    const auto shape = Part::Feature::getTopoShape(
        object,
        Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
    );
    if (shape.isNull()) {
        return false;
    }
    if (input != ProfileCommandInput::Pipe || object->isDerivedFrom<Part::Part2DObject>()) {
        return true;
    }

    const auto& subNames = selection.getSubNames();
    return !subNames.empty() && std::ranges::all_of(subNames, [](const std::string& subName) {
        return subName.starts_with("Edge");
    });
}

bool isProfileCommandActive(bool subtractive, ProfileCommandInput input)
{
    PartDesign::Body* body = nullptr;
    if (!featureCommandBody(body) || (subtractive && !usableSolidTip(body))) {
        return false;
    }

    const auto selection = PartGui::getModelingSelectionForBody(body, body->getDocument()->getName());
    if (selection.empty()) {
        return hasAvailableProfile(body);
    }
    if ((input == ProfileCommandInput::Single && selection.size() != 1)
        || (input == ProfileCommandInput::Pipe && selection.size() > 2)) {
        return false;
    }
    if (!std::ranges::all_of(selection, [body](const Gui::SelectionObject& item) {
            return selectionBelongsToBody(item, body) && resolveBodyResultSelection(item, body);
        })) {
        return false;
    }
    if (!isValidProfileSelection(selection.front(), body)) {
        return false;
    }
    return std::ranges::all_of(
        std::next(selection.begin()),
        selection.end(),
        [input, body](const Gui::SelectionObject& item) {
            return isValidSecondaryProfileInput(item, input, body);
        }
    );
}

bool isDraftCommandActive()
{
    PartDesign::Body* body = nullptr;
    if (!featureCommandBody(body) || !usableSolidTip(body)) {
        return false;
    }

    const auto selection = PartGui::getModelingSelectionForBody(body, body->getDocument()->getName());
    if (selection.size() != 1 || !selectionBelongsToBody(selection.front(), body)) {
        return false;
    }
    auto* base = freecad_cast<Part::Feature*>(resolveBodyResultSelection(selection.front(), body));
    if (!base || base->Shape.getShape().isNull()) {
        return false;
    }

    const auto& subNames = selection.front().getSubNames();
    if (subNames.empty()) {
        return false;
    }
    const auto& topShape = base->Shape.getShape();
    return std::ranges::all_of(subNames, [&topShape](const std::string& subName) {
        const auto subShape = topShape.getSubShape(subName.c_str());
        if (subShape.IsNull() || subShape.ShapeType() != TopAbs_FACE) {
            return false;
        }
        BRepAdaptor_Surface surface(TopoDS::Face(subShape));
        return surface.GetType() == GeomAbs_Plane || surface.GetType() == GeomAbs_Cylinder
            || surface.GetType() == GeomAbs_Cone;
    });
}

enum class DressupSelection
{
    EdgeOrFace,
    Face,
};

bool isDressupCommandActive(DressupSelection requiredSelection)
{
    PartDesign::Body* body = nullptr;
    if (!featureCommandBody(body) || !usableSolidTip(body)) {
        return false;
    }

    const auto selection = PartGui::getModelingSelectionForBody(body, body->getDocument()->getName());
    if (selection.size() != 1 || !selectionBelongsToBody(selection.front(), body)) {
        return false;
    }

    auto* base = freecad_cast<Part::Feature*>(resolveBodyResultSelection(selection.front(), body));
    if (!base || !base->isValid()) {
        return false;
    }
    const auto& shape = base->Shape.getShape();
    if (shape.isNull() || shape.countSubShapes(TopAbs_SOLID) == 0) {
        return false;
    }

    const auto& subNames = selection.front().getSubNames();
    if (subNames.empty()) {
        return false;
    }
    return std::ranges::all_of(subNames, [&shape, requiredSelection](const std::string& subName) {
        const auto subShape = shape.getSubShape(subName.c_str());
        if (subShape.IsNull()) {
            return false;
        }
        if (subShape.ShapeType() == TopAbs_FACE) {
            return true;
        }
        return requiredSelection == DressupSelection::EdgeOrFace
            && subShape.ShapeType() == TopAbs_EDGE;
    });
}

bool isTransformCommandActive(bool rejectMultiTransform = false)
{
    PartDesign::Body* body = nullptr;
    if (!featureCommandBody(body) || !usableSolidTip(body)) {
        return false;
    }

    const auto selection = Gui::Selection().getSelectionEx();
    if (selection.empty()) {
        return true;
    }

    return std::ranges::all_of(selection, [body, rejectMultiTransform](const Gui::SelectionObject& selected) {
        auto* selectedObject = selected.getObject();
        if (!selectedObject || !PartGui::isModelingObjectActive(selectedObject)
            || !selectionBelongsToBody(selected, body)) {
            return false;
        }
        auto* object = PartGui::resolveModelingObjectForBody(selectedObject, body);
        if (!object) {
            return false;
        }
        if (selectedObject == body
            || (!selectedObject->isDerivedFrom<Part::Feature>() && object == body->Tip.getValue())) {
            return usableSolidTip(body) != nullptr;
        }
        if (rejectMultiTransform && object->isDerivedFrom<PartDesign::MultiTransform>()) {
            return false;
        }
        if (object->isDerivedFrom<PartDesign::FeatureAddSub>()) {
            return true;
        }
        if (!object->isDerivedFrom<Part::Feature>() || object->isDerivedFrom<Part::Part2DObject>()) {
            return false;
        }
        return !Part::Feature::getTopoShape(
                    object,
                    Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
        )
                    .isNull();
    });
}

void prepareProfileBased(
    PartDesign::Body* pcActiveBody,
    Gui::Command* cmd,
    const std::string& which,
    std::function<void(Part::Feature*, App::DocumentObject*)> func
)
{
    const BodyIdentity targetBodyIdentity = bodyIdentity(pcActiveBody);
    auto base_worker = [=](App::DocumentObject* feature, const std::vector<std::string>& subs) {
        if (!feature || !feature->isDerivedFrom<Part::Feature>()) {
            return;
        }
        auto* targetBody = resolveBody(targetBodyIdentity);
        if (!targetBody || feature->getDocument() != targetBody->getDocument()) {
            return;
        }

        // Related to #0002760: when an operation can't be performed due to a broken
        // profile then make sure that it is recomputed when cancelling the operation
        // otherwise it might be impossible to see that it's broken.
        if (feature->isTouched()) {
            feature->recomputeFeature();
        }

        std::string FeatName = cmd->getUniqueObjectName(which.c_str(), targetBody);

        cmd->openCommand(targetBody->getDocument(), std::string("Make ") + which);

        auto* Feat = createBodyFeatureExact(targetBody, "PartDesign::" + which, FeatName);

        auto objCmd = Gui::Command::getObjectCmd(feature);

        // Populate the subs parameter by checking for external elements before
        // we construct our command.
        auto* ProfileFeature = freecad_cast<PartDesign::ProfileBased*>(Feat);
        if (!ProfileFeature) {
            throw Base::TypeError("The exact profile-based factory returned an incompatible feature");
        }

        std::vector<std::string>& cmdSubs = const_cast<vector<std::string>&>(subs);
        if (subs.size() == 0) {
            importExternalElements(ProfileFeature->Profile, {feature});
            cmdSubs = ProfileFeature->Profile.getSubValues();
        }
        // run the command in console to set the profile (without selected subelements)
        auto runProfileCmd = [=]() {
            FCMD_OBJ_CMD(Feat, "Profile = " << objCmd);
        };

        // run the command in console to set the profile with selected subelements
        // useful to set, say, a face of a solid as the "profile"
        auto runProfileCmdWithSubs = [=]() {
            std::ostringstream ss;
            for (auto& s : cmdSubs) {
                ss << "'" << s << "',";
            }
            FCMD_OBJ_CMD(Feat, "Profile = (" << objCmd << ", [" << ss.str() << "])");
        };

        if (which.compare("AdditiveLoft") == 0 || which.compare("SubtractiveLoft") == 0) {
            // for additive and subtractive lofts set subvalues even for sketches
            // when a vertex is first selected
            auto subName = subs.empty() ? "" : subs.front();

            // `ProfileBased::getProfileShape()` and other methods will return
            // just the sub-shapes if they are set. So when whole sketches are
            // desired, do not set sub-values.
            if (feature->isDerivedFrom<Part::Part2DObject>() && subName.compare(0, 6, "Vertex") != 0) {
                runProfileCmd();
            }
            else {
                runProfileCmdWithSubs();
            }

            // for additive and subtractive lofts allow the user to preselect the sections
            auto selection = PartGui::getModelingSelectionForBody(
                targetBody,
                targetBody->getDocument()->getName()
            );
            if (selection.size() > 1) {  // treat additional selected objects as sections
                for (std::vector<Gui::SelectionObject>::size_type ii = 1; ii < selection.size();
                     ii++) {
                    // Add subvalues even for sketches in case we just want points
                    auto* section = resolveBodyResultSelection(selection[ii], targetBody);
                    if (!section) {
                        continue;
                    }
                    auto objCmdSection = Gui::Command::getObjectCmd(section);
                    const auto& subnames = selection[ii].getSubNames();
                    std::ostringstream ss;
                    if (!subnames.empty()) {
                        for (auto& s : subnames) {
                            ss << "'" << s << "',";
                        }
                    }
                    else {
                        // an empty string indicates the whole object
                        ss << "''";
                    }
                    FCMD_OBJ_CMD(Feat, "Sections += [(" << objCmdSection << ", [" << ss.str() << "])]");
                }
            }
        }
        else if (which.compare("AdditivePipe") == 0 || which.compare("SubtractivePipe") == 0) {
            // for additive and subtractive pipes set subvalues even for sketches
            // to support point sections
            auto subName = subs.empty() ? "" : subs.front();

            // `ProfileBased::getProfileShape()` and other methods will return
            // just the sub-shapes if they are set. So when whole sketches are
            // desired, don't set sub-values.
            if (feature->isDerivedFrom<Part::Part2DObject>() && subName.compare(0, 6, "Vertex") != 0) {
                runProfileCmd();
            }
            else {
                runProfileCmdWithSubs();
            }

            // for additive and subtractive pipes allow the user to preselect the spines
            auto selection = PartGui::getModelingSelectionForBody(
                targetBody,
                targetBody->getDocument()->getName()
            );
            if (selection.size() == 2) {  // treat additional selected object as spine
                std::vector<string> subnames = selection[1].getSubNames();
                auto* spine = resolveBodyResultSelection(selection[1], targetBody);
                if (!spine) {
                    return;
                }
                auto objCmdSpine = Gui::Command::getObjectCmd(spine);
                if (spine->isDerivedFrom<Part::Part2DObject>() && subnames.empty()) {
                    FCMD_OBJ_CMD(Feat, "Spine = " << objCmdSpine);
                }
                else {
                    std::ostringstream ss;
                    for (auto& s : subnames) {
                        if (s.find("Edge") != std::string::npos) {
                            ss << "'" << s << "',";
                        }
                    }
                    FCMD_OBJ_CMD(Feat, "Spine = (" << objCmdSpine << ", [" << ss.str() << "])");
                }
            }
        }
        else {
            // Always use the subs
            runProfileCmdWithSubs();
        }

        func(static_cast<Part::Feature*>(feature), Feat);
    };


    // in case of subtractive types, check that there is something to subtract from
    if ((which.find("Subtractive") != std::string::npos) || (which.compare("Groove") == 0)
        || (which.compare("Pocket") == 0) || (which.compare("Hole") == 0)) {

        if (!usableSolidTip(pcActiveBody)) {
            QMessageBox msgBox(Gui::getMainWindow());
            msgBox.setText(
                QObject::tr("Cannot use this command as there is no solid to subtract from.")
            );
            msgBox.setInformativeText(QObject::tr(
                "Ensure that the body contains a feature before attempting a subtractive command."
            ));
            msgBox.setStandardButtons(QMessageBox::Ok);
            msgBox.setDefaultButton(QMessageBox::Ok);
            msgBox.exec();
            return;
        }
    }


    // if a profile is selected we can make our life easy and fast
    auto selection
        = PartGui::getModelingSelectionForBody(pcActiveBody, pcActiveBody->getDocument()->getName());
    if (!selection.empty()) {
        bool onlyAllowed = true;
        for (auto& it : selection) {
            auto* object = it.getObject();
            if (!resolveBodyResultSelection(it, pcActiveBody)) {
                onlyAllowed = false;
                break;
            }
            auto* selectedBody = freecad_cast<PartDesign::Body*>(PartGui::findModelingBody(object));
            if (selectedBody != pcActiveBody) {  // selected objects must belong to the body
                onlyAllowed = false;
                break;
            }
        }
        if (!onlyAllowed) {
            QMessageBox msgBox(Gui::getMainWindow());
            msgBox.setText(QObject::tr(
                "Cannot use selected object. Selected object must belong to the active body"
            ));
            msgBox.setInformativeText(QObject::tr("Consider using a shape binder or a base feature "
                                                  "to reference external geometry in a body"));
            msgBox.setStandardButtons(QMessageBox::Ok);
            msgBox.setDefaultButton(QMessageBox::Ok);
            msgBox.exec();
        }
        else {
            auto& profileSelection = selection.front();
            auto* profileObject = resolveBodyResultSelection(profileSelection, pcActiveBody);
            if (!isValidProfileSelection(profileSelection, pcActiveBody)) {
                QMessageBox::warning(
                    Gui::getMainWindow(),
                    QObject::tr("Profile required"),
                    QObject::tr("Select a sketch, 2D profile, or planar face.")
                );
                return;
            }
            base_worker(profileObject, profileSelection.getSubNames());
        }
        return;
    }

    // no face profile was selected, do the extended sketch logic

    bool bNoSketchWasSelected = false;
    // Get a valid sketch from the user
    // First check selections
    std::vector<App::DocumentObject*> sketches = cmd->getSelection().getObjectsOfType(
        Part::Part2DObject::getClassTypeId()
    );
    if (sketches.empty()) {  // no sketches were selected. Let user pick an object from valid ones
                             // available in document
        sketches = cmd->getDocument()->getObjectsOfType(Part::Part2DObject::getClassTypeId());
        bNoSketchWasSelected = true;
    }

    if (sketches.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("No sketch to work on"),
            QObject::tr("No sketch is available in the document")
        );
        return;
    }

    std::vector<PartDesignGui::TaskFeaturePick::featureStatus> status;
    std::vector<App::DocumentObject*>::iterator firstFreeSketch;
    int freeSketches = validateSketches(sketches, status, firstFreeSketch, pcActiveBody);

    auto accepter = [=](const std::vector<App::DocumentObject*>& features) -> bool {
        if (features.empty()) {
            return false;
        }

        return true;
    };

    auto sketch_worker = [base_worker](std::vector<App::DocumentObject*> features) {
        if (features.empty()) {
            return;
        }
        base_worker(features.front(), {});
    };

    // if there is a sketch selected which is from another body or part we need to bring up the
    // pick task dialog to decide how those are handled
    bool extReference = std::find_if(
                            status.begin(),
                            status.end(),
                            [](const PartDesignGui::TaskFeaturePick::featureStatus& s) {
                                return s == PartDesignGui::TaskFeaturePick::otherBody
                                    || s == PartDesignGui::TaskFeaturePick::otherPart
                                    || s == PartDesignGui::TaskFeaturePick::notInBody;
                            }
                        )
        != status.end();

    // TODO Clean this up (2015-10-20, Fat-Zer)
    if (pcActiveBody && !bNoSketchWasSelected && extReference) {

        // Hint: In an older version the function expected the body to be inside
        // a Part container and if not an error was raised and the function aborted.
        // First of all, for the user this wasn't obvious because the error message
        // was quite confusing (and thus the user may have done the wrong thing since
        // they may have assumed the that the sketch was meant) and
        // Second, there is no need that the body must be inside a Part container.
        // For more details see: https://forum.freecad.org/viewtopic.php?f=19&t=32164
        // The function has been modified not to expect the body to be in the Part
        // and it now directly invokes the 'makeCopy' dialog.
        auto* pcActivePart = PartDesignGui::getPartFor(pcActiveBody, false);

        QDialog dia(Gui::getMainWindow());
        PartDesignGui::Ui_DlgReference dlg;
        dlg.setupUi(&dia);
        dia.setModal(true);
        int result = dia.exec();
        if (result == QDialog::DialogCode::Rejected) {
            return;
        }

        if (!dlg.radioXRef->isChecked()) {
            cmd->openCommand(QT_TRANSLATE_NOOP("Command", "Make Copy"));
            auto copy = PartDesignGui::TaskFeaturePick::makeCopy(
                sketches[0],
                "",
                dlg.radioIndependent->isChecked(),
                pcActiveBody->getDocument()
            );
            if (!copy) {
                cmd->abortCommand();
                QMessageBox::warning(
                    Gui::getMainWindow(),
                    QObject::tr("Copy failed"),
                    QObject::tr("The selected profile could not be copied into "
                                "the active body.")
                );
                return;
            }
            auto oBody = PartDesignGui::getBodyFor(sketches[0], false);
            if (oBody) {
                pcActiveBody->addObject(copy);
            }
            else if (pcActivePart) {
                pcActivePart->addObject(copy);
            }

            sketches[0] = copy;
            firstFreeSketch = sketches.begin();
        }
    }

    // Show sketch choose dialog and let user pick sketch if no sketch was selected and no free one
    // available or multiple free ones are available
    if (bNoSketchWasSelected && (freeSketches != 1)) {

        App::Document* targetDocument = cmd->getDocument();
        if (!targetDocument) {
            return;
        }
        Gui::TaskView::TaskDialog* dlg = Gui::Control().activeDialog(targetDocument);
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
                Gui::Control().closeDialog(targetDocument);
            }
            else {
                return;
            }
        }

        if (dlg) {
            Gui::Control().closeDialog(targetDocument);
        }

        Gui::Selection().clearSelection(targetDocument->getName());
        pickDlg = new PartDesignGui::TaskDlgFeaturePick(
            sketches,
            status,
            accepter,
            sketch_worker,
            true,
            {},
            pcActiveBody
        );
        // Logically dead code because 'bNoSketchWasSelected' must be true
        // if (!bNoSketchWasSelected && extReference)
        //    pickDlg->showExternal(true);

        Gui::Control().showDialog(pickDlg, targetDocument);
    }
    else {
        std::vector<App::DocumentObject*> theSketch;
        if (!bNoSketchWasSelected) {
            theSketch.push_back(sketches[0]);
        }
        else {
            theSketch.push_back(*firstFreeSketch);
        }

        sketch_worker(theSketch);
    }
}

void finishProfileBased(Gui::Command* cmd, const Part::Feature* sketch, App::DocumentObject* Feat)
{
    if (sketch && sketch->isDerivedFrom<Part::Part2DObject>()) {
        FCMD_OBJ_HIDE(sketch);
    }
    finishFeature(cmd, Feat);
}

void prepareProfileBased(Gui::Command* cmd, const std::string& which, double length)
{
    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    auto worker = [cmd, length](Part::Feature* profile, App::DocumentObject* Feat) {
        if (!Feat) {
            return;
        }

        // specific parameters for Pad/Pocket
        FCMD_OBJ_CMD(Feat, "Length = " << length);
        Gui::Command::updateActive();

        Part::Part2DObject* sketch = dynamic_cast<Part::Part2DObject*>(profile);

        if (sketch) {
            std::ostringstream str;
            Gui::cmdAppObject(
                Feat,
                str << "ReferenceAxis = (" << Gui::Command::getObjectCmd(sketch) << ",['N_Axis'])"
            );
        }

        finishProfileBased(cmd, sketch, Feat);
    };

    prepareProfileBased(pcActiveBody, cmd, which, worker);
}

namespace
{

struct DesignProfileSelection
{
    Part::Part2DObject* profile {};
    App::Part* destinationComponent {};
    std::vector<PartDesign::Body*> bodies;
    bool valid {true};
};

void addDesignTargetSelection(DesignProfileSelection& result, App::DocumentObject* object)
{
    if (auto* component = freecad_cast<App::Part*>(object);
        component && component->Type.getStrValue() == "Component") {
        if (result.destinationComponent && result.destinationComponent != component) {
            result.valid = false;
        }
        result.destinationComponent = component;
        return;
    }

    auto* body = freecad_cast<PartDesign::Body*>(object);
    if (!body) {
        body = freecad_cast<PartDesign::Body*>(PartGui::findModelingBody(object));
    }
    if (!body) {
        result.valid = false;
        return;
    }
    if (std::ranges::find(result.bodies, body) == result.bodies.end()) {
        result.bodies.push_back(body);
    }
}

DesignProfileSelection selectedDesignProfile()
{
    DesignProfileSelection result;
    App::Document* document = nullptr;
    for (auto& selected : Gui::Selection().getSelectionEx()) {
        auto* object = selected.getObject();
        if (!object || !PartGui::isModelingObjectActive(object)) {
            result.valid = false;
            continue;
        }
        if (!document) {
            document = object->getDocument();
        }
        if (object->getDocument() != document) {
            result.valid = false;
            continue;
        }

        if (auto* profile = freecad_cast<Part::Part2DObject*>(object)) {
            if (result.profile && result.profile != profile) {
                result.valid = false;
            }
            result.profile = profile;
            continue;
        }
        addDesignTargetSelection(result, object);
    }
    return result;
}

template<typename Operation, typename Configure>
void startConfiguredDesignProfileOperation(
    Gui::Command& command,
    const char* typeName,
    const char* objectName,
    const char* transactionLabel,
    const DesignProfileSelection& selected,
    Configure&& configure
)
{
    if (!selected.valid || !selected.profile) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Sketch required"),
            QObject::tr("Select exactly one sketch. Bodies may also be selected to "
                        "initialize the explicit target list.")
        );
        return;
    }
    auto* document = selected.profile->getDocument();
    if (!document || Gui::Control().activeDialog(document)) {
        return;
    }

    const int transactionId = command.openCommand(document, transactionLabel);
    if (transactionId == App::NullTransaction) {
        command.resetTransactionID();
        return;
    }

    try {
        auto* operation = freecad_cast<Operation*>(
            createDocumentFeatureExact(document, typeName, document->getUniqueObjectName(objectName))
        );
        if (!operation) {
            throw Base::TypeError("The Design operation factory returned an incompatible object");
        }

        operation->Label.setValue(objectName);
        operation->Profile.setValue(selected.profile);

        if constexpr (std::is_same_v<Operation, PartDesign::DesignHole>) {
            PartDesign::DesignModel::setOperationTargets(
                *operation,
                "Cut",
                selected.bodies,
                nullptr,
                {},
                true
            );
        }
        else if (selected.bodies.empty()) {
            PartDesign::DesignModel::setOperationTargets(
                *operation,
                "New Body",
                {},
                selected.destinationComponent
            );
        }
        else {
            PartDesign::DesignModel::setOperationTargets(*operation, "Join", selected.bodies);
        }

        std::forward<Configure>(configure)(*operation, *selected.profile);

        operation->recomputeFeature();
        operation->recomputePreview();
        command.doCommand(
            Gui::Command::Doc,
            "Gui.getDocument('%s').setEdit('%s',0)",
            document->getName(),
            operation->getNameInDocument()
        );
        if (!Gui::Control().activeDialog(document)) {
            throw Base::RuntimeError("The Design operation task panel did not open");
        }
        Gui::Selection().clearSelection(document->getName());
    }
    catch (...) {
        command.abortCommand(transactionId);
        command.resetTransactionID();
        throw;
    }
}

template<typename Operation>
void startDesignProfileOperation(
    Gui::Command& command,
    const char* typeName,
    const char* objectName,
    const char* transactionLabel
)
{
    const auto selected = selectedDesignProfile();
    startConfiguredDesignProfileOperation<Operation>(
        command,
        typeName,
        objectName,
        transactionLabel,
        selected,
        [](Operation& operation, Part::Part2DObject& profile) {
            if constexpr (std::is_same_v<Operation, PartDesign::DesignExtrude>) {
                operation.ReferenceAxis.setValue(&profile, {"N_Axis"});
                operation.Length.setValue(10.0);
            }
            else if constexpr (std::is_same_v<Operation, PartDesign::DesignRevolve>) {
                operation.ReferenceAxis.setValue(&profile, {"V_Axis"});
                operation.Angle.setValue(360.0);
            }
            else if constexpr (std::is_same_v<Operation, PartDesign::DesignHelix>) {
                operation.ReferenceAxis.setValue(&profile, {"V_Axis"});
            }
        }
    );
}

struct DesignLoftSelection
{
    DesignProfileSelection common;
    std::vector<Part::Part2DObject*> sections;
};

DesignLoftSelection selectedDesignLoft()
{
    DesignLoftSelection result;
    App::Document* document = nullptr;
    for (auto& selected : Gui::Selection().getSelectionEx()) {
        auto* object = selected.getObject();
        if (!object || !PartGui::isModelingObjectActive(object)) {
            result.common.valid = false;
            continue;
        }
        if (!document) {
            document = object->getDocument();
        }
        if (object->getDocument() != document) {
            result.common.valid = false;
            continue;
        }

        if (auto* profile = freecad_cast<Part::Part2DObject*>(object)) {
            if (!result.common.profile) {
                result.common.profile = profile;
            }
            else if (profile != result.common.profile
                     && std::ranges::find(result.sections, profile) == result.sections.end()) {
                result.sections.push_back(profile);
            }
            continue;
        }
        addDesignTargetSelection(result.common, object);
    }
    result.common.valid = result.common.valid && result.common.profile && !result.sections.empty();
    return result;
}

struct DesignSweepSelection
{
    DesignProfileSelection common;
    Part::Feature* path {};
    std::vector<std::string> pathSubElements;
};

DesignSweepSelection selectedDesignSweep()
{
    DesignSweepSelection result;
    App::Document* document = nullptr;
    for (auto& selected : Gui::Selection().getSelectionEx()) {
        auto* object = selected.getObject();
        if (!object || !PartGui::isModelingObjectActive(object)) {
            result.common.valid = false;
            continue;
        }
        if (!document) {
            document = object->getDocument();
        }
        if (object->getDocument() != document) {
            result.common.valid = false;
            continue;
        }

        if (auto* body = freecad_cast<PartDesign::Body*>(object)) {
            addDesignTargetSelection(result.common, body);
            continue;
        }
        if (auto* component = freecad_cast<App::Part*>(object);
            component && component->Type.getStrValue() == "Component") {
            addDesignTargetSelection(result.common, component);
            continue;
        }

        if (!result.common.profile) {
            result.common.profile = freecad_cast<Part::Part2DObject*>(object);
            if (!result.common.profile) {
                result.common.valid = false;
            }
            continue;
        }

        auto* path = freecad_cast<Part::Feature*>(object);
        if (!path || (result.path && result.path != path)) {
            result.common.valid = false;
            continue;
        }
        result.path = path;
        for (const auto& subElement : selected.getSubNames()) {
            if (!subElement.empty()
                && std::ranges::find(result.pathSubElements, subElement)
                    == result.pathSubElements.end()) {
                result.pathSubElements.push_back(subElement);
            }
        }
    }
    result.common.valid = result.common.valid && result.common.profile && result.path;
    return result;
}

bool designProfileOperationActive()
{
    const auto selected = selectedDesignProfile();
    return PartDesignGui::canStartModelingCommand() && selected.valid && selected.profile;
}

bool designLoftOperationActive()
{
    return PartDesignGui::canStartModelingCommand() && selectedDesignLoft().common.valid;
}

bool designSweepOperationActive()
{
    return PartDesignGui::canStartModelingCommand() && selectedDesignSweep().common.valid;
}

}  // namespace

//===========================================================================
// PartDesign_DesignExtrude
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignDesignExtrude)

CmdPartDesignDesignExtrude::CmdPartDesignDesignExtrude()
    : Command("PartDesign_DesignExtrude")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Extrude");
    sToolTipText = QT_TR_NOOP(
        "Extrudes one reusable sketch as a new Body or applies it to explicit Bodies"
    );
    sWhatsThis = "PartDesign_DesignExtrude";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Pad";
}

void CmdPartDesignDesignExtrude::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignProfileOperation<PartDesign::DesignExtrude>(
        *this,
        "PartDesign::DesignExtrude",
        "Extrude",
        QT_TRANSLATE_NOOP("Command", "Create Extrude")
    );
}

bool CmdPartDesignDesignExtrude::isActive()
{
    return designProfileOperationActive();
}

//===========================================================================
// PartDesign_DesignRevolve
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignDesignRevolve)

CmdPartDesignDesignRevolve::CmdPartDesignDesignRevolve()
    : Command("PartDesign_DesignRevolve")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Revolve");
    sToolTipText = QT_TR_NOOP(
        "Revolves one reusable sketch as a new Body or applies it to explicit Bodies"
    );
    sWhatsThis = "PartDesign_DesignRevolve";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Revolution";
}

void CmdPartDesignDesignRevolve::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignProfileOperation<PartDesign::DesignRevolve>(
        *this,
        "PartDesign::DesignRevolve",
        "Revolve",
        QT_TRANSLATE_NOOP("Command", "Create Revolve")
    );
}

bool CmdPartDesignDesignRevolve::isActive()
{
    return designProfileOperationActive();
}

//===========================================================================
// PartDesign_DesignLoft
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignDesignLoft)

CmdPartDesignDesignLoft::CmdPartDesignDesignLoft()
    : Command("PartDesign_DesignLoft")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Loft");
    sToolTipText = QT_TR_NOOP("Creates one loft from ordered reusable sketches, then applies it "
                              "to explicit Bodies");
    sWhatsThis = "PartDesign_DesignLoft";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_AdditiveLoft";
}

void CmdPartDesignDesignLoft::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    const auto selected = selectedDesignLoft();
    if (!selected.common.valid) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Loft profiles required"),
            QObject::tr("Select two or more sketches in loft order. Bodies may also "
                        "be selected to initialize the explicit target list.")
        );
        return;
    }
    startConfiguredDesignProfileOperation<PartDesign::DesignLoft>(
        *this,
        "PartDesign::DesignLoft",
        "Loft",
        QT_TRANSLATE_NOOP("Command", "Create Loft"),
        selected.common,
        [&selected](PartDesign::DesignLoft& operation, Part::Part2DObject&) {
            std::vector<App::DocumentObject*> sections(
                selected.sections.begin(),
                selected.sections.end()
            );
            operation.Sections.setValues(sections, std::vector<const char*>(sections.size(), ""));
        }
    );
}

bool CmdPartDesignDesignLoft::isActive()
{
    return designLoftOperationActive();
}

//===========================================================================
// PartDesign_DesignSweep
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignDesignSweep)

CmdPartDesignDesignSweep::CmdPartDesignDesignSweep()
    : Command("PartDesign_DesignSweep")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Sweep");
    sToolTipText = QT_TR_NOOP("Sweeps one reusable sketch along an explicit path, then applies "
                              "the result to explicit Bodies");
    sWhatsThis = "PartDesign_DesignSweep";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_AdditivePipe";
}

void CmdPartDesignDesignSweep::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    const auto selected = selectedDesignSweep();
    if (!selected.common.valid) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Sweep profile and path required"),
            QObject::tr("Select the profile sketch first and the path second. Bodies "
                        "may also be selected to initialize the explicit target list.")
        );
        return;
    }
    startConfiguredDesignProfileOperation<PartDesign::DesignSweep>(
        *this,
        "PartDesign::DesignSweep",
        "Sweep",
        QT_TRANSLATE_NOOP("Command", "Create Sweep"),
        selected.common,
        [&selected](PartDesign::DesignSweep& operation, Part::Part2DObject&) {
            operation.Spine.setValue(selected.path, selected.pathSubElements);
        }
    );
}

bool CmdPartDesignDesignSweep::isActive()
{
    return designSweepOperationActive();
}

//===========================================================================
// PartDesign_DesignHelix
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignDesignHelix)

CmdPartDesignDesignHelix::CmdPartDesignDesignHelix()
    : Command("PartDesign_DesignHelix")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Helix");
    sToolTipText = QT_TR_NOOP("Sweeps one reusable sketch along a parametric helix, then applies "
                              "the result to explicit Bodies");
    sWhatsThis = "PartDesign_DesignHelix";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_AdditiveHelix";
}

void CmdPartDesignDesignHelix::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignProfileOperation<PartDesign::DesignHelix>(
        *this,
        "PartDesign::DesignHelix",
        "Helix",
        QT_TRANSLATE_NOOP("Command", "Create Helix")
    );
}

bool CmdPartDesignDesignHelix::isActive()
{
    return designProfileOperationActive();
}

//===========================================================================
// PartDesign_Pad
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignPad)

CmdPartDesignPad::CmdPartDesignPad()
    : Command("PartDesign_Pad")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Extrude — Add Material");
    sToolTipText = QT_TR_NOOP("Extrudes the selected sketch or profile and adds it to the body");
    sWhatsThis = "PartDesign_Pad";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Pad";
}

void CmdPartDesignPad::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    prepareProfileBased(this, "Pad", 10.0);
}

bool CmdPartDesignPad::isActive()
{
    return isProfileCommandActive(false, ProfileCommandInput::Single);
}

//===========================================================================
// PartDesign_Pocket
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignPocket)

CmdPartDesignPocket::CmdPartDesignPocket()
    : Command("PartDesign_Pocket")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Extrude — Remove Material");
    sToolTipText = QT_TR_NOOP("Extrudes the selected sketch or profile and removes it from the body");
    sWhatsThis = "PartDesign_Pocket";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Pocket";
}

void CmdPartDesignPocket::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    prepareProfileBased(this, "Pocket", 5.0);
}

bool CmdPartDesignPocket::isActive()
{
    return isProfileCommandActive(true, ProfileCommandInput::Single);
}

//===========================================================================
// PartDesign_Hole
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignHole)

CmdPartDesignHole::CmdPartDesignHole()
    : Command("PartDesign_Hole")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Hole");
    sToolTipText = QT_TR_NOOP(
        "Cuts standard, counterbored, countersunk, or threaded holes from "
        "every explicit Body at the points and circle centers in one reusable sketch"
    );
    sWhatsThis = "PartDesign_Hole";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Hole";
}

void CmdPartDesignHole::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignProfileOperation<PartDesign::DesignHole>(
        *this,
        "PartDesign::DesignHole",
        "Hole",
        QT_TRANSLATE_NOOP("Command", "Create Hole")
    );
}

bool CmdPartDesignHole::isActive()
{
    return designProfileOperationActive();
}

//===========================================================================
// PartDesign_Revolution
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignRevolution)

CmdPartDesignRevolution::CmdPartDesignRevolution()
    : Command("PartDesign_Revolution")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Revolve — Add Material");
    sToolTipText = QT_TR_NOOP(
        "Revolves the selected sketch or profile around a line or axis and adds it to the body"
    );
    sWhatsThis = "PartDesign_Revolution";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Revolution";
}

void CmdPartDesignRevolution::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd](Part::Feature* sketch, App::DocumentObject* Feat) {
        if (!Feat) {
            return;
        }
        auto* body = PartDesign::Body::findBodyOf(Feat);
        if (!body) {
            return;
        }

        if (sketch->isDerivedFrom<Part::Part2DObject>()) {
            FCMD_OBJ_CMD(Feat, "ReferenceAxis = (" << getObjectCmd(sketch) << ",['V_Axis'])");
        }
        else {
            FCMD_OBJ_CMD(
                Feat,
                "ReferenceAxis = (" << getObjectCmd(body->getOrigin()->getY()) << ",[''])"
            );
        }

        FCMD_OBJ_CMD(Feat, "Angle = 360.0");
        PartDesign::Revolution* pcRevolution = dynamic_cast<PartDesign::Revolution*>(Feat);
        if (pcRevolution && pcRevolution->suggestReversed()) {
            FCMD_OBJ_CMD(Feat, "Reversed = 1");
        }

        finishProfileBased(cmd, sketch, Feat);
    };

    prepareProfileBased(pcActiveBody, this, "Revolution", worker);
}

bool CmdPartDesignRevolution::isActive()
{
    return isProfileCommandActive(false, ProfileCommandInput::Single);
}

//===========================================================================
// PartDesign_Groove
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignGroove)

CmdPartDesignGroove::CmdPartDesignGroove()
    : Command("PartDesign_Groove")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Revolve — Remove Material");
    sToolTipText = QT_TR_NOOP(
        "Revolves the sketch or profile around a line or axis and removes it from the body"
    );
    sWhatsThis = "PartDesign_Groove";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Groove";
}

void CmdPartDesignGroove::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd](Part::Feature* sketch, App::DocumentObject* Feat) {
        if (!Feat) {
            return;
        }
        auto* body = PartDesign::Body::findBodyOf(Feat);
        if (!body) {
            return;
        }

        if (sketch->isDerivedFrom<Part::Part2DObject>()) {
            FCMD_OBJ_CMD(Feat, "ReferenceAxis = (" << getObjectCmd(sketch) << ",['V_Axis'])");
        }
        else {
            FCMD_OBJ_CMD(
                Feat,
                "ReferenceAxis = (" << getObjectCmd(body->getOrigin()->getY()) << ",[''])"
            );
        }

        FCMD_OBJ_CMD(Feat, "Angle = 360.0");

        try {
            // This raises as exception if line is perpendicular to sketch/support face.
            // Here we should continue to give the user a chance to change the default values.
            PartDesign::Groove* pcGroove = dynamic_cast<PartDesign::Groove*>(Feat);
            if (pcGroove && pcGroove->suggestReversed()) {
                FCMD_OBJ_CMD(Feat, "Reversed = 1");
            }
        }
        catch (const Base::Exception& e) {
            e.reportException();
        }

        finishProfileBased(cmd, sketch, Feat);
    };

    prepareProfileBased(pcActiveBody, this, "Groove", worker);
}

bool CmdPartDesignGroove::isActive()
{
    return isProfileCommandActive(true, ProfileCommandInput::Single);
}

//===========================================================================
// PartDesign_AdditivePipe
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignAdditivePipe)

CmdPartDesignAdditivePipe::CmdPartDesignAdditivePipe()
    : Command("PartDesign_AdditivePipe")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Sweep — Add Material");
    sToolTipText = QT_TR_NOOP(
        "Sweeps the selected sketch or profile along a path and adds it to the body"
    );
    sWhatsThis = "PartDesign_AdditivePipe";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_AdditivePipe";
}

void CmdPartDesignAdditivePipe::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd](Part::Feature* sketch, App::DocumentObject* Feat) {
        if (!Feat) {
            return;
        }

        // specific parameters for pipe
        Gui::Command::updateActive();

        finishProfileBased(cmd, sketch, Feat);
    };

    prepareProfileBased(pcActiveBody, this, "AdditivePipe", worker);
}

bool CmdPartDesignAdditivePipe::isActive()
{
    return isProfileCommandActive(false, ProfileCommandInput::Pipe);
}


//===========================================================================
// PartDesign_SubtractivePipe
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignSubtractivePipe)

CmdPartDesignSubtractivePipe::CmdPartDesignSubtractivePipe()
    : Command("PartDesign_SubtractivePipe")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Sweep — Remove Material");
    sToolTipText = QT_TR_NOOP(
        "Sweeps the selected sketch or profile along a path and removes it from the body"
    );
    sWhatsThis = "PartDesign_SubtractivePipe";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_SubtractivePipe";
}

void CmdPartDesignSubtractivePipe::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd](Part::Feature* sketch, App::DocumentObject* Feat) {
        if (!Feat) {
            return;
        }

        // specific parameters for pipe
        Gui::Command::updateActive();

        finishProfileBased(cmd, sketch, Feat);
    };

    prepareProfileBased(pcActiveBody, this, "SubtractivePipe", worker);
}

bool CmdPartDesignSubtractivePipe::isActive()
{
    return isProfileCommandActive(true, ProfileCommandInput::Pipe);
}


//===========================================================================
// PartDesign_AdditiveLoft
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignAdditiveLoft)

CmdPartDesignAdditiveLoft::CmdPartDesignAdditiveLoft()
    : Command("PartDesign_AdditiveLoft")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Loft — Add Material");
    sToolTipText = QT_TR_NOOP(
        "Lofts the selected sketch or profile along a path and adds it to the body"
    );
    sWhatsThis = "PartDesign_AdditiveLoft";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_AdditiveLoft";
}

void CmdPartDesignAdditiveLoft::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd](Part::Feature* sketch, App::DocumentObject* Feat) {
        if (!Feat) {
            return;
        }

        // specific parameters for pipe
        Gui::Command::updateActive();

        finishProfileBased(cmd, sketch, Feat);
    };

    prepareProfileBased(pcActiveBody, this, "AdditiveLoft", worker);
}

bool CmdPartDesignAdditiveLoft::isActive()
{
    return isProfileCommandActive(false, ProfileCommandInput::Loft);
}


//===========================================================================
// PartDesign_SubtractiveLoft
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignSubtractiveLoft)

CmdPartDesignSubtractiveLoft::CmdPartDesignSubtractiveLoft()
    : Command("PartDesign_SubtractiveLoft")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Loft — Remove Material");
    sToolTipText = QT_TR_NOOP(
        "Lofts the selected sketch or profile along a path and removes it from the body"
    );
    sWhatsThis = "PartDesign_SubtractiveLoft";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_SubtractiveLoft";
}

void CmdPartDesignSubtractiveLoft::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd](Part::Feature* sketch, App::DocumentObject* Feat) {
        if (!Feat) {
            return;
        }

        // specific parameters for pipe
        Gui::Command::updateActive();

        finishProfileBased(cmd, sketch, Feat);
    };

    prepareProfileBased(pcActiveBody, this, "SubtractiveLoft", worker);
}

bool CmdPartDesignSubtractiveLoft::isActive()
{
    return isProfileCommandActive(true, ProfileCommandInput::Loft);
}

//===========================================================================
// PartDesign_AdditiveHelix
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignAdditiveHelix)

CmdPartDesignAdditiveHelix::CmdPartDesignAdditiveHelix()
    : Command("PartDesign_AdditiveHelix")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Helix Sweep — Add Material");
    sToolTipText = QT_TR_NOOP(
        "Sweeps the selected sketch or profile along a helix and adds it to the body"
    );
    sWhatsThis = "PartDesign_AdditiveHelix";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_AdditiveHelix";
}

void CmdPartDesignAdditiveHelix::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd](Part::Feature* sketch, App::DocumentObject* Feat) {
        if (!Feat) {
            return;
        }
        auto* body = PartDesign::Body::findBodyOf(Feat);
        if (!body) {
            return;
        }

        // Creating a helix with default values isn't always valid but fixes
        // itself when more values are set. So, this guard is used to suppress
        // errors before the user is able to change the parameters.
        Base::ObjectStatusLocker<App::Document::Status, App::Document> guard(
            App::Document::IgnoreErrorOnRecompute,
            Feat->getDocument(),
            true
        );

        // specific parameters for helix
        Gui::Command::updateActive();

        if (sketch->isDerivedFrom<Part::Part2DObject>()) {
            FCMD_OBJ_CMD(Feat, "ReferenceAxis = (" << getObjectCmd(sketch) << ",['V_Axis'])");
        }
        else {
            FCMD_OBJ_CMD(
                Feat,
                "ReferenceAxis = (" << getObjectCmd(body->getOrigin()->getY()) << ",[''])"
            );
        }

        finishProfileBased(cmd, sketch, Feat);

        // If the initial helix creation fails then it leaves the base object invisible which makes
        // things more difficult for the user. To avoid this the base object will be made tmp.
        // visible again.
        if (Feat->isError()) {
            App::DocumentObject* base = static_cast<PartDesign::Feature*>(Feat)->BaseFeature.getValue(
            );
            if (base) {
                PartDesignGui::ViewProvider* view = dynamic_cast<PartDesignGui::ViewProvider*>(
                    Gui::Application::Instance->getViewProvider(base)
                );
                if (view) {
                    view->makeTemporaryVisible(true);
                }
            }
        }
    };

    prepareProfileBased(pcActiveBody, this, "AdditiveHelix", worker);
}

bool CmdPartDesignAdditiveHelix::isActive()
{
    return isProfileCommandActive(false, ProfileCommandInput::Single);
}


//===========================================================================
// PartDesign_SubtractiveHelix
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignSubtractiveHelix)

CmdPartDesignSubtractiveHelix::CmdPartDesignSubtractiveHelix()
    : Command("PartDesign_SubtractiveHelix")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Helix Sweep — Remove Material");
    sToolTipText = QT_TR_NOOP(
        "Sweeps the selected sketch or profile along a helix and removes it from the body"
    );
    sWhatsThis = "PartDesign_SubtractiveHelix";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_SubtractiveHelix";
}

void CmdPartDesignSubtractiveHelix::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd](Part::Feature* sketch, App::DocumentObject* Feat) {
        if (!Feat) {
            return;
        }
        auto* body = PartDesign::Body::findBodyOf(Feat);
        if (!body) {
            return;
        }

        // A helix has no usable default axis. Set the reference before the first
        // recompute so a valid profile never produces a transient zero-direction
        // preview.
        if (sketch->isDerivedFrom<Part::Part2DObject>()) {
            FCMD_OBJ_CMD(Feat, "ReferenceAxis = (" << getObjectCmd(sketch) << ",['V_Axis'])");
        }
        else {
            FCMD_OBJ_CMD(
                Feat,
                "ReferenceAxis = (" << getObjectCmd(body->getOrigin()->getY()) << ",[''])"
            );
        }

        Gui::Command::updateActive();
        finishProfileBased(cmd, sketch, Feat);
    };

    prepareProfileBased(pcActiveBody, this, "SubtractiveHelix", worker);
}

bool CmdPartDesignSubtractiveHelix::isActive()
{
    return isProfileCommandActive(true, ProfileCommandInput::Single);
}

//===========================================================================
// Common utility functions for Dressup features
//===========================================================================

namespace
{

struct DesignDressupSelection
{
    App::Document* document {};
    std::vector<PartDesign::Body*> bodies;
    std::vector<std::vector<std::string>> elementGroups;
    bool valid {true};
};

enum class DesignDressupSelectionKind
{
    EdgesOrFaces,
    Faces,
    DraftFaces,
};

DesignDressupSelection selectedDesignDressup(DesignDressupSelectionKind selectionKind)
{
    DesignDressupSelection result;
    for (auto& selected : Gui::Selection().getSelectionEx()) {
        auto* object = selected.getObject();
        if (!object || !PartGui::isModelingObjectActive(object) || selected.getSubNames().empty()) {
            result.valid = false;
            continue;
        }
        if (!result.document) {
            result.document = object->getDocument();
        }
        if (object->getDocument() != result.document) {
            result.valid = false;
            continue;
        }

        auto* body = freecad_cast<PartDesign::Body*>(object);
        if (!body) {
            body = freecad_cast<PartDesign::Body*>(PartGui::findModelingBody(object));
        }
        auto* state = PartDesign::designBodyStateBefore(body, nullptr);
        if (!body || !state || !state->isValid()) {
            result.valid = false;
            continue;
        }

        auto found = std::ranges::find(result.bodies, body);
        std::size_t index = 0;
        if (found == result.bodies.end()) {
            result.bodies.push_back(body);
            result.elementGroups.emplace_back();
            index = result.bodies.size() - 1;
        }
        else {
            index = static_cast<std::size_t>(std::distance(result.bodies.begin(), found));
        }
        auto& group = result.elementGroups[index];
        for (const auto& subElement : selected.getSubNames()) {
            try {
                const bool facesOnly = selectionKind != DesignDressupSelectionKind::EdgesOrFaces;
                const auto resolved = facesOnly
                    ? PartDesign::resolveDesignTargetFaces(state->Shape.getShape(), {subElement})
                    : PartDesign::resolveDesignTargetEdges(state->Shape.getShape(), {subElement}, false);
                if (resolved.empty()) {
                    result.valid = false;
                    continue;
                }
                if (selectionKind == DesignDressupSelectionKind::DraftFaces) {
                    BRepAdaptor_Surface surface(TopoDS::Face(resolved.front().getShape()));
                    if (surface.GetType() != GeomAbs_Plane && surface.GetType() != GeomAbs_Cylinder
                        && surface.GetType() != GeomAbs_Cone) {
                        result.valid = false;
                        continue;
                    }
                }
                if (std::ranges::find(group, subElement) == group.end()) {
                    group.push_back(subElement);
                }
            }
            catch (const Base::Exception&) {
                result.valid = false;
            }
        }
    }

    result.valid = result.valid && result.document && !result.bodies.empty()
        && result.bodies.size() == result.elementGroups.size()
        && std::ranges::all_of(result.elementGroups, [](const auto& group) { return !group.empty(); });
    return result;
}

template<typename Operation>
void startDesignDressupOperation(
    Gui::Command& command,
    const char* typeName,
    const char* objectName,
    const char* transactionLabel,
    DesignDressupSelectionKind selectionKind
)
{
    const auto selected = selectedDesignDressup(selectionKind);
    if (!selected.valid) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            selectionKind != DesignDressupSelectionKind::EdgesOrFaces
                ? QObject::tr("Faces required")
                : QObject::tr("Edges or faces required"),
            selectionKind != DesignDressupSelectionKind::EdgesOrFaces
                ? QObject::tr("Select one or more supported faces. Selections may "
                              "belong to multiple Bodies in this Design.")
                : QObject::tr("Select one or more dressable edges or faces. "
                              "Selections may belong to multiple Bodies in this "
                              "Design.")
        );
        return;
    }
    if (Gui::Control().activeDialog(selected.document)) {
        return;
    }

    const int transactionId = command.openCommand(selected.document, transactionLabel);
    if (transactionId == App::NullTransaction) {
        command.resetTransactionID();
        return;
    }

    try {
        auto* operation = freecad_cast<Operation*>(createDocumentFeatureExact(
            selected.document,
            typeName,
            selected.document->getUniqueObjectName(objectName)
        ));
        if (!operation) {
            throw Base::TypeError("The Design dress-up factory returned an incompatible object");
        }
        operation->Label.setValue(objectName);
        PartDesign::DesignModel::setOperationTargets(*operation, "Modify", selected.bodies);
        operation->setTargetElementGroups(selected.elementGroups);
        operation->recomputeFeature();
        operation->recomputePreview();

        command.doCommand(
            Gui::Command::Doc,
            "Gui.getDocument('%s').setEdit('%s',0)",
            selected.document->getName(),
            operation->getNameInDocument()
        );
        if (!Gui::Control().activeDialog(selected.document)) {
            throw Base::RuntimeError("The Design dress-up task panel did not open");
        }
        Gui::Selection().clearSelection(selected.document->getName());
    }
    catch (...) {
        command.abortCommand(transactionId);
        command.resetTransactionID();
        throw;
    }
}

bool designDressupOperationActive(DesignDressupSelectionKind selectionKind)
{
    return PartDesignGui::canStartModelingCommand() && selectedDesignDressup(selectionKind).valid;
}

}  // namespace

bool dressupGetSelected(
    Gui::Command* cmd,
    const std::string& which,
    Part::Feature*& base,
    std::vector<std::string>& subNames,
    bool& useAllEdges
)
{
    Q_UNUSED(cmd);

    base = nullptr;
    subNames.clear();
    useAllEdges = false;

    if (Gui::Control().activeDialog()) {
        return false;
    }

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody || !usableSolidTip(pcActiveBody)) {
        return false;
    }

    auto selection
        = PartGui::getModelingSelectionForBody(pcActiveBody, pcActiveBody->getDocument()->getName());

    if (selection.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Selection required"),
            which == "Draft" || which == "Thickness"
                ? QObject::tr("Select at least one face before starting this tool.")
                : QObject::tr("Select at least one edge or face before starting this tool.")
        );
        return false;
    }
    else if (selection.size() != 1) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("Select an edge, face, or body from a single body.")
        );
        return false;
    }
    auto* selectedObject = selection.front().getObject();
    auto* selectedBody = freecad_cast<PartDesign::Body*>(selectedObject);
    if (!selectedBody) {
        selectedBody = PartDesignGui::getBodyFor(selectedObject, false);
    }
    if (pcActiveBody != selectedBody) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Selection is not in the active body"),
            QObject::tr("Select an edge, face, or body from an active body.")
        );
        return false;
    }

    base = freecad_cast<Part::Feature*>(resolveBodyResultSelection(selection.front(), pcActiveBody));
    if (!base) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong object type"),
            QObject::tr("%1 works only on parts.").arg(QString::fromStdString(which))
        );
        return false;
    }

    const Part::TopoShape& TopShape = base->Shape.getShape();

    if (TopShape.getShape().IsNull()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("Shape of the selected part is empty")
        );
        return false;
    }

    subNames = selection.front().getSubNames();
    if (subNames.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            which == "Draft" || which == "Thickness"
                ? QObject::tr("Select at least one face before starting this tool.")
                : QObject::tr("Select at least one edge or face before starting this tool.")
        );
        return false;
    }

    const bool facesOnly = which == "Draft" || which == "Thickness";
    for (const auto& subName : subNames) {
        const auto subShape = TopShape.getSubShape(subName.c_str());
        const bool isFace = !subShape.IsNull() && subShape.ShapeType() == TopAbs_FACE;
        const bool isEdge = !subShape.IsNull() && subShape.ShapeType() == TopAbs_EDGE;
        if (!isFace && (facesOnly || !isEdge)) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Wrong selection"),
                facesOnly ? QObject::tr("Select only faces.")
                          : QObject::tr("Select only edges or faces.")
            );
            return false;
        }
    }
    return true;
}

void finishDressupFeature(
    Gui::Command* cmd,
    const std::string& which,
    Part::Feature* base,
    const std::vector<std::string>& SubNames,
    const bool useAllEdges
)
{
    if (!base || SubNames.empty()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Selection required"),
            QObject::tr("Select valid geometry before starting this tool.")
        );
        return;
    }

    std::ostringstream str;
    str << '(' << Gui::Command::getObjectCmd(base) << ",[";
    for (const auto& SubName : SubNames) {
        str << "'" << SubName << "',";
    }
    str << "])";

    std::string FeatName = cmd->getUniqueObjectName(which.c_str(), base);

    auto body = PartDesignGui::getBodyFor(base, false);
    if (!body) {
        return;
    }
    cmd->openCommand(body->getDocument(), std::string("Make ") + which);
    auto* Feat = createBodyFeatureExact(body, "PartDesign::" + which, FeatName);
    auto* dressup = freecad_cast<PartDesign::DressUp*>(Feat);
    if (!dressup) {
        cmd->abortCommand();
        throw Base::TypeError("The exact dress-up factory returned an incompatible feature");
    }
    FCMD_OBJ_CMD(Feat, "Base = " << str.str());
    if (useAllEdges && (which.compare("Fillet") == 0 || which.compare("Chamfer") == 0)) {
        FCMD_OBJ_CMD(Feat, "UseAllEdges = True");
    }
    Gui::Selection().clearSelection(body->getDocument()->getName());
    finishFeature(cmd, Feat, base);

    App::DocumentObject* baseFeature = dressup->Base.getValue();
    if (baseFeature) {
        PartDesignGui::ViewProvider* view = dynamic_cast<PartDesignGui::ViewProvider*>(
            Gui::Application::Instance->getViewProvider(baseFeature)
        );
        // in case there is an error, for example when a fillet is larger than the available space
        // display the base feature to avoid that the user sees nothing
        if (view && Feat->isError()) {
            view->Visibility.setValue(true);
        }
    }
}

void makeChamferOrFillet(Gui::Command* cmd, const std::string& which)
{
    bool useAllEdges = false;
    Part::Feature* base = nullptr;
    std::vector<std::string> subNames;
    if (!dressupGetSelected(cmd, which, base, subNames, useAllEdges)) {
        return;
    }

    finishDressupFeature(cmd, which, base, subNames, useAllEdges);
}

//===========================================================================
// PartDesign_Fillet
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignFillet)

CmdPartDesignFillet::CmdPartDesignFillet()
    : Command("PartDesign_Fillet")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Fillet");
    sToolTipText = QT_TR_NOOP("Applies a fillet to the selected edges or faces");
    sWhatsThis = "PartDesign_Fillet";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Fillet";
}

void CmdPartDesignFillet::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignDressupOperation<PartDesign::DesignFillet>(
        *this,
        "PartDesign::DesignFillet",
        "Fillet",
        QT_TRANSLATE_NOOP("Command", "Create Fillet"),
        DesignDressupSelectionKind::EdgesOrFaces
    );
}

bool CmdPartDesignFillet::isActive()
{
    return designDressupOperationActive(DesignDressupSelectionKind::EdgesOrFaces);
}

//===========================================================================
// PartDesign_Chamfer
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignChamfer)

CmdPartDesignChamfer::CmdPartDesignChamfer()
    : Command("PartDesign_Chamfer")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Chamfer");
    sToolTipText = QT_TR_NOOP("Applies a chamfer to the selected edges or faces");
    sWhatsThis = "PartDesign_Chamfer";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Chamfer";
}

void CmdPartDesignChamfer::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignDressupOperation<PartDesign::DesignChamfer>(
        *this,
        "PartDesign::DesignChamfer",
        "Chamfer",
        QT_TRANSLATE_NOOP("Command", "Create Chamfer"),
        DesignDressupSelectionKind::EdgesOrFaces
    );
}

bool CmdPartDesignChamfer::isActive()
{
    return designDressupOperationActive(DesignDressupSelectionKind::EdgesOrFaces);
}

//===========================================================================
// PartDesign_Draft
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignDraft)

CmdPartDesignDraft::CmdPartDesignDraft()
    : Command("PartDesign_Draft")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Draft");
    sToolTipText = QT_TR_NOOP("Applies a draft to the selected faces");
    sWhatsThis = "PartDesign_Draft";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Draft";
}

void CmdPartDesignDraft::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignDressupOperation<PartDesign::DesignDraft>(
        *this,
        "PartDesign::DesignDraft",
        "Draft",
        QT_TRANSLATE_NOOP("Command", "Create Draft"),
        DesignDressupSelectionKind::DraftFaces
    );
}

bool CmdPartDesignDraft::isActive()
{
    return designDressupOperationActive(DesignDressupSelectionKind::DraftFaces);
}


//===========================================================================
// PartDesign_Thickness
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignThickness)

CmdPartDesignThickness::CmdPartDesignThickness()
    : Command("PartDesign_Thickness")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Thickness");
    sToolTipText = QT_TR_NOOP("Applies thickness and removes the selected faces");
    sWhatsThis = "PartDesign_Thickness";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Thickness";
}

void CmdPartDesignThickness::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignDressupOperation<PartDesign::DesignThickness>(
        *this,
        "PartDesign::DesignThickness",
        "Thickness",
        QT_TRANSLATE_NOOP("Command", "Create Thickness"),
        DesignDressupSelectionKind::Faces
    );
}

bool CmdPartDesignThickness::isActive()
{
    return designDressupOperationActive(DesignDressupSelectionKind::Faces);
}

//===========================================================================
// Common functions for all Transformed features
//===========================================================================

void prepareTransformed(
    PartDesign::Body* pcActiveBody,
    Gui::Command* cmd,
    const std::string& which,
    std::function<void(App::DocumentObject*, std::vector<App::DocumentObject*>)> func
)
{
    const BodyIdentity targetBodyIdentity = bodyIdentity(pcActiveBody);
    std::string FeatName = cmd->getUniqueObjectName(which.c_str(), pcActiveBody);

    auto worker = [=](std::vector<App::DocumentObject*> features) {
        auto* targetBody = resolveBody(targetBodyIdentity);
        if (!targetBody) {
            throw Base::RuntimeError("The active Body changed before the transform could be created");
        }
        std::string msg("Make ");
        msg += which;
        cmd->openCommand(targetBody->getDocument(), msg);
        auto* Feat = createBodyFeatureExact(targetBody, "PartDesign::" + which, FeatName);
        const long featureId = Feat->getID();
        const std::string exactFeatureName = Feat->getNameInDocument();
        const auto refreshCreatedFeature = [&]() {
            targetBody = resolveBody(targetBodyIdentity);
            Feat = targetBody ? targetBody->getDocument()->getObjectByID(featureId) : nullptr;
            if (!targetBody || !Feat || !Feat->isAttachedToDocument() || !Feat->getNameInDocument()
                || exactFeatureName != Feat->getNameInDocument()
                || PartDesign::Body::findBodyOf(Feat) != targetBody) {
                throw Base::RuntimeError(
                    "The exact transformed feature changed during command execution"
                );
            }
        };
        refreshCreatedFeature();

        if (features.empty()) {
            FCMD_OBJ_CMD(Feat, "TransformMode = \"Whole shape\"");
        }
        else {
            std::stringstream str;
            str << "Originals = [";
            for (auto feature : features) {
                str << cmd->getObjectCmd(feature) << ",";
            }
            str << "]";
            FCMD_OBJ_CMD(Feat, str.str().c_str());
        }
        refreshCreatedFeature();

        // TODO What is this function supposed to do? (2015-08-05, Fat-Zer)
        func(Feat, features);
        refreshCreatedFeature();

        // Set the tip of the body
        FCMD_OBJ_CMD(targetBody, "Tip = " << Gui::Command::getObjectCmd(Feat));
        Gui::Command::updateActive();
        refreshCreatedFeature();
    };

    // Feature mode can transform only additive/subtractive Part Design deltas. A selected Body or
    // any other result feature means "transform the completed Body shape", including ordinary
    // Part::Feature results adopted by the consolidated workbench.
    std::vector<App::DocumentObject*> features;
    bool wholeShape = false;
    auto selection = cmd->getSelection().getSelectionEx();
    for (auto& selected : selection) {
        auto* selectedObject = selected.getObject();
        if (!selectedObject) {
            continue;
        }
        if (!PartGui::isModelingObjectActive(selectedObject)) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Selection is not in the current History state"),
                QObject::tr(
                    "Move History after the selected object or choose an active result feature."
                )
            );
            return;
        }

        auto* selectedBody = freecad_cast<PartDesign::Body*>(PartGui::findModelingBody(selectedObject)
        );
        if (selectedBody != pcActiveBody) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Selection is not in the active body"),
                QObject::tr("Select only a body or result features from the selected body.")
            );
            return;
        }

        auto* object = PartGui::resolveModelingObjectForBody(selectedObject, pcActiveBody);
        const bool selectedBodyPresentation = selectedObject == pcActiveBody
            || (!selectedObject->isDerivedFrom<Part::Feature>()
                && object == pcActiveBody->Tip.getValue());
        if (selectedBodyPresentation) {
            wholeShape = true;
        }
        else if (object->isDerivedFrom<PartDesign::FeatureAddSub>()) {
            if (std::ranges::find(features, object) == features.end()) {
                features.push_back(object);
            }
        }
        else if (object->isDerivedFrom<Part::Feature>()
                 && !object->isDerivedFrom<Part::Part2DObject>()) {
            wholeShape = true;
        }
        else {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Wrong selection"),
                QObject::tr("Select a body or a solid result feature to transform.")
            );
            return;
        }
    }
    if (wholeShape) {
        features.clear();
    }
    worker(features);
}

void finishTransformed(Gui::Command* cmd, App::DocumentObject* Feat)
{
    finishFeature(cmd, Feat);
}

//===========================================================================
// PartDesign_Mirrored
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignMirrored)

CmdPartDesignMirrored::CmdPartDesignMirrored()
    : Command("PartDesign_Mirrored")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Mirror");
    sToolTipText = QT_TR_NOOP("Mirrors the selected features or active body");
    sWhatsThis = "PartDesign_Mirrored";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Mirrored";
}

void CmdPartDesignMirrored::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd,
                   pcActiveBody](App::DocumentObject* Feat, std::vector<App::DocumentObject*> features) {
        bool direction = false;
        if (!features.empty() && features.front()->isDerivedFrom<PartDesign::ProfileBased>()) {
            Part::Part2DObject* sketch = (static_cast<PartDesign::ProfileBased*>(features.front()))
                                             ->getVerifiedSketch(/* silent =*/true);
            if (sketch) {
                FCMD_OBJ_CMD(Feat, "MirrorPlane = (" << getObjectCmd(sketch) << ", ['V_Axis'])");
                direction = true;
            }
        }
        if (!direction) {
            FCMD_OBJ_CMD(
                Feat,
                "MirrorPlane = (" << getObjectCmd(pcActiveBody->getOrigin()->getXY()) << ", [''])"
            );
        }

        finishTransformed(cmd, Feat);
    };

    prepareTransformed(pcActiveBody, this, "Mirrored", worker);
}

bool CmdPartDesignMirrored::isActive()
{
    return isTransformCommandActive();
}

//===========================================================================
// PartDesign_LinearPattern
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignLinearPattern)

CmdPartDesignLinearPattern::CmdPartDesignLinearPattern()
    : Command("PartDesign_LinearPattern")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Linear Pattern");
    sToolTipText = QT_TR_NOOP(
        "Duplicates the selected features or the active body in a linear pattern"
    );
    sWhatsThis = "PartDesign_LinearPattern";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_LinearPattern";
}

void CmdPartDesignLinearPattern::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker =
        [cmd, pcActiveBody](App::DocumentObject* Feat, std::vector<App::DocumentObject*> features) {
            bool direction = false;
            if (!features.empty() && features.front()->isDerivedFrom<PartDesign::ProfileBased>()) {
                Part::Part2DObject* sketch = (static_cast<PartDesign::ProfileBased*>(features.front()))
                                                 ->getVerifiedSketch(/* silent =*/true);
                if (sketch) {
                    FCMD_OBJ_CMD(
                        Feat,
                        "Direction = (" << Gui::Command::getObjectCmd(sketch) << ", ['H_Axis'])"
                    );
                    FCMD_OBJ_CMD(
                        Feat,
                        "Direction2 = (" << Gui::Command::getObjectCmd(sketch) << ", ['V_Axis'])"
                    );
                    direction = true;
                }
            }
            if (!direction) {
                FCMD_OBJ_CMD(
                    Feat,
                    "Direction = (" << Gui::Command::getObjectCmd(pcActiveBody->getOrigin()->getX())
                                    << ",[''])"
                );
            }
            FCMD_OBJ_CMD(Feat, "Length = 100");
            FCMD_OBJ_CMD(Feat, "Occurrences = 2");

            finishTransformed(cmd, Feat);
        };

    prepareTransformed(pcActiveBody, this, "LinearPattern", worker);
}

bool CmdPartDesignLinearPattern::isActive()
{
    return isTransformCommandActive();
}

//===========================================================================
// PartDesign_PolarPattern
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignPolarPattern)

CmdPartDesignPolarPattern::CmdPartDesignPolarPattern()
    : Command("PartDesign_PolarPattern")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Polar Pattern");
    sToolTipText = QT_TR_NOOP(
        "Duplicates the selected features or the active body in a circular pattern"
    );
    sWhatsThis = "PartDesign_PolarPattern";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_PolarPattern";
}

void CmdPartDesignPolarPattern::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd,
                   pcActiveBody](App::DocumentObject* Feat, std::vector<App::DocumentObject*> features) {
        bool direction = false;
        if (!features.empty() && features.front()->isDerivedFrom<PartDesign::ProfileBased>()) {
            Part::Part2DObject* sketch = (static_cast<PartDesign::ProfileBased*>(features.front()))
                                             ->getVerifiedSketch(/* silent =*/true);
            if (sketch) {
                FCMD_OBJ_CMD(Feat, "Axis = (" << Gui::Command::getObjectCmd(sketch) << ",['N_Axis'])");
                direction = true;
            }
        }
        if (!direction) {
            FCMD_OBJ_CMD(
                Feat,
                "Axis = (" << Gui::Command::getObjectCmd(pcActiveBody->getOrigin()->getZ()) << ",[''])"
            );
        }

        FCMD_OBJ_CMD(Feat, "Angle = 360");
        FCMD_OBJ_CMD(Feat, "Occurrences = 2");

        finishTransformed(cmd, Feat);
    };

    prepareTransformed(pcActiveBody, this, "PolarPattern", worker);
}

bool CmdPartDesignPolarPattern::isActive()
{
    return isTransformCommandActive();
}

//===========================================================================
// PartDesign_Scaled
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignScaled)

CmdPartDesignScaled::CmdPartDesignScaled()
    : Command("PartDesign_Scaled")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Scale");
    sToolTipText = QT_TR_NOOP("Scales the selected features or the active body");
    sWhatsThis = "PartDesign_Scaled";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Scaled";
}

void CmdPartDesignScaled::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    Gui::Command* cmd = this;
    auto worker = [cmd](App::DocumentObject* Feat, std::vector<App::DocumentObject*> /*features*/) {
        FCMD_OBJ_CMD(Feat, "Factor = 2");
        FCMD_OBJ_CMD(Feat, "Occurrences = 2");

        finishTransformed(cmd, Feat);
    };

    prepareTransformed(pcActiveBody, this, "Scale", worker);
}

bool CmdPartDesignScaled::isActive()
{
    return isTransformCommandActive();
}

//===========================================================================
// PartDesign_MultiTransform
//===========================================================================
DEF_STD_CMD_A(CmdPartDesignMultiTransform)

CmdPartDesignMultiTransform::CmdPartDesignMultiTransform()
    : Command("PartDesign_MultiTransform")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Multi-Transform");
    sToolTipText = QT_TR_NOOP(
        "Applies multiple transformations to the selected features or active body"
    );
    sWhatsThis = "PartDesign_MultiTransform";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_MultiTransform";
}

void CmdPartDesignMultiTransform::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(true);

    if (!pcActiveBody) {
        return;
    }

    std::vector<App::DocumentObject*> features;

    // Check if a Transformed feature has been selected, convert it to MultiTransform
    features = getSelection().getObjectsOfType(PartDesign::Transformed::getClassTypeId());
    if (!features.empty()) {
        // Throw out MultiTransform features, we don't want to nest them
        for (std::vector<App::DocumentObject*>::iterator f = features.begin(); f != features.end();) {
            if ((*f)->isDerivedFrom<PartDesign::MultiTransform>()) {
                f = features.erase(f);
            }
            else {
                f++;
            }
        }

        if (features.empty()) {
            return;
        }
        // Note: If multiple Transformed features were selected, only the first one is used
        PartDesign::Transformed* trFeat = static_cast<PartDesign::Transformed*>(features.front());
        const BodyIdentity targetBodyIdentity = bodyIdentity(pcActiveBody);

        // Move the insert point back one feature
        App::DocumentObject* oldTip = nullptr;
        App::DocumentObject* prevFeature = nullptr;
        if (pcActiveBody) {
            oldTip = pcActiveBody->Tip.getValue();
            prevFeature = pcActiveBody->getPrevResultFeature(trFeat);
        }
        Gui::Selection().clearSelection(pcActiveBody->getDocument()->getName());
        if (prevFeature) {
            Gui::Selection().addSelection(
                prevFeature->getDocument()->getName(),
                prevFeature->getNameInDocument()
            );
        }

        openCommand(
            pcActiveBody->getDocument(),
            QT_TRANSLATE_NOOP("Command", "Convert to Multi-Transform feature")
        );

        Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();
        rcCmdMgr.runCommandByName("PartDesign_MoveTip");

        // We cannot remove the Transform feature from the body as otherwise
        // we will have a PartDesign feature without a body which is not allowed
        // and causes to pop up the migration dialog later when adding new features
        // to the body.
        // Additionally it creates the error message: "Links go out of the allowed scope"
        // #0003509
#if 0
        // Remove the Transformed feature from the Body
        if (pcActiveBody)
            FCMD_OBJ_CMD(pcActiveBody,"removeObject("<<getObjectCmd(trFeat)<<")");
#endif

        // Create a MultiTransform feature and move the Transformed feature inside it
        pcActiveBody = resolveBody(targetBodyIdentity);
        if (!pcActiveBody) {
            abortCommand();
            return;
        }
        std::string FeatName = getUniqueObjectName("MultiTransform", pcActiveBody);
        auto* Feat = freecad_cast<PartDesign::MultiTransform*>(
            createBodyFeatureExact(pcActiveBody, "PartDesign::MultiTransform", FeatName)
        );
        if (!Feat) {
            abortCommand();
            Base::Console().error("Could not create the Multi-Transform feature.\n");
            return;
        }
        const long featureId = Feat->getID();
        const std::string exactFeatureName = Feat->getNameInDocument();
        const auto refreshCreatedFeature = [&]() {
            pcActiveBody = resolveBody(targetBodyIdentity);
            Feat = pcActiveBody ? freecad_cast<PartDesign::MultiTransform*>(
                                      pcActiveBody->getDocument()->getObjectByID(featureId)
                                  )
                                : nullptr;
            return pcActiveBody && Feat && Feat->isAttachedToDocument() && Feat->getNameInDocument()
                && exactFeatureName == Feat->getNameInDocument()
                && PartDesign::Body::findBodyOf(Feat) == pcActiveBody;
        };
        if (!refreshCreatedFeature()) {
            abortCommand();
            return;
        }
        try {
            auto* timeline = App::DocumentTimeline::ensure(pcActiveBody->getDocument());
            timeline->stageExistingOperationResources(Feat, {trFeat});
            if (!refreshCreatedFeature()) {
                throw Base::RuntimeError(
                    "The exact Multi-Transform feature changed during timeline staging"
                );
            }
        }
        catch (const Base::Exception& error) {
            abortCommand();
            error.reportException();
            return;
        }
        auto objCmd = getObjectCmd(trFeat);
        FCMD_OBJ_CMD(Feat, "Originals = " << objCmd << ".Originals");
        FCMD_OBJ_CMD(Feat, "TransformMode = " << objCmd << ".TransformMode");
        FCMD_OBJ_CMD(Feat, "BaseFeature = " << objCmd << ".BaseFeature");
        FCMD_OBJ_CMD(Feat, "Transformations = [" << objCmd << "]");

        FCMD_OBJ_CMD(trFeat, "Originals = []");
        if (!refreshCreatedFeature()) {
            abortCommand();
            return;
        }

        // Add the MultiTransform into the Body at the current insert point
        finishFeature(this, Feat);
        if (!refreshCreatedFeature()) {
            abortCommand();
            return;
        }

        // Restore the insert point
        if (oldTip && oldTip != trFeat) {
            Gui::Selection().clearSelection(pcActiveBody->getDocument()->getName());
            Gui::Selection().addSelection(oldTip->getDocument()->getName(), oldTip->getNameInDocument());
            rcCmdMgr.runCommandByName("PartDesign_MoveTip");
            Gui::Selection().clearSelection(pcActiveBody->getDocument()->getName());
        }  // otherwise the insert point remains at the new MultiTransform, which is fine
    }
    else {

        Gui::Command* cmd = this;
        auto worker =
            [cmd,
             pcActiveBody](App::DocumentObject* Feat, std::vector<App::DocumentObject*> /*features*/) {
                // Make sure the user isn't presented with an empty screen because no
                // transformations are defined yet...
                App::DocumentObject* prevSolid = pcActiveBody->Tip.getValue();
                if (prevSolid) {
                    Part::Feature* feat = static_cast<Part::Feature*>(prevSolid);
                    FCMD_OBJ_CMD(Feat, "Shape = " << getObjectCmd(feat) << ".Shape");
                }
                finishFeature(cmd, Feat);
            };

        prepareTransformed(pcActiveBody, this, "MultiTransform", worker);
    }
}

bool CmdPartDesignMultiTransform::isActive()
{
    return isTransformCommandActive(true);
}

//===========================================================================
// Design-global Mirror and Patterns
//===========================================================================

namespace
{

struct DesignPatternSelection
{
    App::Document* document {};
    App::DocumentObject* sourceOperation {};
    PartDesign::Body* sourceBody {};
    std::vector<PartDesign::Body*> targetBodies;
    bool valid {true};
};

DesignPatternSelection selectedDesignPatternSource()
{
    DesignPatternSelection result;
    std::vector<PartDesign::Body*> selectedBodies;
    for (auto& selected : Gui::Selection().getSelectionEx()) {
        auto* object = selected.getObject();
        if (!object || !PartGui::isModelingObjectActive(object)) {
            result.valid = false;
            continue;
        }
        if (!result.document) {
            result.document = object->getDocument();
        }
        if (object->getDocument() != result.document) {
            result.valid = false;
            continue;
        }

        if (dynamic_cast<PartDesign::DesignOperationProperties*>(object)
            && freecad_cast<PartDesign::FeatureAddSub*>(object)) {
            if (result.sourceOperation && result.sourceOperation != object) {
                result.valid = false;
            }
            result.sourceOperation = object;
            continue;
        }

        auto* body = freecad_cast<PartDesign::Body*>(PartGui::findModelingBody(object));
        if (!body || !PartDesign::designBodyStateBefore(body, nullptr)) {
            result.valid = false;
            continue;
        }
        if (std::ranges::find(selectedBodies, body) == selectedBodies.end()) {
            selectedBodies.push_back(body);
        }
    }

    if (!result.document) {
        result.valid = false;
        return result;
    }

    if (result.sourceOperation) {
        result.targetBodies = std::move(selectedBodies);
        if (result.targetBodies.empty()) {
            auto* properties = dynamic_cast<PartDesign::DesignOperationProperties*>(
                result.sourceOperation
            );
            for (const auto& bodyId : properties->OutputBodyIds.getValues()) {
                auto* body = PartDesign::DesignModel::bodyWithId(*result.document, bodyId);
                if (body && PartDesign::designBodyStateBefore(body, nullptr)
                    && std::ranges::find(result.targetBodies, body) == result.targetBodies.end()) {
                    result.targetBodies.push_back(body);
                }
            }
        }
        result.valid = result.valid && !result.targetBodies.empty();
        return result;
    }

    if (selectedBodies.size() != 1) {
        result.valid = false;
        return result;
    }
    result.sourceBody = selectedBodies.front();
    return result;
}

template<typename Operation>
void startDesignPattern(
    Gui::Command& command,
    const char* typeName,
    const char* objectName,
    const char* transactionLabel
)
{
    const auto selected = selectedDesignPatternSource();
    if (!selected.valid) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Pattern source required"),
            QObject::tr("Select one Body to create independent Body copies, or "
                        "select one earlier additive or subtractive History feature. "
                        "With a feature selected, any selected Bodies are its explicit "
                        "Pattern targets.")
        );
        return;
    }
    if (Gui::Control().activeDialog(selected.document)) {
        return;
    }

    const int transactionId = command.openCommand(selected.document, transactionLabel);
    if (transactionId == App::NullTransaction) {
        command.resetTransactionID();
        return;
    }

    try {
        auto* operation = freecad_cast<Operation*>(createDocumentFeatureExact(
            selected.document,
            typeName,
            selected.document->getUniqueObjectName(objectName)
        ));
        if (!operation) {
            throw Base::TypeError("The Design Pattern factory returned an incompatible object");
        }
        operation->Label.setValue(objectName);

        auto edit = PartDesign::DesignModel::beginOperationEdit(*operation);
        if (selected.sourceOperation) {
            PartDesign::DesignModel::setFeaturePatternTargets(
                edit,
                *selected.sourceOperation,
                selected.targetBodies
            );
        }
        else {
            PartDesign::DesignModel::setBodyPatternSource(edit, *selected.sourceBody, 1);
        }
        operation->recomputeFeature();
        operation->recomputePreview();

        command.doCommand(
            Gui::Command::Doc,
            "Gui.getDocument('%s').setEdit('%s',0)",
            selected.document->getName(),
            operation->getNameInDocument()
        );
        if (!Gui::Control().activeDialog(selected.document)) {
            throw Base::RuntimeError("The Design Pattern task panel did not open");
        }
        Gui::Selection().clearSelection(selected.document->getName());
    }
    catch (...) {
        command.abortCommand(transactionId);
        command.resetTransactionID();
        throw;
    }
}

bool designPatternCommandActive()
{
    return PartDesignGui::canStartModelingCommand() && selectedDesignPatternSource().valid;
}

}  // namespace

DEF_STD_CMD_A(CmdPartDesignDesignMirror)

CmdPartDesignDesignMirror::CmdPartDesignDesignMirror()
    : Command("PartDesign_DesignMirror")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Mirror");
    sToolTipText = QT_TR_NOOP("Mirrors an earlier feature on explicit Bodies or creates an "
                              "independent mirrored Body");
    sWhatsThis = "PartDesign_DesignMirror";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Mirrored";
}

void CmdPartDesignDesignMirror::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignPattern<PartDesign::DesignMirror>(
        *this,
        "PartDesign::DesignMirror",
        "Mirror",
        QT_TRANSLATE_NOOP("Command", "Create Mirror")
    );
}

bool CmdPartDesignDesignMirror::isActive()
{
    return designPatternCommandActive();
}

DEF_STD_CMD_A(CmdPartDesignDesignLinearPattern)

CmdPartDesignDesignLinearPattern::CmdPartDesignDesignLinearPattern()
    : Command("PartDesign_DesignLinearPattern")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Linear Pattern");
    sToolTipText = QT_TR_NOOP(
        "Repeats an earlier feature on explicit Bodies or creates independent "
        "Bodies at a linear spacing"
    );
    sWhatsThis = "PartDesign_DesignLinearPattern";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_LinearPattern";
}

void CmdPartDesignDesignLinearPattern::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignPattern<PartDesign::DesignLinearPattern>(
        *this,
        "PartDesign::DesignLinearPattern",
        "Linear Pattern",
        QT_TRANSLATE_NOOP("Command", "Create Linear Pattern")
    );
}

bool CmdPartDesignDesignLinearPattern::isActive()
{
    return designPatternCommandActive();
}

DEF_STD_CMD_A(CmdPartDesignDesignCircularPattern)

CmdPartDesignDesignCircularPattern::CmdPartDesignDesignCircularPattern()
    : Command("PartDesign_DesignCircularPattern")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Circular Pattern");
    sToolTipText = QT_TR_NOOP(
        "Repeats an earlier feature on explicit Bodies or creates independent "
        "Bodies around an axis"
    );
    sWhatsThis = "PartDesign_DesignCircularPattern";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_PolarPattern";
}

void CmdPartDesignDesignCircularPattern::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    startDesignPattern<PartDesign::DesignCircularPattern>(
        *this,
        "PartDesign::DesignCircularPattern",
        "Circular Pattern",
        QT_TRANSLATE_NOOP("Command", "Create Circular Pattern")
    );
}

bool CmdPartDesignDesignCircularPattern::isActive()
{
    return designPatternCommandActive();
}

//===========================================================================
// Design Body selection and PartDesign_Scale
//===========================================================================

namespace
{

struct DesignBodySelection
{
    App::Document* document {};
    std::vector<PartDesign::Body*> bodies;
    bool valid {true};
};

DesignBodySelection selectedDesignBodies(bool requireSelection)
{
    DesignBodySelection result;
    for (auto& selected : Gui::Selection().getSelectionEx()) {
        auto* object = selected.getObject();
        if (!object || !PartGui::isModelingObjectActive(object)) {
            result.valid = false;
            continue;
        }
        if (!result.document) {
            result.document = object->getDocument();
        }
        if (object->getDocument() != result.document) {
            result.valid = false;
            continue;
        }

        auto* body = freecad_cast<PartDesign::Body*>(PartGui::findModelingBody(object));
        if (!body || !PartDesign::designBodyStateBefore(body, nullptr)) {
            result.valid = false;
            continue;
        }
        if (std::ranges::find(result.bodies, body) == result.bodies.end()) {
            result.bodies.push_back(body);
        }
    }
    if (!result.document && !requireSelection) {
        result.document = App::GetApplication().getActiveDocument();
    }
    const bool hasAvailableBody = result.document
        && std::ranges::any_of(
            result.document->getObjectsOfType<PartDesign::Body>(),
            [](auto* body) { return PartDesign::designBodyStateBefore(body, nullptr) != nullptr; }
        );
    result.valid = result.valid && result.document && hasAvailableBody
        && (!requireSelection || !result.bodies.empty());
    return result;
}

}  // namespace

DEF_STD_CMD_A(CmdPartDesignScale)

CmdPartDesignScale::CmdPartDesignScale()
    : Command("PartDesign_Scale")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Scale");
    sToolTipText = QT_TR_NOOP(
        "Scales one or more explicit Bodies around a fixed Design-space center"
    );
    sWhatsThis = "PartDesign_Scale";
    sStatusTip = sToolTipText;
    sPixmap = "Part_Scale";
}

void CmdPartDesignScale::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    const auto selected = selectedDesignBodies(false);
    if (!selected.valid) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Body required"),
            QObject::tr("Create at least one Body before starting Scale.")
        );
        return;
    }
    if (Gui::Control().activeDialog(selected.document)) {
        return;
    }

    const int transactionId
        = openCommand(selected.document, QT_TRANSLATE_NOOP("Command", "Scale Bodies"));
    if (transactionId == App::NullTransaction) {
        resetTransactionID();
        return;
    }

    try {
        auto* operation = freecad_cast<PartDesign::DesignScale*>(createDocumentFeatureExact(
            selected.document,
            "PartDesign::DesignScale",
            selected.document->getUniqueObjectName("Scale")
        ));
        if (!operation) {
            throw Base::TypeError("The Design Scale factory returned an incompatible object");
        }
        operation->Label.setValue("Scale");
        PartDesign::DesignModel::setOperationTargets(
            *operation,
            "Modify",
            selected.bodies,
            nullptr,
            {},
            true
        );
        operation->recomputeFeature();
        operation->recomputePreview();

        doCommand(
            Gui::Command::Doc,
            "Gui.getDocument('%s').setEdit('%s',0)",
            selected.document->getName(),
            operation->getNameInDocument()
        );
        if (!Gui::Control().activeDialog(selected.document)) {
            throw Base::RuntimeError("The Design Scale task panel did not open");
        }
        Gui::Selection().clearSelection(selected.document->getName());
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
}

bool CmdPartDesignScale::isActive()
{
    return PartDesignGui::canStartModelingCommand() && selectedDesignBodies(false).valid;
}

//===========================================================================
// PartDesign_Combine
//===========================================================================

DEF_STD_CMD_A(CmdPartDesignCombine)

CmdPartDesignCombine::CmdPartDesignCombine()
    : Command("PartDesign_Combine")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Combine");
    sToolTipText = QT_TR_NOOP("Joins, cuts, or intersects explicitly selected Bodies; the first "
                              "selected Body receives the result");
    sWhatsThis = "PartDesign_Combine";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Boolean";
}

void CmdPartDesignCombine::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    const auto selected = selectedDesignBodies(true);
    if (!selected.valid) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Body required"),
            QObject::tr("Select the result Body first. Select any tool Bodies after "
                        "it, then choose Join, Cut, or Intersect in the task panel.")
        );
        return;
    }
    if (Gui::Control().activeDialog(selected.document)) {
        return;
    }

    const int transactionId
        = openCommand(selected.document, QT_TRANSLATE_NOOP("Command", "Combine Bodies"));
    if (transactionId == App::NullTransaction) {
        resetTransactionID();
        return;
    }

    try {
        auto* operation = freecad_cast<PartDesign::DesignCombine*>(createDocumentFeatureExact(
            selected.document,
            "PartDesign::DesignCombine",
            selected.document->getUniqueObjectName("Combine")
        ));
        if (!operation) {
            throw Base::TypeError("The Design Combine factory returned an incompatible object");
        }

        operation->Label.setValue("Combine");
        const std::vector<PartDesign::Body*> tools(
            std::next(selected.bodies.begin()),
            selected.bodies.end()
        );
        PartDesign::DesignModel::setCombineBodies(
            *operation,
            "Join",
            *selected.bodies.front(),
            tools,
            false,
            {},
            true
        );
        operation->recomputeFeature();
        operation->recomputePreview();

        doCommand(
            Gui::Command::Doc,
            "Gui.getDocument('%s').setEdit('%s',0)",
            selected.document->getName(),
            operation->getNameInDocument()
        );
        if (!Gui::Control().activeDialog(selected.document)) {
            throw Base::RuntimeError("The Design Combine task panel did not open");
        }
        Gui::Selection().clearSelection(selected.document->getName());
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
}

bool CmdPartDesignCombine::isActive()
{
    return PartDesignGui::canStartModelingCommand() && selectedDesignBodies(true).valid;
}

//===========================================================================
// PartDesign_Split
//===========================================================================

namespace
{

struct DesignSplitSelection
{
    App::Document* document {};
    PartDesign::Body* source {};
    std::vector<App::PropertyLinkSubList::SubSet> definitions;
    bool valid {true};
};

DesignSplitSelection selectedDesignSplit()
{
    DesignSplitSelection result;
    auto selected = Gui::Selection().getSelectionEx();
    if (selected.size() < 2) {
        result.valid = false;
        return result;
    }

    for (std::size_t index = 0; index < selected.size(); ++index) {
        auto* object = selected[index].getObject();
        if (!object || !PartGui::isModelingObjectActive(object)) {
            result.valid = false;
            continue;
        }
        if (!result.document) {
            result.document = object->getDocument();
        }
        if (!result.document || object->getDocument() != result.document) {
            result.valid = false;
            continue;
        }

        if (index == 0) {
            result.source = freecad_cast<PartDesign::Body*>(PartGui::findModelingBody(object));
            if (!result.source || !PartDesign::designBodyStateBefore(result.source, nullptr)) {
                result.valid = false;
            }
            continue;
        }

        const bool usableDefinition = freecad_cast<PartDesign::Body*>(object)
            || freecad_cast<Part::Feature*>(object)
            || freecad_cast<PartDesign::DesignBodyPublication*>(object);
        if (!usableDefinition) {
            result.valid = false;
            continue;
        }
        const auto& subElements = selected[index].getSubNames();
        auto existing = std::ranges::find(result.definitions, object, [](const auto& reference) {
            return reference.first;
        });
        if (existing == result.definitions.end()) {
            result.definitions.emplace_back(object, subElements);
        }
        else if (subElements.empty()) {
            existing->second.clear();
        }
        else if (!existing->second.empty()) {
            for (const auto& subElement : subElements) {
                if (std::ranges::find(existing->second, subElement) == existing->second.end()) {
                    existing->second.push_back(subElement);
                }
            }
        }
    }

    result.valid = result.valid && result.document && result.source && !result.definitions.empty();
    return result;
}

}  // namespace

DEF_STD_CMD_A(CmdPartDesignSplit)

CmdPartDesignSplit::CmdPartDesignSplit()
    : Command("PartDesign_Split")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Split");
    sToolTipText = QT_TR_NOOP("Divides the first selected Body with the subsequently selected "
                              "faces, surfaces, shells, solids, or Bodies");
    sWhatsThis = "PartDesign_Split";
    sStatusTip = sToolTipText;
    sPixmap = "Part_SliceApart";
}

void CmdPartDesignSplit::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    const auto selected = selectedDesignSplit();
    if (!selected.valid) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Source and splitting definition required"),
            QObject::tr("Select the Body to divide first. Then select one or more "
                        "splitting faces, surfaces, shells, solids, or Bodies.")
        );
        return;
    }
    if (Gui::Control().activeDialog(selected.document)) {
        return;
    }

    const int transactionId
        = openCommand(selected.document, QT_TRANSLATE_NOOP("Command", "Split Body"));
    if (transactionId == App::NullTransaction) {
        resetTransactionID();
        return;
    }

    try {
        auto* operation = freecad_cast<PartDesign::DesignSplit*>(createDocumentFeatureExact(
            selected.document,
            "PartDesign::DesignSplit",
            selected.document->getUniqueObjectName("Split")
        ));
        if (!operation) {
            throw Base::TypeError("The Design Split factory returned an incompatible object");
        }
        operation->Label.setValue("Split");

        auto edit = PartDesign::DesignModel::beginOperationEdit(*operation);
        PartDesign::DesignModel::setSplitDefinition(edit, *selected.source, selected.definitions);

        doCommand(
            Gui::Command::Doc,
            "Gui.getDocument('%s').setEdit('%s',0)",
            selected.document->getName(),
            operation->getNameInDocument()
        );
        if (!Gui::Control().activeDialog(selected.document)) {
            throw Base::RuntimeError("The Design Split task panel did not open");
        }
        Gui::Selection().clearSelection(selected.document->getName());
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
}

bool CmdPartDesignSplit::isActive()
{
    return PartDesignGui::canStartModelingCommand() && selectedDesignSplit().valid;
}

//===========================================================================
// PartDesign_Separate
//===========================================================================

namespace
{

struct DesignSeparateSelection
{
    App::Document* document {};
    Part::Feature* source {};
    App::Part* destinationComponent {};
    bool valid {true};
};

DesignSeparateSelection selectedDesignSeparate()
{
    DesignSeparateSelection result;
    auto selected = Gui::Selection().getSelectionEx(
        nullptr,
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::NoResolve
    );
    if (selected.empty() || selected.size() > 2) {
        result.valid = false;
        return result;
    }

    for (auto& item : selected) {
        auto* object = item.getObject();
        if (!object || !item.getSubNames().empty() || !PartGui::isModelingObjectActive(object)) {
            result.valid = false;
            continue;
        }
        if (!result.document) {
            result.document = object->getDocument();
        }
        if (!result.document || object->getDocument() != result.document) {
            result.valid = false;
            continue;
        }

        if (auto* component = freecad_cast<App::Part*>(object);
            component && !PartDesign::DesignModel::componentId(*component).empty()) {
            if (result.destinationComponent) {
                result.valid = false;
            }
            result.destinationComponent = component;
            continue;
        }

        auto* feature = freecad_cast<Part::Feature*>(object);
        if (result.source || !feature || freecad_cast<App::Link*>(feature)
            || freecad_cast<App::LinkElement*>(feature) || PartGui::findModelingBody(feature)
            || App::GeoFeatureGroupExtension::getGroupOfObject(feature)
            || App::GroupExtension::getGroupOfObject(feature)
            || dynamic_cast<PartDesign::DesignOperationProperties*>(feature)) {
            result.valid = false;
            continue;
        }
        const Part::TopoShape shape = feature->Shape.getShape();
        if (shape.isNull() || shape.countSubShapes(TopAbs_SOLID) < 2) {
            result.valid = false;
            continue;
        }
        result.source = feature;
    }

    result.valid = result.valid && result.document && result.source;
    return result;
}

}  // namespace

DEF_STD_CMD_A(CmdPartDesignSeparate)

CmdPartDesignSeparate::CmdPartDesignSeparate()
    : Command("PartDesign_Separate")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Separate Solids");
    sToolTipText = QT_TR_NOOP("Creates one stable Body for every solid in the selected reusable "
                              "definition");
    sWhatsThis = "PartDesign_Separate";
    sStatusTip = sToolTipText;
    sPixmap = "Part_ExplodeCompound";
}

void CmdPartDesignSeparate::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    const auto selected = selectedDesignSeparate();
    if (!selected.valid) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Multi-solid definition required"),
            QObject::tr("Select one reusable definition containing at least two "
                        "solids. Optionally also select the destination Component.")
        );
        return;
    }
    if (Gui::Control().activeDialog(selected.document)) {
        return;
    }

    const CloneInteractionState interactionState = captureCloneInteractionState(selected.document);
    const int transactionId
        = openCommand(selected.document, QT_TRANSLATE_NOOP("Command", "Separate Solids"));
    if (transactionId == App::NullTransaction) {
        resetTransactionID();
        return;
    }

    try {
        if (!selected.source->getPropertyByName(App::DocumentTimeline::DefinitionIdPropertyName)) {
            PartDesign::DesignModel::finalizeDefinition(*selected.source);
        }

        auto* operation = freecad_cast<PartDesign::DesignSeparate*>(createDocumentFeatureExact(
            selected.document,
            "PartDesign::DesignSeparate",
            selected.document->getUniqueObjectName("Separate")
        ));
        if (!operation) {
            throw Base::TypeError("The Design Separate factory returned an incompatible object");
        }
        operation->Label.setValue(
            (std::string("Separate ") + selected.source->Label.getValue()).c_str()
        );

        auto edit = PartDesign::DesignModel::beginOperationEdit(*operation);
        PartDesign::DesignModel::setSeparateDefinition(
            edit,
            *selected.source,
            selected.destinationComponent
        );
        PartGui::setModelingReplacedInputs(*operation, {selected.source});
        auto outputs = PartDesign::DesignModel::finalizeOperation(edit);
        if (outputs.size() < 2 || std::ranges::any_of(outputs, [](const PartDesign::Body* body) {
                return body == nullptr;
            })) {
            throw Base::RuntimeError("Separate did not publish one Body per source solid");
        }

        for (std::size_t index = 0; index < outputs.size(); ++index) {
            auto* output = outputs[index];
            output->Label.setValue(
                (std::string(selected.source->Label.getValue()) + " " + std::to_string(index + 1)).c_str()
            );
            output->ShapeMaterial.setValue(selected.source->ShapeMaterial.getValue());
            PartDesignGui::copyShapeVisualProperties(*output, *selected.source);
        }

        Gui::cmdAppObjectHide(selected.source);
        Gui::Selection().clearSelection(selected.document->getName());
        for (auto* output : outputs) {
            Gui::Selection().addSelection(selected.document->getName(), output->getNameInDocument());
        }
        commitCommand();
    }
    catch (const Base::Exception& error) {
        abortCommand(transactionId);
        resetTransactionID();
        restoreCloneInteractionState(interactionState);
        error.reportException();
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Separate failed"),
            QApplication::translate("Exception", error.what())
        );
    }
    catch (const std::exception& error) {
        abortCommand(transactionId);
        resetTransactionID();
        restoreCloneInteractionState(interactionState);
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Separate failed"),
            QString::fromUtf8(error.what())
        );
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        restoreCloneInteractionState(interactionState);
        QMessageBox::critical(
            Gui::getMainWindow(),
            QObject::tr("Separate failed"),
            QObject::tr("An unexpected error prevented solid separation.")
        );
    }
}

bool CmdPartDesignSeparate::isActive()
{
    return PartDesignGui::canStartModelingCommand() && selectedDesignSeparate().valid;
}

//===========================================================================
// PartDesign_Boolean
//===========================================================================

/* Boolean commands =======================================================*/
DEF_STD_CMD_A(CmdPartDesignBoolean)

CmdPartDesignBoolean::CmdPartDesignBoolean()
    : Command("PartDesign_Boolean")
{
    sAppModule = "PartDesign";
    sGroup = QT_TR_NOOP("PartDesign");
    sMenuText = QT_TR_NOOP("Boolean Operation");
    sToolTipText = QT_TR_NOOP(
        "Applies boolean operations with the selected objects and the active body"
    );
    sWhatsThis = "PartDesign_Boolean";
    sStatusTip = sToolTipText;
    sPixmap = "PartDesign_Boolean";
}


void CmdPartDesignBoolean::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    PartDesign::Body* pcActiveBody = PartDesignGui::getBody(/*messageIfNot = */ true);
    if (!pcActiveBody) {
        return;
    }

    Gui::SelectionFilter BodyFilter("SELECT Part::Feature COUNT 1..");

    openCommand(QT_TRANSLATE_NOOP("Command", "Create Boolean"));
    const BodyIdentity targetBodyIdentity = bodyIdentity(pcActiveBody);
    std::string FeatName = getUniqueObjectName("Boolean", pcActiveBody);
    auto* Feat = createBodyFeatureExact(pcActiveBody, "PartDesign::Boolean", FeatName);
    pcActiveBody = resolveBody(targetBodyIdentity);
    if (!pcActiveBody || !Feat || PartDesign::Body::findBodyOf(Feat) != pcActiveBody) {
        abortCommand();
        return;
    }

    // If we don't add an object to the boolean group then don't update the body
    // as otherwise this will fail and it will be marked as invalid
    bool updateDocument = false;
    if (BodyFilter.match() && !BodyFilter.Result.empty()) {
        std::vector<App::DocumentObject*> bodies;
        for (auto& results : BodyFilter.Result) {
            for (auto& result : results) {
                if (result.getObject() != pcActiveBody) {
                    bodies.push_back(result.getObject());
                }
            }
        }
        if (!bodies.empty()) {
            updateDocument = true;
            std::string bodyString = PartDesignGui::buildLinkListPythonStr(bodies);
            FCMD_OBJ_CMD(Feat, "addObjects(" << bodyString << ")");
        }
    }

    finishFeature(this, Feat, nullptr, false, updateDocument);
}

bool CmdPartDesignBoolean::isActive()
{
    return PartDesignGui::canStartModelingCommand();
}

// Command group for datums =============================================

class CmdPartDesignCompDatums: public Gui::GroupCommand
{
public:
    CmdPartDesignCompDatums()
        : GroupCommand("PartDesign_CompDatums")
    {
        sAppModule = "PartDesign";
        sGroup = "PartDesign";
        sMenuText = QT_TR_NOOP("Create Datum");
        sToolTipText = QT_TR_NOOP("Creates a datum object or local coordinate system");
        sWhatsThis = "PartDesign_CompDatums";
        sStatusTip = sToolTipText;
        eType = ForEdit;

        setCheckable(false);

        addCommand("PartDesign_Plane");
        addCommand("PartDesign_Line");
        addCommand("PartDesign_Point");
        addCommand("PartDesign_CoordinateSystem");
    }

    const char* className() const override
    {
        return "CmdPartDesignCompDatums";
    }

    bool isActive() override
    {
        return PartDesignGui::canStartModelingCommand();
    }
};

// Command group for datums =============================================

class CmdPartDesignCompSketches: public Gui::GroupCommand
{
public:
    CmdPartDesignCompSketches()
        : GroupCommand("PartDesign_CompSketches")
    {
        sAppModule = "PartDesign";
        sGroup = "PartDesign";
        sMenuText = QT_TR_NOOP("Sketch Tools");
        sToolTipText = QT_TR_NOOP("Creates, maps, or edits a sketch");
        sWhatsThis = "PartDesign_CompSketches";
        sStatusTip = sToolTipText;
        eType = ForEdit;

        setCheckable(false);
        setRememberLast(false);

        addCommand("PartDesign_NewSketch");
        addCommand("Sketcher_MapSketch");
        addCommand("Sketcher_EditSketch");
    }

    const char* className() const override
    {
        return "CmdPartDesignCompSketches";
    }

    bool isActive() override
    {
        return PartDesignGui::canStartModelingCommand();
    }
};

//===========================================================================
// Initialization
//===========================================================================

void CreatePartDesignCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdPartDesignShapeBinder());
    rcCmdMgr.addCommand(new CmdPartDesignSubShapeBinder());
    rcCmdMgr.addCommand(new CmdPartDesignClone());
    rcCmdMgr.addCommand(new CmdPartDesignPlane());
    rcCmdMgr.addCommand(new CmdPartDesignLine());
    rcCmdMgr.addCommand(new CmdPartDesignPoint());
    rcCmdMgr.addCommand(new CmdPartDesignCS());

    rcCmdMgr.addCommand(new CmdPartDesignNewSketch());

    rcCmdMgr.addCommand(new CmdPartDesignDesignExtrude());
    rcCmdMgr.addCommand(new CmdPartDesignDesignRevolve());
    rcCmdMgr.addCommand(new CmdPartDesignDesignLoft());
    rcCmdMgr.addCommand(new CmdPartDesignDesignSweep());
    rcCmdMgr.addCommand(new CmdPartDesignDesignHelix());
    rcCmdMgr.addCommand(new CmdPartDesignPad());
    rcCmdMgr.addCommand(new CmdPartDesignPocket());
    rcCmdMgr.addCommand(new CmdPartDesignHole());
    rcCmdMgr.addCommand(new CmdPartDesignRevolution());
    rcCmdMgr.addCommand(new CmdPartDesignGroove());
    rcCmdMgr.addCommand(new CmdPartDesignAdditivePipe);
    rcCmdMgr.addCommand(new CmdPartDesignSubtractivePipe);
    rcCmdMgr.addCommand(new CmdPartDesignAdditiveLoft);
    rcCmdMgr.addCommand(new CmdPartDesignSubtractiveLoft);
    rcCmdMgr.addCommand(new CmdPartDesignAdditiveHelix);
    rcCmdMgr.addCommand(new CmdPartDesignSubtractiveHelix);

    rcCmdMgr.addCommand(new CmdPartDesignFillet());
    rcCmdMgr.addCommand(new CmdPartDesignDraft());
    rcCmdMgr.addCommand(new CmdPartDesignChamfer());
    rcCmdMgr.addCommand(new CmdPartDesignThickness());

    rcCmdMgr.addCommand(new CmdPartDesignDesignMirror());
    rcCmdMgr.addCommand(new CmdPartDesignDesignLinearPattern());
    rcCmdMgr.addCommand(new CmdPartDesignDesignCircularPattern());
    rcCmdMgr.addCommand(new CmdPartDesignScale());

    // Legacy Body-owned transformation commands remain callable for saved
    // documents and macros but are not used by the shipped Design ribbon.
    rcCmdMgr.addCommand(new CmdPartDesignMirrored());
    rcCmdMgr.addCommand(new CmdPartDesignLinearPattern());
    rcCmdMgr.addCommand(new CmdPartDesignPolarPattern());
    // rcCmdMgr.addCommand(new CmdPartDesignScaled());
    rcCmdMgr.addCommand(new CmdPartDesignMultiTransform());

    rcCmdMgr.addCommand(new CmdPartDesignCombine());
    rcCmdMgr.addCommand(new CmdPartDesignSplit());
    rcCmdMgr.addCommand(new CmdPartDesignSeparate());
    rcCmdMgr.addCommand(new CmdPartDesignBoolean());
    rcCmdMgr.addCommand(new CmdPartDesignCompDatums());
    rcCmdMgr.addCommand(new CmdPartDesignCompSketches());
}
