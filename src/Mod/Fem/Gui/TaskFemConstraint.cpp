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
#include <QKeyEvent>
#include <QListWidget>
#include <QMessageBox>
#include <sstream>


#include <App/Application.h>
#include <App/Document.h>
#include <Gui/Application.h>
#include <Gui/BitmapFactory.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Tools.h>
#include <Gui/ViewProvider.h>
#include <Mod/Fem/App/FemConstraint.h>

#include "TaskFemConstraint.h"
#include "ui_TaskFemConstraint.h"


using namespace FemGui;
using namespace Gui;

/* TRANSLATOR FemGui::TaskFemConstraint */

TaskFemConstraint::TaskFemConstraint(
    ViewProviderFemConstraint* ConstraintView,
    QWidget* parent,
    const char* pixmapname
)
    : TaskBox(Gui::BitmapFactory().pixmap(pixmapname), tr("Analysis Feature Properties"), true, parent)
    , proxy(nullptr)
    , actionList(nullptr)
    , clearListAction(nullptr)
    , deleteAction(nullptr)
    , ConstraintView(ConstraintView)
    , selectionMode(selref)
{}

bool TaskFemConstraint::event(QEvent* event)
{
    if (event && event->type() == QEvent::ShortcutOverride) {
        auto ke = static_cast<QKeyEvent*>(event);  // NOLINT
        if (deleteAction) {
            if (ke->matches(QKeySequence::Delete) || ke->matches(QKeySequence::Backspace)) {
                ke->accept();
            }
        }
    }
    return TaskBox::event(event);
}

void TaskFemConstraint::keyPressEvent(QKeyEvent* ke)
{
    // if we have a Del key, trigger the deleteAction
    if (ke->matches(QKeySequence::Delete) || ke->matches(QKeySequence::Backspace)) {
        if (deleteAction && deleteAction->isEnabled()) {
            ke->accept();
            deleteAction->trigger();
        }
    }

    TaskBox::keyPressEvent(ke);
}

const std::string TaskFemConstraint::getReferences(const std::vector<std::string>& items) const
{
    const App::DocumentObject* constraint = ConstraintView->getObject();
    const App::Document* document = constraint ? constraint->getDocument() : nullptr;
    if (!document) {
        return {};
    }

    std::string result;
    for (std::vector<std::string>::const_iterator i = items.begin(); i != items.end(); i++) {
        int pos = i->find_last_of(":");
        std::string objStr = "App.getDocument('" + std::string(document->getName())
            + "').getObject('" + i->substr(0, pos) + "')";
        std::string refStr = "\"" + i->substr(pos + 1) + "\"";
        result = result + (i != items.begin() ? ", " : "") + "(" + objStr + "," + refStr + ")";
    }

    return result;
}

const std::string TaskFemConstraint::getScale() const
{
    Fem::Constraint* pcConstraint = ConstraintView->getObject<Fem::Constraint>();

    return std::to_string(pcConstraint->Scale.getValue());
}

std::string TaskDlgFemConstraint::constraintReference(
    const std::string& objectName,
    const std::string& subElement
) const
{
    auto* constraint = ConstraintView ? ConstraintView->getObject() : nullptr;
    auto* document = constraint ? constraint->getDocument() : nullptr;
    if (!document) {
        throw Base::RuntimeError("The FEM constraint document is no longer available");
    }

    return "(" + Gui::Command::getObjectCmd(objectName.c_str(), document) + ",[\"" + subElement
        + "\"])";
}

void TaskFemConstraint::setSelection(QListWidgetItem* item)
{
    // highlights the list item in the model

    // get the document name
    std::string docName = ConstraintView->getObject()->getDocument()->getName();
    // name of the item
    std::string ItemName = item->text().toStdString();
    std::string delimiter = ":";
    size_t pos = 0;
    pos = ItemName.find(delimiter);
    // the objName is the name piece before the ':' of the item name
    std::string objName = ItemName.substr(0, pos);
    // the subName is the name piece behind the ':'
    ItemName.erase(0, pos + delimiter.length());
    // clear existing selection
    Gui::Selection().clearSelection();
    // highlight the selected item
    Gui::Selection().addSelection(docName.c_str(), objName.c_str(), ItemName.c_str(), 0, 0, 0);
}

void TaskFemConstraint::onReferenceClearList()
{
    QSignalBlocker block(actionList);
    actionList->clear();
}

void TaskFemConstraint::onReferenceDeleted(const int row)
{
    Fem::Constraint* pcConstraint = ConstraintView->getObject<Fem::Constraint>();
    std::vector<App::DocumentObject*> Objects = pcConstraint->References.getValues();
    std::vector<std::string> SubElements = pcConstraint->References.getSubValues();

    Objects.erase(Objects.begin() + row);
    SubElements.erase(SubElements.begin() + row);
    pcConstraint->References.setValues(Objects, SubElements);
}

void TaskFemConstraint::onButtonReference(const bool pressed)
{
    if (pressed) {
        selectionMode = selref;
    }
    else {
        selectionMode = selnone;
    }
    Gui::Selection().clearSelection();
}

const QString TaskFemConstraint::makeRefText(const std::string& objName, const std::string& subName) const
{
    return QString::fromUtf8((objName + ":" + subName).c_str());
}

const QString TaskFemConstraint::makeRefText(
    const App::DocumentObject* obj,
    const std::string& subName
) const
{
    return QString::fromUtf8((std::string(obj->getNameInDocument()) + ":" + subName).c_str());
}

void TaskFemConstraint::createActions(QListWidget* parentList)
{
    actionList = parentList;
    createDeleteAction(parentList);
    createClearListAction(parentList);
}

void TaskFemConstraint::createClearListAction(QListWidget* parentList)
{
    clearListAction = new QAction(tr("Clear list"), this);
    connect(clearListAction, &QAction::triggered, this, &TaskFemConstraint::onReferenceClearList);

    parentList->addAction(clearListAction);
    parentList->setContextMenuPolicy(Qt::ActionsContextMenu);
}

void TaskFemConstraint::createDeleteAction(QListWidget* parentList)
{
    // creates a context menu, a shortcut for it and connects it to a slot function

    deleteAction = new QAction(tr("Delete"), this);
    deleteAction->setShortcut(Gui::QtTools::deleteKeySequence());

    // display shortcut behind the context menu entry
    deleteAction->setShortcutVisibleInContextMenu(true);

    parentList->addAction(deleteAction);
    parentList->setContextMenuPolicy(Qt::ActionsContextMenu);
}

//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

//==== calls from the TaskView ===============================================================

void TaskDlgFemConstraint::open()
{
    Gui::Document* guiDocument = ConstraintView->getDocument();
    if (!guiDocument->hasPendingCommand()) {
        const auto typeName = ConstraintView->getObject()->getTypeId().getName();
        const int transactionId = guiDocument->openCommand(std::string {typeName}.c_str());
        if (transactionId == App::NullTransaction
            || !guiDocument->adoptOwnedEditTransaction(transactionId)) {
            if (transactionId != App::NullTransaction) {
                App::GetApplication().abortTransaction(transactionId);
            }
            throw Base::RuntimeError("Could not establish ownership of the FEM constraint edit");
        }
        ConstraintView->setVisible(true);
    }
}

bool TaskDlgFemConstraint::accept()
{
    App::DocumentObject* constraint = ConstraintView->getObject();
    App::Document* document = constraint->getDocument();

    try {
        std::string refs = parameter->getReferences();

        if (!refs.empty()) {
            Gui::cmdAppObject(constraint, std::ostringstream() << "References = [" << refs << "]");
        }
        else {
            QMessageBox::warning(
                parameter,
                tr("Input Error"),
                tr("You must specify at least one reference")
            );
            return false;
        }

        std::string scale = parameter->getScale();
        Gui::cmdAppObject(constraint, std::ostringstream() << "Scale = " << scale);
        Gui::cmdAppDocument(document, "recompute()");
        if (!constraint->isValid()) {
            throw Base::RuntimeError(constraint->getStatusString());
        }
        // The common task boundary owns durability. This reset tears down the
        // captured editor and commits only its exact transaction.
        Gui::cmdGuiDocument(document, "resetEdit()");
    }
    catch (const Base::Exception& e) {
        // Preserve the correctable task and transaction. An explicit Cancel
        // is the sole rollback boundary.
        QMessageBox::warning(parameter, tr("Input Error"), QString::fromLatin1(e.what()));
        return false;
    }

    return true;
}

bool TaskDlgFemConstraint::reject()
{
    App::Document* document = ConstraintView->getObject()->getDocument();
    // TaskView has marked the exact transaction for rollback. Reset the
    // owning editor first; rollback may delete a newly-created constraint.
    Gui::cmdGuiDocument(document, "resetEdit()");
    Gui::Command::updateActive();

    return true;
}

#include "moc_TaskFemConstraint.cpp"
