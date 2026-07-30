# SPDX-License-Identifier: LGPL-2.1-or-later

"""Accepted-output contracts for retained single-input Part ribbon tools."""

import math
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
from PySide import QtCore, QtGui


class TestPartSingleInputRibbonTools(unittest.TestCase):
    """Body-row inputs must create one valid, undoable result feature."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("PartSingleInputRibbonTools")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self._process_events()

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
            self._process_events()
        if App.getDocument("PartSingleInputRibbonTools") is not None:
            App.closeDocument("PartSingleInputRibbonTools")
        self._process_events()

    @staticmethod
    def _process_events(wait_ms=20):
        Gui.updateGui()
        application = QtGui.QApplication.instance()
        if application is not None:
            application.processEvents()
        if wait_ms:
            loop = QtCore.QEventLoop()
            QtCore.QTimer.singleShot(wait_ms, loop.quit)
            loop.exec()

    def _body_with_shape(self, name, shape):
        body = self.document.addObject("PartDesign::Body", name)
        feature = body.newObject("PartDesign::Feature", f"{name}Source")
        feature.Shape = shape
        body.Tip = feature
        Gui.activeView().setActiveObject("pdbody", body)
        self.document.recompute()
        self.assertTrue(feature.isValid(), feature.getStatusString())
        self.assertFalse(feature.Shape.isNull())
        return body, feature

    @staticmethod
    def _closed_wire(points):
        return Part.makePolygon([*points, points[0]])

    def _select(self, obj):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(obj)
        self._process_events()

    def _select_subelement(self, obj, subelement, point):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(
            obj,
            subelement,
            point.x,
            point.y,
            point.z,
        )
        self._process_events()

    def _run_task(self, command_name):
        self.assertTrue(Gui.isCommandActive(command_name), command_name)
        Gui.runCommand(command_name, 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog(), command_name)

    def _visible_widget(self, widget_type, object_name):
        widgets = [
            widget
            for widget in Gui.getMainWindow().findChildren(
                widget_type,
                object_name,
            )
            if widget.isVisible()
        ]
        self.assertEqual(len(widgets), 1, object_name)
        return widgets[0]

    def _assert_preselected_row(self, tree_name, expected_object):
        tree = self._visible_widget(QtGui.QTreeWidget, tree_name)
        selected = tree.selectedItems()
        self.assertEqual(len(selected), 1)
        self.assertEqual(
            str(selected[0].data(0, QtCore.Qt.UserRole)),
            expected_object.Name,
        )

    def _accept_task(self, command_name):
        button = None
        for button_box in Gui.getMainWindow().findChildren(
            QtGui.QDialogButtonBox
        ):
            if not button_box.isVisible():
                continue
            candidate = button_box.button(QtGui.QDialogButtonBox.Ok)
            if (
                candidate is not None
                and candidate.isVisible()
                and candidate.isEnabled()
            ):
                button = candidate
                break
        self.assertIsNotNone(button, command_name)
        button.click()
        self._process_events(50)
        self.assertFalse(Gui.Control.activeDialog(), command_name)
        self.assertFalse(self.document.HasPendingTransaction, command_name)

    def _task_button(self, standard_button):
        for button_box in Gui.getMainWindow().findChildren(
            QtGui.QDialogButtonBox
        ):
            if not button_box.isVisible():
                continue
            button = button_box.button(standard_button)
            if button is not None and button.isVisible() and button.isEnabled():
                return button
        return None

    def _dismiss_task(self, command_name):
        button = self._task_button(QtGui.QDialogButtonBox.Cancel)
        if button is None:
            button = self._task_button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(button, command_name)
        button.click()
        self._process_events(50)
        self.assertFalse(Gui.Control.activeDialog(), command_name)
        self.assertFalse(self.document.HasPendingTransaction, command_name)

    def _click_with_error_dismissed(self, button):
        attempts = [0]
        active = [True]

        def dismiss():
            if not active[0]:
                return
            attempts[0] += 1
            boxes = [
                widget
                for widget in QtGui.QApplication.topLevelWidgets()
                if isinstance(widget, QtGui.QMessageBox)
                and widget.isVisible()
            ]
            if boxes:
                active[0] = False
                boxes[0].accept()
            elif attempts[0] < 100:
                QtCore.QTimer.singleShot(10, dismiss)

        QtCore.QTimer.singleShot(0, dismiss)
        button.click()
        active[0] = False
        self._process_events(50)

    def _snapshot(self, body):
        objects = tuple(self.document.Objects)
        return (
            tuple(obj.Name for obj in objects),
            tuple(body.Group),
            body.Tip,
            tuple(
                (obj.Name, bool(obj.ViewObject.Visibility))
                for obj in objects
                if getattr(obj, "ViewObject", None) is not None
            ),
        )

    def _interaction_snapshot(self):
        selection = tuple(
            (
                item.Object,
                tuple(item.SubElementNames),
                tuple(
                    (point.x, point.y, point.z)
                    for point in item.PickedPoints
                ),
            )
            for item in Gui.Selection.getSelectionEx()
        )
        return (
            tuple(self.document.Objects),
            tuple(
                (
                    obj,
                    bool(obj.Visibility),
                    bool(obj.ViewObject.Visibility),
                )
                for obj in self.document.Objects
                if getattr(obj, "ViewObject", None) is not None
            ),
            self.document.ActiveObject,
            Gui.activeView().getActiveObject("pdbody"),
            selection,
            bool(self.document.HasPendingTransaction),
        )

    def _created_result(self, previous_names, expected_type):
        created = [
            obj
            for obj in self.document.Objects
            if obj.Name not in previous_names
        ]
        self.assertEqual(
            len(created),
            1,
            tuple((obj.Name, obj.TypeId) for obj in created),
        )
        result = created[0]
        self.assertEqual(result.TypeId, expected_type)
        self.assertTrue(result.isValid(), result.getStatusString())
        self.assertFalse(result.Shape.isNull())
        self.assertTrue(result.Shape.isValid())
        return result

    def _assert_same_body_result(self, body, result):
        self.assertIs(result.getParentGeoFeatureGroup(), body)
        self.assertIn(result, body.Group)
        self.assertIs(body.Tip, result)
        self.assertEqual(len(result.Shape.Solids), 1)
        self.assertGreater(result.Shape.Volume, 0.0)

    def _assert_one_step_undo(self, body, result_name, expected):
        self.document.undo()
        self._process_events(50)
        self.document.recompute()
        self.assertIsNone(self.document.getObject(result_name))
        self.assertEqual(self._snapshot(body), expected)
        self.assertFalse(self.document.HasPendingTransaction)

    def _assert_bounds(self, shape, expected, places=7, delta=None):
        bounds = shape.BoundBox
        for attribute, value in expected.items():
            tolerance = (
                {"places": places}
                if delta is None
                else {"delta": delta}
            )
            self.assertAlmostEqual(
                getattr(bounds, attribute),
                value,
                msg=attribute,
                **tolerance,
            )

    def test_mirror_accepts_body_row_and_undoes_as_one_feature(self):
        body, source = self._body_with_shape(
            "MirrorBody",
            Part.makeBox(3, 4, 5, App.Vector(1, 2, 2)),
        )
        self._select(body)
        expected = self._snapshot(body)
        previous_names = set(expected[0])

        self._run_task("Part_Mirror")
        self._assert_preselected_row("shapes", source)
        self._accept_task("Part_Mirror")

        result = self._created_result(previous_names, "Part::Mirroring")
        self.assertIs(result.Source, source)
        self._assert_same_body_result(body, result)
        self.assertAlmostEqual(result.Shape.Volume, source.Shape.Volume, places=7)
        self._assert_bounds(
            result.Shape,
            {
                "XMin": 1.0,
                "XMax": 4.0,
                "YMin": 2.0,
                "YMax": 6.0,
                "ZMin": -7.0,
                "ZMax": -2.0,
            },
        )
        self.assertTrue(body.ViewObject.Visibility)
        self.assertFalse(source.ViewObject.Visibility)
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            result.PropertiesList,
        )
        self._assert_one_step_undo(body, result.Name, expected)

    def test_root_mirror_replaces_its_exact_visible_source(self):
        Gui.activeView().setActiveObject("pdbody", None)
        source = self.document.addObject("Part::Box", "RootMirrorSource")
        source.Length = 3.0
        source.Width = 4.0
        source.Height = 5.0
        source.Placement.Base = App.Vector(1, 2, 2)
        self.document.recompute()
        previous_names = {obj.Name for obj in self.document.Objects}

        self._select(source)
        self._run_task("Part_Mirror")
        self._assert_preselected_row("shapes", source)
        self._accept_task("Part_Mirror")

        result = self._created_result(previous_names, "Part::Mirroring")
        self.assertIs(result.Source, source)
        self.assertIsNone(result.getParentGeoFeatureGroup())
        self.assertTrue(result.ViewObject.Visibility)
        self.assertFalse(source.ViewObject.Visibility)
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertEqual(
            list(result.VibeCADTimelineReplacedInputs),
            [source],
        )

    def test_mirror_task_rejects_a_same_name_replacement_source(self):
        Gui.activeView().setActiveObject("pdbody", None)
        source = self.document.addObject(
            "Part::Box",
            "ReplaceMirrorSource",
        )
        source.Length = 3.0
        source.Width = 4.0
        source.Height = 5.0
        self.document.recompute()
        source_name = source.Name

        self._select(source)
        self._run_task("Part_Mirror")
        self._assert_preselected_row("shapes", source)

        self.document.removeObject(source_name)
        replacement = self.document.addObject("Part::Box", source_name)
        replacement.Length = 7.0
        replacement.Width = 2.0
        replacement.Height = 1.0
        self.document.recompute()
        before_accept = tuple(self.document.Objects)

        ok_button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(ok_button)
        self._click_with_error_dismissed(ok_button)

        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(tuple(self.document.Objects), before_accept)
        self.assertIs(self.document.getObject(source_name), replacement)
        self.assertFalse(
            any(
                obj.TypeId == "Part::Mirroring"
                for obj in self.document.Objects
            )
        )
        self._dismiss_task("Part_Mirror")

    def test_scale_accepts_body_row_and_undoes_as_one_feature(self):
        body, source = self._body_with_shape(
            "ScaleBody",
            Part.makeBox(3, 4, 5, App.Vector(2, 3, 4)),
        )
        self._select(body)
        expected = self._snapshot(body)
        previous_names = set(expected[0])

        self._run_task("Part_Scale")
        self._assert_preselected_row("treeWidget", source)
        factor = self._visible_widget(
            QtGui.QDoubleSpinBox,
            "dsbUniformScale",
        )
        factor.setValue(2.0)
        self._accept_task("Part_Scale")

        result = self._created_result(previous_names, "Part::Scale")
        self.assertIs(result.Base, source)
        self.assertTrue(result.Uniform)
        self.assertAlmostEqual(result.UniformScale, 2.0, places=7)
        self._assert_same_body_result(body, result)
        self.assertAlmostEqual(
            result.Shape.Volume,
            source.Shape.Volume * 8.0,
            places=7,
        )
        self._assert_bounds(
            result.Shape,
            {
                "XMin": 4.0,
                "XMax": 10.0,
                "YMin": 6.0,
                "YMax": 14.0,
                "ZMin": 8.0,
                "ZMax": 18.0,
            },
        )
        self._assert_one_step_undo(body, result.Name, expected)

    def test_extrude_accepts_body_row_and_undoes_as_one_feature(self):
        wire = self._closed_wire(
            (
                App.Vector(1, 2, 0),
                App.Vector(5, 2, 0),
                App.Vector(5, 5, 0),
                App.Vector(1, 5, 0),
            )
        )
        body, source = self._body_with_shape("ExtrudeBody", wire)
        self._select(body)
        expected = self._snapshot(body)
        previous_names = set(expected[0])

        self._run_task("Part_Extrude")
        self._assert_preselected_row("treeWidget", source)
        normal = self._visible_widget(
            QtGui.QRadioButton,
            "rbDirModeNormal",
        )
        solid = self._visible_widget(QtGui.QCheckBox, "chkSolid")
        self.assertTrue(normal.isChecked())
        self.assertTrue(solid.isChecked())
        self._accept_task("Part_Extrude")

        result = self._created_result(previous_names, "Part::Extrusion")
        self.assertIs(result.Base, source)
        self.assertEqual(result.DirMode, "Normal")
        self.assertTrue(result.Solid)
        self.assertAlmostEqual(result.LengthFwd.Value, 10.0, places=7)
        self._assert_same_body_result(body, result)
        self.assertAlmostEqual(result.Shape.Volume, 120.0, places=7)
        self._assert_bounds(
            result.Shape,
            {
                "XMin": 1.0,
                "XMax": 5.0,
                "YMin": 2.0,
                "YMax": 5.0,
                "ZMin": 0.0,
                "ZMax": 10.0,
            },
        )
        self._assert_one_step_undo(body, result.Name, expected)

    def test_revolve_accepts_body_row_and_undoes_as_one_feature(self):
        wire = self._closed_wire(
            (
                App.Vector(2, 0, 0),
                App.Vector(4, 0, 0),
                App.Vector(4, 0, 5),
                App.Vector(2, 0, 5),
            )
        )
        body, source = self._body_with_shape("RevolveBody", wire)
        self._select(body)
        expected = self._snapshot(body)
        previous_names = set(expected[0])

        self._run_task("Part_Revolve")
        self._assert_preselected_row("treeWidget", source)
        solid = self._visible_widget(QtGui.QCheckBox, "checkSolid")
        self.assertTrue(solid.isChecked())
        self._accept_task("Part_Revolve")

        result = self._created_result(previous_names, "Part::Revolution")
        self.assertIs(result.Source, source)
        self.assertTrue(result.Solid)
        self.assertAlmostEqual(result.Angle, 360.0, places=7)
        self._assert_same_body_result(body, result)
        self.assertAlmostEqual(
            result.Shape.Volume,
            math.pi * (4.0**2 - 2.0**2) * 5.0,
            places=6,
        )
        self._assert_bounds(
            result.Shape,
            {
                "XMin": -4.0,
                "XMax": 4.0,
                "YMin": -4.0,
                "YMax": 4.0,
                "ZMin": 0.0,
                "ZMax": 5.0,
            },
            # OCC's cached triangulation bounds undershoot the analytic
            # circular extrema at the default display deflection.
            delta=0.01,
        )
        self._assert_one_step_undo(body, result.Name, expected)

    def test_scale_preserves_a_transformed_link_occurrence(self):
        body, source = self._body_with_shape(
            "LinkedScaleBody",
            Part.makeBox(2, 3, 4),
        )
        occurrence = self.document.addObject("App::Link", "ScaleOccurrence")
        occurrence.LinkedObject = body
        occurrence.Placement = App.Placement(
            App.Vector(25, 7, 3),
            App.Rotation(),
        )
        self.document.recompute()
        self._select_subelement(
            occurrence,
            "Face1",
            App.Vector(25.5, 7.5, 3.0),
        )
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            body,
        )
        expected = self._snapshot(body)
        previous_names = set(expected[0])

        self._run_task("Part_Scale")
        self._assert_preselected_row("treeWidget", occurrence)
        factor = self._visible_widget(
            QtGui.QDoubleSpinBox,
            "dsbUniformScale",
        )
        factor.setValue(2.0)
        self._accept_task("Part_Scale")

        result = self._created_result(previous_names, "Part::Scale")
        self.assertIs(result.Base, occurrence)
        # Scale prepares the result inside its modeling attempt and the
        # generic task lifecycle prepares it again after accept(). Neither
        # pass may reinterpret a root occurrence as belonging to the still
        # active definition Body.
        self.assertIsNone(result.getParentGeoFeatureGroup())
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            body,
        )
        self.assertEqual(tuple(body.Group), expected[1])
        self.assertIs(body.Tip, source)
        self.assertEqual(len(result.Shape.Solids), 1)
        self.assertAlmostEqual(result.Shape.Volume, 192.0, places=7)
        self._assert_bounds(
            result.Shape,
            {
                # Scale acts in the selected occurrence's local coordinate
                # system. Its placement remains the occurrence placement.
                "XMin": 25.0,
                "XMax": 29.0,
                "YMin": 7.0,
                "YMax": 13.0,
                "ZMin": 3.0,
                "ZMax": 11.0,
            },
        )
        self._assert_one_step_undo(body, result.Name, expected)

    def test_shape_only_copy_uses_its_exact_same_body_operand(self):
        body, source = self._body_with_shape(
            "BodyCopySourceBody",
            Part.makeBox(3, 4, 5, App.Vector(2, 3, 4)),
        )
        self._select(body)
        expected = self._snapshot(body)
        previous_names = set(expected[0])

        Gui.runCommand("Part_SimpleCopy", 0)
        self._process_events(50)

        result = self._created_result(previous_names, "Part::Feature")
        self._assert_same_body_result(body, result)
        self.assertAlmostEqual(result.Shape.Volume, source.Shape.Volume, places=7)
        self._assert_bounds(
            result.Shape,
            {
                "XMin": 2.0,
                "XMax": 5.0,
                "YMin": 3.0,
                "YMax": 7.0,
                "ZMin": 4.0,
                "ZMax": 9.0,
            },
        )
        self._assert_one_step_undo(body, result.Name, expected)

    def test_shape_only_copy_keeps_root_occurrence_out_of_active_body(self):
        body, source = self._body_with_shape(
            "RootCopyDefinitionBody",
            Part.makeBox(2, 3, 4),
        )
        occurrence = self.document.addObject(
            "App::Link",
            "RootCopyOccurrence",
        )
        occurrence.LinkedObject = body
        occurrence.Placement = App.Placement(
            App.Vector(25, 7, 3),
            App.Rotation(),
        )
        self.document.recompute()
        self._select(occurrence)
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            body,
        )
        expected = self._snapshot(body)
        previous_names = set(expected[0])

        Gui.runCommand("Part_SimpleCopy", 0)
        self._process_events(50)

        result = self._created_result(previous_names, "Part::Feature")
        self.assertIsNone(result.getParentGeoFeatureGroup())
        self.assertEqual(tuple(body.Group), expected[1])
        self.assertIs(body.Tip, source)
        self._assert_bounds(
            result.Shape,
            {
                "XMin": 25.0,
                "XMax": 27.0,
                "YMin": 7.0,
                "YMax": 10.0,
                "ZMin": 3.0,
                "ZMax": 7.0,
            },
        )
        self._assert_one_step_undo(body, result.Name, expected)

    def test_all_retained_tools_accept_into_the_body_without_undo(self):
        self.document.UndoMode = False
        cases = (
            (
                "Part_Mirror",
                "Part::Mirroring",
                Part.makeBox(3, 4, 5, App.Vector(1, 2, 2)),
                None,
            ),
            (
                "Part_Scale",
                "Part::Scale",
                Part.makeBox(3, 4, 5, App.Vector(2, 3, 4)),
                ("dsbUniformScale", 2.0),
            ),
            (
                "Part_Extrude",
                "Part::Extrusion",
                self._closed_wire(
                    (
                        App.Vector(1, 2, 0),
                        App.Vector(5, 2, 0),
                        App.Vector(5, 5, 0),
                        App.Vector(1, 5, 0),
                    )
                ),
                None,
            ),
            (
                "Part_Revolve",
                "Part::Revolution",
                self._closed_wire(
                    (
                        App.Vector(2, 0, 0),
                        App.Vector(4, 0, 0),
                        App.Vector(4, 0, 5),
                        App.Vector(2, 0, 5),
                    )
                ),
                None,
            ),
        )

        for index, (command, result_type, shape, setting) in enumerate(cases):
            with self.subTest(command=command):
                body, source = self._body_with_shape(
                    f"NoUndoBody{index}",
                    shape,
                )
                self._select(body)
                previous_names = {obj.Name for obj in self.document.Objects}
                self._run_task(command)
                if setting is not None:
                    object_name, value = setting
                    self._visible_widget(
                        QtGui.QDoubleSpinBox,
                        object_name,
                    ).setValue(value)
                self._accept_task(command)

                result = self._created_result(previous_names, result_type)
                self._assert_same_body_result(body, result)
                self.assertEqual(tuple(body.Group), (source, result))
                self.assertFalse(source.ViewObject.Visibility)
                self.assertTrue(result.ViewObject.Visibility)
                self.assertFalse(
                    [
                        obj
                        for obj in self.document.Objects
                        if obj.isDerivedFrom("Part::Feature")
                        and not obj.isDerivedFrom("PartDesign::Body")
                        and obj.getParentGeoFeatureGroup() is None
                    ],
                    command,
                )

    def test_cancel_restores_exact_interaction_state_with_or_without_undo(self):
        cases = (
            (
                "Part_Mirror",
                Part.makeBox(3, 4, 5),
                "Face1",
                App.Vector(0.5, 0.5, 0.0),
            ),
            (
                "Part_Scale",
                Part.makeBox(3, 4, 5),
                "Face1",
                App.Vector(0.5, 0.5, 0.0),
            ),
            (
                "Part_Extrude",
                self._closed_wire(
                    (
                        App.Vector(0, 0, 0),
                        App.Vector(4, 0, 0),
                        App.Vector(4, 3, 0),
                        App.Vector(0, 3, 0),
                    )
                ),
                "Edge1",
                App.Vector(2.0, 0.0, 0.0),
            ),
            (
                "Part_Revolve",
                self._closed_wire(
                    (
                        App.Vector(2, 0, 0),
                        App.Vector(4, 0, 0),
                        App.Vector(4, 0, 5),
                        App.Vector(2, 0, 5),
                    )
                ),
                "Edge1",
                App.Vector(3.0, 0.0, 0.0),
            ),
        )

        for undo_enabled in (True, False):
            self.document.UndoMode = undo_enabled
            for index, (command, shape, subelement, point) in enumerate(cases):
                with self.subTest(
                    undo_enabled=undo_enabled,
                    command=command,
                ):
                    body, source = self._body_with_shape(
                        f"Cancel{int(undo_enabled)}Body{index}",
                        shape,
                    )
                    self.assertIs(self.document.ActiveObject, source)
                    self._select_subelement(body, subelement, point)
                    expected = self._interaction_snapshot()
                    self._run_task(command)
                    self._dismiss_task(command)
                    self.assertEqual(
                        self._interaction_snapshot(),
                        expected,
                        command,
                    )
                    self.assertIs(
                        self.document.getObject(source.Name),
                        source,
                        command,
                    )

    def test_failed_scale_apply_is_atomic_with_or_without_undo(self):
        for undo_enabled in (True, False):
            with self.subTest(undo_enabled=undo_enabled):
                self.document.UndoMode = undo_enabled
                body, source = self._body_with_shape(
                    f"FailedScale{int(undo_enabled)}Body",
                    Part.makeBox(3, 4, 5),
                )
                self.assertIs(self.document.ActiveObject, source)
                self._select_subelement(
                    body,
                    "Face1",
                    App.Vector(0.5, 0.5, 0.0),
                )
                command_state = self._interaction_snapshot()
                self._run_task("Part_Scale")
                factor = self._visible_widget(
                    QtGui.QDoubleSpinBox,
                    "dsbUniformScale",
                )
                factor.setValue(0.0)
                attempt_state = self._interaction_snapshot()

                apply_button = self._task_button(
                    QtGui.QDialogButtonBox.Apply
                )
                self.assertIsNotNone(apply_button)
                self._click_with_error_dismissed(apply_button)

                self.assertTrue(Gui.Control.activeDialog())
                self.assertEqual(
                    self._interaction_snapshot(),
                    attempt_state,
                )
                self.assertEqual(
                    tuple(self.document.Objects),
                    attempt_state[0],
                )
                self.assertIs(self.document.getObject(source.Name), source)

                self._dismiss_task("Part_Scale")
                self.assertEqual(
                    self._interaction_snapshot(),
                    command_state,
                )

    def test_failed_revolve_ok_is_atomic_with_or_without_undo(self):
        for undo_enabled in (True, False):
            with self.subTest(undo_enabled=undo_enabled):
                self.document.UndoMode = undo_enabled
                wire = self._closed_wire(
                    (
                        App.Vector(2, 0, 0),
                        App.Vector(4, 0, 0),
                        App.Vector(4, 0, 5),
                        App.Vector(2, 0, 5),
                    )
                )
                body, source = self._body_with_shape(
                    f"FailedRevolve{int(undo_enabled)}Body",
                    wire,
                )
                self.assertIs(self.document.ActiveObject, source)
                self._select_subelement(
                    body,
                    "Edge1",
                    App.Vector(3.0, 0.0, 0.0),
                )
                command_state = self._interaction_snapshot()
                self._run_task("Part_Revolve")
                angle = self._visible_widget(
                    QtGui.QAbstractSpinBox,
                    "angle",
                )
                self.assertTrue(angle.setProperty("rawValue", 0.0))
                attempt_state = self._interaction_snapshot()

                ok_button = self._task_button(QtGui.QDialogButtonBox.Ok)
                self.assertIsNotNone(ok_button)
                self._click_with_error_dismissed(ok_button)

                self.assertTrue(Gui.Control.activeDialog())
                self.assertEqual(
                    self._interaction_snapshot(),
                    attempt_state,
                )
                self.assertEqual(
                    tuple(self.document.Objects),
                    attempt_state[0],
                )
                self.assertIs(self.document.getObject(source.Name), source)

                self._dismiss_task("Part_Revolve")
                self.assertEqual(
                    self._interaction_snapshot(),
                    command_state,
                )

    def test_failed_extrude_apply_is_atomic_with_or_without_undo(self):
        for undo_enabled in (True, False):
            with self.subTest(undo_enabled=undo_enabled):
                self.document.UndoMode = undo_enabled
                wire = self._closed_wire(
                    (
                        App.Vector(0, 0, 0),
                        App.Vector(4, 0, 0),
                        App.Vector(4, 3, 0),
                        App.Vector(0, 3, 0),
                    )
                )
                body, source = self._body_with_shape(
                    f"FailedExtrude{int(undo_enabled)}Body",
                    wire,
                )
                self.assertIs(self.document.ActiveObject, source)
                self._select_subelement(
                    body,
                    "Edge1",
                    App.Vector(2.0, 0.0, 0.0),
                )
                command_state = self._interaction_snapshot()
                self._run_task("Part_Extrude")

                custom = self._visible_widget(
                    QtGui.QRadioButton,
                    "rbDirModeCustom",
                )
                custom.click()
                for object_name in ("dirX", "dirY", "dirZ"):
                    control = self._visible_widget(
                        QtGui.QDoubleSpinBox,
                        object_name,
                    )
                    control.setValue(0.0)
                attempt_state = self._interaction_snapshot()
                apply_button = self._task_button(
                    QtGui.QDialogButtonBox.Apply
                )
                self.assertIsNotNone(apply_button)
                self._click_with_error_dismissed(apply_button)

                self.assertTrue(Gui.Control.activeDialog())
                self.assertEqual(
                    self._interaction_snapshot(),
                    attempt_state,
                )
                self.assertEqual(
                    tuple(self.document.Objects),
                    attempt_state[0],
                )
                self.assertIs(self.document.getObject(source.Name), source)

                self._dismiss_task("Part_Extrude")
                self.assertEqual(
                    self._interaction_snapshot(),
                    command_state,
                )

    def test_failed_mirror_removes_its_new_result_not_the_existing_input(self):
        for undo_enabled in (True, False):
            with self.subTest(undo_enabled=undo_enabled):
                self.document.UndoMode = undo_enabled
                body, source = self._body_with_shape(
                    f"FailedMirror{int(undo_enabled)}Body",
                    Part.makeBox(3, 4, 5),
                )
                self.assertIs(self.document.ActiveObject, source)
                self._select_subelement(
                    body,
                    "Face1",
                    App.Vector(0.5, 0.5, 0.0),
                )
                command_state = self._interaction_snapshot()
                self._run_task("Part_Mirror")

                # The row remains the same native input, but its kernel shape
                # is deliberately invalidated after the task has captured it.
                # Mirror creates a result object before recompute discovers
                # the null source, exercising exact failed-result cleanup.
                source.Shape = Part.Shape()
                self.document.recompute()
                attempt_state = self._interaction_snapshot()
                ok_button = self._task_button(QtGui.QDialogButtonBox.Ok)
                self.assertIsNotNone(ok_button)
                self._click_with_error_dismissed(ok_button)

                self.assertTrue(Gui.Control.activeDialog())
                self.assertEqual(
                    self._interaction_snapshot(),
                    attempt_state,
                )
                self.assertEqual(
                    tuple(self.document.Objects),
                    attempt_state[0],
                )
                self.assertIs(self.document.getObject(source.Name), source)

                self._dismiss_task("Part_Mirror")
                self.assertEqual(
                    self._interaction_snapshot(),
                    command_state,
                )

    def test_scale_apply_then_close_keeps_only_the_accepted_result(self):
        for undo_enabled in (True, False):
            with self.subTest(undo_enabled=undo_enabled):
                self.document.UndoMode = undo_enabled
                body, source = self._body_with_shape(
                    f"ApplyClose{int(undo_enabled)}Body",
                    Part.makeBox(3, 4, 5),
                )
                self._select(body)
                previous_names = {obj.Name for obj in self.document.Objects}
                self._run_task("Part_Scale")
                self._visible_widget(
                    QtGui.QDoubleSpinBox,
                    "dsbUniformScale",
                ).setValue(2.0)

                apply_button = self._task_button(
                    QtGui.QDialogButtonBox.Apply
                )
                self.assertIsNotNone(apply_button)
                apply_button.click()
                self._process_events(80)

                self.assertTrue(Gui.Control.activeDialog())
                result = self._created_result(
                    previous_names,
                    "Part::Scale",
                )
                self._assert_same_body_result(body, result)
                self.assertEqual(tuple(body.Group), (source, result))
                self.assertFalse(self.document.HasPendingTransaction)

                close_button = self._task_button(
                    QtGui.QDialogButtonBox.Close
                )
                self.assertIsNotNone(close_button)
                close_button.click()
                self._process_events(80)

                self.assertFalse(Gui.Control.activeDialog())
                self.assertIs(self.document.getObject(result.Name), result)
                self.assertEqual(tuple(body.Group), (source, result))
                self.assertIs(body.Tip, result)
                self.assertFalse(source.ViewObject.Visibility)
                self.assertTrue(result.ViewObject.Visibility)
                self.assertFalse(
                    [
                        obj
                        for obj in self.document.Objects
                        if obj.isDerivedFrom("Part::Feature")
                        and not obj.isDerivedFrom("PartDesign::Body")
                        and obj.getParentGeoFeatureGroup() is None
                    ]
                )

    def test_failed_second_apply_never_removes_the_first_accepted_result(self):
        for undo_enabled in (True, False):
            with self.subTest(undo_enabled=undo_enabled):
                self.document.UndoMode = undo_enabled
                body, source = self._body_with_shape(
                    f"SecondApply{int(undo_enabled)}Body",
                    Part.makeBox(3, 4, 5),
                )
                self._select(body)
                original_names = {obj.Name for obj in self.document.Objects}
                self._run_task("Part_Scale")
                factor = self._visible_widget(
                    QtGui.QDoubleSpinBox,
                    "dsbUniformScale",
                )
                factor.setValue(2.0)
                apply_button = self._task_button(
                    QtGui.QDialogButtonBox.Apply
                )
                self.assertIsNotNone(apply_button)
                apply_button.click()
                self._process_events(80)

                accepted = self._created_result(
                    original_names,
                    "Part::Scale",
                )
                self._assert_same_body_result(body, accepted)
                accepted_state = self._interaction_snapshot()

                factor.setValue(0.0)
                self._click_with_error_dismissed(apply_button)
                self.assertTrue(Gui.Control.activeDialog())
                self.assertEqual(
                    self._interaction_snapshot(),
                    accepted_state,
                )
                self.assertIs(
                    self.document.getObject(accepted.Name),
                    accepted,
                )
                self.assertEqual(tuple(body.Group), (source, accepted))
                self.assertIs(body.Tip, accepted)

                self._dismiss_task("Part_Scale")
                self.assertIs(
                    self.document.getObject(accepted.Name),
                    accepted,
                )
                self.assertEqual(tuple(body.Group), (source, accepted))
                self.assertIs(body.Tip, accepted)


if __name__ == "__main__":
    unittest.main()
