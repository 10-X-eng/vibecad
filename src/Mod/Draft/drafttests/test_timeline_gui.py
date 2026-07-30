# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD document-timeline contracts for accepted Draft GUI operations."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui

import Draft
from draftutils import init_tools
from draftutils import timeline
from draftutils.transaction import ObjectReference
from draftutils.transaction import OwnedDocumentTransaction


DRAFT_COMPOSITE_CHILDREN = {
    "Draft_ArcTools": {
        "Draft_Arc",
        "Draft_Arc_3Points",
    },
    "Draft_BezierTools": {
        "Draft_BezCurve",
        "Draft_CubicBezCurve",
    },
    "Draft_ArrayTools": {
        "Draft_OrthoArray",
        "Draft_PolarArray",
        "Draft_CircularArray",
        "Draft_PathArray",
        "Draft_PathLinkArray",
        "Draft_PointArray",
        "Draft_PointLinkArray",
        "Draft_PathTwistedArray",
        "Draft_PathTwistedLinkArray",
    },
}

DRAFT_COMPOSITE_COMMANDS = {
    "Draft_ArcTools",
    "Draft_BezierTools",
    "Draft_ArrayTools",
}

DRAFT_STANDALONE_CREATION_COMMANDS = {
    "Draft_Line",
    "Draft_Arc",
    "Draft_Arc_3Points",
    "Draft_Circle",
    "Draft_Ellipse",
    "Draft_Rectangle",
    "Draft_Polygon",
    "Draft_BSpline",
    "Draft_BezCurve",
    "Draft_CubicBezCurve",
    "Draft_Point",
    "Draft_ShapeString",
    "Draft_Text",
    "Draft_Layer",
    "Draft_AddNamedGroup",
    "Draft_WorkingPlaneProxy",
}

DRAFT_SOURCE_PRESERVING_COMMANDS = {
    "Draft_Facebinder",
    "Draft_Hatch",
    "Draft_Dimension",
    "Draft_Label",
    "Draft_Mirror",
    "Draft_Clone",
    "Draft_OrthoArray",
    "Draft_PolarArray",
    "Draft_CircularArray",
    "Draft_PathArray",
    "Draft_PathLinkArray",
    "Draft_PointArray",
    "Draft_PointLinkArray",
    "Draft_PathTwistedArray",
    "Draft_PathTwistedLinkArray",
    "Draft_Shape2DView",
}

DRAFT_EXACT_REPLACEMENT_COMMANDS = {
    "Draft_Join",
    "Draft_Split",
    "Draft_Upgrade",
    "Draft_Downgrade",
    "Draft_WireToBSpline",
    "Draft_Draft2Sketch",
}

DRAFT_MODE_DEPENDENT_COMMANDS = {
    "Draft_Wire",
    "Draft_Fillet",
    "Draft_Move",
    "Draft_Rotate",
    "Draft_Scale",
    "Draft_Offset",
    "Draft_Trimex",
    "Draft_Stretch",
}

DRAFT_IN_PLACE_COMMANDS = {
    "Draft_Edit",
    "Draft_Slope",
    "Draft_FlipDimension",
    "Draft_SetStyle",
    "Draft_ApplyStyle",
    "Draft_ToggleConstructionMode",
    "Draft_AddToLayer",
    "Draft_AddToGroup",
    "Draft_AddConstruction",
    "Draft_Heal",
}

DRAFT_VIEW_SELECTION_OR_PREFERENCE_COMMANDS = {
    "Draft_LayerManager",
    "Draft_AnnotationStyleEditor",
    "Draft_SubelementHighlight",
    "Draft_SelectGroup",
    "Draft_ToggleDisplayMode",
    "Draft_ToggleGrid",
    "Draft_SelectPlane",
    "Draft_ShowSnapBar",
    "Draft_Snap_Lock",
    "Draft_Snap_Endpoint",
    "Draft_Snap_Midpoint",
    "Draft_Snap_Center",
    "Draft_Snap_Angle",
    "Draft_Snap_Intersection",
    "Draft_Snap_Perpendicular",
    "Draft_Snap_Extension",
    "Draft_Snap_Parallel",
    "Draft_Snap_Special",
    "Draft_Snap_Near",
    "Draft_Snap_Ortho",
    "Draft_Snap_Grid",
    "Draft_Snap_WorkingPlane",
    "Draft_Snap_Dimensions",
}


def _update_gui(count=2):
    for _ in range(count):
        Gui.updateGui()


def _timeline(document):
    return next(
        (
            obj
            for obj in document.Objects
            if obj.TypeId == "App::DocumentTimeline"
        ),
        None,
    )


def _timeline_button(object_name):
    for _attempt in range(100):
        button = Gui.getMainWindow().findChild(QtGui.QToolButton, object_name)
        if button is not None:
            if not button.isVisible() or not button.isEnabled():
                _update_gui()
                continue
            return button
        _update_gui()
    raise AssertionError("Timeline button is unavailable: " + object_name)


def _timeline_object_names():
    _update_gui()
    widget = Gui.getMainWindow().findChild(
        QtGui.QListWidget,
        "VibeCADFeatureTimelineItems",
    )
    if widget is None:
        raise AssertionError("Timeline item list is unavailable")
    return tuple(
        widget.item(row).data(QtCore.Qt.UserRole)
        for row in range(widget.count())
        if widget.item(row).data(QtCore.Qt.UserRole)
    )


class DraftTimelineGui(unittest.TestCase):
    """Tests one-operation Draft creation and exact replacement history."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("DraftTimelineGui")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self.documents = [self.document.Name]
        _update_gui()

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except RuntimeError:
                pass
        for document_name in reversed(self.documents):
            if document_name in App.listDocuments():
                App.closeDocument(document_name)

    def _join_sources(self):
        first = Draft.make_wire(
            [App.Vector(0, 0, 0), App.Vector(5, 0, 0)],
        )
        second = Draft.make_wire(
            [App.Vector(5, 0, 0), App.Vector(10, 0, 0)],
        )
        self.document.recompute()
        first.Visibility = True
        second.Visibility = True
        return first, second

    def test_every_draft_workbench_command_has_one_history_contract(self):
        workbench_commands = set()
        for commands in (
            init_tools.get_draft_drawing_commands(),
            init_tools.get_draft_annotation_commands(),
            init_tools.get_draft_modification_commands(),
            init_tools.get_draft_utility_commands_menu(),
            init_tools.get_draft_snap_commands(),
        ):
            workbench_commands.update(
                command for command in commands if command != "Separator"
            )
        for children in DRAFT_COMPOSITE_CHILDREN.values():
            workbench_commands.update(children)

        contracts = (
            DRAFT_COMPOSITE_COMMANDS,
            DRAFT_STANDALONE_CREATION_COMMANDS,
            DRAFT_SOURCE_PRESERVING_COMMANDS,
            DRAFT_EXACT_REPLACEMENT_COMMANDS,
            DRAFT_MODE_DEPENDENT_COMMANDS,
            DRAFT_IN_PLACE_COMMANDS,
            DRAFT_VIEW_SELECTION_OR_PREFERENCE_COMMANDS,
        )
        self.assertEqual(set().union(*contracts), workbench_commands)
        for index, contract in enumerate(contracts):
            for other in contracts[index + 1 :]:
                self.assertFalse(contract & other)

    def test_join_is_one_exact_replacement_with_undo_and_marker(self):
        first, second = self._join_sources()
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount

        self.document.openTransaction("Join Lines")
        outputs = timeline.join_replacement([first, second])
        self.document.recompute()
        self.document.commitTransaction()
        _update_gui()

        self.assertEqual(len(outputs), 1)
        operation = outputs[0]
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(operation.VibeCADTimelineReplacedInputs),
            [first, second],
        )
        self.assertFalse(first.Visibility)
        self.assertFalse(second.Visibility)
        self.assertTrue(operation.Visibility)
        controller = _timeline(self.document)
        self.assertIsNotNone(controller)
        self.assertIn(operation, controller.Operations)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertFalse(self.document.HasPendingTransaction)

        operation_name = operation.Name
        self.document.undo()
        _update_gui()
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertTrue(first.Visibility)
        self.assertTrue(second.Visibility)
        self.document.redo()
        _update_gui()
        operation = self.document.getObject(operation_name)
        self.assertIsNotNone(operation)

        _timeline_button("VibeCADFeatureTimelinePrevious").click()
        _update_gui()
        self.assertFalse(operation.Visibility)
        self.assertTrue(first.Visibility)
        self.assertTrue(second.Visibility)
        _timeline_button("VibeCADFeatureTimelineEnd").click()
        _update_gui()
        self.assertTrue(operation.Visibility)
        self.assertFalse(first.Visibility)
        self.assertFalse(second.Visibility)

    def test_multi_copy_is_one_operation_with_owned_resources(self):
        first, second = self._join_sources()
        self.document.openTransaction("Copy")
        outputs = timeline.move(
            [first, second],
            App.Vector(0, 5, 0),
            copy=True,
        )
        self.document.recompute()
        self.document.commitTransaction()
        _update_gui()

        self.assertEqual(len(outputs), 2)
        resource, operation = outputs
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertEqual(resource.VibeCADTimelineRole, "resource")
        self.assertIs(resource.VibeCADTimelineOwner, operation)
        controller = _timeline(self.document)
        self.assertIn(operation, controller.Operations)
        self.assertIn(resource, controller.Operations)
        operations = list(controller.Operations)
        resource_index = operations.index(resource)
        operation_index = operations.index(operation)
        self.assertEqual(operation_index, resource_index + 1)
        self.assertEqual(int(controller.Position), len(operations))
        visible_names = _timeline_object_names()
        self.assertEqual(visible_names.count(operation.Name), 1)
        self.assertNotIn(resource.Name, visible_names)
        self.assertTrue(first.Visibility)
        self.assertTrue(second.Visibility)

        _timeline_button("VibeCADFeatureTimelinePrevious").click()
        _update_gui()
        self.assertEqual(int(controller.Position), resource_index)
        self.assertFalse(operation.Visibility)
        self.assertFalse(resource.Visibility)
        self.assertTrue(first.Visibility)
        self.assertTrue(second.Visibility)
        _timeline_button("VibeCADFeatureTimelineEnd").click()
        _update_gui()
        self.assertTrue(operation.Visibility)
        self.assertTrue(resource.Visibility)

    def test_parametric_array_is_a_source_preserving_operation(self):
        source = self.document.addObject("Part::Box", "ArraySource")
        source.Length = 2
        source.Width = 3
        source.Height = 4
        source.Visibility = True
        self.document.recompute()
        undo_before = self.document.UndoCount

        self.document.openTransaction("Create Orthogonal Array")
        array = Draft.make_ortho_array(
            source,
            v_x=App.Vector(10, 0, 0),
            v_y=App.Vector(0, 10, 0),
            v_z=App.Vector(0, 0, 10),
            n_x=2,
            n_y=1,
            n_z=1,
            use_link=False,
            hide_base=False,
        )
        timeline.accept_derived_output(array, [source])
        self.document.recompute()
        self.document.commitTransaction()
        _update_gui()

        self.assertIs(array.Base, source)
        self.assertEqual(array.VibeCADTimelineRole, "operation")
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            array.PropertiesList,
        )
        self.assertTrue(source.Visibility)
        self.assertTrue(array.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        source.Length = 6
        self.document.recompute()
        self.assertGreater(array.Shape.BoundBox.XLength, 10)

        _timeline_button("VibeCADFeatureTimelinePrevious").click()
        _update_gui()
        self.assertTrue(source.Visibility)
        self.assertFalse(array.Visibility)
        _timeline_button("VibeCADFeatureTimelineEnd").click()
        _update_gui()
        self.assertTrue(source.Visibility)
        self.assertTrue(array.Visibility)

        array_name = array.Name
        self.document.undo()
        self.document.undo()
        self.document.undo()
        _update_gui()
        self.assertIsNone(self.document.getObject(array_name))
        self.assertTrue(source.Visibility)

    def test_in_place_move_does_not_invent_an_operation(self):
        first, _second = self._join_sources()
        controller = _timeline(self.document)
        operations_before = tuple(controller.Operations)
        placement_before = App.Placement(first.Placement)
        undo_before = self.document.UndoCount

        self.document.openTransaction("Move")
        result = timeline.move(
            [first],
            App.Vector(3, 4, 0),
            copy=False,
        )
        self.document.recompute()
        self.document.commitTransaction()
        _update_gui()

        self.assertIs(result, first)
        self.assertEqual(tuple(controller.Operations), operations_before)
        self.assertEqual(first.Placement.Base, App.Vector(3, 4, 0))
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.document.undo()
        _update_gui()
        self.assertEqual(first.Placement, placement_before)

    def test_partial_multi_output_is_rejected_before_tracking(self):
        first, _second = self._join_sources()
        controller = _timeline(self.document)
        operations_before = tuple(controller.Operations)

        with self.assertRaisesRegex(RuntimeError, "missing output"):
            timeline.accept_outputs([first, None])

        self.assertNotIn(
            "VibeCADTimelineRole",
            first.PropertiesList,
        )
        self.assertEqual(tuple(controller.Operations), operations_before)

    def test_replacement_metadata_and_ownership_survive_reopen(self):
        first, second = self._join_sources()
        first_name = first.Name
        second_name = second.Name
        self.document.openTransaction("Join Lines")
        operation = timeline.join_replacement([first, second])[0]
        self.document.recompute()
        self.document.commitTransaction()
        operation_name = operation.Name

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "draft-timeline.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            App.setActiveDocument(reopened.Name)
            _update_gui(5)

            reopened_first = reopened.getObject(first_name)
            reopened_second = reopened.getObject(second_name)
            reopened_operation = reopened.getObject(operation_name)
            self.assertEqual(
                reopened_operation.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                list(reopened_operation.VibeCADTimelineReplacedInputs),
                [reopened_first, reopened_second],
            )
            self.assertIn(
                "Hidden",
                reopened_operation.getEditorMode("VibeCADTimelineRole"),
            )
            self.assertIn(
                "Hidden",
                reopened_operation.getEditorMode(
                    "VibeCADTimelineReplacedInputs"
                ),
            )
            self.assertFalse(
                reopened_operation.removeProperty("VibeCADTimelineRole")
            )
            self.assertFalse(
                reopened_operation.removeProperty(
                    "VibeCADTimelineReplacedInputs"
                )
            )
            self.assertFalse(reopened_first.Visibility)
            self.assertFalse(reopened_second.Visibility)
            self.assertTrue(reopened_operation.Visibility)
            self.assertIn(reopened_operation, _timeline(reopened).Operations)

    def test_hidden_source_stays_hidden_when_derived_output_is_saved_and_reopened(
        self,
    ):
        source = self.document.addObject("Part::Feature", "HiddenSource")
        source.Shape = Part.makeBox(4, 5, 6)
        source.Visibility = False
        self.document.recompute()

        self.document.openTransaction("Create derived result")
        derived = self.document.addObject("Part::Feature", "DerivedResult")
        derived.Shape = source.Shape.copy()
        derived.Visibility = True
        timeline.accept_derived_output(derived, [source])
        self.document.recompute()
        self.document.commitTransaction()

        self.assertFalse(source.Visibility)
        self.assertTrue(derived.Visibility)
        self.assertEqual(derived.VibeCADTimelineRole, "operation")
        self.assertIn(
            "Hidden",
            derived.getEditorMode("VibeCADTimelineRole"),
        )

        source_name = source.Name
        derived_name = derived.Name
        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "hidden-source.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            App.setActiveDocument(reopened.Name)
            _update_gui(5)

            reopened_source = reopened.getObject(source_name)
            reopened_derived = reopened.getObject(derived_name)
            self.assertFalse(reopened_source.Visibility)
            self.assertTrue(reopened_derived.Visibility)
            self.assertEqual(
                reopened_derived.VibeCADTimelineRole,
                "operation",
            )
            self.assertIn(
                "Hidden",
                reopened_derived.getEditorMode("VibeCADTimelineRole"),
            )

    def test_retained_transaction_refuses_to_change_its_close_outcome(self):
        transaction = OwnedDocumentTransaction(
            self.document,
            "Retained Draft action",
        )
        self.document.addObject("Part::Feature", "RetainedOutput")
        with patch.object(
            App,
            "closeActiveTransaction",
            return_value=None,
        ):
            with self.assertRaisesRegex(
                RuntimeError,
                "Could not commit Draft transaction",
            ):
                transaction.commit()
            with self.assertRaisesRegex(
                RuntimeError,
                "already retained as commit; refusing abort",
            ):
                transaction.abort()

        transaction._retry_close()
        self.assertTrue(transaction.is_closed)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_non_parametric_scale_preserves_source_as_replaced_history(self):
        source = self.document.addObject("Part::Feature", "ScaleSource")
        source.Shape = Part.makeBox(2, 3, 4)
        source.Visibility = True
        self.document.recompute()
        undo_before = self.document.UndoCount

        self.document.openTransaction("Scale")
        result = timeline.scale(
            [source],
            App.Vector(2, 1, 1),
            copy=False,
        )
        self.document.recompute()
        self.document.commitTransaction()
        _update_gui()

        self.assertIsNot(result, source)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            [source],
        )
        self.assertFalse(source.Visibility)
        self.assertTrue(result.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        result_name = result.Name
        self.document.undo()
        _update_gui()
        self.assertIsNone(self.document.getObject(result_name))
        self.assertTrue(source.Visibility)

    def test_exact_reference_rejects_an_object_beyond_the_history_marker(self):
        first = self.document.addObject("Part::Feature", "FirstReference")
        first.Shape = Part.makeBox(1, 1, 1)
        second = self.document.addObject("Part::Feature", "SecondReference")
        second.Shape = Part.makeBox(2, 2, 2)
        self.document.recompute()
        reference = ObjectReference.capture(second)
        self.assertIs(reference.resolve(), second)

        _timeline_button("VibeCADFeatureTimelinePrevious").click()
        _update_gui()
        self.assertTrue(
            self.document.isObjectUsableAtCurrentTimelinePosition(first)
        )
        self.assertFalse(
            self.document.isObjectUsableAtCurrentTimelinePosition(second),
            (
                int(_timeline(self.document).Position),
                [
                    operation.Name
                    for operation in _timeline(self.document).Operations
                ],
            ),
        )
        self.assertIsNone(reference.resolve())
        with self.assertRaisesRegex(ValueError, "current History position"):
            ObjectReference.capture(second)

        _timeline_button("VibeCADFeatureTimelineEnd").click()
        _update_gui()
        self.assertIs(reference.resolve(), second)

    def test_facebinder_cancel_discards_staged_sources_without_an_undo(self):
        first = self.document.addObject("Part::Box", "FaceSource")
        second = self.document.addObject("Part::Box", "OtherFaceSource")
        self.document.recompute()

        self.document.openTransaction("Create Facebinder")
        facebinder = Draft.make_facebinder(
            [(first, ("Face1",))],
        )
        timeline.accept_derived_output(facebinder, [first])
        self.document.recompute()
        self.document.commitTransaction()
        original_faces = list(facebinder.Faces)
        undo_before = self.document.UndoCount

        from DraftGui import FacebinderTaskPanel

        panel = FacebinderTaskPanel()
        panel.obj = facebinder
        panel.update()
        panel._staged_faces.append((second, "Face2"))

        self.assertEqual(list(facebinder.Faces), original_faces)
        self.assertTrue(panel.reject())
        self.assertEqual(list(facebinder.Faces), original_faces)
        self.assertEqual(self.document.UndoCount, undo_before)
