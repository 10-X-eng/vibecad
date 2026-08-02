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

#include <QMessageBox>

#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/CommandT.h>
#include <Gui/Document.h>
#include <Gui/ExactTransaction.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Robot/App/RobotObject.h>

#include "OperationSupport.h"


using namespace std;

DEF_STD_CMD_A(CmdRobotAddToolShape)

CmdRobotAddToolShape::CmdRobotAddToolShape()
    : Command("Robot_AddToolShape")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Tool");
    sToolTipText = QT_TR_NOOP("Adds a tool shape to the robot");
    sWhatsThis = "Robot_AddToolShape";
    sStatusTip = sToolTipText;
    sPixmap = "Link";
}


void CmdRobotAddToolShape::activated(int)
{
    auto* activeDocument = RobotGui::OperationSupport::cleanActiveDocument();
    auto* robot = RobotGui::OperationSupport::selectedRobot();
    auto* shape = robot ? RobotGui::OperationSupport::selectedToolShape(*robot) : nullptr;
    if (!activeDocument || !robot || !shape) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("Select one robot and one shape or VRML object.")
        );
        return;
    }
    try {
        const auto documents = RobotGui::OperationSupport::mutationDocuments(*activeDocument, {robot});
        RobotGui::OperationSupport::requireCleanDocuments(*activeDocument, documents);
        Gui::ExactTransaction transaction(
            *activeDocument,
            documents,
            QT_TRANSLATE_NOOP("Command", "Attach robot tool")
        );
        Gui::cmdAppObjectArgs(robot, "ToolShape = %s", Gui::Command::getObjectCmd(shape));
        if (robot->ToolShape.getValue() != shape) {
            throw Base::RuntimeError("The selected tool shape could not be attached to the robot");
        }
        RobotGui::OperationSupport::recompute(documents);
        RobotGui::OperationSupport::commit(transaction);
        Gui::Command::updateActive();
    }
    catch (const Base::Exception& error) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Attach Robot Tool"),
            QString::fromUtf8(error.what())
        );
    }
}

bool CmdRobotAddToolShape::isActive()
{
    auto* robot = RobotGui::OperationSupport::selectedRobot();
    return RobotGui::OperationSupport::cleanActiveDocument() && robot
        && RobotGui::OperationSupport::selectedToolShape(*robot);
}

void CreateRobotCommandsInsertRobots()
{
    Gui::CommandManager& command_manager = Gui::Application::Instance->commandManager();

    command_manager.addCommand(new CmdRobotAddToolShape());
}
