# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI contracts for Design-global Hole authoring and thread presentation."""

from pathlib import Path
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign
import Sketcher  # noqa: F401 - registers global sketch objects
from PySide import QtGui
from pivy import coin


class TestDesignHoleGui(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("DesignHoleGui")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self.temporary_directory = tempfile.TemporaryDirectory(
            prefix="vibecad-design-hole-gui-"
        )
        self._process_events()

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
        if self.document is not None:
            document = App.getDocument(self.document.Name)
            if document is not None:
                App.closeDocument(document.Name)
        self.temporary_directory.cleanup()
        self._process_events()

    @staticmethod
    def _process_events():
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()

    @staticmethod
    def _thread_texture_count(body):
        search = coin.SoSearchAction()
        search.setType(coin.SoTexture2.getClassTypeId())
        search.setInterest(coin.SoSearchAction.ALL)
        search.setSearchingAll(True)
        search.apply(body.ViewObject.RootNode)
        paths = search.getPaths()
        if paths is None:
            return 0
        return sum(
            1
            for index in range(paths.getLength())
            if str(paths[index].getTail().filename.getValue()).endswith(
                "ThreadOverlay.png"
            )
        )

    def _component_body(self, name, x_offset):
        component = self.document.addObject(
            "PartDesign::Component",
            f"{name}Component",
        )
        component.Placement.Base.x = x_offset
        body = self.document.addObject("PartDesign::Body", f"{name}Body")
        component.addObject(body)
        initial = body.newObject(
            "PartDesign::Feature",
            f"{name}ImportedState",
        )
        initial.Shape = Part.makeBox(10, 10, 10)
        return component, body

    def _cosmetic_hole(self):
        first_component, first_body = self._component_body("First", 0)
        _, second_body = self._component_body("Second", 15)
        sketch = self._hole_sketch()

        self.document.openTransaction("Create Shared Threaded Hole")
        hole = self.document.addObject(
            "PartDesign::DesignHole",
            "SharedThreadedHole",
        )
        edit = PartDesign.beginDesignOperationEdit(hole)
        hole.Profile = sketch
        hole.Depth = 10
        hole.DepthType = "Dimension"
        hole.DrillPoint = "Flat"
        hole.ThreadType = "ISOMetricProfile"
        hole.ThreadSize = "M6x1.0"
        hole.Threaded = True
        hole.ModelThread = False
        hole.CosmeticThread = True
        PartDesign.setDesignOperationTargets(
            edit,
            "Cut",
            [first_body, second_body],
        )
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        self.document.recompute()
        self._process_events()
        return first_component, first_body, second_body, hole

    def _hole_sketch(self, name="SharedThreadLocations"):
        sketch = self.document.addObject(
            "Sketcher::SketchObject",
            name,
        )
        sketch.Placement.Base.z = 10
        for x in (5, 20):
            sketch.addGeometry(
                Part.Circle(
                    App.Vector(x, 5, 0),
                    App.Vector(0, 0, 1),
                    1,
                ),
                False,
            )
        self.document.recompute()
        PartDesign.finalizeDesignDefinition(sketch)
        return sketch

    def _run_hole_command(self, sketch, bodies):
        import PartGui

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch)
        for body in bodies:
            Gui.Selection.addSelection(body)
        self._process_events()
        self.assertTrue(
            Gui.isCommandActive("PartDesign_Hole"),
            {
                "selection": [
                    selected.Object.Name
                    for selected in Gui.Selection.getSelectionEx()
                ],
                "active": {
                    obj.Name: PartGui.isModelingObjectActive(obj)
                    for obj in [sketch, *bodies]
                },
                "transaction": self.document.HasPendingTransaction,
            },
        )
        Gui.runCommand("PartDesign_Hole", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        hole = self.document.ActiveObject
        self.assertEqual(hole.TypeId, "PartDesign::DesignHole")
        self.assertIs(hole.Profile[0], sketch)
        self.assertEqual(hole.ResultOperation, "Cut")
        self.assertEqual(
            set(hole.InputBodyIds),
            {body.VibeCADBodyId for body in bodies},
        )
        return hole

    def _finish_task_dialog(self, button):
        self.assertTrue(Gui.Control.activeDialog())
        for button_box in Gui.getMainWindow().findChildren(
            QtGui.QDialogButtonBox
        ):
            if not button_box.isVisible():
                continue
            task_button = button_box.button(button)
            if task_button is not None and task_button.isEnabled():
                task_button.click()
                self._process_events()
                self.assertFalse(Gui.Control.activeDialog())
                return
        self.fail("Active Hole task dialog has no enabled action button")

    def test_native_command_accepts_global_sketch_and_exact_bodies(self):
        import PartGui

        _, first_body = self._component_body("CommandFirst", 0)
        _, second_body = self._component_body("CommandSecond", 15)
        sketch = self._hole_sketch("CommandHoleLocations")

        hole = self._run_hole_command(
            sketch,
            [first_body, second_body],
        )
        self._finish_task_dialog(QtGui.QDialogButtonBox.Ok)

        self.assertIn(hole, self.document.Objects)
        self.assertTrue(hole.isValid(), hole.getStatusString())
        self.assertEqual(
            set(hole.OutputBodyIds),
            {
                first_body.VibeCADBodyId,
                second_body.VibeCADBodyId,
            },
        )
        self.assertIsNotNone(first_body.Tip)
        self.assertIsNotNone(second_body.Tip)
        first_state = PartGui.resolveModelingObject(first_body)
        second_state = PartGui.resolveModelingObject(second_body)
        timeline = next(
            (
                obj
                for obj in self.document.Objects
                if obj.TypeId == "App::DocumentTimeline"
            ),
            None,
        )
        state_diagnostic = {
            "first": first_state.TypeId,
            "second": second_state.TypeId,
            "timeline_position": timeline.Position if timeline else None,
            "timeline_operations": [
                obj.Name for obj in timeline.Operations
            ]
            if timeline
            else [],
            "hole_states": [
                obj.Name
                for obj in self.document.Objects
                if obj.TypeId == "PartDesign::DesignBodyState"
                and obj.Operation is hole
            ],
        }
        self.assertEqual(
            first_state.TypeId,
            "PartDesign::DesignBodyState",
            state_diagnostic,
        )
        self.assertEqual(
            second_state.TypeId,
            "PartDesign::DesignBodyState",
            state_diagnostic,
        )
        self.assertEqual(
            first_state.Operation,
            hole,
        )
        self.assertEqual(
            second_state.Operation,
            hole,
        )

    def test_native_command_cancel_restores_document_exactly(self):
        _, first_body = self._component_body("CancelFirst", 0)
        _, second_body = self._component_body("CancelSecond", 15)
        sketch = self._hole_sketch("CancelledHoleLocations")
        original_names = tuple(obj.Name for obj in self.document.Objects)
        original_first_tip = first_body.Tip
        original_second_tip = second_body.Tip

        self._run_hole_command(sketch, [first_body, second_body])
        self._finish_task_dialog(QtGui.QDialogButtonBox.Cancel)

        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertIs(first_body.Tip, original_first_tip)
        self.assertIs(second_body.Tip, original_second_tip)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_cosmetic_threads_follow_each_target_body_and_reopen(self):
        component, first_body, second_body, hole = self._cosmetic_hole()

        self.assertGreater(self._thread_texture_count(first_body), 0)
        self.assertGreater(self._thread_texture_count(second_body), 0)

        first_body.ViewObject.Visibility = False
        self._process_events()
        self.assertEqual(self._thread_texture_count(first_body), 0)
        self.assertGreater(self._thread_texture_count(second_body), 0)

        first_body.ViewObject.Visibility = True
        component.Placement.Base.x = 30
        self.document.recompute()
        self._process_events()
        self.assertTrue(
            hole.isValid(),
            (
                f"{hole.getStatusString()}; placement={hole.Placement}; "
                f"frames={hole.OutputFrames}; "
                f"cutter_bounds={hole.AddSubShape.BoundBox}"
            ),
        )
        self.assertGreater(self._thread_texture_count(first_body), 0)

        hole.ModelThread = True
        self.document.recompute()
        self._process_events()
        self.assertTrue(
            hole.isValid(),
            (
                f"{hole.getStatusString()}; placement={hole.Placement}; "
                f"frames={hole.OutputFrames}; "
                f"cutter_bounds={hole.AddSubShape.BoundBox}"
            ),
        )
        self.assertEqual(self._thread_texture_count(first_body), 0)
        self.assertEqual(self._thread_texture_count(second_body), 0)

        hole.ModelThread = False
        hole.CosmeticThread = True
        hole.Suppressed = True
        self.document.recompute()
        self._process_events()
        self.assertEqual(self._thread_texture_count(first_body), 0)
        self.assertEqual(self._thread_texture_count(second_body), 0)

        hole.Suppressed = False
        self.document.recompute()
        self._process_events()
        self.assertTrue(hole.isValid(), hole.getStatusString())
        self.assertGreater(self._thread_texture_count(first_body), 0)
        self.assertGreater(self._thread_texture_count(second_body), 0)

        path = (
            Path(self.temporary_directory.name)
            / "DesignHoleGui.FCStd"
        )
        self.document.saveAs(str(path))
        App.closeDocument(self.document.Name)
        self.document = App.openDocument(str(path))
        self.document.recompute()
        self._process_events()

        reopened_hole = self.document.getObject("SharedThreadedHole")
        reopened_first = self.document.getObject("FirstBody")
        reopened_second = self.document.getObject("SecondBody")
        self.assertTrue(
            reopened_hole.isValid(),
            reopened_hole.getStatusString(),
        )
        self.assertGreater(self._thread_texture_count(reopened_first), 0)
        self.assertGreater(self._thread_texture_count(reopened_second), 0)
