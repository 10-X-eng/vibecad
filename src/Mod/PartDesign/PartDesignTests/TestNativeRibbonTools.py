# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live command-contract tests for the native tools exposed by the Model ribbon."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers Part Design document types
from PySide import QtCore, QtGui


PRIMITIVE_SHAPES = (
    "Box",
    "Cylinder",
    "Sphere",
    "Cone",
    "Ellipsoid",
    "Torus",
    "Prism",
    "Wedge",
)

DESIGN_PRIMITIVE_SHAPES = PRIMITIVE_SHAPES + ("Tube",)

TRANSFORM_COMMANDS = (
    ("PartDesign_Mirrored", "PartDesign::Mirrored"),
    ("PartDesign_LinearPattern", "PartDesign::LinearPattern"),
    ("PartDesign_PolarPattern", "PartDesign::PolarPattern"),
    ("PartDesign_MultiTransform", "PartDesign::MultiTransform"),
)

DESIGN_PATTERN_COMMANDS = (
    ("PartDesign_DesignMirror", "PartDesign::DesignMirror"),
    (
        "PartDesign_DesignLinearPattern",
        "PartDesign::DesignLinearPattern",
    ),
    (
        "PartDesign_DesignCircularPattern",
        "PartDesign::DesignCircularPattern",
    ),
)

FINISH_COMMANDS = (
    ("PartDesign_Fillet", "PartDesign::DesignFillet", "Edge1"),
    ("PartDesign_Chamfer", "PartDesign::DesignChamfer", "Edge1"),
    ("PartDesign_Thickness", "PartDesign::DesignThickness", "Face1"),
)

PROFILE_COMMANDS = (
    ("PartDesign_Pad", "PartDesign::Pad", False, "profile"),
    ("PartDesign_Revolution", "PartDesign::Revolution", False, "profile"),
    ("PartDesign_AdditiveLoft", "PartDesign::AdditiveLoft", False, "loft"),
    ("PartDesign_AdditivePipe", "PartDesign::AdditivePipe", False, "pipe"),
    ("PartDesign_AdditiveHelix", "PartDesign::AdditiveHelix", False, "profile"),
    ("PartDesign_Pocket", "PartDesign::Pocket", True, "profile"),
    ("PartDesign_Hole", "PartDesign::DesignHole", True, "hole"),
    ("PartDesign_Groove", "PartDesign::Groove", True, "profile"),
    ("PartDesign_SubtractiveLoft", "PartDesign::SubtractiveLoft", True, "loft"),
    ("PartDesign_SubtractivePipe", "PartDesign::SubtractivePipe", True, "pipe"),
    ("PartDesign_SubtractiveHelix", "PartDesign::SubtractiveHelix", True, "profile"),
)


class TestNativeRibbonTools(unittest.TestCase):
    """Ribbon actions must enforce usable inputs and preserve native Body history."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("NativeRibbonTools")
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
        if App.getDocument("NativeRibbonTools") is not None:
            App.closeDocument("NativeRibbonTools")
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

    def _new_body(self, name, *, solid=False, solid_size=10.0):
        body = self.document.addObject("PartDesign::Body", name)
        Gui.activeView().setActiveObject("pdbody", body)
        if not solid:
            return body, None
        feature = body.newObject("PartDesign::Feature", f"{name}Result")
        feature.Shape = Part.makeBox(solid_size, solid_size, solid_size)
        body.Tip = feature
        self.document.recompute()
        self.assertTrue(feature.isValid())
        self.assertFalse(feature.Shape.isNull())
        return body, feature

    def _wire_body(self, name, *, x=0.0, z=0.0):
        body, _feature = self._new_body(name)
        feature = body.newObject("PartDesign::Feature", f"{name}Result")
        feature.Shape = Part.makePolygon(
            [
                App.Vector(x, 0, z),
                App.Vector(x + 5, 0, z),
                App.Vector(x + 5, 5, z),
                App.Vector(x, 5, z),
                App.Vector(x, 0, z),
            ]
        )
        body.Tip = feature
        self.document.recompute()
        self.assertTrue(feature.isValid())
        self.assertFalse(feature.Shape.isNull())
        return body, feature

    def _profile_sketch(self, body, name, *, size=4.0, z=0.0):
        sketch = body.newObject("Sketcher::SketchObject", name)
        sketch.addGeometry(
            [
                Part.LineSegment(
                    App.Vector(2, 2, 0),
                    App.Vector(2 + size, 2, 0),
                ),
                Part.LineSegment(
                    App.Vector(2 + size, 2, 0),
                    App.Vector(2 + size, 2 + size, 0),
                ),
                Part.LineSegment(
                    App.Vector(2 + size, 2 + size, 0),
                    App.Vector(2, 2 + size, 0),
                ),
                Part.LineSegment(
                    App.Vector(2, 2 + size, 0),
                    App.Vector(2, 2, 0),
                ),
            ],
            False,
        )
        sketch.Placement.Base.z = z
        self.document.recompute()
        return sketch

    def _circle_sketch(self, body, name):
        sketch = body.newObject("Sketcher::SketchObject", name)
        sketch.addGeometry(
            Part.Circle(
                App.Vector(5, 5, 0),
                App.Vector(0, 0, 1),
                1.0,
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

    def _native_pad(self, body, name):
        profile = self._profile_sketch(body, f"{name}Sketch", size=10.0)
        pad = body.newObject("PartDesign::Pad", name)
        pad.Profile = profile
        pad.Length = 10.0
        body.Tip = pad
        self.document.recompute()
        self.assertTrue(pad.isValid(), pad.getStatusString())
        self.assertFalse(pad.Shape.isNull())
        return pad

    def _profile_command_inputs(
        self,
        index,
        *,
        subtractive,
        input_kind,
        prefix,
    ):
        body, _feature = self._new_body(f"{prefix}Body{index}")
        base = self._native_pad(body, f"{prefix}Base{index}") if subtractive else None
        profile = (
            self._circle_sketch(body, f"{prefix}Profile{index}")
            if input_kind == "hole"
            else self._profile_sketch(body, f"{prefix}Profile{index}")
        )
        selections = [profile]
        if input_kind == "loft":
            selections.append(
                self._profile_sketch(
                    body,
                    f"{prefix}Section{index}",
                    size=3.0,
                    z=8.0,
                )
            )
        elif input_kind == "pipe":
            selections.append(self._path_sketch(body, f"{prefix}Path{index}"))
        if base is not None:
            body.Tip = base
        self.document.recompute()
        return body, base, profile, selections

    def _bodies(self):
        return [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::Body"
        ]

    def _snapshot(self, _body=None):
        """Capture every user-visible state a native task is allowed to borrow.

        App transactions own feature-property rollback.  Selection, active Body,
        and view state are GUI state, so they must be asserted explicitly rather
        than assumed to follow the transaction.  The undo count and booked
        transaction ID also distinguish an exact Cancel from closing the wrong
        transaction or leaving an empty undo entry.
        """

        document = self.document
        objects = tuple(document.Objects)
        bodies = tuple(
            (
                body.Name,
                tuple(body.Group),
                body.Tip,
            )
            for body in objects
            if body.TypeId == "PartDesign::Body"
        )
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
        active_body = Gui.activeView().getActiveObject("pdbody")
        return (
            objects,
            tuple(
                (obj.Name, obj.TypeId, id(obj))
                for obj in objects
            ),
            bodies,
            document.ActiveObject,
            selection,
            active_body,
            tuple(
                (obj, bool(obj.ViewObject.Visibility))
                for obj in objects
                if getattr(obj, "ViewObject", None) is not None
            ),
            bool(document.HasPendingTransaction),
            int(document.UndoCount),
            int(document.getBookedTransactionID()),
        )

    def _assert_snapshot(self, body, expected, command_name):
        self._process_events()
        self.assertEqual(
            self._snapshot(body),
            expected,
            command_name,
        )
        self.assertFalse(self.document.HasPendingTransaction, command_name)

    def _task_button(self, standard_button):
        self._process_events()
        for button_box in Gui.getMainWindow().findChildren(
            QtGui.QDialogButtonBox
        ):
            if not button_box.isVisible():
                continue
            button = button_box.button(standard_button)
            if button is not None and button.isVisible() and button.isEnabled():
                return button
        return None

    def _accept_task(self, command_name):
        self.assertTrue(Gui.Control.activeDialog(), command_name)
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button, command_name)
        button.click()
        self._process_events(50)
        self.assertFalse(Gui.Control.activeDialog(), command_name)
        self.assertFalse(self.document.HasPendingTransaction, command_name)

    def _cancel_task(self, command_name):
        self.assertTrue(Gui.Control.activeDialog(), command_name)
        button = self._task_button(QtGui.QDialogButtonBox.Cancel)
        self.assertIsNotNone(button, command_name)
        button.click()
        self._process_events(50)
        self.assertFalse(Gui.Control.activeDialog(), command_name)
        self.assertFalse(self.document.HasPendingTransaction, command_name)

    def _dismiss_task(self, command_name, *, allow_close=False):
        self.assertTrue(Gui.Control.activeDialog(), command_name)
        buttons = [QtGui.QDialogButtonBox.Cancel]
        if allow_close:
            buttons.append(QtGui.QDialogButtonBox.Close)
        button = None
        for standard_button in buttons:
            button = self._task_button(standard_button)
            if button is not None:
                break
        self.assertIsNotNone(button, command_name)
        button.click()
        self._process_events(50)
        self.assertFalse(Gui.Control.activeDialog(), command_name)
        self.assertFalse(self.document.HasPendingTransaction, command_name)

    def _activate_body(self, body, subelement=None):
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        if subelement is None:
            Gui.Selection.addSelection(body)
        else:
            Gui.Selection.addSelection(body, subelement)
        self._process_events()

    def _launch_and_cancel_exact(
        self,
        command_name,
        active_body,
        selections=(),
        *,
        index=0,
        allow_close=False,
    ):
        Gui.activeView().setActiveObject("pdbody", active_body)
        Gui.Selection.clearSelection()
        for selected in selections:
            if isinstance(selected, tuple):
                if len(selected) == 2:
                    Gui.Selection.addSelection(selected[0], selected[1])
                else:
                    Gui.Selection.addSelection(
                        selected[0],
                        selected[1],
                        selected[2],
                        selected[3],
                        selected[4],
                    )
            else:
                Gui.Selection.addSelection(selected)
        self._process_events()
        self.assertTrue(Gui.isCommandActive(command_name), command_name)
        expected = self._snapshot(active_body)

        Gui.runCommand(command_name, index)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog(), command_name)
        self._dismiss_task(command_name, allow_close=allow_close)
        self._assert_snapshot(active_body, expected, command_name)

    def _configure_transform_for_accept(self, command_name, feature):
        if command_name == "PartDesign_Mirrored":
            return
        if command_name == "PartDesign_LinearPattern":
            feature.Length = 10.0
            feature.Occurrences = 2
            return
        if command_name == "PartDesign_PolarPattern":
            feature.Angle = 90.0
            feature.Occurrences = 2
            return

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
        self.assertEqual(len(feature.Transformations), 1, command_name)
        internal = feature.Transformations[0]
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

    def _drive_map_sketch_dialogs(self, sketch, *, cancel_at=None):
        seen = []
        handled = set()
        errors = []
        finished = {"value": False}

        def respond():
            if finished["value"]:
                return
            for dialog in QtGui.QApplication.topLevelWidgets():
                if (
                    not isinstance(dialog, QtGui.QInputDialog)
                    or not dialog.isVisible()
                    or id(dialog) in handled
                ):
                    continue
                handled.add(id(dialog))
                title = dialog.windowTitle()
                seen.append(title)
                combo = dialog.findChild(QtGui.QComboBox)
                if combo is None:
                    errors.append(f"{title}: missing choice list")
                    finished["value"] = True
                    dialog.reject()
                    return

                if "Select Sketch" in title:
                    if cancel_at == "sketch":
                        finished["value"] = True
                        dialog.reject()
                        return
                    sketch_index = combo.findText(
                        f"{sketch.Label} ({sketch.Name})"
                    )
                    if sketch_index < 0:
                        errors.append(f"{title}: {sketch.Label} was not offered")
                        finished["value"] = True
                        dialog.reject()
                        return
                    combo.setCurrentIndex(sketch_index)
                    dialog.accept()
                    QtCore.QTimer.singleShot(10, respond)
                    return

                if "Attachment" in title:
                    if cancel_at == "attachment":
                        finished["value"] = True
                        dialog.reject()
                        return
                    suggested = next(
                        (
                            index
                            for index in range(combo.count())
                            if "suggested" in combo.itemText(index).lower()
                        ),
                        -1,
                    )
                    if suggested < 0:
                        errors.append(f"{title}: no suggested attachment mode")
                        finished["value"] = True
                        dialog.reject()
                        return
                    combo.setCurrentIndex(suggested)
                    finished["value"] = True
                    dialog.accept()
                    return

                errors.append(f"unexpected input dialog: {title}")
                finished["value"] = True
                dialog.reject()
                return

            QtCore.QTimer.singleShot(10, respond)

        QtCore.QTimer.singleShot(0, respond)
        Gui.runCommand("PartDesign_CompSketches", 1)
        self._process_events(50)
        self.assertEqual(errors, [])
        return tuple(seen)

    @staticmethod
    def _attachment_state(sketch):
        return (
            str(sketch.MapMode),
            tuple(
                (support, tuple(sub_elements))
                for support, sub_elements in sketch.AttachmentSupport
            ),
        )

    @staticmethod
    def _line_geometry_state(sketch):
        return tuple(
            (
                geometry.TypeId,
                geometry.StartPoint.x,
                geometry.StartPoint.y,
                geometry.StartPoint.z,
                geometry.EndPoint.x,
                geometry.EndPoint.y,
                geometry.EndPoint.z,
            )
            for geometry in sketch.Geometry
        )

    def test_design_body_selection_uses_state_not_publication(self):
        import PartGui

        body, initial = self._new_body("DesignSelectionBody", solid=True)
        self.document.openTransaction("Create reusable selection sketch")
        sketch = self.document.addObject(
            "Sketcher::SketchObject",
            "DesignSelectionSketch",
        )
        sketch.addGeometry(
            [
                Part.LineSegment(App.Vector(2, 2, 0), App.Vector(6, 2, 0)),
                Part.LineSegment(App.Vector(6, 2, 0), App.Vector(6, 6, 0)),
                Part.LineSegment(App.Vector(6, 6, 0), App.Vector(2, 6, 0)),
                Part.LineSegment(App.Vector(2, 6, 0), App.Vector(2, 2, 0)),
            ],
            False,
        )
        sketch.addProperty(
            "App::PropertyString",
            "VibeCADTimelineRole",
            "Timeline",
        )
        sketch.setPropertyStatus(
            "VibeCADTimelineRole",
            ("Hidden", "LockDynamic", "NoRecompute"),
        )
        sketch.VibeCADTimelineRole = "operation"
        self.document.finalizeProvisionalTimelineOperationBlock(
            sketch,
            [sketch],
        )
        self.document.commitTransaction()

        self.document.openTransaction("Create Design cut")
        operation = self.document.addObject(
            "PartDesign::DesignExtrude",
            "DesignSelectionCut",
        )
        edit = PartDesign.beginDesignOperationEdit(operation)
        operation.Profile = sketch
        operation.Length = 5
        PartDesign.setDesignOperationTargets(edit, "Cut", [body])
        self.document.recompute()
        PartDesign.finalizeDesignOperationEdit(edit)
        self.document.commitTransaction()
        self.document.recompute()

        publication = body.Tip
        state = publication.CurrentState
        self.assertIs(state.PreviousState, initial)
        self.assertIs(PartGui.resolveModelingObject(body), state)
        self.assertIs(PartGui.resolveModelingObject(publication), state)
        self.assertIs(
            PartGui.resolveModelingObjectForBody(body, body),
            state,
        )
        self.assertIs(PartGui.findModelingBody(state), body)
        self.assertIs(
            PartGui.resolveModelingPresentationObject(state),
            body,
        )
        self.assertIsNot(
            PartGui.resolveModelingObject(body),
            publication,
        )
        self.assertTrue(PartGui.isModelingObjectActive(body))
        self.assertTrue(PartGui.isModelingObjectActive(publication))
        self.assertTrue(PartGui.isModelingObjectActive(state))

    def test_combine_uses_explicit_selection_roles_and_design_history(self):
        result_body, result_feature = self._new_body(
            "CombineResultBody",
            solid=True,
        )
        tool_body, tool_feature = self._new_body(
            "CombineToolBody",
            solid=True,
        )
        tool_feature.Shape = Part.makeBox(
            10,
            10,
            10,
            App.Vector(5, 0, 0),
        )
        self.document.recompute()

        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(result_body)
        Gui.Selection.addSelection(tool_body)
        self._process_events()

        self.assertTrue(Gui.isCommandActive("PartDesign_Combine"))
        Gui.runCommand("PartDesign_Combine", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())

        operations = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::DesignCombine"
        ]
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertEqual(operation.ResultOperation, "Join")
        self.assertEqual(
            str(operation.ResultBodyId),
            str(result_body.VibeCADBodyId),
        )
        self.assertEqual(
            list(operation.InputStates),
            [result_feature, tool_feature],
        )
        self.assertEqual(
            list(operation.InputBodyIds),
            [
                str(result_body.VibeCADBodyId),
                str(tool_body.VibeCADBodyId),
            ],
        )
        self.assertFalse(operation.KeepTools)

        self._accept_task("PartDesign_Combine")
        self.document.recompute()
        PartDesign.validateDesign(operation)

        self.assertAlmostEqual(
            result_body.Tip.Shape.Volume,
            1500.0,
            places=6,
        )
        self.assertTrue(tool_body.Tip.Shape.isNull())
        self.assertEqual(
            result_body.Tip.CurrentState.PreviousState,
            result_feature,
        )
        self.assertEqual(
            tool_body.Tip.CurrentState.PreviousState,
            tool_feature,
        )
        self.assertFalse(tool_body.Tip.CurrentState.Present)

    def test_thickness_shells_faces_across_bodies_with_one_history_operation(self):
        first_body, first_feature = self._new_body(
            "FirstThicknessBody",
            solid=True,
        )
        second_body, second_feature = self._new_body(
            "SecondThicknessBody",
            solid=True,
        )
        second_body.Placement.Base.x = 20
        self.document.recompute()

        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first_body, "Face6")
        Gui.Selection.addSelection(second_body, "Face6")
        self._process_events()

        self.assertTrue(Gui.isCommandActive("PartDesign_Thickness"))
        Gui.runCommand("PartDesign_Thickness", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())

        operations = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::DesignThickness"
        ]
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertIsNone(operation.Base)
        self.assertIsNone(operation.BaseFeature)
        self.assertEqual(
            list(operation.InputStates),
            [first_feature, second_feature],
        )
        self.assertEqual(
            list(operation.TargetElementOffsets),
            [0, 1, 2],
        )
        self.assertEqual(
            list(operation.TargetElements),
            ["Face6", "Face6"],
        )

        references = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "listWidgetReferences",
        )
        select_button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "buttonRefSel",
        )
        self.assertIsNotNone(references)
        self.assertIsNotNone(select_button)
        self.assertEqual(references.count(), 2)
        self.assertIn("FirstThicknessBody", references.item(0).text())
        self.assertIn("SecondThicknessBody", references.item(1).text())

        select_button.click()
        Gui.Selection.addSelection(first_body, "Face5")
        self._process_events(50)
        self.assertEqual(references.count(), 3)
        self.assertEqual(
            list(operation.TargetElementOffsets),
            [0, 2, 3],
        )
        self.assertEqual(
            list(operation.TargetElements),
            ["Face6", "Face5", "Face6"],
        )

        face5_row = next(
            row
            for row in range(references.count())
            if references.item(row).text().endswith("Face5")
        )
        references.setCurrentRow(face5_row)
        remove_action = next(
            action
            for action in references.actions()
            if action.text() == "Remove"
        )
        remove_action.trigger()
        self._process_events(50)
        self.assertEqual(references.count(), 2)
        self.assertEqual(
            list(operation.TargetElementOffsets),
            [0, 1, 2],
        )
        self.assertEqual(
            list(operation.TargetElements),
            ["Face6", "Face6"],
        )

        self._accept_task("PartDesign_Thickness")
        self.document.recompute()
        PartDesign.validateDesign(operation)

        self.assertAlmostEqual(first_body.Shape.Volume, 424.0, places=6)
        self.assertAlmostEqual(second_body.Shape.Volume, 424.0, places=6)
        self.assertEqual(
            first_body.Tip.CurrentState.PreviousState,
            first_feature,
        )
        self.assertEqual(
            second_body.Tip.CurrentState.PreviousState,
            second_feature,
        )

    def test_draft_tapers_faces_across_bodies_with_exact_global_references(self):
        first_body, first_feature = self._new_body(
            "FirstDraftBody",
            solid=True,
        )
        second_body, second_feature = self._new_body(
            "SecondDraftBody",
            solid=True,
        )
        first_body.Placement.Base.x = 7
        second_body.Placement.Base.x = 27
        self.document.recompute()

        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first_body, "Face1")
        Gui.Selection.addSelection(second_body, "Face1")
        self._process_events()

        self.assertTrue(Gui.isCommandActive("PartDesign_Draft"))
        Gui.runCommand("PartDesign_Draft", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())

        operations = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::DesignDraft"
        ]
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertIsNone(operation.Base)
        self.assertIsNone(operation.BaseFeature)
        self.assertEqual(
            list(operation.InputStates),
            [first_feature, second_feature],
        )
        self.assertEqual(
            list(operation.TargetElementOffsets),
            [0, 1, 2],
        )
        self.assertEqual(
            list(operation.TargetElements),
            ["Face1", "Face1"],
        )

        references = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "listWidgetReferences",
        )
        plane_button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "buttonPlane",
        )
        line_button = Gui.getMainWindow().findChild(
            QtGui.QToolButton,
            "buttonLine",
        )
        self.assertIsNotNone(references)
        self.assertIsNotNone(plane_button)
        self.assertIsNotNone(line_button)
        self.assertEqual(references.count(), 2)

        plane_button.click()
        Gui.Selection.addSelection(first_body, "Face5")
        self._process_events(50)
        self.assertEqual(
            operation.NeutralPlane,
            (first_feature, ["Face5"]),
        )
        self.assertAlmostEqual(operation.NeutralPlaneFrame.Base.x, 7.0)

        line_button.click()
        Gui.Selection.addSelection(first_body, "Edge1")
        self._process_events(50)
        self.assertEqual(
            operation.PullDirection,
            (first_feature, ["Edge1"]),
        )
        self.assertAlmostEqual(operation.PullDirectionFrame.Base.x, 7.0)
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertEqual(len(operation.OutputShapes), 2)

        self._accept_task("PartDesign_Draft")
        self.document.recompute()
        PartDesign.validateDesign(operation)

        expected_volume = 986.9070392154064
        self.assertAlmostEqual(first_body.Shape.Volume, expected_volume)
        self.assertAlmostEqual(second_body.Shape.Volume, expected_volume)

    def test_split_requires_an_explicit_region_identity_and_publishes_each_body(self):
        source_body, source_feature = self._new_body(
            "SplitSourceBody",
            solid=True,
        )
        splitter = self.document.addObject(
            "Part::Feature",
            "SplitDefinition",
        )
        splitter.Shape = Part.makePlane(
            30,
            30,
            App.Vector(5, 20, -10),
            App.Vector(1, 0, 0),
        )
        second_splitter = self.document.addObject(
            "Part::Feature",
            "SecondSplitDefinition",
        )
        second_splitter.Shape = Part.makePlane(
            30,
            30,
            App.Vector(7, 20, -10),
            App.Vector(1, 0, 0),
        )
        self.document.recompute()

        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_body)
        Gui.Selection.addSelection(splitter)
        self._process_events()

        self.assertTrue(Gui.isCommandActive("PartDesign_Split"))
        Gui.runCommand("PartDesign_Split", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())

        operations = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::DesignSplit"
        ]
        self.assertEqual(len(operations), 1)
        operation = operations[0]
        self.assertEqual(operation.ResultOperation, "Split")
        self.assertEqual(
            str(operation.SourceBodyId),
            str(source_body.VibeCADBodyId),
        )
        self.assertEqual(list(operation.InputStates), [source_feature])
        self.assertFalse(operation.RetainedRegionChosen)
        self.assertEqual(list(operation.OutputBodyIds), [])

        region_selector = Gui.getMainWindow().findChild(
            QtGui.QComboBox,
            "DesignSplitRetainedRegion",
        )
        self.assertIsNotNone(region_selector)
        self.assertEqual(region_selector.count(), 3)
        self.assertEqual(region_selector.currentIndex(), 0)

        definition_list = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "DesignBodyList",
        )
        add_definition = Gui.getMainWindow().findChild(
            QtGui.QPushButton,
            "DesignSplitAddDefinitions",
        )
        remove_definition = Gui.getMainWindow().findChild(
            QtGui.QPushButton,
            "DesignSplitRemoveDefinitions",
        )
        self.assertIsNotNone(definition_list)
        self.assertIsNotNone(add_definition)
        self.assertIsNotNone(remove_definition)
        self.assertEqual(definition_list.count(), 1)

        Gui.Selection.addSelection(second_splitter)
        add_definition.click()
        self._process_events(50)
        self.assertEqual(definition_list.count(), 2)
        self.assertEqual(region_selector.count(), 4)
        self.assertFalse(operation.RetainedRegionChosen)

        definition_list.setCurrentRow(1)
        self._process_events()
        self.assertTrue(remove_definition.isEnabled())
        remove_definition.click()
        self._process_events(50)
        self.assertEqual(definition_list.count(), 1)
        self.assertEqual(region_selector.count(), 3)
        self.assertFalse(operation.RetainedRegionChosen)

        region_selector.setCurrentIndex(1)
        self._process_events(50)

        self.assertTrue(operation.RetainedRegionChosen)
        self.assertEqual(len(operation.OutputBodyIds), 2)
        self.assertEqual(
            str(operation.OutputBodyIds[0]),
            str(source_body.VibeCADBodyId),
        )
        self.assertEqual(
            list(operation.OutputPreviousInputIndices),
            [0, -1],
        )
        self.assertEqual(len(operation.OutputShapes), 2)

        self._accept_task("PartDesign_Split")
        self.document.recompute()
        PartDesign.validateDesign(operation)

        result_bodies = [
            body
            for body in self.document.findObjects("PartDesign::Body")
            if not body.Tip.Shape.isNull()
        ]
        self.assertEqual(len(result_bodies), 2)
        self.assertEqual(
            sorted(round(body.Tip.Shape.Volume, 6) for body in result_bodies),
            [500.0, 500.0],
        )
        self.assertIs(
            source_body.Tip.CurrentState.PreviousState,
            source_feature,
        )

    def test_split_cancel_restores_the_exact_document(self):
        source_body, source_feature = self._new_body(
            "CancelledSplitSource",
            solid=True,
        )
        splitter = self.document.addObject(
            "Part::Feature",
            "CancelledSplitDefinition",
        )
        splitter.Shape = Part.makePlane(
            30,
            30,
            App.Vector(5, 20, -10),
            App.Vector(1, 0, 0),
        )
        self.document.recompute()
        before_objects = tuple(self.document.Objects)
        timeline = self.document.getObject("VibeCADTimeline")
        before_history = tuple(timeline.Operations) if timeline else ()

        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_body)
        Gui.Selection.addSelection(splitter)
        self._process_events()
        Gui.runCommand("PartDesign_Split", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())

        self._cancel_task("PartDesign_Split")
        self.assertEqual(tuple(self.document.Objects), before_objects)
        self.assertIs(source_body.Tip, source_feature)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertEqual(
            tuple(timeline.Operations) if timeline else (),
            before_history,
        )

    def test_new_body_is_disabled_while_another_task_is_open(self):
        body, _feature = self._new_body("BodyTaskGate")
        self._activate_body(body)
        expected = self._snapshot(body)

        Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        task_objects = tuple(self.document.Objects)
        self.assertFalse(Gui.isCommandActive("PartDesign_Body"))

        Gui.runCommand("PartDesign_Body", 0)
        self._process_events()
        self.assertEqual(tuple(self.document.Objects), task_objects)
        self.assertTrue(Gui.Control.activeDialog())

        self._cancel_task("New Body task gate")
        self._assert_snapshot(body, expected, "New Body task gate")

    def test_new_empty_body_is_structural_and_its_features_remain_history_steps(self):
        timeline = self.document.getObject("VibeCADTimeline")
        original_operations = (
            tuple(timeline.Operations)
            if timeline is not None
            else ()
        )

        Gui.Selection.clearSelection()
        Gui.runCommand("PartDesign_Body", 0)
        self._process_events()

        body = Gui.activeView().getActiveObject("pdbody")
        self.assertIsNotNone(body)
        self.assertEqual(body.TypeId, "PartDesign::Body")
        timeline = self.document.getObject("VibeCADTimeline")
        current_operations = (
            tuple(timeline.Operations)
            if timeline is not None
            else ()
        )
        self.assertEqual(current_operations, original_operations)
        self.assertNotIn(body, current_operations)

        Gui.runCommand("Part_Box", 0)
        self._process_events()
        feature = self.document.ActiveObject
        timeline = self.document.getObject("VibeCADTimeline")

        self.assertIsNotNone(feature)
        self.assertIsNotNone(timeline)
        self.assertIs(feature.getParentGeoFeatureGroup(), body)
        self.assertIn(feature, timeline.Operations)
        self.assertNotIn(body, timeline.Operations)

    def test_parametric_primitive_uses_its_exact_creation_return(self):
        component = self.document.addObject(
            "App::Part",
            "PrimitiveExactIdentityGroup",
        )
        Gui.activeView().setActiveObject("part", component)
        document = self.document

        class SameTransactionPrimitiveDistractor:
            def __init__(self):
                self.injected = False
                self.primitive = None
                self.distractor = None

            def slotCreatedObject(self, obj):
                if (
                    self.injected
                    or obj.Document.Name != document.Name
                    or obj.TypeId != "Part::Box"
                ):
                    return
                self.injected = True
                self.primitive = obj
                self.distractor = document.addObject(
                    "Part::Box",
                    "SameTransactionPrimitiveDistractor",
                )

        observer = SameTransactionPrimitiveDistractor()
        App.addDocumentObserver(observer)
        try:
            Gui.runCommand("Part_Box", 0)
            self._process_events()
        finally:
            App.removeDocumentObserver(observer)

        self.assertTrue(observer.injected)
        self.assertIsNotNone(observer.primitive)
        self.assertIsNotNone(observer.distractor)
        self.assertIn(observer.primitive, component.Group)
        self.assertNotIn(observer.distractor, component.Group)
        self.assertEqual(
            "SameTransactionPrimitiveDistractor",
            observer.distractor.Label,
        )
        self.assertNotEqual(
            observer.primitive.Label,
            observer.distractor.Label,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_ruled_surface_uses_its_exact_creation_return(self):
        first = self.document.addObject(
            "Part::Feature",
            "ExactRuledSurfaceFirst",
        )
        first.Shape = Part.makeLine(
            App.Vector(0, 0, 0),
            App.Vector(12, 0, 0),
        )
        second = self.document.addObject(
            "Part::Feature",
            "ExactRuledSurfaceSecond",
        )
        second.Shape = Part.makeLine(
            App.Vector(0, 0, 6),
            App.Vector(12, 0, 6),
        )
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(first)
        Gui.Selection.addSelection(second)
        document = self.document

        class SameTransactionRuledSurfaceDistractor:
            def __init__(self):
                self.injected = False
                self.result = None
                self.distractor = None

            def slotCreatedObject(self, obj):
                if (
                    self.injected
                    or obj.Document.Name != document.Name
                    or obj.TypeId != "Part::RuledSurface"
                ):
                    return
                self.injected = True
                self.result = obj
                self.distractor = document.addObject(
                    "Part::RuledSurface",
                    "SameTransactionRuledSurfaceDistractor",
                )

        observer = SameTransactionRuledSurfaceDistractor()
        App.addDocumentObserver(observer)
        try:
            Gui.runCommand("Part_RuledSurface", 0)
            self._process_events()
        finally:
            App.removeDocumentObserver(observer)

        self.assertTrue(observer.injected)
        self.assertIsNotNone(observer.result)
        self.assertIsNotNone(observer.distractor)
        self.assertIs(observer.result.Curve1[0], first)
        self.assertIs(observer.result.Curve2[0], second)
        self.assertIsNone(observer.distractor.Curve1)
        self.assertIsNone(observer.distractor.Curve2)
        self.assertTrue(first.ViewObject.Visibility)
        self.assertTrue(second.ViewObject.Visibility)
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            observer.result.PropertiesList,
        )
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertIn(observer.result, timeline.Operations)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_new_body_keeps_its_exact_identity_with_a_creation_distractor(self):
        document = self.document

        class SameTransactionBodyDistractor:
            def __init__(self):
                self.injected = False
                self.created_body = None
                self.distractor = None

            def slotCreatedObject(self, obj):
                if (
                    self.injected
                    or obj.Document.Name != document.Name
                    or obj.TypeId != "PartDesign::Body"
                ):
                    return
                self.injected = True
                self.created_body = obj
                self.distractor = document.addObject(
                    "PartDesign::Body",
                    "SameTransactionBodyDistractor",
                )

        observer = SameTransactionBodyDistractor()
        App.addDocumentObserver(observer)
        try:
            Gui.Selection.clearSelection()
            Gui.runCommand("PartDesign_Body", 0)
            self._process_events()
        finally:
            App.removeDocumentObserver(observer)

        self.assertTrue(observer.injected)
        self.assertIsNotNone(observer.created_body)
        self.assertIsNotNone(observer.distractor)
        active_body = Gui.activeView().getActiveObject("pdbody")
        self.assertIs(active_body, observer.created_body)
        self.assertIsNot(active_body, observer.distractor)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_new_body_from_selected_sketch_cancel_is_exact(self):
        previous_body, _feature = self._new_body("PreviousActiveBody")
        sketch = self.document.addObject(
            "Sketcher::SketchObject",
            "StandaloneBodySketch",
        )
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(1, 2, 0),
                App.Vector(6, 2, 0),
            ),
            False,
        )
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", previous_body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch, "Edge1", 2.5, 2.0, 0.0)
        self._process_events()
        expected = self._snapshot(previous_body)

        Gui.runCommand("PartDesign_Body", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        self._cancel_task("new Body from selected sketch")

        self._assert_snapshot(
            previous_body,
            expected,
            "cancel new Body from selected sketch",
        )
        self.assertIsNone(sketch.getParentGeoFeatureGroup())

    def test_new_body_from_selected_sketch_accepts_one_transaction(self):
        sketch = self.document.addObject(
            "Sketcher::SketchObject",
            "AcceptedStandaloneBodySketch",
        )
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(0, 0, 0),
                App.Vector(8, 0, 0),
            ),
            False,
        )
        self.document.recompute()
        original_names = tuple(obj.Name for obj in self.document.Objects)
        original_bodies = set(self._bodies())
        Gui.Selection.addSelection(sketch)

        Gui.runCommand("PartDesign_Body", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self.assertTrue(self.document.HasPendingTransaction)
        created_bodies = set(self._bodies()) - original_bodies
        self.assertEqual(len(created_bodies), 1)
        body = created_bodies.pop()

        plane_list = next(
            (
                widget
                for widget in Gui.getMainWindow().findChildren(
                    QtGui.QListWidget,
                    "listWidget",
                )
                if widget.isVisible()
            ),
            None,
        )
        self.assertIsNotNone(plane_list)
        self.assertGreater(plane_list.count(), 0)
        plane_list.setCurrentRow(0)
        self._process_events()
        if Gui.Control.activeDialog():
            self._accept_task("new Body plane")

        self.assertFalse(self.document.HasPendingTransaction)
        self.assertIn(sketch, body.Group)
        self.assertIs(sketch.getParentGeoFeatureGroup(), body)
        self.assertNotEqual(sketch.MapMode, "Deactivated")
        self.assertEqual(len(sketch.AttachmentSupport), 1)
        support, sub_elements = sketch.AttachmentSupport[0]
        self.assertTrue(support.isDerivedFrom("App::Plane"))
        self.assertIn(support, body.Origin.OriginFeatures)
        self.assertEqual(tuple(sub_elements), ("",))
        self.assertIs(Gui.activeView().getActiveObject("pdbody"), body)
        accepted_selection = Gui.Selection.getSelectionEx()
        self.assertEqual(len(accepted_selection), 1)
        self.assertIs(accepted_selection[0].Object, body)

        self.document.undo()
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertIsNone(sketch.getParentGeoFeatureGroup())
        self.assertFalse(self.document.HasPendingTransaction)

    def test_map_sketch_body_face_cancel_paths_are_exact(self):
        body, source = self._new_body("MapCancelBody", solid=True)
        mapped = self._profile_sketch(body, "MapCancelSketch")
        body.Tip = source
        self.document.recompute()
        self._activate_body(body, "Face6")
        expected = self._snapshot(body)
        expected_attachment = self._attachment_state(mapped)

        seen = self._drive_map_sketch_dialogs(mapped, cancel_at="sketch")
        self.assertEqual(len(seen), 1)
        self._assert_snapshot(body, expected, "cancel sketch choice")
        self.assertEqual(self._attachment_state(mapped), expected_attachment)

        seen = self._drive_map_sketch_dialogs(mapped, cancel_at="attachment")
        self.assertEqual(len(seen), 2)
        self._assert_snapshot(body, expected, "cancel attachment choice")
        self.assertEqual(self._attachment_state(mapped), expected_attachment)

    def test_map_sketch_body_face_links_the_current_tip(self):
        body, source = self._new_body("MapAcceptedBody", solid=True)
        mapped = self._profile_sketch(body, "MapAcceptedSketch")
        body.Tip = source
        self.document.recompute()
        self._activate_body(body, "Face6")
        expected = self._snapshot(body)
        expected_attachment = self._attachment_state(mapped)

        seen = self._drive_map_sketch_dialogs(mapped)
        self.assertEqual(len(seen), 2)
        self.assertNotEqual(mapped.MapMode, "Deactivated")
        self.assertEqual(len(mapped.AttachmentSupport), 1)
        support, sub_elements = mapped.AttachmentSupport[0]
        self.assertIs(support, source)
        self.assertIsNot(support, body)
        self.assertEqual(tuple(sub_elements), ("Face6",))
        self.assertNotIn(body, mapped.OutList)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.undo()
        self._process_events()
        self.assertEqual(self._attachment_state(mapped), expected_attachment)
        self._assert_snapshot(body, expected, "undo attach sketch")

    def test_subshape_binder_requires_source_and_undoes_one_command(self):
        body, tip = self._new_body("BinderTargetBody", solid=True)
        self._activate_body(body)
        Gui.Selection.clearSelection()
        self._process_events()
        expected = self._snapshot(body)

        command_name = "PartDesign_SubShapeBinder"
        self.assertFalse(Gui.isCommandActive(command_name))
        actions = Gui.Command.get(command_name).getAction()
        self.assertTrue(actions)
        self.assertFalse(actions[0].isEnabled())
        actions[0].trigger()
        self._process_events()
        self._assert_snapshot(body, expected, "binder without source")

        source_body, source_tip = self._new_body(
            "BinderSourceBody",
            solid=True,
            solid_size=8.0,
        )
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_body)
        self._process_events()
        body_source_names = tuple(obj.Name for obj in self.document.Objects)
        body_source_group = tuple(body.Group)
        body_source_tip = body.Tip
        source_group = tuple(source_body.Group)

        self.assertTrue(Gui.isCommandActive(command_name))
        Gui.runCommand(command_name, 0)
        self._process_events()

        body_binder = self.document.ActiveObject
        self.assertEqual(body_binder.TypeId, "PartDesign::SubShapeBinder")
        self.assertIsNone(body_binder.getParentGeoFeatureGroup())
        self.assertIs(body.Tip, body_source_tip)
        self.assertEqual(tuple(body.Group), body_source_group)
        self.assertEqual(tuple(source_body.Group), source_group)
        self.assertEqual(body_binder.Support[0][0], source_tip)
        self.assertIn(source_tip, body_binder.OutList)
        self.assertNotIn(source_body, body_binder.OutList)
        self.assertNotIn(body, body_binder.OutList)
        self.assertNotEqual(str(body_binder.VibeCADDefinitionId), "")
        self.assertEqual(body_binder.VibeCADTimelineRole, "operation")
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(body_binder),
            1,
        )
        self.assertTrue(body_binder.isValid(), body_binder.getStatusString())
        self.assertFalse(body_binder.Shape.isNull())
        self.assertTrue(body_binder.Shape.isValid())
        self.assertAlmostEqual(
            body_binder.Shape.Volume,
            source_tip.Shape.Volume,
            places=7,
        )
        PartDesign.validateDesign(body_binder)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.undo()
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            body_source_names,
        )
        self.assertEqual(tuple(body.Group), body_source_group)
        self.assertIs(body.Tip, body_source_tip)
        self.assertFalse(self.document.HasPendingTransaction)

        Gui.activeView().setActiveObject("pdbody", None)
        self.document.openTransaction("Create standalone source")
        source = self.document.addObject("Part::Feature", "BinderSource")
        source.Shape = Part.makeBox(6, 7, 8, App.Vector(20, 0, 0))
        self.document.recompute()
        self.document.commitTransaction()
        self.assertIsNone(source.getParentGeoFeatureGroup())
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1", 20.0, 3.5, 4.0)
        self._process_events()
        original_names = tuple(obj.Name for obj in self.document.Objects)
        original_group = tuple(body.Group)
        original_tip = body.Tip

        self.assertTrue(Gui.isCommandActive(command_name))
        Gui.runCommand(command_name, 0)
        self._process_events()

        binder = self.document.ActiveObject
        self.assertEqual(binder.TypeId, "PartDesign::SubShapeBinder")
        self.assertIsNone(binder.getParentGeoFeatureGroup())
        self.assertIs(body.Tip, original_tip)
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIn(source, binder.OutList)
        self.assertEqual(binder.Support[0][0], source)
        self.assertNotEqual(str(binder.VibeCADDefinitionId), "")
        self.assertEqual(binder.VibeCADTimelineRole, "operation")
        self.assertTrue(binder.isValid(), binder.getStatusString())
        self.assertFalse(binder.Shape.isNull())
        self.assertTrue(binder.Shape.isValid())
        PartDesign.validateDesign(binder)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.undo()
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertEqual(tuple(body.Group), original_group)
        self.assertIs(body.Tip, original_tip)
        self.assertIs(body.Tip, tip)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_datum_plane_is_one_global_definition_with_exact_lifecycle(self):
        Gui.Selection.clearSelection()
        Gui.runCommand("PartDesign_NewComponent", 0)
        self._process_events()
        component = next(
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::Component"
        )
        support = next(
            obj
            for obj in component.Origin.OriginFeatures
            if obj.Name.startswith("XY_Plane")
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(support)
        self._process_events()
        before_cancel = self._snapshot()
        self.assertTrue(Gui.isCommandActive("PartDesign_Plane"))
        Gui.runCommand("PartDesign_Plane", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())
        self._cancel_task("PartDesign_Plane")
        self._assert_snapshot(
            None,
            before_cancel,
            "cancel global datum plane",
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(support)
        self._process_events()
        names_before_accept = {
            obj.Name for obj in self.document.Objects
        }
        component_group = tuple(component.Group)
        Gui.runCommand("PartDesign_Plane", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())
        created = [
            obj
            for obj in self.document.Objects
            if obj.Name not in names_before_accept
            and obj.TypeId == "PartDesign::Plane"
        ]
        self.assertEqual(len(created), 1)
        datum = created[0]
        datum_name = datum.Name
        self._accept_task("PartDesign_Plane")

        self.assertIsNone(datum.getParentGeoFeatureGroup())
        self.assertEqual(tuple(component.Group), component_group)
        self.assertIs(datum.AttachmentSupport[0][0], support)
        self.assertNotEqual(str(datum.VibeCADDefinitionId), "")
        self.assertEqual(
            str(datum.DesignId),
            str(self.document.VibeCADTimeline.DesignId),
        )
        self.assertEqual(datum.VibeCADTimelineRole, "operation")
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(datum),
            1,
        )
        self.assertTrue(datum.isValid(), datum.getStatusString())
        PartDesign.validateDesign(datum)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(datum_name))
        self.assertEqual(tuple(component.Group), component_group)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_clone_copies_exact_body_state_and_preserves_body_placement(self):
        body, source = self._new_body("CloneSourceBody", solid=True)
        body.Placement = App.Placement(
            App.Vector(13, -7, 4),
            App.Rotation(App.Vector(0, 0, 1), 30),
        )
        self.document.recompute()
        original_names = tuple(obj.Name for obj in self.document.Objects)
        self._activate_body(body)

        self.assertTrue(Gui.isCommandActive("PartDesign_Clone"))
        Gui.runCommand("PartDesign_Clone", 0)
        self.document.recompute()
        clone = next(
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::DesignClone"
        )
        clone_body = next(
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::Body"
            and str(obj.VibeCADBodyId) == clone.OutputBodyIds[0]
        )
        self.assertIsNone(clone.getParentGeoFeatureGroup())
        self.assertIsNone(clone.BaseFeature)
        self.assertEqual(clone.ResultOperation, "New Bodies")
        self.assertEqual(clone.InputStates, [source])
        self.assertEqual(clone.OutputPreviousInputIndices, [-1])
        self.assertEqual(clone.OutputPresence, (True,))
        self.assertEqual(clone_body.TypeId, "PartDesign::Body")
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertNotIn(clone_body, timeline.Operations)
        self.assertEqual(list(timeline.Operations).count(clone), 1)
        self.assertEqual(
            clone_body.Tip.TypeId,
            "PartDesign::DesignBodyPublication",
        )
        self.assertEqual(clone_body.Group, [clone_body.Tip])
        self.assertIs(clone_body.Tip.CurrentState.Operation, clone)
        self.assertFalse(
            any(
                obj.TypeId == "PartDesign::FeatureBase"
                for obj in self.document.Objects
                if obj.Name not in original_names
            )
        )
        self.assertEqual(clone_body.Placement, body.Placement)
        self.assertTrue(clone.isValid(), clone.getStatusString())
        self.assertTrue(clone.Shape.isNull())
        self.assertFalse(clone.PreviewShape.isNull())
        source_bounds = body.Shape.BoundBox
        clone_bounds = clone_body.Shape.BoundBox
        for attribute in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax"):
            self.assertAlmostEqual(
                getattr(clone_bounds, attribute),
                getattr(source_bounds, attribute),
                places=7,
            )

        previous_volume = clone_body.Shape.Volume
        source.Shape = Part.makeBox(14, 9, 7)
        self.document.recompute()
        self.assertNotAlmostEqual(clone_body.Shape.Volume, previous_volume)
        self.assertAlmostEqual(clone_body.Shape.Volume, source.Shape.Volume)
        PartDesign.validateDesign(clone)

        clone_name = clone.Name
        clone_body_name = clone_body.Name
        identities = (
            str(clone.OperationId),
            str(clone_body.VibeCADBodyId),
            str(clone_body.Tip.CurrentState.BodyStateId),
        )
        self.document.undo()
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.redo()
        self._process_events()
        restored_clone = self.document.getObject(clone_name)
        restored_body = self.document.getObject(clone_body_name)
        restored_timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(restored_clone)
        self.assertIsNotNone(restored_body)
        self.assertIsNotNone(restored_timeline)
        self.assertNotIn(restored_body, restored_timeline.Operations)
        self.assertEqual(
            list(restored_timeline.Operations).count(restored_clone),
            1,
        )
        self.assertEqual(
            (
                str(restored_clone.OperationId),
                str(restored_body.VibeCADBodyId),
                str(restored_body.Tip.CurrentState.BodyStateId),
            ),
            identities,
        )
        PartDesign.validateDesign(restored_clone)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_clone_rejects_an_assembly_occurrence_as_modeling_input(self):
        body, _source = self._new_body("CloneLinkDefinition", solid=True)
        occurrence = self.document.addObject("App::Link", "CloneOccurrence")
        occurrence.LinkedObject = body
        occurrence.Placement = App.Placement(
            App.Vector(25, 7, 3),
            App.Rotation(),
        )
        self.document.recompute()
        original_names = tuple(obj.Name for obj in self.document.Objects)
        Gui.Selection.addSelection(occurrence)
        self._process_events()

        self.assertFalse(Gui.isCommandActive("PartDesign_Clone"))
        Gui.runCommand("PartDesign_Clone", 0)
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_scale_is_one_global_modify_operation_with_exact_lifecycle(self):
        body, source = self._new_body("ScaleBody", solid=True)
        self._launch_and_cancel_exact(
            "PartDesign_Scale",
            body,
            (body,),
        )

        self._activate_body(body)
        self.assertTrue(Gui.isCommandActive("PartDesign_Scale"))
        Gui.runCommand("PartDesign_Scale", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())

        scale = self.document.ActiveObject
        self.assertEqual(scale.TypeId, "PartDesign::DesignScale")
        self.assertIsNone(scale.getParentGeoFeatureGroup())
        self.assertEqual(scale.ResultOperation, "Modify")
        self.assertEqual(list(scale.InputStates), [source])
        self.assertEqual(
            list(scale.OutputBodyIds),
            [str(body.VibeCADBodyId)],
        )
        self.assertNotIn(scale, body.Group)

        factor = Gui.getMainWindow().findChild(
            QtGui.QDoubleSpinBox,
            "DesignScaleUniformFactor",
        )
        center_values = [
            Gui.getMainWindow().findChild(
                QtGui.QDoubleSpinBox,
                f"DesignScaleCenter{axis}",
            )
            for axis in ("X", "Y", "Z")
        ]
        self.assertIsNotNone(factor)
        self.assertTrue(all(value is not None for value in center_values))
        factor.setValue(2.0)
        for value in center_values:
            value.setValue(5.0)
        self._process_events(80)

        self.assertTrue(scale.isValid(), scale.getStatusString())
        self.assertEqual(len(scale.OutputShapes), 1)
        self.assertAlmostEqual(scale.OutputShapes[0].Volume, 8000.0)
        self._accept_task("PartDesign_Scale")
        self.document.recompute()

        self.assertEqual(
            body.Tip.TypeId,
            "PartDesign::DesignBodyPublication",
        )
        self.assertIn(source, body.Group)
        self.assertIn(body.Tip, body.Group)
        self.assertNotIn(scale, body.Group)
        self.assertIs(body.Tip.CurrentState.Operation, scale)
        self.assertAlmostEqual(body.Shape.Volume, 8000.0)
        self.assertFalse(
            any(obj.TypeId == "Part::Scale" for obj in self.document.Objects)
        )
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertEqual(list(timeline.Operations).count(scale), 1)
        PartDesign.validateDesign(scale)

        scale_name = scale.Name
        operation_id = str(scale.OperationId)
        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(scale_name))
        self.assertIs(body.Tip, source)
        self.assertAlmostEqual(body.Shape.Volume, 1000.0)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.redo()
        self._process_events()
        restored = self.document.getObject(scale_name)
        self.assertIsNotNone(restored)
        self.assertEqual(str(restored.OperationId), operation_id)
        self.assertIs(body.Tip.CurrentState.Operation, restored)
        self.assertAlmostEqual(body.Shape.Volume, 8000.0)
        PartDesign.validateDesign(restored)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_edit_sketch_cancel_restores_geometry_and_interaction_state(self):
        body, _source = self._new_body("EditSketchBody", solid=True)
        sketch = self._profile_sketch(body, "EditableRibbonSketch")
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch)
        self._process_events()
        self.assertTrue(Gui.isCommandActive("Sketcher_EditSketch"))
        expected = self._snapshot(body)
        expected_geometry = self._line_geometry_state(sketch)
        expected_label = sketch.Label

        Gui.runCommand("PartDesign_CompSketches", 2)
        self._process_events()
        self.assertIsNotNone(Gui.activeDocument().getInEdit())
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(20, 20, 0),
                App.Vector(30, 25, 0),
            ),
            False,
        )
        sketch.Label = "Discarded edit"
        self.assertNotEqual(
            self._line_geometry_state(sketch),
            expected_geometry,
        )

        Gui.runCommand("Sketcher_CancelSketch", 0)
        self._process_events(50)
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertEqual(self._line_geometry_state(sketch), expected_geometry)
        self.assertEqual(sketch.Label, expected_label)
        self._assert_snapshot(body, expected, "cancel edited sketch")

    def test_validate_sketch_close_is_an_exact_read_only_operation(self):
        body, _source = self._new_body("ValidateSketchBody", solid=True)
        sketch = self._profile_sketch(body, "ValidatedRibbonSketch")
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch, "Edge1", 2.0, 2.0, 0.0)
        self._process_events()
        self.assertTrue(Gui.isCommandActive("Sketcher_ValidateSketch"))
        expected = self._snapshot(body)
        expected_geometry = self._line_geometry_state(sketch)

        Gui.runCommand("Sketcher_ValidateSketch", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self._dismiss_task("validate sketch", allow_close=True)

        self.assertEqual(self._line_geometry_state(sketch), expected_geometry)
        self._assert_snapshot(body, expected, "close validate sketch")

    def test_sketch_setup_does_not_follow_a_link_to_its_shared_definition(self):
        source = self.document.addObject(
            "Sketcher::SketchObject",
            "SharedSketchDefinition",
        )
        source.addGeometry(
            Part.LineSegment(
                App.Vector(0, 0, 0),
                App.Vector(8, 3, 0),
            ),
            False,
        )
        occurrence = self.document.addObject(
            "App::Link",
            "SharedSketchOccurrence",
        )
        occurrence.LinkedObject = source
        self.document.recompute()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(occurrence)
        self._process_events()
        expected = self._snapshot()

        for command_name in (
            "Sketcher_EditSketch",
            "Sketcher_ReorientSketch",
            "Sketcher_ValidateSketch",
        ):
            self.assertFalse(
                Gui.isCommandActive(command_name),
                command_name,
            )
            Gui.runCommand(command_name, 0)
            self._assert_snapshot(
                None,
                expected,
                f"{command_name} linked occurrence",
            )

    def test_all_additive_and_subtractive_primitive_children_accept(self):
        for subtractive, command_name, type_prefix in (
            (False, "PartDesign_CompPrimitiveAdditive", "Additive"),
            (True, "PartDesign_CompPrimitiveSubtractive", "Subtractive"),
        ):
            for index, shape_name in enumerate(PRIMITIVE_SHAPES):
                body, base = self._new_body(
                    f"{type_prefix}{shape_name}Body",
                    solid=subtractive,
                    solid_size=40.0 if subtractive else 10.0,
                )
                base_volume = base.Shape.Volume if base is not None else None
                self._activate_body(body)
                self.assertTrue(Gui.isCommandActive(command_name), shape_name)

                Gui.runCommand(command_name, index)
                self.assertTrue(Gui.Control.activeDialog(), shape_name)
                feature = self.document.ActiveObject
                self.assertEqual(
                    feature.TypeId,
                    f"PartDesign::{type_prefix}{shape_name}",
                    shape_name,
                )
                self.assertIs(feature.getParentGeoFeatureGroup(), body, shape_name)
                if subtractive:
                    self.assertIs(feature.BaseFeature, base, shape_name)
                self.document.recompute()
                self.assertTrue(feature.isValid(), (shape_name, feature.getStatusString()))
                self.assertFalse(feature.Shape.isNull(), shape_name)

                self._accept_task(f"{command_name}:{shape_name}")
                self.document.recompute()
                self.assertIs(body.Tip, feature, shape_name)
                self.assertIn(feature, body.Group, shape_name)
                self.assertTrue(feature.isValid(), (shape_name, feature.getStatusString()))
                self.assertFalse(feature.Shape.isNull(), shape_name)
                self.assertEqual(len(feature.Shape.Solids), 1, shape_name)
                if subtractive:
                    self.assertLess(feature.Shape.Volume, base_volume, shape_name)

    def test_unified_primitives_create_global_design_operations(self):
        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        self._process_events()
        self.assertTrue(Gui.isCommandActive("PartDesign_DesignPrimitive"))

        for index, shape_name in enumerate(DESIGN_PRIMITIVE_SHAPES):
            before_bodies = set(self._bodies())
            Gui.runCommand("PartDesign_DesignPrimitive", index)
            self._process_events(50)
            self.assertTrue(Gui.Control.activeDialog(), shape_name)

            operation = self.document.ActiveObject
            self.assertEqual(
                operation.TypeId,
                f"PartDesign::Design{shape_name}",
            )
            self.assertIsNone(operation.getParentGeoFeatureGroup())
            self.assertIsNone(operation.BaseFeature)
            self.assertEqual(operation.ResultOperation, "New Body")
            self.assertEqual(list(operation.InputStates), [])
            self.assertEqual(
                list(operation.OutputPreviousInputIndices),
                [-1],
            )
            self.assertEqual(
                set(self._bodies()),
                before_bodies,
                "a provisional operation must not create structural Bodies",
            )

            self._accept_task(f"PartDesign_DesignPrimitive:{shape_name}")
            self.document.recompute()
            created = [
                body
                for body in self._bodies()
                if body not in before_bodies
            ]
            self.assertEqual(len(created), 1, shape_name)
            body = created[0]
            self.assertEqual(
                str(body.VibeCADBodyId),
                str(operation.OutputBodyIds[0]),
            )
            self.assertEqual(
                body.Tip.TypeId,
                "PartDesign::DesignBodyPublication",
                (
                    body.Tip.Name,
                    body.Tip.TypeId,
                    [
                        (member.Name, member.TypeId)
                        for member in body.Group
                    ],
                ),
            )
            self.assertIs(body.Tip.CurrentState.Operation, operation)
            self.assertTrue(body.isValid(), body.getStatusString())
            self.assertEqual(len(body.Shape.Solids), 1)
            PartDesign.validateDesign(operation)

        original_names = tuple(
            obj.Name for obj in self.document.Objects
        )
        Gui.runCommand("PartDesign_DesignPrimitive", 0)
        self._process_events(50)
        self._cancel_task("PartDesign_DesignPrimitive cancel")
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )

        target, previous = self._new_body(
            "DesignPrimitiveJoinBody",
            solid=True,
        )
        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(target)
        self._process_events()
        Gui.runCommand("PartDesign_DesignPrimitive", 0)
        self._process_events(50)
        operation = self.document.ActiveObject
        self.assertEqual(operation.TypeId, "PartDesign::DesignBox")
        self.assertEqual(operation.ResultOperation, "Join")
        self.assertEqual(list(operation.InputStates), [previous])
        operation.Placement.Base.x = 5
        self.document.recompute()
        self._accept_task("PartDesign_DesignPrimitive Join")
        self.document.recompute()
        self.assertGreater(target.Shape.Volume, previous.Shape.Volume)
        self.assertIs(target.Tip.CurrentState.Operation, operation)
        PartDesign.validateDesign(operation)

    def test_additive_cancel_removes_its_automatically_created_body(self):
        self.assertEqual(
            self._bodies(),
            [],
        )
        original_names = tuple(obj.Name for obj in self.document.Objects)
        self.assertFalse(self.document.HasPendingTransaction)

        Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(
            len(self._bodies()),
            1,
        )
        self._cancel_task("automatic Body additive primitive")

        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertEqual(
            self._bodies(),
            [],
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_additive_auto_body_is_structural_and_one_history_operation(self):
        original_names = tuple(obj.Name for obj in self.document.Objects)

        Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
        self.assertTrue(Gui.Control.activeDialog())
        bodies = self._bodies()
        self.assertEqual(len(bodies), 1)
        body = bodies[0]
        feature = self.document.ActiveObject
        self.assertIs(feature.getParentGeoFeatureGroup(), body)
        self.assertEqual(body.VibeCADTimelineRole, "internal")
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertNotIn(body, timeline.Operations)
        self.assertEqual(list(timeline.Operations).count(feature), 1)

        body_name = body.Name
        feature_name = feature.Name
        self._accept_task("automatic Body additive primitive")
        self.assertNotIn(body, timeline.Operations)
        self.assertEqual(list(timeline.Operations).count(feature), 1)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.undo()
        self._process_events()
        self.assertEqual(
            tuple(obj.Name for obj in self.document.Objects),
            original_names,
        )
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.redo()
        self._process_events()
        restored_body = self.document.getObject(body_name)
        restored_feature = self.document.getObject(feature_name)
        restored_timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(restored_body)
        self.assertIsNotNone(restored_feature)
        self.assertIsNotNone(restored_timeline)
        self.assertEqual(restored_body.VibeCADTimelineRole, "internal")
        self.assertNotIn(restored_body, restored_timeline.Operations)
        self.assertEqual(
            list(restored_timeline.Operations).count(restored_feature),
            1,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_subtractive_primitives_require_the_current_tip_to_be_a_solid(self):
        body, _base = self._new_body("SubtractiveStateBody", solid=True)
        wire_tip = body.newObject("PartDesign::Feature", "WireTip")
        wire_tip.Shape = Part.makePolygon(
            [
                App.Vector(0, 0, 0),
                App.Vector(5, 0, 0),
                App.Vector(5, 5, 0),
            ]
        )
        body.Tip = wire_tip
        self.document.recompute()
        self._activate_body(body)

        command_name = "PartDesign_CompPrimitiveSubtractive"
        self.assertFalse(Gui.isCommandActive(command_name))
        expected = self._snapshot(body)
        actions = Gui.Command.get(command_name).getAction()
        for action in actions:
            self.assertFalse(action.isEnabled())
            action.trigger()
        # Compatibility-only commands remain callable for old macros even
        # when the consolidated ribbon deliberately exposes no QAction.
        Gui.runCommand(command_name, 0)
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())
        self._assert_snapshot(body, expected, command_name)

        solid_tip = body.newObject("PartDesign::Feature", "RestoredSolidTip")
        solid_tip.Shape = Part.makeBox(10, 10, 10)
        body.Tip = solid_tip
        self.document.recompute()
        self._activate_body(body)
        self.assertTrue(Gui.isCommandActive(command_name))

    def test_finish_tools_require_real_subelements_and_lock_during_tasks(self):
        body, _base = self._new_body("FinishStateBody", solid=True)
        finish_commands = tuple(case[0] for case in FINISH_COMMANDS)

        Gui.Selection.clearSelection()
        for command_name in finish_commands:
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        self._activate_body(body)
        for command_name in finish_commands:
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        self._activate_body(body, "Vertex1")
        for command_name in finish_commands:
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        self._activate_body(body, "Edge1")
        self.assertTrue(Gui.isCommandActive("PartDesign_Fillet"))
        self.assertTrue(Gui.isCommandActive("PartDesign_Chamfer"))
        self.assertFalse(Gui.isCommandActive("PartDesign_Thickness"))

        self._activate_body(body, "Face1")
        for command_name in finish_commands:
            self.assertTrue(Gui.isCommandActive(command_name), command_name)

        Gui.Selection.clearSelection()
        self._process_events()
        expected = self._snapshot(body)
        Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
        self.assertTrue(Gui.Control.activeDialog())
        for command_name in finish_commands:
            self.assertFalse(Gui.isCommandActive(command_name), command_name)
        self._cancel_task("unrelated additive primitive")
        self._assert_snapshot(body, expected, "unrelated additive primitive")

    def test_chamfer_without_an_edge_is_a_strict_no_op(self):
        body, _base = self._new_body("ChamferNoEdgeBody", solid=True)
        self._activate_body(body)
        expected = self._snapshot(body)

        self.assertFalse(Gui.isCommandActive("PartDesign_Chamfer"))
        actions = Gui.Command.get("PartDesign_Chamfer").getAction()
        self.assertTrue(actions)
        self.assertFalse(actions[0].isEnabled())
        actions[0].trigger()
        self._process_events(50)

        self.assertFalse(Gui.Control.activeDialog())
        self._assert_snapshot(body, expected, "PartDesign_Chamfer without an edge")

    def test_new_sketch_on_body_face_uses_tip_and_cancel_is_exact(self):
        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/PartDesign"
        )
        previous = preferences.GetBool("NewSketchUseAttachmentDialog", False)
        preferences.SetBool("NewSketchUseAttachmentDialog", False)
        try:
            body, source = self._new_body("SketchFaceBody", solid=True)
            self._activate_body(body, "Face6")
            expected = self._snapshot(body)

            Gui.runCommand("PartDesign_CompSketches", 0)
            self._process_events(50)
            sketch = self.document.ActiveObject
            self.assertIsNotNone(sketch)
            self.assertTrue(sketch.isDerivedFrom("Sketcher::SketchObject"))
            self.assertIs(sketch.getParentGeoFeatureGroup(), body)
            support = sketch.AttachmentSupport
            self.assertEqual(len(support), 1)
            support_object, support_elements = support[0]
            self.assertIs(support_object, source)
            self.assertIsNot(support_object, body)
            self.assertEqual(tuple(support_elements), ("Face6",))
            self.assertIsNotNone(Gui.activeDocument().getInEdit())

            Gui.runCommand("Sketcher_CancelSketch", 0)
            self._process_events(50)
            self.assertIsNone(Gui.activeDocument().getInEdit())
            self._assert_snapshot(body, expected, "cancel new face-supported sketch")
        finally:
            preferences.SetBool("NewSketchUseAttachmentDialog", previous)

    def test_new_sketch_on_body_face_accepts_into_the_same_body(self):
        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/PartDesign"
        )
        previous = preferences.GetBool("NewSketchUseAttachmentDialog", False)
        preferences.SetBool("NewSketchUseAttachmentDialog", False)
        try:
            body, source = self._new_body("AcceptedSketchFaceBody", solid=True)
            self._activate_body(body, "Face6")

            Gui.runCommand("PartDesign_CompSketches", 0)
            self._process_events(50)
            sketch = self.document.ActiveObject
            self.assertTrue(sketch.isDerivedFrom("Sketcher::SketchObject"))
            self.assertEqual(len(sketch.AttachmentSupport), 1)
            support_object, support_elements = sketch.AttachmentSupport[0]
            self.assertIs(support_object, source)
            self.assertEqual(tuple(support_elements), ("Face6",))

            Gui.runCommand("Sketcher_LeaveSketch", 0)
            self._process_events(50)
            self.assertIsNone(Gui.activeDocument().getInEdit())
            self.assertIn(sketch, body.Group)
            self.assertIs(sketch.getParentGeoFeatureGroup(), body)
            support_object, support_elements = sketch.AttachmentSupport[0]
            self.assertIs(support_object, source)
            self.assertEqual(tuple(support_elements), ("Face6",))
            self.assertTrue(sketch.isValid(), sketch.getStatusString())
            self.assertFalse(self.document.HasPendingTransaction)
        finally:
            preferences.SetBool("NewSketchUseAttachmentDialog", previous)

    def test_shipped_new_sketch_is_one_global_reusable_history_definition(self):
        body, source = self._new_body(
            "GlobalSketchSupportBody",
            solid=True,
        )

        def launch():
            self._activate_body(body, "Face6")
            dialog_state = {"complete": False}

            def accept_default_attachment():
                if dialog_state["complete"]:
                    return
                for widget in QtGui.QApplication.topLevelWidgets():
                    if (
                        isinstance(widget, QtGui.QInputDialog)
                        and widget.isVisible()
                    ):
                        widget.accept()
                        return
                QtCore.QTimer.singleShot(
                    10,
                    accept_default_attachment,
                )

            QtCore.QTimer.singleShot(0, accept_default_attachment)
            Gui.runCommand("Sketcher_NewSketch", 0)
            dialog_state["complete"] = True
            self._process_events(50)
            self.assertIsNotNone(Gui.activeDocument().getInEdit())
            sketch = self.document.ActiveObject
            self.assertTrue(
                sketch.isDerivedFrom("Sketcher::SketchObject")
            )
            return sketch

        self._activate_body(body, "Face6")
        body_group_before_cancel = tuple(body.Group)
        before_cancel = self._snapshot(body)
        cancelled = launch()
        self.assertIsNone(cancelled.getParentGeoFeatureGroup())
        self.assertEqual(tuple(body.Group), body_group_before_cancel)
        Gui.runCommand("Sketcher_CancelSketch", 0)
        self._process_events(50)
        self._assert_snapshot(
            body,
            before_cancel,
            "cancel global Sketch",
        )

        body_group = tuple(body.Group)
        names_before_accept = {
            obj.Name for obj in self.document.Objects
        }
        sketch = launch()
        self.assertNotIn(sketch.Name, names_before_accept)
        self.assertIsNone(sketch.getParentGeoFeatureGroup())
        self.assertEqual(tuple(body.Group), body_group)
        self.assertEqual(len(sketch.AttachmentSupport), 1)
        support_object, support_elements = sketch.AttachmentSupport[0]
        self.assertIs(support_object, source)
        self.assertEqual(tuple(support_elements), ("Face6",))

        Gui.runCommand("Sketcher_LeaveSketch", 0)
        self._process_events(50)
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertIsNone(sketch.getParentGeoFeatureGroup())
        self.assertEqual(tuple(body.Group), body_group)
        self.assertNotEqual(str(sketch.VibeCADSketchId), "")
        self.assertEqual(
            str(sketch.DesignId),
            str(self.document.VibeCADTimeline.DesignId),
        )
        self.assertEqual(sketch.VibeCADTimelineRole, "operation")
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(sketch),
            1,
        )
        PartDesign.validateDesign(sketch)

        sketch.setPropertyStatus(
            "VibeCADTimelineRole",
            "-LockDynamic",
        )
        sketch.removeProperty("VibeCADTimelineRole")
        Gui.activeDocument().setEdit(sketch.Name)
        self._process_events(50)
        self.assertIsNotNone(Gui.activeDocument().getInEdit())
        Gui.runCommand("Sketcher_LeaveSketch", 0)
        self._process_events(50)
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertEqual(sketch.VibeCADTimelineRole, "operation")
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(sketch),
            1,
        )
        PartDesign.validateDesign(sketch)

        sketch_name = sketch.Name
        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(sketch_name))
        self.assertEqual(tuple(body.Group), body_group)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_profile_tasks_cancel_back_to_the_exact_body_history(self):
        for index, (
            command_name,
            feature_type,
            subtractive,
            input_kind,
        ) in enumerate(PROFILE_COMMANDS):
            body, base, _profile, selections = self._profile_command_inputs(
                index,
                subtractive=subtractive,
                input_kind=input_kind,
                prefix="Cancel",
            )
            Gui.activeView().setActiveObject("pdbody", body)
            Gui.Selection.clearSelection()
            for selected in selections:
                Gui.Selection.addSelection(selected)
            self._process_events()
            self.assertTrue(Gui.isCommandActive(command_name), command_name)
            expected = self._snapshot(body)

            Gui.runCommand(command_name, 0)
            self._process_events(50)
            self.assertTrue(Gui.Control.activeDialog(), command_name)
            feature = self.document.ActiveObject
            self.assertEqual(feature.TypeId, feature_type, command_name)
            if feature_type.startswith("PartDesign::Design"):
                self.assertIsNone(
                    feature.getParentGeoFeatureGroup(),
                    command_name,
                )
                self.assertNotIn(feature, body.Group, command_name)
            else:
                self.assertIs(
                    feature.getParentGeoFeatureGroup(),
                    body,
                    command_name,
                )
            if subtractive and not feature_type.startswith("PartDesign::Design"):
                self.assertIs(feature.BaseFeature, base, command_name)

            self._cancel_task(command_name)
            self._assert_snapshot(body, expected, command_name)

    def test_retained_geometry_task_dialogs_cancel_or_close_exactly(self):
        anchor_body, _anchor_tip = self._new_body("TaskAnchorBody", solid=True)
        wire_body, _wire_tip = self._wire_body("TaskWireBody", x=20.0)
        solid_body_2, _solid_tip_2 = self._new_body(
            "TaskSecondSolidBody",
            solid=True,
        )
        solid_body_2.Placement.Base.x = 20.0
        self.document.recompute()

        for command_name, selections, allow_close in (
            ("Part_Tube", (anchor_body,), False),
            ("Part_Primitives", (anchor_body,), True),
            # Shape Builder creates zero or more shapes while it remains open;
            # its native terminal action is Close (a reject-role button).
            ("Part_Builder", (anchor_body,), True),
            ("Part_CrossSections", (anchor_body,), False),
            ("Part_Boolean", (anchor_body, solid_body_2), True),
            ("Part_ProjectionOnSurface", (anchor_body,), False),
        ):
            self._launch_and_cancel_exact(
                command_name,
                anchor_body,
                selections,
                allow_close=allow_close,
            )

        self._launch_and_cancel_exact(
            "Part_CompOffset",
            anchor_body,
            (anchor_body,),
            index=0,
        )
        self._launch_and_cancel_exact(
            "Part_CompOffset",
            wire_body,
            (wire_body,),
            index=1,
        )

    def test_cancel_restores_picked_position_after_selection_gates_are_removed(self):
        body, _source = self._new_body("PickedPositionBody", solid=True)
        self._launch_and_cancel_exact(
            "PartDesign_Chamfer",
            body,
            ((body, "Edge1", 1.25, 2.5, 3.75),),
        )

    def test_body_selection_transforms_the_current_tip_and_accepts(self):
        for command_name, feature_type in TRANSFORM_COMMANDS:
            body, source = self._new_body(
                f"{feature_type.rsplit('::', 1)[-1]}Body",
                solid=True,
            )
            self._activate_body(body)
            selected = Gui.Selection.getSelectionEx()
            self.assertEqual(len(selected), 1, command_name)
            self.assertIs(selected[0].Object, body, command_name)
            self.assertTrue(Gui.isCommandActive(command_name), command_name)

            Gui.runCommand(command_name, 0)
            self.assertTrue(Gui.Control.activeDialog(), command_name)
            feature = self.document.ActiveObject
            self.assertEqual(feature.TypeId, feature_type, command_name)
            self.assertIs(feature.getParentGeoFeatureGroup(), body, command_name)
            self.assertEqual(feature.TransformMode, "Whole shape", command_name)
            self.assertEqual(list(feature.Originals), [], command_name)
            self.assertIs(feature.BaseFeature, source, command_name)
            for diagnostic in (
                "GeneratedOccurrenceCount",
                "RejectedSolidCount",
            ):
                status = set(feature.getPropertyStatus(diagnostic))
                # Static property-type flags are exposed by the Python API as
                # their bit positions (Prop_ReadOnly=24, Prop_Output=27);
                # dynamically assigned equivalents are exposed by name.
                self.assertTrue(
                    {"Output", 27} & status,
                    (command_name, diagnostic),
                )
                self.assertTrue(
                    {"ReadOnly", 24} & status,
                    (command_name, diagnostic),
                )

            self._configure_transform_for_accept(command_name, feature)
            self.document.recompute()
            self.assertTrue(feature.isValid(), (command_name, feature.getStatusString()))
            self.assertFalse(feature.Shape.isNull(), command_name)
            self._accept_task(command_name)
            self.document.recompute()
            self.assertIs(body.Tip, feature, command_name)
            self.assertTrue(feature.isValid(), (command_name, feature.getStatusString()))
            self.assertFalse(feature.Shape.isNull(), command_name)
            self.assertGreaterEqual(len(feature.Shape.Solids), 1, command_name)

    def test_design_body_patterns_create_independent_global_body_outputs(self):
        body, source = self._new_body(
            "GlobalBodyPatternSource",
            solid=True,
        )
        before_bodies = set(self._bodies())
        self._activate_body(body)

        command_name = "PartDesign_DesignLinearPattern"
        self.assertTrue(Gui.isCommandActive(command_name))
        Gui.runCommand(command_name, 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())

        operation = self.document.ActiveObject
        self.assertEqual(
            operation.TypeId,
            "PartDesign::DesignLinearPattern",
        )
        self.assertIsNone(operation.getParentGeoFeatureGroup())
        self.assertEqual(operation.PatternSource, "Body")
        self.assertEqual(operation.ResultOperation, "New Bodies")
        self.assertEqual(list(operation.InputStates), [source])
        self.assertEqual(
            str(operation.InputBodyIds[0]),
            str(body.VibeCADBodyId),
        )
        self.assertEqual(
            set(self._bodies()),
            before_bodies,
            "provisional Body Pattern outputs must not create Bodies",
        )

        occurrences = Gui.getMainWindow().findChild(
            QtGui.QSpinBox,
            "DesignPatternOccurrences",
        )
        source_mode = Gui.getMainWindow().findChild(
            QtGui.QComboBox,
            "DesignPatternSourceMode",
        )
        source_object = Gui.getMainWindow().findChild(
            QtGui.QComboBox,
            "DesignPatternSourceObject",
        )
        target_list = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "DesignBodyList",
        )
        self.assertIsNotNone(occurrences)
        self.assertIsNotNone(source_mode)
        self.assertIsNotNone(source_object)
        self.assertIsNotNone(target_list)
        self.assertEqual(source_mode.currentData(), "Body")
        self.assertEqual(
            source_object.currentData(),
            str(body.VibeCADBodyId),
        )
        self.assertFalse(target_list.isEnabled())

        occurrences.setValue(4)
        self._process_events(80)
        self.assertEqual(operation.Occurrences, 4)
        self.assertEqual(len(operation.OutputBodyIds), 3)
        self.assertEqual(
            list(operation.OutputPreviousInputIndices),
            [-1, -1, -1],
        )
        self.assertEqual(set(self._bodies()), before_bodies)

        self._accept_task(command_name)
        self.document.recompute()
        generated_bodies = [
            candidate
            for candidate in self._bodies()
            if candidate not in before_bodies
        ]
        self.assertEqual(len(generated_bodies), 3)
        self.assertEqual(
            {str(candidate.VibeCADBodyId) for candidate in generated_bodies},
            {str(identity) for identity in operation.OutputBodyIds},
        )
        self.assertEqual(
            sorted(
                round(candidate.Shape.BoundBox.XMin, 6)
                for candidate in generated_bodies
            ),
            [10.0, 20.0, 30.0],
        )
        self.assertTrue(
            all(
                candidate.Tip.TypeId
                == "PartDesign::DesignBodyPublication"
                and candidate.Tip.CurrentState.Operation is operation
                for candidate in generated_bodies
            )
        )
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertEqual(list(timeline.Operations).count(operation), 1)
        PartDesign.validateDesign(operation)

    def test_design_feature_pattern_uses_exact_source_and_reference_state(self):
        Gui.activeView().setActiveObject("pdbody", None)
        Gui.Selection.clearSelection()
        Gui.runCommand("PartDesign_DesignPrimitive", 0)
        self._process_events(50)
        source_operation = self.document.ActiveObject
        self.assertEqual(source_operation.TypeId, "PartDesign::DesignBox")
        self._accept_task("create feature-pattern source")
        self.document.recompute()

        source_body = next(
            body
            for body in self._bodies()
            if str(body.VibeCADBodyId)
            == str(source_operation.OutputBodyIds[0])
        )
        exact_source_state = source_body.Tip.CurrentState

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_operation)
        self._process_events()
        command_name = "PartDesign_DesignLinearPattern"
        self.assertTrue(Gui.isCommandActive(command_name))
        Gui.runCommand(command_name, 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())

        operation = self.document.ActiveObject
        self.assertEqual(
            operation.TypeId,
            "PartDesign::DesignLinearPattern",
        )
        self.assertEqual(operation.PatternSource, "Feature")
        self.assertIs(operation.SourceOperation, source_operation)
        self.assertEqual(operation.ResultOperation, "Join")
        self.assertEqual(list(operation.InputStates), [exact_source_state])
        self.assertEqual(
            list(operation.OutputBodyIds),
            [str(source_body.VibeCADBodyId)],
        )
        self.assertIsNone(operation.getParentGeoFeatureGroup())

        occurrences = Gui.getMainWindow().findChild(
            QtGui.QSpinBox,
            "DesignPatternOccurrences",
        )
        reference_button = Gui.getMainWindow().findChild(
            QtGui.QPushButton,
            "DesignPatternUseReference",
        )
        target_list = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "DesignBodyList",
        )
        self.assertIsNotNone(occurrences)
        self.assertIsNotNone(reference_button)
        self.assertIsNotNone(target_list)
        self.assertTrue(target_list.isEnabled())

        occurrences.setValue(3)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source_body, "Edge1")
        raw_reference_selection = Gui.Selection.getSelectionEx()
        self.assertEqual(
            len(raw_reference_selection),
            1,
            "the Pattern task must allow geometric reference selection",
        )
        self.assertIs(raw_reference_selection[0].Object, source_body)
        self.assertEqual(
            raw_reference_selection[0].SubElementNames,
            ("Edge1",),
        )
        reference_warnings = []
        reference_finished = {"value": False}

        def capture_reference_warning():
            if reference_finished["value"]:
                return
            for dialog in QtGui.QApplication.topLevelWidgets():
                if (
                    isinstance(dialog, QtGui.QMessageBox)
                    and dialog.isVisible()
                ):
                    reference_warnings.append(
                        (dialog.windowTitle(), dialog.text())
                    )
                    dialog.accept()
                    return
            QtCore.QTimer.singleShot(10, capture_reference_warning)

        QtCore.QTimer.singleShot(0, capture_reference_warning)
        reference_button.click()
        reference_finished["value"] = True
        self._process_events(80)
        self.assertEqual(reference_warnings, [])
        reference_object, reference_subelements = (
            operation.DirectionReference
        )
        self.assertIs(reference_object, exact_source_state)
        self.assertEqual(reference_subelements, ["Edge1"])
        self.assertIsNot(reference_object, source_body)
        self.assertIsNot(reference_object, source_body.Tip)
        self.assertTrue(operation.isValid(), operation.getStatusString())

        self._accept_task(command_name)
        self.document.recompute()
        self.assertIs(source_body.Tip.CurrentState.Operation, operation)
        self.assertAlmostEqual(source_body.Shape.Volume, 3000.0)
        PartDesign.validateDesign(operation)

    def test_design_pattern_cancel_restores_the_exact_document(self):
        for index, (command_name, feature_type) in enumerate(
            DESIGN_PATTERN_COMMANDS
        ):
            body, source = self._new_body(
                f"CancelledDesignPatternBody{index}",
                solid=True,
            )
            self._activate_body(body)
            expected = self._snapshot(body)
            self.assertTrue(Gui.isCommandActive(command_name), command_name)

            Gui.runCommand(command_name, 0)
            self._process_events(50)
            self.assertTrue(Gui.Control.activeDialog(), command_name)
            operation = self.document.ActiveObject
            self.assertEqual(operation.TypeId, feature_type, command_name)
            self.assertEqual(operation.PatternSource, "Body", command_name)
            self.assertEqual(list(operation.InputStates), [source])
            self.assertIsNone(operation.getParentGeoFeatureGroup())

            self._cancel_task(command_name)
            self._assert_snapshot(body, expected, command_name)

    def test_every_covered_task_cancel_restores_body_and_transaction(self):
        for command_name, type_prefix, subtractive in (
            ("PartDesign_CompPrimitiveAdditive", "Additive", False),
            ("PartDesign_CompPrimitiveSubtractive", "Subtractive", True),
        ):
            for index, shape_name in enumerate(PRIMITIVE_SHAPES):
                body, _base = self._new_body(
                    f"Cancel{type_prefix}{shape_name}Body",
                    solid=subtractive,
                    solid_size=40.0 if subtractive else 10.0,
                )
                self._activate_body(body)
                expected = self._snapshot(body)
                Gui.runCommand(command_name, index)
                self.assertTrue(Gui.Control.activeDialog(), shape_name)
                self._cancel_task(f"{command_name}:{shape_name}")
                self._assert_snapshot(body, expected, f"{command_name}:{shape_name}")

        for command_name, feature_type, subelement in FINISH_COMMANDS:
            body, _base = self._new_body(
                f"Cancel{feature_type.rsplit('::', 1)[-1]}Body",
                solid=True,
            )
            self._activate_body(body, subelement)
            expected = self._snapshot(body)
            Gui.runCommand(command_name, 0)
            self.assertTrue(Gui.Control.activeDialog(), command_name)
            self.assertEqual(self.document.ActiveObject.TypeId, feature_type, command_name)
            self._cancel_task(command_name)
            self._assert_snapshot(body, expected, command_name)

        draft_body, _draft_base = self._new_body(
            "CancelDraftBody",
            solid=True,
        )
        self._activate_body(draft_body, "Face1")
        draft_expected = self._snapshot(draft_body)
        self.assertTrue(Gui.isCommandActive("PartDesign_Draft"))
        Gui.runCommand("PartDesign_Draft", 0)
        self.assertTrue(Gui.Control.activeDialog(), "PartDesign_Draft")
        self.assertEqual(
            self.document.ActiveObject.TypeId,
            "PartDesign::DesignDraft",
        )
        self._cancel_task("PartDesign_Draft")
        self._assert_snapshot(
            draft_body,
            draft_expected,
            "PartDesign_Draft",
        )

        for command_name, feature_type in TRANSFORM_COMMANDS:
            body, _base = self._new_body(
                f"Cancel{feature_type.rsplit('::', 1)[-1]}Body",
                solid=True,
            )
            self._activate_body(body)
            expected = self._snapshot(body)
            Gui.runCommand(command_name, 0)
            self.assertTrue(Gui.Control.activeDialog(), command_name)
            self.assertEqual(self.document.ActiveObject.TypeId, feature_type, command_name)
            self._cancel_task(command_name)
            self._assert_snapshot(body, expected, command_name)

    def test_chamfer_and_draft_cancel_stress_is_exact(self):
        """Repeated dress-up Cancel is exact across Undo and caller ownership."""

        body, source = self._new_body(
            "DressUpCancelStressBody",
            solid=True,
        )
        body.Visibility = True
        self.document.recompute()
        self._process_events()

        commands = (
            ("PartDesign_Chamfer", "PartDesign::DesignChamfer", "Edge1"),
            ("PartDesign_Draft", "PartDesign::DesignDraft", "Face1"),
        )

        def launch_and_cancel(command_name, feature_type, subelement, context):
            self._activate_body(body, subelement)
            self.assertTrue(Gui.isCommandActive(command_name), context)
            expected = self._snapshot(body)

            Gui.runCommand(command_name, 0)
            self._process_events()
            self.assertTrue(Gui.Control.activeDialog(), context)
            provisional = self.document.ActiveObject
            self.assertIsNotNone(provisional, context)
            self.assertEqual(provisional.TypeId, feature_type, context)
            self.assertIsNone(
                provisional.getParentGeoFeatureGroup(),
                context,
            )
            self.assertNotIn(provisional, body.Group, context)
            self.assertEqual(
                list(provisional.InputStates),
                [source],
                context,
            )
            self.assertEqual(
                list(provisional.TargetElementOffsets),
                [0, 1],
                context,
            )
            self.assertEqual(
                tuple(provisional.TargetElements),
                (subelement,),
                context,
            )

            button = self._task_button(QtGui.QDialogButtonBox.Cancel)
            self.assertIsNotNone(button, context)
            button.click()
            # Drive both the immediate transaction abort and queued TreeWidget
            # object/property notifications before inspecting restored state.
            self._process_events(40)
            self._process_events()

            self.assertFalse(Gui.Control.activeDialog(), context)
            self.assertEqual(self._snapshot(body), expected, context)

        for cycle in range(50):
            self.document.UndoMode = cycle % 2 == 0
            for command_name, feature_type, subelement in commands:
                context = (
                    f"{command_name} cycle {cycle + 1}/50 "
                    f"UndoMode={self.document.UndoMode}"
                )
                with self.subTest(
                    command=command_name,
                    cycle=cycle,
                    undo_mode=self.document.UndoMode,
                ):
                    launch_and_cancel(
                        command_name,
                        feature_type,
                        subelement,
                        context,
                    )

        self.document.UndoMode = True
        caller_probe = self.document.addObject(
            "Part::Feature",
            "DressUpCallerTransactionProbe",
        )
        caller_probe.addProperty(
            "App::PropertyString",
            "ContractValue",
        )
        caller_probe.ContractValue = "before caller transaction"
        caller_probe.Shape = Part.makeBox(2, 3, 4)
        self.document.recompute()
        self._process_events()

        original_undo_count = self.document.UndoCount
        self.document.openTransaction("Dress-up stress caller transaction")
        caller_transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(caller_transaction_id, 0)
        caller_probe.ContractValue = "inside caller transaction"
        caller_undo_count = self.document.UndoCount

        try:
            for cycle in range(5):
                for command_name, feature_type, subelement in commands:
                    context = (
                        f"{command_name} caller-owned refusal "
                        f"{cycle + 1}/5"
                    )
                    with self.subTest(
                        command=command_name,
                        caller_cycle=cycle,
                    ):
                        self._activate_body(body, subelement)
                        expected = self._snapshot(body)
                        self.assertFalse(
                            Gui.isCommandActive(command_name),
                            context,
                        )
                        actions = Gui.Command.get(command_name).getAction()
                        self.assertTrue(actions, context)
                        self.assertFalse(actions[0].isEnabled(), context)

                        Gui.runCommand(command_name, 0)
                        self._process_events(40)
                        self._process_events()

                        self.assertFalse(
                            Gui.Control.activeDialog(),
                            context,
                        )
                        self.assertEqual(
                            self._snapshot(body),
                            expected,
                            context,
                        )
                        self.assertTrue(
                            self.document.HasPendingTransaction,
                            context,
                        )
                        self.assertEqual(
                            self.document.getBookedTransactionID(),
                            caller_transaction_id,
                            context,
                        )
                        self.assertEqual(
                            self.document.UndoCount,
                            caller_undo_count,
                            context,
                        )
                        self.assertEqual(
                            caller_probe.ContractValue,
                            "inside caller transaction",
                            context,
                        )
        finally:
            if (
                self.document.getBookedTransactionID()
                == caller_transaction_id
            ):
                self.document.abortTransaction()
            self._process_events(40)
            self._process_events()

        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(self.document.UndoCount, original_undo_count)
        self.assertEqual(
            caller_probe.ContractValue,
            "before caller transaction",
        )

    def test_modal_feature_families_refuse_unrelated_caller_transaction(self):
        """Top-level modal tools never replace a caller-owned transaction."""

        profile_body, _feature = self._new_body("CallerProfileBody")
        profile = self._profile_sketch(
            profile_body,
            "CallerProfile",
        )
        finish_body, _feature = self._new_body(
            "CallerFinishBody",
            solid=True,
        )
        transform_body, _feature = self._new_body(
            "CallerTransformBody",
            solid=True,
        )
        boolean_body, _feature = self._new_body(
            "CallerBooleanBody",
            solid=True,
        )
        boolean_operand, _feature = self._new_body(
            "CallerBooleanOperand",
            solid=True,
        )
        caller_probe = self.document.addObject(
            "Part::Feature",
            "ModalCallerTransactionProbe",
        )
        caller_probe.addProperty(
            "App::PropertyString",
            "ContractValue",
        )
        caller_probe.ContractValue = "outside caller transaction"
        caller_probe.Shape = Part.makeBox(2, 3, 4)
        self.document.recompute()

        cases = (
            (
                "profile",
                "PartDesign_Pad",
                profile_body,
                ((profile, None),),
            ),
            (
                "finish",
                "PartDesign_Chamfer",
                finish_body,
                ((finish_body, "Edge1"),),
            ),
            (
                "transform",
                "PartDesign_Mirrored",
                transform_body,
                ((transform_body, None),),
            ),
            (
                "boolean",
                "PartDesign_Boolean",
                boolean_body,
                (
                    (boolean_body, None),
                    (boolean_operand, None),
                ),
            ),
        )

        for family, command_name, body, selections in cases:
            with self.subTest(family=family, command=command_name):
                Gui.activeView().setActiveObject("pdbody", body)
                Gui.Selection.clearSelection()
                for selected, subelement in selections:
                    if subelement is None:
                        Gui.Selection.addSelection(selected)
                    else:
                        Gui.Selection.addSelection(selected, subelement)
                self._process_events()
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    f"{family} setup is not a valid native command input",
                )

                original_undo_count = self.document.UndoCount
                self.document.openTransaction(
                    f"{family} unrelated caller transaction"
                )
                caller_transaction_id = (
                    self.document.getBookedTransactionID()
                )
                self.assertNotEqual(caller_transaction_id, 0)
                caller_probe.ContractValue = f"inside {family} caller"
                self._process_events()
                expected = self._snapshot(body)

                try:
                    self.assertFalse(
                        Gui.isCommandActive(command_name),
                        family,
                    )

                    Gui.runCommand(command_name, 0)
                    self._process_events(40)
                    self._process_events()

                    self.assertFalse(Gui.Control.activeDialog(), family)
                    self.assertEqual(
                        self._snapshot(body),
                        expected,
                        family,
                    )
                    self.assertEqual(
                        self.document.getBookedTransactionID(),
                        caller_transaction_id,
                        family,
                    )
                    self.assertEqual(
                        caller_probe.ContractValue,
                        f"inside {family} caller",
                        family,
                    )
                finally:
                    if (
                        self.document.getBookedTransactionID()
                        == caller_transaction_id
                    ):
                        self.document.abortTransaction()
                    self._process_events()

                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(
                    self.document.getBookedTransactionID(),
                    0,
                )
                self.assertEqual(
                    self.document.UndoCount,
                    original_undo_count,
                )
                self.assertEqual(
                    caller_probe.ContractValue,
                    "outside caller transaction",
                )

    def test_composite_modal_groups_refuse_unrelated_caller_transaction(self):
        """A group invocation cannot disguise an externally owned transaction."""

        body, _feature = self._new_body(
            "CompositeCallerBody",
            solid=True,
            solid_size=20.0,
        )
        caller_probe = self.document.addObject(
            "Part::Feature",
            "CompositeCallerTransactionProbe",
        )
        caller_probe.addProperty(
            "App::PropertyString",
            "ContractValue",
        )
        caller_probe.ContractValue = "outside caller transaction"
        caller_probe.Shape = Part.makeBox(2, 3, 4)
        self.document.recompute()

        cases = (
            ("additive primitive", "PartDesign_CompPrimitiveAdditive", 0),
            ("subtractive primitive", "PartDesign_CompPrimitiveSubtractive", 0),
            ("datum group", "PartDesign_CompDatums", 0),
        )
        for family, command_name, index in cases:
            with self.subTest(family=family, command=command_name):
                self._activate_body(body)
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    f"{family} setup is not active",
                )

                original_undo_count = self.document.UndoCount
                self.document.openTransaction(
                    f"{family} unrelated caller transaction"
                )
                caller_transaction_id = (
                    self.document.getBookedTransactionID()
                )
                self.assertNotEqual(caller_transaction_id, 0)
                caller_probe.ContractValue = f"inside {family} caller"
                self._process_events()
                expected = self._snapshot(body)

                try:
                    self.assertFalse(
                        Gui.isCommandActive(command_name),
                        family,
                    )

                    Gui.runCommand(command_name, index)
                    self._process_events(40)
                    self._process_events()

                    self.assertFalse(Gui.Control.activeDialog(), family)
                    self.assertEqual(
                        self._snapshot(body),
                        expected,
                        family,
                    )
                    self.assertEqual(
                        self.document.getBookedTransactionID(),
                        caller_transaction_id,
                        family,
                    )
                    self.assertEqual(
                        caller_probe.ContractValue,
                        f"inside {family} caller",
                        family,
                    )
                finally:
                    if (
                        self.document.getBookedTransactionID()
                        == caller_transaction_id
                    ):
                        self.document.abortTransaction()
                    self._process_events()

                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(
                    self.document.getBookedTransactionID(),
                    0,
                )
                self.assertEqual(
                    self.document.UndoCount,
                    original_undo_count,
                )
                self.assertEqual(
                    caller_probe.ContractValue,
                    "outside caller transaction",
                )

    def test_structure_commands_refuse_unrelated_caller_transaction(self):
        """Synchronous Structure tools cannot replace a caller transaction."""

        binder_body, _feature = self._new_body(
            "CallerBinderDestination",
            solid=True,
        )
        binder_source = self.document.addObject(
            "Part::Feature",
            "CallerBinderSource",
        )
        binder_source.Shape = Part.makeBox(
            6,
            7,
            8,
            App.Vector(20, 0, 0),
        )
        clone_body, _feature = self._new_body(
            "CallerCloneSource",
            solid=True,
        )
        caller_probe = self.document.addObject(
            "Part::Feature",
            "StructureCallerTransactionProbe",
        )
        caller_probe.addProperty(
            "App::PropertyString",
            "ContractValue",
        )
        caller_probe.ContractValue = "outside caller transaction"
        caller_probe.Shape = Part.makeBox(2, 3, 4)
        self.document.recompute()

        cases = (
            (
                "sub-shape binder",
                "PartDesign_SubShapeBinder",
                binder_body,
                ((binder_source, "Face1"),),
            ),
            (
                "clone",
                "PartDesign_Clone",
                clone_body,
                ((clone_body, None),),
            ),
        )

        for family, command_name, active_body, selections in cases:
            with self.subTest(family=family, command=command_name):
                Gui.activeView().setActiveObject("pdbody", active_body)
                Gui.Selection.clearSelection()
                for selected, subelement in selections:
                    if subelement is None:
                        Gui.Selection.addSelection(selected)
                    else:
                        Gui.Selection.addSelection(selected, subelement)
                self._process_events()
                self.assertTrue(
                    Gui.isCommandActive(command_name),
                    f"{family} setup is not a valid native command input",
                )

                original_undo_count = self.document.UndoCount
                self.document.openTransaction(
                    f"{family} unrelated caller transaction"
                )
                caller_transaction_id = (
                    self.document.getBookedTransactionID()
                )
                self.assertNotEqual(caller_transaction_id, 0)
                caller_probe.ContractValue = f"inside {family} caller"
                self._process_events()
                expected = self._snapshot(active_body)

                try:
                    self.assertFalse(
                        Gui.isCommandActive(command_name),
                        family,
                    )

                    Gui.runCommand(command_name, 0)
                    self._process_events(40)
                    self._process_events()

                    self.assertFalse(Gui.Control.activeDialog(), family)
                    self.assertEqual(
                        self._snapshot(active_body),
                        expected,
                        family,
                    )
                    self.assertEqual(
                        self.document.getBookedTransactionID(),
                        caller_transaction_id,
                        family,
                    )
                    self.assertEqual(
                        caller_probe.ContractValue,
                        f"inside {family} caller",
                        family,
                    )
                finally:
                    if (
                        self.document.getBookedTransactionID()
                        == caller_transaction_id
                    ):
                        self.document.abortTransaction()
                    self._process_events()

                self.assertFalse(self.document.HasPendingTransaction)
                self.assertEqual(
                    self.document.getBookedTransactionID(),
                    0,
                )
                self.assertEqual(
                    self.document.UndoCount,
                    original_undo_count,
                )
                self.assertEqual(
                    caller_probe.ContractValue,
                    "outside caller transaction",
                )

    def test_native_feature_cancel_is_exact_without_an_undo_journal(self):
        """Cancel is a GUI contract, not an optional side effect of Undo."""

        self.document.UndoMode = False

        primitive_body, _feature = self._new_body(
            "NoUndoPrimitiveBody",
        )
        self._activate_body(primitive_body)
        expected = self._snapshot(primitive_body)
        Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self._cancel_task("no-undo additive primitive")
        self._assert_snapshot(
            primitive_body,
            expected,
            "no-undo additive primitive",
        )

        chamfer_body, _feature = self._new_body(
            "NoUndoChamferBody",
            solid=True,
        )
        self._activate_body(chamfer_body, "Edge1")
        expected = self._snapshot(chamfer_body)
        Gui.runCommand("PartDesign_Chamfer", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self._cancel_task("no-undo chamfer")
        self._assert_snapshot(chamfer_body, expected, "no-undo chamfer")

        draft_body, _feature = self._new_body(
            "NoUndoDraftBody",
            solid=True,
        )
        self._activate_body(draft_body, "Face1")
        expected = self._snapshot(draft_body)
        Gui.runCommand("PartDesign_Draft", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self._cancel_task("no-undo draft")
        self._assert_snapshot(draft_body, expected, "no-undo draft")

        profile_body, _feature = self._new_body("NoUndoPadBody")
        profile = self._profile_sketch(profile_body, "NoUndoPadProfile")
        Gui.activeView().setActiveObject("pdbody", profile_body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(profile)
        self._process_events()
        expected = self._snapshot(profile_body)
        Gui.runCommand("PartDesign_Pad", 0)
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self._cancel_task("no-undo pad")
        self._assert_snapshot(profile_body, expected, "no-undo pad")
