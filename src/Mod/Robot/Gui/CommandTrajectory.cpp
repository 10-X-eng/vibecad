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

#include <QInputDialog>
#include <QMessageBox>

#include <string_view>

#include <App/Application.h>
#include <App/Document.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/MainWindow.h>
#include <Gui/Placement.h>
#include <Gui/Selection/Selection.h>
#include <Gui/ViewProvider.h>
#include <Mod/Robot/App/Edge2TracObject.h>
#include <Mod/Robot/App/RobotObject.h>
#include <Mod/Robot/App/TrajectoryCompound.h>
#include <Mod/Robot/App/TrajectoryDressUpObject.h>
#include <Mod/Robot/App/TrajectoryObject.h>

#include "TaskDlgEdge2Trac.h"
#include "OperationSupport.h"


using namespace std;
using namespace RobotGui;

namespace
{

void showRobotError(const QString& title, const Base::Exception& error)
{
    QMessageBox::warning(Gui::getMainWindow(), title, QString::fromUtf8(error.what()));
}

template<typename T>
T* addRobotObject(App::Document& document, const char* typeName, const char* baseName)
{
    const std::string name = document.getUniqueObjectName(baseName);
    const std::string documentLiteral = RobotGui::OperationSupport::pythonString(document.getName());
    const std::string nameLiteral = RobotGui::OperationSupport::pythonString(name);
    const QByteArray expression = QByteArray::fromStdString(
        "App.getDocument(" + documentLiteral + ").addObject('" + typeName + "'," + nameLiteral + ")"
    );
    auto* object = freecad_cast<T*>(Gui::Command::runDocumentObjectCommand(
        Gui::Command::Doc,
        document,
        expression,
        T::getClassTypeId()
    ));
    if (!object) {
        throw Base::RuntimeError("The robot operation object could not be created");
    }
    return object;
}

bool selectedExactObjectInActiveDocument(
    const App::DocumentObject* object,
    const App::Document* activeDocument
) noexcept
{
    return object && activeDocument && object->getDocument() == activeDocument
        && RobotGui::OperationSupport::isUsableObject(object);
}

}  // namespace

// #####################################################################################################

DEF_STD_CMD_A(CmdRobotCreateTrajectory)

CmdRobotCreateTrajectory::CmdRobotCreateTrajectory()
    : Command("Robot_CreateTrajectory")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Trajectory");
    sToolTipText = QT_TR_NOOP("Creates a new empty trajectory");
    sWhatsThis = "Robot_CreateTrajectory";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_CreateTrajectory";
}


void CmdRobotCreateTrajectory::activated(int)
{
    auto* document = RobotGui::OperationSupport::cleanActiveDocument();
    if (!document) {
        return;
    }
    try {
        Gui::ExactTransaction transaction(*document, QT_TRANSLATE_NOOP("Command", "Create trajectory"));
        auto* trajectory = addRobotObject<Robot::TrajectoryObject>(
            *document,
            "Robot::TrajectoryObject",
            "Trajectory"
        );
        RobotGui::OperationSupport::publishOperation(*trajectory);
        RobotGui::OperationSupport::recompute({document});
        RobotGui::OperationSupport::commit(transaction);
        Gui::Selection().clearSelection();
        Gui::Selection().addSelection(document->getName(), trajectory->getNameInDocument());
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showRobotError(QObject::tr("Create Trajectory"), error);
    }
}

bool CmdRobotCreateTrajectory::isActive()
{
    return RobotGui::OperationSupport::cleanActiveDocument() != nullptr;
}

// #####################################################################################################

DEF_STD_CMD_A(CmdRobotInsertWaypoint)

CmdRobotInsertWaypoint::CmdRobotInsertWaypoint()
    : Command("Robot_InsertWaypoint")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Insert in Trajectory");
    sToolTipText = QT_TR_NOOP("Inserts the robot tool location into the trajectory");
    sWhatsThis = "Robot_InsertWaypoint";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_InsertWaypoint";
    sAccel = "A";
}


void CmdRobotInsertWaypoint::activated(int)
{
    const auto selection = RobotGui::OperationSupport::selectedRobotAndTrajectory();
    auto* activeDocument = RobotGui::OperationSupport::cleanActiveDocument();
    if (!selection || !activeDocument) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("Select one Robot and one Trajectory object.")
        );
        return;
    }
    try {
        const auto documents
            = RobotGui::OperationSupport::mutationDocuments(*activeDocument, {selection.trajectory});
        RobotGui::OperationSupport::requireCleanDocuments(*activeDocument, documents);
        Gui::ExactTransaction transaction(
            *activeDocument,
            documents,
            QT_TRANSLATE_NOOP("Command", "Insert waypoint")
        );
        const auto robotExpression = Gui::Command::getObjectCmd(selection.robot);
        const auto trajectoryExpression = Gui::Command::getObjectCmd(selection.trajectory);
        Gui::cmdAppObjectArgs(
            selection.trajectory,
            "Trajectory = %s.Trajectory.insertWaypoints("
            "Robot.Waypoint(%s.Tcp.multiply(%s.Tool), type='LIN', "
            "name='Pt', vel=_DefSpeed, cont=_DefCont, "
            "acc=_DefAcceleration, tool=1))",
            trajectoryExpression,
            robotExpression,
            robotExpression
        );
        RobotGui::OperationSupport::recompute(documents);
        RobotGui::OperationSupport::commit(transaction);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showRobotError(QObject::tr("Insert Waypoint"), error);
    }
}

bool CmdRobotInsertWaypoint::isActive()
{
    return RobotGui::OperationSupport::cleanActiveDocument()
        && RobotGui::OperationSupport::selectedRobotAndTrajectory();
}

// #####################################################################################################

DEF_STD_CMD_A(CmdRobotInsertWaypointPreselect)

CmdRobotInsertWaypointPreselect::CmdRobotInsertWaypointPreselect()
    : Command("Robot_InsertWaypointPreselect")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Insert in Trajectory");
    sToolTipText = QT_TR_NOOP("Inserts the preselection position into the trajectory (W)");
    sWhatsThis = "Robot_InsertWaypointPreselect";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_InsertWaypointPre";
    sAccel = "W";
}


void CmdRobotInsertWaypointPreselect::activated(int)
{
    auto* activeDocument = RobotGui::OperationSupport::cleanActiveDocument();
    auto* trajectory = RobotGui::OperationSupport::selectedTrajectory();
    if (!activeDocument || !trajectory) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("Select one Trajectory object.")
        );
        return;
    }

    const Gui::SelectionChanges& PreSel = getSelection().getPreselection();
    if (!PreSel.pDocName || std::string_view(activeDocument->getName()) != PreSel.pDocName) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("No preselection"),
            QObject::tr(
                "You have to hover above a geometry (Preselection) with the mouse to use "
                "this command. See documentation for details."
            )
        );
        return;
    }
    try {
        const auto documents
            = RobotGui::OperationSupport::mutationDocuments(*activeDocument, {trajectory});
        RobotGui::OperationSupport::requireCleanDocuments(*activeDocument, documents);
        Gui::ExactTransaction transaction(
            *activeDocument,
            documents,
            QT_TRANSLATE_NOOP("Command", "Insert waypoint")
        );
        const auto trajectoryExpression = Gui::Command::getObjectCmd(trajectory);
        Gui::cmdAppObjectArgs(
            trajectory,
            "Trajectory = %s.Trajectory.insertWaypoints("
            "Robot.Waypoint(App.Placement("
            "App.Vector(%f, %f, %f) + _DefDisplacement, "
            "_DefOrientation), type='LIN', name='Pt', "
            "vel=_DefSpeed, cont=_DefCont, "
            "acc=_DefAcceleration, tool=1))",
            trajectoryExpression,
            PreSel.x,
            PreSel.y,
            PreSel.z
        );
        RobotGui::OperationSupport::recompute(documents);
        RobotGui::OperationSupport::commit(transaction);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showRobotError(QObject::tr("Insert Waypoint"), error);
    }
}

bool CmdRobotInsertWaypointPreselect::isActive()
{
    auto* activeDocument = RobotGui::OperationSupport::cleanActiveDocument();
    auto* trajectory = RobotGui::OperationSupport::selectedTrajectory();
    const auto& preselection = getSelection().getPreselection();
    return activeDocument && trajectory && preselection.pDocName
        && std::string_view(activeDocument->getName()) == preselection.pDocName;
}

// #####################################################################################################

DEF_STD_CMD_A(CmdRobotSetDefaultOrientation)

CmdRobotSetDefaultOrientation::CmdRobotSetDefaultOrientation()
    : Command("Robot_SetDefaultOrientation")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Set Default Orientation");
    sToolTipText = QT_TR_NOOP(
        "Sets the default orientation for subsequent commands for waypoint creation"
    );
    sWhatsThis = "Robot_SetDefaultOrientation";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_SetDefaultOrientation";
}


void CmdRobotSetDefaultOrientation::activated(int)
{
    // create placement dialog
    Gui::Dialog::Placement Dlg;
    Dlg.setValueOnlyMode(true);
    const auto selection = Gui::Selection().getSelectionEx();
    if (!selection.empty()) {
        Dlg.setSelection(selection);
    }
    Base::Placement place;
    Dlg.setPlacement(place);
    if (Dlg.exec() == QDialog::Accepted) {
        place = Dlg.getPlacement();
        Base::Rotation rot = place.getRotation();
        Base::Vector3d disp = place.getPosition();
        doCommand(Doc, "_DefOrientation = App.Rotation(%f, %f, %f, %f)", rot[0], rot[1], rot[2], rot[3]);
        doCommand(Doc, "_DefDisplacement = App.Vector(%f, %f, %f)", disp[0], disp[1], disp[2]);
    }
}

bool CmdRobotSetDefaultOrientation::isActive()
{
    return !Gui::Control().activeDialog();
}

// #####################################################################################################

DEF_STD_CMD_A(CmdRobotSetDefaultValues)

CmdRobotSetDefaultValues::CmdRobotSetDefaultValues()
    : Command("Robot_SetDefaultValues")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Set Default Values");
    sToolTipText = QT_TR_NOOP(
        "Sets the default values for speed, acceleration, and continuity for "
        "subsequent commands of waypoint creation"
    );
    sWhatsThis = "Robot_SetDefaultValues";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_SetDefaultValues";
}


void CmdRobotSetDefaultValues::activated(int)
{
    bool ok;
    const QString speed = QInputDialog::getText(
        nullptr,
        QObject::tr("Set default speed"),
        QObject::tr("speed: (e.g. 1 m/s or 3 cm/s)"),
        QLineEdit::Normal,
        QStringLiteral("1 m/s"),
        &ok,
        Qt::MSWindowsFixedSizeDialogHint
    );
    if (!ok || speed.trimmed().isEmpty()) {
        return;
    }

    QStringList items;
    items << QStringLiteral("False") << QStringLiteral("True");

    const QString continuity = QInputDialog::getItem(
        nullptr,
        QObject::tr("Set default continuity"),
        QObject::tr("continuous ?"),
        items,
        0,
        false,
        &ok,
        Qt::MSWindowsFixedSizeDialogHint
    );
    if (!ok || continuity.isEmpty()) {
        return;
    }

    const QString acceleration = QInputDialog::getText(
        nullptr,
        QObject::tr("Set default acceleration"),
        QObject::tr("acceleration: (e.g. 1 m/s^2 or 3 cm/s^2)"),
        QLineEdit::Normal,
        QStringLiteral("1 m/s^2"),
        &ok,
        Qt::MSWindowsFixedSizeDialogHint
    );
    if (!ok || acceleration.trimmed().isEmpty()) {
        return;
    }

    doCommand(
        Doc,
        "_DefSpeed = %s",
        RobotGui::OperationSupport::pythonString(speed.trimmed().toStdString()).c_str()
    );
    doCommand(Doc, "_DefCont = %s", continuity == QStringLiteral("True") ? "True" : "False");
    doCommand(
        Doc,
        "_DefAcceleration = %s",
        RobotGui::OperationSupport::pythonString(acceleration.trimmed().toStdString()).c_str()
    );
}

bool CmdRobotSetDefaultValues::isActive()
{
    return !Gui::Control().activeDialog();
}

// #####################################################################################################

DEF_STD_CMD_A(CmdRobotEdge2Trac)

CmdRobotEdge2Trac::CmdRobotEdge2Trac()
    : Command("Robot_Edge2Trac")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Edge to Trajectory");
    sToolTipText = QT_TR_NOOP("Generates a trajectory from the selected edges");
    sWhatsThis = "Robot_Edge2Trac";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_Edge2Trac";
}


void CmdRobotEdge2Trac::activated(int)
{
    auto* document = RobotGui::OperationSupport::cleanActiveDocument();
    if (!document) {
        return;
    }
    Gui::SelectionFilter ObjectFilter("SELECT Robot::Edge2TracObject COUNT 1");
    Gui::SelectionFilter EdgeFilter("SELECT Part::Feature SUBELEMENT Edge COUNT 1..");
    Robot::Edge2TracObject* operation = nullptr;
    std::string sourceExpression;
    bool create = true;

    if (ObjectFilter.match()) {
        operation = freecad_cast<Robot::Edge2TracObject*>(ObjectFilter.Result[0][0].getObject());
        if (!selectedExactObjectInActiveDocument(operation, document)) {
            return;
        }
        create = false;
    }
    else if (EdgeFilter.match()) {
        auto* source = EdgeFilter.Result[0][0].getObject();
        if (!selectedExactObjectInActiveDocument(source, document)) {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Edge Is Not Available"),
                QObject::tr("Select active edges from the current document.")
            );
            return;
        }
        sourceExpression = EdgeFilter.Result[0][0].getAsPropertyLinkSubString();
    }

    const int transactionId = openCommand(
        document,
        create ? QT_TRANSLATE_NOOP("Command", "Create edge trajectory")
               : QT_TRANSLATE_NOOP("Command", "Edit edge trajectory")
    );
    try {
        if (transactionId == App::NullTransaction) {
            throw Base::RuntimeError("The edge trajectory task could not start");
        }
        if (create) {
            operation = addRobotObject<Robot::Edge2TracObject>(
                *document,
                "Robot::Edge2TracObject",
                "EdgeTrajectory"
            );
            if (!sourceExpression.empty()) {
                Gui::cmdAppObjectArgs(operation, "Source = %s", sourceExpression);
            }
            RobotGui::OperationSupport::publishOperation(*operation);
        }
        const std::string documentLiteral = RobotGui::OperationSupport::pythonString(
            document->getName()
        );
        const std::string objectLiteral = RobotGui::OperationSupport::pythonString(
            operation->getNameInDocument()
        );
        doCommand(
            Gui,
            "Gui.getDocument(%s).setEdit(%s, 0)",
            documentLiteral.c_str(),
            objectLiteral.c_str()
        );
        auto* guiDocument = Gui::Application::Instance->getDocument(document);
        if (!guiDocument || !guiDocument->getEditViewProvider()
            || guiDocument->getEditViewProvider()
                != Gui::Application::Instance->getViewProvider(operation)) {
            throw Base::RuntimeError("The edge trajectory editor could not be opened");
        }
    }
    catch (const Base::Exception& error) {
        if (auto* guiDocument = Gui::Application::Instance->getDocument(document);
            guiDocument && guiDocument->getEditViewProvider()) {
            guiDocument->cancelEdit();
        }
        if (document->getBookedTransactionID() == transactionId) {
            abortCommand(transactionId);
        }
        resetTransactionID();
        showRobotError(QObject::tr("Edge to Trajectory"), error);
    }
}

bool CmdRobotEdge2Trac::isActive()
{
    return RobotGui::OperationSupport::cleanActiveDocument() != nullptr;
}

// #####################################################################################################

DEF_STD_CMD_A(CmdRobotTrajectoryDressUp)

CmdRobotTrajectoryDressUp::CmdRobotTrajectoryDressUp()
    : Command("Robot_TrajectoryDressUp")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Dress-Up Trajectory");
    sToolTipText = QT_TR_NOOP("Creates a dress-up object that overrides aspects of a trajectory");
    sWhatsThis = "Robot_TrajectoryDressUp";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_TrajectoryDressUp";
}


void CmdRobotTrajectoryDressUp::activated(int)
{
    auto* document = RobotGui::OperationSupport::cleanActiveDocument();
    if (!document) {
        return;
    }
    Gui::SelectionFilter ObjectFilterDressUp("SELECT Robot::TrajectoryDressUpObject COUNT 1");
    Robot::TrajectoryDressUpObject* operation = nullptr;
    Robot::TrajectoryObject* source = nullptr;
    bool create = true;
    if (ObjectFilterDressUp.match()) {
        operation = freecad_cast<Robot::TrajectoryDressUpObject*>(
            ObjectFilterDressUp.Result[0][0].getObject()
        );
        if (!selectedExactObjectInActiveDocument(operation, document)) {
            return;
        }
        create = false;
    }
    else {
        source = RobotGui::OperationSupport::selectedTrajectory();
        if (source) {
            create = true;
        }
        else {
            QMessageBox::warning(
                Gui::getMainWindow(),
                QObject::tr("Wrong selection"),
                QObject::tr(
                    "Select one trajectory to modify, or select an existing "
                    "trajectory modifier to edit it."
                )
            );
            return;
        }
    }

    const int transactionId = openCommand(
        document,
        create ? QT_TRANSLATE_NOOP("Command", "Create trajectory modifier")
               : QT_TRANSLATE_NOOP("Command", "Edit trajectory modifier")
    );
    try {
        if (transactionId == App::NullTransaction) {
            throw Base::RuntimeError("The trajectory modifier task could not start");
        }
        if (create) {
            operation = addRobotObject<Robot::TrajectoryDressUpObject>(
                *document,
                "Robot::TrajectoryDressUpObject",
                "TrajectoryModifier"
            );
            Gui::cmdAppObjectArgs(operation, "Source = %s", Gui::Command::getObjectCmd(source));
            if (source->getDocument() == document) {
                RobotGui::OperationSupport::publishReplacingOperation(*operation, {source});
            }
            else {
                RobotGui::OperationSupport::publishOperation(*operation);
            }
        }
        const std::string documentLiteral = RobotGui::OperationSupport::pythonString(
            document->getName()
        );
        const std::string objectLiteral = RobotGui::OperationSupport::pythonString(
            operation->getNameInDocument()
        );
        doCommand(
            Gui,
            "Gui.getDocument(%s).setEdit(%s, 0)",
            documentLiteral.c_str(),
            objectLiteral.c_str()
        );
        auto* guiDocument = Gui::Application::Instance->getDocument(document);
        if (!guiDocument || !guiDocument->getEditViewProvider()
            || guiDocument->getEditViewProvider()
                != Gui::Application::Instance->getViewProvider(operation)) {
            throw Base::RuntimeError("The trajectory modifier editor could not be opened");
        }
    }
    catch (const Base::Exception& error) {
        if (auto* guiDocument = Gui::Application::Instance->getDocument(document);
            guiDocument && guiDocument->getEditViewProvider()) {
            guiDocument->cancelEdit();
        }
        if (document->getBookedTransactionID() == transactionId) {
            abortCommand(transactionId);
        }
        resetTransactionID();
        showRobotError(QObject::tr("Modify Trajectory"), error);
    }
}

bool CmdRobotTrajectoryDressUp::isActive()
{
    return RobotGui::OperationSupport::cleanActiveDocument()
        && RobotGui::OperationSupport::selectedTrajectory();
}

// #####################################################################################################

DEF_STD_CMD_A(CmdRobotTrajectoryCompound)

CmdRobotTrajectoryCompound::CmdRobotTrajectoryCompound()
    : Command("Robot_TrajectoryCompound")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Trajectory Compound");
    sToolTipText = QT_TR_NOOP("Groups and connects multiple trajectories into one");
    sWhatsThis = "Robot_TrajectoryCompound";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_TrajectoryCompound";
}


void CmdRobotTrajectoryCompound::activated(int)
{
    auto* document = RobotGui::OperationSupport::cleanActiveDocument();
    if (!document) {
        return;
    }
    Gui::SelectionFilter ObjectFilter("SELECT Robot::TrajectoryCompound COUNT 1");
    Robot::TrajectoryCompound* operation = nullptr;
    bool create = true;
    if (ObjectFilter.match()) {
        operation = freecad_cast<Robot::TrajectoryCompound*>(ObjectFilter.Result[0][0].getObject());
        if (!selectedExactObjectInActiveDocument(operation, document)) {
            return;
        }
        create = false;
    }

    const int transactionId = openCommand(
        document,
        create ? QT_TRANSLATE_NOOP("Command", "Create trajectory sequence")
               : QT_TRANSLATE_NOOP("Command", "Edit trajectory sequence")
    );
    try {
        if (transactionId == App::NullTransaction) {
            throw Base::RuntimeError("The trajectory sequence task could not start");
        }
        if (create) {
            operation = addRobotObject<Robot::TrajectoryCompound>(
                *document,
                "Robot::TrajectoryCompound",
                "TrajectorySequence"
            );
            const auto selected = RobotGui::OperationSupport::selectedTrajectories();
            std::vector<App::DocumentObject*> localSources;
            std::string sourceList = "[";
            bool first = true;
            for (auto* trajectory : selected) {
                if (trajectory == operation) {
                    continue;
                }
                if (!first) {
                    sourceList += ", ";
                }
                sourceList += Gui::Command::getObjectCmd(trajectory);
                first = false;
                if (trajectory->getDocument() == document) {
                    localSources.push_back(trajectory);
                }
            }
            sourceList += "]";
            if (!selected.empty()) {
                Gui::cmdAppObjectArgs(operation, "Source = %s", sourceList);
            }
            if (!localSources.empty()) {
                RobotGui::OperationSupport::publishReplacingOperation(*operation, localSources);
            }
            else {
                RobotGui::OperationSupport::publishOperation(*operation);
            }
        }
        const std::string documentLiteral = RobotGui::OperationSupport::pythonString(
            document->getName()
        );
        const std::string objectLiteral = RobotGui::OperationSupport::pythonString(
            operation->getNameInDocument()
        );
        doCommand(
            Gui,
            "Gui.getDocument(%s).setEdit(%s, 0)",
            documentLiteral.c_str(),
            objectLiteral.c_str()
        );
        auto* guiDocument = Gui::Application::Instance->getDocument(document);
        if (!guiDocument || !guiDocument->getEditViewProvider()
            || guiDocument->getEditViewProvider()
                != Gui::Application::Instance->getViewProvider(operation)) {
            throw Base::RuntimeError("The trajectory sequence editor could not be opened");
        }
    }
    catch (const Base::Exception& error) {
        if (auto* guiDocument = Gui::Application::Instance->getDocument(document);
            guiDocument && guiDocument->getEditViewProvider()) {
            guiDocument->cancelEdit();
        }
        if (document->getBookedTransactionID() == transactionId) {
            abortCommand(transactionId);
        }
        resetTransactionID();
        showRobotError(QObject::tr("Trajectory Sequence"), error);
    }
}

bool CmdRobotTrajectoryCompound::isActive()
{
    return RobotGui::OperationSupport::cleanActiveDocument() != nullptr;
}


// #####################################################################################################


void CreateRobotCommandsTrajectory()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdRobotCreateTrajectory());
    rcCmdMgr.addCommand(new CmdRobotInsertWaypoint());
    rcCmdMgr.addCommand(new CmdRobotInsertWaypointPreselect());
    rcCmdMgr.addCommand(new CmdRobotSetDefaultOrientation());
    rcCmdMgr.addCommand(new CmdRobotSetDefaultValues());
    rcCmdMgr.addCommand(new CmdRobotEdge2Trac());
    rcCmdMgr.addCommand(new CmdRobotTrajectoryDressUp());
    rcCmdMgr.addCommand(new CmdRobotTrajectoryCompound());
}
