// SPDX-License-Identifier: LGPL-2.1-or-later

/***************************************************************************
 *   Copyright (c) 2008 Werner Mayer <wmayer[at]users.sourceforge.net>     *
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


#include <Gui/Application.h>
#include <Gui/Command.h>
#include <Gui/Control.h>
#include <Mod/Sketcher/Gui/Workbench.h>

#include "Workbench.h"
#include "WorkflowManager.h"

using namespace PartDesignGui;
namespace sp = std::placeholders;

#if 0  // needed for Qt's lupdate utility
    qApp->translate("Workbench", "&Sketch");
    //
    qApp->translate("Workbench", "&Part Design");
    qApp->translate("Workbench", "Datums");
    qApp->translate("Workbench", "Additive Features");
    qApp->translate("Workbench", "Subtractive Features");
    qApp->translate("Workbench", "Dress-Up Features");
    qApp->translate("Workbench", "Transformation Features");
    qApp->translate("Workbench", "Sprocket…");
    qApp->translate("Workbench", "Involute Gear");

    qApp->translate("Workbench", "Shaft Design Wizard");
    qApp->translate("Gui::TaskView::TaskWatcherCommands", "Face Tools");
    qApp->translate("Gui::TaskView::TaskWatcherCommands", "Edge Tools");
    qApp->translate("Gui::TaskView::TaskWatcherCommands", "Boolean Tools");
    qApp->translate("Gui::TaskView::TaskWatcherCommands", "Helper Tools");
    qApp->translate("Gui::TaskView::TaskWatcherCommands", "Modeling Tools");
    qApp->translate("Gui::TaskView::TaskWatcherCommands", "Create Geometry");
    //
    qApp->translate("Workbench", "Measure");
    qApp->translate("Workbench", "Refresh");
    qApp->translate("Workbench", "Toggle 3D");
    qApp->translate("Workbench", "Part Design Helper");
    qApp->translate("Workbench", "Part Design Modeling");
    qApp->translate("Workbench", "Add Material");
    qApp->translate("Workbench", "Remove Material");
    qApp->translate("Workbench", "Transform Features");
    qApp->translate("Workbench", "Finish Shape");
    qApp->translate("Workbench", "Reference Geometry");
    qApp->translate("Workbench", "Standalone and Surface Geometry");
    qApp->translate("Workbench", "Convert and Repair");
    qApp->translate("Workbench", "Copy");
    qApp->translate("Workbench", "Boolean");
    qApp->translate("Workbench", "Join");
    qApp->translate("Workbench", "Split");
    qApp->translate("Workbench", "Compound");
    qApp->translate("Workbench", "Inspect and Appearance");
    qApp->translate("Workbench", "Part Design Helper Features");
    qApp->translate("Workbench", "Standard Components");
    qApp->translate("Workbench", "Create and Remove Material");
    qApp->translate("Workbench", "Boolean, Split, and Repair");
#endif

/// @namespace PartDesignGui @class Workbench
TYPESYSTEM_SOURCE(PartDesignGui::Workbench, Gui::StdWorkbench)

Workbench::Workbench() = default;

Workbench::~Workbench()
{
    WorkflowManager::destruct();
}

void Workbench::setupContextMenu(const char* recipient, Gui::MenuItem* item) const
{
    // VibeCAD's Design graph has one global operation order.  Moving a
    // feature between Body groups or changing a Body Tip rewrites the legacy
    // ownership graph and can create dependencies which the Design graph
    // cannot represent.  Keep the compatibility commands registered for old
    // documents and macros, but never advertise those mutations from the
    // shipped authoring surface.
    Gui::StdWorkbench::setupContextMenu(recipient, item);
}

void Workbench::activated()
{
    Gui::Workbench::activated();

    WorkflowManager::init();

    std::vector<Gui::TaskView::TaskWatcher*> Watcher;

    const char* Vertex[] = {
        "PartDesign_Point",
        "PartDesign_Line",
        "PartDesign_Plane",
        "PartDesign_CoordinateSystem",
        nullptr
    };
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT Part::Feature SUBELEMENT Vertex COUNT 1..",
        Vertex,
        "Datum objects",
        "PartDesign_CoordinateSystem"
    ));

    const char* Edge[] = {
        "PartDesign_Fillet",
        "PartDesign_Chamfer",
        "PartDesign_Point",
        "PartDesign_Line",
        "PartDesign_Plane",
        "PartDesign_CoordinateSystem",
        nullptr
    };
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT Part::Feature SUBELEMENT Edge COUNT 1..",
        Edge,
        "Edge Tools",
        "PartDesign_CoordinateSystem"
    ));

    const char* Face[] = {
        "Sketcher_NewSketch",
        "PartDesign_Fillet",
        "PartDesign_Chamfer",
        "PartDesign_Draft",
        "PartDesign_Thickness",
        "PartDesign_Point",
        "PartDesign_Line",
        "PartDesign_Plane",
        "PartDesign_CoordinateSystem",
        nullptr
    };
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT Part::Feature SUBELEMENT Face COUNT 1",
        Face,
        "Face Tools",
        "PartDesign_CoordinateSystem"
    ));

    const char* Body[] = {"Sketcher_NewSketch", nullptr};
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT PartDesign::Body COUNT 1",
        Body,
        "Helper Tools",
        "PartDesign_Body"
    ));

    const char* Body2[] = {"PartDesign_Scale", "PartDesign_Combine", nullptr};
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT PartDesign::Body COUNT 1..",
        Body2,
        "Boolean Tools",
        "PartDesign_Body"
    ));

    const char* Plane1[] = {
        "Sketcher_NewSketch",
        "PartDesign_Point",
        "PartDesign_Line",
        "PartDesign_Plane",
        "PartDesign_CoordinateSystem",
        nullptr
    };
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT App::Plane COUNT 1",
        Plane1,
        "Helper Tools",
        "PartDesign_CoordinateSystem"
    ));

    const char* Plane2[] = {
        "Sketcher_NewSketch",
        "PartDesign_Point",
        "PartDesign_Line",
        "PartDesign_Plane",
        "PartDesign_CoordinateSystem",
        nullptr
    };
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT PartDesign::Plane COUNT 1",
        Plane2,
        "Helper Tools",
        "PartDesign_CoordinateSystem"
    ));

    const char* Line[] = {"PartDesign_Point", "PartDesign_Line", "PartDesign_Plane", nullptr};
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT PartDesign::Line COUNT 1",
        Line,
        "Datum objects",
        "PartDesign_CoordinateSystem"
    ));

    const char* Point[] = {
        "PartDesign_Point",
        "PartDesign_Line",
        "PartDesign_Plane",
        "PartDesign_CoordinateSystem",
        nullptr
    };
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT PartDesign::Point COUNT 1",
        Point,
        "Datum objects",
        "PartDesign_CoordinateSystem"
    ));

    const char* NoSel[]
        = {"PartDesign_NewComponent", "PartDesign_NewBody", "Sketcher_NewSketch", nullptr};
    Watcher.push_back(
        new Gui::TaskView::TaskWatcherCommandsEmptySelection(NoSel, "Start Design", "PartDesign_Body")
    );

    const char* Faces[] = {
        "PartDesign_Fillet",
        "PartDesign_Chamfer",
        "PartDesign_Draft",
        "PartDesign_Thickness",
        nullptr
    };
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT Part::Feature SUBELEMENT Face COUNT 2..",
        Faces,
        "Face Tools",
        "PartDesign_Body"
    ));

    const char* Sketch[] = {
        "Sketcher_EditSketch",
        "PartDesign_DesignExtrude",
        "PartDesign_Hole",
        "PartDesign_DesignRevolve",
        "PartDesign_DesignHelix",
        nullptr
    };
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT Sketcher::SketchObject COUNT 1",
        Sketch,
        "Modeling Tools",
        "PartDesign_Body"
    ));

    const char* Sketches[] = {"PartDesign_DesignLoft", "PartDesign_DesignSweep", nullptr};
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT Sketcher::SketchObject COUNT 2..",
        Sketches,
        "Modeling tools",
        "PartDesign_Body"
    ));

    const char* Transformed[] = {
        "PartDesign_DesignMirror",
        "PartDesign_DesignLinearPattern",
        "PartDesign_DesignCircularPattern",
        nullptr
    };
    Watcher.push_back(new Gui::TaskView::TaskWatcherCommands(
        "SELECT PartDesign::Feature COUNT 1",
        Transformed,
        "Pattern Tools",
        "PartDesign_LinearPattern"
    ));

    addTaskWatcher(Watcher);
    if (App::GetApplication()
            .GetUserParameter()
            .GetGroup("BaseApp")
            ->GetGroup("Preferences")
            ->GetGroup("Mod/PartDesign")
            ->GetBool("SwitchToTask", true)) {
        Gui::Control().showTaskView();
    }
}

void Workbench::deactivated()
{
    removeTaskWatcher();
    // reset the active Body
    Gui::Command::doCommand(Gui::Command::Doc, "import PartDesignGui");

    Gui::Workbench::deactivated();
}

Gui::MenuItem* Workbench::setupMenuBar() const
{
    Gui::MenuItem* root = StdWorkbench::setupMenuBar();
    Gui::MenuItem* item = root->findItem("&Windows");

    // add another top level menu left besides the Part Design menu for the Sketcher commands
    Gui::MenuItem* sketch = new Gui::MenuItem;
    root->insertItem(item, sketch);
    sketch->setCommand("&Sketch");

    *sketch << "Sketcher_NewSketch" << "Sketcher_EditSketch" << "Sketcher_MapSketch"
            << "Sketcher_ReorientSketch" << "Sketcher_ValidateSketch" << "Sketcher_MergeSketches"
            << "Sketcher_MirrorSketch";

    Gui::MenuItem* model = new Gui::MenuItem;
    root->insertItem(item, model);
    model->setCommand("&Part Design");

    Gui::MenuItem* additives = new Gui::MenuItem;
    additives->setCommand("Add Material");

    *additives << "PartDesign_DesignExtrude" << "PartDesign_DesignRevolve"
               << "PartDesign_DesignLoft" << "PartDesign_DesignSweep" << "PartDesign_DesignHelix";

    Gui::MenuItem* subtractives = new Gui::MenuItem;
    subtractives->setCommand("Remove Material");

    *subtractives << "PartDesign_Hole";

    Gui::MenuItem* transformations = new Gui::MenuItem;
    transformations->setCommand("Transform Features");

    *transformations << "PartDesign_Scale" << "PartDesign_DesignMirror"
                     << "PartDesign_DesignLinearPattern" << "PartDesign_DesignCircularPattern";

    Gui::MenuItem* dressups = new Gui::MenuItem;
    dressups->setCommand("Finish Shape");

    *dressups << "PartDesign_Fillet"
              << "PartDesign_Chamfer"
              << "PartDesign_Draft"
              << "PartDesign_Thickness";

    Gui::MenuItem* datums = new Gui::MenuItem;
    datums->setCommand("Reference Geometry");
    *datums << "PartDesign_Point"
            << "PartDesign_Line"
            << "PartDesign_Plane"
            << "PartDesign_CoordinateSystem";

    Gui::MenuItem* standardComponents = new Gui::MenuItem;
    standardComponents->setCommand("Standard Components");
    *standardComponents << "VibeCAD_InsertStandardFastener"
                        << "VibeCAD_EditStandardFastener"
                        << "VibeCAD_CreateMatchingFastenerHole"
                        << "VibeCAD_AttachStandardFastener";

    // Part Design owns the stronger solid-feature implementations.  General
    // Part commands remain here only for capabilities that do not have a Part
    // Design equivalent: standalone/surface construction, copies, repair,
    // split/join, and explicit BREP booleans.
    Gui::MenuItem* generalGeometry = new Gui::MenuItem;
    generalGeometry->setCommand("Standalone and Surface Geometry");
    *generalGeometry << "Part_Primitives" << "Part_Builder" << "Separator" << "Part_Extrude"
                     << "Part_Revolve" << "Part_Mirror" << "Part_MakeFace"
                     << "Part_RuledSurface" << "Part_Loft" << "Part_Sweep" << "Part_Section"
                     << "Part_CrossSections" << "Part_Offset" << "Part_Offset2D"
                     << "Part_ProjectionOnSurface" << "Std_ToggleClipPlane";

    Gui::MenuItem* conversions = new Gui::MenuItem;
    conversions->setCommand("Convert and Repair");
    *conversions << "Part_ShapeFromMesh"
                 << "Part_PointsFromMesh"
                 << "Part_MakeSolid"
                 << "Part_ReverseShape"
                 << "Part_RefineShape"
                 << "Part_Defeaturing";

    Gui::MenuItem* copies = new Gui::MenuItem;
    copies->setCommand("Copy");
    *copies << "Part_SimpleCopy" << "Part_TransformedCopy" << "Part_ElementCopy";

    Gui::MenuItem* booleans = new Gui::MenuItem;
    booleans->setCommand("Combine");
    *booleans << "PartDesign_Combine";

    Gui::MenuItem* joins = new Gui::MenuItem;
    joins->setCommand("Join");
    *joins << "Part_JoinConnect"
           << "Part_JoinEmbed"
           << "Part_JoinCutout";

    Gui::MenuItem* splits = new Gui::MenuItem;
    splits->setCommand("Split");
    *splits << "PartDesign_Split";

    Gui::MenuItem* compounds = new Gui::MenuItem;
    compounds->setCommand("Compound");
    *compounds << "Part_Compound" << "PartDesign_Separate" << "Part_CompoundFilter"
               << "Part_ToleranceSet";

    Gui::MenuItem* inspection = new Gui::MenuItem;
    inspection->setCommand("Inspect and Appearance");
    *inspection << "Part_ColorPerFace"
                << "Part_CheckGeometry"
                << "Materials_InspectAppearance"
                << "Materials_InspectMaterial";

    *model << "PartDesign_NewComponent" << "PartDesign_NewBody" << "Sketcher_NewSketch" << datums
           << "PartDesign_SubShapeBinder" << "PartDesign_Clone" << standardComponents << "Separator"
           << additives << "PartDesign_DesignPrimitive" << "Separator" << subtractives
           << "Separator" << dressups << transformations << "Separator" << booleans << joins
           << splits << compounds << "Separator" << generalGeometry << conversions << copies
           << "Separator" << "Part_BoxSelection" << "Part_EditAttachment" << inspection
           << "Separator" << "PartDesign_InvoluteGear" << "PartDesign_Sprocket";

    if (Gui::Application::Instance->commandManager().getCommandByName("PartDesign_WizardShaft")) {
        *model << "Separator" << "PartDesign_WizardShaft";
    }

    Gui::MenuItem* view = root->findItem("&View");
    if (view) {
        Gui::MenuItem* appr = view->findItem("Std_RandomColor");
        appr = view->afterItem(appr);
        Gui::MenuItem* face = new Gui::MenuItem();
        face->setCommand("Part_ColorPerFace");
        view->insertItem(appr, face);
    }

    // Replace the "Duplicate selection" menu item with a replacement that is compatible with Body
    item = root->findItem("&Edit");
    Gui::MenuItem* dup = item->findItem("Std_DuplicateSelection");
    dup->setCommand("PartDesign_DuplicateSelection");

    return root;
}

Gui::ToolBarItem* Workbench::setupToolBars() const
{
    Gui::ToolBarItem* root = StdWorkbench::setupToolBars();
    Gui::ToolBarItem* part = new Gui::ToolBarItem(root);
    part->setCommand("Part Design Helper Features");

    *part << "PartDesign_NewComponent" << "PartDesign_NewBody" << "Sketcher_NewSketch"
          << "Sketcher_EditSketch" << "Sketcher_ValidateSketch" << "Part_CheckGeometry"
          << "PartDesign_SubShapeBinder" << "PartDesign_Clone";

    part = new Gui::ToolBarItem(root);
    part->setCommand("Create and Remove Material");

    *part << "PartDesign_DesignExtrude" << "PartDesign_DesignRevolve" << "PartDesign_DesignLoft"
          << "PartDesign_DesignSweep" << "PartDesign_DesignHelix" << "PartDesign_DesignPrimitive"
          << "Separator" << "PartDesign_Hole";

    part = new Gui::ToolBarItem(root);

    part->setCommand("Finish Shape");
    *part << "PartDesign_Fillet"
          << "PartDesign_Chamfer"
          << "PartDesign_Draft"
          << "PartDesign_Thickness";

    part = new Gui::ToolBarItem(root);
    part->setCommand("Transform Features");

    *part << "PartDesign_Scale" << "PartDesign_DesignMirror"
          << "PartDesign_DesignLinearPattern" << "PartDesign_DesignCircularPattern";

    part = new Gui::ToolBarItem(root);
    part->setCommand("Standalone and Surface Geometry");
    *part << "Part_Primitives" << "Part_Builder" << "Separator" << "Part_Extrude" << "Part_Revolve"
          << "Part_Mirror" << "Part_MakeFace" << "Part_RuledSurface" << "Part_Loft"
          << "Part_Sweep" << "Part_Section" << "Part_CrossSections" << "Part_CompOffset"
          << "Part_ProjectionOnSurface";

    part = new Gui::ToolBarItem(root);
    part->setCommand("Boolean, Split, and Repair");
    *part << "Part_Compound" << "PartDesign_Separate" << "Part_CompoundFilter" << "PartDesign_Combine"
          << "Part_CompJoinFeatures" << "PartDesign_Split" << "Part_Defeaturing";

    part = new Gui::ToolBarItem(root);
    part->setCommand("Standard Components");
    *part << "VibeCAD_InsertStandardFastener"
          << "VibeCAD_EditStandardFastener"
          << "VibeCAD_CreateMatchingFastenerHole"
          << "VibeCAD_AttachStandardFastener";

    return root;
}
