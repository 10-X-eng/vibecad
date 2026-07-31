// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2013 Jan Rheinländer                                    *
 *                                   <jrheinlaender@users.sourceforge.net> *
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
#include <QApplication>
#include <QMessageBox>


#include <App/Document.h>
#include <App/DocumentObject.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Tools.h>
#include <Gui/ViewProvider.h>
#include <Mod/Part/Gui/ModelingSelection.h>
#include <Mod/PartDesign/App/Body.h>
#include <Mod/PartDesign/App/FeatureBoolean.h>

#include "ui_TaskBooleanParameters.h"
#include "TaskBooleanParameters.h"
#include "TaskDialogState.h"


using namespace PartDesignGui;
using namespace Gui;

/* TRANSLATOR PartDesignGui::TaskBooleanParameters */

TaskBooleanParameters::TaskBooleanParameters(ViewProviderBoolean* BooleanView, QWidget* parent)
    : TaskBox(Gui::BitmapFactory().pixmap("PartDesign_Boolean"), tr("Boolean Parameters"), true, parent)
    , ui(new Ui_TaskBooleanParameters)
    , BooleanView(BooleanView)
{
    selectionMode = none;

    // we need a separate container widget to add all controls to
    proxy = new QWidget(this);
    ui->setupUi(proxy);
    QMetaObject::connectSlotsByName(this);

    // clang-format off
    connect(ui->buttonBodyAdd, &QToolButton::toggled,
            this, &TaskBooleanParameters::onButtonBodyAdd);
    connect(ui->buttonBodyRemove, &QToolButton::toggled,
            this, &TaskBooleanParameters::onButtonBodyRemove);
    connect(ui->comboType, qOverload<int>(&QComboBox::currentIndexChanged),
            this, &TaskBooleanParameters::onTypeChanged);
    // clang-format on

    this->groupLayout()->addWidget(proxy);

    PartDesign::Boolean* pcBoolean = BooleanView->getObject<PartDesign::Boolean>();
    std::vector<App::DocumentObject*> bodies = pcBoolean->Group.getValues();
    for (auto body : bodies) {
        QListWidgetItem* item = new QListWidgetItem(ui->listWidgetBodies);
        item->setText(QString::fromUtf8(body->Label.getValue()));
        item->setData(Qt::UserRole, QString::fromLatin1(body->getNameInDocument()));
    }

    // Create context menu
    QAction* action = new QAction(tr("Remove"), this);
    action->setShortcut(Gui::QtTools::deleteKeySequence());

    // display shortcut behind the context menu entry
    action->setShortcutVisibleInContextMenu(true);

    ui->listWidgetBodies->addAction(action);
    connect(action, &QAction::triggered, this, &TaskBooleanParameters::onBodyDeleted);
    ui->listWidgetBodies->setContextMenuPolicy(Qt::ActionsContextMenu);

    int index = pcBoolean->Type.getValue();
    ui->comboType->setCurrentIndex(index);
}

void TaskBooleanParameters::onSelectionChanged(const Gui::SelectionChanges& msg)
{
    if (selectionMode == none) {
        return;
    }

    if (msg.Type == Gui::SelectionChanges::AddSelection) {
        if (strcmp(msg.pDocName, BooleanView->getObject()->getDocument()->getName()) != 0) {
            return;
        }

        // get the selected object
        PartDesign::Boolean* pcBoolean = BooleanView->getObject<PartDesign::Boolean>();
        std::string body(msg.pObjectName);
        if (body.empty()) {
            return;
        }
        App::DocumentObject* pcBody = pcBoolean->getDocument()->getObject(body.c_str());
        if (!pcBody) {
            return;
        }

        // Boolean operands are native Body definitions. A visible component
        // may publish one of those Bodies, but the destination Body itself is
        // never a valid operand of its own feature.
        pcBody = PartGui::findModelingBody(pcBody);
        auto* destinationBody = PartGui::findModelingBody(pcBoolean);
        if (!pcBody || pcBody == destinationBody) {
            return;
        }
        body = pcBody->getNameInDocument();

        std::vector<App::DocumentObject*> bodies = pcBoolean->Group.getValues();

        if (selectionMode == bodyAdd) {
            if (std::ranges::find(bodies, pcBody) == bodies.end()) {
                bodies.push_back(pcBody);
                pcBoolean->Group.setValues(std::vector<App::DocumentObject*>());
                pcBoolean->addObjects(bodies);

                QListWidgetItem* item = new QListWidgetItem(ui->listWidgetBodies);
                item->setText(QString::fromUtf8(pcBody->Label.getValue()));
                item->setData(Qt::UserRole, QString::fromLatin1(pcBody->getNameInDocument()));

                pcBoolean->getDocument()->recomputeFeature(pcBoolean);
                ui->buttonBodyAdd->setChecked(false);
                exitSelectionMode();

                // Hide the bodies
                if (bodies.size() == 1) {
                    // Hide base body and added body
                    Gui::ViewProviderDocumentObject* vp = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                        Gui::Application::Instance->getViewProvider(pcBoolean->BaseFeature.getValue())
                    );
                    if (vp) {
                        vp->hide();
                    }
                    vp = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                        Gui::Application::Instance->getViewProvider(bodies.front())
                    );
                    if (vp) {
                        vp->hide();
                    }
                    BooleanView->show();
                }
                else {
                    // Hide newly added body
                    Gui::ViewProviderDocumentObject* vp
                        = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                            Gui::Application::Instance->getViewProvider(bodies.back())
                        );
                    if (vp) {
                        vp->hide();
                    }
                }
            }
        }
        else if (selectionMode == bodyRemove) {
            if (const auto b = std::ranges::find(bodies, pcBody); b != bodies.end()) {
                bodies.erase(b);
                pcBoolean->setObjects(bodies);

                const QString internalName = QString::fromStdString(body);
                for (int row = 0; row < ui->listWidgetBodies->count(); row++) {
                    QListWidgetItem* item = ui->listWidgetBodies->item(row);
                    QString name = item->data(Qt::UserRole).toString();
                    if (name == internalName) {
                        ui->listWidgetBodies->takeItem(row);
                        delete item;
                        break;
                    }
                }

                pcBoolean->getDocument()->recomputeFeature(pcBoolean);
                ui->buttonBodyRemove->setChecked(false);
                exitSelectionMode();

                // Make bodies visible again
                Gui::ViewProviderDocumentObject* vp = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                    Gui::Application::Instance->getViewProvider(pcBody)
                );
                if (vp) {
                    vp->show();
                }
                if (bodies.empty()) {
                    Gui::ViewProviderDocumentObject* vp = dynamic_cast<Gui::ViewProviderDocumentObject*>(
                        Gui::Application::Instance->getViewProvider(pcBoolean->BaseFeature.getValue())
                    );
                    if (vp) {
                        vp->show();
                    }
                    BooleanView->hide();
                }
            }
        }
    }
}

void TaskBooleanParameters::onButtonBodyAdd(bool checked)
{
    if (checked) {
        PartDesign::Boolean* pcBoolean = BooleanView->getObject<PartDesign::Boolean>();
        Gui::Document* doc = BooleanView->getDocument();
        BooleanView->hide();
        if (pcBoolean->Group.getValues().empty() && pcBoolean->BaseFeature.getValue()) {
            doc->setHide(pcBoolean->BaseFeature.getValue()->getNameInDocument());
        }
        selectionMode = bodyAdd;
        Gui::Selection().clearSelection(
            pcBoolean->getDocument()->getName()
        );
    }
    else {
        exitSelectionMode();
    }
}

void TaskBooleanParameters::onButtonBodyRemove(bool checked)
{
    if (checked) {
        auto* boolean = BooleanView
            ? BooleanView->getObject<PartDesign::Boolean>()
            : nullptr;
        if (!boolean || !boolean->isAttachedToDocument()) {
            selectionMode = none;
            return;
        }
        BooleanView->show();
        selectionMode = bodyRemove;
        Gui::Selection().clearSelection(
            boolean->getDocument()->getName()
        );
    }
    else {
        exitSelectionMode();
    }
}

void TaskBooleanParameters::onTypeChanged(int index)
{
    PartDesign::Boolean* pcBoolean = BooleanView->getObject<PartDesign::Boolean>();

    switch (index) {
        case 0:
            pcBoolean->Type.setValue("Fuse");
            break;
        case 1:
            pcBoolean->Type.setValue("Cut");
            break;
        case 2:
            pcBoolean->Type.setValue("Common");
            break;
        default:
            pcBoolean->Type.setValue("Fuse");
    }

    // Force UI update before starting heavy computation to show user's selection immediately
    QApplication::processEvents();

    pcBoolean->getDocument()->recomputeFeature(pcBoolean);
}

const std::vector<std::string> TaskBooleanParameters::getBodies() const
{
    std::vector<std::string> result;
    for (int i = 0; i < ui->listWidgetBodies->count(); i++) {
        result.push_back(ui->listWidgetBodies->item(i)->data(Qt::UserRole).toString().toStdString());
    }
    return result;
}

int TaskBooleanParameters::getType() const
{
    return ui->comboType->currentIndex();
}

void TaskBooleanParameters::onBodyDeleted()
{
    PartDesign::Boolean* pcBoolean = BooleanView->getObject<PartDesign::Boolean>();
    std::vector<App::DocumentObject*> bodies = pcBoolean->Group.getValues();
    int index = ui->listWidgetBodies->currentRow();
    if (index < 0 || static_cast<size_t>(index) >= bodies.size()) {
        return;
    }

    App::DocumentObject* body = bodies[index];
    QString internalName = ui->listWidgetBodies->item(index)->data(Qt::UserRole).toString();
    for (auto it = bodies.begin(); it != bodies.end(); ++it) {
        if (internalName == QLatin1String((*it)->getNameInDocument())) {
            body = *it;
            bodies.erase(it);
            break;
        }
    }

    ui->listWidgetBodies->model()->removeRow(index);
    pcBoolean->setObjects(bodies);
    pcBoolean->getDocument()->recomputeFeature(pcBoolean);

    // Make bodies visible again
    Gui::ViewProviderDocumentObject* vp = dynamic_cast<Gui::ViewProviderDocumentObject*>(
        Gui::Application::Instance->getViewProvider(body)
    );
    if (vp) {
        vp->show();
    }
    if (bodies.empty()) {
        Gui::ViewProviderDocumentObject* vp = dynamic_cast<Gui::ViewProviderDocumentObject*>(
            Gui::Application::Instance->getViewProvider(pcBoolean->BaseFeature.getValue())
        );
        if (vp) {
            vp->show();
        }
        BooleanView->hide();
    }
}

TaskBooleanParameters::~TaskBooleanParameters() = default;

void TaskBooleanParameters::changeEvent(QEvent* e)
{
    TaskBox::changeEvent(e);
    if (e->type() == QEvent::LanguageChange) {
        ui->comboType->blockSignals(true);
        int index = ui->comboType->currentIndex();
        ui->retranslateUi(proxy);
        ui->comboType->setCurrentIndex(index);
    }
}

void TaskBooleanParameters::exitSelectionMode()
{
    selectionMode = none;
    Gui::Document* doc = BooleanView ? BooleanView->getDocument() : nullptr;
    if (doc && BooleanView->getObject()
        && BooleanView->getObject()->isAttachedToDocument()) {
        doc->setShow(BooleanView->getObject()->getNameInDocument());
    }
}

//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskDlgBooleanParameters::TaskDlgBooleanParameters(ViewProviderBoolean* BooleanView)
    : TaskDlgFeatureParameters(BooleanView)
    , BooleanView(BooleanView)
{
    assert(BooleanView);
    parameter = new TaskBooleanParameters(BooleanView);

    Content.push_back(parameter);
    Content.push_back(preview);
}

TaskDlgBooleanParameters::~TaskDlgBooleanParameters() = default;

//==== calls from the TaskView ===============================================================


void TaskDlgBooleanParameters::open()
{}

void TaskDlgBooleanParameters::clicked(int)
{}

bool TaskDlgBooleanParameters::accept()
{
    auto* obj = BooleanView->getObject<PartDesign::Boolean>();
    if (!obj || !obj->isAttachedToDocument()) {
        return false;
    }
    TaskInternal::AcceptedMacro acceptedMacro;

    try {
        std::vector<std::string> bodies = parameter->getBodies();
        if (bodies.empty()) {
            acceptedMacro.discard();
            QMessageBox::warning(parameter, tr("Empty Body List"), tr("The body list cannot be empty"));
            return false;
        }
        std::stringstream str;
        str << Gui::Command::getObjectCmd(obj) << ".setObjects( [";
        for (const auto& body : bodies) {
            str << "App.getDocument('" << obj->getDocument()->getName() << "').getObject('" << body
                << "'),";
        }
        str << "])";
        Gui::Command::runCommand(Gui::Command::Doc, str.str().c_str());
        FCMD_OBJ_CMD(obj, "Type = " << parameter->getType());

        Gui::cmdAppDocument(obj, "recompute()");
        if (!obj->isValid() || obj->Shape.getShape().isNull()
            || !obj->Shape.getShape().isValid()) {
            const char* status = obj->getStatusString();
            throw Base::RuntimeError(
                status && *status
                    ? status
                    : "Boolean did not produce valid geometry"
            );
        }

        BooleanView->Visibility.setValue(true);
        Gui::cmdGuiDocument(obj, "resetEdit()");
    }
    catch (const Base::Exception& e) {
        acceptedMacro.discard();
        // Invalid input is not Cancel. Keep the provisional Boolean and task
        // alive so the user can fix the operands or operation type.
        QMessageBox::warning(
            parameter,
            tr("Boolean: Accept: Input error"),
            QCoreApplication::translate("Exception", e.what())
        );
        return false;
    }
    acceptedMacro.publish();
    return true;
}

bool TaskDlgBooleanParameters::reject()
{
    return TaskDlgFeatureParameters::reject();
}

#include "moc_TaskBooleanParameters.cpp"
