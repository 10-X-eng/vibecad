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


#include <algorithm>
#include <QAction>
#include <utility>


#include <App/Application.h>
#include <App/Document.h>
#include <Gui/Application.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Tools.h>
#include <Mod/PartDesign/App/FeatureLoft.h>

#include "ui_TaskLoftParameters.h"
#include "TaskLoftParameters.h"
#include "TaskSketchBasedParameters.h"

Q_DECLARE_METATYPE(App::PropertyLinkSubList::SubSet)

using namespace PartDesignGui;
using namespace Gui;

namespace
{
QVariant sectionIdentity(
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

bool resolveSection(
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

bool sameSection(
    const App::PropertyLinkSubList::SubSet& left,
    const App::PropertyLinkSubList::SubSet& right
)
{
    return left.first == right.first && left.second == right.second;
}
}

/* TRANSLATOR PartDesignGui::TaskLoftParameters */

TaskLoftParameters::TaskLoftParameters(ViewProviderLoft* LoftView, bool /*newObj*/, QWidget* parent)
    : TaskSketchBasedParameters(LoftView, parent, "PartDesign_AdditiveLoft", tr("Loft Parameters"))
    , ui(new Ui_TaskLoftParameters)
{
    // we need a separate container widget to add all controls to
    proxy = new QWidget(this);
    ui->setupUi(proxy);
    QMetaObject::connectSlotsByName(this);

    // clang-format off
    connect(ui->buttonProfileBase, &QToolButton::toggled,
            this, &TaskLoftParameters::onProfileButton);
    connect(ui->buttonRefAdd, &QToolButton::toggled,
            this, &TaskLoftParameters::onRefButtonAdd);
    connect(ui->buttonRefRemove, &QToolButton::toggled,
            this, &TaskLoftParameters::onRefButtonRemove);
    connect(ui->checkBoxRuled, &QCheckBox::toggled,
            this, &TaskLoftParameters::onRuled);
    connect(ui->checkBoxClosed, &QCheckBox::toggled,
            this, &TaskLoftParameters::onClosed);
    connect(ui->checkBoxUpdateView, &QCheckBox::toggled,
            this, &TaskLoftParameters::onUpdateView);
    // clang-format on

    // Create context menu
    QAction* remove = new QAction(tr("Remove"), this);
    remove->setShortcut(Gui::QtTools::deleteKeySequence());

    // display shortcut behind the context menu entry
    remove->setShortcutVisibleInContextMenu(true);
    ui->listWidgetReferences->addAction(remove);
    ui->listWidgetReferences->setContextMenuPolicy(Qt::ActionsContextMenu);
    connect(remove, &QAction::triggered, this, &TaskLoftParameters::onDeleteSection);

    connect(
        ui->listWidgetReferences->model(),
        &QAbstractListModel::rowsMoved,
        this,
        &TaskLoftParameters::indexesMoved
    );

    this->groupLayout()->addWidget(proxy);

    // Temporarily prevent unnecessary feature recomputes
    const auto childs = proxy->findChildren<QWidget*>();
    for (QWidget* child : childs) {
        child->blockSignals(true);
    }

    // add the profiles
    PartDesign::Loft* loft = LoftView->getObject<PartDesign::Loft>();
    App::DocumentObject* profile = loft->Profile.getValue();
    if (profile) {
        Gui::Application::Instance->showViewProvider(profile);

        // TODO: if it is a single vertex of a sketch, use that subshape's name
        QString label = make2DLabel(profile, loft->Profile.getSubValues());
        ui->profileBaseEdit->setText(label);
    }

    // Opening the task intentionally reveals its existing profiles so the
    // operation can be edited in context. Subsequent list refreshes are
    // presentation-only and must not override a user's visibility changes.
    for (const auto& section : loft->Sections.getSubListValues()) {
        if (section.first && section.first->isAttachedToDocument()) {
            Gui::Application::Instance->showViewProvider(section.first);
        }
    }
    rebuildSectionRows();

    // get options
    ui->checkBoxRuled->setChecked(loft->Ruled.getValue());
    ui->checkBoxClosed->setChecked(loft->Closed.getValue());

    // activate and de-activate dialog elements as appropriate
    for (QWidget* child : childs) {
        child->blockSignals(false);
    }

    updateUI();
}

TaskLoftParameters::~TaskLoftParameters() = default;

void TaskLoftParameters::rebuildSectionRows()
{
    ui->listWidgetReferences->clear();
    auto* loft = getObject<PartDesign::Loft>();
    if (!loft) {
        return;
    }
    for (const auto& section : loft->Sections.getSubListValues()) {
        if (!section.first || !section.first->isAttachedToDocument()) {
            continue;
        }
        auto* item = new QListWidgetItem(
            make2DLabel(section.first, section.second)
        );
        item->setData(Qt::UserRole, sectionIdentity(section));
        ui->listWidgetReferences->addItem(item);
    }
}

void TaskLoftParameters::slotChangedObject(
    const Gui::ViewProviderDocumentObject& object,
    const App::Property& property
)
{
    auto* loft = getObject<PartDesign::Loft>();
    if (loft && object.getObject() == loft
        && &property == &loft->Sections) {
        rebuildSectionRows();
    }
}

void TaskLoftParameters::slotRelabelObject(
    const Gui::ViewProviderDocumentObject& object
)
{
    auto* loft = getObject<PartDesign::Loft>();
    if (!loft) {
        return;
    }
    const auto sections = loft->Sections.getValues();
    if (std::ranges::find(sections, object.getObject())
        != sections.end()) {
        rebuildSectionRows();
    }
}

void TaskLoftParameters::updateUI()
{
    // we must assure the changed loft is kept visible on section changes,
    // see https://forum.freecad.org/viewtopic.php?f=3&t=63252
    auto loft = getObject<PartDesign::Loft>();
    if (loft) {
        auto view = getViewObject();
        view->makeTemporaryVisible(!loft->Sections.getValues().empty());
    }
}

void TaskLoftParameters::onSelectionChanged(const Gui::SelectionChanges& msg)
{
    if (selectionMode == none) {
        return;
    }

    if (msg.Type == Gui::SelectionChanges::AddSelection) {
        if (referenceSelected(msg)) {
            App::Document* document = App::GetApplication().getDocument(msg.pDocName);
            App::DocumentObject* object = document ? document->getObject(msg.pObjectName) : nullptr;
            if (object) {
                // TODO: if it is a single vertex of a sketch, use that subshape's name
                QString label = make2DLabel(object, {msg.pSubName});
                if (selectionMode == refProfile) {
                    ui->profileBaseEdit->setText(label);
                }
                else if (selectionMode == refAdd
                         || selectionMode == refRemove) {
                    // The property is authoritative. Rebuilding also handles
                    // duplicate display labels without deleting the wrong
                    // section row.
                    rebuildSectionRows();
                }
            }

            clearButtons();
            recomputeFeature();
        }

        clearButtons();
        exitSelectionMode();
        updateUI();
    }
}

bool TaskLoftParameters::referenceSelected(const Gui::SelectionChanges& msg) const
{

    if (msg.Type == Gui::SelectionChanges::AddSelection && selectionMode != none) {

        if (strcmp(msg.pDocName, getObject()->getDocument()->getName()) != 0) {
            return false;
        }

        // not allowed to reference ourself
        const char* fname = getObject()->getNameInDocument();
        if (strcmp(msg.pObjectName, fname) == 0) {
            return false;
        }

        // every selection needs to be a profile in itself, hence currently only full objects are
        // supported, not individual edges of a part

        // change the references
        auto loft = getObject<PartDesign::Loft>();
        App::Document* doc = loft->getDocument();
        App::DocumentObject* obj = doc->getObject(msg.pObjectName);

        if (selectionMode == refProfile) {
            auto view = getViewObject<ViewProviderLoft>();
            view->highlightReferences(ViewProviderLoft::Profile, false);
            loft->Profile.setValue(obj, {msg.pSubName});
            return true;
        }

        if (selectionMode == refAdd || selectionMode == refRemove) {
            // now check the sections
            std::vector<App::DocumentObject*> refs = loft->Sections.getValues();
            const auto f = std::ranges::find(refs, obj);

            if (selectionMode == refAdd) {
                if (f != refs.end()) {
                    return false;  // duplicate selection
                }

                loft->Sections.addValue(obj, {msg.pSubName});
            }
            else if (selectionMode == refRemove) {
                if (f == refs.end()) {
                    return false;
                }

                loft->Sections.removeValue(obj);
            }

            return true;
        }
    }

    return false;
}

void TaskLoftParameters::removeFromListWidget(QListWidget* widget, QString name)
{

    QList<QListWidgetItem*> items = widget->findItems(name, Qt::MatchExactly);
    if (!items.empty()) {
        for (auto it : items) {
            QListWidgetItem* item = widget->takeItem(widget->row(it));
            delete item;
        }
    }
}

void TaskLoftParameters::onDeleteSection()
{
    const int row = ui->listWidgetReferences->currentRow();
    auto* item = row >= 0
        ? ui->listWidgetReferences->item(row)
        : nullptr;
    auto* loft = getObject<PartDesign::Loft>();
    if (!item || !loft) {
        return;
    }

    App::PropertyLinkSubList::SubSet selected;
    if (!resolveSection(item->data(Qt::UserRole), selected)
        || selected.first->getDocument() != loft->getDocument()) {
        rebuildSectionRows();
        return;
    }

    auto sections = loft->Sections.getSubListValues();
    const auto found = std::ranges::find_if(
        sections,
        [&selected](const auto& section) {
            return sameSection(section, selected);
        }
    );
    if (found == sections.end()) {
        rebuildSectionRows();
        return;
    }
    sections.erase(found);
    loft->Sections.setSubListValues(sections);
    rebuildSectionRows();
    recomputeFeature();
    updateUI();
}

void TaskLoftParameters::indexesMoved()
{
    QAbstractItemModel* model = qobject_cast<QAbstractItemModel*>(sender());
    if (!model) {
        return;
    }

    if (auto loft = getObject<PartDesign::Loft>()) {
        auto remaining = loft->Sections.getSubListValues();
        if (model->rowCount()
            != static_cast<int>(remaining.size())) {
            rebuildSectionRows();
            return;
        }

        std::vector<App::PropertyLinkSubList::SubSet> reordered;
        reordered.reserve(remaining.size());
        for (int row = 0; row < model->rowCount(); ++row) {
            App::PropertyLinkSubList::SubSet section;
            if (!resolveSection(
                    model->index(row, 0).data(Qt::UserRole),
                    section
                )
                || section.first->getDocument()
                    != loft->getDocument()) {
                rebuildSectionRows();
                return;
            }
            const auto found = std::ranges::find_if(
                remaining,
                [&section](const auto& candidate) {
                    return sameSection(candidate, section);
                }
            );
            if (found == remaining.end()) {
                rebuildSectionRows();
                return;
            }
            reordered.push_back(*found);
            remaining.erase(found);
        }

        loft->Sections.setSubListValues(reordered);
        recomputeFeature();
        updateUI();
    }
}

void TaskLoftParameters::clearButtons(const selectionModes notThis)
{
    if (notThis != refProfile) {
        ui->buttonProfileBase->setChecked(false);
    }
    if (notThis != refAdd) {
        ui->buttonRefAdd->setChecked(false);
    }
    if (notThis != refRemove) {
        ui->buttonRefRemove->setChecked(false);
    }
}

void TaskLoftParameters::exitSelectionMode()
{
    selectionMode = none;
    if (auto* loft = getObject<PartDesign::Loft>()) {
        Gui::Selection().clearSelection(
            loft->getDocument()->getName()
        );
    }
    this->blockSelection(true);
}

void TaskLoftParameters::changeEvent(QEvent* /*e*/)
{}

void TaskLoftParameters::onClosed(bool val)
{
    if (auto loft = getObject<PartDesign::Loft>()) {
        loft->Closed.setValue(val);
        recomputeFeature();
    }
}

void TaskLoftParameters::onRuled(bool val)
{
    if (auto loft = getObject<PartDesign::Loft>()) {
        loft->Ruled.setValue(val);
        recomputeFeature();
    }
}

void TaskLoftParameters::setSelectionMode(selectionModes mode, bool checked)
{
    if (checked) {
        clearButtons(mode);
        if (auto* loft = getObject<PartDesign::Loft>()) {
            Gui::Selection().clearSelection(
                loft->getDocument()->getName()
            );
        }
        selectionMode = mode;
        this->blockSelection(false);
    }
    else {
        if (auto* loft = getObject<PartDesign::Loft>()) {
            Gui::Selection().clearSelection(
                loft->getDocument()->getName()
            );
        }
        selectionMode = none;
    }

    auto view = getViewObject<ViewProviderLoft>();
    view->highlightReferences(ViewProviderLoft::Both, checked);
}

void TaskLoftParameters::onProfileButton(bool checked)
{
    setSelectionMode(refProfile, checked);
}

void TaskLoftParameters::onRefButtonAdd(bool checked)
{
    setSelectionMode(refAdd, checked);
}

void TaskLoftParameters::onRefButtonRemove(bool checked)
{
    setSelectionMode(refRemove, checked);
}


//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskDlgLoftParameters::TaskDlgLoftParameters(ViewProviderLoft* LoftView, bool newObj)
    : TaskDlgSketchBasedParameters(LoftView)
{
    assert(LoftView);
    parameter = new TaskLoftParameters(LoftView, newObj);

    Content.push_back(parameter);
    Content.push_back(preview);
}

TaskDlgLoftParameters::~TaskDlgLoftParameters() = default;

bool TaskDlgLoftParameters::accept()
{
    if (auto loft = getObject<PartDesign::Loft>()) {
        getViewObject<ViewProviderLoft>()->highlightReferences(ViewProviderLoft::Both, false);

        // First verify that the loft can be built and then hide the sections as otherwise
        // they will remain hidden if the loft's recompute fails
        if (TaskDlgSketchBasedParameters::accept()) {
            for (App::DocumentObject* obj : loft->Sections.getValues()) {
                Gui::cmdAppObjectHide(obj);
            }

            return true;
        }
    }

    return false;
}

//==== calls from the TaskView ===============================================================


#include "moc_TaskLoftParameters.cpp"
