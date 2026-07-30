// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2023 David Friedli <david[at]friedli-be.ch>             *
 *                                                                         *
 *   This file is part of FreeCAD.                                         *
 *                                                                         *
 *   FreeCAD is free software: you can redistribute it and/or modify it    *
 *   under the terms of the GNU Lesser General Public License as           *
 *   published by the Free Software Foundation, either version 2.1 of the  *
 *   License, or (at your option) any later version.                       *
 *                                                                         *
 *   FreeCAD is distributed in the hope that it will be useful, but        *
 *   WITHOUT ANY WARRANTY; without even the implied warranty of            *
 *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU      *
 *   Lesser General Public License for more details.                       *
 *                                                                         *
 *   You should have received a copy of the GNU Lesser General Public      *
 *   License along with FreeCAD. If not, see                               *
 *   <https://www.gnu.org/licenses/>.                                      *
 *                                                                         *
 **************************************************************************/

#include <QApplication>

#include <exception>
#include <memory>

#include <App/Application.h>
#include <App/Datums.h>
#include <App/Document.h>
#include <Base/Console.h>
#include <Base/Exception.h>
#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Gui/MainWindow.h>
#include <Gui/MDIView.h>
#include <Gui/View3DInventor.h>
#include <Gui/View3DInventorViewer.h>
#include <Mod/Part/App/PartFeature.h>
#include <Mod/Part/Gui/ModelingSelection.h>
#include <TopoDS_Shape.hxx>

#include "TaskMeasure.h"
#include "TaskMassProperties.h"
#include "TimelineSelection.h"

namespace
{
bool hasMeasurableObject(App::Document* document)
{
    if (!document) {
        return false;
    }
    try {
        for (auto* rawObject : document->getObjects()) {
            if (!MeasureGui::isTimelineSelectionActive(rawObject)) {
                continue;
            }
            auto* object = PartGui::resolveModelingObject(rawObject);
            if (!object
                || !MeasureGui::isTimelineSelectionActive(object)) {
                continue;
            }

            // A Body's built-in origin axes, planes and point are reference
            // geometry, not user-created model content. Their non-null Shapes
            // must not make an otherwise empty document appear measurable.
            if (auto* datum = freecad_cast<App::DatumElement*>(object);
                datum && datum->isOriginFeature()) {
                continue;
            }
            if (!object->isDerivedFrom<App::GeoFeature>()) {
                continue;
            }

            TopoDS_Shape shape = Part::Feature::getShape(
                object,
                Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
            );
            if (!shape.IsNull()) {
                return true;
            }

            std::string module = Base::Type::getModuleName(object->getTypeId().getName());
            if (App::MeasureManager::hasMeasureHandler(module.c_str())) {
                return true;
            }
        }
    }
    catch (...) {
        // Command-state queries run while documents are being recomputed,
        // restored, and closed. A transiently invalid link must disable the
        // action for this update instead of escaping through Qt.
        return false;
    }
    return false;
}

template<typename Task>
void showExactMeasureTask(App::Document& document)
{
    auto task = std::make_unique<Task>();
    auto* taskPointer = task.get();
    taskPointer->setDocumentName(document.getName());
    Gui::Control().showDialog(taskPointer, &document);
    if (Gui::Control().activeDialog(&document) == taskPointer) {
        task.release();
    }
}
}  // namespace

//===========================================================================
// Std_Measure
// this is the Unified Measurement Facility Measure command
//===========================================================================


DEF_STD_CMD_A(StdCmdMeasure)

StdCmdMeasure::StdCmdMeasure()
    : Command("Std_Measure")
{
    sGroup = "Measure";
    sMenuText = QT_TR_NOOP("&Measure");
    sToolTipText = QT_TR_NOOP("Measures a feature");
    sWhatsThis = "Std_Measure";
    sStatusTip = QT_TR_NOOP("Measures a feature");
    sPixmap = "umf-measurement";
}

void StdCmdMeasure::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    auto* document = App::GetApplication().getActiveDocument();
    if (!isActive() || !document) {
        return;
    }
    try {
        showExactMeasureTask<MeasureGui::TaskMeasure>(*document);
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not start measurement: %s\n",
            error.what()
        );
    }
    catch (const std::exception& error) {
        Base::Console().error(
            "Could not start measurement: %s\n",
            error.what()
        );
    }
}

bool StdCmdMeasure::isActive()
{
    App::Document* doc = App::GetApplication().getActiveDocument();
    if (!doc || !PartGui::canStartRetainedModelingTask(doc)
        || !hasMeasurableObject(doc) || Gui::Control().activeDialog()) {
        return false;
    }

    Gui::MDIView* view = Gui::getMainWindow()->activeWindow();
    if (view && view->isDerivedFrom<Gui::View3DInventor>()) {
        Gui::View3DInventorViewer* viewer = static_cast<Gui::View3DInventor*>(view)->getViewer();
        return !viewer->isEditing();
    }
    return false;
}

void CreateMeasureCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    auto cmd = new StdCmdMeasure();
    cmd->initAction();
    rcCmdMgr.addCommand(cmd);
}

DEF_STD_CMD_A(StdCmdMassProperties)

StdCmdMassProperties::StdCmdMassProperties()
    : Command("Std_MassProperties")
{
    sGroup = "MassProperties";
    sMenuText = QT_TR_NOOP("Mass Properties");
    sToolTipText = QT_TR_NOOP("Calculates mass properties of selected objects");
    sWhatsThis = "Std_MassProperties";
    sStatusTip = sToolTipText;
    sPixmap = "MassPropertiesIcon";
}

void StdCmdMassProperties::activated(int iMsg)
{
    Q_UNUSED(iMsg);

    auto* document = App::GetApplication().getActiveDocument();
    if (!isActive() || !document) {
        return;
    }
    try {
        showExactMeasureTask<
            MassPropertiesGui::TaskMassProperties>(*document);
    }
    catch (const Base::Exception& error) {
        Base::Console().error(
            "Could not start mass properties: %s\n",
            error.what()
        );
    }
    catch (const std::exception& error) {
        Base::Console().error(
            "Could not start mass properties: %s\n",
            error.what()
        );
    }
}

bool StdCmdMassProperties::isActive()
{
    App::Document* doc = App::GetApplication().getActiveDocument();
    if (!doc || !PartGui::canStartRetainedModelingTask(doc)
        || Gui::Control().activeDialog()) {
        return false;
    }

    try {
        for (auto* rawObject : doc->getObjects()) {
            if (!MeasureGui::isTimelineSelectionActive(rawObject)) {
                continue;
            }
            auto* object = PartGui::resolveModelingObject(rawObject);
            if (!object
                || !MeasureGui::isTimelineSelectionActive(object)
                || object->isDerivedFrom<App::DatumElement>()) {
                continue;
            }

            TopoDS_Shape shape = Part::Feature::getShape(
                object,
                Part::ShapeOption::ResolveLink | Part::ShapeOption::Transform
            );
            if (shape.IsNull()) {
                continue;
            }

            switch (shape.ShapeType()) {
                case TopAbs_SOLID:
                case TopAbs_COMPSOLID:
                case TopAbs_SHELL:
                case TopAbs_FACE:
                case TopAbs_COMPOUND:
                    return true;
                default:
                    break;
            }
        }
    }
    catch (...) {
        return false;
    }
    return false;
}

void CreateMassPropertiesCommands()
{
    Gui::CommandManager& rcCmdMgr = Gui::Application::Instance->commandManager();

    auto cmd = new StdCmdMassProperties();
    cmd->initAction();
    rcCmdMgr.addCommand(cmd);
}
