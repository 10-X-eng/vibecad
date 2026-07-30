// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2015 Stefan Tröger <stefantroeger@gmx.net>              *
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


#include <QAction>
#include <QMessageBox>
#include <QMetaObject>

#include <algorithm>
#include <unordered_map>
#include <unordered_set>
#include <utility>

#include <App/Application.h>
#include <App/DocumentObject.h>
#include <App/Origin.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Tools.h>
#include <Gui/ViewProvider.h>
#include <Gui/Widgets.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/FeaturePipe.h>

#include "ui_TaskPipeParameters.h"
#include "ui_TaskPipeOrientation.h"
#include "ui_TaskPipeScaling.h"
#include <ui_DlgReference.h>

#include "TaskDialogState.h"
#include "TaskPipeParameters.h"
#include "TaskFeaturePick.h"
#include "TaskSketchBasedParameters.h"
#include "Utils.h"


Q_DECLARE_METATYPE(App::PropertyLinkSubList::SubSet)

using namespace PartDesignGui;
using namespace Gui;

namespace
{
std::unordered_map<
    const TaskPipeParameters*,
    TaskInternal::VisibilitySnapshot>
    pipeInputVisibility;
std::unordered_map<
    const PartDesign::Pipe*,
    const TaskPipeParameters*>
    pipeTaskOwners;

void rememberPipeInputVisibility(
    const TaskPipeParameters* task,
    App::DocumentObject* object
)
{
    if (const auto state = pipeInputVisibility.find(task);
        state != pipeInputVisibility.end()) {
        state->second.captureObject(object);
    }
}

void rememberPipeInputVisibility(
    const PartDesign::Pipe* pipe,
    App::DocumentObject* object
)
{
    if (const auto owner = pipeTaskOwners.find(pipe);
        owner != pipeTaskOwners.end()) {
        rememberPipeInputVisibility(owner->second, object);
    }
}

void restorePipeInputVisibility(
    const TaskPipeParameters* task,
    App::Document* document
)
{
    if (const auto state = pipeInputVisibility.find(task);
        state != pipeInputVisibility.end()) {
        state->second.restore(document);
    }
}

QVariant pipeSectionIdentity(
    const App::PropertyLinkSubList::SubSet& section
)
{
    QVariantMap identity;
    if (!section.first || !section.first->isAttachedToDocument()) {
        return identity;
    }
    identity.insert(
        QStringLiteral("document"),
        QString::fromLatin1(section.first->getDocument()->getName())
    );
    identity.insert(
        QStringLiteral("object"),
        QString::fromLatin1(section.first->getNameInDocument())
    );
    identity.insert(
        QStringLiteral("id"),
        QVariant::fromValue<qlonglong>(section.first->getID())
    );
    QStringList subNames;
    for (const auto& subName : section.second) {
        subNames.push_back(QString::fromStdString(subName));
    }
    identity.insert(QStringLiteral("subNames"), subNames);
    return identity;
}

bool resolvePipeSection(
    const QVariant& value,
    App::PropertyLinkSubList::SubSet& section
)
{
    const QVariantMap identity = value.toMap();
    const QString documentName =
        identity.value(QStringLiteral("document")).toString();
    const QString objectName =
        identity.value(QStringLiteral("object")).toString();
    const qlonglong objectId =
        identity.value(QStringLiteral("id"), -1).toLongLong();
    if (documentName.isEmpty() || objectName.isEmpty()
        || objectId < 0) {
        return false;
    }

    App::Document* document = nullptr;
    try {
        document = App::GetApplication().getDocument(
            documentName.toLatin1().constData()
        );
    }
    catch (...) {
    }
    auto* object = document
        ? document->getObject(objectName.toLatin1().constData())
        : nullptr;
    if (!object || object->getID() != objectId) {
        return false;
    }

    std::vector<std::string> subNames;
    for (const auto& subName :
         identity.value(QStringLiteral("subNames")).toStringList()) {
        subNames.push_back(subName.toStdString());
    }
    section = {object, std::move(subNames)};
    return true;
}

bool samePipeSection(
    const App::PropertyLinkSubList::SubSet& left,
    const App::PropertyLinkSubList::SubSet& right
)
{
    return left.first == right.first && left.second == right.second;
}
}

/* TRANSLATOR PartDesignGui::TaskPipeParameters */


//**************************************************************************
//**************************************************************************
// Task Parameter
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskPipeParameters::TaskPipeParameters(ViewProviderPipe* PipeView, bool /*newObj*/, QWidget* parent)
    : TaskSketchBasedParameters(PipeView, parent, "PartDesign_AdditivePipe", tr("Pipe Parameters"))
    , ui(new Ui_TaskPipeParameters)
    , stateHandler(nullptr)
{
    pipeInputVisibility.insert_or_assign(
        this,
        TaskInternal::VisibilitySnapshot()
    );
    // we need a separate container widget to add all controls to
    proxy = new QWidget(this);
    ui->setupUi(proxy);
    // Enable multi-selection in edges list
    ui->listWidgetReferences->setSelectionMode(QAbstractItemView::ExtendedSelection);

    // Ctrl+A should select edges list, not tree view
    auto* selectAll = new QAction(tr("Select All"), this);
    selectAll->setShortcut(QKeySequence::SelectAll);
    selectAll->setShortcutContext(Qt::WidgetShortcut);
    ui->listWidgetReferences->addAction(selectAll);
    connect(selectAll, &QAction::triggered, ui->listWidgetReferences, &QListWidget::selectAll);

    QMetaObject::connectSlotsByName(this);

    // some buttons are handled in a buttongroup
    connect(ui->buttonProfileBase, &QToolButton::toggled, this, &TaskPipeParameters::onProfileButton);
    connect(
        ui->comboBoxTransition,
        qOverload<int>(&QComboBox::currentIndexChanged),
        this,
        &TaskPipeParameters::onTransitionChanged
    );

    // Create context menu
    QAction* remove = new QAction(tr("Remove"), this);
    remove->setShortcut(Gui::QtTools::deleteKeySequence());
    remove->setShortcutContext(Qt::WidgetShortcut);

    // display shortcut behind the context menu entry
    remove->setShortcutVisibleInContextMenu(true);


    ui->listWidgetReferences->addAction(remove);
    connect(remove, &QAction::triggered, this, &TaskPipeParameters::onDeleteEdge);
    connect(ui->buttonRefRemove, &QToolButton::clicked, this, &TaskPipeParameters::onDeleteEdge);
    ui->listWidgetReferences->setContextMenuPolicy(Qt::ActionsContextMenu);

    this->groupLayout()->addWidget(proxy);

    PartDesign::Pipe* pipe = PipeView->getObject<PartDesign::Pipe>();
    pipeTaskOwners.insert_or_assign(pipe, this);
    Gui::Document* doc = PipeView->getDocument();

    // make sure the user sees all important things and load the values
    // also save visibility state to reset it later when pipe is closed
    // first the spine
    if (pipe->Spine.getValue()) {
        rememberPipeInputVisibility(this, pipe->Spine.getValue());
        auto* spineVP = doc->getViewProvider(pipe->Spine.getValue());
        if (spineVP) {
            spineVP->setVisible(true);
        }
        ui->spineBaseEdit->setText(QString::fromUtf8(pipe->Spine.getValue()->Label.getValue()));
    }
    // the profile
    if (pipe->Profile.getValue()) {
        rememberPipeInputVisibility(this, pipe->Profile.getValue());
        auto* profileVP = doc->getViewProvider(pipe->Profile.getValue());
        if (profileVP) {
            profileVP->setVisible(true);
        }
        ui->profileBaseEdit->setText(
            make2DLabel(pipe->Profile.getValue(), pipe->Profile.getSubValues())
        );
    }
    // the auxiliary spine
    if (pipe->AuxiliarySpine.getValue()) {
        rememberPipeInputVisibility(
            this,
            pipe->AuxiliarySpine.getValue()
        );
        auto* svp = doc->getViewProvider(pipe->AuxiliarySpine.getValue());
        if (svp) {
            svp->show();
        }
    }
    for (auto* section : pipe->Sections.getValues()) {
        rememberPipeInputVisibility(this, section);
    }
    // the spine edges
    std::vector<std::string> strings = pipe->Spine.getSubValues();
    for (const auto& string : strings) {
        QString label = QString::fromStdString(string);
        QListWidgetItem* item = new QListWidgetItem();
        item->setText(label);
        item->setData(Qt::UserRole, QByteArray(label.toUtf8()));
        ui->listWidgetReferences->addItem(item);
    }

    if (!strings.empty()) {
        PipeView->makeTemporaryVisible(true);
    }

    ui->comboBoxTransition->setCurrentIndex(pipe->Transition.getValue());

    updateUI();
    this->blockSelection(false);
}

TaskPipeParameters::~TaskPipeParameters()
{
    try {
        if (getObject<PartDesign::Pipe>()) {
            // Destruction is task teardown, not a model operation.  It must
            // never emit a macro line or overwrite visibility restored by
            // Accept/Cancel.
            getViewObject<ViewProviderPipe>()->highlightReferences(ViewProviderPipe::Spine, false);
            getViewObject<ViewProviderPipe>()->highlightReferences(ViewProviderPipe::Profile, false);
        }
    }
    catch (const Standard_OutOfRange&) {
    }
    catch (const Base::Exception& e) {
        // getDocument() may raise an exception
        e.reportException();
    }
    catch (const Py::Exception&) {
        Base::PyException e;  // extract the Python error text
        e.reportException();
    }
    std::erase_if(pipeTaskOwners, [this](const auto& entry) {
        return entry.second == this;
    });
    pipeInputVisibility.erase(this);
}

void TaskPipeParameters::updateUI()
{}

void TaskPipeParameters::onSelectionChanged(const Gui::SelectionChanges& msg)
{
    if (stateHandler->getSelectionMode() == StateHandlerTaskPipe::SelectionModes::none) {
        return;
    }

    if (msg.Type == Gui::SelectionChanges::AddSelection) {
        if (referenceSelected(msg)) {
            if (stateHandler->getSelectionMode() == StateHandlerTaskPipe::SelectionModes::refProfile) {
                App::Document* document = App::GetApplication().getDocument(msg.pDocName);
                App::DocumentObject* object = document ? document->getObject(msg.pObjectName)
                                                       : nullptr;
                if (object) {
                    QString label = make2DLabel(object, {msg.pSubName});
                    ui->profileBaseEdit->setText(label);
                }
            }
            else if (
                stateHandler->getSelectionMode() == StateHandlerTaskPipe::SelectionModes::refSpineEdgeAdd
            ) {
                QString sub = QString::fromStdString(msg.pSubName);
                if (!sub.isEmpty()) {
                    QListWidgetItem* item = new QListWidgetItem();
                    item->setText(sub);
                    item->setData(Qt::UserRole, QByteArray(msg.pSubName));
                    ui->listWidgetReferences->addItem(item);
                }

                App::Document* document = App::GetApplication().getDocument(msg.pDocName);
                App::DocumentObject* object = document ? document->getObject(msg.pObjectName)
                                                       : nullptr;
                if (object) {
                    QString label = QString::fromUtf8(object->Label.getValue());
                    ui->spineBaseEdit->setText(label);
                }
            }
            else if (
                stateHandler->getSelectionMode()
                == StateHandlerTaskPipe::SelectionModes::refSpineEdgeRemove
            ) {
                QString sub = QString::fromLatin1(msg.pSubName);
                if (!sub.isEmpty()) {
                    removeFromListWidget(ui->listWidgetReferences, sub);
                }
                else {
                    ui->spineBaseEdit->clear();
                }
            }
            else if (
                stateHandler->getSelectionMode() == StateHandlerTaskPipe::SelectionModes::refSpine
            ) {
                ui->listWidgetReferences->clear();

                App::Document* document = App::GetApplication().getDocument(msg.pDocName);
                App::DocumentObject* object = document ? document->getObject(msg.pObjectName)
                                                       : nullptr;
                if (object) {
                    QString label = QString::fromUtf8(object->Label.getValue());
                    ui->spineBaseEdit->setText(label);
                }
            }

            clearButtons();
            recomputeFeature();
        }

        clearButtons();
        exitSelectionMode();
    }
}

void TaskPipeParameters::onTransitionChanged(int idx)
{
    if (auto pipe = getObject<PartDesign::Pipe>()) {
        pipe->Transition.setValue(idx);
        recomputeFeature();
    }
}

void TaskPipeParameters::onProfileButton(bool checked)
{
    if (checked) {
        if (auto pipe = getObject<PartDesign::Pipe>()) {
            Gui::Document* doc = getGuiDocument();

            if (pipe->Profile.getValue()) {
                rememberPipeInputVisibility(
                    this,
                    pipe->Profile.getValue()
                );
                auto* pvp = doc->getViewProvider(pipe->Profile.getValue());
                if (pvp) {
                    pvp->setVisible(true);
                }
            }
        }
    }
}

void TaskPipeParameters::onTangentChanged(bool checked)
{
    if (auto pipe = getObject<PartDesign::Pipe>()) {
        pipe->SpineTangent.setValue(checked);
        recomputeFeature();
    }
}

void TaskPipeParameters::removeFromListWidget(QListWidget* widget, QString itemstr)
{
    QList<QListWidgetItem*> items = widget->findItems(itemstr, Qt::MatchExactly);
    if (!items.empty()) {
        for (auto item : items) {
            QListWidgetItem* it = widget->takeItem(widget->row(item));
            delete it;
        }
    }
}

void TaskPipeParameters::onDeleteEdge()
{
    auto items = ui->listWidgetReferences->selectedItems();
    if (items.empty()) {
        return;
    }

    const auto pipe = getObject<PartDesign::Pipe>();
    std::vector<std::string> refs = pipe->Spine.getSubValues();

    for (auto* item : items) {
        QByteArray data = item->data(Qt::UserRole).toByteArray();
        std::string obj = data.constData();

        delete ui->listWidgetReferences->takeItem(ui->listWidgetReferences->row(item));

        if (const auto f = std::ranges::find(refs, obj); f != refs.end()) {
            refs.erase(f);
        }
    }

    pipe->Spine.setValue(pipe->Spine.getValue(), refs);
    clearButtons();
    recomputeFeature();
}

bool TaskPipeParameters::referenceSelected(const SelectionChanges& msg) const
{
    auto selectionMode = stateHandler->getSelectionMode();

    if (msg.Type == Gui::SelectionChanges::AddSelection
        && selectionMode != StateHandlerTaskPipe::SelectionModes::none) {
        if (strcmp(msg.pDocName, getAppDocument()->getName()) != 0) {
            return false;
        }

        // not allowed to reference ourself
        const char* fname = getObject()->getNameInDocument();
        if (strcmp(msg.pObjectName, fname) == 0) {
            return false;
        }

        switch (selectionMode) {
            case StateHandlerTaskPipe::SelectionModes::refProfile: {
                auto pipe = getObject<PartDesign::Pipe>();
                Gui::Document* doc = getGuiDocument();

                rememberPipeInputVisibility(
                    this,
                    pipe->Profile.getValue()
                );
                getViewObject<ViewProviderPipe>()->highlightReferences(ViewProviderPipe::Profile, false);

                bool success = true;
                App::DocumentObject* profile = pipe->getDocument()->getObject(msg.pObjectName);
                if (profile) {
                    rememberPipeInputVisibility(this, profile);
                    std::vector<App::DocumentObject*> sections = pipe->Sections.getValues();

                    // cannot use the same object for profile and section
                    if (std::ranges::find(sections, profile) != sections.end()) {
                        success = false;
                    }
                    else {
                        pipe->Profile.setValue(profile, {msg.pSubName});
                    }

                    // hide the old or new profile again
                    auto* pvp = doc->getViewProvider(pipe->Profile.getValue());
                    if (pvp) {
                        pvp->setVisible(false);
                    }
                }
                return success;
            }
            case StateHandlerTaskPipe::SelectionModes::refSpine:
            case StateHandlerTaskPipe::SelectionModes::refSpineEdgeAdd:
            case StateHandlerTaskPipe::SelectionModes::refSpineEdgeRemove: {
                // change the references
                const std::string subName(msg.pSubName);
                const auto pipe = getObject<PartDesign::Pipe>();
                auto* selected =
                    getAppDocument()->getObject(msg.pObjectName);
                rememberPipeInputVisibility(
                    this,
                    pipe->Spine.getValue()
                );
                rememberPipeInputVisibility(this, selected);
                std::vector<std::string> refs = pipe->Spine.getSubValues();
                const auto f = std::ranges::find(refs, subName);

                if (selectionMode == StateHandlerTaskPipe::SelectionModes::refSpine) {
                    getViewObject<ViewProviderPipe>()->highlightReferences(
                        ViewProviderPipe::Spine,
                        false
                    );
                    refs.clear();
                }
                else if (selectionMode == StateHandlerTaskPipe::SelectionModes::refSpineEdgeAdd) {
                    if (f == refs.end()) {
                        refs.push_back(subName);
                    }
                    else {
                        return false;  // duplicate selection
                    }
                }
                else if (selectionMode == StateHandlerTaskPipe::SelectionModes::refSpineEdgeRemove) {
                    if (f != refs.end()) {
                        refs.erase(f);
                    }
                    else {
                        return false;
                    }
                }

                pipe->Spine.setValue(selected, refs);
                return true;
            }
            default:
                return false;
        }
    }

    return false;
}

void TaskPipeParameters::clearButtons()
{
    ui->buttonProfileBase->setChecked(false);
    ui->buttonRefAdd->setChecked(false);
    ui->buttonRefRemove->setChecked(false);
    ui->buttonSpineBase->setChecked(false);
}

void TaskPipeParameters::exitSelectionMode()
{
    // commenting because this should be handled by buttonToggled signal
    // selectionMode = none;
    if (auto* pipe = getObject<PartDesign::Pipe>()) {
        Gui::Selection().clearSelection(
            pipe->getDocument()->getName()
        );
    }
}

void TaskPipeParameters::setVisibilityOfSpineAndProfile()
{
    restorePipeInputVisibility(this, getAppDocument());
}

bool TaskPipeParameters::accept()
{
    auto* pipe = getObject<PartDesign::Pipe>();
    auto* activeBody = PartDesignGui::getBodyFor(pipe, false);
    if (!pipe || !pipe->getDocument() || !activeBody
        || !activeBody->hasObject(pipe)) {
        QMessageBox::warning(this, tr("Input Error"), tr("No active body"));
        return false;
    }

    auto* document = pipe->getDocument();
    const App::PropertyLinkSubList::SubSet originalSpine {
        pipe->Spine.getValue(),
        pipe->Spine.getSubValues(),
    };
    const App::PropertyLinkSubList::SubSet originalAuxiliarySpine {
        pipe->AuxiliarySpine.getValue(),
        pipe->AuxiliarySpine.getSubValues(),
    };
    const auto originalSections = pipe->Sections.getSubListValues();
    const auto originalBodyGroup = activeBody->Group.getValues();
    auto* originalBodyTip = activeBody->Tip.getValue();

    std::unordered_set<long> objectsBeforeAttempt;
    for (auto* object : document->getObjects()) {
        if (object) {
            objectsBeforeAttempt.insert(object->getID());
        }
    }

    App::DocumentObject* spine = pipe->Spine.getValue();
    App::DocumentObject* auxSpine = pipe->AuxiliarySpine.getValue();

    // Resolve a manually entered internal label before deciding whether the
    // reference must be imported.
    QString label = ui->spineBaseEdit->text();
    if (!spine && !label.isEmpty()) {
        QByteArray ba = label.toUtf8();
        std::vector<App::DocumentObject*> objs = pipe->getDocument()->findObjects(
            App::DocumentObject::getClassTypeId(),
            nullptr,
            ba.constData()
        );
        if (!objs.empty()) {
            pipe->Spine.setValue(objs.front());
            spine = objs.front();
        }
    }

    const auto isExternal = [activeBody](App::DocumentObject* object) {
        return object && !activeBody->hasObject(object)
            && !activeBody->getOrigin()->hasObject(object);
    };
    bool externalReference =
        isExternal(spine) || isExternal(auxSpine);
    if (!externalReference) {
        externalReference = std::ranges::any_of(
            pipe->Sections.getValues(),
            isExternal
        );
    }

    bool makeIndependentCopies = false;
    bool copyExternalReferences = false;
    if (externalReference) {
        QDialog dia(Gui::getMainWindow());
        Ui_DlgReference dlg;
        dlg.setupUi(&dia);
        dia.setModal(true);
        int result = dia.exec();
        if (result == QDialog::DialogCode::Rejected) {
            return false;
        }
        copyExternalReferences = !dlg.radioXRef->isChecked();
        makeIndependentCopies = dlg.radioIndependent->isChecked();
    }

    std::vector<App::DocumentObject*> copies;
    auto restoreAttempt = [&]() noexcept {
        try {
            pipe->Spine.setValue(originalSpine.first, originalSpine.second);
            pipe->AuxiliarySpine.setValue(
                originalAuxiliarySpine.first,
                originalAuxiliarySpine.second
            );
            pipe->Sections.setSubListValues(originalSections);
        }
        catch (const Base::Exception& error) {
            Base::Console().error(
                "Could not restore failed Pipe references: %s\n",
                error.what()
            );
        }

        // Remove every object created by this acceptance attempt.  Comparing
        // document IDs also catches a helper whose factory threw after adding
        // the object but before returning its pointer.
        std::vector<std::pair<long, std::string>> createdObjects;
        for (auto* object : document->getObjects()) {
            if (object && object->getNameInDocument()
                && !objectsBeforeAttempt.contains(object->getID())) {
                createdObjects.emplace_back(
                    object->getID(),
                    object->getNameInDocument()
                );
            }
        }
        for (auto item = createdObjects.rbegin();
             item != createdObjects.rend();
             ++item) {
            try {
                auto* object = document->getObjectByID(item->first);
                if (object && object->getNameInDocument()
                    && item->second == object->getNameInDocument()) {
                    document->removeObject(item->second.c_str());
                }
            }
            catch (const Base::Exception& error) {
                Base::Console().error(
                    "Could not remove failed Pipe helper '%s': %s\n",
                    item->second.c_str(),
                    error.what()
                );
            }
        }

        try {
            activeBody->Group.setValues(originalBodyGroup);
            activeBody->Tip.setValue(originalBodyTip);
        }
        catch (const Base::Exception& error) {
            Base::Console().error(
                "Could not restore Body after failed Pipe: %s\n",
                error.what()
            );
        }
        setVisibilityOfSpineAndProfile();
        try {
            document->recomputeFeature(pipe);
        }
        catch (...) {
        }
    };

    TaskInternal::AcceptedMacro acceptedMacro;
    const auto failAttempt = [&](const QString& message) {
        acceptedMacro.discard();
        restoreAttempt();
        QMessageBox::warning(this, tr("Input Error"), message);
        return false;
    };
    try {
        const auto makeCopy = [&](App::DocumentObject* source) {
            auto* copy = PartDesignGui::TaskFeaturePick::makeCopy(
                source,
                "",
                makeIndependentCopies,
                document
            );
            if (!copy || copy->getDocument() != document) {
                throw Base::RuntimeError(
                    "Could not create a local copy of an external Pipe reference"
                );
            }
            copies.push_back(copy);
            return copy;
        };

        if (copyExternalReferences) {
            // Spine and AuxiliarySpine are independent properties.  Both must
            // be imported when both are external.
            if (isExternal(spine)) {
                auto* copy = makeCopy(spine);
                pipe->Spine.setValue(copy, pipe->Spine.getSubValues());
            }
            if (isExternal(auxSpine)) {
                auto* copy = makeCopy(auxSpine);
                pipe->AuxiliarySpine.setValue(
                    copy,
                    pipe->AuxiliarySpine.getSubValues()
                );
            }

            std::vector<App::PropertyLinkSubList::SubSet> sections;
            sections.reserve(pipe->Sections.getSize());
            for (const auto& section : pipe->Sections.getSubListValues()) {
                sections.emplace_back(
                    isExternal(section.first)
                        ? makeCopy(section.first)
                        : section.first,
                    section.second
                );
            }
            pipe->Sections.setSubListValues(sections);
        }

        // Helpers become real Body members before the Pipe is validated or
        // committed.  Insert them immediately before the Pipe so a reference
        // can never replace the Body's final result/TIP.
        for (auto* copy : copies) {
            if (!activeBody->hasObject(copy)) {
                activeBody->insertObject(copy, pipe, false);
            }
            // Imported paths and sections are support references, not
            // competing viewport results.
            copy->Visibility.setValue(false);
        }

        Gui::cmdAppDocument(pipe, "recompute()");
        if (!pipe->isValid() || pipe->Shape.getShape().isNull()
            || !pipe->Shape.getShape().isValid()) {
            const char* status = pipe->getStatusString();
            throw Base::RuntimeError(
                status && *status
                    ? status
                    : "Pipe did not produce valid geometry"
            );
        }

        App::PropertyLinkT spineProperty(
            pipe->Spine.getValue(),
            pipe->Spine.getSubValues()
        );
        Gui::cmdAppObjectArgs(
            pipe,
            "Spine = %s",
            spineProperty.getPropertyPython()
        );
        App::PropertyLinkT auxiliaryProperty(
            pipe->AuxiliarySpine.getValue(),
            pipe->AuxiliarySpine.getSubValues()
        );
        Gui::cmdAppObjectArgs(
            pipe,
            "AuxiliarySpine = %s",
            auxiliaryProperty.getPropertyPython()
        );
        Gui::cmdAppObjectArgs(
            pipe,
            "Sections = %s",
            pipe->Sections.getPyReprString().c_str()
        );

        setVisibilityOfSpineAndProfile();
        Gui::cmdGuiDocument(pipe, "resetEdit()");
    }
    catch (const Base::Exception& e) {
        return failAttempt(
            QApplication::translate("Exception", e.what())
        );
    }
    catch (const Standard_Failure& e) {
        return failAttempt(
            QString::fromUtf8(
                e.GetMessageString()
                    ? e.GetMessageString()
                    : "OpenCascade rejected the Pipe geometry"
            )
        );
    }
    catch (const std::exception& e) {
        return failAttempt(QString::fromUtf8(e.what()));
    }
    catch (...) {
        return failAttempt(tr("Pipe creation failed."));
    }

    acceptedMacro.publish();
    return true;
}


//**************************************************************************
//**************************************************************************
// Task Orientation
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskPipeOrientation::TaskPipeOrientation(ViewProviderPipe* PipeView, bool /*newObj*/, QWidget* parent)
    : TaskSketchBasedParameters(PipeView, parent, "PartDesign_AdditivePipe", tr("Section Orientation"))
    , ui(new Ui_TaskPipeOrientation)
    , stateHandler(nullptr)
{
    // we need a separate container widget to add all controls to
    proxy = new QWidget(this);
    ui->setupUi(proxy);
    QMetaObject::connectSlotsByName(this);

    // clang-format off
    // some buttons are handled in a buttongroup
    connect(ui->comboBoxMode, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &TaskPipeOrientation::onOrientationChanged);
    connect(ui->buttonProfileClear, &QToolButton::clicked,
            this, &TaskPipeOrientation::onClearButton);
    connect(ui->stackedWidget, &QStackedWidget::currentChanged,
            this, &TaskPipeOrientation::updateUI);
    connect(ui->curvilinear, &QCheckBox::toggled,
            this, &TaskPipeOrientation::onCurvilinearChanged);
    connect(ui->doubleSpinBoxX, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &TaskPipeOrientation::onBinormalChanged);
    connect(ui->doubleSpinBoxY, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &TaskPipeOrientation::onBinormalChanged);
    connect(ui->doubleSpinBoxZ, qOverload<double>(&QDoubleSpinBox::valueChanged),
            this, &TaskPipeOrientation::onBinormalChanged);
    // clang-format on

    // Create context menu
    QAction* remove = new QAction(tr("Remove"), this);
    remove->setShortcut(Gui::QtTools::deleteKeySequence());
    remove->setShortcutContext(Qt::WidgetShortcut);

    // display shortcut behind the context menu entry
    remove->setShortcutVisibleInContextMenu(true);

    ui->listWidgetReferences->addAction(remove);
    connect(remove, &QAction::triggered, this, &TaskPipeOrientation::onDeleteItem);
    connect(ui->buttonRefRemove, &QToolButton::clicked, this, &TaskPipeOrientation::onDeleteItem);
    ui->listWidgetReferences->setContextMenuPolicy(Qt::ActionsContextMenu);

    this->groupLayout()->addWidget(proxy);

    PartDesign::Pipe* pipe = PipeView->getObject<PartDesign::Pipe>();

    // add initial values
    if (pipe->AuxiliarySpine.getValue()) {
        ui->profileBaseEdit->setText(
            QString::fromUtf8(pipe->AuxiliarySpine.getValue()->Label.getValue())
        );
    }

    std::vector<std::string> strings = pipe->AuxiliarySpine.getSubValues();
    for (const auto& string : strings) {
        QString label = QString::fromStdString(string);
        QListWidgetItem* item = new QListWidgetItem();
        item->setText(label);
        item->setData(Qt::UserRole, QByteArray(label.toUtf8()));
        ui->listWidgetReferences->addItem(item);
    }

    ui->comboBoxMode->setCurrentIndex(pipe->Mode.getValue());
    ui->curvilinear->setChecked(pipe->AuxiliaryCurvilinear.getValue());

    // should be called after panel has become visible
    QMetaObject::invokeMethod(this, "updateUI", Qt::QueuedConnection, Q_ARG(int, pipe->Mode.getValue()));
    this->blockSelection(false);
}

TaskPipeOrientation::~TaskPipeOrientation()
{
    try {
        if (auto view = getViewObject<ViewProviderPipe>()) {
            view->highlightReferences(ViewProviderPipe::AuxiliarySpine, false);
        }
    }
    catch (const Standard_OutOfRange&) {
    }
}

void TaskPipeOrientation::onOrientationChanged(int idx)
{
    if (auto pipe = getObject<PartDesign::Pipe>()) {
        pipe->Mode.setValue(idx);
        recomputeFeature();
    }
}

void TaskPipeOrientation::clearButtons()
{
    ui->buttonRefAdd->setChecked(false);
    ui->buttonRefRemove->setChecked(false);
    ui->buttonProfileBase->setChecked(false);
}

void TaskPipeOrientation::exitSelectionMode()
{
    if (auto* pipe = getObject<PartDesign::Pipe>()) {
        Gui::Selection().clearSelection(
            pipe->getDocument()->getName()
        );
    }
}

void TaskPipeOrientation::onClearButton()
{
    ui->listWidgetReferences->clear();
    ui->profileBaseEdit->clear();
    if (auto view = getViewObject<ViewProviderPipe>()) {
        view->highlightReferences(ViewProviderPipe::AuxiliarySpine, false);
        auto* pipe = getObject<PartDesign::Pipe>();
        rememberPipeInputVisibility(
            pipe,
            pipe->AuxiliarySpine.getValue()
        );
        pipe->AuxiliarySpine.setValue(nullptr);
    }
}

void TaskPipeOrientation::onCurvilinearChanged(bool checked)
{
    if (auto pipe = getObject<PartDesign::Pipe>()) {
        pipe->AuxiliaryCurvilinear.setValue(checked);
        recomputeFeature();
    }
}

void TaskPipeOrientation::onBinormalChanged(double)
{
    if (auto pipe = getObject<PartDesign::Pipe>()) {
        Base::Vector3d vec(
            ui->doubleSpinBoxX->value(),
            ui->doubleSpinBoxY->value(),
            ui->doubleSpinBoxZ->value()
        );

        pipe->Binormal.setValue(vec);
        recomputeFeature();
    }
}

void TaskPipeOrientation::onSelectionChanged(const SelectionChanges& msg)
{
    if (stateHandler->getSelectionMode() == StateHandlerTaskPipe::SelectionModes::none) {
        return;
    }

    if (msg.Type == Gui::SelectionChanges::AddSelection) {
        if (referenceSelected(msg)) {
            if (stateHandler->getSelectionMode()
                == StateHandlerTaskPipe::SelectionModes::refAuxSpineEdgeAdd) {
                QString sub = QString::fromStdString(msg.pSubName);
                if (!sub.isEmpty()) {
                    QListWidgetItem* item = new QListWidgetItem();
                    item->setText(sub);
                    item->setData(Qt::UserRole, QByteArray(msg.pSubName));
                    ui->listWidgetReferences->addItem(item);
                }

                App::Document* document = App::GetApplication().getDocument(msg.pDocName);
                App::DocumentObject* object = document ? document->getObject(msg.pObjectName)
                                                       : nullptr;
                if (object) {
                    QString label = QString::fromUtf8(object->Label.getValue());
                    ui->profileBaseEdit->setText(label);
                }
            }
            else if (
                stateHandler->getSelectionMode()
                == StateHandlerTaskPipe::SelectionModes::refAuxSpineEdgeRemove
            ) {
                QString sub = QString::fromLatin1(msg.pSubName);
                if (!sub.isEmpty()) {
                    removeFromListWidget(ui->listWidgetReferences, sub);
                }
                else {
                    ui->profileBaseEdit->clear();
                }
            }
            else if (
                stateHandler->getSelectionMode() == StateHandlerTaskPipe::SelectionModes::refAuxSpine
            ) {
                ui->listWidgetReferences->clear();

                App::Document* document = App::GetApplication().getDocument(msg.pDocName);
                App::DocumentObject* object = document ? document->getObject(msg.pObjectName)
                                                       : nullptr;
                if (object) {
                    QString label = QString::fromUtf8(object->Label.getValue());
                    ui->profileBaseEdit->setText(label);
                }
            }

            clearButtons();
            auto view = getViewObject<ViewProviderPipe>();
            view->highlightReferences(ViewProviderPipe::AuxiliarySpine, false);
            recomputeFeature();
        }

        clearButtons();
        exitSelectionMode();
    }
}

bool TaskPipeOrientation::referenceSelected(const SelectionChanges& msg) const
{
    auto selectionMode = stateHandler->getSelectionMode();

    if (msg.Type == Gui::SelectionChanges::AddSelection
        && (selectionMode == StateHandlerTaskPipe::SelectionModes::refAuxSpine
            || selectionMode == StateHandlerTaskPipe::SelectionModes::refAuxSpineEdgeAdd
            || selectionMode == StateHandlerTaskPipe::SelectionModes::refAuxSpineEdgeRemove)) {
        if (strcmp(msg.pDocName, getObject()->getDocument()->getName()) != 0) {
            return false;
        }

        // not allowed to reference ourself
        const char* fname = getObject()->getNameInDocument();
        if (strcmp(msg.pObjectName, fname) == 0) {
            return false;
        }

        if (const auto pipe = getObject<PartDesign::Pipe>()) {
            // change the references
            const std::string subName(msg.pSubName);
            auto* selected =
                pipe->getDocument()->getObject(msg.pObjectName);
            rememberPipeInputVisibility(
                pipe,
                pipe->AuxiliarySpine.getValue()
            );
            rememberPipeInputVisibility(pipe, selected);
            std::vector<std::string> refs = pipe->AuxiliarySpine.getSubValues();
            const auto f = std::ranges::find(refs, subName);

            if (selectionMode == StateHandlerTaskPipe::SelectionModes::refAuxSpine) {
                refs.clear();
            }
            else if (selectionMode == StateHandlerTaskPipe::SelectionModes::refAuxSpineEdgeAdd) {
                if (f != refs.end()) {
                    return false;  // duplicate selection
                }

                refs.push_back(subName);
            }
            else if (selectionMode == StateHandlerTaskPipe::SelectionModes::refAuxSpineEdgeRemove) {
                if (f == refs.end()) {
                    return false;
                }

                refs.erase(f);
            }

            pipe->AuxiliarySpine.setValue(selected, refs);
            return true;
        }
    }

    return false;
}

void TaskPipeOrientation::removeFromListWidget(QListWidget* widget, QString name)
{
    QList<QListWidgetItem*> items = widget->findItems(name, Qt::MatchExactly);
    if (!items.empty()) {
        for (auto item : items) {
            QListWidgetItem* it = widget->takeItem(widget->row(item));
            delete it;
        }
    }
}

void TaskPipeOrientation::onDeleteItem()
{
    // Delete the selected spine
    int row = ui->listWidgetReferences->currentRow();
    QListWidgetItem* item = ui->listWidgetReferences->takeItem(row);
    if (item) {
        QByteArray data = item->data(Qt::UserRole).toByteArray();
        delete item;

        // search inside the list of spines
        if (const auto pipe = getObject<PartDesign::Pipe>()) {
            std::vector<std::string> refs = pipe->AuxiliarySpine.getSubValues();
            const std::string obj = data.constData();

            // if something was found, delete it and update the spine list
            if (const auto f = std::ranges::find(refs, obj); f != refs.end()) {
                refs.erase(f);
                pipe->AuxiliarySpine.setValue(pipe->AuxiliarySpine.getValue(), refs);
                clearButtons();
                recomputeFeature();
            }
        }
    }
}

void TaskPipeOrientation::updateUI(int idx)
{
    // make sure we resize to the size of the current page
    for (int i = 0; i < ui->stackedWidget->count(); ++i) {
        ui->stackedWidget->widget(i)->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Ignored);
    }

    if (idx < ui->stackedWidget->count()) {
        ui->stackedWidget->widget(idx)->setSizePolicy(QSizePolicy::Expanding, QSizePolicy::Expanding);
    }
}


//**************************************************************************
//**************************************************************************
// Task Scaling
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++
TaskPipeScaling::TaskPipeScaling(ViewProviderPipe* PipeView, bool /*newObj*/, QWidget* parent)
    : TaskSketchBasedParameters(PipeView, parent, "PartDesign_AdditivePipe", tr("Section Transformation"))
    , ui(new Ui_TaskPipeScaling)
    , stateHandler(nullptr)
{
    // we need a separate container widget to add all controls to
    proxy = new QWidget(this);
    ui->setupUi(proxy);
    QMetaObject::connectSlotsByName(this);

    // some buttons are handled in a buttongroup
    connect(
        ui->comboBoxScaling,
        qOverload<int>(&QComboBox::currentIndexChanged),
        this,
        &TaskPipeScaling::onScalingChanged
    );
    connect(ui->stackedWidget, &QStackedWidget::currentChanged, this, &TaskPipeScaling::updateUI);

    // Create context menu
    QAction* remove = new QAction(tr("Remove"), this);
    remove->setShortcut(Gui::QtTools::deleteKeySequence());
    remove->setShortcutContext(Qt::WidgetShortcut);

    // display shortcut behind the context menu entry
    remove->setShortcutVisibleInContextMenu(true);

    ui->listWidgetReferences->addAction(remove);
    ui->listWidgetReferences->setContextMenuPolicy(Qt::ActionsContextMenu);
    connect(remove, &QAction::triggered, this, &TaskPipeScaling::onDeleteSection);
    connect(ui->buttonRefRemove, &QToolButton::clicked, this, &TaskPipeScaling::onDeleteSection);

    connect(
        ui->listWidgetReferences->model(),
        &QAbstractListModel::rowsMoved,
        this,
        &TaskPipeScaling::indexesMoved
    );

    this->groupLayout()->addWidget(proxy);

    PartDesign::Pipe* pipe = PipeView->getObject<PartDesign::Pipe>();
    // Reveal the task's existing section inputs once, at task entry. A later
    // property or label refresh must not force geometry back on after the user
    // has hidden it.
    for (const auto& section : pipe->Sections.getSubListValues()) {
        if (section.first && section.first->isAttachedToDocument()) {
            Gui::Application::Instance->showViewProvider(section.first);
        }
    }
    rebuildSectionRows();

    ui->comboBoxScaling->setCurrentIndex(pipe->Transformation.getValue());

    // should be called after panel has become visible
    QMetaObject::invokeMethod(
        this,
        "updateUI",
        Qt::QueuedConnection,
        Q_ARG(int, pipe->Transformation.getValue())
    );
    this->blockSelection(false);
}

TaskPipeScaling::~TaskPipeScaling()
{
    try {
        if (auto view = getViewObject<ViewProviderPipe>()) {
            view->highlightReferences(ViewProviderPipe::Section, false);
        }
    }
    catch (const Standard_OutOfRange&) {
    }
}

void TaskPipeScaling::rebuildSectionRows()
{
    ui->listWidgetReferences->clear();
    auto* pipe = getObject<PartDesign::Pipe>();
    if (!pipe) {
        return;
    }
    for (const auto& section : pipe->Sections.getSubListValues()) {
        if (!section.first || !section.first->isAttachedToDocument()) {
            continue;
        }
        auto* item = new QListWidgetItem(
            make2DLabel(section.first, section.second)
        );
        item->setData(Qt::UserRole, pipeSectionIdentity(section));
        ui->listWidgetReferences->addItem(item);
    }
}

void TaskPipeScaling::slotChangedObject(
    const Gui::ViewProviderDocumentObject& object,
    const App::Property& property
)
{
    auto* pipe = getObject<PartDesign::Pipe>();
    if (pipe && object.getObject() == pipe
        && &property == &pipe->Sections) {
        rebuildSectionRows();
    }
}

void TaskPipeScaling::slotRelabelObject(
    const Gui::ViewProviderDocumentObject& object
)
{
    auto* pipe = getObject<PartDesign::Pipe>();
    if (!pipe) {
        return;
    }
    const auto sections = pipe->Sections.getValues();
    if (std::ranges::find(sections, object.getObject())
        != sections.end()) {
        rebuildSectionRows();
    }
}

void TaskPipeScaling::indexesMoved()
{
    QAbstractItemModel* model = qobject_cast<QAbstractItemModel*>(sender());
    if (!model) {
        return;
    }

    if (auto pipe = getObject<PartDesign::Pipe>()) {
        auto remaining = pipe->Sections.getSubListValues();
        if (model->rowCount()
            != static_cast<int>(remaining.size())) {
            rebuildSectionRows();
            return;
        }

        std::vector<App::PropertyLinkSubList::SubSet> reordered;
        reordered.reserve(remaining.size());
        for (int row = 0; row < model->rowCount(); ++row) {
            App::PropertyLinkSubList::SubSet section;
            if (!resolvePipeSection(
                    model->index(row, 0).data(Qt::UserRole),
                    section
                )
                || section.first->getDocument()
                    != pipe->getDocument()) {
                rebuildSectionRows();
                return;
            }
            const auto found = std::ranges::find_if(
                remaining,
                [&section](const auto& candidate) {
                    return samePipeSection(candidate, section);
                }
            );
            if (found == remaining.end()) {
                rebuildSectionRows();
                return;
            }
            reordered.push_back(*found);
            remaining.erase(found);
        }

        pipe->Sections.setSubListValues(reordered);
        recomputeFeature();
        updateUI(ui->stackedWidget->currentIndex());
    }
}

void TaskPipeScaling::clearButtons()
{
    ui->buttonRefRemove->setChecked(false);
    ui->buttonRefAdd->setChecked(false);
}

void TaskPipeScaling::exitSelectionMode()
{
    if (auto* pipe = getObject<PartDesign::Pipe>()) {
        Gui::Selection().clearSelection(
            pipe->getDocument()->getName()
        );
    }
}

void TaskPipeScaling::onScalingChanged(int idx)
{
    if (auto pipe = getObject<PartDesign::Pipe>()) {
        updateUI(idx);
        pipe->Transformation.setValue(idx);
    }
}

void TaskPipeScaling::onSelectionChanged(const SelectionChanges& msg)
{
    if (stateHandler->getSelectionMode() == StateHandlerTaskPipe::SelectionModes::none) {
        return;
    }

    if (msg.Type == Gui::SelectionChanges::AddSelection) {
        if (referenceSelected(msg)) {
            App::Document* document = App::GetApplication().getDocument(msg.pDocName);
            App::DocumentObject* object = document ? document->getObject(msg.pObjectName) : nullptr;
            if (object) {
                const auto mode = stateHandler->getSelectionMode();
                if (mode
                        == StateHandlerTaskPipe::SelectionModes::refSectionAdd
                    || mode
                        == StateHandlerTaskPipe::SelectionModes::refSectionRemove) {
                    // The live Sections property is authoritative. This also
                    // avoids confusing two inputs with the same display
                    // label.
                    rebuildSectionRows();
                }
            }

            clearButtons();
            recomputeFeature();
        }
        clearButtons();
        exitSelectionMode();
    }
}

bool TaskPipeScaling::referenceSelected(const SelectionChanges& msg) const
{
    auto selectionMode = stateHandler->getSelectionMode();

    if ((msg.Type == Gui::SelectionChanges::AddSelection)
        && ((selectionMode == StateHandlerTaskPipe::SelectionModes::refSectionAdd)
            || (selectionMode == StateHandlerTaskPipe::SelectionModes::refSectionRemove))) {
        if (strcmp(msg.pDocName, getObject()->getDocument()->getName()) != 0) {
            return false;
        }

        // not allowed to reference ourself
        const char* fname = getObject()->getNameInDocument();
        if (strcmp(msg.pObjectName, fname) == 0) {
            return false;
        }

        // change the references
        if (const auto pipe = getObject<PartDesign::Pipe>()) {
            std::vector<App::DocumentObject*> refs = pipe->Sections.getValues();
            App::DocumentObject* obj = pipe->getDocument()->getObject(msg.pObjectName);
            rememberPipeInputVisibility(pipe, obj);
            const auto f = std::ranges::find(refs, obj);

            if (selectionMode == StateHandlerTaskPipe::SelectionModes::refSectionAdd) {
                if (f != refs.end()) {
                    return false;  // duplicate selection
                }

                pipe->Sections.addValue(obj, {msg.pSubName});
            }
            else {
                if (f == refs.end()) {
                    return false;
                }

                pipe->Sections.removeValue(obj);
            }

            auto view = getViewObject<ViewProviderPipe>();
            view->highlightReferences(ViewProviderPipe::Section, false);
            return true;
        }
    }

    return false;
}

void TaskPipeScaling::removeFromListWidget(QListWidget* widget, QString name)
{
    QList<QListWidgetItem*> items = widget->findItems(name, Qt::MatchExactly);
    if (!items.empty()) {
        for (auto item : items) {
            QListWidgetItem* it = widget->takeItem(widget->row(item));
            delete it;
        }
    }
}

void TaskPipeScaling::onDeleteSection()
{
    const int row = ui->listWidgetReferences->currentRow();
    auto* item = row >= 0
        ? ui->listWidgetReferences->item(row)
        : nullptr;
    auto* pipe = getObject<PartDesign::Pipe>();
    if (!item || !pipe) {
        return;
    }

    App::PropertyLinkSubList::SubSet selected;
    if (!resolvePipeSection(item->data(Qt::UserRole), selected)
        || selected.first->getDocument() != pipe->getDocument()) {
        rebuildSectionRows();
        return;
    }

    auto sections = pipe->Sections.getSubListValues();
    const auto found = std::ranges::find_if(
        sections,
        [&selected](const auto& section) {
            return samePipeSection(section, selected);
        }
    );
    if (found == sections.end()) {
        rebuildSectionRows();
        return;
    }
    sections.erase(found);
    pipe->Sections.setSubListValues(sections);
    rebuildSectionRows();
    clearButtons();
    recomputeFeature();
}

void TaskPipeScaling::updateUI(int idx)
{
    // make sure we resize to the size of the current page
    for (int i = 0; i < ui->stackedWidget->count(); ++i) {
        ui->stackedWidget->widget(i)->setSizePolicy(QSizePolicy::Ignored, QSizePolicy::Ignored);
    }

    if (idx >= 0 && idx < ui->stackedWidget->count()) {
        if (auto* page = ui->stackedWidget->widget(idx)) {
            page->setSizePolicy(
                QSizePolicy::Expanding,
                QSizePolicy::Expanding
            );
        }
    }
}


//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskDlgPipeParameters::TaskDlgPipeParameters(ViewProviderPipe* PipeView, bool newObj)
    : TaskDlgSketchBasedParameters(PipeView)
{
    assert(PipeView);
    parameter = new TaskPipeParameters(PipeView, newObj);
    orientation = new TaskPipeOrientation(PipeView, newObj);
    scaling = new TaskPipeScaling(PipeView, newObj);

    stateHandler = new StateHandlerTaskPipe();

    Content.push_back(parameter);
    Content.push_back(orientation);
    Content.push_back(scaling);
    Content.push_back(preview);

    parameter->stateHandler = stateHandler;
    orientation->stateHandler = stateHandler;
    scaling->stateHandler = stateHandler;

    buttonGroup = new ButtonGroup(this);
    buttonGroup->setExclusive(true);

    buttonGroup->addButton(parameter->ui->buttonProfileBase, StateHandlerTaskPipe::refProfile);
    buttonGroup->addButton(parameter->ui->buttonSpineBase, StateHandlerTaskPipe::refSpine);
    buttonGroup->addButton(parameter->ui->buttonRefAdd, StateHandlerTaskPipe::refSpineEdgeAdd);
    buttonGroup->addButton(parameter->ui->buttonRefRemove, StateHandlerTaskPipe::refSpineEdgeRemove);

    buttonGroup->addButton(orientation->ui->buttonProfileBase, StateHandlerTaskPipe::refAuxSpine);
    buttonGroup->addButton(orientation->ui->buttonRefAdd, StateHandlerTaskPipe::refAuxSpineEdgeAdd);
    buttonGroup->addButton(orientation->ui->buttonRefRemove, StateHandlerTaskPipe::refAuxSpineEdgeRemove);

    buttonGroup->addButton(scaling->ui->buttonRefAdd, StateHandlerTaskPipe::refSectionAdd);
    buttonGroup->addButton(scaling->ui->buttonRefRemove, StateHandlerTaskPipe::refSectionRemove);

    connect(
        buttonGroup,
        qOverload<QAbstractButton*, bool>(&QButtonGroup::buttonToggled),
        this,
        &TaskDlgPipeParameters::onButtonToggled
    );
}

TaskDlgPipeParameters::~TaskDlgPipeParameters()
{
    delete stateHandler;
}

void TaskDlgPipeParameters::onButtonToggled(QAbstractButton* button, bool checked)
{
    int id = buttonGroup->id(button);

    auto clearTaskSelection = [this]() {
        if (auto* pipe = getObject<PartDesign::Pipe>()) {
            Gui::Selection().clearSelection(
                pipe->getDocument()->getName()
            );
        }
    };
    if (checked) {
        // hideObject();
        clearTaskSelection();
        stateHandler->selectionMode = static_cast<StateHandlerTaskPipe::SelectionModes>(id);
    }
    else {
        clearTaskSelection();
        if (stateHandler->selectionMode == static_cast<StateHandlerTaskPipe::SelectionModes>(id)) {
            stateHandler->selectionMode = StateHandlerTaskPipe::SelectionModes::none;
        }
    }

    switch (id) {
        case StateHandlerTaskPipe::SelectionModes::refProfile:
            getViewObject<ViewProviderPipe>()->highlightReferences(ViewProviderPipe::Profile, checked);
            break;
        case StateHandlerTaskPipe::SelectionModes::refSpine:
        case StateHandlerTaskPipe::SelectionModes::refSpineEdgeAdd:
        case StateHandlerTaskPipe::SelectionModes::refSpineEdgeRemove:
            getViewObject<ViewProviderPipe>()->highlightReferences(ViewProviderPipe::Spine, checked);
            break;
        case StateHandlerTaskPipe::SelectionModes::refAuxSpine:
        case StateHandlerTaskPipe::SelectionModes::refAuxSpineEdgeAdd:
        case StateHandlerTaskPipe::SelectionModes::refAuxSpineEdgeRemove:
            getViewObject<ViewProviderPipe>()->highlightReferences(
                ViewProviderPipe::AuxiliarySpine,
                checked
            );
            break;
        case StateHandlerTaskPipe::SelectionModes::refSectionAdd:
        case StateHandlerTaskPipe::SelectionModes::refSectionRemove:
            getViewObject<ViewProviderPipe>()->highlightReferences(ViewProviderPipe::Section, checked);
            break;
        default:
            break;
    }
}

//==== calls from the TaskView ===============================================================


bool TaskDlgPipeParameters::accept()
{
    return parameter->accept();
}


#include "moc_TaskPipeParameters.cpp"
