# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI release gate for bundled standard fasteners."""

from __future__ import annotations

import hashlib
from pathlib import Path
import tempfile
import unittest
from unittest import mock


_STANDARD_COMPONENT_COMMANDS = (
    "VibeCAD_InsertStandardFastener",
    "VibeCAD_EditStandardFastener",
    "VibeCAD_CreateMatchingFastenerHole",
    "VibeCAD_AttachStandardFastener",
)

_FASTENER_COMMAND_TIMELINE_BEHAVIOR = {
    "VibeCAD_InsertStandardFastener": frozenset({"operation", "standalone"}),
    "VibeCAD_EditStandardFastener": frozenset({"in-place"}),
    "VibeCAD_CreateMatchingFastenerHole": frozenset(
        {"operation", "body-history-step"}
    ),
    "VibeCAD_AttachStandardFastener": frozenset({"in-place"}),
}


class TestVibeCADFastenersGui(unittest.TestCase):
    def test_a_timeline_editor_capability_survives_early_registration(
        self,
    ) -> None:
        import FreeCADGui as Gui

        import VibeCADFastenersGui

        VibeCADFastenersGui.ensure_commands_registered()
        actions = Gui.Command.get(
            "VibeCAD_EditStandardFastener"
        ).ensureAction()
        self.assertTrue(actions)
        self.assertTrue(
            all(
                action.property("VibeCADTimelineOperationEditor") is True
                for action in actions
            )
        )

    def test_standard_component_timeline_matrix_is_exhaustive_and_disjoint(
        self,
    ) -> None:
        self.assertEqual(
            set(_FASTENER_COMMAND_TIMELINE_BEHAVIOR),
            set(_STANDARD_COMPONENT_COMMANDS),
        )

        primary_behaviors = {
            "standalone",
            "source-preserving",
            "replacement",
            "body-history-step",
            "in-place",
            "read-only",
        }
        operation_behaviors = {
            "standalone",
            "source-preserving",
            "replacement",
            "body-history-step",
        }
        for command, behaviors in _FASTENER_COMMAND_TIMELINE_BEHAVIOR.items():
            with self.subTest(command=command):
                primary = behaviors & primary_behaviors
                self.assertEqual(len(primary), 1)
                self.assertFalse(behaviors - primary_behaviors - {"operation"})
                self.assertEqual(
                    "operation" in behaviors,
                    bool(primary & operation_behaviors),
                )

    @staticmethod
    def _process_events() -> None:
        import FreeCADGui as Gui
        from PySide import QtWidgets

        Gui.updateGui()
        QtWidgets.QApplication.instance().processEvents()

    def _ribbon_actions(self) -> dict[str, object]:
        """Return the real command actions wired into the Model ribbon."""

        import FreeCADGui as Gui
        from PySide import QtWidgets

        import VibeCADFastenersGui

        source_module = (
            Path(__file__).resolve().parent.parent
            / "VibeCADFastenersGui.py"
        )
        self.assertEqual(
            Path(VibeCADFastenersGui.__file__).resolve(),
            source_module,
            "Run this source gate with the VibeCAD source module registered "
            "at startup (FreeCAD -M <source>/Mod/VibeCAD).",
        )
        VibeCADFastenersGui.ensure_commands_registered()
        Gui.activateWorkbench("PartDesignWorkbench")
        main_window = Gui.getMainWindow()
        main_window.show()
        self._process_events()

        group = main_window.findChild(
            QtWidgets.QFrame,
            "VibeCADRibbonGroup_Fasteners",
        )
        self.assertIsNotNone(group)
        group_menu = group.findChild(
            QtWidgets.QToolButton,
            "VibeCADRibbonGroupMenu",
        )
        self.assertIsNotNone(group_menu)
        self.assertIsNotNone(group_menu.menu())
        menu_actions = {
            str(action.property("VibeCADCommandId")): action
            for action in group_menu.menu().actions()
            if action.property("VibeCADCommandId")
        }
        self.assertEqual(
            set(menu_actions),
            set(_STANDARD_COMPONENT_COMMANDS),
        )

        primary_buttons = [
            button
            for button in group.findChildren(QtWidgets.QToolButton)
            if button.property("ribbonCommand")
            and button.defaultAction() is not None
        ]
        primary_actions = {
            str(button.defaultAction().property("VibeCADCommandId")): (
                button,
                button.defaultAction(),
            )
            for button in primary_buttons
        }
        self.assertEqual(
            set(primary_actions),
            set(_STANDARD_COMPONENT_COMMANDS),
        )
        for command_name in _STANDARD_COMPONENT_COMMANDS:
            button, action = primary_actions[command_name]
            self.assertEqual(
                action.property("VibeCADCommandId"),
                command_name,
            )
            self.assertEqual(
                menu_actions[command_name].property("VibeCADCommandId"),
                command_name,
            )
            self.assertFalse(action.icon().isNull())
            self.assertFalse(menu_actions[command_name].icon().isNull())
            self.assertFalse(button.icon().isNull())
            self.assertTrue(action.toolTip().strip())
        return menu_actions

    def _history_item(self, operation: object):
        import FreeCADGui as Gui
        from PySide import QtCore, QtWidgets

        self._process_events()
        items = Gui.getMainWindow().findChild(
            QtWidgets.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(items)
        matches = [
            items.item(row)
            for row in range(items.count())
            if str(items.item(row).data(QtCore.Qt.UserRole) or "")
            == operation.Name
        ]
        self.assertEqual(len(matches), 1, operation.Name)
        return items, matches[0]

    def _assert_exact_timeline_block(
        self,
        document: object,
        operation: object,
        resources: tuple[object, ...] = (),
        *,
        explicit_operation: bool = True,
    ) -> None:
        """Assert one canonical resource-first semantic-history block."""

        controller = document.getObject("VibeCADTimeline")
        self.assertIsNotNone(controller)
        operations = list(controller.Operations)
        expected_block = [*resources, operation]
        self.assertEqual(operations.count(operation), 1)
        for resource in resources:
            self.assertEqual(operations.count(resource), 1)
        operation_index = operations.index(operation)
        self.assertEqual(
            operations[
                operation_index - len(resources) : operation_index + 1
            ],
            expected_block,
        )
        if explicit_operation:
            self.assertEqual(operation.VibeCADTimelineRole, "operation")
            self.assertIsNone(
                getattr(operation, "VibeCADTimelineOwner", None)
            )
        else:
            self.assertNotIn("VibeCADTimelineRole", operation.PropertiesList)
            self.assertNotIn("VibeCADTimelineOwner", operation.PropertiesList)
        for resource in resources:
            self.assertEqual(resource.VibeCADTimelineRole, "resource")
            self.assertIs(resource.VibeCADTimelineOwner, operation)

    def _trigger_catalog_dialog_action(
        self,
        action: object,
        configure,
    ) -> None:
        """Drive a real catalog dialog opened through one ribbon QAction."""

        from PySide import QtCore, QtWidgets

        import VibeCADFastenersGui

        dialog_class = VibeCADFastenersGui._FastenerDialog
        original_init = dialog_class.__init__
        captured: dict[str, object] = {}
        errors: list[BaseException] = []

        def capture_init(instance, *args, **kwargs):
            original_init(instance, *args, **kwargs)
            captured["driver"] = instance

        def accept_dialog() -> None:
            driver = captured.get("driver")
            if driver is None:
                errors.append(
                    AssertionError("The fastener action did not create its dialog.")
                )
                return
            try:
                configure(driver)
                buttons = driver.dialog.findChild(
                    QtWidgets.QDialogButtonBox
                )
                self.assertIsNotNone(buttons)
                accept = buttons.button(QtWidgets.QDialogButtonBox.Ok)
                self.assertIsNotNone(accept)
                accept.click()
                if (
                    driver.dialog.result()
                    != QtWidgets.QDialog.Accepted
                ):
                    errors.append(
                        AssertionError(
                            "The configured fastener dialog rejected valid "
                            f"catalog values: {driver.status_label.text()}"
                        )
                    )
                    driver.dialog.reject()
            except BaseException as exc:
                errors.append(exc)
                driver.dialog.reject()

        self.assertTrue(action.isEnabled())
        with mock.patch.object(dialog_class, "__init__", capture_init):
            QtCore.QTimer.singleShot(0, accept_dialog)
            action.trigger()
        self._process_events()
        self.assertIn("driver", captured)
        if errors:
            raise errors[0]
        driver = captured["driver"]
        self.assertEqual(
            driver.dialog.result(),
            QtWidgets.QDialog.Accepted,
        )
        driver.dialog.deleteLater()

    def _cancel_catalog_dialog_action(
        self,
        action: object,
        configure,
    ) -> None:
        """Change dialog fields, then exercise the real Cancel button."""

        from PySide import QtCore, QtWidgets

        import VibeCADFastenersGui

        dialog_class = VibeCADFastenersGui._FastenerDialog
        original_init = dialog_class.__init__
        captured: dict[str, object] = {}
        errors: list[BaseException] = []

        def capture_init(instance, *args, **kwargs):
            original_init(instance, *args, **kwargs)
            captured["driver"] = instance

        def reject_dialog() -> None:
            driver = captured.get("driver")
            if driver is None:
                errors.append(
                    AssertionError("The fastener action did not create its dialog.")
                )
                return
            try:
                configure(driver)
                buttons = driver.dialog.findChild(QtWidgets.QDialogButtonBox)
                self.assertIsNotNone(buttons)
                cancel = buttons.button(QtWidgets.QDialogButtonBox.Cancel)
                self.assertIsNotNone(cancel)
                self.assertTrue(cancel.isEnabled())
                cancel.click()
            except BaseException as exc:
                errors.append(exc)
                driver.dialog.reject()

        self.assertTrue(action.isEnabled())
        with mock.patch.object(dialog_class, "__init__", capture_init):
            QtCore.QTimer.singleShot(0, reject_dialog)
            action.trigger()
        self._process_events()
        self.assertIn("driver", captured)
        if errors:
            raise errors[0]
        driver = captured["driver"]
        self.assertEqual(
            driver.dialog.result(),
            QtWidgets.QDialog.Rejected,
        )
        driver.dialog.deleteLater()

    def _drive_item_dialog_action(
        self,
        action: object,
        decisions: tuple[tuple[str, str, bool], ...],
    ) -> None:
        """Drive the real Purpose/Fit item dialogs opened by one QAction."""

        from PySide import QtCore, QtWidgets

        pending = list(decisions)
        completed: list[tuple[str, str, bool]] = []
        errors: list[BaseException] = []

        def respond() -> None:
            modal = QtWidgets.QApplication.activeModalWidget()
            if not isinstance(modal, QtWidgets.QInputDialog):
                errors.append(
                    AssertionError(
                        "The matching-hole action did not open a QInputDialog."
                    )
                )
                if modal is not None:
                    modal.reject()
                return
            if not pending:
                errors.append(
                    AssertionError("The matching-hole action opened an extra dialog.")
                )
                modal.reject()
                return
            expected_label, choice, accepted = pending.pop(0)
            try:
                self.assertIn(
                    expected_label.casefold(),
                    str(modal.labelText()).casefold(),
                )
                combo = modal.findChild(QtWidgets.QComboBox)
                self.assertIsNotNone(combo)
                index = combo.findText(choice, QtCore.Qt.MatchExactly)
                self.assertGreaterEqual(index, 0)
                combo.setCurrentIndex(index)
                buttons = modal.findChild(QtWidgets.QDialogButtonBox)
                self.assertIsNotNone(buttons)
                standard_button = (
                    QtWidgets.QDialogButtonBox.Ok
                    if accepted
                    else QtWidgets.QDialogButtonBox.Cancel
                )
                button = buttons.button(standard_button)
                self.assertIsNotNone(button)
                completed.append((expected_label, choice, accepted))
                if pending:
                    QtCore.QTimer.singleShot(0, respond)
                button.click()
            except BaseException as exc:
                errors.append(exc)
                modal.reject()

        self.assertTrue(action.isEnabled())
        QtCore.QTimer.singleShot(0, respond)
        action.trigger()
        self._process_events()
        if errors:
            raise errors[0]
        self.assertEqual(completed, list(decisions))
        self.assertFalse(pending)

    def _set_catalog_dialog_values(
        self,
        driver: object,
        *,
        standard: str,
        nominal_thread: str,
        length_mm: float,
        label: str,
    ) -> None:
        driver.filter_edit.clear()
        self.assertTrue(
            driver._select_data(driver.standard_combo, standard)
        )
        self.assertTrue(
            driver._select_data(driver.size_combo, nominal_thread)
        )
        selected_length = False
        for index in range(driver.length_combo.count()):
            raw = driver.length_combo.itemData(index)
            if raw is not None and abs(float(raw) - length_mm) <= 1.0e-7:
                driver.length_combo.setCurrentIndex(index)
                selected_length = True
                break
        if not selected_length and driver.length_combo.isEditable():
            driver.length_combo.setEditText(f"{length_mm:g}")
            selected_length = True
        self.assertTrue(selected_length)
        driver.model_thread.setChecked(False)
        driver.left_handed.setChecked(False)
        driver.label_edit.setText(label)

    def _create_standard_fastener(
        self,
        document: object,
        *,
        body_name: str = "StandardFastenerBody",
        standard: str = "ISO4762",
        nominal_thread: str = "M3",
        length_mm: float = 10,
    ):
        import VibeCADFastenersGui
        from VibeCADFasteners import create_fastener_feature

        body = document.addObject("PartDesign::Body", body_name)
        feature, identity = create_fastener_feature(
            body,
            standard=standard,
            nominal_thread=nominal_thread,
            length_mm=length_mm,
            model_thread=False,
            object_name="Fastener",
        )
        VibeCADFastenersGui._mark_timeline_operation(feature)
        body.Tip = feature
        document.recompute()
        self.assertIs(feature.getParentGeoFeatureGroup(), body)
        self.assertIs(body.Tip, feature)
        self.assertFalse(feature.Shape.isNull())
        self.assertTrue(feature.Shape.isValid())
        self.assertEqual(len(feature.Shape.Solids), 1)
        return body, feature, identity

    def _create_matching_hole_fixture(
        self,
        document: object,
        *,
        standard: str = "ISO4762",
        linked_fastener: bool = False,
    ) -> dict[str, object]:
        """Create one native plate/sketch/fastener selection for hole commands."""

        import FreeCAD as App
        import FreeCADGui as Gui
        import Part

        fastener_body, fastener, identity = self._create_standard_fastener(
            document,
            body_name=f"{standard}FastenerBody",
            standard=standard,
            nominal_thread="M3",
            length_mm=10,
        )
        selected_fastener = fastener_body
        occurrence = None
        if linked_fastener:
            occurrence = document.addObject("App::Link", "FastenerOccurrence")
            occurrence.LinkedObject = fastener
            occurrence.Label = "Linked standard fastener"
            selected_fastener = occurrence

        host_body = document.addObject("PartDesign::Body", "HostBody")
        base = document.addObject("PartDesign::AdditiveBox", "HostPlate")
        base.Length = 24
        base.Width = 24
        base.Height = 8
        host_body.addObject(base)
        document.recompute()
        sketch = document.addObject(
            "Sketcher::SketchObject",
            "HoleLocations",
        )
        sketch.AttachmentSupport = (base, ["Face6"])
        sketch.MapMode = "FlatFace"
        sketch.addGeometry(
            Part.Circle(
                App.Vector(12, 12, 0),
                App.Vector(0, 0, 1),
                1.5,
            ),
            False,
        )
        host_body.addObject(sketch)
        document.recompute()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(selected_fastener)
        Gui.Selection.addSelection(sketch)
        self._process_events()
        return {
            "fastener_body": fastener_body,
            "fastener": fastener,
            "identity": identity,
            "occurrence": occurrence,
            "host_body": host_body,
            "base": base,
            "sketch": sketch,
        }

    def _command_state_snapshot(self, document: object) -> dict[str, object]:
        """Capture all document and GUI state a canceled dialog must preserve."""

        import FreeCADGui as Gui

        objects = tuple(document.Objects)
        return {
            "objects": objects,
            "object_identity": tuple(
                (
                    obj,
                    str(obj.Name),
                    str(obj.Label),
                    str(obj.TypeId),
                )
                for obj in objects
                if getattr(obj, "ViewObject", None) is not None
            ),
            "body_history": tuple(
                (
                    obj,
                    tuple(obj.Group),
                    obj.Tip,
                )
                for obj in objects
                if str(obj.TypeId) == "PartDesign::Body"
            ),
            "shape_state": tuple(
                (
                    obj,
                    str(obj.Name),
                    hashlib.sha256(
                        obj.Shape.exportBrepToString().encode("utf-8")
                    ).hexdigest(),
                    round(float(obj.Shape.Volume), 12),
                    round(float(obj.Shape.Area), 12),
                    round(float(obj.Shape.Length), 12),
                    len(obj.Shape.Vertexes),
                    len(obj.Shape.Edges),
                    len(obj.Shape.Faces),
                    len(obj.Shape.Solids),
                )
                for obj in objects
                if hasattr(obj, "Shape") and not obj.Shape.isNull()
            ),
            "selection": tuple(
                (
                    item.Object,
                    tuple(item.SubElementNames),
                )
                for item in Gui.Selection.getSelectionEx()
            ),
            "active_body": Gui.activeView().getActiveObject("pdbody"),
            "active_object": document.ActiveObject,
            "visibility": tuple(
                (
                    obj,
                    (
                        bool(obj.ViewObject.Visibility)
                        if obj.ViewObject is not None
                        else None
                    ),
                    (
                        bool(getattr(obj.ViewObject, "ShowInTree", True))
                        if obj.ViewObject is not None
                        else None
                    ),
                )
                for obj in objects
            ),
            "pending_transaction": bool(document.HasPendingTransaction),
            "booked_transaction": int(document.getBookedTransactionID()),
            "undo_names": tuple(document.UndoNames),
            "undo_count": int(document.UndoCount),
            "redo_count": int(document.RedoCount),
            "workbench": str(Gui.activeWorkbench().name()),
        }

    def _assert_command_state_unchanged(
        self,
        document: object,
        before: dict[str, object],
    ) -> None:
        """Report the exact state dimension changed by a canceled command."""

        after = self._command_state_snapshot(document)
        self.assertEqual(set(after), set(before))
        for key in before:
            self.assertEqual(after[key], before[key], f"Changed state: {key}")

    def test_commands_workbenches_icons_and_native_thread_boolean(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide import QtWidgets

        import VibeCADFastenersGui
        from VibeCADFasteners import catalog_index, resolve_fastener

        self.assertTrue(App.GuiUp)
        VibeCADFastenersGui.ensure_commands_registered()
        expected = {
            "VibeCAD_InsertStandardFastener",
            "VibeCAD_EditStandardFastener",
            "VibeCAD_CreateMatchingFastenerHole",
            "VibeCAD_AttachStandardFastener",
        }
        self.assertTrue(expected.issubset(set(Gui.listCommands())))
        command_types = (
            VibeCADFastenersGui._InsertStandardFastenerCommand,
            VibeCADFastenersGui._EditStandardFastenerCommand,
            VibeCADFastenersGui._CreateMatchingHoleCommand,
            VibeCADFastenersGui._AttachStandardFastenerCommand,
        )
        for command_type in command_types:
            self.assertEqual(
                command_type().GetResources().get("CmdType"),
                "AlterDoc",
                command_type.__name__,
            )
        edit_actions = Gui.Command.get(
            "VibeCAD_EditStandardFastener"
        ).getAction()
        self.assertTrue(edit_actions)
        self.assertTrue(
            all(
                action.property("VibeCADTimelineOperationEditor") is True
                for action in edit_actions
            )
        )

        toolbar_actions = {}
        for workbench in (
            "FastenersWorkbench",
            "PartDesignWorkbench",
            "AssemblyWorkbench",
        ):
            Gui.activateWorkbench(workbench)
            self.assertEqual(Gui.activeWorkbench().name(), workbench)
            if workbench not in {"PartDesignWorkbench", "AssemblyWorkbench"}:
                continue
            matching = [
                toolbar
                for toolbar in Gui.getMainWindow().findChildren(
                    QtWidgets.QToolBar
                )
                if toolbar.windowTitle() == "Standard Components"
                and toolbar.isVisible()
            ]
            self.assertTrue(matching)
            actions = matching[-1].actions()
            self.assertGreaterEqual(
                len(actions),
                4 if workbench == "PartDesignWorkbench" else 2,
            )
            self.assertTrue(
                all(not action.icon().isNull() for action in actions)
            )
            toolbar_actions[workbench] = len(actions)

        identity = resolve_fastener(
            standard="ISO4762",
            nominal_thread="M6",
            length_mm=20,
            model_thread=True,
        )
        dialog = VibeCADFastenersGui._FastenerDialog(
            title="Fastener GUI integration",
            initial=identity,
            initial_label="Motor mount bolt",
        )
        try:
            self.assertIsInstance(dialog.model_thread, QtWidgets.QCheckBox)
            self.assertTrue(dialog.model_thread.isChecked())
            values = dialog.values()
            self.assertEqual(
                values["identity"]["canonical_key"],
                identity["canonical_key"],
            )
            self.assertIs(values["model_thread"], True)
            dialog.model_thread.setChecked(False)
            self.assertIs(dialog.values()["model_thread"], False)
            self.assertEqual(
                len(dialog._rows),
                len(catalog_index()["standards"]),
            )

            dialog.filter_edit.setText("m3")
            self.assertGreater(dialog.standard_combo.count(), 0)
            self.assertEqual(dialog.size_combo.currentData(), "M3")

            dialog.filter_edit.setText("m3 socket")
            self.assertGreater(dialog.standard_combo.count(), 0)
            self.assertEqual(dialog.size_combo.currentData(), "M3")
            matching_standards = {
                str(dialog.standard_combo.itemData(index))
                for index in range(dialog.standard_combo.count())
            }
            matching_rows = [
                row
                for row in dialog._rows
                if str(row["standard"]) in matching_standards
            ]
            self.assertEqual(
                len(matching_rows),
                dialog.standard_combo.count(),
            )
            self.assertTrue(
                all(
                    "socket"
                    in (
                        f"{row['standard']} {row['family']} "
                        f"{row['description']}"
                    ).casefold()
                    and any(
                        "m3" in str(size).casefold()
                        for size in row["nominal_threads"]
                    )
                    for row in matching_rows
                )
            )
            exact_m3_standard = next(
                row["standard"]
                for row in matching_rows
                if "M3" in row["nominal_threads"]
            )
            exact_m3_index = dialog.standard_combo.findData(
                exact_m3_standard
            )
            self.assertGreaterEqual(exact_m3_index, 0)
            dialog.standard_combo.setCurrentIndex(exact_m3_index)
            self.assertEqual(dialog.size_combo.currentData(), "M3")

            dialog.filter_edit.setText("476")
            self.assertGreaterEqual(
                dialog.standard_combo.findData("ISO4762"),
                0,
            )
            self.assertIn(
                str(dialog.standard_combo.count()),
                dialog.match_label.text(),
            )
        finally:
            dialog.dialog.close()

        print(
            "VIBECAD_FASTENERS_GUI_OK "
            f"commands={len(expected)} catalog_rows={len(dialog._rows)} "
            f"toolbar_actions={toolbar_actions}",
            flush=True,
        )

    def test_context_commands_enable_only_for_complete_valid_selection(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        import Part
        import Sketcher  # noqa: F401 - registers Sketcher object types

        import VibeCADFastenersGui
        from VibeCADFasteners import create_fastener_feature

        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("FastenerCommandSelection")
        try:
            fastener_body = document.addObject(
                "PartDesign::Body", "StandardFastenerBody"
            )
            fastener, _identity = create_fastener_feature(
                fastener_body,
                standard="ISO4762",
                nominal_thread="M3",
                length_mm=10,
                model_thread=False,
                object_name="Fastener",
            )
            fastener_body.Tip = fastener

            host_body = document.addObject("PartDesign::Body", "HostBody")
            sketch = host_body.newObject("Sketcher::SketchObject", "HoleLocations")
            sketch.addGeometry(
                Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 1.6),
                False,
            )
            cylinder = document.addObject("Part::Cylinder", "HoleHost")
            cylinder.Radius = 5
            cylinder.Height = 5
            document.recompute()

            matching_hole = VibeCADFastenersGui._CreateMatchingHoleCommand()
            attach = VibeCADFastenersGui._AttachStandardFastenerCommand()

            Gui.Selection.clearSelection()
            self.assertFalse(matching_hole.IsActive())
            self.assertFalse(attach.IsActive())

            Gui.Selection.addSelection(fastener_body)
            Gui.Selection.addSelection(sketch)
            self.assertTrue(matching_hole.IsActive())
            self.assertFalse(attach.IsActive())

            circular_edges = [
                index
                for index, edge in enumerate(cylinder.Shape.Edges, start=1)
                if isinstance(edge.Curve, Part.Circle)
            ]
            self.assertGreaterEqual(len(circular_edges), 2)
            circular_edge = circular_edges[0]
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(fastener_body)
            Gui.Selection.addSelection(cylinder, f"Edge{circular_edge}")
            self.assertFalse(matching_hole.IsActive())
            self.assertTrue(attach.IsActive())

            Gui.Selection.addSelection(
                cylinder,
                f"Edge{circular_edges[1]}",
            )
            self.assertFalse(attach.IsActive())
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_model_ribbon_exposes_one_live_action_per_standard_component(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui

        document = App.newDocument("FastenerRibbonActionGraph")
        try:
            actions = self._ribbon_actions()
            self.assertEqual(
                set(actions),
                set(_STANDARD_COMPONENT_COMMANDS),
            )
            self._process_events()
            self.assertTrue(
                actions["VibeCAD_InsertStandardFastener"].isEnabled()
            )
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_insert_standard_fastener_ribbon_action_creates_owned_body_tip(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide import QtCore, QtWidgets

        from VibeCADFasteners import COMPONENT_SCHEMA, PROP_SCHEMA

        document = App.newDocument("FastenerRibbonInsert")
        document.UndoMode = True
        try:
            actions = self._ribbon_actions()
            insert = actions["VibeCAD_InsertStandardFastener"]

            self._trigger_catalog_dialog_action(
                insert,
                lambda driver: self._set_catalog_dialog_values(
                    driver,
                    standard="ISO4762",
                    nominal_thread="M3",
                    length_mm=10,
                    label="M3 socket bolt",
                ),
            )

            selected = Gui.Selection.getSelection()
            self.assertEqual(len(selected), 1)
            body = selected[0]
            self.assertEqual(body.TypeId, "PartDesign::Body")
            self.assertEqual(body.Label, "M3 socket bolt")
            feature = body.Tip
            self.assertIsNotNone(feature)
            self.assertTrue(feature.Label.startswith("M3 socket bolt"))
            self.assertNotEqual(feature.Label, body.Label)
            self.assertEqual(feature.TypeId, "PartDesign::FeaturePython")
            self.assertIs(feature.getParentGeoFeatureGroup(), body)
            self.assertEqual(
                body.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                body.VibeCADTimelineEditCommand,
                "VibeCAD_EditStandardFastener",
            )
            self.assertIs(body.VibeCADTimelineEditor, feature)
            self.assertEqual(feature.VibeCADTimelineRole, "resource")
            self.assertIs(feature.VibeCADTimelineOwner, body)
            self.assertEqual(
                feature.getTypeIdOfProperty("VibeCADTimelineOwner"),
                "App::PropertyLinkHidden",
            )
            self.assertEqual(getattr(feature, PROP_SCHEMA), COMPONENT_SCHEMA)
            self.assertEqual(str(feature.Type), "ISO4762")
            self.assertEqual(str(feature.Diameter), "M3")
            self.assertFalse(feature.Shape.isNull())
            self.assertTrue(feature.Shape.isValid())
            self.assertEqual(len(feature.Shape.Solids), 1)
            self.assertIs(
                Gui.activeView().getActiveObject("pdbody"),
                body,
            )

            controller = document.getObject("VibeCADTimeline")
            self.assertIsNotNone(controller)
            operations = list(controller.Operations)
            self.assertEqual(operations[-2:], [feature, body])
            block_start = operations.index(feature)
            block_end = operations.index(body) + 1

            timeline = Gui.getMainWindow().findChild(
                QtWidgets.QListWidget,
                "VibeCADFeatureTimelineItems",
            )
            previous = Gui.getMainWindow().findChild(
                QtWidgets.QToolButton,
                "VibeCADFeatureTimelinePrevious",
            )
            next_button = Gui.getMainWindow().findChild(
                QtWidgets.QToolButton,
                "VibeCADFeatureTimelineNext",
            )
            end = Gui.getMainWindow().findChild(
                QtWidgets.QToolButton,
                "VibeCADFeatureTimelineEnd",
            )
            self.assertIsNotNone(timeline)
            self.assertIsNotNone(previous)
            self.assertIsNotNone(next_button)
            self.assertIsNotNone(end)
            timeline_names = [
                str(timeline.item(row).data(QtCore.Qt.UserRole) or "")
                for row in range(timeline.count())
            ]
            self.assertIn(body.Name, timeline_names)
            self.assertNotIn(feature.Name, timeline_names)

            end.click()
            self._process_events()
            previous.click()
            self._process_events()
            self.assertEqual(controller.Position, block_start)
            self.assertFalse(body.Visibility)
            self.assertFalse(feature.Visibility)

            next_button.click()
            self._process_events()
            self.assertEqual(controller.Position, block_end)
            self.assertIs(body.Tip, feature)
            self.assertTrue(body.Visibility)
            self.assertTrue(feature.Visibility)
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_model_fastener_block_survives_history_storage_and_semantic_delete(
        self,
    ) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui

        document = App.newDocument("ModelFastenerLifecycle")
        document.UndoMode = True
        restored_document = None
        try:
            actions = self._ribbon_actions()
            self._trigger_catalog_dialog_action(
                actions["VibeCAD_InsertStandardFastener"],
                lambda driver: self._set_catalog_dialog_values(
                    driver,
                    standard="ISO4762",
                    nominal_thread="M3",
                    length_mm=10,
                    label="Lifecycle M3 socket bolt",
                ),
            )

            selected = Gui.Selection.getSelection()
            self.assertEqual(len(selected), 1)
            body = selected[0]
            feature = body.Tip
            body_name = body.Name
            feature_name = feature.Name
            self._assert_exact_timeline_block(
                document,
                body,
                (feature,),
            )
            self.assertIs(body.VibeCADTimelineEditor, feature)
            self.assertIs(feature.getParentGeoFeatureGroup(), body)
            self.assertFalse(feature.Shape.isNull())

            document.undo()
            self._process_events()
            self.assertIsNone(document.getObject(body_name))
            self.assertIsNone(document.getObject(feature_name))

            document.redo()
            self._process_events()
            body = document.getObject(body_name)
            feature = document.getObject(feature_name)
            self.assertIsNotNone(body)
            self.assertIsNotNone(feature)
            self.assertIs(body.Tip, feature)
            self.assertIs(feature.getParentGeoFeatureGroup(), body)
            self.assertIs(body.VibeCADTimelineEditor, feature)
            self._assert_exact_timeline_block(
                document,
                body,
                (feature,),
            )

            with tempfile.TemporaryDirectory() as temporary_directory:
                saved_file = (
                    Path(temporary_directory)
                    / "model-fastener-lifecycle.FCStd"
                )
                document.saveAs(str(saved_file))
                App.closeDocument(document.Name)
                document = None

                restored_document = App.openDocument(str(saved_file))
                restored_document.UndoMode = True
                self._process_events()
                body = restored_document.getObject(body_name)
                feature = restored_document.getObject(feature_name)
                self.assertIsNotNone(body)
                self.assertIsNotNone(feature)
                self.assertIs(body.Tip, feature)
                self.assertIs(feature.getParentGeoFeatureGroup(), body)
                self.assertIs(body.VibeCADTimelineEditor, feature)
                self.assertFalse(feature.Shape.isNull())
                self._assert_exact_timeline_block(
                    restored_document,
                    body,
                    (feature,),
                )

                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(body)
                undo_before_delete = int(restored_document.UndoCount)
                Gui.runCommand("Std_Delete", 0)
                self._process_events()
                self.assertIsNone(restored_document.getObject(body_name))
                self.assertIsNone(restored_document.getObject(feature_name))
                deleted_timeline_names = {
                    obj.Name
                    for obj in restored_document.getObject(
                        "VibeCADTimeline"
                    ).Operations
                }
                self.assertNotIn(body_name, deleted_timeline_names)
                self.assertNotIn(feature_name, deleted_timeline_names)
                self.assertEqual(
                    int(restored_document.UndoCount),
                    undo_before_delete + 1,
                )

                restored_document.undo()
                self._process_events()
                body = restored_document.getObject(body_name)
                feature = restored_document.getObject(feature_name)
                self.assertIsNotNone(body)
                self.assertIsNotNone(feature)
                self.assertIs(body.Tip, feature)
                self.assertIs(feature.getParentGeoFeatureGroup(), body)
                self._assert_exact_timeline_block(
                    restored_document,
                    body,
                    (feature,),
                )

                restored_document.redo()
                self._process_events()
                self.assertIsNone(restored_document.getObject(body_name))
                self.assertIsNone(restored_document.getObject(feature_name))
                deleted_timeline_names = {
                    obj.Name
                    for obj in restored_document.getObject(
                        "VibeCADTimeline"
                    ).Operations
                }
                self.assertNotIn(body_name, deleted_timeline_names)
                self.assertNotIn(feature_name, deleted_timeline_names)
        finally:
            Gui.Selection.clearSelection()
            if (
                restored_document is not None
                and restored_document.Name in App.listDocuments()
            ):
                App.closeDocument(restored_document.Name)
            if document is not None and document.Name in App.listDocuments():
                App.closeDocument(document.Name)

    def test_assembly_fastener_definition_follows_occurrence_timeline(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide import QtCore, QtWidgets

        import UtilsAssembly
        import VibeCADFastenersGui
        from VibeCADFasteners import resolve_fastener

        document = App.newDocument("AssemblyFastenerTimeline")
        document.UndoMode = True
        restored_document = None
        try:
            Gui.activateWorkbench("AssemblyWorkbench")
            assembly = document.addObject(
                "Assembly::AssemblyObject",
                "FastenerAssembly",
            )
            assembly.Type = "Assembly"
            assembly.newObject("Assembly::JointGroup", "Joints")
            self.assertTrue(Gui.getDocument(document.Name).setEdit(assembly))
            self._process_events()
            self.assertIs(UtilsAssembly.activeAssembly(), assembly)
            controller = document.getObject("VibeCADTimeline")
            self.assertIsNotNone(controller)
            position_before_insert = int(controller.Position)

            identity = resolve_fastener(
                standard="ISO4762",
                nominal_thread="M3",
                length_mm=10,
                model_thread=False,
            )
            values = {
                "standard": identity["standard"],
                "nominal_thread": identity["nominal_size"],
                "length_mm": identity["length_mm"],
                "model_thread": identity["model_thread"],
                "left_handed": identity["left_handed"],
                "options": identity["options"],
                "label": "Assembly M3 socket bolt",
                "identity": identity,
            }
            command = VibeCADFastenersGui._InsertStandardFastenerCommand()
            self.assertTrue(command.IsActive())
            with mock.patch.object(
                VibeCADFastenersGui,
                "_FastenerDialog",
            ) as dialog_class:
                dialog_class.return_value.exec.return_value = values
                command.Activated()
            self._process_events()

            selected = Gui.Selection.getSelection()
            self.assertEqual(len(selected), 1)
            occurrence = selected[0]
            self.assertEqual(occurrence.TypeId, "App::Link")
            self.assertIn(occurrence, assembly.Group)
            self.assertEqual(
                occurrence.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                occurrence.VibeCADTimelineEditCommand,
                "VibeCAD_EditStandardFastener",
            )
            source = occurrence.LinkedObject
            self.assertIsNotNone(source)
            self.assertEqual(source.TypeId, "Part::FeaturePython")
            self.assertEqual(source.VibeCADTimelineRole, "resource")
            self.assertIs(source.VibeCADTimelineOwner, occurrence)
            self.assertEqual(
                source.getTypeIdOfProperty("VibeCADTimelineOwner"),
                "App::PropertyLinkHidden",
            )
            self.assertNotIn(
                occurrence,
                source.OutList,
                "Timeline ownership must not create a reverse modeling dependency",
            )
            self.assertIn(
                "Hidden",
                source.getEditorMode("VibeCADTimelineRole"),
            )
            self.assertIn(
                "Hidden",
                source.getEditorMode("VibeCADTimelineOwner"),
            )

            edited_identity = resolve_fastener(
                standard="ISO4762",
                nominal_thread="M3",
                length_mm=12,
                model_thread=False,
            )
            undo_before_edit = int(document.UndoCount)
            with mock.patch.object(
                VibeCADFastenersGui,
                "_FastenerDialog",
            ) as dialog_class:
                dialog_class.return_value.exec.return_value = {
                    "standard": edited_identity["standard"],
                    "nominal_thread": edited_identity["nominal_size"],
                    "length_mm": edited_identity["length_mm"],
                    "model_thread": edited_identity["model_thread"],
                    "left_handed": edited_identity["left_handed"],
                    "options": edited_identity["options"],
                    "label": "Edited Assembly M3 socket bolt",
                    "identity": edited_identity,
                }
                items, item = self._history_item(occurrence)
                items.itemDoubleClicked.emit(item)
            self._process_events()
            self.assertEqual(
                int(document.UndoCount),
                undo_before_edit + 1,
            )
            self.assertIs(occurrence.LinkedObject, source)
            self.assertEqual(
                occurrence.Label,
                "Edited Assembly M3 socket bolt",
            )
            self.assertAlmostEqual(float(source.Length), 12.0)

            document.undo()
            self._process_events()
            self.assertIs(occurrence.LinkedObject, source)
            self.assertEqual(
                occurrence.Label,
                "Assembly M3 socket bolt",
            )
            self.assertAlmostEqual(float(source.Length), 10.0)

            controller = document.getObject("VibeCADTimeline")
            self.assertIsNotNone(controller)
            operations = list(controller.Operations)
            self.assertIn(occurrence, operations)
            self.assertIn(source, operations)
            self.assertEqual(operations[-2:], [source, occurrence])
            self.assertEqual(operations.index(source), position_before_insert)
            occurrence_boundary = operations.index(occurrence) + 1

            main_window = Gui.getMainWindow()
            timeline = main_window.findChild(
                QtWidgets.QListWidget,
                "VibeCADFeatureTimelineItems",
            )
            previous = main_window.findChild(
                QtWidgets.QToolButton,
                "VibeCADFeatureTimelinePrevious",
            )
            next_button = main_window.findChild(
                QtWidgets.QToolButton,
                "VibeCADFeatureTimelineNext",
            )
            end = main_window.findChild(
                QtWidgets.QToolButton,
                "VibeCADFeatureTimelineEnd",
            )
            self.assertIsNotNone(timeline)
            self.assertIsNotNone(previous)
            self.assertIsNotNone(next_button)
            self.assertIsNotNone(end)

            def visible_timeline_names() -> list[str]:
                return [
                    str(timeline.item(row).data(QtCore.Qt.UserRole))
                    for row in range(timeline.count())
                    if timeline.item(row).data(QtCore.Qt.UserRole)
                ]

            self.assertIn(occurrence.Name, visible_timeline_names())
            self.assertNotIn(source.Name, visible_timeline_names())
            end.click()
            self._process_events()
            previous.click()
            self._process_events()
            self.assertEqual(controller.Position, position_before_insert)
            self.assertFalse(occurrence.Visibility)
            self.assertNotIn(source.Name, visible_timeline_names())

            next_button.click()
            self._process_events()
            self.assertEqual(controller.Position, occurrence_boundary)
            self.assertTrue(occurrence.Visibility)

            previous.click()
            self._process_events()
            self.assertEqual(controller.Position, position_before_insert)
            self.assertFalse(occurrence.Visibility)

            occurrence_name = occurrence.Name
            source_name = source.Name
            saved_position = controller.Position
            Gui.getDocument(document.Name).resetEdit()
            with tempfile.TemporaryDirectory() as temporary_directory:
                saved_file = (
                    Path(temporary_directory) / "assembly_fastener_timeline.FCStd"
                )
                document.saveAs(str(saved_file))
                App.closeDocument(document.Name)
                document = None

                restored_document = App.openDocument(str(saved_file))
                self._process_events()
                restored_occurrence = restored_document.getObject(occurrence_name)
                restored_source = restored_document.getObject(source_name)
                restored_controller = restored_document.getObject("VibeCADTimeline")
                self.assertIsNotNone(restored_occurrence)
                self.assertIsNotNone(restored_source)
                self.assertIs(
                    restored_occurrence.LinkedObject,
                    restored_source,
                )
                self.assertEqual(
                    restored_source.VibeCADTimelineRole,
                    "resource",
                )
                self.assertIs(
                    restored_source.VibeCADTimelineOwner,
                    restored_occurrence,
                )
                self.assertEqual(
                    restored_source.getTypeIdOfProperty("VibeCADTimelineOwner"),
                    "App::PropertyLinkHidden",
                )
                self.assertNotIn(
                    restored_occurrence,
                    restored_source.OutList,
                )
                self.assertEqual(
                    restored_controller.Position,
                    saved_position,
                )
                self.assertNotIn(
                    source_name,
                    visible_timeline_names(),
                )
        finally:
            Gui.Selection.clearSelection()
            if (
                restored_document is not None
                and restored_document.Name in App.listDocuments()
            ):
                App.closeDocument(restored_document.Name)
            if document is not None and document.Name in App.listDocuments():
                if Gui.getDocument(document.Name) is not None:
                    Gui.getDocument(document.Name).resetEdit()
                App.closeDocument(document.Name)

    def test_deleting_assembly_fastener_occurrence_removes_its_definition(
        self,
    ) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui

        import UtilsAssembly
        import VibeCADFastenersGui
        from VibeCADFasteners import resolve_fastener

        document = App.newDocument("AssemblyFastenerDeleteContract")
        document.UndoMode = True
        try:
            Gui.activateWorkbench("AssemblyWorkbench")
            assembly = document.addObject(
                "Assembly::AssemblyObject",
                "FastenerDeleteAssembly",
            )
            assembly.Type = "Assembly"
            assembly.newObject("Assembly::JointGroup", "Joints")
            self.assertTrue(Gui.getDocument(document.Name).setEdit(assembly))
            self._process_events()
            self.assertIs(UtilsAssembly.activeAssembly(), assembly)

            identity = resolve_fastener(
                standard="ISO4762",
                nominal_thread="M3",
                length_mm=10,
                model_thread=False,
            )
            command = VibeCADFastenersGui._InsertStandardFastenerCommand()
            with mock.patch.object(
                VibeCADFastenersGui,
                "_FastenerDialog",
            ) as dialog_class:
                dialog_class.return_value.exec.return_value = {
                    "standard": identity["standard"],
                    "nominal_thread": identity["nominal_size"],
                    "length_mm": identity["length_mm"],
                    "model_thread": identity["model_thread"],
                    "left_handed": identity["left_handed"],
                    "options": identity["options"],
                    "label": "Delete-contract socket bolt",
                    "identity": identity,
                }
                command.Activated()
            self._process_events()

            occurrence = Gui.Selection.getSelection()[0]
            source = occurrence.LinkedObject
            occurrence_name = occurrence.Name
            source_name = source.Name
            before_undo = int(document.UndoCount)
            self.assertEqual(source.VibeCADTimelineRole, "resource")
            self.assertIs(source.VibeCADTimelineOwner, occurrence)

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(occurrence)
            Gui.runCommand("Std_Delete", 0)
            self._process_events()

            self.assertIsNone(document.getObject(source_name))
            self.assertIsNone(document.getObject(occurrence_name))
            self.assertEqual(int(document.UndoCount), before_undo + 1)

            document.undo()
            self._process_events()
            restored_occurrence = document.getObject(occurrence_name)
            restored_source = document.getObject(source_name)
            self.assertIsNotNone(restored_occurrence)
            self.assertIsNotNone(restored_source)
            self.assertIs(restored_occurrence.LinkedObject, restored_source)
            self.assertIs(
                restored_source.VibeCADTimelineOwner,
                restored_occurrence,
            )

            document.redo()
            self._process_events()
            self.assertIsNone(document.getObject(source_name))
            self.assertIsNone(document.getObject(occurrence_name))
        finally:
            Gui.Selection.clearSelection()
            gui_document = Gui.getDocument(document.Name)
            if gui_document is not None and gui_document.getInEdit() is not None:
                gui_document.resetEdit()
            App.closeDocument(document.Name)

    def test_legacy_assembly_fastener_migration_requires_one_occurrence(
        self,
    ) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui

        import VibeCADFastenersGui
        from VibeCADFasteners import create_fastener_feature

        document = App.newDocument("LegacyAssemblyFastenerTimeline")
        restored_document = None
        try:
            assembly = document.addObject(
                "Assembly::AssemblyObject",
                "LegacyFastenerAssembly",
            )
            unique_source, _identity = create_fastener_feature(
                document,
                standard="ISO4762",
                nominal_thread="M3",
                length_mm=10,
                model_thread=False,
                object_name="UniqueLegacyDefinition",
            )
            unique_source.ViewObject.Visibility = False
            unique_source.ViewObject.ShowInTree = False
            unique_occurrence = assembly.newObject(
                "App::Link",
                "UniqueLegacyOccurrence",
            )
            unique_occurrence.LinkedObject = unique_source

            shared_source, _identity = create_fastener_feature(
                document,
                standard="ISO4762",
                nominal_thread="M4",
                length_mm=12,
                model_thread=False,
                object_name="SharedLegacyDefinition",
            )
            shared_source.ViewObject.Visibility = False
            shared_source.ViewObject.ShowInTree = False
            first_shared_occurrence = assembly.newObject(
                "App::Link",
                "FirstSharedLegacyOccurrence",
            )
            first_shared_occurrence.LinkedObject = shared_source
            second_shared_occurrence = assembly.newObject(
                "App::Link",
                "SecondSharedLegacyOccurrence",
            )
            second_shared_occurrence.LinkedObject = shared_source
            document.recompute()

            self.assertNotIn(
                "VibeCADTimelineRole",
                unique_source.PropertiesList,
            )
            self.assertNotIn(
                "VibeCADTimelineRole",
                shared_source.PropertiesList,
            )
            unique_source_name = unique_source.Name
            unique_occurrence_name = unique_occurrence.Name
            shared_source_name = shared_source.Name

            with tempfile.TemporaryDirectory() as temporary_directory:
                saved_file = (
                    Path(temporary_directory)
                    / "legacy_assembly_fastener_timeline.FCStd"
                )
                document.saveAs(str(saved_file))
                App.closeDocument(document.Name)
                document = None

                restored_document = App.openDocument(str(saved_file))
                restored_unique_source = restored_document.getObject(unique_source_name)
                restored_unique_occurrence = restored_document.getObject(
                    unique_occurrence_name
                )
                restored_shared_source = restored_document.getObject(shared_source_name)
                migrated = (
                    VibeCADFastenersGui.migrate_assembly_fastener_timeline_resources(
                        restored_document
                    )
                )

                self.assertEqual(
                    restored_unique_source.VibeCADTimelineRole,
                    "resource",
                )
                self.assertIs(
                    restored_unique_source.VibeCADTimelineOwner,
                    restored_unique_occurrence,
                )
                self.assertEqual(
                    restored_unique_occurrence.VibeCADTimelineRole,
                    "operation",
                )
                self.assertEqual(
                    restored_unique_occurrence.VibeCADTimelineEditCommand,
                    "VibeCAD_EditStandardFastener",
                )
                self.assertTrue(not migrated or migrated == [restored_unique_source])
                self.assertNotIn(
                    "VibeCADTimelineRole",
                    restored_shared_source.PropertiesList,
                    "A shared definition has no single correct occurrence owner",
                )

                restored_document.save()
                App.closeDocument(restored_document.Name)
                restored_document = App.openDocument(str(saved_file))
                restored_unique_source = restored_document.getObject(unique_source_name)
                restored_unique_occurrence = restored_document.getObject(
                    unique_occurrence_name
                )
                self.assertEqual(
                    restored_unique_source.VibeCADTimelineRole,
                    "resource",
                )
                self.assertIs(
                    restored_unique_source.VibeCADTimelineOwner,
                    restored_unique_occurrence,
                )
        finally:
            Gui.Selection.clearSelection()
            if (
                restored_document is not None
                and restored_document.Name in App.listDocuments()
            ):
                App.closeDocument(restored_document.Name)
            if document is not None and document.Name in App.listDocuments():
                App.closeDocument(document.Name)

    def test_edit_standard_fastener_ribbon_action_updates_in_place(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui

        from VibeCADFasteners import fastener_feature_identity

        document = App.newDocument("FastenerRibbonEdit")
        document.UndoMode = True
        try:
            actions = self._ribbon_actions()
            body, feature, initial = self._create_standard_fastener(document)
            feature_name = feature.Name
            initial_height = float(feature.Shape.BoundBox.ZLength)
            Gui.Selection.clearSelection()
            # Timeline selection resolves to the native feature rather than
            # the visible Body row; editing through either route must keep the
            # Body's user-facing label synchronized.
            Gui.Selection.addSelection(feature)
            self._process_events()
            edit = actions["VibeCAD_EditStandardFastener"]
            self.assertTrue(edit.isEnabled())
            self.assertEqual(
                feature.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                feature.VibeCADTimelineEditCommand,
                "VibeCAD_EditStandardFastener",
            )
            undo_before = int(document.UndoCount)

            self._trigger_catalog_dialog_action(
                edit,
                lambda driver: self._set_catalog_dialog_values(
                    driver,
                    standard="ISO4762",
                    nominal_thread="M3",
                    length_mm=12,
                    label="Edited M3 socket bolt",
                ),
            )

            self.assertIs(document.getObject(feature_name), feature)
            self.assertIs(feature.getParentGeoFeatureGroup(), body)
            self.assertIs(body.Tip, feature)
            self.assertEqual(body.Label, "Edited M3 socket bolt")
            self.assertTrue(
                feature.Label.startswith("Edited M3 socket bolt")
            )
            self.assertNotEqual(feature.Label, body.Label)
            updated = fastener_feature_identity(feature)
            self.assertNotEqual(
                updated["canonical_key"],
                initial["canonical_key"],
            )
            self.assertAlmostEqual(float(updated["length_mm"]), 12.0)
            self.assertFalse(feature.Shape.isNull())
            self.assertTrue(feature.Shape.isValid())
            self.assertEqual(len(feature.Shape.Solids), 1)
            self.assertTrue(feature.isValid(), feature.getStatusString())
            self.assertAlmostEqual(
                float(feature.Shape.BoundBox.ZLength),
                initial_height + 2.0,
            )
            self.assertEqual(Gui.Selection.getSelection(), [feature])
            self.assertEqual(
                int(document.UndoCount),
                undo_before + 1,
            )

            document.undo()
            self._process_events()
            restored = fastener_feature_identity(feature)
            self.assertEqual(
                restored["canonical_key"],
                initial["canonical_key"],
            )
            self.assertAlmostEqual(float(restored["length_mm"]), 10.0)
            self.assertTrue(feature.isValid(), feature.getStatusString())
            self.assertAlmostEqual(
                float(feature.Shape.BoundBox.ZLength),
                initial_height,
            )

            history_identity = dict(updated)
            history_undo_before = int(document.UndoCount)
            with mock.patch(
                "VibeCADFastenersGui._FastenerDialog"
            ) as dialog_class:
                dialog_class.return_value.exec.return_value = {
                    "standard": history_identity["standard"],
                    "nominal_thread": history_identity["nominal_size"],
                    "length_mm": history_identity["length_mm"],
                    "model_thread": history_identity["model_thread"],
                    "left_handed": history_identity["left_handed"],
                    "options": history_identity["options"],
                    "label": "History-edited M3 socket bolt",
                    "identity": history_identity,
                }
                items, item = self._history_item(feature)
                items.itemDoubleClicked.emit(item)
            self._process_events()
            self.assertEqual(
                int(document.UndoCount),
                history_undo_before + 1,
            )
            self.assertEqual(
                body.Label,
                "History-edited M3 socket bolt",
            )
            self.assertAlmostEqual(
                float(fastener_feature_identity(feature)["length_mm"]),
                12.0,
            )
            self.assertTrue(feature.isValid(), feature.getStatusString())
            self.assertAlmostEqual(
                float(feature.Shape.BoundBox.ZLength),
                initial_height + 2.0,
            )
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_insert_and_edit_cancel_are_exact_no_mutation_operations(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        import Part

        from VibeCADFasteners import fastener_feature_identity

        document = App.newDocument("FastenerRibbonCancel")
        # Drain the document-created callbacks before constructing the fixture.
        # Otherwise the zero-delay restored-document render pass can recompute
        # freshly added objects inside the modal dialog event loop, which is
        # unrelated to either fastener command's Cancel contract.
        self._process_events()
        document.UndoMode = True
        try:
            context_body = document.addObject(
                "PartDesign::Body",
                "UntouchedContextBody",
            )
            context_feature = context_body.newObject(
                "PartDesign::Feature",
                "UntouchedContextResult",
            )
            context_feature.Shape = Part.makeBox(12, 10, 8)
            context_body.Tip = context_feature

            fastener_body, fastener, initial_identity = (
                self._create_standard_fastener(
                    document,
                    body_name="CanceledEditFastenerBody",
                    length_mm=10,
                )
            )
            sentinel = document.addObject(
                "Part::Feature",
                "VisibilitySentinel",
            )
            sentinel.Shape = Part.makeCylinder(2, 5)
            document.recompute()

            actions = self._ribbon_actions()
            insert = actions["VibeCAD_InsertStandardFastener"]
            edit = actions["VibeCAD_EditStandardFastener"]

            context_body.ViewObject.Visibility = True
            context_feature.ViewObject.Visibility = False
            fastener_body.ViewObject.Visibility = False
            fastener.ViewObject.Visibility = True
            sentinel.ViewObject.Visibility = False
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(context_feature, "Face1")
            Gui.activeView().setActiveObject("pdbody", context_body)
            self._process_events()
            self.assertTrue(insert.isEnabled())
            self.assertFalse(document.HasPendingTransaction)
            insert_before = self._command_state_snapshot(document)

            def configure_insert_cancel(driver):
                self._set_catalog_dialog_values(
                    driver,
                    standard="ISO4762",
                    nominal_thread="M3",
                    length_mm=12,
                    label="Must not be inserted",
                )
                driver.model_thread.setChecked(True)
                if driver.left_handed.isEnabled():
                    driver.left_handed.setChecked(True)

            self._cancel_catalog_dialog_action(
                insert,
                configure_insert_cancel,
            )
            self._assert_command_state_unchanged(document, insert_before)
            self.assertFalse(document.HasPendingTransaction)
            self.assertIs(document.getObject("VisibilitySentinel"), sentinel)

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(fastener)
            Gui.activeView().setActiveObject("pdbody", context_body)
            context_body.ViewObject.Visibility = False
            context_feature.ViewObject.Visibility = True
            fastener_body.ViewObject.Visibility = True
            fastener.ViewObject.Visibility = False
            sentinel.ViewObject.Visibility = True
            self._process_events()
            self.assertTrue(edit.isEnabled())
            self.assertFalse(bool(fastener.Thread))
            self.assertFalse(document.HasPendingTransaction)
            edit_before = self._command_state_snapshot(document)

            def configure_edit_cancel(driver):
                self._set_catalog_dialog_values(
                    driver,
                    standard="ISO4762",
                    nominal_thread="M3",
                    length_mm=12,
                    label="Must not replace the existing label",
                )
                driver.model_thread.setChecked(True)
                if driver.left_handed.isEnabled():
                    driver.left_handed.setChecked(True)

            self._cancel_catalog_dialog_action(
                edit,
                configure_edit_cancel,
            )
            self._assert_command_state_unchanged(document, edit_before)
            self.assertEqual(
                fastener_feature_identity(fastener),
                initial_identity,
            )
            self.assertFalse(bool(fastener.Thread))
            self.assertFalse(document.HasPendingTransaction)
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_disabled_matching_hole_and_attach_actions_are_exact_no_ops(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        import Part

        document = App.newDocument("FastenerDisabledActionNoOp")
        self._process_events()
        document.UndoMode = True
        try:
            actions = self._ribbon_actions()
            sentinel = document.addObject("Part::Feature", "UntouchedSentinel")
            sentinel.Shape = Part.makeBox(4, 5, 6)
            document.recompute()
            sentinel.ViewObject.Visibility = False
            Gui.Selection.clearSelection()
            self._process_events()

            for command_name in (
                "VibeCAD_CreateMatchingFastenerHole",
                "VibeCAD_AttachStandardFastener",
            ):
                with self.subTest(command=command_name):
                    action = actions[command_name]
                    self.assertFalse(action.isEnabled())
                    before = self._command_state_snapshot(document)
                    action.trigger()
                    self._process_events()
                    self._assert_command_state_unchanged(document, before)
                    self.assertFalse(document.HasPendingTransaction)
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_all_standard_component_actions_lock_during_native_task(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        import Part
        from PySide import QtGui

        document = App.newDocument("FastenerNestedTaskLock")
        self._process_events()
        document.UndoMode = True
        try:
            actions = self._ribbon_actions()
            fixture = self._create_matching_hole_fixture(document)
            attachment_host = document.addObject(
                "Part::Cylinder",
                "NestedTaskAttachmentHost",
            )
            attachment_host.Radius = 6
            attachment_host.Height = 8
            document.recompute()
            circular_edge = next(
                index
                for index, edge in enumerate(
                    attachment_host.Shape.Edges,
                    start=1,
                )
                if isinstance(edge.Curve, Part.Circle)
            )
            circular_sub_name = f"Edge{circular_edge}"

            def select_for(command_name: str) -> None:
                Gui.Selection.clearSelection()
                if command_name == "VibeCAD_EditStandardFastener":
                    Gui.Selection.addSelection(fixture["fastener_body"])
                elif command_name == "VibeCAD_CreateMatchingFastenerHole":
                    Gui.Selection.addSelection(fixture["fastener_body"])
                    Gui.Selection.addSelection(fixture["sketch"])
                elif command_name == "VibeCAD_AttachStandardFastener":
                    Gui.Selection.addSelection(fixture["fastener_body"])
                    Gui.Selection.addSelection(
                        attachment_host,
                        circular_sub_name,
                    )
                self._process_events()

            for command_name in _STANDARD_COMPONENT_COMMANDS:
                select_for(command_name)
                self.assertTrue(actions[command_name].isEnabled(), command_name)

            class BlockingTask:
                def __init__(self) -> None:
                    self.form = QtGui.QWidget()
                    self.form.setWindowTitle("Fastener command boundary")

                def accept(self) -> bool:
                    return True

                def reject(self) -> bool:
                    return True

                def getStandardButtons(self):
                    return QtGui.QDialogButtonBox.Cancel

            dialog = Gui.Control.showDialog(
                BlockingTask(),
                Gui.activeDocument(),
            )
            self.assertIsNotNone(dialog)
            self._process_events()
            self.assertTrue(Gui.Control.activeDialog())

            for command_name in _STANDARD_COMPONENT_COMMANDS:
                with self.subTest(command=command_name):
                    select_for(command_name)
                    action = actions[command_name]
                    self.assertFalse(action.isEnabled(), command_name)
                    before = self._command_state_snapshot(document)
                    action.trigger()
                    self._process_events()
                    self._assert_command_state_unchanged(document, before)
        finally:
            Gui.Selection.clearSelection()
            if Gui.Control.activeDialog():
                try:
                    Gui.Control.activeTaskDialog().reject()
                except (AttributeError, RuntimeError):
                    Gui.Control.closeDialog()
                self._process_events()
            self.assertFalse(document.HasPendingTransaction)
            App.closeDocument(document.Name)

    def test_matching_hole_purpose_and_fit_cancel_are_exact_no_ops(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui

        document = App.newDocument("FastenerMatchingHoleCancel")
        self._process_events()
        document.UndoMode = True
        try:
            actions = self._ribbon_actions()
            fixture = self._create_matching_hole_fixture(document)
            Gui.activeView().setActiveObject(
                "pdbody",
                fixture["fastener_body"],
            )
            self._process_events()
            action = actions["VibeCAD_CreateMatchingFastenerHole"]
            self.assertTrue(action.isEnabled())

            cancel_paths = (
                (
                    "purpose",
                    (("Purpose", "clearance", False),),
                ),
                (
                    "fit",
                    (
                        ("Purpose", "clearance", True),
                        ("Fit", "normal", False),
                    ),
                ),
            )
            for cancel_point, decisions in cancel_paths:
                with self.subTest(cancel=cancel_point):
                    before = self._command_state_snapshot(document)
                    self._drive_item_dialog_action(action, decisions)
                    self._assert_command_state_unchanged(document, before)
                    self.assertFalse(document.HasPendingTransaction)
                    self.assertIsNone(
                        document.getObject("StandardFastenerHole")
                    )
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_matching_hole_ribbon_action_creates_native_parametric_hole(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        import Part
        from PySide import QtGui

        from VibeCADFasteners import (
            HOLE_SCHEMA,
            PROP_HOLE_FASTENER_KEY,
            PROP_HOLE_SCHEMA,
        )

        document = App.newDocument("FastenerRibbonHole")
        try:
            actions = self._ribbon_actions()
            fastener_body, _fastener, identity = (
                self._create_standard_fastener(document)
            )

            host_body = document.addObject("PartDesign::Body", "HostBody")
            base = document.addObject("PartDesign::AdditiveBox", "HostPlate")
            base.Length = 20
            base.Width = 20
            base.Height = 6
            host_body.addObject(base)
            document.recompute()
            sketch = document.addObject(
                "Sketcher::SketchObject",
                "HoleLocations",
            )
            sketch.AttachmentSupport = (base, ["Face6"])
            sketch.MapMode = "FlatFace"
            sketch.addGeometry(
                Part.Circle(
                    App.Vector(10, 10, 0),
                    App.Vector(0, 0, 1),
                    1.5,
                ),
                False,
            )
            host_body.addObject(sketch)
            document.recompute()

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(fastener_body)
            Gui.Selection.addSelection(sketch)
            self._process_events()
            action = actions["VibeCAD_CreateMatchingFastenerHole"]
            self.assertTrue(action.isEnabled())

            answers = iter(
                (
                    ("clearance", True),
                    ("normal", True),
                )
            )

            def choose_item(
                _parent,
                _title,
                _label,
                items,
                _current,
                _editable,
            ):
                answer = next(answers)
                self.assertIn(answer[0], [str(item) for item in items])
                return answer

            with mock.patch.object(QtGui, "QInputDialog") as input_dialog:
                input_dialog.getItem.side_effect = choose_item
                action.trigger()
            self._process_events()
            self.assertEqual(input_dialog.getItem.call_count, 2)

            selected = Gui.Selection.getSelection()
            self.assertEqual(len(selected), 1)
            hole = selected[0]
            self.assertEqual(hole.TypeId, "PartDesign::Hole")
            self.assertIs(hole.getParentGeoFeatureGroup(), host_body)
            self.assertIs(host_body.Tip, hole)
            profile = hole.Profile
            profile_object = profile[0] if isinstance(profile, tuple) else profile
            self.assertIs(profile_object, sketch)
            self.assertEqual(getattr(hole, PROP_HOLE_SCHEMA), HOLE_SCHEMA)
            self.assertEqual(
                getattr(hole, PROP_HOLE_FASTENER_KEY),
                identity["canonical_key"],
            )
            self.assertFalse(hole.Shape.isNull())
            self.assertTrue(hole.Shape.isValid())
            self.assertEqual(len(hole.Shape.Solids), 1)
            self.assertLess(hole.Shape.Volume, base.Shape.Volume)
            self.assertIs(
                Gui.activeView().getActiveObject("pdbody"),
                host_body,
            )
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_matching_hole_is_one_body_history_step_with_full_lifecycle(
        self,
    ) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        from PySide import QtGui

        document = App.newDocument("MatchingHoleLifecycle")
        document.UndoMode = True
        restored_document = None
        try:
            actions = self._ribbon_actions()
            fixture = self._create_matching_hole_fixture(document)
            host_body = fixture["host_body"]
            base = fixture["base"]
            sketch = fixture["sketch"]
            fastener_body = fixture["fastener_body"]
            fastener = fixture["fastener"]
            prior_tip = host_body.Tip
            prior_tip_name = prior_tip.Name
            prior_group_names = tuple(obj.Name for obj in host_body.Group)
            controller = document.getObject("VibeCADTimeline")
            self.assertIsNotNone(controller)
            operations_before = tuple(controller.Operations)

            answers = iter((("clearance", True), ("normal", True)))

            def choose_item(
                _parent,
                _title,
                _label,
                items,
                _current,
                _editable,
            ):
                answer = next(answers)
                self.assertIn(answer[0], [str(item) for item in items])
                return answer

            with mock.patch.object(QtGui, "QInputDialog") as input_dialog:
                input_dialog.getItem.side_effect = choose_item
                actions["VibeCAD_CreateMatchingFastenerHole"].trigger()
            self._process_events()
            self.assertEqual(input_dialog.getItem.call_count, 2)

            hole = Gui.Selection.getSelection()[0]
            hole_name = hole.Name
            host_body_name = host_body.Name
            base_name = base.Name
            sketch_name = sketch.Name
            fastener_body_name = fastener_body.Name
            fastener_name = fastener.Name
            self.assertEqual(hole.TypeId, "PartDesign::Hole")
            self.assertIs(hole.getParentGeoFeatureGroup(), host_body)
            self.assertIs(host_body.Tip, hole)
            self.assertEqual(
                tuple(obj.Name for obj in host_body.Group),
                prior_group_names + (hole_name,),
            )
            self.assertEqual(
                tuple(controller.Operations),
                operations_before + (hole,),
            )
            self._assert_exact_timeline_block(
                document,
                hole,
                explicit_operation=False,
            )
            self.assertNotIn(
                "VibeCADTimelineReplacedInputs",
                hole.PropertiesList,
                "A native Part Design Hole is a new Body history step, "
                "not a replacement of its prior feature",
            )

            document.undo()
            self._process_events()
            host_body = document.getObject(host_body_name)
            self.assertIsNone(document.getObject(hole_name))
            self.assertIs(host_body.Tip, document.getObject(prior_tip_name))
            self.assertEqual(
                tuple(obj.Name for obj in host_body.Group),
                prior_group_names,
            )
            for name in (
                base_name,
                sketch_name,
                fastener_body_name,
                fastener_name,
            ):
                self.assertIsNotNone(document.getObject(name), name)

            document.redo()
            self._process_events()
            host_body = document.getObject(host_body_name)
            hole = document.getObject(hole_name)
            self.assertIsNotNone(hole)
            self.assertIs(host_body.Tip, hole)
            self.assertIs(hole.getParentGeoFeatureGroup(), host_body)
            self.assertNotIn(
                "VibeCADTimelineReplacedInputs",
                hole.PropertiesList,
            )
            self._assert_exact_timeline_block(
                document,
                hole,
                explicit_operation=False,
            )

            with tempfile.TemporaryDirectory() as temporary_directory:
                saved_file = (
                    Path(temporary_directory)
                    / "matching-hole-lifecycle.FCStd"
                )
                document.saveAs(str(saved_file))
                App.closeDocument(document.Name)
                document = None

                restored_document = App.openDocument(str(saved_file))
                restored_document.UndoMode = True
                self._process_events()
                host_body = restored_document.getObject(host_body_name)
                hole = restored_document.getObject(hole_name)
                base = restored_document.getObject(base_name)
                sketch = restored_document.getObject(sketch_name)
                fastener_body = restored_document.getObject(
                    fastener_body_name
                )
                fastener = restored_document.getObject(fastener_name)
                for obj in (
                    host_body,
                    hole,
                    base,
                    sketch,
                    fastener_body,
                    fastener,
                ):
                    self.assertIsNotNone(obj)
                self.assertIs(host_body.Tip, hole)
                profile = hole.Profile
                profile_object = (
                    profile[0] if isinstance(profile, tuple) else profile
                )
                self.assertIs(profile_object, sketch)
                self.assertNotIn(
                    "VibeCADTimelineReplacedInputs",
                    hole.PropertiesList,
                )
                self._assert_exact_timeline_block(
                    restored_document,
                    hole,
                    explicit_operation=False,
                )

                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(hole)
                Gui.runCommand("Std_Delete", 0)
                self._process_events()
                self.assertIsNone(restored_document.getObject(hole_name))
                self.assertNotIn(
                    hole_name,
                    {
                        obj.Name
                        for obj in restored_document.getObject(
                            "VibeCADTimeline"
                        ).Operations
                    },
                )
                host_body = restored_document.getObject(host_body_name)
                self.assertIs(
                    host_body.Tip,
                    restored_document.getObject(prior_tip_name),
                )
                for name in (
                    host_body_name,
                    base_name,
                    sketch_name,
                    fastener_body_name,
                    fastener_name,
                ):
                    self.assertIsNotNone(
                        restored_document.getObject(name),
                        name,
                    )

                restored_document.undo()
                self._process_events()
                host_body = restored_document.getObject(host_body_name)
                hole = restored_document.getObject(hole_name)
                self.assertIsNotNone(hole)
                self.assertIs(host_body.Tip, hole)
                profile = hole.Profile
                profile_object = (
                    profile[0] if isinstance(profile, tuple) else profile
                )
                self.assertIs(
                    profile_object,
                    restored_document.getObject(sketch_name),
                )
                self._assert_exact_timeline_block(
                    restored_document,
                    hole,
                    explicit_operation=False,
                )

                restored_document.redo()
                self._process_events()
                self.assertIsNone(restored_document.getObject(hole_name))
                self.assertNotIn(
                    hole_name,
                    {
                        obj.Name
                        for obj in restored_document.getObject(
                            "VibeCADTimeline"
                        ).Operations
                    },
                )
                for name in (
                    host_body_name,
                    base_name,
                    sketch_name,
                    fastener_body_name,
                    fastener_name,
                ):
                    self.assertIsNotNone(
                        restored_document.getObject(name),
                        name,
                    )
        finally:
            Gui.Selection.clearSelection()
            if (
                restored_document is not None
                and restored_document.Name in App.listDocuments()
            ):
                App.closeDocument(restored_document.Name)
            if document is not None and document.Name in App.listDocuments():
                App.closeDocument(document.Name)

    def test_matching_hole_additional_supported_purpose_fit_variants(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui

        from VibeCADFasteners import (
            HOLE_SCHEMA,
            PROP_HOLE_FASTENER_KEY,
            PROP_HOLE_FIT,
            PROP_HOLE_PURPOSE,
            PROP_HOLE_SCHEMA,
        )

        cases = (
            ("tapped", "normal", "ISO4762", False),
            ("counterbore", "close", "ISO4762", False),
            ("countersink", "loose", "ISO10642", True),
        )
        for purpose, fit, standard, linked_fastener in cases:
            with self.subTest(
                purpose=purpose,
                fit=fit,
                standard=standard,
                linked=linked_fastener,
            ):
                document = App.newDocument(
                    f"FastenerHole_{purpose}_{fit}"
                )
                self._process_events()
                try:
                    actions = self._ribbon_actions()
                    fixture = self._create_matching_hole_fixture(
                        document,
                        standard=standard,
                        linked_fastener=linked_fastener,
                    )
                    action = actions["VibeCAD_CreateMatchingFastenerHole"]
                    self.assertTrue(action.isEnabled())
                    decisions = (("Purpose", purpose, True),)
                    if purpose != "tapped":
                        decisions += (("Fit", fit, True),)
                    self._drive_item_dialog_action(action, decisions)

                    selected = Gui.Selection.getSelection()
                    self.assertEqual(len(selected), 1)
                    hole = selected[0]
                    self.assertEqual(hole.TypeId, "PartDesign::Hole")
                    self.assertIs(
                        hole.getParentGeoFeatureGroup(),
                        fixture["host_body"],
                    )
                    self.assertIs(fixture["host_body"].Tip, hole)
                    profile = hole.Profile
                    profile_object = (
                        profile[0] if isinstance(profile, tuple) else profile
                    )
                    self.assertIs(profile_object, fixture["sketch"])
                    self.assertEqual(
                        getattr(hole, PROP_HOLE_SCHEMA),
                        HOLE_SCHEMA,
                    )
                    self.assertEqual(
                        getattr(hole, PROP_HOLE_FASTENER_KEY),
                        fixture["identity"]["canonical_key"],
                    )
                    self.assertEqual(
                        getattr(hole, PROP_HOLE_PURPOSE),
                        purpose,
                    )
                    self.assertEqual(getattr(hole, PROP_HOLE_FIT), fit)
                    self.assertEqual(bool(hole.Threaded), purpose == "tapped")
                    self.assertFalse(hole.Shape.isNull())
                    self.assertTrue(hole.Shape.isValid())
                    self.assertEqual(len(hole.Shape.Solids), 1)
                    self.assertLess(
                        hole.Shape.Volume,
                        fixture["base"].Shape.Volume,
                    )
                    self.assertIs(
                        Gui.activeView().getActiveObject("pdbody"),
                        fixture["host_body"],
                    )
                    occurrence = fixture["occurrence"]
                    if linked_fastener:
                        self.assertIsNotNone(occurrence)
                        self.assertEqual(occurrence.TypeId, "App::Link")
                        self.assertIs(
                            occurrence.LinkedObject,
                            fixture["fastener"],
                        )
                finally:
                    Gui.Selection.clearSelection()
                    App.closeDocument(document.Name)

    def test_attach_standard_fastener_ribbon_action_tracks_one_circular_edge(self) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        import Part

        document = App.newDocument("FastenerRibbonAttach")
        try:
            actions = self._ribbon_actions()
            body, fastener, _identity = self._create_standard_fastener(document)
            host = document.addObject("Part::Cylinder", "AttachmentHost")
            host.Radius = 6
            host.Height = 8
            host.Placement.Base = App.Vector(30, 0, 0)
            document.recompute()
            circular_edge = next(
                index
                for index, edge in enumerate(host.Shape.Edges, start=1)
                if isinstance(edge.Curve, Part.Circle)
            )
            sub_name = f"Edge{circular_edge}"

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(body)
            Gui.Selection.addSelection(host, sub_name)
            self._process_events()
            action = actions["VibeCAD_AttachStandardFastener"]
            self.assertTrue(action.isEnabled())
            action.trigger()
            self._process_events()

            self.assertIs(fastener.getParentGeoFeatureGroup(), body)
            self.assertIs(body.Tip, fastener)
            attachment = fastener.BaseObject
            self.assertIsNotNone(attachment)
            self.assertIs(attachment[0], host)
            self.assertEqual(list(attachment[1]), [sub_name])
            self.assertFalse(fastener.Shape.isNull())
            self.assertTrue(fastener.Shape.isValid())
            self.assertEqual(len(fastener.Shape.Solids), 1)
            self.assertAlmostEqual(fastener.Placement.Base.x, 30.0)
            self.assertAlmostEqual(fastener.Placement.Base.y, 0.0)
            selected = Gui.Selection.getSelectionEx()
            self.assertEqual([item.Object for item in selected], [body, host])
            self.assertEqual(list(selected[1].SubElementNames), [sub_name])
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_attach_accepts_body_owned_edge_and_rejects_assembly_occurrence(
        self,
    ) -> None:
        import Assembly  # noqa: F401 - registers native Assembly object types
        import FreeCAD as App
        import FreeCADGui as Gui
        import Part

        import VibeCADFastenersGui

        document = App.newDocument("FastenerAttachBodyAndAssembly")
        self._process_events()
        document.UndoMode = True
        try:
            actions = self._ribbon_actions()
            fastener_body, fastener, _identity = (
                self._create_standard_fastener(document)
            )
            host_body = document.addObject(
                "PartDesign::Body",
                "BodyOwnedHost",
            )
            host = host_body.newObject(
                "PartDesign::Feature",
                "BodyOwnedCircularFeature",
            )
            host.Shape = Part.makeCylinder(6, 8)
            host.Placement.Base = App.Vector(30, 0, 0)
            host_body.Tip = host
            document.recompute()
            circular_edge = next(
                index
                for index, edge in enumerate(host.Shape.Edges, start=1)
                if isinstance(edge.Curve, Part.Circle)
            )
            sub_name = f"Edge{circular_edge}"

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(fastener_body)
            Gui.Selection.addSelection(host, sub_name)
            self._process_events()
            action = actions["VibeCAD_AttachStandardFastener"]
            self.assertTrue(action.isEnabled())
            action.trigger()
            self._process_events()

            self.assertIs(fastener.getParentGeoFeatureGroup(), fastener_body)
            self.assertIs(fastener_body.Tip, fastener)
            self.assertIs(host.getParentGeoFeatureGroup(), host_body)
            self.assertIs(host_body.Tip, host)
            attachment = fastener.BaseObject
            self.assertIsNotNone(attachment)
            self.assertIs(attachment[0], host_body)
            self.assertEqual(
                list(attachment[1]),
                [f"{host.Name}.{sub_name}"],
            )
            self.assertFalse(fastener.Shape.isNull())
            self.assertTrue(fastener.Shape.isValid())
            self.assertEqual(len(fastener.Shape.Solids), 1)

            assembly = document.addObject(
                "Assembly::AssemblyObject",
                "NativeAssembly",
            )
            occurrence = assembly.newObject(
                "App::Link",
                "FastenerOccurrence",
            )
            occurrence.LinkedObject = fastener
            document.recompute()
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(occurrence)
            Gui.Selection.addSelection(host, sub_name)
            self._process_events()
            self.assertFalse(action.isEnabled())

            before = self._command_state_snapshot(document)
            action.trigger()
            self._process_events()
            self._assert_command_state_unchanged(document, before)

            command = VibeCADFastenersGui._AttachStandardFastenerCommand()
            with mock.patch.object(
                VibeCADFastenersGui,
                "_show_error",
            ) as show_error:
                command.Activated()
            self.assertEqual(show_error.call_count, 1)
            self.assertIn(
                "Assembly connectors and joints",
                str(show_error.call_args.args[1]),
            )
            self._assert_command_state_unchanged(document, before)
            self.assertFalse(document.HasPendingTransaction)
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)


if __name__ == "__main__":
    unittest.main()
