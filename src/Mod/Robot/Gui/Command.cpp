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

#include <QFileInfo>
#include <QMessageBox>

#include <App/Document.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Control.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/FileDialog.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Gui/Selection/SelectionFilter.h>
#include <Mod/Robot/App/RobotObject.h>
#include <Mod/Robot/App/TrajectoryObject.h>

#include "TaskDlgSimulate.h"
#include "OperationSupport.h"


using namespace std;
using namespace RobotGui;

namespace
{

QString getWrl(const QString& hintDirectory)
{
    const Gui::FileDialog::FilterList filters {
        {QObject::tr("VRML files"), {"*.wrl", "*.vrml"}},
        Gui::FileDialog::Filter::AllFiles(),
    };
    return Gui::FileDialog::getOpenFileName(
        Gui::getMainWindow(),
        QObject::tr("Select VRML file for Robot"),
        hintDirectory,
        filters
    );
}

QString getCsv(const QString& wrlPath)
{
    const Gui::FileDialog::FilterList filters {
        {QObject::tr("CSV files"), {"*.csv"}},
        Gui::FileDialog::Filter::AllFiles(),
    };
    return Gui::FileDialog::getOpenFileName(
        Gui::getMainWindow(),
        QObject::tr("Select Kinematic CSV file for Robot"),
        QFileInfo(wrlPath).absolutePath(),
        filters
    );
}

bool isReadableFile(const QString& path)
{
    const QFileInfo info(path);
    return info.exists() && info.isFile() && info.isReadable();
}

void showRobotError(const QString& title, const Base::Exception& error)
{
    QMessageBox::warning(Gui::getMainWindow(), title, QString::fromUtf8(error.what()));
}

}  // namespace

DEF_STD_CMD_A(CmdRobotSetHomePos)

CmdRobotSetHomePos::CmdRobotSetHomePos()
    : Command("Robot_SetHomePos")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Set Home Position");
    sToolTipText = QT_TR_NOOP("Sets the home position");
    sWhatsThis = "Robot_SetHomePos";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_SetHomePos";
}


void CmdRobotSetHomePos::activated(int)
{
    auto* activeDocument = RobotGui::OperationSupport::cleanActiveDocument();
    auto* robot = RobotGui::OperationSupport::selectedRobot();
    if (!activeDocument || !robot) {
        return;
    }
    try {
        const auto documents = RobotGui::OperationSupport::mutationDocuments(*activeDocument, {robot});
        RobotGui::OperationSupport::requireCleanDocuments(*activeDocument, documents);
        Gui::ExactTransaction transaction(
            *activeDocument,
            documents,
            QT_TRANSLATE_NOOP("Command", "Set robot home")
        );
        Gui::cmdAppObjectArgs(
            robot,
            "Home = [%.17g, %.17g, %.17g, %.17g, %.17g, %.17g]",
            robot->Axis1.getValue(),
            robot->Axis2.getValue(),
            robot->Axis3.getValue(),
            robot->Axis4.getValue(),
            robot->Axis5.getValue(),
            robot->Axis6.getValue()
        );
        RobotGui::OperationSupport::recompute(documents);
        RobotGui::OperationSupport::commit(transaction);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showRobotError(QObject::tr("Set Robot Home"), error);
    }
}

bool CmdRobotSetHomePos::isActive()
{
    return RobotGui::OperationSupport::cleanActiveDocument()
        && RobotGui::OperationSupport::selectedRobot();
}


// #####################################################################################################
DEF_STD_CMD_A(CmdRobotRestoreHomePos)

CmdRobotRestoreHomePos::CmdRobotRestoreHomePos()
    : Command("Robot_RestoreHomePos")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Move to Home");
    sToolTipText = QT_TR_NOOP("Moves to the home position");
    sWhatsThis = "Robot_RestoreHomePos";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_RestoreHomePos";
}


void CmdRobotRestoreHomePos::activated(int)
{
    auto* activeDocument = RobotGui::OperationSupport::cleanActiveDocument();
    auto* robot = RobotGui::OperationSupport::selectedRobot();
    if (!activeDocument || !robot) {
        return;
    }
    if (robot->Home.getSize() != 6) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Robot Home Is Not Set"),
            QObject::tr("Set a six-axis home position for the selected robot first.")
        );
        return;
    }
    try {
        const auto documents = RobotGui::OperationSupport::mutationDocuments(*activeDocument, {robot});
        RobotGui::OperationSupport::requireCleanDocuments(*activeDocument, documents);
        Gui::ExactTransaction transaction(
            *activeDocument,
            documents,
            QT_TRANSLATE_NOOP("Command", "Move robot home")
        );
        const auto& home = robot->Home.getValues();
        for (int axis = 1; axis <= 6; ++axis) {
            Gui::cmdAppObjectArgs(robot, "Axis%d = %.17g", axis, home[axis - 1]);
        }
        RobotGui::OperationSupport::recompute(documents);
        RobotGui::OperationSupport::commit(transaction);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showRobotError(QObject::tr("Move Robot Home"), error);
    }
}

bool CmdRobotRestoreHomePos::isActive()
{
    auto* robot = RobotGui::OperationSupport::selectedRobot();
    return RobotGui::OperationSupport::cleanActiveDocument() && robot && robot->Home.getSize() == 6;
}


// #####################################################################################################
DEF_STD_CMD_A(CmdRobotConstraintAxle)

CmdRobotConstraintAxle::CmdRobotConstraintAxle()
    : Command("Robot_Create")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Place Robot");
    sToolTipText = QT_TR_NOOP("Places a robot in the scene");

    sWhatsThis = "Robot_Create";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_CreateRobot";
}


void CmdRobotConstraintAxle::activated([[maybe_unused]] int msg)
{
    auto* document = RobotGui::OperationSupport::cleanActiveDocument();
    if (!document) {
        return;
    }
    const QString wrlPath = getWrl({});
    if (wrlPath.isEmpty()) {
        return;
    }
    const QString kinematicPath = getCsv(wrlPath);
    if (kinematicPath.isEmpty()) {
        return;
    }
    if (!isReadableFile(wrlPath) || !isReadableFile(kinematicPath)) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Robot Definition Is Unavailable"),
            QObject::tr("Choose readable VRML and kinematic CSV files.")
        );
        return;
    }

    try {
        Gui::ExactTransaction transaction(*document, QT_TRANSLATE_NOOP("Command", "Place robot"));
        const std::string name = document->getUniqueObjectName("Robot");
        const std::string documentLiteral = RobotGui::OperationSupport::pythonString(
            document->getName()
        );
        const std::string nameLiteral = RobotGui::OperationSupport::pythonString(name);
        const QByteArray expression = QByteArray::fromStdString(
            "App.getDocument(" + documentLiteral + ").addObject('Robot::RobotObject'," + nameLiteral
            + ")"
        );
        auto* robot = freecad_cast<Robot::RobotObject*>(Gui::Command::runDocumentObjectCommand(
            Gui::Command::Doc,
            *document,
            expression,
            Robot::RobotObject::getClassTypeId()
        ));
        if (!robot) {
            throw Base::RuntimeError("The robot object could not be created");
        }
        Gui::cmdAppObjectArgs(
            robot,
            "RobotVrmlFile = %s",
            RobotGui::OperationSupport::pythonString(wrlPath.toStdString())
        );
        Gui::cmdAppObjectArgs(
            robot,
            "RobotKinematicFile = %s",
            RobotGui::OperationSupport::pythonString(kinematicPath.toStdString())
        );
        RobotGui::OperationSupport::publishOperation(*robot);
        RobotGui::OperationSupport::recompute({document});
        RobotGui::OperationSupport::commit(transaction);
        Gui::Selection().clearSelection();
        Gui::Selection().addSelection(document->getName(), robot->getNameInDocument());
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        showRobotError(QObject::tr("Place Robot"), error);
    }
}

bool CmdRobotConstraintAxle::isActive()
{
    return RobotGui::OperationSupport::cleanActiveDocument() != nullptr;
}


// #####################################################################################################

DEF_STD_CMD_A(CmdRobotSimulate)

CmdRobotSimulate::CmdRobotSimulate()
    : Command("Robot_Simulate")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Simulate Trajectory");
    sToolTipText = QT_TR_NOOP("Simulates robot movement along a selected trajectory");
    sWhatsThis = "Robot_Simulate";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_Simulate";
}


void CmdRobotSimulate::activated(int)
{
    const auto selection = RobotGui::OperationSupport::selectedRobotAndTrajectory();
    if (!selection || !RobotGui::OperationSupport::cleanActiveDocument()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("Select one Robot and one Trajectory object.")
        );
        return;
    }

    const auto& trajectory = selection.trajectory->Trajectory.getValue();
    if (trajectory.getSize() < 2 || trajectory.getDuration() <= 0.0) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Trajectory not valid"),
            QObject::tr(
                "Simulation requires at least two waypoints and a positive "
                "trajectory duration."
            )
        );
        return;
    }

    Gui::TaskView::TaskDialog* dlg
        = new TaskDlgSimulate(selection.robot, selection.trajectory, selection.activeDocument);
    Gui::Control().showDialog(dlg, selection.activeDocument);
}

bool CmdRobotSimulate::isActive()
{
    return RobotGui::OperationSupport::cleanActiveDocument()
        && RobotGui::OperationSupport::selectedRobotAndTrajectory();
}


// #####################################################################################################


void CreateRobotCommands()
{
    Gui::CommandManager& command_manager = Gui::Application::Instance->commandManager();

    command_manager.addCommand(new CmdRobotRestoreHomePos());
    command_manager.addCommand(new CmdRobotSetHomePos());
    command_manager.addCommand(new CmdRobotConstraintAxle());
    command_manager.addCommand(new CmdRobotSimulate());
}
