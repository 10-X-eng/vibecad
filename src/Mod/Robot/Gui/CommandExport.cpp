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


#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Document.h>
#include <Gui/FileDialog.h>
#include <Gui/MainWindow.h>
#include <Gui/Selection/Selection.h>
#include <Mod/Robot/App/RobotObject.h>
#include <Mod/Robot/App/TrajectoryObject.h>

#include "OperationSupport.h"

using namespace std;

namespace
{

void exportKukaProgram(const char* functionName, const QString& title)
{
    const auto selection = RobotGui::OperationSupport::selectedRobotAndTrajectory();
    if (!selection || !RobotGui::OperationSupport::cleanActiveDocument()) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Wrong selection"),
            QObject::tr("Select one robot and one trajectory in the current document.")
        );
        return;
    }
    if (selection.trajectory->Trajectory.getValue().getSize() == 0) {
        QMessageBox::warning(
            Gui::getMainWindow(),
            QObject::tr("Trajectory Is Empty"),
            QObject::tr("Add at least one waypoint before exporting the trajectory.")
        );
        return;
    }

    const Gui::FileDialog::FilterList filter {
        {QObject::tr("KRL source"), {"*.src"}},
        Gui::FileDialog::Filter::AllFiles(),
    };
    const QString fileName
        = Gui::FileDialog::getSaveFileName(Gui::getMainWindow(), title, QString(), filter);
    if (fileName.isEmpty()) {
        return;
    }

    const std::string fileLiteral = RobotGui::OperationSupport::pythonString(fileName.toStdString());
    Gui::Command::doCommand(Gui::Command::Doc, "from KukaExporter import %s", functionName);
    Gui::Command::doCommand(
        Gui::Command::Doc,
        "%s(%s, %s, %s)",
        functionName,
        Gui::Command::getObjectCmd(selection.robot).c_str(),
        Gui::Command::getObjectCmd(selection.trajectory).c_str(),
        fileLiteral.c_str()
    );
}

bool canExportKukaProgram()
{
    const auto selection = RobotGui::OperationSupport::selectedRobotAndTrajectory();
    return RobotGui::OperationSupport::cleanActiveDocument() && selection
        && selection.trajectory->Trajectory.getValue().getSize() > 0;
}

}  // namespace

DEF_STD_CMD_A(CmdRobotExportKukaCompact)

CmdRobotExportKukaCompact::CmdRobotExportKukaCompact()
    : Command("Robot_ExportKukaCompact")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Kuka Compact Subroutine");
    sToolTipText = QT_TR_NOOP("Exports the trajectory as a compact KRL subroutine");
    sWhatsThis = "Robot_ExportKukaCompact";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_Export";
}


void CmdRobotExportKukaCompact::activated(int)
{
    exportKukaProgram("ExportCompactSub", QObject::tr("Export Compact KUKA Program"));
}

bool CmdRobotExportKukaCompact::isActive()
{
    return canExportKukaProgram();
}

// #####################################################################################################


DEF_STD_CMD_A(CmdRobotExportKukaFull)

CmdRobotExportKukaFull::CmdRobotExportKukaFull()
    : Command("Robot_ExportKukaFull")
{
    sAppModule = "Robot";
    sGroup = QT_TR_NOOP("Robot");
    sMenuText = QT_TR_NOOP("Kuka Full Subroutine");
    sToolTipText = QT_TR_NOOP("Exports the trajectory as a full KRL subroutine");
    sWhatsThis = "Robot_ExportKukaFull";
    sStatusTip = sToolTipText;
    sPixmap = "Robot_Export";
}


void CmdRobotExportKukaFull::activated(int)
{
    exportKukaProgram("ExportFullSub", QObject::tr("Export Full KUKA Program"));
}

bool CmdRobotExportKukaFull::isActive()
{
    return canExportKukaProgram();
}

// #####################################################################################################


void CreateRobotCommandsExport()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    rcCmdMgr.addCommand(new CmdRobotExportKukaFull());
    rcCmdMgr.addCommand(new CmdRobotExportKukaCompact());
}
