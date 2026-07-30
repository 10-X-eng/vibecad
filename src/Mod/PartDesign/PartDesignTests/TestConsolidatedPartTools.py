# SPDX-License-Identifier: LGPL-2.1-or-later

"""Functional GUI coverage for Part tools hosted by Part Design."""

import importlib
from pathlib import Path
import shutil
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui


PART_DESIGN_COMPOSITE_ACTIONS = {
    "PartDesign_CompSketches": (
        "PartDesign_NewSketch",
        "Sketcher_MapSketch",
        "Sketcher_EditSketch",
    ),
    "PartDesign_CompPrimitiveAdditive": (
        "PartDesign_AdditiveBox",
        "PartDesign_AdditiveCylinder",
        "PartDesign_AdditiveSphere",
        "PartDesign_AdditiveCone",
        "PartDesign_AdditiveEllipsoid",
        "PartDesign_AdditiveTorus",
        "PartDesign_AdditivePrism",
        "PartDesign_AdditiveWedge",
    ),
    "PartDesign_CompPrimitiveSubtractive": (
        "PartDesign_SubtractiveBox",
        "PartDesign_SubtractiveCylinder",
        "PartDesign_SubtractiveSphere",
        "PartDesign_SubtractiveCone",
        "PartDesign_SubtractiveEllipsoid",
        "PartDesign_SubtractiveTorus",
        "PartDesign_SubtractivePrism",
        "PartDesign_SubtractiveWedge",
    ),
    "Part_CompCompoundTools": (
        "Part_Compound",
        "Part_ExplodeCompound",
        "Part_CompoundFilter",
    ),
    "Part_CompJoinFeatures": (
        "Part_JoinConnect",
        "Part_JoinEmbed",
        "Part_JoinCutout",
    ),
    "Part_CompOffset": (
        "Part_Offset",
        "Part_Offset2D",
    ),
    "Part_CompSplitFeatures": (
        "Part_BooleanFragments",
        "Part_SliceApart",
        "Part_Slice",
        "Part_XOR",
    ),
}

PROFILE_COMMAND_CASES = (
    ("PartDesign_Pad", "PartDesign::Pad", False, "profile"),
    ("PartDesign_Revolution", "PartDesign::Revolution", False, "profile"),
    ("PartDesign_AdditiveLoft", "PartDesign::AdditiveLoft", False, "loft"),
    ("PartDesign_AdditivePipe", "PartDesign::AdditivePipe", False, "pipe"),
    ("PartDesign_AdditiveHelix", "PartDesign::AdditiveHelix", False, "profile"),
    ("PartDesign_Pocket", "PartDesign::Pocket", True, "profile"),
    ("PartDesign_Hole", "PartDesign::Hole", True, "hole"),
    ("PartDesign_Groove", "PartDesign::Groove", True, "profile"),
    ("PartDesign_SubtractiveLoft", "PartDesign::SubtractiveLoft", True, "loft"),
    ("PartDesign_SubtractivePipe", "PartDesign::SubtractivePipe", True, "pipe"),
    ("PartDesign_SubtractiveHelix", "PartDesign::SubtractiveHelix", True, "profile"),
)

TRANSFORM_COMMAND_CASES = (
    ("PartDesign_Mirrored", "PartDesign::Mirrored"),
    ("PartDesign_LinearPattern", "PartDesign::LinearPattern"),
    ("PartDesign_PolarPattern", "PartDesign::PolarPattern"),
    ("PartDesign_MultiTransform", "PartDesign::MultiTransform"),
)


class TestConsolidatedPartTools(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("ConsolidatedPartTools")
        Gui.activateView("Gui::View3DInventor", True)
        self.body = self.document.addObject("PartDesign::Body", "Body")
        Gui.activeView().setActiveObject("pdbody", self.body)

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            Gui.Control.closeDialog()
        if App.getDocument("ConsolidatedPartTools") is not None:
            App.closeDocument("ConsolidatedPartTools")

    def _select(self, *objects):
        Gui.Selection.clearSelection()
        for obj in objects:
            Gui.Selection.addSelection(obj)

    def _box(self, name, x=0.0, size=10.0):
        import PartDesignGui

        box = self.document.addObject("Part::Box", name)
        box.Length = size
        box.Width = size
        box.Height = size
        box.Placement.Base.x = x
        self.document.recompute()
        PartDesignGui.adoptPartResult(box)
        return box

    def _wire(self, name, x=0.0, z=0.0):
        import PartDesignGui

        wire = self.document.addObject("Part::Feature", name)
        wire.Shape = Part.makePolygon(
            [
                App.Vector(x + 2, 0, z),
                App.Vector(x + 7, 0, z),
                App.Vector(x + 7, 0, z + 5),
                App.Vector(x + 2, 0, z + 5),
                App.Vector(x + 2, 0, z),
            ]
        )
        self.document.recompute()
        PartDesignGui.adoptPartResult(wire)
        return wire

    def _new_body(self, name):
        body = self.document.addObject("PartDesign::Body", name)
        Gui.activeView().setActiveObject("pdbody", body)
        return body

    def _profile_sketch(self, body, name, x=2.0, y=2.0, size=4.0):
        sketch = body.newObject("Sketcher::SketchObject", name)
        sketch.addGeometry(
            [
                Part.LineSegment(
                    App.Vector(x, y, 0),
                    App.Vector(x + size, y, 0),
                ),
                Part.LineSegment(
                    App.Vector(x + size, y, 0),
                    App.Vector(x + size, y + size, 0),
                ),
                Part.LineSegment(
                    App.Vector(x + size, y + size, 0),
                    App.Vector(x, y + size, 0),
                ),
                Part.LineSegment(
                    App.Vector(x, y + size, 0),
                    App.Vector(x, y, 0),
                ),
            ],
            False,
        )
        self.document.recompute()
        return sketch

    def _circle_sketch(self, body, name, x=5.0, y=5.0, radius=1.0):
        sketch = body.newObject("Sketcher::SketchObject", name)
        sketch.addGeometry(
            Part.Circle(
                App.Vector(x, y, 0),
                App.Vector(0, 0, 1),
                radius,
            ),
            False,
        )
        self.document.recompute()
        return sketch

    def _path_sketch(self, body, name):
        sketch = body.newObject("Sketcher::SketchObject", name)
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(4, 4, 0),
                App.Vector(4, 14, 0),
            ),
            False,
        )
        sketch.Placement = App.Placement(
            App.Vector(0, 4, -4),
            App.Rotation(App.Vector(1, 0, 0), 90),
        )
        self.document.recompute()
        return sketch

    @staticmethod
    def _linked_object(value):
        return value[0] if isinstance(value, tuple) else value

    @classmethod
    def _linked_objects(cls, value):
        if value is None:
            return []
        if hasattr(value, "Document"):
            return [value]
        if isinstance(value, tuple):
            return cls._linked_objects(value[0]) if value else []
        if isinstance(value, list):
            linked = []
            for item in value:
                linked.extend(cls._linked_objects(item))
            return linked
        return []

    def _native_pad(self, body, name):
        sketch = body.newObject("Sketcher::SketchObject", f"{name}Sketch")
        sketch.addGeometry(
            [
                Part.LineSegment(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
                Part.LineSegment(App.Vector(10, 0, 0), App.Vector(10, 10, 0)),
                Part.LineSegment(App.Vector(10, 10, 0), App.Vector(0, 10, 0)),
                Part.LineSegment(App.Vector(0, 10, 0), App.Vector(0, 0, 0)),
            ],
            False,
        )
        pad = body.newObject("PartDesign::Pad", name)
        pad.Profile = sketch
        pad.Length = 10
        self.document.recompute()
        self.assertFalse(pad.Shape.isNull())
        return pad

    def _profile_command_inputs(
        self,
        index,
        subtractive,
        input_kind,
        prefix,
    ):
        body = self._new_body(f"{prefix}Body{index}")
        base = self._native_pad(body, f"{prefix}Base{index}") if subtractive else None
        if input_kind == "hole":
            profile = self._circle_sketch(body, f"{prefix}Sketch{index}")
        else:
            profile = self._profile_sketch(body, f"{prefix}Sketch{index}")

        selections = [profile]
        secondary = None
        if input_kind == "loft":
            secondary = self._profile_sketch(
                body,
                f"{prefix}Section{index}",
                x=2.5,
                y=2.5,
                size=3.0,
            )
            secondary.Placement.Base.z = 8.0
            selections.append(secondary)
        elif input_kind == "pipe":
            secondary = self._path_sketch(body, f"{prefix}Spine{index}")
            selections.append(secondary)

        if base is not None:
            body.Tip = base
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", body)
        return body, base, profile, secondary, selections

    def _assert_body_result(self, result, body=None):
        body = body or self.body
        self._process_events()
        self.document.recompute()
        self.assertIsNotNone(result)
        self.assertIn(result, body.Group, result.Name)
        self.assertEqual(result.getParentGeoFeatureGroup(), body)
        self.assertTrue(result.isDerivedFrom("Part::Feature"), result.TypeId)
        self.assertFalse(result.Shape.isNull(), result.Name)
        self.assertEqual(result.getStatusString(), "Valid", result.getStatusString())

    def _assert_document_root_result(self, result):
        self._process_events()
        self.document.recompute()
        self.assertIsNotNone(result)
        self.assertIsNone(result.getParentGeoFeatureGroup(), result.Name)
        self.assertTrue(result.isDerivedFrom("Part::Feature"), result.TypeId)
        self.assertFalse(result.Shape.isNull(), result.Name)
        self.assertEqual(result.getStatusString(), "Valid", result.getStatusString())

    def _assert_body_native_timeline_result(self, body, result):
        self.assertIs(body.Tip, result)
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            result.PropertiesList,
        )

    def _assert_exact_root_replacement(self, result, sources):
        self._process_events()
        self.document.recompute()
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(
            result.getTypeIdOfProperty("VibeCADTimelineRole"),
            "App::PropertyString",
        )
        self.assertIn(
            "Hidden",
            result.getEditorMode("VibeCADTimelineRole"),
        )
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            list(sources),
        )
        self.assertEqual(
            result.getTypeIdOfProperty(
                "VibeCADTimelineReplacedInputs"
            ),
            "App::PropertyLinkListHidden",
        )
        self.assertIn(
            "Hidden",
            result.getEditorMode(
                "VibeCADTimelineReplacedInputs"
            ),
        )
        if "VibeCADTimelineOwner" in result.PropertiesList:
            self.assertIsNone(result.VibeCADTimelineOwner)

        source_tips = {
            source: source.Tip
            for source in sources
            if source.isDerivedFrom("PartDesign::Body")
        }
        source_shapes = {
            source: source.Shape.exportBrepToString()
            for source in sources
            if hasattr(source, "Shape") and not source.Shape.isNull()
        }
        self.assertTrue(result.ViewObject.Visibility)
        self.assertTrue(
            all(not source.ViewObject.Visibility for source in sources)
        )

        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        operation_index = list(timeline.Operations).index(result)
        previous = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        end = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        self.assertIsNotNone(previous)
        self.assertIsNotNone(end)
        self.assertTrue(previous.isEnabled())

        previous.click()
        self._process_events(40)
        self.assertEqual(timeline.Position, operation_index)
        self.assertFalse(result.ViewObject.Visibility)
        self.assertTrue(
            all(source.ViewObject.Visibility for source in sources)
        )
        for source, tip in source_tips.items():
            self.assertIs(source.Tip, tip)
        for source, brep in source_shapes.items():
            self.assertEqual(source.Shape.exportBrepToString(), brep)

        end.click()
        self._process_events(40)
        self.assertEqual(
            timeline.Position,
            len(timeline.Operations),
        )
        self.assertTrue(result.ViewObject.Visibility)
        self.assertTrue(
            all(not source.ViewObject.Visibility for source in sources)
        )

    def _cross_body_boxes(self, prefix, x):
        base_body = self._new_body(f"{prefix}BaseBody")
        base = self._box(f"{prefix}Base", x=x)
        tool_body = self._new_body(f"{prefix}ToolBody")
        tool = self._box(f"{prefix}Tool", x=x + 3.0, size=4.0)
        self.document.recompute()
        return base_body, base, tool_body, tool

    def _accept_task_dialog(self):
        self.assertTrue(Gui.Control.activeDialog())
        Gui.updateGui()
        for button_box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not button_box.isVisible():
                continue
            button = button_box.button(QtGui.QDialogButtonBox.Ok)
            if button is not None and button.isEnabled():
                button.click()
                QtGui.QApplication.processEvents()
                self.assertFalse(Gui.Control.activeDialog())
                return
        self.fail("Active task dialog has no enabled OK button")

    def _cancel_task_dialog(self):
        self.assertTrue(Gui.Control.activeDialog())
        Gui.updateGui()
        for button_box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not button_box.isVisible():
                continue
            button = button_box.button(QtGui.QDialogButtonBox.Cancel)
            if button is not None and button.isEnabled():
                button.click()
                QtGui.QApplication.processEvents()
                self.assertFalse(Gui.Control.activeDialog())
                return
        self.fail("Active task dialog has no enabled Cancel button")

    def _close_task_dialog(self):
        self.assertTrue(Gui.Control.activeDialog())
        Gui.updateGui()
        for button_box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not button_box.isVisible():
                continue
            for standard_button in (
                QtGui.QDialogButtonBox.Close,
                QtGui.QDialogButtonBox.Cancel,
            ):
                button = button_box.button(standard_button)
                if button is not None and button.isEnabled():
                    button.click()
                    QtGui.QApplication.processEvents()
                    if not Gui.Control.activeDialog():
                        return
        Gui.Control.closeDialog()
        QtGui.QApplication.processEvents()
        self.assertFalse(Gui.Control.activeDialog())

    def _run_modal_command(
        self, command_name, standard_button=QtGui.QDialogButtonBox.Ok
    ):
        clicked = []

        def click_dialog():
            for dialog in QtGui.QApplication.topLevelWidgets():
                if not isinstance(dialog, QtGui.QDialog) or not dialog.isVisible():
                    continue
                for button_box in dialog.findChildren(QtGui.QDialogButtonBox):
                    button = button_box.button(standard_button)
                    if button is not None and button.isEnabled():
                        clicked.append(dialog.windowTitle())
                        button.click()
                        return
            QtCore.QTimer.singleShot(20, click_dialog)

        QtCore.QTimer.singleShot(0, click_dialog)
        Gui.runCommand(command_name, 0)
        self.assertTrue(clicked, command_name)

    def _choose_action_selector_items(self, labels):
        main_window = Gui.getMainWindow()
        available = next(
            (
                tree
                for tree in main_window.findChildren(QtGui.QTreeWidget)
                if tree.objectName() == "availableTreeWidget" and tree.isVisible()
            ),
            None,
        )
        add_button = next(
            (
                button
                for button in main_window.findChildren(QtGui.QPushButton)
                if button.objectName() == "addButton" and button.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(available)
        self.assertIsNotNone(add_button)
        for label in labels:
            matches = available.findItems(label, QtCore.Qt.MatchExactly)
            self.assertEqual(len(matches), 1, label)
            available.setCurrentItem(matches[0])
            add_button.click()
            QtGui.QApplication.processEvents()

    def _open_and_close_task_command(self, command_name):
        Gui.runCommand(command_name, 0)
        self.assertTrue(Gui.Control.activeDialog(), command_name)
        for button_box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not button_box.isVisible():
                continue
            button = button_box.button(QtGui.QDialogButtonBox.Cancel)
            if button is None:
                button = button_box.button(QtGui.QDialogButtonBox.Close)
            if button is not None and button.isEnabled():
                button.click()
                QtGui.QApplication.processEvents()
                if not Gui.Control.activeDialog():
                    return
        Gui.Control.closeDialog()
        QtGui.QApplication.processEvents()
        self.assertFalse(Gui.Control.activeDialog(), command_name)

    @staticmethod
    def _process_events(wait_ms=20):
        Gui.updateGui()
        loop = QtCore.QEventLoop()
        QtCore.QTimer.singleShot(wait_ms, loop.quit)
        loop.exec()

    def _wait_until(self, predicate, timeout_ms=5000):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            self._process_events(10)
            try:
                result = predicate()
            except RuntimeError:
                result = None
            if result:
                return result
        return None

    @staticmethod
    def _visible_tree_labels():
        labels = []

        def collect(item):
            if item.isHidden():
                return
            labels.append(item.text(0))
            for child_index in range(item.childCount()):
                collect(item.child(child_index))

        for tree in Gui.getMainWindow().findChildren(QtGui.QTreeWidget):
            try:
                for index in range(tree.topLevelItemCount()):
                    collect(tree.topLevelItem(index))
            except RuntimeError:
                continue
        return labels

    def _assert_accepted_body_output(
        self,
        command_name,
        body,
        result,
        *,
        require_global_current=True,
    ):
        self.assertFalse(Gui.Control.activeDialog(), command_name)
        self.document.recompute()
        self._assert_body_result(result, body)
        self.assertTrue(result.Shape.isValid(), command_name)
        self.assertGreater(result.Shape.Volume, 0.0, command_name)
        self.assertEqual(body.Tip, result, command_name)
        self.assertEqual(
            Gui.activeView().getActiveObject("pdbody"),
            body,
            command_name,
        )
        self.assertTrue(
            self._wait_until(lambda: body.Label in self._visible_tree_labels()),
            (command_name, self._visible_tree_labels()),
        )

        object_name_role = int(QtCore.Qt.UserRole)
        is_current_role = object_name_role + 2
        is_after_position_role = object_name_role + 3
        is_marker_role = object_name_role + 5

        def result_timeline_item():
            timeline = Gui.getMainWindow().findChild(
                QtGui.QListWidget,
                "VibeCADFeatureTimelineItems",
            )
            if timeline is None:
                return None
            for index in range(timeline.count()):
                item = timeline.item(index)
                if item.data(object_name_role) == result.Name:
                    return item
            return None

        result_item = self._wait_until(result_timeline_item)
        self.assertIsNotNone(result_item, command_name)
        self.assertIn(result.Label, result_item.toolTip(), command_name)
        self.assertFalse(
            result_item.data(is_after_position_role),
            command_name,
        )

        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        current_items = [
            timeline.item(index)
            for index in range(timeline.count())
            if timeline.item(index).data(is_current_role)
        ]
        markers = [
            timeline.item(index)
            for index in range(timeline.count())
            if timeline.item(index).data(is_marker_role)
        ]
        self.assertEqual(len(current_items), 1, command_name)
        self.assertEqual(len(markers), 1, command_name)
        current = current_items[0]
        if require_global_current:
            self.assertIs(current, result_item, command_name)
        self.assertIn("Current document state", current.toolTip(), command_name)
        self.assertEqual(
            timeline.row(markers[0]),
            timeline.row(current) + 1,
            command_name,
        )

    def _send_mouse_event(self, viewport, event_type, pos, button, buttons):
        global_pos = viewport.mapToGlobal(pos)
        QtGui.QCursor.setPos(global_pos)
        event = QtGui.QMouseEvent(
            event_type,
            pos,
            global_pos,
            button,
            buttons,
            QtCore.Qt.NoModifier,
        )
        QtGui.QApplication.sendEvent(viewport, event)

    def test_part_design_composite_action_graphs_and_icons(self):
        for command_name, expected_actions in PART_DESIGN_COMPOSITE_ACTIONS.items():
            actions = Gui.Command.get(command_name).getAction()
            self.assertEqual(len(actions), len(expected_actions), command_name)
            self.assertEqual(
                tuple(action.objectName() for action in actions),
                expected_actions,
                command_name,
            )
            for action in actions:
                self.assertFalse(action.icon().isNull(), action.objectName())
                self.assertFalse(
                    action.icon().pixmap(24, 24).isNull(),
                    action.objectName(),
                )

    def test_part_composite_children_follow_valid_selection_state(self):
        part_composites = {
            name: children
            for name, children in PART_DESIGN_COMPOSITE_ACTIONS.items()
            if name.startswith("Part_")
        }

        def assert_state(command_name, child_states):
            self._process_events()
            expected_parent = any(child_states.values())
            self.assertEqual(
                Gui.isCommandActive(command_name),
                expected_parent,
                command_name,
            )
            actions = Gui.Command.get(command_name).getAction()
            self.assertEqual(
                tuple(action.objectName() for action in actions),
                tuple(child_states),
                command_name,
            )
            for action in actions:
                child_name = action.objectName()
                expected_child = child_states[child_name]
                self.assertEqual(
                    Gui.isCommandActive(child_name),
                    expected_child,
                    child_name,
                )
                self.assertEqual(
                    action.isEnabled(),
                    expected_child,
                    (command_name, child_name),
                )

        invalid = self.document.addObject("App::FeaturePython", "CompositeInvalid")
        self._select(invalid)
        for command_name, children in part_composites.items():
            assert_state(command_name, dict.fromkeys(children, False))

        offset_source = self._box("CompositeOffsetSource")
        self._select(offset_source)
        assert_state(
            "Part_CompOffset",
            {
                "Part_Offset": True,
                "Part_Offset2D": False,
            },
        )
        assert_state(
            "Part_CompJoinFeatures",
            dict.fromkeys(part_composites["Part_CompJoinFeatures"], False),
        )
        assert_state(
            "Part_CompSplitFeatures",
            dict.fromkeys(part_composites["Part_CompSplitFeatures"], False),
        )

        second = self._box("CompositeSecondSource", x=5.0)
        self._select(offset_source, second)
        assert_state(
            "Part_CompOffset",
            dict.fromkeys(part_composites["Part_CompOffset"], False),
        )
        assert_state(
            "Part_CompJoinFeatures",
            dict.fromkeys(part_composites["Part_CompJoinFeatures"], True),
        )
        assert_state(
            "Part_CompSplitFeatures",
            dict.fromkeys(part_composites["Part_CompSplitFeatures"], True),
        )

        compound = self.document.addObject("Part::Feature", "CompositeCompound")
        compound.Shape = Part.makeCompound([offset_source.Shape, second.Shape])
        self.document.recompute()
        self._select(compound)
        assert_state(
            "Part_CompCompoundTools",
            dict.fromkeys(part_composites["Part_CompCompoundTools"], True),
        )

    def test_part_composite_parents_dispatch_every_child_index(self):
        left = self._box("CompositeDispatchLeft")
        right = self._box("CompositeDispatchRight", x=3.0, size=6.0)

        self._select(left, right)
        self.assertTrue(Gui.isCommandActive("Part_CompCompoundTools"))
        Gui.runCommand("Part_CompCompoundTools", 0)
        compound = self.document.ActiveObject
        self._assert_body_result(compound)
        self.assertEqual(list(compound.Links), [left, right])

        explode_module = importlib.import_module(
            "CompoundTools._CommandExplodeCompound"
        )
        filter_module = importlib.import_module("CompoundTools._CommandCompoundFilter")
        original_explode = explode_module.cmdExplode
        original_filter = filter_module.cmdCreateCompoundFilter
        compound_calls = []
        try:
            explode_module.cmdExplode = lambda: compound_calls.append(("explode",))
            filter_module.cmdCreateCompoundFilter = lambda name: compound_calls.append(
                ("filter", name)
            )

            self._select(compound)
            Gui.runCommand("Part_CompCompoundTools", 1)
            self.assertEqual(compound_calls[-1], ("explode",))

            self._select(compound)
            Gui.runCommand("Part_CompCompoundTools", 2)
            self.assertEqual(compound_calls[-1], ("filter", "CompoundFilter"))
        finally:
            explode_module.cmdExplode = original_explode
            filter_module.cmdCreateCompoundFilter = original_filter

        join_module = importlib.import_module("BOPTools.JoinFeatures")
        original_join = join_module.cmdCreateJoinFeature
        join_calls = []
        try:
            join_module.cmdCreateJoinFeature = lambda name, mode: join_calls.append(
                (name, mode)
            )
            for index, mode in enumerate(("Connect", "Embed", "Cutout")):
                self._select(left, right)
                self.assertTrue(Gui.isCommandActive("Part_CompJoinFeatures"))
                Gui.runCommand("Part_CompJoinFeatures", index)
                self.assertEqual(join_calls[-1], (mode, mode))
        finally:
            join_module.cmdCreateJoinFeature = original_join

        split_module = importlib.import_module("BOPTools.SplitFeatures")
        original_fragments = split_module.cmdCreateBooleanFragmentsFeature
        original_slice_apart = split_module.cmdSliceApart
        original_slice = split_module.cmdCreateSliceFeature
        original_xor = split_module.cmdCreateXORFeature
        split_calls = []
        try:
            split_module.cmdCreateBooleanFragmentsFeature = lambda name, mode: (
                split_calls.append(("fragments", name, mode))
            )
            split_module.cmdSliceApart = lambda: split_calls.append(("slice-apart",))
            split_module.cmdCreateSliceFeature = lambda name, mode, transaction=True: (
                split_calls.append(("slice", name, mode, transaction))
            )
            split_module.cmdCreateXORFeature = lambda name: split_calls.append(
                ("xor", name)
            )
            expected = (
                ("fragments", "BooleanFragments", "Standard"),
                ("slice-apart",),
                ("slice", "Slice", "Split", True),
                ("xor", "XOR"),
            )
            for index, dispatched in enumerate(expected):
                self._select(left, right)
                self.assertTrue(Gui.isCommandActive("Part_CompSplitFeatures"))
                Gui.runCommand("Part_CompSplitFeatures", index)
                self.assertEqual(split_calls[-1], dispatched)
        finally:
            split_module.cmdCreateBooleanFragmentsFeature = original_fragments
            split_module.cmdSliceApart = original_slice_apart
            split_module.cmdCreateSliceFeature = original_slice
            split_module.cmdCreateXORFeature = original_xor

    def test_retained_part_commands_expose_only_geometrically_valid_states(self):
        guarded_commands = (
            "Part_Cut",
            "Part_Fuse",
            "Part_Common",
            "Part_Section",
            "Part_MakeFace",
            "Part_RuledSurface",
            "Part_Offset2D",
        )
        invalid = self.document.addObject("App::FeaturePython", "InvalidOperand")
        self._select(invalid)
        for command_name in (
            *guarded_commands,
            "Part_SimpleCopy",
            "Part_TransformedCopy",
            "Part_ElementCopy",
            "Part_RefineShape",
            "Part_PointsFromMesh",
        ):
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        first = self._box("GuardedFirst")
        self._select(first)
        for command_name in (
            "Part_Cut",
            "Part_Fuse",
            "Part_Common",
            "Part_Section",
            "Part_MakeFace",
            "Part_RuledSurface",
            "Part_Offset2D",
        ):
            self.assertFalse(Gui.isCommandActive(command_name), command_name)
        self.assertTrue(Gui.isCommandActive("Part_Offset"))
        for command_name in (
            "Part_SimpleCopy",
            "Part_TransformedCopy",
            "Part_RefineShape",
            "Part_PointsFromMesh",
        ):
            self.assertTrue(Gui.isCommandActive(command_name), command_name)
        self.assertFalse(Gui.isCommandActive("Part_ElementCopy"))

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first, "Edge1")
        self.assertTrue(Gui.isCommandActive("Part_ElementCopy"))

        second = self._box("GuardedSecond", x=5.0)
        self._select(first, second)
        for command_name in (
            "Part_Cut",
            "Part_Fuse",
            "Part_Common",
            "Part_Section",
        ):
            self.assertTrue(Gui.isCommandActive(command_name), command_name)
        for command_name in (
            "Part_MakeFace",
            "Part_RuledSurface",
            "Part_Offset2D",
        ):
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        closed_wire = self._wire("GuardedClosedWire", x=25.0)
        self._select(closed_wire)
        self.assertTrue(Gui.isCommandActive("Part_MakeFace"))
        self.assertTrue(Gui.isCommandActive("Part_Offset2D"))
        for command_name in (
            "Part_Cut",
            "Part_Fuse",
            "Part_Common",
            "Part_Section",
            "Part_RuledSurface",
        ):
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

    def test_parametric_primitive_commands_create_valid_body_results(self):
        results = []
        for command_name in (
            "Part_Box",
            "Part_Cylinder",
            "Part_Sphere",
            "Part_Cone",
            "Part_Torus",
        ):
            Gui.runCommand(command_name, 0)
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertGreater(len(result.Shape.Solids), 0, command_name)
            results.append(result)

        self.assertEqual(list(self.body.Group)[-len(results) :], results)
        self.assertEqual(self.body.Tip, results[-1])

    def test_tube_command_accepts_defaults_and_creates_valid_body_result(self):
        Gui.runCommand("Part_Tube", 0)
        tube = self.document.ActiveObject
        self._accept_task_dialog()
        self._assert_body_result(tube)
        self.assertGreater(len(tube.Shape.Solids), 0)

    def test_extrude_revolve_mirror_and_scale_task_commands(self):
        extrusion_profile = self._wire("ExtrusionProfile")
        self._select(extrusion_profile)
        Gui.runCommand("Part_Extrude", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

        revolution_profile = self._wire("RevolutionProfile", x=15.0)
        self._select(revolution_profile)
        Gui.runCommand("Part_Revolve", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

        mirror_source = self._box("MirrorSource", x=30.0)
        self._select(mirror_source)
        Gui.runCommand("Part_Mirror", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

        scale_source = self._box("ScaleSource", x=50.0)
        self._select(scale_source)
        Gui.runCommand("Part_Scale", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

    def _exercise_edge_task_command(self, command_name):
        source = self._box(f"{command_name}Source")
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Edge1")
        self.assertEqual(Gui.Selection.getSelectionEx()[0].SubElementNames, ("Edge1",))
        Gui.runCommand(command_name, 0)
        edge_tree = next(
            (
                tree
                for tree in Gui.getMainWindow().findChildren(QtGui.QTreeView)
                if tree.objectName() == "treeView" and tree.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(edge_tree)
        model = edge_tree.model()
        self.assertGreater(model.rowCount(), 0)
        checked_rows = [
            model.index(row, 0).data(QtCore.Qt.CheckStateRole)
            for row in range(model.rowCount())
        ]
        self.assertTrue(
            any(state == QtCore.Qt.CheckState.Checked.value for state in checked_rows),
            (
                f"{command_name} did not preserve the selected edge: "
                f"selection={[item.SubElementNames for item in Gui.Selection.getSelectionEx()]}, "
                f"rows={checked_rows}"
            ),
        )
        self._accept_task_dialog()
        result = self.document.ActiveObject
        self.assertIsNot(result, source)
        self._assert_body_result(result)

    def test_fillet_task_command(self):
        self._exercise_edge_task_command("Part_Fillet")

    def test_chamfer_task_command(self):
        self._exercise_edge_task_command("Part_Chamfer")

    def test_body_native_dressup_winners_accept_ordinary_part_results(self):
        for index, (command_name, subelement) in enumerate(
            (
                ("PartDesign_Fillet", "Edge1"),
                ("PartDesign_Chamfer", "Edge1"),
                ("PartDesign_Thickness", "Face1"),
            )
        ):
            source = self._box(f"BodyDressupSource{index}", x=index * 25.0)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(source, subelement)
            Gui.runCommand(command_name, 0)
            self.assertTrue(Gui.Control.activeDialog(), command_name)
            self._accept_task_dialog()
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertTrue(
                result.isDerivedFrom("PartDesign::Feature"),
                (command_name, result.TypeId),
            )
            self.assertEqual(result.BaseFeature, source)

    def test_body_native_finish_tools_cancel_without_leaving_invalid_features(self):
        for index, (command_name, feature_type, subelement) in enumerate(
            (
                ("PartDesign_Fillet", "PartDesign::Fillet", "Edge1"),
                ("PartDesign_Chamfer", "PartDesign::Chamfer", "Edge1"),
                ("PartDesign_Draft", "PartDesign::Draft", "Face1"),
                ("PartDesign_Thickness", "PartDesign::Thickness", "Face1"),
            )
        ):
            source = self._box(f"CancelFinishSource{index}", x=index * 25.0)
            original_tip = self.body.Tip
            original_group = tuple(self.body.Group)
            original_names = tuple(obj.Name for obj in self.document.Objects)

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(source, subelement)
            Gui.runCommand(command_name, 0)

            self.assertTrue(Gui.Control.activeDialog(), command_name)
            temporary = self.document.ActiveObject
            self.assertEqual(temporary.TypeId, feature_type)
            self.assertEqual(temporary.Base[0], source)
            self.assertEqual(list(temporary.Base[1]), [subelement])

            self._cancel_task_dialog()
            self.assertEqual(self.body.Tip, original_tip, command_name)
            self.assertEqual(tuple(self.body.Group), original_group, command_name)
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
                command_name,
            )

    def test_body_rendered_subelements_resolve_finish_base_to_tip(self):
        body = self._new_body("BodyRenderedFinishBody")
        source = self._native_pad(body, "BodyRenderedFinishSource")

        for command_name, feature_type, subelement in (
            ("PartDesign_Fillet", "PartDesign::Fillet", "Edge1"),
            ("PartDesign_Chamfer", "PartDesign::Chamfer", "Edge1"),
            ("PartDesign_Draft", "PartDesign::Draft", "Face1"),
            ("PartDesign_Thickness", "PartDesign::Thickness", "Face1"),
        ):
            body.Tip = source
            self.document.recompute()
            Gui.activeView().setActiveObject("pdbody", body)
            original_names = tuple(obj.Name for obj in self.document.Objects)
            original_group = tuple(body.Group)

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(body, subelement)
            raw_selection = Gui.Selection.getSelectionEx()
            self.assertEqual(len(raw_selection), 1, command_name)
            self.assertEqual(raw_selection[0].Object, body, command_name)
            self.assertEqual(
                raw_selection[0].SubElementNames,
                (subelement,),
                command_name,
            )

            Gui.runCommand(command_name, 0)

            self.assertTrue(Gui.Control.activeDialog(), command_name)
            temporary = self.document.ActiveObject
            self.assertEqual(temporary.TypeId, feature_type, command_name)
            self.assertEqual(temporary.getParentGeoFeatureGroup(), body, command_name)
            self.assertEqual(temporary.Base[0], source, command_name)
            self.assertNotEqual(temporary.Base[0], body, command_name)
            self.assertEqual(list(temporary.Base[1]), [subelement], command_name)

            self._cancel_task_dialog()
            self.assertEqual(body.Tip, source, command_name)
            self.assertEqual(tuple(body.Group), original_group, command_name)
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
                command_name,
            )

    def test_body_native_finish_tools_require_explicit_geometry(self):
        self._box("FinishSelectionSource")
        for command_name in (
            "PartDesign_Fillet",
            "PartDesign_Chamfer",
            "PartDesign_Draft",
            "PartDesign_Thickness",
        ):
            Gui.Selection.clearSelection()
            original_names = tuple(obj.Name for obj in self.document.Objects)
            original_group = tuple(self.body.Group)
            original_tip = self.body.Tip

            self.assertFalse(Gui.isCommandActive(command_name), command_name)
            Gui.runCommand(command_name, 0)
            self._process_events()

            self.assertFalse(Gui.Control.activeDialog(), command_name)
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
                command_name,
            )
            self.assertEqual(tuple(self.body.Group), original_group, command_name)
            self.assertEqual(self.body.Tip, original_tip, command_name)

    def test_body_native_finish_tools_preserve_rejected_geometry_selection(self):
        source = self._box("RejectedFinishSelectionSource")
        for command_name, subelement in (
            ("PartDesign_Fillet", "Vertex1"),
            ("PartDesign_Chamfer", "Vertex1"),
            ("PartDesign_Draft", "Edge1"),
            ("PartDesign_Thickness", "Edge1"),
        ):
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(source, subelement)
            original_names = tuple(obj.Name for obj in self.document.Objects)
            original_group = tuple(self.body.Group)
            original_tip = self.body.Tip

            self.assertFalse(Gui.isCommandActive(command_name), command_name)
            Gui.runCommand(command_name, 0)
            self._process_events()

            self.assertFalse(Gui.Control.activeDialog(), command_name)
            selection = Gui.Selection.getSelectionEx()
            self.assertEqual(len(selection), 1, command_name)
            self.assertEqual(selection[0].Object, source, command_name)
            self.assertEqual(
                selection[0].SubElementNames,
                (subelement,),
                command_name,
            )
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
                command_name,
            )
            self.assertEqual(tuple(self.body.Group), original_group, command_name)
            self.assertEqual(self.body.Tip, original_tip, command_name)

    def test_body_native_transform_uses_selected_body_and_ordinary_result(self):
        other_body = self.document.addObject("PartDesign::Body", "SelectedBody")
        source = self.document.addObject("Part::Box", "SelectedBodyResult")
        source.Length = 10
        source.Width = 10
        source.Height = 10
        source.Placement.Base.x = 20
        other_body.addObject(source)
        self.document.recompute()

        for selected in (source, other_body):
            Gui.activeView().setActiveObject("pdbody", self.body)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(selected)
            Gui.runCommand("PartDesign_Mirrored", 0)

            self.assertTrue(Gui.Control.activeDialog())
            self.assertEqual(
                Gui.activeView().getActiveObject("pdbody"),
                other_body,
            )
            transformed = self.document.ActiveObject
            self.assertEqual(transformed.TypeId, "PartDesign::Mirrored")
            self.assertEqual(transformed.getParentGeoFeatureGroup(), other_body)
            self.assertEqual(transformed.TransformMode, "Whole shape")
            self.assertEqual(list(transformed.Originals), [])
            self._cancel_task_dialog()

    def test_body_native_transform_preserves_native_feature_mode(self):
        pad = self._native_pad(self.body, "NativeTransformPad")
        fillet = self.body.newObject("PartDesign::Fillet", "NativeTransformFillet")
        fillet.Base = (pad, ["Edge1"])
        fillet.Radius = 0.5
        self.document.recompute()
        self.assertFalse(fillet.Shape.isNull())

        for source in (pad, fillet):
            Gui.activeView().setActiveObject("pdbody", self.body)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(source)
            Gui.runCommand("PartDesign_Mirrored", 0)

            self.assertTrue(Gui.Control.activeDialog())
            transformed = self.document.ActiveObject
            self.assertEqual(transformed.TypeId, "PartDesign::Mirrored")
            self.assertEqual(transformed.TransformMode, "Features")
            self.assertEqual(list(transformed.Originals), [source])
            self._cancel_task_dialog()

    def test_transform_family_routes_whole_results_and_native_features(self):
        commands = (
            ("PartDesign_LinearPattern", "PartDesign::LinearPattern"),
            ("PartDesign_PolarPattern", "PartDesign::PolarPattern"),
            ("PartDesign_MultiTransform", "PartDesign::MultiTransform"),
        )
        result_body = self._new_body("TransformResultBody")
        ordinary_result = self.document.addObject(
            "Part::Box", "TransformOrdinaryResult"
        )
        ordinary_result.Length = 10
        ordinary_result.Width = 10
        ordinary_result.Height = 10
        result_body.addObject(ordinary_result)
        self.document.recompute()

        for command_name, feature_type in commands:
            Gui.activeView().setActiveObject("pdbody", self.body)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(ordinary_result)
            original_names = tuple(obj.Name for obj in self.document.Objects)
            original_group = tuple(result_body.Group)
            original_tip = result_body.Tip

            Gui.runCommand(command_name, 0)

            self.assertTrue(Gui.Control.activeDialog(), command_name)
            transformed = self.document.ActiveObject
            self.assertEqual(transformed.TypeId, feature_type, command_name)
            self.assertEqual(transformed.getParentGeoFeatureGroup(), result_body)
            self.assertEqual(transformed.TransformMode, "Whole shape")
            self.assertEqual(list(transformed.Originals), [])
            self.assertEqual(
                Gui.activeView().getActiveObject("pdbody"),
                result_body,
            )
            self._cancel_task_dialog()
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
                command_name,
            )
            self.assertEqual(tuple(result_body.Group), original_group, command_name)
            self.assertEqual(result_body.Tip, original_tip, command_name)

        native_body = self._new_body("TransformNativeBody")
        native_pad = self._native_pad(native_body, "TransformNativePad")
        native_fillet = native_body.newObject(
            "PartDesign::Fillet",
            "TransformNativeFillet",
        )
        native_fillet.Base = (native_pad, ["Edge1"])
        native_fillet.Radius = 0.5
        self.document.recompute()
        self.assertFalse(native_fillet.Shape.isNull())

        for command_name, feature_type in commands:
            for source in (native_pad, native_fillet):
                native_body.Tip = native_fillet
                self.document.recompute()
                Gui.activeView().setActiveObject("pdbody", self.body)
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(source)
                original_names = tuple(obj.Name for obj in self.document.Objects)
                original_group = tuple(native_body.Group)
                original_tip = native_body.Tip

                Gui.runCommand(command_name, 0)

                self.assertTrue(Gui.Control.activeDialog(), command_name)
                transformed = self.document.ActiveObject
                self.assertEqual(transformed.TypeId, feature_type, command_name)
                self.assertEqual(transformed.getParentGeoFeatureGroup(), native_body)
                self.assertEqual(transformed.TransformMode, "Features")
                self.assertEqual(list(transformed.Originals), [source])
                self._cancel_task_dialog()
                self.assertEqual(
                    tuple(obj.Name for obj in self.document.Objects),
                    original_names,
                    command_name,
                )
                self.assertEqual(tuple(native_body.Group), original_group, command_name)
                self.assertEqual(native_body.Tip, original_tip, command_name)

    def test_transform_commands_accept_into_current_body_timeline(self):
        for index, (command_name, feature_type) in enumerate(TRANSFORM_COMMAND_CASES):
            body = self._new_body(f"AcceptedTransformBody{index}")
            source = self._native_pad(body, f"AcceptedTransformPad{index}")
            body.Tip = source
            self.document.recompute()
            Gui.activeView().setActiveObject("pdbody", body)
            self._select(source)
            self.assertTrue(Gui.isCommandActive(command_name), command_name)

            Gui.runCommand(command_name, 0)
            self.assertTrue(Gui.Control.activeDialog(), command_name)
            transformed = self.document.ActiveObject
            self.assertEqual(transformed.TypeId, feature_type, command_name)
            self.assertEqual(
                transformed.getParentGeoFeatureGroup(),
                body,
                command_name,
            )
            self.assertEqual(transformed.TransformMode, "Features", command_name)
            self.assertEqual(list(transformed.Originals), [source], command_name)

            if command_name == "PartDesign_LinearPattern":
                transformed.Length = 10.0
                transformed.Occurrences = 2
            elif command_name == "PartDesign_PolarPattern":
                transformed.Angle = 90.0
                transformed.Occurrences = 2
            elif command_name == "PartDesign_MultiTransform":
                transform_list = next(
                    (
                        widget
                        for widget in Gui.getMainWindow().findChildren(
                            QtGui.QListWidget,
                            "listTransformFeatures",
                        )
                        if widget.isVisible()
                    ),
                    None,
                )
                self.assertIsNotNone(transform_list, command_name)
                add_linear = next(
                    (
                        action
                        for action in transform_list.actions()
                        if "Add Linear Pattern" in action.text()
                    ),
                    None,
                )
                self.assertIsNotNone(add_linear, command_name)
                add_linear.trigger()
                self._process_events()
                self.assertEqual(len(transformed.Transformations), 1, command_name)
                internal = transformed.Transformations[0]
                internal.Length = 10.0
                internal.Occurrences = 2
                self.document.recompute()
                subtask_ok = next(
                    (
                        button
                        for button in Gui.getMainWindow().findChildren(
                            QtGui.QPushButton,
                            "buttonOK",
                        )
                        if button.isVisible() and button.isEnabled()
                    ),
                    None,
                )
                self.assertIsNotNone(subtask_ok, command_name)
                subtask_ok.click()
                self._process_events()

            self.document.recompute()
            self.assertFalse(transformed.Shape.isNull(), command_name)
            self._accept_task_dialog()
            self._assert_accepted_body_output(command_name, body, transformed)

    def test_draft_accepts_valid_faces_into_current_body_timeline(self):
        body = self._new_body("AcceptedDraftBody")
        source = self._native_pad(body, "AcceptedDraftPad")
        body.Tip = source
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1")
        self.assertTrue(Gui.isCommandActive("PartDesign_Draft"))

        Gui.runCommand("PartDesign_Draft", 0)
        self.assertTrue(Gui.Control.activeDialog())
        draft = self.document.ActiveObject
        self.assertEqual(draft.TypeId, "PartDesign::Draft")
        z_axis = body.Origin.OriginFeatures[2]
        draft.NeutralPlane = (source, ["Face5"])
        draft.PullDirection = (z_axis, [""])
        draft.Angle = 5.0
        line_plane = next(
            (
                field
                for field in Gui.getMainWindow().findChildren(
                    QtGui.QLineEdit,
                    "linePlane",
                )
                if field.isVisible()
            ),
            None,
        )
        line_direction = next(
            (
                field
                for field in Gui.getMainWindow().findChildren(
                    QtGui.QLineEdit,
                    "lineLine",
                )
                if field.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(line_plane)
        self.assertIsNotNone(line_direction)
        line_plane.setText(f"{source.Name}:Face5")
        line_direction.setText(z_axis.Name)
        self.document.recompute()
        self.assertFalse(draft.Shape.isNull())

        self._accept_task_dialog()
        self._assert_accepted_body_output("PartDesign_Draft", body, draft)

    def test_profile_command_rejects_body_selection_without_mutation(self):
        self.body.newObject("Sketcher::SketchObject", "AvailableProfile")
        self.document.recompute()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.body)
        original_names = tuple(obj.Name for obj in self.document.Objects)
        original_group = tuple(self.body.Group)
        original_tip = self.body.Tip

        self.assertFalse(Gui.isCommandActive("PartDesign_Pad"))
        Gui.runCommand("PartDesign_Pad", 0)
        self._process_events()

        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects), original_names
        )
        self.assertEqual(tuple(self.body.Group), original_group)
        self.assertEqual(self.body.Tip, original_tip)

    def test_body_rendered_face_resolves_extrude_profile_to_tip(self):
        body = self._new_body("BodyRenderedProfileBody")
        source = self._native_pad(body, "BodyRenderedProfileSource")

        for command_name, feature_type in (
            ("PartDesign_Pad", "PartDesign::Pad"),
            ("PartDesign_Pocket", "PartDesign::Pocket"),
        ):
            body.Tip = source
            self.document.recompute()
            Gui.activeView().setActiveObject("pdbody", body)
            original_names = tuple(obj.Name for obj in self.document.Objects)
            original_group = tuple(body.Group)

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(body, "Face6")
            raw_selection = Gui.Selection.getSelectionEx()
            self.assertEqual(len(raw_selection), 1, command_name)
            self.assertEqual(raw_selection[0].Object, body, command_name)
            self.assertEqual(raw_selection[0].SubElementNames, ("Face6",), command_name)

            Gui.runCommand(command_name, 0)

            self.assertTrue(Gui.Control.activeDialog(), command_name)
            temporary = self.document.ActiveObject
            self.assertEqual(temporary.TypeId, feature_type, command_name)
            self.assertEqual(temporary.getParentGeoFeatureGroup(), body, command_name)
            self.assertEqual(
                self._linked_object(temporary.Profile), source, command_name
            )
            self.assertNotEqual(
                self._linked_object(temporary.Profile), body, command_name
            )
            self.assertEqual(list(temporary.Profile[1]), ["Face6"], command_name)

            self._cancel_task_dialog()
            self.assertEqual(body.Tip, source, command_name)
            self.assertEqual(tuple(body.Group), original_group, command_name)
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
                command_name,
            )

    def test_hole_rejects_empty_body_without_creating_a_feature(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.body)
        original_names = tuple(obj.Name for obj in self.document.Objects)

        self.assertFalse(Gui.isCommandActive("PartDesign_Hole"))
        Gui.runCommand("PartDesign_Hole", 0)
        self._process_events()

        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects), original_names
        )

    def test_profile_draft_and_transform_commands_expose_only_usable_states(self):
        profile_commands = tuple(case[0] for case in PROFILE_COMMAND_CASES)
        additive_commands = tuple(
            command
            for command, _feature_type, subtractive, _input_kind in PROFILE_COMMAND_CASES
            if not subtractive
        )
        subtractive_commands = tuple(
            command
            for command, _feature_type, subtractive, _input_kind in PROFILE_COMMAND_CASES
            if subtractive
        )
        transform_commands = tuple(case[0] for case in TRANSFORM_COMMAND_CASES)

        Gui.Selection.clearSelection()
        for command_name in (
            profile_commands + transform_commands + ("PartDesign_Draft",)
        ):
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        profile = self._profile_sketch(self.body, "CommandStateProfile")
        self._select(profile)
        for command_name in additive_commands:
            self.assertTrue(Gui.isCommandActive(command_name), command_name)
        for command_name in subtractive_commands:
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        self._select(self.body)
        for command_name in additive_commands:
            self.assertTrue(Gui.isCommandActive(command_name), command_name)
        for command_name in subtractive_commands:
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        base = self._native_pad(self.body, "CommandStateBase")
        self.body.Tip = base
        self.document.recompute()
        self._select(profile)
        for command_name in profile_commands:
            self.assertTrue(Gui.isCommandActive(command_name), command_name)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(base, "Face1")
        self.assertTrue(Gui.isCommandActive("PartDesign_Draft"))
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(base, "Edge1")
        self.assertFalse(Gui.isCommandActive("PartDesign_Draft"))

        Gui.Selection.clearSelection()
        for command_name in transform_commands:
            self.assertTrue(Gui.isCommandActive(command_name), command_name)
        self._select(profile)
        for command_name in transform_commands:
            self.assertFalse(Gui.isCommandActive(command_name), command_name)
        self._select(base)
        for command_name in transform_commands:
            self.assertTrue(Gui.isCommandActive(command_name), command_name)

    def test_profile_commands_launch_with_valid_inputs_and_cancel_cleanly(self):
        for index, (command_name, feature_type, subtractive, input_kind) in enumerate(
            PROFILE_COMMAND_CASES
        ):
            body, _base, profile, secondary, selections = self._profile_command_inputs(
                index,
                subtractive,
                input_kind,
                "ProfileCommand",
            )
            original_names = tuple(obj.Name for obj in self.document.Objects)
            original_group = tuple(body.Group)
            original_tip = body.Tip

            Gui.Selection.clearSelection()
            for selected in selections:
                Gui.Selection.addSelection(selected)
            Gui.runCommand(command_name, 0)

            self.assertTrue(Gui.Control.activeDialog(), command_name)
            feature = self.document.ActiveObject
            self.assertEqual(feature.TypeId, feature_type, command_name)
            self.assertEqual(feature.getParentGeoFeatureGroup(), body, command_name)
            self.assertEqual(
                self._linked_object(feature.Profile), profile, command_name
            )
            if command_name.endswith("Helix"):
                self.assertFalse(feature.Shape.isNull(), command_name)
            if input_kind == "loft":
                self.assertIn(
                    secondary,
                    self._linked_objects(feature.Sections),
                    command_name,
                )
            elif input_kind == "pipe":
                self.assertIn(
                    secondary,
                    self._linked_objects(feature.Spine),
                    command_name,
                )

            self._cancel_task_dialog()
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
                command_name,
            )
            self.assertEqual(tuple(body.Group), original_group, command_name)
            self.assertEqual(body.Tip, original_tip, command_name)

    def test_profile_commands_accept_valid_geometry_into_body_history(self):
        for index, (command_name, feature_type, subtractive, input_kind) in enumerate(
            PROFILE_COMMAND_CASES
        ):
            body, _base, profile, secondary, selections = self._profile_command_inputs(
                index,
                subtractive,
                input_kind,
                "AcceptedProfile",
            )
            Gui.Selection.clearSelection()
            for selected in selections:
                Gui.Selection.addSelection(selected)
            self.assertTrue(Gui.isCommandActive(command_name), command_name)

            Gui.runCommand(command_name, 0)
            self.assertTrue(Gui.Control.activeDialog(), command_name)
            feature = self.document.ActiveObject
            self.assertEqual(feature.TypeId, feature_type, command_name)
            self.assertEqual(feature.getParentGeoFeatureGroup(), body, command_name)
            self.assertEqual(
                self._linked_object(feature.Profile), profile, command_name
            )
            if input_kind == "loft":
                self.assertIn(
                    secondary,
                    self._linked_objects(feature.Sections),
                    command_name,
                )
            elif input_kind == "pipe":
                self.assertIn(
                    secondary,
                    self._linked_objects(feature.Spine),
                    command_name,
                )
            if command_name.endswith("Helix"):
                feature.Pitch = 10.0
                feature.Height = 8.0
                feature.Mode = 0
            self.document.recompute()
            self.assertFalse(feature.Shape.isNull(), command_name)

            self._accept_task_dialog()
            self._assert_accepted_body_output(command_name, body, feature)

    def test_primitive_composite_children_launch_and_cancel_cleanly(self):
        shape_names = (
            "Box",
            "Cylinder",
            "Sphere",
            "Cone",
            "Ellipsoid",
            "Torus",
            "Prism",
            "Wedge",
        )
        additive_body = self._new_body("AdditivePrimitiveBody")
        for index, shape_name in enumerate(shape_names):
            Gui.activeView().setActiveObject("pdbody", additive_body)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(additive_body)
            original_names = tuple(obj.Name for obj in self.document.Objects)
            original_group = tuple(additive_body.Group)
            original_tip = additive_body.Tip

            Gui.runCommand("PartDesign_CompPrimitiveAdditive", index)

            self.assertTrue(Gui.Control.activeDialog(), shape_name)
            feature = self.document.ActiveObject
            self.assertEqual(feature.TypeId, f"PartDesign::Additive{shape_name}")
            self.assertEqual(feature.getParentGeoFeatureGroup(), additive_body)
            self._cancel_task_dialog()
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
                shape_name,
            )
            self.assertEqual(tuple(additive_body.Group), original_group, shape_name)
            self.assertEqual(additive_body.Tip, original_tip, shape_name)

        subtractive_body = self._new_body("SubtractivePrimitiveBody")
        base = self._native_pad(subtractive_body, "SubtractivePrimitiveBase")
        for index, shape_name in enumerate(shape_names):
            subtractive_body.Tip = base
            self.document.recompute()
            Gui.activeView().setActiveObject("pdbody", subtractive_body)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(subtractive_body)
            original_names = tuple(obj.Name for obj in self.document.Objects)
            original_group = tuple(subtractive_body.Group)
            original_tip = subtractive_body.Tip

            Gui.runCommand("PartDesign_CompPrimitiveSubtractive", index)

            self.assertTrue(Gui.Control.activeDialog(), shape_name)
            feature = self.document.ActiveObject
            self.assertEqual(feature.TypeId, f"PartDesign::Subtractive{shape_name}")
            self.assertEqual(feature.getParentGeoFeatureGroup(), subtractive_body)
            self._cancel_task_dialog()
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
                shape_name,
            )
            self.assertEqual(tuple(subtractive_body.Group), original_group, shape_name)
            self.assertEqual(subtractive_body.Tip, original_tip, shape_name)

    def test_standalone_primitive_dialog_dispatches_each_retained_shape(self):
        Gui.activeView().setActiveObject("pdbody", self.body)
        Gui.runCommand("Part_Primitives", 0)
        self.assertTrue(Gui.Control.activeDialog())
        primitive_selector = next(
            (
                combo
                for combo in Gui.getMainWindow().findChildren(QtGui.QComboBox)
                if combo.objectName() == "PrimitiveTypeCB" and combo.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(primitive_selector)
        expected_shapes = {
            "Plane": "Part::Plane",
            "Helix": "Part::Helix",
            "Spiral": "Part::Spiral",
            "Circle": "Part::Circle",
            "Ellipse": "Part::Ellipse",
            "Point": "Part::Vertex",
            "Line": "Part::Line",
            "Regular polygon": "Part::RegularPolygon",
        }
        self.assertEqual(
            tuple(
                primitive_selector.itemText(index)
                for index in range(primitive_selector.count())
            ),
            tuple(expected_shapes),
        )
        button_box = next(
            (
                box
                for box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox)
                if box.isVisible()
                and box.button(QtGui.QDialogButtonBox.Ok) is not None
                and box.button(QtGui.QDialogButtonBox.Ok).isEnabled()
            ),
            None,
        )
        self.assertIsNotNone(button_box)

        for index, (shape_name, type_id) in enumerate(expected_shapes.items()):
            primitive_selector.setCurrentIndex(index)
            self._process_events()
            before = set(self.document.Objects)
            button_box.button(QtGui.QDialogButtonBox.Ok).click()
            self._process_events()
            created = [
                obj
                for obj in self.document.Objects
                if obj not in before and obj.isDerivedFrom("Part::Feature")
            ]
            self.assertEqual(len(created), 1, shape_name)
            result = created[0]
            self._assert_body_result(result)
            self.assertEqual(result.TypeId, type_id, shape_name)

        close_button = button_box.button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(close_button)
        close_button.click()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())

    def test_add_material_winner_continues_from_ordinary_part_result(self):
        source = self._box("PartBaseForAddMaterial")
        Gui.Selection.clearSelection()
        Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
        self.assertTrue(Gui.Control.activeDialog())
        self._accept_task_dialog()

        result = self.document.ActiveObject
        self._assert_body_result(result)
        self.assertEqual(result.TypeId, "PartDesign::AdditiveBox")
        self.assertEqual(result.BaseFeature, source)
        self.assertGreater(len(result.Shape.Solids), 0)

    def test_mesh_conversion_and_point_sampling_commands(self):
        import Mesh

        mesh = self.document.addObject("Mesh::Feature", "SourceMesh")
        mesh.Mesh = Mesh.createBox(10.0, 10.0, 10.0)
        self._select(mesh)
        self._run_modal_command("Part_ShapeFromMesh")
        converted = self.document.ActiveObject
        self._assert_document_root_result(converted)

        source = self._box("PointSource", x=20.0)
        self._select(source)
        self._run_modal_command("Part_PointsFromMesh")
        sampled = self.document.ActiveObject
        self._assert_body_result(sampled)
        self.assertGreater(len(sampled.Shape.Vertexes), 0)

    def test_multi_mesh_conversion_publishes_one_exact_semantic_block(self):
        import Mesh

        first_mesh = self.document.addObject(
            "Mesh::Feature",
            "FirstSourceMesh",
        )
        first_mesh.Mesh = Mesh.createBox(10.0, 10.0, 10.0)
        second_mesh = self.document.addObject(
            "Mesh::Feature",
            "SecondSourceMesh",
        )
        second_mesh.Mesh = Mesh.createBox(8.0, 8.0, 8.0)
        second_mesh.Placement.Base.x = 20.0
        self.document.recompute()

        before = set(self.document.Objects)
        self._select(first_mesh, second_mesh)
        self._run_modal_command("Part_ShapeFromMesh")

        created = [
            obj
            for obj in self.document.Objects
            if obj not in before
        ]
        controllers = [
            obj
            for obj in created
            if obj.TypeId == "App::DocumentObjectGroup"
            and "OperationKind" in obj.PropertiesList
            and obj.OperationKind == "Convert mesh to shape"
        ]
        self.assertEqual(len(controllers), 1)
        controller = controllers[0]
        resources = list(controller.Group)
        self.assertEqual(len(resources), 2)
        self.assertEqual(
            set(created),
            set(resources) | {controller},
        )
        self.assertEqual(controller.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(controller.VibeCADTimelineReplacedInputs),
            [first_mesh, second_mesh],
        )
        self.assertFalse(first_mesh.Visibility)
        self.assertFalse(second_mesh.Visibility)

        for resource in resources:
            self.assertEqual(resource.TypeId, "Part::Feature")
            self.assertFalse(resource.Shape.isNull())
            self.assertEqual(resource.getStatusString(), "Valid")
            self.assertEqual(resource.VibeCADTimelineRole, "resource")
            self.assertIs(resource.VibeCADTimelineOwner, controller)

        timeline = next(
            obj
            for obj in self.document.Objects
            if obj.TypeId == "App::DocumentTimeline"
        )
        operations = list(timeline.Operations)
        block = resources + [controller]
        block_start = operations.index(resources[0])
        self.assertEqual(
            operations[block_start:block_start + len(block)],
            block,
        )

    def test_offset_thickness_and_defeaturing_commands(self):
        offset_source = self._box("OffsetSource")
        self._select(offset_source)
        Gui.runCommand("Part_Offset", 0)
        offset = self.document.ActiveObject
        self._accept_task_dialog()
        self._assert_body_result(offset)

        wire_source = self._wire("Offset2DSource", x=20.0)
        self._select(wire_source)
        Gui.runCommand("Part_Offset2D", 0)
        offset_2d = self.document.ActiveObject
        self._accept_task_dialog()
        self._assert_body_result(offset_2d)

        thickness_source = self._box("ThicknessSource", x=40.0)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(thickness_source, "Face1")
        Gui.runCommand("Part_Thickness", 0)
        thickness = self.document.ActiveObject
        self._accept_task_dialog()
        self._assert_body_result(thickness)

        defeature_source = self.document.addObject("Part::Feature", "DefeatureSource")
        defeature_source.Shape = Part.makeBox(10, 10, 10, App.Vector(60, 0, 0)).cut(
            Part.makeCylinder(2, 10, App.Vector(65, 5, 0))
        )
        self.document.recompute()
        cylindrical_face = next(
            index
            for index, face in enumerate(defeature_source.Shape.Faces, start=1)
            if isinstance(face.Surface, Part.Cylinder)
        )
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(defeature_source, f"Face{cylindrical_face}")
        Gui.runCommand("Part_Defeaturing", 0)
        defeatured = self.document.ActiveObject
        self._assert_document_root_result(defeatured)
        self._assert_exact_root_replacement(
            defeatured,
            [defeature_source],
        )

    def test_loft_and_sweep_commands_create_valid_body_results(self):
        loft_lower = self._wire("LoftLower")
        loft_lower.Label = "Loft Lower Profile"
        loft_upper = self._wire("LoftUpper")
        loft_upper.Label = "Loft Upper Profile"
        loft_upper.Placement.Base.y = 10.0
        self.document.recompute()

        Gui.runCommand("Part_Loft", 0)
        self._choose_action_selector_items([loft_lower.Label, loft_upper.Label])
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

        sweep_profile = self._wire("SweepProfile", x=20.0)
        sweep_profile.Label = "Sweep Profile"
        sweep_path = self.document.addObject("Part::Feature", "SweepPath")
        sweep_path.Label = "Sweep Path"
        sweep_path.Shape = Part.makeLine(App.Vector(22, 0, 0), App.Vector(22, 15, 0))
        self.document.recompute()

        Gui.runCommand("Part_Sweep", 0)
        self._choose_action_selector_items([sweep_profile.Label])
        path_button = next(
            (
                button
                for button in Gui.getMainWindow().findChildren(QtGui.QPushButton)
                if button.objectName() == "buttonPath" and button.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(path_button)
        path_button.click()
        Gui.Selection.addSelection(sweep_path)
        path_button.click()
        self._accept_task_dialog()
        self._assert_document_root_result(self.document.ActiveObject)

    def test_canonical_model_loft_and_sweep_create_standalone_solids(self):
        from tool_impl.service import model_loft, model_sweep

        class Service:
            def __init__(self, document):
                self.document = document

            def _active_document(self):
                return self.document

        service = Service(self.document)
        loft_lower = self._wire("CanonicalLoftLower", x=60.0)
        loft_upper = self._wire("CanonicalLoftUpper", x=60.0)
        loft_upper.Placement.Base.y = 10.0
        self.document.recompute()

        loft_result = model_loft.run(
            service,
            operation="new_solid",
            profile_names=[loft_lower.Name, loft_upper.Name],
            label="Canonical Standalone Loft",
            closed=False,
            ruled=False,
            reversed=False,
            midplane=False,
            refine=False,
        )
        self.assertTrue(loft_result["ok"], loft_result)
        loft = self.document.getObject(loft_result["mutation"]["feature"])
        self._assert_body_result(loft)
        self.assertGreater(len(loft.Shape.Solids), 0)

        sweep_profile = self._wire("CanonicalSweepProfile", x=80.0)
        sweep_path = self.document.addObject("Part::Feature", "CanonicalSweepPath")
        sweep_path.Shape = Part.makeLine(
            App.Vector(82, 0, 0),
            App.Vector(82, 15, 0),
        )
        self.document.recompute()

        sweep_result = model_sweep.run(
            service,
            operation="new_solid",
            profile_name=sweep_profile.Name,
            spine_name=sweep_path.Name,
            section_names=[],
            label="Canonical Standalone Sweep",
            orientation="standard",
            transformation="constant",
            transition="right_corner",
            spine_tangent=False,
            auxiliary_spine_tangent=False,
            auxiliary_curvilinear=False,
            reversed=False,
            midplane=False,
            refine=False,
        )
        self.assertTrue(sweep_result["ok"], sweep_result)
        sweep = self.document.getObject(sweep_result["mutation"]["feature"])
        self._assert_body_result(sweep)
        self.assertGreater(len(sweep.Shape.Solids), 0)

    def test_canonical_model_revolve_uses_native_symmetric_part_feature(self):
        from tool_impl.service import model_revolve

        class Service:
            def __init__(self, document):
                self.document = document

            def _active_document(self):
                return self.document

        profile = self._wire("CanonicalRevolveProfile")
        result = model_revolve.run(
            Service(self.document),
            profile_name=profile.Name,
            operation="new_solid",
            axis={
                "source": "global",
                "point": {"x": 0.0, "y": 0.0, "z": 0.0},
                "direction": {"x": 0.0, "y": 0.0, "z": 1.0},
            },
            extent={"type": "angle", "angle_degrees": 180.0},
            midplane=True,
            reversed=False,
            label="Canonical Symmetric Revolution",
        )

        self.assertTrue(result["ok"], result)
        revolution = self.document.getObject(result["mutation"]["feature"])
        self._assert_body_result(revolution)
        self.assertTrue(revolution.Symmetric)
        self.assertGreater(len(revolution.Shape.Solids), 0)

    def test_body_mirror_winner_transforms_whole_ordinary_part_result(self):
        from tool_impl.service import model_mirror

        class Service:
            def __init__(self, document):
                self.document = document

            def _active_document(self):
                return self.document

            def _get_partdesign_body(self, body_name):
                candidate = self.document.getObject(body_name)
                return (
                    candidate
                    if getattr(candidate, "TypeId", "") == "PartDesign::Body"
                    else None
                )

            @staticmethod
            def _partdesign_body_header(body):
                return {
                    "name": body.Name,
                    "tip": getattr(getattr(body, "Tip", None), "Name", None),
                    "group": [item.Name for item in body.Group],
                }

            @staticmethod
            def _partdesign_body_for_feature(feature):
                parent = feature.getParentGeoFeatureGroup()
                return (
                    parent
                    if getattr(parent, "TypeId", "") == "PartDesign::Body"
                    else None
                )

            @staticmethod
            def _partdesign_origin_feature(body, role):
                return next(
                    (
                        item
                        for item in body.Origin.OriginFeatures
                        if getattr(item, "Role", "") == role
                        or getattr(item, "Name", "") == role
                        or getattr(item, "Label", "").replace("-", "_") == role
                    ),
                    None,
                )

        source = self._box("WholeShapeMirrorSource")
        service = Service(self.document)
        feature_mode = model_mirror.run(
            service,
            result_mode="body_features",
            feature_names=[source.Name],
            body_plane={"source": "body_origin", "plane": "YZ_Plane"},
            transform_mode="features",
            refine=True,
            label="Invalid Feature-Delta Mirror",
        )
        self.assertFalse(feature_mode["ok"])
        self.assertIn("whole_shape", feature_mode["error"])

        result = model_mirror.run(
            service,
            result_mode="body_features",
            feature_names=[source.Name],
            body_plane={"source": "body_origin", "plane": "YZ_Plane"},
            transform_mode="whole_shape",
            refine=True,
            label="Whole Shape Mirror",
        )
        self.assertTrue(result["ok"], result)
        mirrored = self.document.getObject(result["mutation"]["feature"])
        self._assert_body_result(mirrored)
        self.assertEqual(mirrored.TransformMode, "Whole shape")
        self.assertGreater(len(mirrored.Shape.Solids), 0)

    def test_cross_sections_command_creates_valid_body_result(self):
        source = self._box("CrossSectionSource")
        self._select(source)
        Gui.runCommand("Part_CrossSections", 0)
        self._accept_task_dialog()
        self._assert_body_result(self.document.ActiveObject)

    def test_shape_builder_dispatches_all_native_creation_modes(self):
        vertex_source = self._wire("BuilderVertexSource")
        edge_source = self._wire("BuilderEdgeSource", x=15.0)
        face_source = self._box("BuilderFaceSource", x=30.0)

        Gui.runCommand("Part_Builder", 0)
        self.assertTrue(Gui.Control.activeDialog())
        main_window = Gui.getMainWindow()
        create_button = next(
            (
                button
                for button in main_window.findChildren(QtGui.QPushButton)
                if button.objectName() == "createButton" and button.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(create_button)

        modes = (
            (
                "radioButtonEdgeFromVertex",
                vertex_source,
                ("Vertex1", "Vertex2"),
                "Edge",
            ),
            (
                "radioButtonWireFromEdge",
                edge_source,
                ("Edge1", "Edge2", "Edge3", "Edge4"),
                "Wire",
            ),
            (
                "radioButtonFaceFromVertex",
                vertex_source,
                ("Vertex1", "Vertex2", "Vertex3", "Vertex4"),
                "Face",
            ),
            (
                "radioButtonFaceFromEdge",
                edge_source,
                ("Edge1", "Edge2", "Edge3", "Edge4"),
                "Face",
            ),
            (
                "radioButtonShellFromFace",
                face_source,
                tuple(f"Face{index}" for index in range(1, 7)),
                "Shell",
            ),
        )

        shell_result = None
        for radio_name, source, subelements, expected_shape_type in modes:
            radio = main_window.findChild(QtGui.QRadioButton, radio_name)
            self.assertIsNotNone(radio, radio_name)
            radio.click()
            self._process_events()
            before = set(self.document.Objects)
            Gui.Selection.clearSelection()
            for subelement in subelements:
                Gui.Selection.addSelection(source, subelement)
            create_button.click()
            self._process_events()

            created = [
                obj
                for obj in self.document.Objects
                if obj not in before and obj.isDerivedFrom("Part::Feature")
            ]
            self.assertEqual(len(created), 1, radio_name)
            result = created[0]
            self._assert_body_result(result)
            self.assertEqual(result.Shape.ShapeType, expected_shape_type, radio_name)
            if expected_shape_type == "Shell":
                shell_result = result

        self.assertIsNotNone(shell_result)
        solid_radio = main_window.findChild(
            QtGui.QRadioButton,
            "radioButtonSolidFromShell",
        )
        self.assertIsNotNone(solid_radio)
        solid_radio.click()
        self._process_events()
        before = set(self.document.Objects)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(shell_result)
        create_button.click()
        self._process_events()
        created = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.isDerivedFrom("Part::Feature")
        ]
        self.assertEqual(len(created), 1)
        solid = created[0]
        self._assert_body_result(solid)
        self.assertEqual(solid.Shape.ShapeType, "Solid")

        Gui.Control.closeDialog()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())

    def test_specialized_task_tools_open_cleanly_in_part_design(self):
        left = self._box("TaskLeft")
        right = self._box("TaskRight", x=5.0)

        Gui.runCommand("Part_Primitives", 0)
        primitive_selector = next(
            (
                combo
                for combo in Gui.getMainWindow().findChildren(QtGui.QComboBox)
                if combo.objectName() == "PrimitiveTypeCB" and combo.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(primitive_selector)
        primitive_names = {
            primitive_selector.itemText(index)
            for index in range(primitive_selector.count())
        }
        self.assertEqual(
            primitive_names
            & {
                "Box",
                "Cylinder",
                "Cone",
                "Sphere",
                "Ellipsoid",
                "Torus",
                "Prism",
                "Wedge",
            },
            set(),
        )
        before_primitives = set(self.body.Group)
        primitive_button_box = next(
            (
                box
                for box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox)
                if box.isVisible()
                and box.button(QtGui.QDialogButtonBox.Ok) is not None
                and box.button(QtGui.QDialogButtonBox.Ok).isEnabled()
            ),
            None,
        )
        self.assertIsNotNone(primitive_button_box)
        primitive_button_box.button(QtGui.QDialogButtonBox.Ok).click()
        self._process_events()
        created_primitives = [
            obj for obj in self.body.Group if obj not in before_primitives
        ]
        self.assertEqual(len(created_primitives), 1)
        self._assert_body_result(created_primitives[0])
        self.assertEqual(created_primitives[0].TypeId, "Part::Plane")
        close_button = primitive_button_box.button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(close_button)
        close_button.click()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())

        self._open_and_close_task_command("Part_Builder")

        self._select(left, right)
        self._open_and_close_task_command("Part_Boolean")

        self._select(left)
        self._open_and_close_task_command("Part_CheckGeometry")
        self._select(left)
        self._open_and_close_task_command("Part_ColorPerFace")
        self._select(left)
        self._open_and_close_task_command("Part_EditAttachment")

        for command_name in (
            "Materials_InspectAppearance",
            "Materials_InspectMaterial",
        ):
            self._select(left)
            self._open_and_close_task_command(command_name)

        self._select(left)
        self._open_and_close_task_command("Part_ProjectionOnSurface")

    def test_structure_commands_create_body_binder_and_clone(self):
        Gui.Selection.clearSelection()
        bodies_before = {
            obj for obj in self.document.Objects if obj.TypeId == "PartDesign::Body"
        }
        Gui.runCommand("PartDesign_Body", 0)
        bodies_after = {
            obj for obj in self.document.Objects if obj.TypeId == "PartDesign::Body"
        }
        created_bodies = bodies_after - bodies_before
        self.assertEqual(len(created_bodies), 1)
        created_body = created_bodies.pop()
        self.assertEqual(
            Gui.activeView().getActiveObject("pdbody"),
            created_body,
        )

        source = self.document.addObject("Part::Feature", "StructureSource")
        source.Shape = Part.makeBox(10, 10, 10, App.Vector(20, 0, 0))
        self.document.recompute()

        Gui.activeView().setActiveObject("pdbody", self.body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1")
        Gui.runCommand("PartDesign_SubShapeBinder", 0)
        binder = self.document.ActiveObject
        self.assertEqual(binder.TypeId, "PartDesign::SubShapeBinder")
        self.assertEqual(binder.getParentGeoFeatureGroup(), self.body)
        self.assertIn(source, self._linked_objects(binder.Support))
        self.document.recompute()
        self.assertFalse(binder.Shape.isNull())

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        Gui.runCommand("PartDesign_Clone", 0)
        clone = self.document.ActiveObject
        clone_body = clone.getParentGeoFeatureGroup()
        self.assertEqual(clone.TypeId, "PartDesign::FeatureBase")
        self.assertIsNotNone(clone_body)
        self.assertIsNot(clone_body, self.body)
        self.assertEqual(clone_body.TypeId, "PartDesign::Body")
        self.assertEqual(clone.BaseFeature, source)
        self.document.recompute()
        self.assertFalse(clone.Shape.isNull())
        self.assertEqual(clone_body.Tip, clone)

    def test_sketch_composite_children_and_validation_use_native_dialogs(self):
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/PartDesign")
        previous = preferences.GetBool("NewSketchUseAttachmentDialog", False)
        preferences.SetBool("NewSketchUseAttachmentDialog", False)
        try:
            support = self._box("SketchSupport")
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(support, "Face6")
            Gui.runCommand("PartDesign_CompSketches", 0)
            sketch = self.document.ActiveObject
            self.assertIsNotNone(sketch)
            self.assertTrue(sketch.isDerivedFrom("Sketcher::SketchObject"))
            self.assertIn(sketch, self.body.Group)
            if Gui.activeDocument().getInEdit() is not None:
                Gui.runCommand("Sketcher_LeaveSketch", 0)
                self._process_events()
            self.assertIsNone(Gui.activeDocument().getInEdit())
            self.assertFalse(Gui.Control.activeDialog())

            original_geometry_count = sketch.GeometryCount
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(sketch)
            Gui.runCommand("PartDesign_CompSketches", 2)
            self.assertIsNotNone(Gui.activeDocument().getInEdit())
            Gui.runCommand("Sketcher_LeaveSketch", 0)
            self.assertIsNone(Gui.activeDocument().getInEdit())
            self.assertEqual(sketch.GeometryCount, original_geometry_count)

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(sketch)
            original_names = tuple(obj.Name for obj in self.document.Objects)
            Gui.runCommand("Sketcher_ValidateSketch", 0)
            self.assertTrue(Gui.Control.activeDialog())
            self._close_task_dialog()
            self.assertEqual(
                tuple(obj.Name for obj in self.document.Objects),
                original_names,
            )

            def map_sketch_to_support(mapped, selected_support, subelement=None):
                Gui.Selection.clearSelection()
                if subelement is None:
                    Gui.Selection.addSelection(selected_support)
                else:
                    Gui.Selection.addSelection(selected_support, subelement)
                accepted_dialogs = []
                handled_dialogs = set()

                def accept_map_dialogs():
                    for dialog in QtGui.QApplication.topLevelWidgets():
                        if (
                            not isinstance(dialog, QtGui.QInputDialog)
                            or not dialog.isVisible()
                            or id(dialog) in handled_dialogs
                        ):
                            continue
                        handled_dialogs.add(id(dialog))
                        accepted_dialogs.append(dialog.windowTitle())
                        combo = dialog.findChild(QtGui.QComboBox)
                        if (
                            combo is not None
                            and "Select Sketch" in dialog.windowTitle()
                        ):
                            sketch_index = combo.findText(
                                f"{mapped.Label} ({mapped.Name})"
                            )
                            self.assertGreaterEqual(sketch_index, 0, mapped.Label)
                            combo.setCurrentIndex(sketch_index)
                        elif combo is not None and "Attachment" in dialog.windowTitle():
                            suggested = next(
                                (
                                    index
                                    for index in range(combo.count())
                                    if "suggested" in combo.itemText(index).lower()
                                ),
                                None,
                            )
                            if suggested is not None:
                                combo.setCurrentIndex(suggested)
                        QtCore.QTimer.singleShot(0, dialog.accept)
                    if len(accepted_dialogs) < 2:
                        QtCore.QTimer.singleShot(20, accept_map_dialogs)

                QtCore.QTimer.singleShot(0, accept_map_dialogs)
                Gui.runCommand("PartDesign_CompSketches", 1)
                self.assertEqual(len(accepted_dialogs), 2, accepted_dialogs)
                self.assertNotEqual(mapped.MapMode, "Deactivated")
                self.assertIn(
                    selected_support,
                    self._linked_objects(mapped.AttachmentSupport),
                )

            mapped = self._profile_sketch(self.body, "MappedSketch")
            self.body.Tip = support
            map_sketch_to_support(mapped, support, "Face6")

            datum_plane = self.body.newObject(
                "PartDesign::Plane",
                "MapSketchDatumPlane",
            )
            self.document.recompute()
            datum_mapped = self._profile_sketch(
                self.body,
                "DatumMappedSketch",
                x=8.0,
            )
            map_sketch_to_support(datum_mapped, datum_plane)
        finally:
            preferences.SetBool("NewSketchUseAttachmentDialog", previous)

    def test_persistent_section_cut_is_one_editable_deletable_timeline_operation(self):
        self.body.Visibility = False
        source = self.document.addObject("Part::Box", "SectionCutSource")
        source.Length = 10
        source.Width = 12
        source.Height = 14
        self.document.recompute()

        Gui.runCommand("Part_SectionCut", 0)
        docks = [
            dock
            for dock in Gui.getMainWindow().findChildren(QtGui.QDockWidget)
            if dock.windowTitle() == "Persistent Section Cut"
        ]
        self.assertEqual(len(docks), 1)
        self.assertTrue(docks[0].isVisible())
        dialog = docks[0].widget()
        cut_x = dialog.findChild(QtGui.QGroupBox, "groupBoxX")
        keep_cut = dialog.findChild(QtGui.QCheckBox, "keepOnlyCutCB")
        self.assertIsNotNone(cut_x)
        self.assertIsNotNone(keep_cut)
        cut_x.setChecked(True)
        keep_cut.setChecked(True)
        self._process_events(100)

        owner = next(
            (
                obj
                for obj in self.document.Objects
                if obj.TypeId == "Part::Compound"
                and "VibeCADSectionCutSchema" in obj.PropertiesList
            ),
            None,
        )
        self.assertIsNotNone(owner)
        self.assertEqual(owner.VibeCADSectionCutSchema, 1)
        self.assertEqual(owner.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(owner.VibeCADTimelineReplacedInputs),
            [source],
        )
        self.assertEqual(
            owner.VibeCADTimelineEditCommand,
            "Part_SectionCut",
        )
        resources = [
            obj
            for obj in self.document.Objects
            if "VibeCADTimelineOwner" in obj.PropertiesList
            and obj.VibeCADTimelineOwner is owner
        ]
        self.assertTrue(resources)
        self.assertTrue(
            all(obj.VibeCADTimelineRole == "resource" for obj in resources)
        )
        self.assertTrue(all(not obj.Visibility for obj in resources))
        resource_names = [obj.Name for obj in resources]

        close_button = dialog.findChild(QtGui.QDialogButtonBox).button(
            QtGui.QDialogButtonBox.Close
        )
        self.assertIsNotNone(close_button)
        close_button.click()
        self._process_events(100)
        self.assertFalse(source.Visibility)
        self.assertTrue(owner.Visibility)

        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline)

        def owner_item():
            return next(
                (
                    timeline.item(row)
                    for row in range(timeline.count())
                    if timeline.item(row).data(QtCore.Qt.UserRole)
                    == owner.Name
                ),
                None,
            )

        item = self._wait_until(owner_item)
        self.assertIsNotNone(item)
        timeline.itemDoubleClicked.emit(item)
        self.assertTrue(
            self._wait_until(
                lambda: any(
                    dock.isVisible()
                    and dock.windowTitle() == "Persistent Section Cut"
                    for dock in Gui.getMainWindow().findChildren(
                        QtGui.QDockWidget
                    )
                )
            ),
            "The timeline Edit action did not reopen the persistent editor.",
        )
        reopened = next(
            dock
            for dock in Gui.getMainWindow().findChildren(QtGui.QDockWidget)
            if dock.isVisible()
            and dock.windowTitle() == "Persistent Section Cut"
        )
        reopened_dialog = reopened.widget()
        reopened_dialog.findChild(
            QtGui.QCheckBox,
            "keepOnlyCutCB",
        ).setChecked(True)
        reopened_dialog.findChild(QtGui.QDialogButtonBox).button(
            QtGui.QDialogButtonBox.Close
        ).click()
        self._process_events(100)

        owner_name = owner.Name
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(owner)
        Gui.runCommand("Std_Delete", 0)
        self._process_events(100)
        self.assertIsNone(self.document.getObject(owner_name))
        self.assertTrue(
            all(self.document.getObject(name) is None for name in resource_names)
        )
        self.assertTrue(source.Visibility)

        self.document.undo()
        self._process_events(100)
        restored_owner = self.document.getObject(owner_name)
        self.assertIsNotNone(restored_owner)
        self.assertTrue(
            all(self.document.getObject(name) is not None for name in resource_names)
        )
        self.assertFalse(source.Visibility)
        self.assertTrue(restored_owner.Visibility)

        self.document.redo()
        self._process_events(100)
        self.assertIsNone(self.document.getObject(owner_name))
        self.assertTrue(
            all(self.document.getObject(name) is None for name in resource_names)
        )
        self.assertTrue(source.Visibility)

    def test_projection_on_surface_command_creates_valid_body_result(self):
        support = self._box("ProjectionSupport")
        projected = self.document.addObject("Part::Feature", "ProjectedEdge")
        projected.Shape = Part.makeLine(App.Vector(2, 2, 15), App.Vector(8, 2, 15))
        self.document.recompute()

        Gui.runCommand("Part_ProjectionOnSurface", 0)
        result = self.document.ActiveObject
        result.SupportFace = (support, ["Face6"])
        result.Projection = [(projected, ["Edge1"])]
        result.Direction = App.Vector(0, 0, -1)
        result.Mode = "Edges"
        self.document.recompute()
        self._accept_task_dialog()
        self._assert_document_root_result(result)

    def test_box_selection_command_selects_faces_in_view(self):
        target = self._box("BoxSelectionTarget")
        view = Gui.activeDocument().activeView()
        view.viewIsometric()
        view.fitAll()
        self._process_events(50)

        graphics_view = view.graphicsView()
        viewport = graphics_view.viewport()
        _, height = view.getSize()
        scale = (
            viewport.devicePixelRatioF()
            if hasattr(viewport, "devicePixelRatioF")
            else float(viewport.devicePixelRatio())
        )
        projected = []
        for x in (target.Shape.BoundBox.XMin, target.Shape.BoundBox.XMax):
            for y in (target.Shape.BoundBox.YMin, target.Shape.BoundBox.YMax):
                for z in (target.Shape.BoundBox.ZMin, target.Shape.BoundBox.ZMax):
                    point = view.getPointOnViewport(App.Vector(x, y, z))
                    projected.append(
                        QtCore.QPoint(
                            int(round(point[0] / scale)),
                            int(round((height - point[1] - 1) / scale)),
                        )
                    )
        bounds = viewport.rect().adjusted(2, 2, -3, -3)
        rect = QtCore.QRect(
            QtCore.QPoint(
                max(bounds.left(), min(point.x() for point in projected) - 15),
                max(bounds.top(), min(point.y() for point in projected) - 15),
            ),
            QtCore.QPoint(
                min(bounds.right(), max(point.x() for point in projected) + 15),
                min(bounds.bottom(), max(point.y() for point in projected) + 15),
            ),
        )

        Gui.Selection.clearSelection()
        Gui.runCommand("Part_BoxSelection", 0)
        start = rect.topLeft()
        middle = rect.center()
        end = rect.bottomRight()
        for event_type, pos, button, buttons in (
            (QtCore.QEvent.MouseMove, start, QtCore.Qt.NoButton, QtCore.Qt.NoButton),
            (
                QtCore.QEvent.MouseButtonPress,
                start,
                QtCore.Qt.LeftButton,
                QtCore.Qt.LeftButton,
            ),
            (QtCore.QEvent.MouseMove, middle, QtCore.Qt.NoButton, QtCore.Qt.LeftButton),
            (QtCore.QEvent.MouseMove, end, QtCore.Qt.NoButton, QtCore.Qt.LeftButton),
            (
                QtCore.QEvent.MouseButtonRelease,
                end,
                QtCore.Qt.LeftButton,
                QtCore.Qt.NoButton,
            ),
        ):
            self._send_mouse_event(viewport, event_type, pos, button, buttons)
            self._process_events(10)

        selected = next(
            (item for item in Gui.Selection.getSelectionEx() if item.Object == target),
            None,
        )
        self.assertIsNotNone(selected)
        self.assertTrue(selected.SubElementNames)
        self.assertTrue(
            all(name.startswith("Face") for name in selected.SubElementNames)
        )

    def test_copy_refine_and_reverse_commands_create_valid_body_results(self):
        source = self._box("CopySource")

        for command_name in (
            "Part_SimpleCopy",
            "Part_TransformedCopy",
            "Part_RefineShape",
            "Part_ReverseShape",
        ):
            self._select(source)
            Gui.runCommand(command_name, 0)
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self._assert_body_native_timeline_result(
                self.body,
                result,
            )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1")
        Gui.runCommand("Part_ElementCopy", 0)
        element = self.document.ActiveObject
        self._assert_body_result(element)
        self.assertEqual(element.Shape.ShapeType, "Face")

    def test_root_refine_and_compound_filter_publish_exact_history(self):
        refine_source = self.document.addObject(
            "Part::Feature",
            "RootRefineSource",
        )
        refine_source.Shape = Part.makeBox(10, 10, 10)
        self.document.recompute()
        self._select(refine_source)
        Gui.runCommand("Part_RefineShape", 0)
        refined = self.document.ActiveObject
        self._assert_document_root_result(refined)
        self._assert_exact_root_replacement(
            refined,
            [refine_source],
        )

        compound_source = self.document.addObject(
            "Part::Feature",
            "RootCompoundFilterSource",
        )
        compound_source.Shape = Part.makeCompound(
            [
                Part.makeBox(
                    5,
                    5,
                    5,
                    App.Vector(20, 0, 0),
                ),
                Part.makeBox(
                    8,
                    8,
                    8,
                    App.Vector(30, 0, 0),
                ),
            ]
        )
        self.document.recompute()
        self._select(compound_source)
        Gui.runCommand("Part_CompoundFilter", 0)
        filtered = self.document.ActiveObject
        self._assert_document_root_result(filtered)
        self._assert_exact_root_replacement(
            filtered,
            [compound_source],
        )

    def test_core_boolean_commands_preserve_inputs_and_create_valid_results(self):
        for index, command_name in enumerate(("Part_Cut", "Part_Fuse", "Part_Common")):
            left = self._box(f"Left{index}", x=index * 30.0)
            right = self._box(f"Right{index}", x=index * 30.0 + 5.0)
            self._select(left, right)
            Gui.runCommand(command_name, 0)
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertIn(left, self.body.Group, command_name)
            self.assertIn(right, self.body.Group, command_name)
            self.assertEqual(self.body.Tip, result)
            self._assert_body_native_timeline_result(
                self.body,
                result,
            )

    def test_generic_boolean_dialog_dispatches_every_operation(self):
        operations = (
            ("unionButton", "Union"),
            ("diffButton", "Difference"),
            ("interButton", "Intersection"),
            ("sectionButton", "Section"),
        )
        for index, (radio_name, operation_name) in enumerate(operations):
            base_x = index * 30.0
            left = self._box(f"BooleanDialogLeft{index}", x=base_x)
            right = self._box(f"BooleanDialogRight{index}", x=base_x + 5.0)
            Gui.activeView().setActiveObject("pdbody", self.body)
            self._select(left, right)
            Gui.runCommand("Part_Boolean", 0)
            self.assertTrue(Gui.Control.activeDialog(), operation_name)

            radio = next(
                (
                    button
                    for button in Gui.getMainWindow().findChildren(
                        QtGui.QRadioButton,
                        radio_name,
                    )
                    if button.isVisible()
                ),
                None,
            )
            self.assertIsNotNone(radio, operation_name)
            radio.click()
            self._process_events()
            button_box = next(
                (
                    box
                    for box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox)
                    if box.isVisible()
                    and box.button(QtGui.QDialogButtonBox.Apply) is not None
                    and box.button(QtGui.QDialogButtonBox.Apply).isEnabled()
                ),
                None,
            )
            self.assertIsNotNone(button_box, operation_name)
            before = set(self.document.Objects)
            button_box.button(QtGui.QDialogButtonBox.Apply).click()
            self._process_events()

            created = [
                obj
                for obj in self.document.Objects
                if obj not in before and obj.isDerivedFrom("Part::Feature")
            ]
            self.assertEqual(len(created), 1, operation_name)
            result = created[0]
            self._assert_body_result(result)
            self.assertIn(left, self.body.Group, operation_name)
            self.assertIn(right, self.body.Group, operation_name)

            close_button = button_box.button(QtGui.QDialogButtonBox.Close)
            if close_button is not None and close_button.isEnabled():
                close_button.click()
            else:
                Gui.Control.closeDialog()
            self._process_events()
            self.assertFalse(Gui.Control.activeDialog(), operation_name)

    def test_join_and_split_commands_filter_non_shape_selection(self):
        invalid = self.document.addObject("App::FeaturePython", "NonShapeSelection")
        single_shape = self._box("SingleShapeOperand")
        command_names = (
            "Part_JoinConnect",
            "Part_JoinEmbed",
            "Part_JoinCutout",
            "Part_BooleanFragments",
            "Part_Slice",
            "Part_SliceApart",
            "Part_XOR",
        )

        self._select(invalid, single_shape)
        self._process_events()
        original_objects = tuple(self.document.Objects)
        for command_name in command_names:
            self.assertFalse(Gui.isCommandActive(command_name), command_name)
        self.assertEqual(tuple(self.document.Objects), original_objects)

        join_base = self._box("FilteredJoinBase", x=20.0)
        join_tool = self._box("FilteredJoinTool", x=23.0, size=4.0)
        self._select(invalid, join_base, join_tool)
        self.assertTrue(Gui.isCommandActive("Part_JoinEmbed"))
        Gui.runCommand("Part_JoinEmbed", 0)
        joined = self.document.ActiveObject
        self._assert_body_result(joined)
        self.assertEqual(joined.Base, join_base)
        self.assertEqual(joined.Tool, join_tool)

        split_base = self._box("FilteredSplitBase", x=40.0)
        split_tool = self._box("FilteredSplitTool", x=45.0)
        self._select(invalid, split_base, split_tool)
        self.assertTrue(Gui.isCommandActive("Part_Slice"))
        Gui.runCommand("Part_Slice", 0)
        sliced = self.document.ActiveObject
        self._assert_body_result(sliced)
        self.assertEqual(sliced.Base, split_base)
        self.assertEqual(list(sliced.Tools), [split_tool])

    def test_join_commands_preserve_inputs_and_create_valid_results(self):
        for index, command_name in enumerate(
            ("Part_JoinConnect", "Part_JoinEmbed", "Part_JoinCutout")
        ):
            base_x = index * 30.0
            base = self._box(f"JoinBase{index}", x=base_x)
            tool = self._box(f"JoinTool{index}", x=base_x + 3.0, size=4.0)
            self._select(base, tool)
            Gui.runCommand(command_name, 0)
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertIn(base, self.body.Group, command_name)
            self.assertIn(tool, self.body.Group, command_name)
            self._assert_body_native_timeline_result(
                self.body,
                result,
            )

    def test_split_commands_preserve_inputs_and_create_valid_results(self):
        for index, command_name in enumerate(
            ("Part_BooleanFragments", "Part_Slice", "Part_XOR")
        ):
            base_x = index * 30.0
            base = self._box(f"SplitBase{index}", x=base_x)
            tool = self._box(f"SplitTool{index}", x=base_x + 5.0)
            self._select(base, tool)
            Gui.runCommand(command_name, 0)
            result = self.document.ActiveObject
            self._assert_body_result(result)
            self.assertIn(base, self.body.Group, command_name)
            self.assertIn(tool, self.body.Group, command_name)
            self._assert_body_native_timeline_result(
                self.body,
                result,
            )

    def test_root_and_cross_body_tools_publish_exact_replacement_history(self):
        cases = (
            ("Part_Cut", "CrossBodyCut"),
            ("Part_Section", "CrossBodySection"),
            ("Part_JoinEmbed", "CrossBodyJoin"),
            ("Part_BooleanFragments", "CrossBodyFragments"),
            ("Part_Slice", "CrossBodySlice"),
            ("Part_XOR", "CrossBodyXor"),
            ("Part_ToleranceSet", "CrossBodyTolerance"),
        )
        for index, (command_name, prefix) in enumerate(cases):
            with self.subTest(command=command_name):
                base_body, base, tool_body, tool = (
                    self._cross_body_boxes(
                        prefix,
                        index * 30.0,
                    )
                )
                before_ids = {
                    obj.ID for obj in self.document.Objects
                }
                self._select(base, tool)
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
                Gui.runCommand(command_name, 0)
                self._process_events()
                created = [
                    obj
                    for obj in self.document.Objects
                    if obj.ID not in before_ids
                    and obj.isDerivedFrom("Part::Feature")
                ]
                self.assertEqual(
                    len(created),
                    1,
                    (command_name, [obj.Name for obj in created]),
                )
                result = created[0]
                self._assert_document_root_result(result)
                self._assert_exact_root_replacement(
                    result,
                    [base_body, tool_body],
                )

    def test_slice_apart_creates_independent_visible_output_bodies(self):
        base_body = self._new_body("SliceApartBaseBody")
        base = self._box("SliceApartBase")
        tool_body = self._new_body("SliceApartToolBody")
        tool = self._box("SliceApartTool", x=5.0)
        self.document.recompute()

        original_names = tuple(obj.Name for obj in self.document.Objects)
        original_base_group = tuple(base_body.Group)
        original_base_tip = base_body.Tip
        original_tool_group = tuple(tool_body.Group)
        original_tool_tip = tool_body.Tip
        original_base_visibility = base.Visibility
        original_tool_visibility = tool.Visibility
        original_bodies = {
            obj for obj in self.document.Objects if obj.TypeId == "PartDesign::Body"
        }
        original_components = {
            obj for obj in self.document.Objects if obj.TypeId == "App::Part"
        }
        self.document.UndoMode = True

        self._select(base_body, tool_body)
        Gui.runCommand("Part_SliceApart", 0)
        self.document.recompute()
        self._process_events()
        self.assertFalse(self.document.HasPendingTransaction)

        output_components = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "App::Part" and obj not in original_components
        ]
        self.assertEqual(len(output_components), 1)
        output_component = output_components[0]
        output_bodies = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::Body" and obj not in original_bodies
        ]
        self.assertGreaterEqual(len(output_bodies), 2)
        self.assertEqual(
            {obj for obj in output_component.Group if obj.TypeId == "PartDesign::Body"},
            set(output_bodies),
        )
        slice_features = [
            obj
            for obj in output_component.Group
            if getattr(getattr(obj, "Proxy", None), "Type", "") == "FeatureSlice"
        ]
        self.assertEqual(len(slice_features), 1)
        self.assertFalse(slice_features[0].ViewObject.ShowInTree)
        self.assertFalse(slice_features[0].Visibility)
        self.assertEqual(output_component.VibeCADTimelineRole, "operation")

        self.assertEqual(tuple(base_body.Group), original_base_group)
        self.assertIs(base_body.Tip, original_base_tip)
        self.assertEqual(tuple(tool_body.Group), original_tool_group)
        self.assertIs(tool_body.Tip, original_tool_tip)
        for body in output_bodies:
            self.assertEqual(len(body.Group), 1, body.Name)
            result = body.Tip
            self.assertIsNotNone(result, body.Name)
            self.assertIs(result.getParentGeoFeatureGroup(), body)
            self.assertIs(result.Base, slice_features[0])
            Gui.activeView().setActiveObject("pdbody", body)
            self._assert_body_result(result, body)
            self.assertTrue(result.Shape.isValid())
            self.assertGreater(result.Shape.Volume, 0.0)
            self.assertTrue(
                self._wait_until(
                    lambda body=body: body.Label
                    in self._visible_tree_labels()
                ),
                ("Part_SliceApart", self._visible_tree_labels()),
            )
            self.assertEqual(body.VibeCADTimelineRole, "resource")
            self.assertIs(body.VibeCADTimelineOwner, output_component)
            self.assertEqual(result.VibeCADTimelineRole, "resource")
            self.assertIs(result.VibeCADTimelineOwner, body)

        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        timeline_results = [
            item
            for index in range(timeline.count())
            if (item := timeline.item(index)).data(QtCore.Qt.UserRole)
        ]
        timeline_names = {item.data(QtCore.Qt.UserRole) for item in timeline_results}
        owned_names = {
            slice_features[0].Name,
            *(body.Name for body in output_bodies),
            *(body.Tip.Name for body in output_bodies),
        }
        self.assertIn(output_component.Name, timeline_names)
        self.assertTrue(timeline_names.isdisjoint(owned_names))

        self.assertTrue(output_component.Visibility)
        self.assertTrue(all(body.Visibility for body in output_bodies))
        self.assertTrue(all(body.Tip.Visibility for body in output_bodies))

        Gui.activeView().setActiveObject("pdbody", base_body)
        self.document.undo()
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertEqual(tuple(base_body.Group), original_base_group)
        self.assertIs(base_body.Tip, original_base_tip)
        self.assertEqual(tuple(tool_body.Group), original_tool_group)
        self.assertIs(tool_body.Tip, original_tool_tip)
        self.assertEqual(base.Visibility, original_base_visibility)
        self.assertEqual(tool.Visibility, original_tool_visibility)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_slice_apart_private_driver_follows_operation_timeline_and_reopens(self):
        base_body = self._new_body("TimelineSliceApartBaseBody")
        base = self._box("TimelineSliceApartBase")
        tool_body = self._new_body("TimelineSliceApartToolBody")
        tool = self._box("TimelineSliceApartTool", x=5.0)
        self.document.recompute()
        base_shape = base_body.Shape.exportBrepToString()
        tool_shape = tool_body.Shape.exportBrepToString()

        original_bodies = {
            obj for obj in self.document.Objects if obj.TypeId == "PartDesign::Body"
        }
        original_components = {
            obj for obj in self.document.Objects if obj.TypeId == "App::Part"
        }
        self._select(base_body, tool_body)
        Gui.runCommand("Part_SliceApart", 0)
        self.document.recompute()
        self._process_events()

        output_components = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "App::Part" and obj not in original_components
        ]
        self.assertEqual(len(output_components), 1)
        output_component = output_components[0]
        output_bodies = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::Body" and obj not in original_bodies
        ]
        self.assertGreaterEqual(len(output_bodies), 2)
        private_drivers = [
            obj
            for obj in output_component.Group
            if getattr(getattr(obj, "Proxy", None), "Type", "") == "FeatureSlice"
        ]
        self.assertEqual(len(private_drivers), 1)
        private_driver = private_drivers[0]

        self.assertEqual(output_component.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(output_component.VibeCADTimelineReplacedInputs),
            [base_body, tool_body],
        )
        self.assertEqual(
            output_component.getTypeIdOfProperty(
                "VibeCADTimelineReplacedInputs"
            ),
            "App::PropertyLinkListHidden",
        )
        self.assertIn(
            "Hidden",
            output_component.getEditorMode("VibeCADTimelineReplacedInputs"),
        )
        self.assertEqual(private_driver.VibeCADTimelineRole, "resource")
        self.assertIs(private_driver.VibeCADTimelineOwner, output_component)
        self.assertIs(output_component.VibeCADTimelineEditor, private_driver)
        self.assertEqual(
            output_component.getTypeIdOfProperty("VibeCADTimelineEditor"),
            "App::PropertyLinkHidden",
        )
        self.assertIn(
            "Hidden",
            output_component.getEditorMode("VibeCADTimelineEditor"),
        )
        self.assertEqual(
            private_driver.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        self.assertNotIn(output_component, private_driver.OutList)
        self.assertIn(
            "Hidden",
            private_driver.getEditorMode("VibeCADTimelineRole"),
        )
        self.assertIn(
            "Hidden",
            private_driver.getEditorMode("VibeCADTimelineOwner"),
        )
        owned_results = []
        for body in output_bodies:
            result = body.Tip
            self.assertIsNotNone(result)
            owned_results.extend((body, result))
            for resource in (body, result):
                self.assertEqual(resource.VibeCADTimelineRole, "resource")
                self.assertIs(
                    resource.VibeCADTimelineOwner,
                    output_component if resource is body else body,
                )
                self.assertEqual(
                    resource.getTypeIdOfProperty("VibeCADTimelineOwner"),
                    "App::PropertyLinkHidden",
                )
                self.assertNotIn(output_component, resource.OutList)
                self.assertTrue(resource.ViewObject.ShowInTree)

        timeline = self.document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        owner_boundary = operations.index(output_component) + 1
        self.assertIn(private_driver, operations)
        self.assertTrue(all(resource in operations for resource in owned_results))
        self.assertTrue(
            all(
                operations.index(body.Tip) < operations.index(body)
                for body in output_bodies
            )
        )
        self.assertTrue(
            all(
                operations.index(resource) < operations.index(output_component)
                for resource in [private_driver, *owned_results]
            )
        )

        main_window = Gui.getMainWindow()
        timeline_items = main_window.findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        previous = main_window.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        end = main_window.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        self.assertIsNotNone(timeline_items)
        self.assertIsNotNone(previous)
        self.assertIsNotNone(end)

        def visible_names():
            return {
                timeline_items.item(row).data(QtCore.Qt.UserRole)
                for row in range(timeline_items.count())
                if timeline_items.item(row).data(QtCore.Qt.UserRole)
            }

        timeline_names = visible_names()
        self.assertIn(output_component.Name, timeline_names)
        self.assertTrue(
            timeline_names.isdisjoint(
                {
                    private_driver.Name,
                    *(resource.Name for resource in owned_results),
                }
            )
        )

        end.click()
        self._process_events()
        self.assertEqual(timeline.Position, owner_boundary)
        self.assertFalse(base_body.Visibility)
        self.assertFalse(tool_body.Visibility)
        self.assertTrue(output_component.Visibility)
        self.assertTrue(all(resource.Visibility for resource in owned_results))

        previous.click()
        self._process_events()
        self.assertLess(timeline.Position, owner_boundary)
        self.assertTrue(base_body.Visibility)
        self.assertTrue(tool_body.Visibility)
        self.assertEqual(base_body.Shape.exportBrepToString(), base_shape)
        self.assertEqual(tool_body.Shape.exportBrepToString(), tool_shape)
        self.assertIs(base_body.Tip, base)
        self.assertIs(tool_body.Tip, tool)
        self.assertFalse(output_component.Visibility)
        self.assertTrue(all(not resource.Visibility for resource in owned_results))

        end.click()
        self._process_events()
        self.assertEqual(timeline.Position, owner_boundary)
        self.assertFalse(base_body.Visibility)
        self.assertFalse(tool_body.Visibility)
        self.assertTrue(output_component.Visibility)
        self.assertTrue(all(resource.Visibility for resource in owned_results))

        component_name = output_component.Name
        driver_name = private_driver.Name
        base_body_name = base_body.Name
        tool_body_name = tool_body.Name
        owned_names = [resource.Name for resource in owned_results]
        saved_position = timeline.Position
        with tempfile.TemporaryDirectory() as temporary_directory:
            saved_file = Path(temporary_directory) / "slice_apart_timeline.FCStd"
            reopened_file = (
                Path(temporary_directory) / "slice_apart_timeline_reopened.FCStd"
            )
            self.document.saveAs(str(saved_file))
            shutil.copy2(saved_file, reopened_file)
            restored_document = App.openDocument(str(reopened_file), True)
            restored_component = restored_document.getObject(component_name)
            restored_driver = restored_document.getObject(driver_name)
            restored_base_body = restored_document.getObject(base_body_name)
            restored_tool_body = restored_document.getObject(tool_body_name)
            restored_resources = [
                restored_document.getObject(name) for name in owned_names
            ]
            restored_timeline = restored_document.getObject("VibeCADTimeline")
            self.assertEqual(
                restored_component.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                restored_driver.VibeCADTimelineRole,
                "resource",
            )
            self.assertIs(
                restored_driver.VibeCADTimelineOwner,
                restored_component,
            )
            self.assertEqual(
                restored_driver.getTypeIdOfProperty("VibeCADTimelineOwner"),
                "App::PropertyLinkHidden",
            )
            self.assertNotIn(
                restored_component,
                restored_driver.OutList,
            )
            self.assertIs(
                restored_component.VibeCADTimelineEditor,
                restored_driver,
            )
            self.assertEqual(
                list(restored_component.VibeCADTimelineReplacedInputs),
                [restored_base_body, restored_tool_body],
            )
            for restored_resource in restored_resources:
                self.assertEqual(
                    restored_resource.VibeCADTimelineRole,
                    "resource",
                )
                restored_owner = (
                    restored_component
                    if restored_resource.TypeId == "PartDesign::Body"
                    else restored_resource.getParentGeoFeatureGroup()
                )
                self.assertIs(
                    restored_resource.VibeCADTimelineOwner,
                    restored_owner,
                )
            self.assertEqual(restored_timeline.Position, saved_position)
            self.assertFalse(restored_base_body.Visibility)
            self.assertFalse(restored_tool_body.Visibility)
            self.assertTrue(restored_component.Visibility)
            self.assertTrue(
                all(resource.Visibility for resource in restored_resources)
            )
            App.closeDocument(restored_document.Name)

    def test_compound_filter_command_creates_valid_result(self):
        left = self._box("CompoundToolLeft")
        right = self._box("CompoundToolRight", x=15.0)
        self._select(left, right)
        Gui.runCommand("Part_Compound", 0)
        compound = self.document.ActiveObject
        self._assert_body_result(compound)

        self._select(compound)
        Gui.runCommand("Part_CompoundFilter", 0)
        filtered = self.document.ActiveObject
        self._assert_body_result(filtered)

    def test_explode_compound_creates_independent_visible_output_bodies(self):
        source = self.document.addObject("Part::Feature", "ExplodeSource")
        source.Label = "Explode Source"
        source.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 10, 10),
                Part.makeBox(6, 6, 6, App.Vector(20, 0, 0)),
            ]
        )
        self.document.recompute()
        self.assertTrue(source.Visibility)

        original_names = tuple(obj.Name for obj in self.document.Objects)
        original_bodies = {
            obj for obj in self.document.Objects if obj.TypeId == "PartDesign::Body"
        }
        original_components = {
            obj for obj in self.document.Objects if obj.TypeId == "App::Part"
        }
        self.document.UndoMode = True

        self._select(source)
        self.assertTrue(Gui.isCommandActive("Part_ExplodeCompound"))
        Gui.runCommand("Part_ExplodeCompound", 0)
        self.document.recompute()
        self._process_events()
        self.assertFalse(self.document.HasPendingTransaction)

        output_components = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "App::Part" and obj not in original_components
        ]
        self.assertEqual(len(output_components), 1)
        output_component = output_components[0]
        output_bodies = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::Body" and obj not in original_bodies
        ]
        self.assertEqual(len(output_bodies), 2)
        self.assertEqual(
            set(output_component.Group),
            set(output_bodies),
        )
        self.assertFalse(source.Visibility)
        self.assertEqual(output_component.VibeCADTimelineRole, "operation")

        for body in output_bodies:
            self.assertEqual(len(body.Group), 1, body.Name)
            result = body.Tip
            self.assertIsNotNone(result, body.Name)
            self.assertIs(result.getParentGeoFeatureGroup(), body)
            self.assertIs(result.Base, source)
            Gui.activeView().setActiveObject("pdbody", body)
            self._assert_body_result(result, body)
            self.assertTrue(result.Shape.isValid())
            self.assertGreater(result.Shape.Volume, 0.0)
            self.assertTrue(
                self._wait_until(
                    lambda body=body: body.Label
                    in self._visible_tree_labels()
                ),
                ("Part_ExplodeCompound", self._visible_tree_labels()),
            )
            self.assertEqual(body.VibeCADTimelineRole, "resource")
            self.assertIs(body.VibeCADTimelineOwner, output_component)
            self.assertEqual(result.VibeCADTimelineRole, "resource")
            self.assertIs(result.VibeCADTimelineOwner, body)

        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        timeline_results = [
            item
            for index in range(timeline.count())
            if (item := timeline.item(index)).data(QtCore.Qt.UserRole)
        ]
        timeline_names = {item.data(QtCore.Qt.UserRole) for item in timeline_results}
        owned_names = {
            *(body.Name for body in output_bodies),
            *(body.Tip.Name for body in output_bodies),
        }
        self.assertIn(output_component.Name, timeline_names)
        self.assertTrue(timeline_names.isdisjoint(owned_names))

        self.assertTrue(output_component.Visibility)
        self.assertTrue(all(body.Visibility for body in output_bodies))
        self.assertTrue(all(body.Tip.Visibility for body in output_bodies))

        Gui.activeView().setActiveObject("pdbody", self.body)
        self.document.undo()
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertTrue(source.Visibility)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_explode_compound_outputs_follow_one_timeline_step_and_reopen(self):
        source_body = self._new_body("TimelineExplodeSourceBody")
        source = source_body.newObject(
            "Part::Feature",
            "TimelineExplodeSource",
        )
        source.Shape = Part.makeCompound(
            [
                Part.makeBox(10, 10, 10),
                Part.makeBox(6, 6, 6, App.Vector(20, 0, 0)),
            ]
        )
        self.document.recompute()
        source_shape = source_body.Shape.exportBrepToString()

        original_bodies = {
            obj for obj in self.document.Objects if obj.TypeId == "PartDesign::Body"
        }
        original_components = {
            obj for obj in self.document.Objects if obj.TypeId == "App::Part"
        }
        self._select(source_body)
        Gui.runCommand("Part_ExplodeCompound", 0)
        self.document.recompute()
        self._process_events()

        output_components = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "App::Part" and obj not in original_components
        ]
        self.assertEqual(len(output_components), 1)
        output_component = output_components[0]
        output_bodies = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::Body" and obj not in original_bodies
        ]
        self.assertEqual(len(output_bodies), 2)
        output_results = [body.Tip for body in output_bodies]
        owned_results = [*output_bodies, *output_results]

        self.assertEqual(output_component.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(output_component.VibeCADTimelineReplacedInputs),
            [source_body],
        )
        self.assertEqual(
            output_component.getTypeIdOfProperty(
                "VibeCADTimelineReplacedInputs"
            ),
            "App::PropertyLinkListHidden",
        )
        self.assertIn(
            "Hidden",
            output_component.getEditorMode("VibeCADTimelineReplacedInputs"),
        )
        for body in output_bodies:
            for resource in (body, body.Tip):
                self.assertEqual(resource.VibeCADTimelineRole, "resource")
                self.assertIs(
                    resource.VibeCADTimelineOwner,
                    output_component if resource is body else body,
                )
                self.assertEqual(
                    resource.getTypeIdOfProperty("VibeCADTimelineOwner"),
                    "App::PropertyLinkHidden",
                )
                self.assertNotIn(output_component, resource.OutList)
                self.assertTrue(resource.ViewObject.ShowInTree)

        timeline = self.document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        owner_boundary = operations.index(output_component) + 1
        self.assertTrue(all(resource in operations for resource in owned_results))
        self.assertTrue(
            all(
                operations.index(body.Tip) < operations.index(body)
                for body in output_bodies
            )
        )
        self.assertTrue(
            all(
                operations.index(resource) < operations.index(output_component)
                for resource in owned_results
            )
        )

        main_window = Gui.getMainWindow()
        timeline_items = main_window.findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        previous = main_window.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        end = main_window.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )

        def visible_names():
            return {
                timeline_items.item(row).data(QtCore.Qt.UserRole)
                for row in range(timeline_items.count())
                if timeline_items.item(row).data(QtCore.Qt.UserRole)
            }

        names = visible_names()
        self.assertIn(output_component.Name, names)
        self.assertTrue(names.isdisjoint(resource.Name for resource in owned_results))

        end.click()
        self._process_events()
        self.assertEqual(timeline.Position, owner_boundary)
        self.assertFalse(source_body.Visibility)
        self.assertTrue(output_component.Visibility)
        self.assertTrue(all(resource.Visibility for resource in owned_results))

        previous.click()
        self._process_events()
        self.assertLess(timeline.Position, owner_boundary)
        self.assertTrue(source_body.Visibility)
        self.assertIs(source_body.Tip, source)
        self.assertEqual(source_body.Shape.exportBrepToString(), source_shape)
        self.assertFalse(output_component.Visibility)
        self.assertTrue(all(not resource.Visibility for resource in owned_results))

        end.click()
        self._process_events()
        self.assertEqual(timeline.Position, owner_boundary)
        self.assertFalse(source_body.Visibility)
        self.assertTrue(output_component.Visibility)
        self.assertTrue(all(resource.Visibility for resource in owned_results))

        component_name = output_component.Name
        source_body_name = source_body.Name
        resource_names = [resource.Name for resource in owned_results]
        saved_position = timeline.Position
        with tempfile.TemporaryDirectory() as temporary_directory:
            saved_file = Path(temporary_directory) / "explode_compound_timeline.FCStd"
            reopened_file = (
                Path(temporary_directory) / "explode_compound_timeline_reopened.FCStd"
            )
            self.document.saveAs(str(saved_file))
            shutil.copy2(saved_file, reopened_file)
            restored_document = App.openDocument(str(reopened_file), True)
            restored_component = restored_document.getObject(component_name)
            restored_source_body = restored_document.getObject(source_body_name)
            restored_resources = [
                restored_document.getObject(name) for name in resource_names
            ]
            restored_timeline = restored_document.getObject("VibeCADTimeline")
            self.assertEqual(
                restored_component.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                list(restored_component.VibeCADTimelineReplacedInputs),
                [restored_source_body],
            )
            for restored_resource in restored_resources:
                self.assertEqual(
                    restored_resource.VibeCADTimelineRole,
                    "resource",
                )
                restored_owner = (
                    restored_component
                    if restored_resource.TypeId == "PartDesign::Body"
                    else restored_resource.getParentGeoFeatureGroup()
                )
                self.assertIs(
                    restored_resource.VibeCADTimelineOwner,
                    restored_owner,
                )
            self.assertEqual(restored_timeline.Position, saved_position)
            self.assertFalse(restored_source_body.Visibility)
            self.assertTrue(restored_component.Visibility)
            self.assertTrue(
                all(resource.Visibility for resource in restored_resources)
            )
            App.closeDocument(restored_document.Name)

    def test_tolerance_command_preserves_input_and_creates_valid_result(self):
        source = self._box("ToleranceSource")
        self._select(source)
        Gui.runCommand("Part_ToleranceSet", 0)
        result = self.document.ActiveObject
        self._assert_body_result(result)
        self.assertIn(source, self.body.Group)
        self._assert_body_native_timeline_result(
            self.body,
            result,
        )

    def test_compound_section_face_surface_and_solid_commands(self):
        left = self._box("CompoundLeft")
        right = self._box("CompoundRight", x=5.0)

        self._select(left, right)
        Gui.runCommand("Part_Compound", 0)
        compound = self.document.ActiveObject
        self._assert_body_result(compound)

        self._select(left, right)
        Gui.runCommand("Part_Section", 0)
        section = self.document.ActiveObject
        self._assert_body_result(section)
        self._assert_body_native_timeline_result(
            self.body,
            section,
        )

        wire = self.document.addObject("Part::Feature", "ClosedWire")
        wire.Shape = Part.makePolygon(
            [
                App.Vector(0, 0, 0),
                App.Vector(5, 0, 0),
                App.Vector(5, 5, 0),
                App.Vector(0, 5, 0),
                App.Vector(0, 0, 0),
            ]
        )
        self._select(wire)
        Gui.runCommand("Part_MakeFace", 0)
        face = self.document.ActiveObject
        self._assert_document_root_result(face)
        self.assertEqual(face.Shape.ShapeType, "Face")

        upper_wire = self.document.addObject("Part::Feature", "UpperWire")
        upper_wire.Shape = wire.Shape.copy()
        upper_wire.Placement.Base.z = 5.0
        self._select(wire, upper_wire)
        Gui.runCommand("Part_RuledSurface", 0)
        self._assert_document_root_result(self.document.ActiveObject)

        shell = self.document.addObject("Part::Feature", "BoxShell")
        shell.Shape = Part.makeShell(left.Shape.Faces)
        self._select(shell)
        Gui.runCommand("Part_MakeSolid", 0)
        solid = self.document.ActiveObject
        self._assert_document_root_result(solid)
        self.assertEqual(solid.Shape.ShapeType, "Solid")

    def test_ruled_surface_accepts_two_edges_from_one_result_feature(self):
        import PartDesignGui

        curves = self.document.addObject("Part::Feature", "RuledSurfaceCurves")
        curves.Shape = Part.makeCompound(
            [
                Part.makeLine(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
                Part.makeLine(App.Vector(0, 0, 5), App.Vector(10, 0, 5)),
            ]
        )
        self.document.recompute()
        PartDesignGui.adoptPartResult(curves)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(curves, "Edge1")
        Gui.Selection.addSelection(curves, "Edge2")
        selection = Gui.Selection.getSelectionEx()
        self.assertEqual(len(selection), 1)
        self.assertEqual(selection[0].SubElementNames, ("Edge1", "Edge2"))

        Gui.runCommand("Part_RuledSurface", 0)
        result = self.document.ActiveObject
        self._assert_body_result(result)
        self.assertEqual(result.Curve1[0], curves)
        self.assertEqual(result.Curve1[1], ["Edge1"])
        self.assertEqual(result.Curve2[0], curves)
        self.assertEqual(result.Curve2[1], ["Edge2"])

    def test_modeling_selection_resolves_only_direct_bodies_to_their_tip(self):
        import PartGui

        self.assertIsNone(PartGui.resolveModelingObject(self.body))

        tip = self._native_pad(self.body, "ResolverPad")
        self.assertEqual(PartGui.resolveModelingObject(self.body), tip)
        self.assertEqual(PartGui.resolveModelingObject(tip), tip)

        occurrence = self.document.addObject("App::Link", "BodyOccurrence")
        occurrence.LinkedObject = self.body
        self.document.recompute()
        self.assertEqual(PartGui.resolveModelingObject(occurrence), occurrence)

    def test_transformed_copy_preserves_link_occurrence_placement(self):
        source = self._native_pad(self.body, "OccurrenceSource")
        occurrence = self.document.addObject("App::Link", "PlacedOccurrence")
        occurrence.LinkedObject = self.body
        occurrence.Placement.Base = App.Vector(25, 7, 3)
        self.document.recompute()

        self._select(occurrence)
        self.assertTrue(Gui.isCommandActive("Part_TransformedCopy"))
        Gui.runCommand("Part_TransformedCopy", 0)

        result = self.document.ActiveObject
        self._assert_document_root_result(result)
        source_bounds = source.Shape.BoundBox
        result_bounds = result.Shape.BoundBox
        self.assertAlmostEqual(result_bounds.XMin, source_bounds.XMin + 25)
        self.assertAlmostEqual(result_bounds.YMin, source_bounds.YMin + 7)
        self.assertAlmostEqual(result_bounds.ZMin, source_bounds.ZMin + 3)
        self.assertNotIn(self.body, result.OutList)
        self.assertNotIn(occurrence, result.OutList)

    def test_body_selected_offset_links_the_previous_tip_and_cancel_rolls_back(self):
        source = self._native_pad(self.body, "OffsetBodySource")
        original_names = tuple(obj.Name for obj in self.document.Objects)
        original_group = tuple(self.body.Group)
        original_tip = self.body.Tip

        self._select(self.body)
        self.assertTrue(Gui.isCommandActive("Part_Offset"))
        Gui.runCommand("Part_Offset", 0)
        self.assertTrue(Gui.Control.activeDialog())
        preview = self.document.ActiveObject
        self.assertEqual(preview.Source, source)
        self.assertNotIn(self.body, preview.OutList)
        self._cancel_task_dialog()

        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects), original_names
        )
        self.assertEqual(tuple(self.body.Group), original_group)
        self.assertEqual(self.body.Tip, original_tip)

        self._select(self.body)
        Gui.runCommand("Part_Offset", 0)
        result = self.document.ActiveObject
        self.assertEqual(result.Source, source)
        self.assertNotIn(self.body, result.OutList)
        self._accept_task_dialog()
        self._assert_body_result(result)
        self.assertEqual(result.Source, source)

    def test_body_subelement_selection_preserves_edges_for_ruled_surface(self):
        import PartDesignGui

        curves_body = self._new_body("BodySelectedCurves")
        curves = self.document.addObject("Part::Feature", "BodySelectedCurveTip")
        curves.Shape = Part.makeCompound(
            [
                Part.makeLine(App.Vector(0, 0, 0), App.Vector(10, 0, 0)),
                Part.makeLine(App.Vector(0, 0, 5), App.Vector(10, 0, 5)),
            ]
        )
        self.document.recompute()
        PartDesignGui.adoptPartResult(curves)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(curves_body, "Edge1")
        Gui.Selection.addSelection(curves_body, "Edge2")
        self.assertTrue(Gui.isCommandActive("Part_RuledSurface"))
        Gui.runCommand("Part_RuledSurface", 0)

        result = self.document.ActiveObject
        self._assert_body_result(result, curves_body)
        self.assertEqual(result.Curve1[0], curves)
        self.assertEqual(result.Curve1[1], ["Edge1"])
        self.assertEqual(result.Curve2[0], curves)
        self.assertEqual(result.Curve2[1], ["Edge2"])
        self.assertNotIn(curves_body, result.OutList)

    def test_body_operands_are_deduplicated_and_python_tools_receive_tips(self):
        import BOPTools.JoinFeatures as JoinFeatures
        import BOPTools.SplitFeatures as SplitFeatures
        from CompoundTools import _CommandCompoundFilter as CompoundFilterCommand
        from CompoundTools import _CommandExplodeCompound as ExplodeCompoundCommand

        first_body = self._new_body("FirstOperandBody")
        first_tip = self._box("FirstOperand", x=0.0, size=10.0)
        second_body = self._new_body("SecondOperandBody")
        second_tip = self._box("SecondOperand", x=2.0, size=4.0)

        self._select(first_body, first_tip)
        for command_name in ("Part_Cut", "Part_Fuse", "Part_Common", "Part_Section"):
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        self._select(first_body, second_body)
        for command_name in ("Part_Cut", "Part_Fuse", "Part_Common", "Part_Section"):
            self.assertTrue(Gui.isCommandActive(command_name), command_name)

        expected = [first_tip, second_tip]
        self.assertEqual(JoinFeatures._selected_shape_objects(), expected)
        self.assertEqual(SplitFeatures._selected_shape_objects(), expected)
        self.assertEqual(
            CompoundFilterCommand._selected_modeling_objects(),
            expected,
        )
        self.assertEqual(
            ExplodeCompoundCommand._selected_modeling_objects(),
            expected,
        )

        Gui.runCommand("Part_Section", 0)
        section = self.document.ActiveObject
        self.document.recompute()
        self.assertEqual(section.Base, first_tip)
        self.assertEqual(section.Tool, second_tip)
        self.assertNotIn(first_body, section.OutList)
        self.assertNotIn(second_body, section.OutList)
