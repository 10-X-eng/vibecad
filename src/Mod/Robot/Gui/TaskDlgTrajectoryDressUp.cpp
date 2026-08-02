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


#include <QMessageBox>

#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>

#include "OperationSupport.h"
#include "TaskDlgTrajectoryDressUp.h"


using namespace RobotGui;

//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskDlgTrajectoryDressUp::TaskDlgTrajectoryDressUp(Robot::TrajectoryDressUpObject* obj)
    : TaskDialog()
    , pcObject(obj)
{
    param = new TaskTrajectoryDressUpParameter(obj);

    Content.push_back(param);
    if (obj && obj->getDocument()) {
        setDocumentName(obj->getDocument()->getName());
        setAutoCloseOnDeletedDocument(true);
    }
}

//==== calls from the TaskView ===============================================================


void TaskDlgTrajectoryDressUp::open()
{
    RobotGui::OperationSupport::ensureEditTransaction(
        *pcObject,
        QT_TRANSLATE_NOOP("Command", "Edit trajectory modifier")
    );
}

void TaskDlgTrajectoryDressUp::clicked(int button)
{
    if (QDialogButtonBox::Apply == button) {
        // transfer the values to the object
        param->writeValues();
        // May throw an exception which we must handle here
        pcObject->recomputeFeature();
    }
}

bool TaskDlgTrajectoryDressUp::accept()
{
    try {
        if (!RobotGui::OperationSupport::isUsableObject(pcObject->Source.getValue())) {
            throw Base::RuntimeError("The source trajectory is suppressed or no longer available");
        }
        param->writeValues();
        if (!pcObject->recomputeFeature() || pcObject->isError()) {
            throw Base::RuntimeError(pcObject->getStatusString());
        }
        if (!RobotGui::OperationSupport::resetEdit(*pcObject)) {
            throw Base::RuntimeError("The trajectory modifier task could not be finalized");
        }
        return true;
    }
    catch (const Base::Exception& error) {
        Base::Console().warning("TaskDlgTrajectoryDressUp::accept(): %s\n", error.what());
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Trajectory Modifier"),
            QString::fromUtf8(error.what())
        );
        return false;
    }
}

bool TaskDlgTrajectoryDressUp::reject()
{
    return RobotGui::OperationSupport::resetEdit(*pcObject);
}

void TaskDlgTrajectoryDressUp::helpRequested()
{}


#include "moc_TaskDlgTrajectoryDressUp.cpp"
