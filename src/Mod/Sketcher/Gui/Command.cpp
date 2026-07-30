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

#include <QApplication>
#include <QByteArray>
#include <QCheckBox>
#include <QGridLayout>
#include <QVBoxLayout>
#include <QInputDialog>
#include <QLabel>
#include <QMenu>
#include <QMessageBox>
#include <QSignalBlocker>
#include <QWidgetAction>

#include <memory>

#include <App/DocumentObjectGroup.h>
#include <App/DocumentTimeline.h>
#include <App/Datums.h>
#include <App/PropertyLinks.h>
#include <App/PropertyStandard.h>
#include <Gui/Action.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/CommandT.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/Notifications.h>
#include <Gui/PrefWidgets.h>
#include <Gui/QuantitySpinBox.h>
#include <Gui/Selection/SelectionFilter.h>
#include <Gui/Selection/SelectionObject.h>
#include <Mod/Part/App/Attacher.h>
#include <Mod/Part/App/BodyBase.h>
#include <Mod/Part/App/Part2DObject.h>
#include <Mod/Part/Gui/AttacherTexts.h>
#include <Mod/Part/Gui/ModelingSelection.h>
#include <Mod/Sketcher/App/Constraint.h>
#include <Mod/Sketcher/App/ExternalGeometryFacade.h>
#include <Mod/Sketcher/App/SketchObject.h>

#include "SketchMirrorDialog.h"
#include "SketchOrientationDialog.h"
#include "TaskSketcherValidation.h"
#include "Utils.h"
#include "ViewProviderSketch.h"
#include "Command.h"

// Hint: this is to prevent to re-format big parts of the file. Remove it later again.
// clang-format off
using namespace std;
using namespace SketcherGui;
using namespace Part;
using namespace Attacher;


namespace
{

App::Property* ensureSketchTimelineProperty(
    App::DocumentObject& object,
    const char* type,
    const char* name,
    const char* description
)
{
    auto* property = object.getPropertyByName(name);
    if (!property) {
        property = object.addDynamicProperty(
            type,
            name,
            "Timeline",
            description,
            App::Prop_NoRecompute,
            true,
            true
        );
    }
    property->setStatus(App::Property::Hidden, true);
    property->setStatus(App::Property::LockDynamic, true);
    property->setStatus(App::Property::NoRecompute, true);
    return property;
}

void markSketchCommandOutputs(
    const std::vector<Sketcher::SketchObject*>& outputs
)
{
    if (outputs.empty()) {
        return;
    }

    auto* operation = outputs.back();
    auto* operationRole = dynamic_cast<App::PropertyString*>(
        ensureSketchTimelineProperty(
            *operation,
            "App::PropertyString",
            App::DocumentTimeline::RolePropertyName,
            "Document timeline classification"
        )
    );
    if (!operationRole) {
        throw Base::TypeError(
            "Sketch timeline role metadata has an incompatible type"
        );
    }
    if (auto* ownerProperty = operation->getPropertyByName(
            App::DocumentTimeline::OwnerPropertyName
        )) {
        ownerProperty->setStatus(App::Property::Hidden, true);
        ownerProperty->setStatus(App::Property::LockDynamic, true);
        ownerProperty->setStatus(App::Property::NoRecompute, true);
        auto* owner = dynamic_cast<App::PropertyLinkHidden*>(ownerProperty);
        if (!owner || owner->getValue()) {
            throw Base::TypeError(
                "A root Sketch operation cannot retain resource-owner metadata"
            );
        }
    }
    operationRole->setValue(App::DocumentTimeline::OperationRole);

    for (std::size_t index = 0; index + 1 < outputs.size(); ++index) {
        auto* resource = outputs[index];
        if (!resource || resource == operation
            || resource->getDocument() != operation->getDocument()) {
            throw Base::ValueError(
                "Sketch command resources must be distinct outputs in one document"
            );
        }
        auto* resourceRole = dynamic_cast<App::PropertyString*>(
            ensureSketchTimelineProperty(
                *resource,
                "App::PropertyString",
                App::DocumentTimeline::RolePropertyName,
                "Document timeline classification"
            )
        );
        auto* resourceOwner = dynamic_cast<App::PropertyLinkHidden*>(
            ensureSketchTimelineProperty(
                *resource,
                "App::PropertyLinkHidden",
                App::DocumentTimeline::OwnerPropertyName,
                "Sketch operation which owns this generated result"
            )
        );
        if (!resourceRole || !resourceOwner) {
            throw Base::TypeError(
                "Sketch timeline resource metadata has an incompatible type"
            );
        }
        resourceOwner->setValue(operation);
        resourceRole->setValue(App::DocumentTimeline::ResourceRole);
    }

    std::vector<App::DocumentObject*> orderedOutputs(
        outputs.begin(),
        outputs.end()
    );
    App::DocumentTimeline::ensure(operation->getDocument())
        ->finalizeProvisionalOperationBlock(
            operation,
            orderedOutputs
        );
}

struct ExactSketchDocumentIdentity
{
    App::Document* address = nullptr;
    std::string name;
    std::string uid;
};

struct ExactSketchObjectIdentity
{
    ExactSketchDocumentIdentity document;
    long id = 0;
    std::string name;
};

struct ExactSketchSelectionOccurrence
{
    ExactSketchObjectIdentity object;
    std::vector<std::string> subNames;
};

ExactSketchDocumentIdentity exactSketchDocumentIdentity(
    App::Document* document
)
{
    if (!document) {
        return {};
    }
    return {
        document,
        document->getName(),
        document->Uid.getValueStr(),
    };
}

ExactSketchObjectIdentity exactSketchObjectIdentity(
    const App::DocumentObject* object
)
{
    if (!object || !object->isAttachedToDocument()
        || !object->getNameInDocument()) {
        return {};
    }
    return {
        exactSketchDocumentIdentity(object->getDocument()),
        object->getID(),
        object->getNameInDocument(),
    };
}

App::Document* resolveExactSketchDocument(
    const ExactSketchDocumentIdentity& identity
)
{
    if (!identity.address || identity.name.empty() || identity.uid.empty()) {
        return nullptr;
    }
    auto* document =
        App::GetApplication().getDocument(identity.name.c_str());
    return document == identity.address
            && document->Uid.getValueStr() == identity.uid
        ? document
        : nullptr;
}

App::DocumentObject* resolveExactSketchObject(
    const ExactSketchObjectIdentity& identity
)
{
    auto* document = resolveExactSketchDocument(identity.document);
    auto* object = document && identity.id > 0
        ? document->getObjectByID(identity.id)
        : nullptr;
    return object && object->getNameInDocument()
            && identity.name == object->getNameInDocument()
            && document->getObject(identity.name.c_str()) == object
        ? object
        : nullptr;
}

App::DocumentObject* resolveExactUsableSketchObject(
    const ExactSketchObjectIdentity& identity
)
{
    auto* object = resolveExactSketchObject(identity);
    return object && PartGui::isModelingObjectActive(object)
        ? object
        : nullptr;
}

Sketcher::SketchObject* resolveExactStandaloneSketch(
    const ExactSketchObjectIdentity& identity
)
{
    return freecad_cast<Sketcher::SketchObject*>(
        resolveExactSketchObject(identity)
    );
}

Sketcher::SketchObject* resolveExactUsableStandaloneSketch(
    const ExactSketchObjectIdentity& identity
)
{
    return freecad_cast<Sketcher::SketchObject*>(
        resolveExactUsableSketchObject(identity)
    );
}

App::DocumentObjectGroup* resolveExactSketchGroup(
    const ExactSketchObjectIdentity& identity
)
{
    return freecad_cast<App::DocumentObjectGroup*>(
        resolveExactSketchObject(identity)
    );
}

App::DocumentObjectGroup* resolveExactUsableSketchGroup(
    const ExactSketchObjectIdentity& identity
)
{
    auto* group = resolveExactSketchGroup(identity);
    return group && PartGui::isModelingObjectActive(group)
        ? group
        : nullptr;
}

std::vector<ExactSketchSelectionOccurrence> captureExactSketchSelection(
    const std::vector<Gui::SelectionObject>& selection
)
{
    std::vector<ExactSketchSelectionOccurrence> captured;
    captured.reserve(selection.size());
    for (const auto& selected : selection) {
        auto* object = selected.getObject();
        if (!object || !PartGui::isModelingObjectActive(object)) {
            return {};
        }
        captured.push_back(
            {
                exactSketchObjectIdentity(object),
                selected.getSubNames(),
            }
        );
    }
    return captured;
}

bool restoreExactSketchSupport(
    App::PropertyLinkSubList& support,
    const std::vector<ExactSketchSelectionOccurrence>& selection,
    App::Document& document
)
{
    if (selection.empty()) {
        return false;
    }

    std::vector<App::DocumentObject*> objects;
    std::vector<std::string> subNames;
    for (const auto& selected : selection) {
        auto* object = resolveExactUsableSketchObject(selected.object);
        if (!object || object->getDocument() != &document) {
            return false;
        }
        if (selected.subNames.empty()) {
            objects.push_back(object);
            subNames.emplace_back();
            continue;
        }
        for (const auto& subName : selected.subNames) {
            objects.push_back(object);
            subNames.push_back(subName);
        }
    }
    support.setValues(std::move(objects), std::move(subNames));
    return true;
}

bool selectionBelongsToExactSketchDocument(App::Document* document)
{
    if (!document) {
        return false;
    }
    const auto selection = Gui::Selection().getSelectionEx(
        document->getName(),
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::NoResolve
    );
    return std::ranges::all_of(
        selection,
        [document](const Gui::SelectionObject& selected) {
            auto* object = selected.getObject();
            return object && object->getDocument() == document
                && PartGui::isModelingObjectActive(object);
        }
    );
}

std::vector<ExactSketchObjectIdentity> selectedExactUsableSketches(
    App::Document* document
)
{
    std::vector<ExactSketchObjectIdentity> result;
    if (!document) {
        return result;
    }

    const auto selection = Gui::Selection().getSelectionEx(
             document->getName(),
             App::DocumentObject::getClassTypeId(),
             // The Fusion-style tree records a selected Body child as the
             // Body plus an exact child path (for example, "Sketch.").
             // Resolve that path to its selected object, but deliberately do
             // not use FollowLink: setup commands must never turn selection
             // of a linked occurrence into an edit of its shared definition.
             Gui::ResolveMode::OldStyleElement
         );
    if (selection.empty()) {
        return result;
    }

    result.reserve(selection.size());
    for (const auto& selected : selection) {
        auto* sketch = freecad_cast<Sketcher::SketchObject*>(
            selected.getObject()
        );
        if (!sketch || sketch->getDocument() != document
            || !PartGui::isModelingObjectActive(sketch)) {
            return {};
        }
        result.push_back(exactSketchObjectIdentity(sketch));
    }
    return result;
}

Sketcher::SketchObject* createStandaloneSketchExact(
    const ExactSketchDocumentIdentity& documentIdentity,
    const ExactSketchObjectIdentity* groupIdentity,
    const std::string& requestedName
)
{
    auto* document = resolveExactSketchDocument(documentIdentity);
    auto* group = groupIdentity
        ? resolveExactUsableSketchGroup(*groupIdentity)
        : nullptr;
    if (!document || requestedName.empty()) {
        throw Base::RuntimeError(
            "Creating a standalone Sketch requires one exact live document "
            "and requested name"
        );
    }
    if (groupIdentity && (!group || group->getDocument() != document)) {
        throw Base::RuntimeError(
            "Creating a grouped standalone Sketch requires one exact live "
            "group in its document"
        );
    }

    QByteArray factory;
    if (group) {
        factory = Gui::Command::getObjectCmd(group).c_str();
        factory += ".newObject('Sketcher::SketchObject','";
    }
    else {
        factory = "App.getDocument('";
        factory += documentIdentity.name.c_str();
        factory += "').addObject('Sketcher::SketchObject','";
    }
    factory += requestedName.c_str();
    factory += "')";

    auto* sketch = freecad_cast<Sketcher::SketchObject*>(
        Gui::Command::runDocumentObjectCommand(
            Gui::Command::Doc,
            *document,
            factory,
            Sketcher::SketchObject::getClassTypeId()
        )
    );
    if (!sketch) {
        throw Base::RuntimeError(
            "The standalone Sketch factory returned an incompatible result"
        );
    }

    const auto sketchIdentity = exactSketchObjectIdentity(sketch);
    sketch = resolveExactStandaloneSketch(sketchIdentity);
    document = resolveExactSketchDocument(documentIdentity);
    group = groupIdentity
        ? resolveExactUsableSketchGroup(*groupIdentity)
        : nullptr;
    if (!document || !sketch
        || (groupIdentity && (!group || !group->hasObject(sketch)))) {
        throw Base::RuntimeError(
            "The standalone Sketch factory did not retain its exact output "
            "and ownership"
        );
    }
    return sketch;
}

}  // namespace


namespace SketcherGui
{
class ExceptionWrongInput: public Base::Exception
{
public:
    ExceptionWrongInput()
        : ErrMsg(QString())
    {}

    // Pass untranslated strings, enclosed in QT_TR_NOOP()
    explicit ExceptionWrongInput(const char* ErrMsg)
    {
        this->ErrMsg = QObject::tr(ErrMsg);
        this->setMessage(ErrMsg);
    }

    ~ExceptionWrongInput() noexcept override
    {}

    QString ErrMsg;
};

void setSupportFromSelection(
    App::PropertyLinkSubList& support,
    const std::vector<Gui::SelectionObject>& selection
)
{
    std::vector<App::DocumentObject*> objects;
    std::vector<std::string> subNames;
    for (auto selected : selection) {
        auto* object = selected.getObject();
        if (!object) {
            continue;
        }
        if (selected.getSubNames().empty()) {
            objects.push_back(object);
            subNames.emplace_back();
            continue;
        }
        for (const auto& subName : selected.getSubNames()) {
            objects.push_back(object);
            subNames.push_back(subName);
        }
    }
    support.setValues(std::move(objects), std::move(subNames));
}

Attacher::eMapMode SuggestAutoMapMode(Attacher::SuggestResult::eSuggestResult* pMsgId = nullptr,
                                      QString* message = nullptr,
                                      std::vector<Attacher::eMapMode>* allmodes = nullptr,
                                      App::PropertyLinkSubList* selectedSupport = nullptr)
{
    // convert pointers into valid references, to avoid checking for null pointers everywhere
    Attacher::SuggestResult::eSuggestResult buf;
    if (!pMsgId)
        pMsgId = &buf;
    Attacher::SuggestResult::eSuggestResult& msg = *pMsgId;
    QString buf2;
    if (!message)
        message = &buf2;
    QString& msg_str = *message;

    App::PropertyLinkSubList tmpSupport;
    if (!selectedSupport) {
        Gui::Selection().getAsPropertyLinkSubList(tmpSupport);
        selectedSupport = &tmpSupport;
    }

    Attacher::SuggestResult sugr;
    AttachEngine3D eng;
    eng.setUp(*selectedSupport);
    eng.suggestMapModes(sugr);
    if (allmodes)
        *allmodes = sugr.allApplicableModes;
    msg = sugr.message;
    switch (msg) {
        case Attacher::SuggestResult::srOK:
            break;
        case Attacher::SuggestResult::srNoModesFit:
            msg_str = QObject::tr("There are no modes that accept the selected set of subelements");
            break;
        case Attacher::SuggestResult::srLinkBroken:
            msg_str = QObject::tr("Broken link to support subelements");
            break;
        case Attacher::SuggestResult::srUnexpectedError:
            msg_str = QObject::tr("Unexpected error");
            break;
        case Attacher::SuggestResult::srIncompatibleGeometry:
            if (!selectedSupport->getSubValues().empty()
                && selectedSupport->getSubValues()[0].substr(0, 4) == std::string("Face"))
                msg_str = QObject::tr("Face is non-planar");
            else
                msg_str = QObject::tr("Selected shapes are of wrong form (e.g., a curved edge "
                                      "where a straight one is needed)");
            break;
        default:
            msg_str = QObject::tr("Unexpected error");
            assert(0 /*no message for eSuggestResult enum item*/);
    }

    return sugr.bestFitMode;
}

bool isSketchSetupAvailable(Gui::Document* document)
{
    if (!document || document->getInEdit()
        || !PartGui::canStartRetainedModelingTask(
            document->getDocument()
        )
        || Gui::Control().activeDialog(document->getDocument())) {
        return false;
    }

    return selectionBelongsToExactSketchDocument(
        document->getDocument()
    );
}
}// namespace SketcherGui


/* Sketch commands =======================================================*/
DEF_STD_CMD_A(CmdSketcherNewSketch)

CmdSketcherNewSketch::CmdSketcherNewSketch()
    : Command("Sketcher_NewSketch")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("New Sketch");
    sToolTipText = QT_TR_NOOP("Creates a new sketch");
    sWhatsThis = "Sketcher_NewSketch";
    sStatusTip = sToolTipText;
    sPixmap = "Sketcher_NewSketch";
}

void CmdSketcherNewSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* guiDocument = getActiveGuiDocument();
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    const auto documentIdentity = exactSketchDocumentIdentity(document);
    if (resolveExactSketchDocument(documentIdentity) != document
        || !isSketchSetupAvailable(guiDocument)) {
        return;
    }

    Attacher::eMapMode mapmode = Attacher::mmDeactivated;
    bool bAttach = false;
    bool groupSelected = false;
    ExactSketchObjectIdentity selectedGroupIdentity;
    std::vector<ExactSketchSelectionOccurrence> capturedSupport;
    const auto rawSelection = Gui::Selection().getSelectionEx(
        document->getName(),
        App::DocumentObject::getClassTypeId(),
        Gui::ResolveMode::NoResolve
    );
    const auto selectedGroup = std::ranges::find_if(
        rawSelection,
        [](const Gui::SelectionObject& selected) {
            return selected.isObjectTypeOf(
                App::DocumentObjectGroup::getClassTypeId()
            );
        }
    );
    if (selectedGroup != rawSelection.end()) {
        if (rawSelection.size() != 1) {
            Gui::TranslatedUserWarning(
                getActiveGuiDocument(),
                QObject::tr("Invalid Selection"),
                QObject::tr("Too many objects selected"));
                return;
        }

        auto* group = freecad_cast<App::DocumentObjectGroup*>(
            selectedGroup->getObject()
        );
        if (!group || group->getDocument() != document) {
            Gui::TranslatedUserWarning(
                guiDocument,
                QObject::tr("Invalid Selection"),
                QObject::tr(
                    "The selected group does not belong to this document"
                )
            );
            return;
        }
        selectedGroupIdentity = exactSketchObjectIdentity(group);
        groupSelected = true;
    }
    else if (!rawSelection.empty()) {
        const auto supportSelection =
            PartGui::getModelingSelection(document->getName());
        if (supportSelection.empty()) {
            Gui::TranslatedUserWarning(
                guiDocument,
                QObject::tr("Sketch mapping"),
                QObject::tr(
                    "The selected support is not available at the current "
                    "History position."
                )
            );
            return;
        }
        capturedSupport =
            captureExactSketchSelection(supportSelection);
        App::PropertyLinkSubList support;
        if (capturedSupport.empty()
            || !restoreExactSketchSupport(
                support,
                capturedSupport,
                *document
            )) {
            return;
        }
        Attacher::SuggestResult::eSuggestResult msgid = Attacher::SuggestResult::srOK;
        QString msg_str;
        std::vector<Attacher::eMapMode> validModes;
        mapmode = SuggestAutoMapMode(
            &msgid,
            &msg_str,
            &validModes,
            &support
        );
        if (msgid == Attacher::SuggestResult::srOK)
            bAttach = true;
        if (msgid != Attacher::SuggestResult::srOK
            && msgid != Attacher::SuggestResult::srNoModesFit) {
            Gui::TranslatedUserWarning(
                getActiveGuiDocument(),
                QObject::tr("Sketch mapping"),
                QObject::tr("Cannot map the sketch to the selected object. %1.").arg(msg_str));
            return;
        }
        if (validModes.size() > 1) {
            validModes.insert(validModes.begin(), Attacher::mmDeactivated);
            bool ok;
            QStringList items;
            items.push_back(QObject::tr("Do not attach"));
            int iSugg = 0;// index of the auto-suggested mode in the list of valid modes
            for (size_t i = 0; i < validModes.size(); ++i) {
                auto uiStrings =
                    AttacherGui::getUIStrings(AttachEnginePlane::getClassTypeId(), validModes[i]);
                items.push_back(uiStrings[0]);
                if (validModes[i] == mapmode)
                    iSugg = items.size() - 1;
            }
            QString text = QInputDialog::getItem(
                Gui::getMainWindow(),
                qApp->translate("Sketcher_NewSketch", "Sketch Attachment"),
                qApp->translate("Sketcher_NewSketch",
                                "Select the method to attach this sketch to selected object"),
                items,
                iSugg,
                false,
                &ok,
                Qt::MSWindowsFixedSizeDialogHint);
            if (!ok)
                return;
            int index = items.indexOf(text);
            if (index == 0) {
                bAttach = false;
                mapmode = Attacher::mmDeactivated;
            }
            else {
                bAttach = true;
                mapmode = validModes[index - 1];
            }
        }
    }

    if (bAttach) {
        document = resolveExactSketchDocument(documentIdentity);
        if (!document || getActiveGuiDocument() != guiDocument) {
            return;
        }
        App::PropertyLinkSubList support;
        if (!restoreExactSketchSupport(
                support,
                capturedSupport,
                *document
            )) {
            Gui::TranslatedUserWarning(
                guiDocument,
                QObject::tr("Sketch mapping"),
                QObject::tr(
                    "The selected support changed while choosing the "
                    "attachment mode."
                )
            );
            return;
        }
        std::string supportString = support.getPyReprString();

        ExactSketchObjectIdentity supportGroupIdentity;
        const ExactSketchObjectIdentity* targetGroupIdentity = nullptr;
        auto* supportPart = freecad_cast<Part::Feature*>(
            support.getValue()
        );
        auto* supportGroup = supportPart ? supportPart->getGroup() : nullptr;
        if (supportGroup && supportGroup->getDocument() == document) {
            supportGroupIdentity = exactSketchObjectIdentity(supportGroup);
            targetGroupIdentity = &supportGroupIdentity;
        }

        if (targetGroupIdentity
            && !resolveExactUsableSketchObject(*targetGroupIdentity)) {
            return;
        }
        const std::string requestedName =
            document->getUniqueObjectName("Sketch");
        const int existingTransaction =
            document->getBookedTransactionID();
        const int transactionId = openCommand(
            document,
            QT_TRANSLATE_NOOP("Command", "Create a new sketch on a face")
        );
        if (transactionId == App::NullTransaction
            || document->getBookedTransactionID() != transactionId
            || !App::GetApplication().transactionIsActive(
                transactionId
            )) {
            if (existingTransaction == App::NullTransaction
                && transactionId != App::NullTransaction
                && document->getBookedTransactionID()
                    == transactionId) {
                abortCommand(transactionId);
            }
            resetTransactionID();
            return;
        }

        try {
            auto* sketch = createStandaloneSketchExact(
                documentIdentity,
                targetGroupIdentity,
                requestedName
            );
            const auto sketchIdentity = exactSketchObjectIdentity(sketch);
            const auto requireSketch = [&]() {
                auto* exactSketch =
                    resolveExactStandaloneSketch(sketchIdentity);
                if (!exactSketch) {
                    throw Base::RuntimeError(
                        "The exact standalone Sketch changed during setup"
                    );
                }
                if (targetGroupIdentity) {
                    auto* exactGroup =
                        resolveExactUsableSketchGroup(
                            *targetGroupIdentity
                        );
                    if (!exactGroup || !exactGroup->hasObject(exactSketch)) {
                        throw Base::RuntimeError(
                            "The exact standalone Sketch changed ownership "
                            "during setup"
                        );
                    }
                }
                return exactSketch;
            };

            if (mapmode >= Attacher::mmDummy_NumberOfModes) {
                throw Base::ValueError(
                    "The selected Sketch attachment mode is invalid"
                );
            }
            const auto requireSupport = [&]() {
                auto* exactDocument =
                    resolveExactSketchDocument(documentIdentity);
                App::PropertyLinkSubList exactSupport;
                if (!exactDocument
                    || !restoreExactSketchSupport(
                        exactSupport,
                        capturedSupport,
                        *exactDocument
                    )) {
                    throw Base::RuntimeError(
                        "The selected Sketch support changed during setup"
                    );
                }
                return exactSupport.getPyReprString();
            };
            sketch = requireSketch();
            doCommand(Gui,
                      "%s.MapMode = \"%s\"",
                      Gui::Command::getObjectCmd(sketch).c_str(),
                      AttachEngine::getModeName(mapmode).c_str());
            sketch = requireSketch();
            supportString = requireSupport();
            doCommand(
                Gui,
                "%s.AttachmentSupport = %s",
                Gui::Command::getObjectCmd(sketch).c_str(),
                supportString.c_str()
            );
            requireSketch();
            requireSupport();
            doCommand(
                Gui,
                "App.getDocument('%s').recompute()",
                documentIdentity.name.c_str()
            );
            sketch = requireSketch();
            doCommand(
                Gui,
                "Gui.getDocument('%s').setEdit('%s')",
                documentIdentity.name.c_str(),
                sketch->getNameInDocument()
            );
            sketch = requireSketch();
            if (guiDocument->getInEdit()
                != Gui::Application::Instance->getViewProvider(sketch)) {
                throw Base::RuntimeError(
                    "The exact standalone Sketch did not enter edit mode"
                );
            }
            resetTransactionID();
        }
        catch (...) {
            abortCommand(transactionId);
            resetTransactionID();
            throw;
        }
        // setEdit() transfers this transaction to the sketch task. The task
        // commits it on Finish and aborts it on Cancel, including creation of
        // the provisional sketch.
    }
    else {
        // ask user for orientation
        SketchOrientationDialog Dlg;

        Dlg.adjustSize();
        if (Dlg.exec() != QDialog::Accepted)
            return;// canceled
        Base::Vector3d p = Dlg.Pos.getPosition();
        Base::Rotation r = Dlg.Pos.getRotation();

        document = resolveExactSketchDocument(documentIdentity);
        if (!document) {
            return;
        }
        const ExactSketchObjectIdentity* targetGroupIdentity =
            groupSelected ? &selectedGroupIdentity : nullptr;
        if (targetGroupIdentity
            && !resolveExactUsableSketchGroup(
                *targetGroupIdentity
            )) {
            return;
        }
        const std::string requestedName =
            document->getUniqueObjectName("Sketch");
        const int existingTransaction =
            document->getBookedTransactionID();
        const int transactionId = openCommand(
            document,
            QT_TRANSLATE_NOOP("Command", "Create a new sketch")
        );
        if (transactionId == App::NullTransaction
            || document->getBookedTransactionID() != transactionId
            || !App::GetApplication().transactionIsActive(
                transactionId
            )) {
            if (existingTransaction == App::NullTransaction
                && transactionId != App::NullTransaction
                && document->getBookedTransactionID()
                    == transactionId) {
                abortCommand(transactionId);
            }
            resetTransactionID();
            return;
        }

        try {
            auto* sketch = createStandaloneSketchExact(
                documentIdentity,
                targetGroupIdentity,
                requestedName
            );
            const auto sketchIdentity = exactSketchObjectIdentity(sketch);
            const auto requireSketch = [&]() {
                auto* exactSketch =
                    resolveExactStandaloneSketch(sketchIdentity);
                if (!exactSketch) {
                    throw Base::RuntimeError(
                        "The exact standalone Sketch changed during setup"
                    );
                }
                if (targetGroupIdentity) {
                    auto* exactGroup =
                        resolveExactUsableSketchGroup(
                            *targetGroupIdentity
                        );
                    if (!exactGroup || !exactGroup->hasObject(exactSketch)) {
                        throw Base::RuntimeError(
                            "The exact standalone Sketch changed ownership "
                            "during setup"
                        );
                    }
                }
                return exactSketch;
            };

            sketch = requireSketch();
            doCommand(
                Doc,
                "%s.Placement = App.Placement(App.Vector(%f, %f, %f), "
                "App.Rotation(%f, %f, %f, %f))",
                Gui::Command::getObjectCmd(sketch).c_str(),
                p.x,
                p.y,
                p.z,
                r[0],
                r[1],
                r[2],
                r[3]
            );
            sketch = requireSketch();
            doCommand(
                Doc,
                "%s.MapMode = \"%s\"",
                Gui::Command::getObjectCmd(sketch).c_str(),
                AttachEngine::getModeName(Attacher::mmDeactivated).c_str()
            );
            sketch = requireSketch();
            doCommand(
                Gui,
                "Gui.getDocument('%s').setEdit('%s')",
                documentIdentity.name.c_str(),
                sketch->getNameInDocument()
            );
            sketch = requireSketch();
            if (guiDocument->getInEdit()
                != Gui::Application::Instance->getViewProvider(sketch)) {
                throw Base::RuntimeError(
                    "The exact standalone Sketch did not enter edit mode"
                );
            }
            resetTransactionID();
        }
        catch (...) {
            abortCommand(transactionId);
            resetTransactionID();
            throw;
        }
        // setEdit() transfers this transaction to the sketch task. Do not
        // close its rollback journal before the user chooses Finish or Cancel.
    }
}

bool CmdSketcherNewSketch::isActive()
{
    return isSketchSetupAvailable(getActiveGuiDocument());
}

DEF_STD_CMD_A(CmdSketcherEditSketch)

CmdSketcherEditSketch::CmdSketcherEditSketch()
    : Command("Sketcher_EditSketch")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Edit Sketch");
    sToolTipText = QT_TR_NOOP("Opens the selected sketch for editing");
    sWhatsThis = "Sketcher_EditSketch";
    sStatusTip = sToolTipText;
    sPixmap = "Sketcher_EditSketch";
}

void CmdSketcherEditSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* guiDocument = getActiveGuiDocument();
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    const auto selected = selectedExactUsableSketches(document);
    if (selected.size() != 1) {
        return;
    }
    auto* sketch = resolveExactUsableStandaloneSketch(selected.front());
    if (!sketch || sketch->getDocument() != document
        || getActiveGuiDocument() != guiDocument) {
        return;
    }
    doCommand(
        Gui,
        "Gui.getDocument('%s').setEdit('%s')",
        document->getName(),
        sketch->getNameInDocument()
    );
}

bool CmdSketcherEditSketch::isActive()
{
    auto* guiDocument = getActiveGuiDocument();
    return isSketchSetupAvailable(guiDocument)
        && selectedExactUsableSketches(
               guiDocument->getDocument()
           ).size() == 1;
}

DEF_STD_CMD_A(CmdSketcherLeaveSketch)

CmdSketcherLeaveSketch::CmdSketcherLeaveSketch()
    : Command("Sketcher_LeaveSketch")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Leave Sketch");
    sToolTipText = QT_TR_NOOP("Finishes editing the active sketch. Press Escape to exit.");
    sWhatsThis = "Sketcher_LeaveSketch";
    sStatusTip = sToolTipText;
    sPixmap = "Sketcher_LeaveSketch";
    eType = 0;
}

void CmdSketcherLeaveSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    Gui::Document* doc = getActiveGuiDocument();
    if (!doc) {
        return;
    }
    const auto documentIdentity =
        exactSketchDocumentIdentity(doc->getDocument());

    // checks if a Sketch Viewprovider is in Edit and is in no special mode
    SketcherGui::ViewProviderSketch* vp =
        dynamic_cast<SketcherGui::ViewProviderSketch*>(doc->getInEdit());
    if (vp && vp->getSketchMode() != ViewProviderSketch::STATUS_NONE)
        vp->purgeHandler();

    if (Gui::Control().activeDialog(doc->getDocument())) {
        // Finish through the owning task dialog so it can make the launch
        // transaction durable before edit teardown destroys the panel.
        Gui::Control().accept(doc->getDocument());
        return;
    }

    // See also TaskDlgEditSketch::reject
    doCommand(
        Gui,
        "Gui.getDocument('%s').resetEdit()",
        documentIdentity.name.c_str()
    );
    if (auto* exactDocument =
            resolveExactSketchDocument(documentIdentity)) {
        Gui::cmdAppDocument(exactDocument, "recompute()");
    }
}

bool CmdSketcherLeaveSketch::isActive()
{
    return isSketchInEdit(getActiveGuiDocument());
}

// Cancel sketch edition

DEF_STD_CMD_A(CmdSketcherCancelSketch)

CmdSketcherCancelSketch::CmdSketcherCancelSketch()
    : Command("Sketcher_CancelSketch")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Cancel Editing");
    sToolTipText = QT_TR_NOOP("Leaves 'edit' mode and reverts any changes");
    sWhatsThis = "Sketcher_CancelSketch";
    sStatusTip = sToolTipText;
    sPixmap = "Sketcher_CancelSketch";
    eType = 0;
}

void CmdSketcherCancelSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    Gui::Document* doc = getActiveGuiDocument();
    if (!doc) {
        return;
    }

    auto* vp = dynamic_cast<SketcherGui::ViewProviderSketch*>(doc->getInEdit());
    if (!vp) {
        return;
    }

    if (vp->getSketchMode() != ViewProviderSketch::STATUS_NONE) {
        vp->purgeHandler();
    }

    if (Gui::Control().activeDialog(doc->getDocument())) {
        // Route through the installed task dialog so its command checkpoint is
        // restored after the edit transaction has been aborted.
        Gui::Control().reject(doc->getDocument());
        return;
    }

    const auto sketchIdentity =
        exactSketchObjectIdentity(vp->getObject());
    vp->editingCancelled = true;
    doc->cancelEdit();
    if (auto* sketch =
            resolveExactStandaloneSketch(sketchIdentity)) {
        if (auto* restored = dynamic_cast<ViewProviderSketch*>(
                Gui::Application::Instance->getViewProvider(sketch))) {
            restored->editingCancelled = false;
        }
    }
}

bool CmdSketcherCancelSketch::isActive()
{
    return isSketchInEdit(getActiveGuiDocument());
}

//===========================================================================
// Sketcher_LeaveGroup
//===========================================================================
class CmdSketcherLeaveGroup : public Gui::GroupCommand
{
public:
    CmdSketcherLeaveGroup() : GroupCommand("Sketcher_LeaveGroup")
    {
        sAppModule = "Sketcher";
        sGroup = "Sketcher";
        sMenuText = QT_TR_NOOP("Leave");
        sToolTipText = QT_TR_NOOP("Leaves the sketch editing mode");
        sWhatsThis = "Sketcher_LeaveGroup";
        sStatusTip = sToolTipText;
        eType = 0;

        setCheckable(false);
        setRememberLast(false);

        addCommand("Sketcher_LeaveSketch");
        addCommand("Sketcher_CancelSketch");
    }

    const char* className() const override { return "CmdSketcherLeaveGroup"; }
};

DEF_STD_CMD_A(CmdSketcherStopOperation)

CmdSketcherStopOperation::CmdSketcherStopOperation()
    : Command("Sketcher_StopOperation")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Stop Operation");
    sToolTipText = QT_TR_NOOP("Stops the active operation while in edit mode");


    sWhatsThis = "Sketcher_StopOperation";
    sStatusTip = sToolTipText;
    sPixmap = "process-stop";
    eType = 0;
}

void CmdSketcherStopOperation::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    Gui::Document* doc = getActiveGuiDocument();

    if (doc) {
        SketcherGui::ViewProviderSketch* vp =
            dynamic_cast<SketcherGui::ViewProviderSketch*>(doc->getInEdit());
        if (vp) {
            vp->purgeHandler();
        }
    }
}

bool CmdSketcherStopOperation::isActive()
{
    return isSketchInEdit(getActiveGuiDocument());
}

DEF_STD_CMD_A(CmdSketcherReorientSketch)

CmdSketcherReorientSketch::CmdSketcherReorientSketch()
    : Command("Sketcher_ReorientSketch")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Reorient Sketch");
    sToolTipText = QT_TR_NOOP("Places the selected sketch on one of the global coordinate planes.\n"
                              "This will clear the AttachmentSupport property.");
    sWhatsThis = "Sketcher_ReorientSketch";
    sStatusTip = sToolTipText;
    sPixmap = "Sketcher_ReorientSketch";
}

void CmdSketcherReorientSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* guiDocument = getActiveGuiDocument();
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    const auto documentIdentity = exactSketchDocumentIdentity(document);
    const auto selected = selectedExactUsableSketches(document);
    if (selected.size() != 1) {
        return;
    }
    const auto sketchIdentity = selected.front();
    auto* sketch = resolveExactUsableStandaloneSketch(sketchIdentity);
    if (!sketch || sketch->getDocument() != document) {
        return;
    }
    const bool detachFromSupport = sketch->AttachmentSupport.getValue();
    if (detachFromSupport) {
        int ret = QMessageBox::question(
            Gui::getMainWindow(),
            qApp->translate("Sketcher_ReorientSketch", "Sketch Has Support"),
            qApp->translate("Sketcher_ReorientSketch",
                            "Sketch with a support face cannot be reoriented.\n"
                            "Detach it from the support?"),
            QMessageBox::Yes | QMessageBox::No);
        if (ret == QMessageBox::No)
            return;
    }

    // ask user for orientation
    SketchOrientationDialog Dlg;

    if (Dlg.exec() != QDialog::Accepted)
        return;// canceled
    Base::Vector3d p = Dlg.Pos.getPosition();
    Base::Rotation r = Dlg.Pos.getRotation();

    document = resolveExactSketchDocument(documentIdentity);
    sketch = resolveExactUsableStandaloneSketch(sketchIdentity);
    if (!document || !sketch || sketch->getDocument() != document
        || getActiveGuiDocument() != guiDocument
        || !isSketchSetupAvailable(guiDocument)) {
        return;
    }

    // do the right view direction
    std::string camstring;
    switch (Dlg.DirType) {
        case 0:
            camstring = "#Inventor V2.1 ascii\\n"
                        "OrthographicCamera {\\n"
                        " viewportMapping ADJUST_CAMERA\\n"
                        "  position 0 0 87\\n"
                        "  orientation 0 0 1  0\\n"
                        "  nearDistance -112.88701\\n"
                        "  farDistance 287.28702\\n"
                        "  aspectRatio 1\\n"
                        "  focalDistance 87\\n"
                        "  height 143.52005 }";
            break;
        case 1:
            camstring = "#Inventor V2.1 ascii\\n"
                        "OrthographicCamera {\\n"
                        " viewportMapping ADJUST_CAMERA\\n"
                        "  position 0 0 -87\\n"
                        "  orientation -1 0 0  3.1415927\\n"
                        "  nearDistance -112.88701\\n"
                        "  farDistance 287.28702\\n "
                        "  aspectRatio 1\\n"
                        "  focalDistance 87\\n"
                        "  height 143.52005 }";
            break;
        case 2:
            camstring = "#Inventor V2.1 ascii\\n"
                        "OrthographicCamera {\\n"
                        " viewportMapping ADJUST_CAMERA\\n"
                        "  position 0 -87 0\\n"
                        "  orientation -1 0 0  4.712389\\n"
                        "  nearDistance -112.88701\\n"
                        "  farDistance 287.28702\\n"
                        "  aspectRatio 1\\n"
                        "  focalDistance 87\\n"
                        "  height 143.52005\\n\\n}";
            break;
        case 3:
            camstring = "#Inventor V2.1 ascii\\n"
                        "OrthographicCamera {\\n"
                        " viewportMapping ADJUST_CAMERA\\n"
                        "  position 0 87 0\\n"
                        "  orientation 0 0.70710683 0.70710683  3.1415927\\n"
                        "  nearDistance -112.88701\\n"
                        "  farDistance 287.28702\\n"
                        "  aspectRatio 1\\n"
                        "  focalDistance 87\\n"
                        "  height 143.52005\\n\\n}";
            break;
        case 4:
            camstring = "#Inventor V2.1 ascii\\n"
                        "OrthographicCamera {\\n"
                        " viewportMapping ADJUST_CAMERA\\n"
                        "  position 87 0 0\\n"
                        "  orientation 0.57735026 0.57735026 0.57735026  2.0943952\\n"
                        "  nearDistance -112.887\\n"
                        "  farDistance 287.28699\\n"
                        "  aspectRatio 1\\n"
                        "  focalDistance 87\\n"
                        "  height 143.52005\\n\\n}";
            break;
        case 5:
            camstring = "#Inventor V2.1 ascii\\n"
                        "OrthographicCamera {\\n"
                        " viewportMapping ADJUST_CAMERA\\n"
                        "  position -87 0 0\\n"
                        "  orientation -0.57735026 0.57735026 0.57735026  4.1887903\\n"
                        "  nearDistance -112.887\\n"
                        "  farDistance 287.28699\\n"
                        "  aspectRatio 1\\n"
                        "  focalDistance 87\\n"
                        "  height 143.52005\\n\\n}";
            break;
    }

    const int existingTransaction =
        document->getBookedTransactionID();
    const int transactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Reorient sketch")
    );
    if (transactionId == App::NullTransaction
        || document->getBookedTransactionID() != transactionId
        || !App::GetApplication().transactionIsActive(transactionId)) {
        if (existingTransaction == App::NullTransaction
            && transactionId != App::NullTransaction
            && document->getBookedTransactionID() == transactionId) {
            abortCommand(transactionId);
        }
        resetTransactionID();
        return;
    }

    try {
        sketch = resolveExactUsableStandaloneSketch(sketchIdentity);
        if (!sketch) {
            throw Base::RuntimeError(
                "The selected Sketch changed before reorientation"
            );
        }
        if (detachFromSupport) {
            Gui::cmdAppObjectArgs(sketch, "AttachmentSupport = None");
            sketch = resolveExactUsableStandaloneSketch(sketchIdentity);
            if (!sketch) {
                throw Base::RuntimeError(
                    "The selected Sketch changed while detaching its support"
                );
            }
        }
        Gui::cmdAppObjectArgs(
            sketch,
            "Placement = App.Placement(App.Vector(%f, %f, %f), "
            "App.Rotation(%f, %f, %f, %f))",
            p.x,
            p.y,
            p.z,
            r[0],
            r[1],
            r[2],
            r[3]
        );
        sketch = resolveExactUsableStandaloneSketch(sketchIdentity);
        if (!sketch) {
            throw Base::RuntimeError(
                "The selected Sketch changed while applying its orientation"
            );
        }
        doCommand(
            Gui,
            "Gui.getDocument('%s').setEdit('%s')",
            documentIdentity.name.c_str(),
            sketch->getNameInDocument()
        );
        if (guiDocument->getInEdit() !=
            Gui::Application::Instance->getViewProvider(sketch)) {
            throw Base::RuntimeError(
                "The reoriented Sketch did not enter edit mode"
            );
        }
        resetTransactionID();
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
    // setEdit() transfers this transaction to the sketch task. Finish commits
    // the new placement and Cancel restores the exact pre-command placement.
}

bool CmdSketcherReorientSketch::isActive()
{
    auto* guiDocument = getActiveGuiDocument();
    return isSketchSetupAvailable(guiDocument)
        && selectedExactUsableSketches(
               guiDocument->getDocument()
           ).size() == 1;
}

DEF_STD_CMD_A(CmdSketcherMapSketch)

CmdSketcherMapSketch::CmdSketcherMapSketch()
    : Command("Sketcher_MapSketch")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Attach Sketch");
    sToolTipText = QT_TR_NOOP(
        "Attaches a sketch to the selected geometry element");
    sWhatsThis = "Sketcher_MapSketch";
    sStatusTip = sToolTipText;
    sPixmap = "Sketcher_MapSketch";
}

void CmdSketcherMapSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    QString msg_str;
    try {
        Attacher::eMapMode suggMapMode;
        std::vector<Attacher::eMapMode> validModes;
        auto* guiDocument = getActiveGuiDocument();
        App::Document* doc =
            guiDocument ? guiDocument->getDocument() : nullptr;
        const auto documentIdentity =
            exactSketchDocumentIdentity(doc);
        if (!doc || !isSketchSetupAvailable(guiDocument)) {
            return;
        }

        // A Body is the visible result container, while its Tip owns the
        // topology. Use one projected support snapshot for suggestion,
        // dependency validation, and the final property assignment.
        std::vector<Gui::SelectionObject> supportSelection =
            PartGui::getModelingSelection(doc->getName());
        if (supportSelection.empty()) {
            throw ExceptionWrongInput(
                QT_TR_NOOP("The selected support is not attachable geometry."));
        }
        const auto capturedSupport =
            captureExactSketchSelection(supportSelection);
        App::PropertyLinkSubList support;
        if (capturedSupport.empty()
            || !restoreExactSketchSupport(
                support,
                capturedSupport,
                *doc
            )) {
            throw ExceptionWrongInput(
                QT_TR_NOOP(
                    "The selected support is no longer available at the "
                    "current History position."
                )
            );
        }

        // check that selection is valid for at least some mapping mode.
        Attacher::SuggestResult::eSuggestResult msgid = Attacher::SuggestResult::srOK;
        suggMapMode = SuggestAutoMapMode(&msgid, &msg_str, &validModes, &support);
        bool sketchInSelection = false;
        std::vector<const Part::Part2DObject*> selectedSketches;
        for (const auto& selected : supportSelection) {
            if (const auto* selectedSketch =
                    freecad_cast<Part::Part2DObject*>(
                        selected.getObject()
                    )) {
                selectedSketches.push_back(selectedSketch);
            }
        }
        std::vector<App::DocumentObject*> sketches =
            doc->getObjectsOfType(Part::Part2DObject::getClassTypeId());

        /** remove any sketches that are in the current selection to avoid
         *  the case where the user attaches the sketch to itself issue #17629
         *  circular dependency check happens later, but a sketch does not appear
         *  in its own outlist, so we remove it from the dialog list proactively
         *  rather than wait and generate an error after the fact.
         */
        const auto newEnd = std::ranges::remove_if(sketches,
            [&selectedSketches, &sketchInSelection](App::DocumentObject* obj) {
                if (!PartGui::isModelingObjectActive(obj)) {
                    return true;
                }
                if (const auto* sketch =
                        dynamic_cast<const Part::Part2DObject*>(obj);
                    sketch
                    && std::ranges::find(selectedSketches, sketch)
                        != selectedSketches.end()) {
                    sketchInSelection = true;
                    return true;
                }
                return false;
            }).begin();
        sketches.erase(newEnd, sketches.end());

        if (sketches.empty()) {
            Gui::TranslatedUserWarning(
                doc->Label.getStrValue(),
                qApp->translate("Sketcher_MapSketch", "No sketch found"),
                sketchInSelection
                ? qApp->translate("Sketcher_MapSketch", "Cannot attach sketch to itself!")
                : qApp->translate("Sketcher_MapSketch", "The document does not contain a sketch"));

            return;
        }
        std::sort(sketches.begin(), sketches.end(), [](const auto &a, const auto &b) {
            return QString::fromUtf8(a->Label.getValue()) < QString::fromUtf8(b->Label.getValue());
        });

        bool ok;
        QStringList items;
        std::vector<ExactSketchObjectIdentity> sketchChoices;
        sketchChoices.reserve(sketches.size());
        for (auto* candidate : sketches) {
            sketchChoices.push_back(
                exactSketchObjectIdentity(candidate)
            );
            items.push_back(
                QStringLiteral("%1 (%2)")
                    .arg(
                        QString::fromUtf8(
                            candidate->Label.getValue()
                        ),
                        QString::fromLatin1(
                            candidate->getNameInDocument()
                        )
                    )
            );
        }
        QString text = QInputDialog::getItem(
            Gui::getMainWindow(),
            qApp->translate("Sketcher_MapSketch", "Select Sketch"),
            sketchInSelection
            ? qApp->translate("Sketcher_MapSketch",
                "Select a sketch (some sketches not shown to prevent a circular dependency)")
            : qApp->translate("Sketcher_MapSketch", "Select a sketch from the list"),
            items,
            0,
            false,
            &ok,
            Qt::MSWindowsFixedSizeDialogHint);
        if (!ok)
            return;
        int index = items.indexOf(text);
        if (index < 0
            || index >= static_cast<int>(sketchChoices.size())) {
            return;
        }
        doc = resolveExactSketchDocument(documentIdentity);
        if (!doc || getActiveGuiDocument() != guiDocument) {
            return;
        }
        const auto sketchIdentity =
            sketchChoices[index];
        Part2DObject* sketch = freecad_cast<Part2DObject*>(
            resolveExactUsableSketchObject(sketchIdentity)
        );
        if (!sketch || sketch->getDocument() != doc) {
            throw ExceptionWrongInput(
                QT_TR_NOOP(
                    "The selected sketch changed while the attachment "
                    "dialog was open."
                )
            );
        }
        // Re-resolve the exact occurrences after the first modal dialog.
        App::PropertyLinkSubList circularSupport;
        if (!restoreExactSketchSupport(
                circularSupport,
                capturedSupport,
                *doc
            )) {
            throw ExceptionWrongInput(
                QT_TR_NOOP(
                    "The selected support changed while the attachment "
                    "dialog was open."
                )
            );
        }

        // check circular dependency
        for (auto* part : circularSupport.getValues()) {
            if (std::vector<App::DocumentObject*> input = part->getOutListRecursive();
                std::ranges::find(input, sketch) != input.end()) {
                throw ExceptionWrongInput(
                    QT_TR_NOOP("Some of the selected objects depend on the sketch to be mapped. "
                               "Circular dependencies are not allowed."));
            }
        }

        // Ask for a new mode.
        // outline:
        //  * find out the modes that are compatible with selection.
        //  * Test if current mode is OK.
        //  * fill in the dialog
        //  * execute the dialog
        //  * collect dialog result
        //  * action

        bool bAttach = true;
        bool bCurIncompatible = false;
        // * find out the modes that are compatible with selection.
        const auto  curMapMode = eMapMode(sketch->MapMode.getValue());
        // * Test if current mode is OK.
        if (std::ranges::find(validModes, curMapMode) == validModes.end())
            bCurIncompatible = true;

        // * fill in the dialog
        validModes.insert(validModes.begin(), Attacher::mmDeactivated);
        if (bCurIncompatible)
            validModes.push_back(curMapMode);
        // bool ok; //already defined
        // QStringList items; //already defined
        items.clear();
        items.push_back(QObject::tr("Do not attach"));
        int iSugg = 0;// index of the auto-suggested mode in the list of valid modes
        int iCurr = 0;// index of current mode in the list of valid modes
        for (size_t i = 0; i < validModes.size(); ++i) {
            // Get the 2-element vector of caption, tooltip -- this class cannot use the tooltip,
            // so it is just ignored.
            auto uiStrings =
                AttacherGui::getUIStrings(AttachEnginePlane::getClassTypeId(), validModes[i]);
            items.push_back(uiStrings[0]);
            if (validModes[i] == curMapMode) {
                iCurr = items.size() - 1;
                items.back().append(
                    bCurIncompatible
                        ? qApp->translate("Sketcher_MapSketch", " (incompatible with selection)")
                        : qApp->translate("Sketcher_MapSketch", " (current)"));
            }
            if (validModes[i] == suggMapMode) {
                iSugg = items.size() - 1;
                if (iSugg == 1) {
                    iSugg = 0;// redirect deactivate to detach
                }
                else {
                    items.back().append(qApp->translate("Sketcher_MapSketch", " (suggested)"));
                }
            }
        }
        // * execute the dialog
        text = QInputDialog::getItem(
            Gui::getMainWindow(),
            qApp->translate("Sketcher_MapSketch", "Sketch Attachment"),
            bCurIncompatible
                ? qApp->translate(
                    "Sketcher_MapSketch",
                    "Current attachment mode is incompatible with the new selection.\n"
                    "Select the method to attach this sketch to selected objects.")
                : qApp->translate("Sketcher_MapSketch",
                                  "Select the method to attach this sketch to selected objects."),
            items,
            bCurIncompatible ? iSugg : iCurr,
            false,
            &ok,
            Qt::MSWindowsFixedSizeDialogHint);
        // * collect dialog result
        if (!ok)
            return;
        index = items.indexOf(text);
        if (index < 0
            || index > static_cast<int>(validModes.size())) {
            return;
        }
        if (index == 0) {
            bAttach = false;
            suggMapMode = Attacher::mmDeactivated;
        }
        else {
            bAttach = true;
            suggMapMode = validModes[index - 1];
        }

        // * action
        doc = resolveExactSketchDocument(documentIdentity);
        sketch = freecad_cast<Part2DObject*>(
            resolveExactUsableSketchObject(sketchIdentity)
        );
        App::PropertyLinkSubList exactSupport;
        if (!doc || !sketch || sketch->getDocument() != doc
            || getActiveGuiDocument() != guiDocument
            || !isSketchSetupAvailable(guiDocument)
            || !restoreExactSketchSupport(
                exactSupport,
                capturedSupport,
                *doc
            )) {
            throw ExceptionWrongInput(
                QT_TR_NOOP(
                    "The sketch or its selected support changed while the "
                    "attachment dialog was open."
                )
            );
        }

        if (bAttach) {
            Attacher::SuggestResult::eSuggestResult finalMessage =
                Attacher::SuggestResult::srOK;
            std::vector<Attacher::eMapMode> finalModes;
            SuggestAutoMapMode(
                &finalMessage,
                &msg_str,
                &finalModes,
                &exactSupport
            );
            if (finalMessage != Attacher::SuggestResult::srOK
                || std::ranges::find(finalModes, suggMapMode)
                    == finalModes.end()) {
                throw ExceptionWrongInput(
                    QT_TR_NOOP(
                        "The chosen attachment mode is no longer valid for "
                        "the selected support."
                    )
                );
            }
        }
        for (auto* part : exactSupport.getValues()) {
            const auto dependencies = part->getOutListRecursive();
            if (std::ranges::find(dependencies, sketch)
                != dependencies.end()) {
                throw ExceptionWrongInput(
                    QT_TR_NOOP(
                        "Some of the selected objects depend on the sketch "
                        "to be mapped. Circular dependencies are not allowed."
                    )
                );
            }
        }

        const int existingTransaction =
            doc->getBookedTransactionID();
        const int transactionId = openCommand(
            doc,
            bAttach
                ? QT_TRANSLATE_NOOP("Command", "Attach sketch")
                : QT_TRANSLATE_NOOP("Command", "Detach sketch")
        );
        if (transactionId == App::NullTransaction
            || doc->getBookedTransactionID() != transactionId
            || !App::GetApplication().transactionIsActive(
                transactionId
            )) {
            if (existingTransaction == App::NullTransaction
                && transactionId != App::NullTransaction
                && doc->getBookedTransactionID()
                    == transactionId) {
                abortCommand(transactionId);
            }
            resetTransactionID();
            return;
        }

        try {
            const auto requireTargetAndSupport = [&]() {
                auto* exactDocument =
                    resolveExactSketchDocument(documentIdentity);
                auto* exactSketch = freecad_cast<Part2DObject*>(
                    resolveExactUsableSketchObject(sketchIdentity)
                );
                App::PropertyLinkSubList currentSupport;
                if (!exactDocument || !exactSketch
                    || exactSketch->getDocument() != exactDocument
                    || !restoreExactSketchSupport(
                        currentSupport,
                        capturedSupport,
                        *exactDocument
                    )) {
                    throw Base::RuntimeError(
                        "The exact Sketch attachment inputs changed"
                    );
                }
                return exactSketch;
            };

            sketch = requireTargetAndSupport();
            if (bAttach) {
                const std::string supportString =
                    exactSupport.getPyReprString();
                Gui::cmdAppObjectArgs(
                    sketch,
                    "AttachmentSupport = %s",
                    supportString.c_str()
                );
                sketch = requireTargetAndSupport();
            }
            Gui::cmdAppObjectArgs(
                sketch,
                "MapMode = \"%s\"",
                AttachEngine::getModeName(suggMapMode).c_str()
            );
            sketch = requireTargetAndSupport();
            if (!bAttach) {
                Gui::cmdAppObjectArgs(sketch, "AttachmentSupport = None");
                requireTargetAndSupport();
            }
            auto* recomputeDocument =
                resolveExactSketchDocument(documentIdentity);
            if (!recomputeDocument) {
                throw Base::RuntimeError(
                    "The Sketch attachment document changed before recompute"
                );
            }
            Gui::cmdAppDocument(recomputeDocument, "recompute()");
            commitCommand(transactionId);
            resetTransactionID();
        }
        catch (...) {
            abortCommand(transactionId);
            resetTransactionID();
            throw;
        }
    }
    catch (ExceptionWrongInput& e) {
        Gui::TranslatedUserWarning(getActiveGuiDocument(),
                                   qApp->translate("Sketcher_MapSketch", "Map sketch"),
                                   qApp->translate("Sketcher_MapSketch",
                                                   "Can't map a sketch to support:\n"
                                                   "%1")
                                       .arg(e.ErrMsg.length() ? e.ErrMsg : msg_str));
    }
    catch (const Base::Exception& error) {
        Gui::TranslatedUserWarning(
            getActiveGuiDocument(),
            qApp->translate("Sketcher_MapSketch", "Map sketch"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdSketcherMapSketch::isActive()
{
    auto* guiDocument = getActiveGuiDocument();
    App::Document* doc =
        guiDocument ? guiDocument->getDocument() : nullptr;
    return isSketchSetupAvailable(guiDocument) && doc
        && doc->countObjectsOfType<Part::Part2DObject>() > 0
        && !PartGui::getModelingSelection(doc->getName()).empty();
}

DEF_STD_CMD_A(CmdSketcherViewSketch)

CmdSketcherViewSketch::CmdSketcherViewSketch()
    : Command("Sketcher_ViewSketch")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Align View to Sketch");
    sToolTipText = QT_TR_NOOP("Aligns the camera orientation perpendicular to the active sketch plane");
    sWhatsThis = "Sketcher_ViewSketch";
    sStatusTip = sToolTipText;
    sPixmap = "Sketcher_ViewSketch";
    sAccel = "Q, P";
    eType = 0;
}

void CmdSketcherViewSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    if (Gui::Application::Instance->isInEdit(getActiveGuiDocument())) {
        runCommand(Gui,
                   "Gui.ActiveDocument.ActiveView.setCameraOrientation("
                   "App.Placement(Gui.ActiveDocument.EditingTransform).Rotation.Q)");
    }
}

bool CmdSketcherViewSketch::isActive()
{
    return isSketchInEdit(getActiveGuiDocument());
}

DEF_STD_CMD_A(CmdSketcherValidateSketch)

CmdSketcherValidateSketch::CmdSketcherValidateSketch()
    : Command("Sketcher_ValidateSketch")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Validate Sketch");
    sToolTipText = QT_TR_NOOP("Validates a sketch by checking for missing coincidences,\n"
                              "invalid constraints, and degenerate geometry");
    sWhatsThis = "Sketcher_ValidateSketch";
    sStatusTip = sToolTipText;
    eType = 0;
    sPixmap = "Sketcher_ValidateSketch";
}

void CmdSketcherValidateSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* guiDocument = getActiveGuiDocument();
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    const auto selection = selectedExactUsableSketches(document);
    if (selection.size() != 1) {
        Gui::TranslatedUserWarning(
            guiDocument,
            qApp->translate("CmdSketcherValidateSketch", "Wrong selection"),
            qApp->translate("CmdSketcherValidateSketch", "Select only 1 sketch."));
        return;
    }

    auto* sketch =
        resolveExactUsableStandaloneSketch(selection.front());
    if (!sketch || sketch->getDocument() != document) {
        return;
    }
    Gui::Control().showDialog(new TaskSketcherValidation(sketch));
}
bool CmdSketcherValidateSketch::isActive()
{
    auto* document = getActiveGuiDocument();
    if (!document
        || !PartGui::canStartRetainedModelingTask(document->getDocument())
        || !isSketchSetupAvailable(document)) {
        return false;
    }
    return selectedExactUsableSketches(
               document->getDocument()
           ).size() == 1;
}

DEF_STD_CMD_A(CmdSketcherMirrorSketch)

CmdSketcherMirrorSketch::CmdSketcherMirrorSketch()
    : Command("Sketcher_MirrorSketch")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Mirror Sketch");
    sToolTipText = QT_TR_NOOP("Creates a new mirrored sketch for each selected sketch\n"
                              "by using the X or Y axes, or the origin point,\n"
                              "as mirroring reference");
    sWhatsThis = "Sketcher_MirrorSketch";
    sStatusTip = sToolTipText;
    eType = 0;
    sPixmap = "Sketcher_MirrorSketch";
}

void CmdSketcherMirrorSketch::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* guiDocument = getActiveGuiDocument();
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    const auto documentIdentity =
        exactSketchDocumentIdentity(document);
    const auto selection = selectedExactUsableSketches(document);
    if (selection.empty()) {
        Gui::TranslatedUserWarning(
            guiDocument,
            qApp->translate("CmdSketcherMirrorSketch", "Wrong selection"),
            qApp->translate("CmdSketcherMirrorSketch", "Select at least 1 sketch"));
        return;
    }

    int refgeoid = -1;
    Sketcher::PointPos refposid = Sketcher::PointPos::none;
    // Ask the user the type of mirroring
    SketchMirrorDialog smd;
    if (smd.exec() != QDialog::Accepted)
        return;

    refgeoid = smd.RefGeoid;
    refposid = smd.RefPosid;

    document = resolveExactSketchDocument(documentIdentity);
    if (!document || getActiveGuiDocument() != guiDocument
        || !isSketchSetupAvailable(guiDocument)) {
        return;
    }
    for (const auto& sourceIdentity : selection) {
        auto* source =
            resolveExactUsableStandaloneSketch(sourceIdentity);
        if (!source || source->getDocument() != document) {
            return;
        }
    }

    const int existingTransaction =
        document->getBookedTransactionID();
    const int transactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP(
            "Command",
            "Create a mirrored sketch for each selected sketch"
        )
    );
    if (transactionId == App::NullTransaction
        || document->getBookedTransactionID() != transactionId
        || !App::GetApplication().transactionIsActive(transactionId)) {
        if (existingTransaction == App::NullTransaction
            && transactionId != App::NullTransaction
            && document->getBookedTransactionID() == transactionId) {
            abortCommand(transactionId);
        }
        resetTransactionID();
        return;
    }

    std::vector<ExactSketchObjectIdentity> mirroredSketches;
    mirroredSketches.reserve(selection.size());

    try {
        for (const auto& sourceIdentity : selection) {
            auto* source =
                resolveExactUsableStandaloneSketch(sourceIdentity);
            if (!source || source->getDocument() != document) {
                throw Base::RuntimeError(
                    "A Mirror Sketch source changed before it was copied"
                );
            }

            const Base::Placement sourcePlacement =
                source->Placement.getValue();
            auto temporarySketch =
                std::make_unique<Sketcher::SketchObject>();
            const int addedGeometries =
                temporarySketch->addGeometry(
                    source->getInternalGeometry()
                );
            const int addedConstraints =
                temporarySketch->addConstraints(
                    source->Constraints.getValues()
                );

            std::vector<int> geometryIds;
            for (int geometryId = 0;
                 geometryId <= addedGeometries;
                 ++geometryId) {
                geometryIds.push_back(geometryId);
            }
            temporarySketch->addSymmetric(
                geometryIds,
                refgeoid,
                refposid
            );

            const auto temporaryGeometry =
                temporarySketch->getInternalGeometry();
            const auto temporaryConstraints =
                temporarySketch->Constraints.getValues();
            std::vector<Part::Geometry*> mirroredGeometry(
                temporaryGeometry.begin() + (addedGeometries + 1),
                temporaryGeometry.end()
            );
            std::vector<Sketcher::Constraint*> mirroredConstraints(
                temporaryConstraints.begin() + (addedConstraints + 1),
                temporaryConstraints.end()
            );
            for (auto* constraint : mirroredConstraints) {
                // The temporary sketch prefixes the mirrored geometry with the
                // source geometry. Only ordinary geometry indices need rebasing;
                // axes, the origin, external geometry, and GeoUndef are stable
                // negative identifiers and must remain unchanged.
                if (constraint->First >= 0)
                    constraint->First -= (addedGeometries + 1);
                if (constraint->Second >= 0)
                    constraint->Second -= (addedGeometries + 1);
                if (constraint->Third >= 0)
                    constraint->Third -= (addedGeometries + 1);
            }

            const std::string featureName =
                document->getUniqueObjectName("MirroredSketch");
            const QString factory =
                QStringLiteral(
                    "App.getDocument('%1').addObject("
                    "'Sketcher::SketchObject','%2')")
                    .arg(
                        QString::fromLatin1(document->getName()),
                        QString::fromStdString(featureName)
                    );
            auto* mirroredSketch =
                freecad_cast<Sketcher::SketchObject*>(
                Gui::Command::runDocumentObjectCommand(
                    Gui::Command::Doc,
                    *document,
                    factory.toUtf8(),
                    Sketcher::SketchObject::getClassTypeId()));
            if (!mirroredSketch) {
                throw Base::RuntimeError("Mirror Sketch returned an incompatible result");
            }
            const auto mirroredIdentity =
                exactSketchObjectIdentity(mirroredSketch);
            if (!resolveExactUsableStandaloneSketch(sourceIdentity)
                || resolveExactStandaloneSketch(mirroredIdentity)
                    != mirroredSketch) {
                throw Base::RuntimeError(
                    "Mirror Sketch inputs or output changed during creation"
                );
            }

            const Base::Vector3d position =
                sourcePlacement.getPosition();
            const Base::Rotation rotation =
                sourcePlacement.getRotation();
            doCommand(
                Doc,
                "%s.Placement = App.Placement(App.Vector(%f, %f, %f), "
                "App.Rotation(%f, %f, %f, %f))",
                Gui::Command::getObjectCmd(mirroredSketch).c_str(),
                position.x,
                position.y,
                position.z,
                rotation[0],
                rotation[1],
                rotation[2],
                rotation[3]
            );
            mirroredSketch =
                resolveExactStandaloneSketch(mirroredIdentity);
            if (!mirroredSketch) {
                throw Base::RuntimeError(
                    "Mirror Sketch output changed while applying placement"
                );
            }
            mirroredSketch->addGeometry(mirroredGeometry);
            mirroredSketch =
                resolveExactStandaloneSketch(mirroredIdentity);
            if (!mirroredSketch) {
                throw Base::RuntimeError(
                    "Mirror Sketch output changed while copying geometry"
                );
            }
            mirroredSketch->addConstraints(mirroredConstraints);
            if (!resolveExactStandaloneSketch(mirroredIdentity)) {
                throw Base::RuntimeError(
                    "Mirror Sketch output changed while copying constraints"
                );
            }
            mirroredSketches.push_back(mirroredIdentity);
        }

        std::vector<Sketcher::SketchObject*> exactOutputs;
        exactOutputs.reserve(mirroredSketches.size());
        for (const auto& outputIdentity : mirroredSketches) {
            auto* output =
                resolveExactStandaloneSketch(outputIdentity);
            if (!output) {
                throw Base::RuntimeError(
                    "A Mirror Sketch output changed before History finalization"
                );
            }
            exactOutputs.push_back(output);
        }
        markSketchCommandOutputs(exactOutputs);
        auto* exactDocument =
            resolveExactSketchDocument(documentIdentity);
        if (!exactDocument) {
            throw Base::RuntimeError(
                "The Mirror Sketch document changed before recompute"
            );
        }
        Gui::cmdAppDocument(exactDocument, "recompute()");
        commitCommand(transactionId);
        resetTransactionID();
    }
    catch (Base::Exception& error) {
        abortCommand(transactionId);
        resetTransactionID();
        error.reportException();
        return;
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
}

bool CmdSketcherMirrorSketch::isActive()
{
    auto* guiDocument = getActiveGuiDocument();
    return isSketchSetupAvailable(guiDocument)
        && !selectedExactUsableSketches(
                guiDocument->getDocument()
            ).empty();
}

// Private helpers for CmdSketcherMergeSketches::activated()
namespace {

    // Import external geometries from srcSketch into dstSketch
    // and build a mapping: srcGeoId -> dstGeoId.
    //
    // Rules:
    //   - If an external object from srcSketch is out of scope → do not import; map to GeoUndef
    //   - If dstSketch already has equivalent external geometries → reuse their GeoIds
    //   - Otherwise → import by calling addExternal() and map to the newly assigned GeoIds
    //
    std::map<int, int> importExternalGeometry(
        const Sketcher::SketchObject* srcSketch,
        Sketcher::SketchObject* dstSketch,
        bool silent = false)
    {
        // extGeoIdMap    : srcGeoId -> dstGeoId (return value)
        // displayIdMap   : srcGeoId -> srcDisplayId
        // refGeoIdMap    : refName  -> list of srcGeoId
        // emptyGeoIdList : fallback empty list
        std::map<int, int> extGeoIdMap;
        std::map<int, int> displayIdMap;
        std::unordered_map<std::string, std::vector<int>> refGeoIdMap;
        std::vector<int> emptyGeoIdList;
        int srcGeoId = 0;
        for (const auto geo : srcSketch->getExternalGeometry()) {
            --srcGeoId;
            if (srcGeoId <= Sketcher::GeoEnum::RefExt) {
                extGeoIdMap[srcGeoId] = Sketcher::GeoEnum::GeoUndef;
            } else {
                extGeoIdMap[srcGeoId] = srcGeoId;
            }

            auto egf = Sketcher::ExternalGeometryFacade::getFacade(geo);
            displayIdMap[srcGeoId] = egf->getId();
            refGeoIdMap[egf->getRef()].push_back(srcGeoId);
        }

        // helper: check if the external object is in scope for dstSketch
        auto isExternalObjectInScope =
            [&](const App::DocumentObject* srcExtObj) -> bool {
            if (!srcExtObj
                || !PartGui::isModelingObjectActive(srcExtObj)
                || dstSketch->getDocument() != srcExtObj->getDocument()) {
                return false; // different documents, not in scope
            }
            auto dstBody = Part::BodyBase::findBodyOf(dstSketch);
            auto srcBody = Part::BodyBase::findBodyOf(srcExtObj);
            if (dstBody != srcBody) {
                return false; // different bodies, not in scope
            }
            return true; // in scope
        };

        // helper: find existing external GeoIds in dstSketch matching the given refName
        auto findExistingExternalGeoIds =
            [&](const std::string& refName) -> std::vector<int> {
            std::vector<int> result;
            int srcGeoId = 0;
            for (const auto& geo : dstSketch->getExternalGeometry()) {
                --srcGeoId;
                auto egf = Sketcher::ExternalGeometryFacade::getFacade(geo);
                if (egf->getRef() == refName) {
                    result.push_back(srcGeoId);
                }
            }
            return result;
        };

        // helper: update existing extGeoIdMap entries using src/dst GeoIds
        auto updateGeoIdMapping =
            [&](const std::vector<int>& srcGeoIds,
                const std::vector<int>& dstGeoIds) {
            auto src = srcGeoIds;
            auto dst = dstGeoIds;
            std::sort(src.begin(), src.end(), std::greater<int>());
            std::sort(dst.begin(), dst.end(), std::greater<int>());

            const size_t count = std::min(src.size(), dst.size());
            for (size_t i = 0; i < count; ++i) {
                auto it = extGeoIdMap.find(src[i]);
                if (it != extGeoIdMap.end()) {
                    it->second = dst[i];
                }
            }
        };

        // helper: print warnings for skipped external geometry displayIds
        auto printSkippedDisplayIds =
            [&](const std::vector<int>& skippedGeoIds) {
            for (const auto& srcGeoId : skippedGeoIds) {
                auto displayId = displayIdMap.count(srcGeoId)
                                ? displayIdMap.at(srcGeoId)
                                : Sketcher::GeoEnum::GeoUndef;
                QString msg = qApp->translate(
                    "CmdSketcherMergeSketches",
                    "Skipping external geometry #%1\n")
                    .arg(displayId);
                Base::Console().message(msg.toUtf8().constData());
            }
        };

        // helper: get refName-style subNames from srcSketch (without old suffixes)
        auto getExtSubs =
            [&]() -> std::vector<std::string> {
            auto newSubs = srcSketch->ExternalGeometry.getSubValues(true);
            const auto& oldSubs = srcSketch->ExternalGeometry.getSubValues(false);
            const size_t count = std::min(newSubs.size(), oldSubs.size());
            for (size_t i = 0; i < count; ++i) {
                std::string suffix = std::string(".") + oldSubs[i];
                if (newSubs[i].ends_with(suffix)) {
                    newSubs[i].erase(newSubs[i].size() - suffix.size());
                }
            }
            return newSubs;
        };

        // --- main processing starts here ---

        const auto& srcExtObjs = srcSketch->ExternalGeometry.getValues();
        const auto& srcExtSubs = getExtSubs();
        const auto& srcOldSubs = srcSketch->ExternalGeometry.getSubValues(false);

        for (size_t i = 0; i < srcExtObjs.size(); ++i) {
            const auto& srcExtObj = srcExtObjs[i];
            const auto& srcExtSub = srcExtSubs[i];
            const auto& srcOldSub = srcOldSubs[i];

            std::string refName = std::string(srcExtObj->getNameInDocument()) + "." + srcExtSub;
            const auto& srcGeoIds = (refGeoIdMap.count(refName)
                                  ? refGeoIdMap.at(refName)
                                  : emptyGeoIdList);
            std::string oldRefName = std::string(srcExtObj->getNameInDocument()) + "." + srcOldSub;

            // 1) Reject out-of-scope external object
            if (!isExternalObjectInScope(srcExtObj)) {
                if (!silent) {
                    QString msg = qApp->translate(
                        "CmdSketcherMergeSketches",
                        "External geometry '%1' is out of scope:\n")
                        .arg(oldRefName.c_str());
                    Base::Console().message(msg.toUtf8().constData());
                    printSkippedDisplayIds(srcGeoIds);
                }
                continue;
            }

            // 2) Reuse existing external geometries if present
            auto existingGeoIds = findExistingExternalGeoIds(refName);
            if (!existingGeoIds.empty()) {
                updateGeoIdMapping(srcGeoIds, existingGeoIds);
                continue;
            }

            // 3) Add new external geometry to dst
            int beforeCount = dstSketch->getExternalGeometryCount();
            int result = dstSketch->addExternal(srcExtObj, srcExtSub.c_str());
            int afterCount = dstSketch->getExternalGeometryCount();

            // addExternal() failed
            if (result < 0) {
                if (!silent) {
                    printSkippedDisplayIds(srcGeoIds);
                }
                continue;
            }

            // getExternalGeometryCount() includes H/V axes,
            // so -beforeCount is the last valid GeoId.
            // Therefore, the new GeoIds are from -(beforeCount+1) to -afterCount.
            std::vector<int> dstGeoIds;
            for (int j = beforeCount + 1; j <= afterCount; ++j) {
                dstGeoIds.push_back(-j);
            }
            updateGeoIdMapping(srcGeoIds, dstGeoIds);
        }

        return extGeoIdMap;
    }
}

DEF_STD_CMD_A(CmdSketcherMergeSketches)

CmdSketcherMergeSketches::CmdSketcherMergeSketches()
    : Command("Sketcher_MergeSketches")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Merge Sketches");
    sToolTipText = QT_TR_NOOP("Creates a new sketch by merging at least 2 selected sketches");
    sWhatsThis = "Sketcher_MergeSketches";
    sStatusTip = sToolTipText;
    eType = 0;
    sPixmap = "Sketcher_MergeSketch";
}

void CmdSketcherMergeSketches::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    auto* guiDocument = getActiveGuiDocument();
    auto* document = guiDocument ? guiDocument->getDocument() : nullptr;
    const auto documentIdentity =
        exactSketchDocumentIdentity(document);
    const auto selection = selectedExactUsableSketches(document);
    if (selection.size() < 2) {
        Gui::TranslatedUserWarning(
            guiDocument,
            qApp->translate("CmdSketcherMergeSketches", "Wrong selection"),
            qApp->translate("CmdSketcherMergeSketches", "Select at least 2 sketches"));
        return;
    }

    document = resolveExactSketchDocument(documentIdentity);
    if (!document || getActiveGuiDocument() != guiDocument
        || !isSketchSetupAvailable(guiDocument)) {
        return;
    }
    std::set<Part::BodyBase*> bodies;
    for (const auto& sourceIdentity : selection) {
        const auto* source =
            resolveExactUsableStandaloneSketch(sourceIdentity);
        if (!source || source->getDocument() != document) {
            return;
        }
        bodies.insert(Part::BodyBase::findBodyOf(source));
    }

    ExactSketchObjectIdentity targetBodyIdentity;
    if (bodies.size() == 1 && *bodies.begin() != nullptr) {
        auto* targetBody = *bodies.begin();
        if (!PartGui::isModelingObjectActive(targetBody)) {
            return;
        }
        targetBodyIdentity = exactSketchObjectIdentity(targetBody);
    }

    const std::string featureName =
        document->getUniqueObjectName("Sketch");
    QByteArray factory;
    if (targetBodyIdentity.id > 0) {
        auto* targetBody = resolveExactUsableSketchObject(
            targetBodyIdentity
        );
        if (!targetBody) {
            return;
        }
        // all sketches belong to the same body → create merged sketch inside the body
        factory =
            Gui::Command::getObjectCmd(targetBody).c_str();
        factory += ".newObject('Sketcher::SketchObject', '";
        factory += featureName.c_str();
        factory += "')";
    }
    else {
        // otherwise, create the merged sketch at the document level
        factory = "App.getDocument('";
        factory += document->getName();
        factory += "').addObject('Sketcher::SketchObject', '";
        factory += featureName.c_str();
        factory += "')";
    }

    const int existingTransaction =
        document->getBookedTransactionID();
    const int transactionId = openCommand(
        document,
        QT_TRANSLATE_NOOP("Command", "Merge sketches")
    );
    if (transactionId == App::NullTransaction
        || document->getBookedTransactionID() != transactionId
        || !App::GetApplication().transactionIsActive(transactionId)) {
        if (existingTransaction == App::NullTransaction
            && transactionId != App::NullTransaction
            && document->getBookedTransactionID() == transactionId) {
            abortCommand(transactionId);
        }
        resetTransactionID();
        return;
    }

    try {
        auto* mergeSketch = freecad_cast<Sketcher::SketchObject*>(
            Gui::Command::runDocumentObjectCommand(
                Gui::Command::Doc,
                *document,
                factory,
                Sketcher::SketchObject::getClassTypeId()));
        if (!mergeSketch) {
            throw Base::RuntimeError("Merge Sketches returned an incompatible result");
        }

        const auto mergeIdentity =
            exactSketchObjectIdentity(mergeSketch);
        const auto requireMergeSketch = [&]() {
            auto* exactMergeSketch =
                resolveExactStandaloneSketch(mergeIdentity);
            if (!exactMergeSketch) {
                throw Base::RuntimeError(
                    "The Merge Sketches output changed during creation"
                );
            }
            return exactMergeSketch;
        };

        int baseGeometry = 0;
        int baseConstraints = 0;
        std::vector<int> constraintsToDelete;

        const auto remapGeoId =
            [&](int& geoId,
                const std::map<int, int>& extGeoIdMap) -> bool {
            if (geoId == Sketcher::GeoEnum::GeoUndef
                || geoId == Sketcher::GeoEnum::HAxis
                || geoId == Sketcher::GeoEnum::VAxis) {
                return true;
            }

            if (geoId <= Sketcher::GeoEnum::RefExt) {
                const auto mapping = extGeoIdMap.find(geoId);
                if (mapping == extGeoIdMap.end()
                    || mapping->second
                        == Sketcher::GeoEnum::GeoUndef) {
                    return false;
                }
                geoId = mapping->second;
                return true;
            }

            mergeSketch = requireMergeSketch();
            const int newId = geoId + baseGeometry;
            if (newId < 0
                || newId >= static_cast<int>(
                    mergeSketch->getInternalGeometry().size()
                )) {
                return false;
            }
            geoId = newId;
            return true;
        };

        for (const auto& sourceIdentity : selection) {
            auto* source =
                resolveExactUsableStandaloneSketch(sourceIdentity);
            if (!source || source->getDocument() != document) {
                throw Base::RuntimeError(
                    "A Merge Sketches source changed before it was copied"
                );
            }
            const std::string sourceName =
                source->getNameInDocument();
            const int sourceConstraintCount =
                source->Constraints.getSize();

            mergeSketch = requireMergeSketch();
            const int afterGeometry =
                1 + mergeSketch->addGeometry(
                    source->getInternalGeometry()
                );

            source =
                resolveExactUsableStandaloneSketch(sourceIdentity);
            mergeSketch = requireMergeSketch();
            if (!source) {
                throw Base::RuntimeError(
                    "A Merge Sketches source changed while copying geometry"
                );
            }
            const auto externalGeometryMap =
                importExternalGeometry(source, mergeSketch);

            source =
                resolveExactUsableStandaloneSketch(sourceIdentity);
            mergeSketch = requireMergeSketch();
            if (!source) {
                throw Base::RuntimeError(
                    "A Merge Sketches source changed while copying external geometry"
                );
            }
            const int afterConstraints =
                1 + mergeSketch->addCopyOfConstraints(*source);
            const int addedConstraints =
                afterConstraints - baseConstraints;
            if (addedConstraints < 0) {
                throw Base::ValueError(
                    "Constraint error in CmdSketcherMergeSketches"
                );
            }
            if (addedConstraints != sourceConstraintCount) {
                const QString message = qApp->translate(
                    "CmdSketcherMergeSketches",
                    "Copied %1 of %2 constraints from '%3'. Some were skipped.\n")
                    .arg(addedConstraints)
                    .arg(sourceConstraintCount)
                    .arg(QString::fromStdString(sourceName));
                Base::Console().message(
                    message.toUtf8().constData()
                );
            }
            mergeSketch = requireMergeSketch();
            for (int offset = 0;
                 offset < addedConstraints;
                 ++offset) {
                const int index = offset + baseConstraints;
                auto* constraint =
                    mergeSketch->Constraints.getValues()[index];
                if (!remapGeoId(
                        constraint->First,
                        externalGeometryMap
                    )
                    || !remapGeoId(
                        constraint->Second,
                        externalGeometryMap
                    )
                    || !remapGeoId(
                        constraint->Third,
                        externalGeometryMap
                    )) {
                    constraintsToDelete.push_back(index);
                    const QString message = qApp->translate(
                        "CmdSketcherMergeSketches",
                        "Skipping constraint #%1 of '%2': references "
                        "unmerged geometry.\n")
                        .arg(offset + 1)
                        .arg(QString::fromStdString(sourceName));
                    Base::Console().message(
                        message.toUtf8().constData()
                    );
                }
            }

            baseGeometry = afterGeometry;
            baseConstraints = afterConstraints;
        }

        std::ranges::sort(
            constraintsToDelete,
            std::greater<int>()
        );
        for (const int index : constraintsToDelete) {
            requireMergeSketch()->delConstraint(index);
        }

        auto* firstSource =
            resolveExactUsableStandaloneSketch(selection.front());
        mergeSketch = requireMergeSketch();
        if (!firstSource) {
            throw Base::RuntimeError(
                "The first Merge Sketches source changed before placement"
            );
        }
        doCommand(
            Doc,
            "%s.Placement = %s.Placement",
            Gui::Command::getObjectCmd(mergeSketch).c_str(),
            Gui::Command::getObjectCmd(firstSource).c_str()
        );
        mergeSketch = requireMergeSketch();
        markSketchCommandOutputs({mergeSketch});

        auto* exactDocument =
            resolveExactSketchDocument(documentIdentity);
        if (!exactDocument) {
            throw Base::RuntimeError(
                "The Merge Sketches document changed before recompute"
            );
        }
        Gui::cmdAppDocument(exactDocument, "recompute()");
        commitCommand(transactionId);
        resetTransactionID();
    }
    catch (Base::Exception& error) {
        abortCommand(transactionId);
        resetTransactionID();
        error.reportException();
    }
    catch (...) {
        abortCommand(transactionId);
        resetTransactionID();
        throw;
    }
}

bool CmdSketcherMergeSketches::isActive()
{
    auto* guiDocument = getActiveGuiDocument();
    return isSketchSetupAvailable(guiDocument)
        && selectedExactUsableSketches(
               guiDocument->getDocument()
           ).size() > 1;
}

// Acknowledgement of idea and original python macro goes to SpritKopf:
// https://github.com/Spritkopf/freecad-macros/blob/master/clip-sketch/clip_sketch.FCMacro
// https://forum.freecad.org/viewtopic.php?p=231481#p231085
DEF_STD_CMD_A(CmdSketcherViewSection)

CmdSketcherViewSection::CmdSketcherViewSection()
    : Command("Sketcher_ViewSection")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Toggle Section View");
    sToolTipText = QT_TR_NOOP("Toggles between section view and full view");
    sWhatsThis = "Sketcher_ViewSection";
    sStatusTip = sToolTipText;
    sPixmap = "Sketcher_ViewSection";
    sAccel = "Q, S";
    eType = 0;
}

void CmdSketcherViewSection::activated(int iMsg)
{
    Q_UNUSED(iMsg);
    QString cmdStr =
        QLatin1String("ActiveSketch.ViewObject.TempoVis.sketchClipPlane(ActiveSketch, Gui.ActiveDocument, None, %1)\n");
    Gui::Document* doc = getActiveGuiDocument();

    bool revert = false;
    if (doc) {
        SketcherGui::ViewProviderSketch* vp =
            dynamic_cast<SketcherGui::ViewProviderSketch*>(doc->getInEdit());
        if (vp) {
            revert = vp->getViewOrientationFactor() < 0 ? true : false;
        }
    }
    cmdStr = cmdStr.arg(revert ? QLatin1String("True") : QLatin1String("False"));
    doCommand(Doc, cmdStr.toLatin1());
}

bool CmdSketcherViewSection::isActive()
{
    return isSketchInEdit(getActiveGuiDocument());
}

/* Grid tool */
GridSpaceAction::GridSpaceAction(QObject* parent)
    : QWidgetAction(parent)
{
    setEnabled(false);
}

void GridSpaceAction::updateWidget()
{
    auto* sketchView = getView();

    if (sketchView) {

        auto updateCheckBox = [](QCheckBox* checkbox, bool value) {
            auto checked = checkbox->checkState() == Qt::Checked;

            if (value != checked) {
                const QSignalBlocker blocker(checkbox);
                checkbox->setChecked(value);
            }
        };

        auto updateCheckBoxFromProperty = [updateCheckBox](QCheckBox* checkbox,
                                                            App::PropertyBool& property) {
            auto propvalue = property.getValue();

            updateCheckBox(checkbox, propvalue);
        };

        updateCheckBoxFromProperty(gridShow, sketchView->ShowGrid);

        updateCheckBoxFromProperty(gridAutoSpacing, sketchView->GridAuto);

        ParameterGrp::handle hGrp = getParameterPath();
        updateCheckBox(snapToGrid, hGrp->GetBool("SnapToGrid", false));

        gridSizeBox->setValue(sketchView->GridSize.getValue());
    }
}

void GridSpaceAction::languageChange()
{
    gridShow->setText(tr("Display grid"));
    gridShow->setToolTip(tr("Toggles the visibility of the grid in the active sketch"));
    gridShow->setStatusTip(gridAutoSpacing->toolTip());

    gridAutoSpacing->setText(tr("Grid auto-spacing"));
    gridAutoSpacing->setToolTip(tr("Automatically adjusts the grid spacing based on the zoom level"));
    gridAutoSpacing->setStatusTip(gridAutoSpacing->toolTip());

    sizeLabel->setText(tr("Spacing"));
    gridSizeBox->setToolTip(tr("Distance between two subsequent grid lines"));

    snapToGrid->setText(tr("Snap to grid"));
    snapToGrid->setToolTip(
        tr("New points will snap to the nearest grid line.\nPoints must be set closer than a "
            "fifth of the grid spacing to a grid line to snap."));
    snapToGrid->setStatusTip(snapToGrid->toolTip());
}

QWidget* GridSpaceAction::createWidget(QWidget* parent)
{
    gridShow = new QCheckBox();

    gridAutoSpacing = new QCheckBox();

    snapToGrid = new QCheckBox();

    sizeLabel = new QLabel();

    gridSizeBox = new Gui::QuantitySpinBox();
    gridSizeBox->setProperty("unit", QVariant(QStringLiteral("mm")));
    gridSizeBox->setObjectName(QStringLiteral("gridSize"));
    gridSizeBox->setMaximum(99999999.0);
    gridSizeBox->setMinimum(0.001);

    QWidget* gridSizeW = new QWidget(parent);
    auto* layout = new QGridLayout(gridSizeW);
    layout->addWidget(gridShow, 0, 0, 1, 2);
    layout->addWidget(gridAutoSpacing, 1, 0, 1, 2);
    layout->addWidget(snapToGrid, 2, 0, 1, 2);
    layout->addWidget(sizeLabel, 3, 0);
    layout->addWidget(gridSizeBox, 3, 1);

    languageChange();

#if QT_VERSION >= QT_VERSION_CHECK(6,7,0)
    QObject::connect(gridShow, &QCheckBox::checkStateChanged, [this](int state) {
#else
    QObject::connect(gridShow, &QCheckBox::stateChanged, [this](int state) {
#endif
        auto* sketchView = getView();

        if (sketchView) {
            auto enable = (state == Qt::Checked);
            sketchView->ShowGrid.setValue(enable);
        }
    });

#if QT_VERSION >= QT_VERSION_CHECK(6,7,0)
    QObject::connect(gridAutoSpacing, &QCheckBox::checkStateChanged, [this](int state) {
#else
    QObject::connect(gridAutoSpacing, &QCheckBox::stateChanged, [this](int state) {
#endif
        auto* sketchView = getView();

        if (sketchView) {
            auto enable = (state == Qt::Checked);
            sketchView->GridAuto.setValue(enable);
        }
    });

#if QT_VERSION >= QT_VERSION_CHECK(6,7,0)
    QObject::connect(snapToGrid, &QCheckBox::checkStateChanged, [this](int state) {
#else
    QObject::connect(snapToGrid, &QCheckBox::stateChanged, [this](int state) {
#endif
        ParameterGrp::handle hGrp = this->getParameterPath();
        hGrp->SetBool("SnapToGrid", state == Qt::Checked);
    });

    QObject::connect(gridSizeBox,
                        qOverload<double>(&Gui::QuantitySpinBox::valueChanged),
                        [this](double val) {
                            auto* sketchView = getView();
                            if (sketchView) {
                                sketchView->GridSize.setValue(val);
                            }
                        });

    return gridSizeW;
}

ViewProviderSketch* GridSpaceAction::getView()
{
    Gui::Document* doc = Gui::Application::Instance->activeDocument();

    if (doc) {
        return dynamic_cast<SketcherGui::ViewProviderSketch*>(doc->getInEdit());
    }

    return nullptr;
}

ParameterGrp::handle GridSpaceAction::getParameterPath()
    {
        return App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/Mod/Sketcher/Snap");
    }


class CmdSketcherGrid: public Gui::Command
{
public:
    CmdSketcherGrid();
    ~CmdSketcherGrid() override
    {}
    const char* className() const override
    {
        return "CmdSketcherGrid";
    }
    void languageChange() override;

protected:
    void activated(int iMsg) override;
    bool isActive() override;
    Gui::Action* createAction() override;

public:
    CmdSketcherGrid(const CmdSketcherGrid&) = delete;
    CmdSketcherGrid(CmdSketcherGrid&&) = delete;
    CmdSketcherGrid& operator=(const CmdSketcherGrid&) = delete;
    CmdSketcherGrid& operator=(CmdSketcherGrid&&) = delete;
};

CmdSketcherGrid::CmdSketcherGrid()
    : Command("Sketcher_Grid")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Toggle Grid");
    sToolTipText =
        QT_TR_NOOP("Toggles the grid display in the active sketch");
    sWhatsThis = "Sketcher_Grid";
    sStatusTip = sToolTipText;
    eType = 0;
}

void CmdSketcherGrid::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    Gui::Document* doc = getActiveGuiDocument();
    assert(doc);
    auto* sketchView = dynamic_cast<SketcherGui::ViewProviderSketch*>(doc->getInEdit());
    assert(sketchView);

    auto value = sketchView->ShowGrid.getValue();
    sketchView->ShowGrid.setValue(!value);
}

Gui::Action* CmdSketcherGrid::createAction()
{
    auto* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    pcAction->setExclusive(false);
    applyCommandData(this->className(), pcAction);

    GridSpaceAction* gsa = new GridSpaceAction(pcAction);
    pcAction->addAction(gsa);

    _pcAction = pcAction;

    QObject::connect(pcAction, &Gui::ActionGroup::aboutToShow, [gsa](QMenu* menu) {
        Q_UNUSED(menu)
        gsa->updateWidget();
    });

    return pcAction;
}

void CmdSketcherGrid::languageChange()
{
    Command::languageChange();

    if (!_pcAction)
        return;

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    auto* gsa = static_cast<GridSpaceAction*>(a[0]);
    gsa->languageChange();
}

bool CmdSketcherGrid::isActive()
{
    auto* vp = getInactiveHandlerEditModeSketchViewProvider();

    if (vp) {
        return true;
    }

    return false;
}

/* Snap tool */
SnapSpaceAction::SnapSpaceAction(QObject* parent)
        : QWidgetAction(parent)
    {
        setEnabled(false);
    }

void SnapSpaceAction::updateWidget(bool snapenabled)
{

    auto updateCheckBox = [](QCheckBox* checkbox, bool value) {
        auto checked = checkbox->checkState() == Qt::Checked;

        if (value != checked) {
            const QSignalBlocker blocker(checkbox);
            checkbox->setChecked(value);
        }
    };

    auto updateSpinBox = [](Gui::QuantitySpinBox* spinbox, double value) {
        auto currentvalue = spinbox->rawValue();

        if (currentvalue != value) {
            const QSignalBlocker blocker(spinbox);
            spinbox->setValue(value);
        }
    };

    ParameterGrp::handle hGrp = getParameterPath();

    updateCheckBox(snapToObjects, hGrp->GetBool("SnapToObjects", true));

    updateSpinBox(snapAngle, hGrp->GetFloat("SnapAngle", 5.0));

    snapToObjects->setEnabled(snapenabled);
    angleLabel->setEnabled(snapenabled);
    snapAngle->setEnabled(snapenabled);
}

void SnapSpaceAction::languageChange()
{
    snapToObjects->setText(tr("Snap to objects"));
    snapToObjects->setToolTip(tr("New points will snap to the currently preselected object. It "
                                    "will also snap to the middle of lines and arcs."));
    snapToObjects->setStatusTip(snapToObjects->toolTip());

    angleLabel->setText(tr("Snap angle"));
    snapAngle->setToolTip(
        tr("Angular step for tools that use 'Snap at angle'. Hold Ctrl to "
            "enable 'Snap at angle'. The angle starts from the positive X axis of the sketch."));
}

QWidget* SnapSpaceAction::createWidget(QWidget* parent)
{
    snapToObjects = new QCheckBox();

    angleLabel = new QLabel();

    snapAngle = new Gui::QuantitySpinBox();
    snapAngle->setProperty("unit", QVariant(QStringLiteral("deg")));
    snapAngle->setObjectName(QStringLiteral("snapAngle"));
    snapAngle->setMaximum(99999999.0);
    snapAngle->setMinimum(0);

    QWidget* snapW = new QWidget(parent);
    auto* layout = new QGridLayout(snapW);
    layout->addWidget(snapToObjects, 0, 0, 1, 2);
    layout->addWidget(angleLabel, 1, 0);
    layout->addWidget(snapAngle, 1, 1);

    languageChange();

#if QT_VERSION >= QT_VERSION_CHECK(6,7,0)
    QObject::connect(snapToObjects, &QCheckBox::checkStateChanged, [this](int state) {
#else
    QObject::connect(snapToObjects, &QCheckBox::stateChanged, [this](int state) {
#endif
        ParameterGrp::handle hGrp = this->getParameterPath();
        hGrp->SetBool("SnapToObjects", state == Qt::Checked);
    });

    QObject::connect(
        snapAngle, qOverload<double>(&Gui::QuantitySpinBox::valueChanged), [this](double val) {
            ParameterGrp::handle hGrp = this->getParameterPath();
            hGrp->SetFloat("SnapAngle", val);
        });

    return snapW;
}

ParameterGrp::handle SnapSpaceAction::getParameterPath()
{
    return App::GetApplication().GetParameterGroupByPath(
        "User parameter:BaseApp/Preferences/Mod/Sketcher/Snap");
}


class CmdSketcherSnap: public Gui::Command, public ParameterGrp::ObserverType
{
public:
    CmdSketcherSnap();
    ~CmdSketcherSnap() override;
    CmdSketcherSnap(const CmdSketcherSnap&) = delete;
    CmdSketcherSnap(CmdSketcherSnap&&) = delete;
    CmdSketcherSnap& operator=(const CmdSketcherSnap&) = delete;
    CmdSketcherSnap& operator=(CmdSketcherSnap&&) = delete;
    const char* className() const override
    {
        return "CmdSketcherSnap";
    }
    void languageChange() override;

    void OnChange(Base::Subject<const char*>& rCaller, const char* sReason) override;

protected:
    void activated(int iMsg) override;
    bool isActive() override;
    Gui::Action* createAction() override;

private:
    ParameterGrp::handle getParameterPath()
    {
        return App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/Mod/Sketcher/Snap");
    }

    bool snapEnabled = true;
};

CmdSketcherSnap::CmdSketcherSnap()
    : Command("Sketcher_Snap")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Toggle Snap");
    sToolTipText =
        QT_TR_NOOP("Toggles snapping");
    sWhatsThis = "Sketcher_Snap";
    sStatusTip = sToolTipText;
    eType = 0;

    ParameterGrp::handle hGrp = this->getParameterPath();
    hGrp->Attach(this);
}

CmdSketcherSnap::~CmdSketcherSnap()
{

    ParameterGrp::handle hGrp = this->getParameterPath();
    hGrp->Detach(this);
}

void CmdSketcherSnap::OnChange(Base::Subject<const char*>& rCaller, const char* sReason)
{
    Q_UNUSED(rCaller)

    if (strcmp(sReason, "Snap") == 0) {
        snapEnabled = getParameterPath()->GetBool("Snap", true);
    }
}

void CmdSketcherSnap::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    getParameterPath()->SetBool("Snap", !snapEnabled);

    // Update the widget :
    if (!_pcAction)
        return;

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    auto* ssa = static_cast<SnapSpaceAction*>(a[0]);
    ssa->updateWidget(snapEnabled);
}

Gui::Action* CmdSketcherSnap::createAction()
{
    auto* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    pcAction->setExclusive(false);
    applyCommandData(this->className(), pcAction);

    SnapSpaceAction* ssa = new SnapSpaceAction(pcAction);
    pcAction->addAction(ssa);

    _pcAction = pcAction;

    QObject::connect(pcAction, &Gui::ActionGroup::aboutToShow, [ssa, this](QMenu* menu) {
        Q_UNUSED(menu)
        ssa->updateWidget(snapEnabled);
    });

    return pcAction;
}

void CmdSketcherSnap::languageChange()
{
    Command::languageChange();

    if (!_pcAction)
        return;

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    auto* ssa = static_cast<SnapSpaceAction*>(a[0]);
    ssa->languageChange();
}

bool CmdSketcherSnap::isActive()
{
    auto* vp = getInactiveHandlerEditModeSketchViewProvider();

    if (vp) {
        return true;
    }

    return false;
}


/* Rendering Order */
RenderingOrderAction::RenderingOrderAction(QObject* parent)
        : QWidgetAction(parent)
{
    setEnabled(false);
}

void RenderingOrderAction::updateWidget()
{
    auto hGrp = getParameterPath();

    // 1->Normal Geometry, 2->Construction, 3->External
    int topid = hGrp->GetInt("TopRenderGeometryId", 1);
    int midid = hGrp->GetInt("MidRenderGeometryId", 2);
    int lowid = hGrp->GetInt("LowRenderGeometryId", 3);

    auto idToText = [](int id) -> QString {
        switch (id) {
        case 1:
            return tr("Normal geometry");
        case 2:
            return tr("Construction geometry");
        case 3:
            return tr("External geometry");
        default:
            // Fallback for an unexpected ID
            return tr("Unknown geometry");
        }
    };

    {
        QSignalBlocker block(this);
        list->clear();

        QListWidgetItem* itemTop = new QListWidgetItem;
        itemTop->setData(Qt::UserRole, QVariant(topid));
        itemTop->setText(idToText(topid));
        list->insertItem(0, itemTop);

        QListWidgetItem* itemMid = new QListWidgetItem;
        itemMid->setData(Qt::UserRole, QVariant(midid));
        itemMid->setText(idToText(midid));
        list->insertItem(1, itemMid);

        QListWidgetItem* itemLow = new QListWidgetItem;
        itemLow->setData(Qt::UserRole, QVariant(lowid));
        itemLow->setText(idToText(lowid));
        list->insertItem(2, itemLow);
    }
}

void RenderingOrderAction::languageChange()
{
    updateWidget();
}

QWidget* RenderingOrderAction::createWidget(QWidget* parent)
{
    list = new QListWidget();
    list->setDragDropMode(QAbstractItemView::InternalMove);
    list->setDefaultDropAction(Qt::MoveAction);
    list->setSelectionMode(QAbstractItemView::SingleSelection);
    list->setDragEnabled(true);
    list->setFixedSize(200, 50);


    QWidget* renderingWidget = new QWidget(parent);
    auto* label = new QLabel(tr("Rendering order"), renderingWidget);
    auto* layout = new QVBoxLayout(renderingWidget);
    layout->addWidget(label);
    layout->addWidget(list);

    languageChange();

    // Handle change in the order of the list entries
    QObject::connect(list->model(),
                        &QAbstractItemModel::rowsMoved,
                        [this](const QModelIndex& sourceParent,
                            int sourceStart,
                            int sourceEnd,
                            const QModelIndex& destinationParent,
                            int destinationRow) {
                            Q_UNUSED(sourceParent)
                            Q_UNUSED(sourceStart)
                            Q_UNUSED(sourceEnd)
                            Q_UNUSED(destinationParent)
                            Q_UNUSED(destinationRow)

                            int topid = list->item(0)->data(Qt::UserRole).toInt();
                            int midid = list->item(1)->data(Qt::UserRole).toInt();
                            int lowid = list->item(2)->data(Qt::UserRole).toInt();

                            auto hGrp = getParameterPath();

                            hGrp->SetInt("TopRenderGeometryId", topid);
                            hGrp->SetInt("MidRenderGeometryId", midid);
                            hGrp->SetInt("LowRenderGeometryId", lowid);
                        });

    return renderingWidget;
}

ParameterGrp::handle RenderingOrderAction::getParameterPath()
    {
        return App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/Mod/Sketcher/General");
    }


class CmdRenderingOrder: public Gui::Command, public ParameterGrp::ObserverType
{
    enum class ElementType
    {
        Normal = 1,
        Construction = 2,
        External = 3,
    };

public:
    CmdRenderingOrder();
    ~CmdRenderingOrder() override;
    CmdRenderingOrder(const CmdRenderingOrder&) = delete;
    CmdRenderingOrder(CmdRenderingOrder&&) = delete;
    CmdRenderingOrder& operator=(const CmdRenderingOrder&) = delete;
    CmdRenderingOrder& operator=(CmdRenderingOrder&&) = delete;
    const char* className() const override
    {
        return "CmdRenderingOrder";
    }
    void languageChange() override;
    void OnChange(Base::Subject<const char*>& rCaller, const char* sReason) override;

protected:
    void activated(int iMsg) override;
    bool isActive() override;
    Gui::Action* createAction() override;

private:
    ParameterGrp::handle getParameterPath()
    {
        return App::GetApplication().GetParameterGroupByPath(
            "User parameter:BaseApp/Preferences/Mod/Sketcher/General");
    }

    ElementType TopElement = ElementType::Normal;
};

CmdRenderingOrder::CmdRenderingOrder()
    : Command("Sketcher_RenderingOrder")
{
    sAppModule = "Sketcher";
    sGroup = "Sketcher";
    sMenuText = QT_TR_NOOP("Rendering Order");
    sToolTipText = QT_TR_NOOP("Reorders items in the rendering order");
    sWhatsThis = "Sketcher_RenderingOrder";
    sStatusTip = sToolTipText;
    eType = 0;

    ParameterGrp::handle hGrp = this->getParameterPath();
    hGrp->Attach(this);

    TopElement = static_cast<ElementType>(getParameterPath()->GetInt("TopRenderGeometryId", 1));
}

CmdRenderingOrder::~CmdRenderingOrder()
{

    ParameterGrp::handle hGrp = this->getParameterPath();
    hGrp->Detach(this);
}

void CmdRenderingOrder::OnChange(Base::Subject<const char*>& rCaller, const char* sReason)
{
    Q_UNUSED(rCaller)

    if (strcmp(sReason, "TopRenderGeometryId") == 0) {
        TopElement = static_cast<ElementType>(getParameterPath()->GetInt("TopRenderGeometryId", 1));
    }
}

void CmdRenderingOrder::activated(int iMsg)
{
    Q_UNUSED(iMsg);
}

Gui::Action* CmdRenderingOrder::createAction()
{
    auto* pcAction = new Gui::ActionGroup(this, Gui::getMainWindow());
    pcAction->setDropDownMenu(true);
    pcAction->setExclusive(false);
    applyCommandData(this->className(), pcAction);

    RenderingOrderAction* roa = new RenderingOrderAction(pcAction);
    pcAction->addAction(roa);

    _pcAction = pcAction;

    QObject::connect(pcAction, &Gui::ActionGroup::aboutToShow, [roa](QMenu* menu) {
        Q_UNUSED(menu)
        roa->updateWidget();
    });

    return pcAction;
}

void CmdRenderingOrder::languageChange()
{
    Command::languageChange();

    if (!_pcAction)
        return;

    Gui::ActionGroup* pcAction = qobject_cast<Gui::ActionGroup*>(_pcAction);
    QList<QAction*> a = pcAction->actions();

    auto* roa = static_cast<RenderingOrderAction*>(a[0]);
    roa->languageChange();
}

bool CmdRenderingOrder::isActive()
{
    return isSketchInEdit(getActiveGuiDocument());
    ;
}


void CreateSketcherCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdSketcherNewSketch());
    rcCmdMgr.addCommand(new CmdSketcherCancelSketch());
    rcCmdMgr.addCommand(new CmdSketcherEditSketch());
    rcCmdMgr.addCommand(new CmdSketcherLeaveSketch());
    rcCmdMgr.addCommand(new CmdSketcherLeaveGroup());
    rcCmdMgr.addCommand(new CmdSketcherStopOperation());
    rcCmdMgr.addCommand(new CmdSketcherReorientSketch());
    rcCmdMgr.addCommand(new CmdSketcherMapSketch());
    rcCmdMgr.addCommand(new CmdSketcherViewSketch());
    rcCmdMgr.addCommand(new CmdSketcherValidateSketch());
    rcCmdMgr.addCommand(new CmdSketcherMirrorSketch());
    rcCmdMgr.addCommand(new CmdSketcherMergeSketches());
    rcCmdMgr.addCommand(new CmdSketcherViewSection());
    rcCmdMgr.addCommand(new CmdSketcherGrid());
    rcCmdMgr.addCommand(new CmdSketcherSnap());
    rcCmdMgr.addCommand(new CmdRenderingOrder());
}
// clang-format on
