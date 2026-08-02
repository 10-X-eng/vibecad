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

#include <QApplication>
#include <QMessageBox>

#include <algorithm>
#include <iterator>
#include <ranges>

#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Document.h>
#include <Gui/MainWindow.h>
#include <Gui/TaskView/TaskSelectLinkProperty.h>

#include "OperationSupport.h"
#include "TaskDlgTrajectoryCompound.h"


using namespace RobotGui;

//**************************************************************************
//**************************************************************************
// TaskDialog
//++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++++

TaskDlgTrajectoryCompound::TaskDlgTrajectoryCompound(Robot::TrajectoryCompound* obj)
    : TaskDialog()
    , TrajectoryCompound(obj)
{
    select = new Gui::TaskView::TaskSelectLinkProperty(
        "SELECT Robot::TrajectoryObject COUNT 1..",
        &(obj->Source)
    );

    Content.push_back(select);
    if (obj && obj->getDocument()) {
        setDocumentName(obj->getDocument()->getName());
        setAutoCloseOnDeletedDocument(true);
    }
}

//==== calls from the TaskView ===============================================================


void TaskDlgTrajectoryCompound::open()
{
    RobotGui::OperationSupport::ensureEditTransaction(
        *TrajectoryCompound,
        QT_TRANSLATE_NOOP("Command", "Edit trajectory sequence")
    );
    select->activate();
}


bool TaskDlgTrajectoryCompound::accept()
{
    try {
        if (select->isSelectionValid()) {
            select->accept();
            const auto sources = TrajectoryCompound->Source.getValues();
            if (sources.empty()
                || std::ranges::any_of(sources, [this](const App::DocumentObject* source) {
                       return source == TrajectoryCompound
                           || !RobotGui::OperationSupport::isUsableObject(source);
                   })) {
                throw Base::RuntimeError("Choose one or more usable source trajectories");
            }
            std::vector<App::DocumentObject*> localSources;
            std::ranges::copy_if(
                sources,
                std::back_inserter(localSources),
                [this](const App::DocumentObject* source) {
                    return source->getDocument() == TrajectoryCompound->getDocument();
                }
            );
            RobotGui::OperationSupport::setReplacedInputs(*TrajectoryCompound, localSources);
            if (!TrajectoryCompound->recomputeFeature() || TrajectoryCompound->isError()
                || TrajectoryCompound->Trajectory.getValue().getSize() == 0) {
                throw Base::RuntimeError(TrajectoryCompound->getStatusString());
            }
            if (!RobotGui::OperationSupport::resetEdit(*TrajectoryCompound)) {
                throw Base::RuntimeError("The trajectory sequence task could not be finalized");
            }
            return true;
        }
        QApplication::beep();
    }
    catch (const Base::Exception& error) {
        Base::Console().warning("TaskDlgTrajectoryCompound::accept(): %s\n", error.what());
        QMessageBox::warning(
            Gui::getMainWindow(),
            tr("Trajectory Sequence"),
            QString::fromUtf8(error.what())
        );
    }
    return false;
}

bool TaskDlgTrajectoryCompound::reject()
{
    select->reject();
    return RobotGui::OperationSupport::resetEdit(*TrajectoryCompound);
}

void TaskDlgTrajectoryCompound::helpRequested()
{}


#include "moc_TaskDlgTrajectoryCompound.cpp"
