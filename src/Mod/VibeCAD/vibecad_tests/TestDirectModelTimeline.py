# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI contracts for direct VibeCAD modeling tools in the document timeline."""

from __future__ import annotations

import os
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Body ownership support
import Sketcher  # noqa: F401 - registers native Sketch objects
from PySide import QtCore, QtGui

from VibeCADCore import VibeCADService
from tool_impl.service import part_boolean, part_extrude, partdesign_set_tip
from tool_impl.sketcher import draw_rectangle, set_construction


def _timeline(document):
    return next(
        (obj for obj in document.Objects if obj.TypeId == "App::DocumentTimeline"),
        None,
    )


def _closed_rectangle(width, height):
    points = [
        App.Vector(0, 0, 0),
        App.Vector(width, 0, 0),
        App.Vector(width, height, 0),
        App.Vector(0, height, 0),
        App.Vector(0, 0, 0),
    ]
    return Part.makePolygon(points)


def _update_gui():
    Gui.updateGui()
    Gui.updateGui()


def _timeline_button(object_name):
    for _attempt in range(100):
        button = Gui.getMainWindow().findChild(QtGui.QToolButton, object_name)
        if button is not None:
            return button
        _update_gui()
    raise AssertionError(f"Timeline button is unavailable: {object_name}")


def _move_to_end(controller):
    _timeline_button("VibeCADFeatureTimelineEnd").click()
    _update_gui()
    if controller.Position != len(controller.Operations):
        raise AssertionError("Timeline did not advance to the end")


def _move_before(controller, operation):
    _move_to_end(controller)
    operation_index = list(controller.Operations).index(operation)
    previous = _timeline_button("VibeCADFeatureTimelinePrevious")
    for _attempt in range(len(controller.Operations) + 1):
        if controller.Position <= operation_index:
            break
        previous.click()
        _update_gui()
    if controller.Position != operation_index:
        raise AssertionError(
            f"Timeline stopped at {controller.Position}, expected {operation_index}"
        )


class _Service(VibeCADService):
    def __init__(self, document):
        self.document = document

    def _active_document(self):
        return self.document

    def _get_partdesign_body(self, name):
        body = self.document.getObject(name)
        return body if getattr(body, "TypeId", "") == "PartDesign::Body" else None

    def _partdesign_body_summary(self, body):
        if body is None:
            return None
        tip = getattr(body, "Tip", None)
        return {
            "name": body.Name,
            "tip": getattr(tip, "Name", None),
            "features": [item.Name for item in list(body.Group)],
        }


class DirectModelTimelineTest(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("DirectModelTimeline")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self.service = _Service(self.document)
        self.saved_path = None

    def tearDown(self):
        if App.GuiUp and Gui.activeDocument() is not None:
            if Gui.Control.activeDialog():
                try:
                    Gui.Control.activeTaskDialog().reject()
                except RuntimeError:
                    pass
            if Gui.activeDocument().getInEdit() is not None:
                Gui.activeDocument().resetEdit()
        Gui.Selection.clearSelection()
        document_names = {"DirectModelTimeline"}
        if hasattr(self, "document"):
            document_names.add(self.document.Name)
        for name in document_names:
            if name in App.listDocuments():
                App.closeDocument(name)
        if self.saved_path and os.path.exists(self.saved_path):
            os.remove(self.saved_path)

    def test_extrude_tracks_exact_input_through_undo_and_reopen(self):
        profile = self.document.addObject("Part::Feature", "Profile")
        profile.Label = "Visible profile"
        profile.Shape = _closed_rectangle(8, 5)
        profile.Visibility = True
        profile_name = profile.Name
        self.document.recompute()

        response = part_extrude.run(
            self.service,
            profile_object_name=profile.Name,
            direction={"x": 0.0, "y": 0.0, "z": 1.0},
            extent={"type": "one_direction", "length_mm": 6.0},
            solid=True,
            taper_angle_degrees=0.0,
            label="Direct Extrusion",
        )
        self.assertTrue(response.get("ok"), response)
        operation_name = response["mutation"]["feature"]
        operation = self.document.getObject(operation_name)
        self.assertIsNotNone(operation)
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(operation.VibeCADTimelineReplacedInputs),
            [profile],
        )
        self.assertFalse(profile.Visibility)
        self.assertTrue(operation.Visibility)

        self.document.undo()
        _update_gui()
        self.assertIsNone(self.document.getObject(operation_name))
        self.assertTrue(profile.Visibility)
        self.document.redo()
        _update_gui()
        operation = self.document.getObject(operation_name)
        self.assertIsNotNone(operation)
        self.assertEqual(
            list(operation.VibeCADTimelineReplacedInputs),
            [profile],
        )

        controller = _timeline(self.document)
        self.assertIsNotNone(controller)
        _move_before(controller, operation)
        self.assertTrue(profile.Visibility)
        self.assertFalse(operation.Visibility)

        _move_to_end(controller)
        self.assertFalse(profile.Visibility)
        self.assertTrue(operation.Visibility)

        controller = _timeline(self.document)
        _move_before(controller, operation)
        temp = tempfile.NamedTemporaryFile(suffix=".FCStd", delete=False)
        temp.close()
        self.saved_path = temp.name
        self.document.saveAs(self.saved_path)
        App.closeDocument(self.document.Name)

        restored = App.openDocument(self.saved_path)
        self.document = restored
        Gui.activeDocument().activeView()
        _update_gui()
        restored_profile = restored.getObject(profile_name)
        restored_operation = restored.getObject(operation_name)
        restored_controller = _timeline(restored)
        self.assertIsNotNone(restored_controller)
        self.assertEqual(
            restored_controller.Position,
            list(restored_controller.Operations).index(restored_operation),
        )
        self.assertTrue(restored_profile.Visibility)
        self.assertFalse(restored_operation.Visibility)
        self.assertEqual(
            list(restored_operation.VibeCADTimelineReplacedInputs),
            [restored_profile],
        )

        _move_to_end(restored_controller)
        self.assertFalse(restored_profile.Visibility)
        self.assertTrue(restored_operation.Visibility)

    def test_boolean_does_not_restore_an_input_that_started_hidden(self):
        base = self.document.addObject("Part::Feature", "VisibleBase")
        base.Shape = Part.makeBox(10, 10, 10)
        base.Visibility = True
        cutter = self.document.addObject("Part::Feature", "HiddenCutter")
        cutter.Shape = Part.makeCylinder(3, 12, App.Vector(5, 5, -1))
        cutter.Visibility = False
        self.document.recompute()

        response = part_boolean.run(
            self.service,
            operation="cut",
            base_object_name=base.Name,
            tool_object_names=[cutter.Name],
            refine=True,
            label="Direct Cut",
        )
        self.assertTrue(response.get("ok"), response)
        operation = self.document.getObject(response["mutation"]["feature"])
        self.assertEqual(
            list(operation.VibeCADTimelineReplacedInputs),
            [base],
        )
        self.assertFalse(base.Visibility)
        self.assertFalse(cutter.Visibility)

        controller = _timeline(self.document)
        _move_before(controller, operation)
        self.assertTrue(base.Visibility)
        self.assertFalse(cutter.Visibility)
        self.assertFalse(operation.Visibility)

        _move_to_end(controller)
        self.assertFalse(base.Visibility)
        self.assertFalse(cutter.Visibility)
        self.assertTrue(operation.Visibility)

    def test_set_tip_moves_the_document_history_boundary_without_faking_an_operation(self):
        body = self.document.addObject("PartDesign::Body", "TimelineBody")
        first = body.newObject("PartDesign::Feature", "FirstBodyResult")
        first.Shape = Part.makeBox(4, 4, 4)
        second = body.newObject("PartDesign::Feature", "SecondBodyResult")
        second.Shape = Part.makeBox(7, 5, 4)
        body.Tip = second
        body.Visibility = True
        first.Visibility = False
        second.Visibility = True
        self.document.recompute()

        controller = _timeline(self.document)
        self.assertIsNotNone(controller)
        _move_to_end(controller)
        operations_before = tuple((item.Name, int(item.ID)) for item in list(controller.Operations))
        first_boundary = list(controller.Operations).index(first) + 1

        widget = Gui.getMainWindow().findChild(
            QtGui.QWidget,
            "VibeCADFeatureTimeline",
        )
        self.assertIsNotNone(widget)
        end_position = int(controller.Position)
        self.assertFalse(
            widget.moveCurrentStateAfterOperation(
                self.document.Name,
                "not-the-live-document-uid",
                first.Name,
                int(first.ID),
            )
        )
        self.assertEqual(controller.Position, end_position)
        self.assertIs(body.Tip, second)
        self.assertFalse(
            widget.moveCurrentStateAfterOperation(
                self.document.Name,
                str(self.document.Uid),
                first.Name,
                int(first.ID) + 10_000,
            )
        )
        self.assertEqual(controller.Position, end_position)
        self.assertIs(body.Tip, second)

        response = partdesign_set_tip.run(
            self.service,
            body_name=body.Name,
            feature_name=first.Name,
        )
        self.assertTrue(response.get("ok"), response)
        self.assertEqual(controller.Position, first_boundary)
        self.assertIs(body.Tip, first)
        self.assertTrue(first.Visibility)
        self.assertFalse(second.Visibility)

        self.assertEqual(
            tuple((item.Name, int(item.ID)) for item in list(controller.Operations)),
            operations_before,
        )
        self.assertTrue(
            response["mutation"]["timeline_operations_unchanged"],
            response,
        )

        self.document.undo()
        _update_gui()
        self.assertEqual(controller.Position, len(controller.Operations))
        self.assertIs(body.Tip, second)
        self.assertFalse(first.Visibility)
        self.assertTrue(second.Visibility)

        self.document.redo()
        _update_gui()
        self.assertEqual(controller.Position, first_boundary)
        self.assertIs(body.Tip, first)
        self.assertTrue(first.Visibility)
        self.assertFalse(second.Visibility)

        temp = tempfile.NamedTemporaryFile(suffix=".FCStd", delete=False)
        temp.close()
        self.saved_path = temp.name
        body_name = body.Name
        first_name = first.Name
        second_name = second.Name
        self.document.saveAs(self.saved_path)
        App.closeDocument(self.document.Name)

        restored = App.openDocument(self.saved_path)
        self.document = restored
        self.service.document = restored
        Gui.activeDocument().activeView()
        _update_gui()
        restored_body = restored.getObject(body_name)
        restored_first = restored.getObject(first_name)
        restored_second = restored.getObject(second_name)
        restored_controller = _timeline(restored)
        self.assertEqual(restored_controller.Position, first_boundary)
        self.assertIs(restored_body.Tip, restored_first)
        self.assertTrue(restored_first.Visibility)
        self.assertFalse(restored_second.Visibility)
        self.assertEqual(
            tuple((item.Name, int(item.ID)) for item in list(restored_controller.Operations)),
            operations_before,
        )

        _move_to_end(restored_controller)
        self.assertIs(restored_body.Tip, restored_second)
        self.assertFalse(restored_first.Visibility)
        self.assertTrue(restored_second.Visibility)

    def test_structural_bodies_share_one_document_history_and_restore_marker_state(self):
        transaction = self.document.openTransaction(
            "Create document-wide structural Body history"
        )
        self.assertNotEqual(transaction, 0)

        first_body = self.document.addObject("PartDesign::Body", "FirstStructuralBody")
        self.document.classifyProvisionalTimelineInternalObject(first_body)
        first_result = first_body.newObject(
            "PartDesign::Feature",
            "FirstStructuralResult",
        )
        first_result.Shape = Part.makeBox(4, 4, 4)

        second_body = self.document.addObject(
            "PartDesign::Body",
            "SecondStructuralBody",
        )
        self.document.classifyProvisionalTimelineInternalObject(second_body)
        second_body_result = second_body.newObject(
            "PartDesign::Feature",
            "SecondBodyResult",
        )
        second_body_result.Shape = Part.makeCylinder(2, 5)

        later_first_result = first_body.newObject(
            "PartDesign::Feature",
            "LaterFirstBodyResult",
        )
        later_first_result.Shape = Part.makeBox(7, 5, 4)
        first_body.Tip = later_first_result
        second_body.Tip = second_body_result
        first_body.Visibility = True
        second_body.Visibility = True
        first_result.Visibility = False
        second_body_result.Visibility = True
        later_first_result.Visibility = True
        self.document.recompute()
        self.document.commitTransaction()

        controller = _timeline(self.document)
        self.assertIsNotNone(controller)
        operations = list(controller.Operations)
        self.assertNotIn(first_body, operations)
        self.assertNotIn(second_body, operations)
        self.assertEqual(
            [first_result, second_body_result, later_first_result],
            [
                operation
                for operation in operations
                if operation
                in {first_result, second_body_result, later_first_result}
            ],
        )

        _update_gui()
        timeline_items = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline_items)

        def visible_history_names():
            return [
                str(timeline_items.item(row).data(QtCore.Qt.UserRole) or "")
                for row in range(timeline_items.count())
                if timeline_items.item(row).data(QtCore.Qt.UserRole)
            ]

        self.assertEqual(
            [
                first_result.Name,
                second_body_result.Name,
                later_first_result.Name,
            ],
            [
                name
                for name in visible_history_names()
                if name
                in {
                    first_result.Name,
                    second_body_result.Name,
                    later_first_result.Name,
                }
            ],
        )

        Gui.activeView().setActiveObject("pdbody", second_body)
        _update_gui()
        self.assertIn(first_result.Name, visible_history_names())
        self.assertIn(second_body_result.Name, visible_history_names())
        self.assertIn(later_first_result.Name, visible_history_names())

        _move_before(controller, later_first_result)
        self.assertIs(first_body.Tip, first_result)
        self.assertIs(second_body.Tip, second_body_result)
        self.assertFalse(first_result.Suppressed)
        self.assertFalse(second_body_result.Suppressed)
        self.assertTrue(later_first_result.Suppressed)
        self.assertTrue(first_result.Visibility)
        self.assertTrue(second_body_result.Visibility)
        self.assertFalse(later_first_result.Visibility)

        saved_position = int(controller.Position)
        body_names = (first_body.Name, second_body.Name)
        result_names = (
            first_result.Name,
            second_body_result.Name,
            later_first_result.Name,
        )
        temp = tempfile.NamedTemporaryFile(suffix=".FCStd", delete=False)
        temp.close()
        self.saved_path = temp.name
        self.document.saveAs(self.saved_path)
        App.closeDocument(self.document.Name)

        restored = App.openDocument(self.saved_path)
        self.document = restored
        self.service.document = restored
        Gui.activeDocument().activeView()
        _update_gui()
        restored_controller = _timeline(restored)
        restored_first_body = restored.getObject(body_names[0])
        restored_second_body = restored.getObject(body_names[1])
        restored_first = restored.getObject(result_names[0])
        restored_second_body_result = restored.getObject(result_names[1])
        restored_later_first = restored.getObject(result_names[2])
        self.assertEqual(restored_controller.Position, saved_position)
        self.assertIs(restored_first_body.Tip, restored_first)
        self.assertIs(restored_second_body.Tip, restored_second_body_result)
        self.assertFalse(restored_first.Suppressed)
        self.assertFalse(restored_second_body_result.Suppressed)
        self.assertTrue(restored_later_first.Suppressed)

        _move_to_end(restored_controller)
        self.assertIs(restored_first_body.Tip, restored_later_first)
        self.assertIs(restored_second_body.Tip, restored_second_body_result)
        self.assertFalse(restored_first.Suppressed)
        self.assertFalse(restored_second_body_result.Suppressed)
        self.assertFalse(restored_later_first.Suppressed)

    def test_sketcher_tools_edit_one_existing_history_operation(self):
        body = self.document.addObject("PartDesign::Body", "SketchToolBody")
        sketch = body.newObject("Sketcher::SketchObject", "TrackedSketch")
        sketch.Label = "Tracked Sketch"
        self.document.recompute()

        controller = _timeline(self.document)
        self.assertIsNotNone(controller)
        _move_to_end(controller)
        operations_before = tuple(
            (item.Name, int(item.ID)) for item in list(controller.Operations)
        )
        position_before = int(controller.Position)

        Gui.activeDocument().setEdit(sketch.Name)
        _update_gui()
        self.assertIs(Gui.activeDocument().getInEdit().Object, sketch)

        drawn = draw_rectangle.run(
            self.service,
            width=18.0,
            height=9.0,
            center_x=2.0,
            center_y=-1.0,
            construction=False,
        )
        self.assertTrue(drawn.get("ok"), drawn)
        self.assertEqual(len(sketch.Geometry), 4)
        self.assertEqual(
            tuple((item.Name, int(item.ID)) for item in list(controller.Operations)),
            operations_before,
        )
        self.assertEqual(int(controller.Position), position_before)

        construction = set_construction.run(
            self.service,
            selection={"mode": "geometry", "items": [0]},
            construction=True,
        )
        self.assertTrue(construction.get("ok"), construction)
        self.assertTrue(sketch.getConstruction(0))
        self.assertEqual(
            tuple((item.Name, int(item.ID)) for item in list(controller.Operations)),
            operations_before,
        )
        self.assertEqual(int(controller.Position), position_before)

        self.document.undo()
        self.assertFalse(sketch.getConstruction(0))
        self.assertEqual(len(sketch.Geometry), 4)
        self.document.undo()
        self.assertEqual(len(sketch.Geometry), 0)
        self.assertEqual(
            tuple((item.Name, int(item.ID)) for item in list(controller.Operations)),
            operations_before,
        )

        self.document.redo()
        self.document.redo()
        self.assertEqual(len(sketch.Geometry), 4)
        self.assertTrue(sketch.getConstruction(0))
        self.assertEqual(
            tuple((item.Name, int(item.ID)) for item in list(controller.Operations)),
            operations_before,
        )
