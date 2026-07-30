// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2009 Jürgen Riegel <juergen.riegel@web.de>              *
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


#include <App/Application.h>
#include <App/Document.h>
#include <Gui/Control.h>

#include "TaskDlgSimulate.h"


using namespace RobotGui;

//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskDlgSimulate::TaskDlgSimulate(
    Robot::RobotObject* pcRobotObject,
    Robot::TrajectoryObject* pcTrajectoryObject
)
    : TaskDlgSimulate(
          pcRobotObject,
          pcTrajectoryObject,
          pcRobotObject ? pcRobotObject->getDocument()
                        : (pcTrajectoryObject ? pcTrajectoryObject->getDocument() : nullptr)
      )
{}

TaskDlgSimulate::TaskDlgSimulate(
    Robot::RobotObject* pcRobotObject,
    Robot::TrajectoryObject* pcTrajectoryObject,
    App::Document* taskDocument
)
    : TaskDialog()
{
    rob = new TaskRobot6Axis(pcRobotObject, nullptr, true);
    ctr = new TaskRobotControl(pcRobotObject);

    trac = new TaskTrajectory(pcRobotObject, pcTrajectoryObject);
    msg = new TaskRobotMessages(pcRobotObject);

    QObject::connect(trac, &TaskTrajectory::axisChanged, rob, &TaskRobot6Axis::setAxis);

    Content.push_back(rob);
    Content.push_back(ctr);
    Content.push_back(trac);
    Content.push_back(msg);
    auto* robotDocument = pcRobotObject ? pcRobotObject->getDocument() : nullptr;
    auto* trajectoryDocument = pcTrajectoryObject ? pcTrajectoryObject->getDocument() : nullptr;
    if (!taskDocument) {
        taskDocument = robotDocument;
    }
    if (taskDocument) {
        setDocumentName(taskDocument->getName());
        setAutoCloseOnDeletedDocument(true);
    }
    if (taskDocument && (robotDocument || trajectoryDocument)) {
        sourceDocumentCloseConnection = App::GetApplication().signalBeforeCloseDocument.connect(
            [this, robotDocument, trajectoryDocument](const App::Document& closing) {
                if (&closing != robotDocument && &closing != trajectoryDocument) {
                    return;
                }
                sourceDocumentCloseConnection.disconnect();
                auto* attached = App::GetApplication().getDocument(getDocumentName().c_str());
                if (attached) {
                    Gui::Control().reject(attached);
                }
            }
        );
    }
}

//==== calls from the TaskView ===============================================================


void TaskDlgSimulate::open()
{
    msg->hideGroupBox();
    ctr->hideGroupBox();
}

void TaskDlgSimulate::clicked(int)
{}

bool TaskDlgSimulate::accept()
{
    trac->stopSimulation();
    trac->restorePreview();
    rob->restorePreview();
    return true;
}

bool TaskDlgSimulate::reject()
{
    trac->stopSimulation();
    trac->restorePreview();
    rob->restorePreview();
    return true;
}

void TaskDlgSimulate::helpRequested()
{}


#include "moc_TaskDlgSimulate.cpp"
