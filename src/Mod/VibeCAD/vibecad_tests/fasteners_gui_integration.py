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

    def _finish_native_task(self, *, accept: bool) -> None:
        """Commit or cancel the one active native task through its real button."""

        import FreeCADGui as Gui
        from PySide import QtWidgets

        self.assertTrue(Gui.Control.activeDialog())
        standard_button = (
            QtWidgets.QDialogButtonBox.Ok
            if accept
            else QtWidgets.QDialogButtonBox.Cancel
        )
        for button_box in Gui.getMainWindow().findChildren(
            QtWidgets.QDialogButtonBox
        ):
            if not button_box.isVisible():
                continue
            button = button_box.button(standard_button)
            if button is None or not button.isEnabled():
                continue
            button.click()
            self._process_events()
            self.assertFalse(Gui.Control.activeDialog())
            return
        self.fail("The active native task has no enabled completion button.")

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

    def _design_fastener_graph(
        self,
        body: object,
    ) -> tuple[object, object, object, object]:
        """Return and validate one Model-ribbon standard-fastener graph."""

        publication = body.Tip
        self.assertIsNotNone(publication)
        self.assertEqual(
            publication.TypeId,
            "PartDesign::DesignBodyPublication",
        )
        self.assertIs(publication.getParentGeoFeatureGroup(), body)

        state = publication.CurrentState
        self.assertIsNotNone(state)
        self.assertEqual(state.TypeId, "PartDesign::DesignBodyState")
        self.assertIsNone(state.getParentGeoFeatureGroup())
        self.assertEqual(str(state.BodyId), str(body.VibeCADBodyId))

        operation = state.Operation
        self.assertIsNotNone(operation)
        self.assertEqual(
            operation.TypeId,
            "PartDesign::DesignGeneratedOperation",
        )
        self.assertIsNone(operation.getParentGeoFeatureGroup())
        self.assertEqual(operation.GeneratorKind, "standard-fastener")
        self.assertEqual(
            list(operation.OutputBodyIds),
            [str(body.VibeCADBodyId)],
        )
        self.assertIs(state.Operation, operation)
        self.assertEqual(str(state.OperationId), str(operation.OperationId))

        generator = operation.Generator
        self.assertIsNotNone(generator)
        self.assertIn(
            generator.TypeId,
            {"Part::FeaturePython", "PartDesign::FeaturePython"},
        )
        self.assertIsNone(generator.getParentGeoFeatureGroup())
        self.assertEqual(generator.VibeCADTimelineRole, "internal")
        self.assertNotIn("VibeCADTimelineOwner", generator.PropertiesList)
        self.assertFalse(generator.ViewObject.ShowInTree)
        self.assertFalse(generator.ViewObject.Visibility)
        self.assertEqual(
            [
                consumer
                for consumer in generator.InList
                if consumer is not body.Document.getObject("VibeCADTimeline")
            ],
            [operation],
        )
        return publication, state, operation, generator

    def _create_matching_hole_fixture(
        self,
        document: object,
        *,
        standard: str = "ISO4762",
        linked_fastener: bool = False,
    ) -> dict[str, object]:
        """Create one plate, reusable Design sketch, and explicit hole target."""

        import FreeCAD as App
        import FreeCADGui as Gui
        import Part
        import PartDesign

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

        document.openTransaction("Create matching-hole host plate")
        host_operation = document.addObject(
            "PartDesign::DesignBox",
            "HostPlate",
        )
        host_edit = PartDesign.beginDesignOperationEdit(host_operation)
        host_operation.Length = 24
        host_operation.Width = 24
        host_operation.Height = 8
        PartDesign.setDesignOperationTargets(
            host_edit,
            "New Body",
            [],
        )
        document.recompute()
        host_body = PartDesign.finalizeDesignOperationEdit(host_edit)[0]
        host_body.Label = "Host Body"
        document.commitTransaction()
        base = host_body.Tip.CurrentState

        document.openTransaction("Create reusable hole locations")
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
        document.recompute()
        self.assertIsNone(sketch.getParentGeoFeatureGroup())
        PartDesign.finalizeDesignDefinition(sketch)
        document.commitTransaction()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(selected_fastener)
        Gui.Selection.addSelection(sketch)
        Gui.Selection.addSelection(host_body)
        self._process_events()
        return {
            "fastener_body": fastener_body,
            "fastener": fastener,
            "identity": identity,
            "occurrence": occurrence,
            "host_body": host_body,
            "host_operation": host_operation,
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
            sketch = document.addObject("Sketcher::SketchObject", "HoleLocations")
            sketch.addGeometry(
                Part.Circle(App.Vector(0, 0, 0), App.Vector(0, 0, 1), 1.6),
                False,
            )
            body_owned_sketch = host_body.newObject(
                "Sketcher::SketchObject",
                "BodyOwnedHoleLocations",
            )
            body_owned_sketch.addGeometry(
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
            self.assertFalse(matching_hole.IsActive())
            self.assertFalse(attach.IsActive())

            Gui.Selection.addSelection(host_body)
            self.assertTrue(matching_hole.IsActive())
            self.assertFalse(attach.IsActive())

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(fastener_body)
            Gui.Selection.addSelection(body_owned_sketch)
            Gui.Selection.addSelection(host_body)
            self.assertFalse(matching_hole.IsActive())

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
            # One-feature Body-owned fasteners from earlier VibeCAD releases
            # are unambiguous and can be promoted when Attach executes.
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

    def test_insert_standard_fastener_ribbon_action_creates_global_operation(
        self,
    ) -> None:
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
            publication, state, operation, generator = (
                self._design_fastener_graph(body)
            )
            self.assertEqual(
                operation.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                operation.VibeCADTimelineEditCommand,
                "VibeCAD_EditStandardFastener",
            )
            self.assertNotIn(
                "VibeCADTimelineEditor",
                operation.PropertiesList,
            )
            self.assertEqual(operation.Label, "Fastener: M3 socket bolt")
            self.assertEqual(operation.OutputLabel, "M3 socket bolt")
            self.assertEqual(generator.Label, "M3 socket bolt generator")
            self.assertEqual(getattr(generator, PROP_SCHEMA), COMPONENT_SCHEMA)
            self.assertEqual(str(generator.Type), "ISO4762")
            self.assertEqual(str(generator.Diameter), "M3")
            self.assertFalse(generator.Shape.isNull())
            self.assertTrue(generator.Shape.isValid())
            self.assertEqual(len(generator.Shape.Solids), 1)
            self.assertTrue(operation.Shape.isNull())
            self.assertFalse(publication.Shape.isNull())
            self.assertTrue(publication.Shape.isValid())
            self.assertEqual(len(publication.Shape.Solids), 1)
            self.assertFalse(body.Shape.isNull())
            self.assertTrue(body.Shape.isValid())
            self.assertEqual(len(body.Shape.Solids), 1)
            self.assertIsNone(
                Gui.activeView().getActiveObject("pdbody"),
            )

            controller = document.getObject("VibeCADTimeline")
            self.assertIsNotNone(controller)
            operations = list(controller.Operations)
            self.assertEqual(operations[-2:], [state, operation])
            block_start = operations.index(state)
            block_end = operations.index(operation) + 1
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )

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
            self.assertIn(operation.Name, timeline_names)
            self.assertNotIn(generator.Name, timeline_names)
            self.assertNotIn(state.Name, timeline_names)
            self.assertNotIn(publication.Name, timeline_names)
            self.assertNotIn(body.Name, timeline_names)

            end.click()
            self._process_events()
            previous.click()
            self._process_events()
            self.assertEqual(controller.Position, block_start)
            # History presence must not overwrite either saved eye state.
            # Before the creating operation, the stable publication remains
            # the Tip but resolves to an empty shape.
            self.assertTrue(body.Visibility)
            self.assertTrue(publication.Visibility)
            self.assertTrue(body.Shape.isNull())
            self.assertTrue(publication.Shape.isNull())
            self.assertFalse(generator.Visibility)

            next_button.click()
            self._process_events()
            self.assertEqual(controller.Position, block_end)
            self.assertIs(body.Tip, publication)
            self.assertIs(publication.CurrentState, state)
            self.assertTrue(body.Visibility)
            self.assertTrue(publication.Visibility)
            self.assertFalse(body.Shape.isNull())
            self.assertFalse(publication.Shape.isNull())
            self.assertFalse(generator.Visibility)
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_model_fastener_block_survives_history_storage_and_semantic_delete(
        self,
    ) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        import PartDesign

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
            publication, state, operation, generator = (
                self._design_fastener_graph(body)
            )
            body_name = body.Name
            publication_name = publication.Name
            state_name = state.Name
            operation_name = operation.Name
            generator_name = generator.Name
            body_id = str(body.VibeCADBodyId)
            state_id = str(state.BodyStateId)
            operation_id = str(operation.OperationId)
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            self.assertFalse(body.Shape.isNull())
            PartDesign.validateDesign(operation)

            document.undo()
            self._process_events()
            for name in (
                body_name,
                publication_name,
                state_name,
                operation_name,
                generator_name,
            ):
                self.assertIsNone(document.getObject(name))

            document.redo()
            self._process_events()
            body = document.getObject(body_name)
            self.assertIsNotNone(body)
            publication, state, operation, generator = (
                self._design_fastener_graph(body)
            )
            self.assertEqual(publication.Name, publication_name)
            self.assertEqual(state.Name, state_name)
            self.assertEqual(operation.Name, operation_name)
            self.assertEqual(generator.Name, generator_name)
            self.assertEqual(str(body.VibeCADBodyId), body_id)
            self.assertEqual(str(state.BodyStateId), state_id)
            self.assertEqual(str(operation.OperationId), operation_id)
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            PartDesign.validateDesign(operation)

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
                self.assertIsNotNone(body)
                publication, state, operation, generator = (
                    self._design_fastener_graph(body)
                )
                self.assertEqual(publication.Name, publication_name)
                self.assertEqual(state.Name, state_name)
                self.assertEqual(operation.Name, operation_name)
                self.assertEqual(generator.Name, generator_name)
                self.assertEqual(str(body.VibeCADBodyId), body_id)
                self.assertEqual(str(state.BodyStateId), state_id)
                self.assertEqual(str(operation.OperationId), operation_id)
                self.assertFalse(body.Shape.isNull())
                self._assert_exact_timeline_block(
                    restored_document,
                    operation,
                    (state,),
                )
                PartDesign.validateDesign(operation)

                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(body)
                undo_before_delete = int(restored_document.UndoCount)
                Gui.runCommand("Std_Delete", 0)
                self._process_events()
                for name in (
                    body_name,
                    publication_name,
                    state_name,
                    operation_name,
                    generator_name,
                ):
                    self.assertIsNone(restored_document.getObject(name))
                deleted_timeline_names = {
                    obj.Name
                    for obj in restored_document.getObject(
                        "VibeCADTimeline"
                    ).Operations
                }
                self.assertNotIn(state_name, deleted_timeline_names)
                self.assertNotIn(operation_name, deleted_timeline_names)
                self.assertEqual(
                    int(restored_document.UndoCount),
                    undo_before_delete + 1,
                )

                restored_document.undo()
                self._process_events()
                body = restored_document.getObject(body_name)
                self.assertIsNotNone(body)
                publication, state, operation, generator = (
                    self._design_fastener_graph(body)
                )
                self.assertEqual(publication.Name, publication_name)
                self.assertEqual(state.Name, state_name)
                self.assertEqual(operation.Name, operation_name)
                self.assertEqual(generator.Name, generator_name)
                self.assertEqual(str(body.VibeCADBodyId), body_id)
                self.assertEqual(str(state.BodyStateId), state_id)
                self.assertEqual(str(operation.OperationId), operation_id)
                self._assert_exact_timeline_block(
                    restored_document,
                    operation,
                    (state,),
                )
                PartDesign.validateDesign(operation)

                restored_document.redo()
                self._process_events()
                for name in (
                    body_name,
                    publication_name,
                    state_name,
                    operation_name,
                    generator_name,
                ):
                    self.assertIsNone(restored_document.getObject(name))
                deleted_timeline_names = {
                    obj.Name
                    for obj in restored_document.getObject(
                        "VibeCADTimeline"
                    ).Operations
                }
                self.assertNotIn(state_name, deleted_timeline_names)
                self.assertNotIn(operation_name, deleted_timeline_names)
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
        import PartDesign

        from VibeCADFasteners import fastener_feature_identity

        document = App.newDocument("FastenerRibbonEdit")
        document.UndoMode = True
        try:
            actions = self._ribbon_actions()
            self._trigger_catalog_dialog_action(
                actions["VibeCAD_InsertStandardFastener"],
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
            publication, state, operation, generator = (
                self._design_fastener_graph(body)
            )
            initial = fastener_feature_identity(generator)
            initial_height = float(body.Shape.BoundBox.ZLength)
            names = {
                "body": body.Name,
                "publication": publication.Name,
                "state": state.Name,
                "operation": operation.Name,
                "generator": generator.Name,
            }
            identities = {
                "body": str(body.VibeCADBodyId),
                "state": str(state.BodyStateId),
                "operation": str(operation.OperationId),
            }
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            PartDesign.validateDesign(operation)

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(body)
            self._process_events()
            edit = actions["VibeCAD_EditStandardFastener"]
            self.assertTrue(edit.isEnabled())
            self.assertEqual(
                operation.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                operation.VibeCADTimelineEditCommand,
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

            self.assertIs(document.getObject(names["body"]), body)
            self.assertIs(document.getObject(names["publication"]), publication)
            self.assertIs(document.getObject(names["state"]), state)
            self.assertIs(document.getObject(names["operation"]), operation)
            self.assertIs(document.getObject(names["generator"]), generator)
            current_publication, current_state, current_operation, current_generator = (
                self._design_fastener_graph(body)
            )
            self.assertIs(current_publication, publication)
            self.assertIs(current_state, state)
            self.assertIs(current_operation, operation)
            self.assertIs(current_generator, generator)
            self.assertEqual(str(body.VibeCADBodyId), identities["body"])
            self.assertEqual(str(state.BodyStateId), identities["state"])
            self.assertEqual(
                str(operation.OperationId),
                identities["operation"],
            )
            self.assertEqual(body.Label, "Edited M3 socket bolt")
            self.assertEqual(
                operation.Label,
                "Fastener: Edited M3 socket bolt",
            )
            self.assertEqual(operation.OutputLabel, "Edited M3 socket bolt")
            self.assertEqual(
                generator.Label,
                "Edited M3 socket bolt generator",
            )
            updated = fastener_feature_identity(generator)
            self.assertNotEqual(
                updated["canonical_key"],
                initial["canonical_key"],
            )
            self.assertAlmostEqual(float(updated["length_mm"]), 12.0)
            self.assertFalse(generator.Shape.isNull())
            self.assertTrue(generator.Shape.isValid())
            self.assertEqual(len(generator.Shape.Solids), 1)
            self.assertTrue(generator.isValid(), generator.getStatusString())
            self.assertFalse(body.Shape.isNull())
            self.assertTrue(body.Shape.isValid())
            self.assertEqual(len(body.Shape.Solids), 1)
            self.assertAlmostEqual(
                float(body.Shape.BoundBox.ZLength),
                initial_height + 2.0,
            )
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            PartDesign.validateDesign(operation)
            self.assertEqual(Gui.Selection.getSelection(), [body])
            self.assertEqual(
                int(document.UndoCount),
                undo_before + 1,
            )

            document.undo()
            self._process_events()
            body = document.getObject(names["body"])
            publication, state, operation, generator = (
                self._design_fastener_graph(body)
            )
            restored = fastener_feature_identity(generator)
            self.assertEqual(
                restored["canonical_key"],
                initial["canonical_key"],
            )
            self.assertAlmostEqual(float(restored["length_mm"]), 10.0)
            self.assertEqual(body.Label, "M3 socket bolt")
            self.assertEqual(operation.Label, "Fastener: M3 socket bolt")
            self.assertEqual(operation.OutputLabel, "M3 socket bolt")
            self.assertEqual(generator.Label, "M3 socket bolt generator")
            self.assertTrue(generator.isValid(), generator.getStatusString())
            self.assertAlmostEqual(
                float(body.Shape.BoundBox.ZLength),
                initial_height,
            )
            self.assertEqual(str(body.VibeCADBodyId), identities["body"])
            self.assertEqual(str(state.BodyStateId), identities["state"])
            self.assertEqual(
                str(operation.OperationId),
                identities["operation"],
            )
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            PartDesign.validateDesign(operation)

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
                items, item = self._history_item(operation)
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
                float(fastener_feature_identity(generator)["length_mm"]),
                12.0,
            )
            self.assertEqual(
                operation.Label,
                "Fastener: History-edited M3 socket bolt",
            )
            self.assertEqual(
                generator.Label,
                "History-edited M3 socket bolt generator",
            )
            self.assertTrue(generator.isValid(), generator.getStatusString())
            self.assertAlmostEqual(
                float(body.Shape.BoundBox.ZLength),
                initial_height + 2.0,
            )
            self.assertEqual(str(body.VibeCADBodyId), identities["body"])
            self.assertEqual(str(state.BodyStateId), identities["state"])
            self.assertEqual(
                str(operation.OperationId),
                identities["operation"],
            )
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            PartDesign.validateDesign(operation)
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_edit_promotes_legacy_model_fastener_with_exact_lifecycle(
        self,
    ) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        import PartDesign

        import VibeCADFastenersGui
        from VibeCADFasteners import fastener_feature_identity

        document = App.newDocument("LegacyFastenerRibbonEdit")
        document.UndoMode = True
        restored_document = None
        try:
            actions = self._ribbon_actions()
            body, generator, initial_identity = self._create_standard_fastener(
                document,
                body_name="LegacyEditableFastenerBody",
                standard="ISO4762",
                nominal_thread="M3",
                length_mm=10,
            )
            body.Label = "Legacy M3 socket bolt"
            document.recompute()
            names = {
                "body": body.Name,
                "generator": generator.Name,
            }
            body_id = str(body.VibeCADBodyId)
            initial_volume = float(generator.Shape.Volume)
            legacy_history_names = [
                obj.Name
                for obj in document.getObject("VibeCADTimeline").Operations
            ]
            self.assertIs(
                VibeCADFastenersGui._legacy_model_fastener_body(generator),
                body,
            )

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(body)
            self._process_events()
            edit = actions["VibeCAD_EditStandardFastener"]
            self.assertTrue(edit.isEnabled())
            undo_before = int(document.UndoCount)
            self._trigger_catalog_dialog_action(
                edit,
                lambda driver: self._set_catalog_dialog_values(
                    driver,
                    standard="ISO4762",
                    nominal_thread="M3",
                    length_mm=12,
                    label="Migrated M3 socket bolt",
                ),
            )

            self.assertEqual(int(document.UndoCount), undo_before + 1)
            self.assertIs(document.getObject(names["body"]), body)
            self.assertIs(document.getObject(names["generator"]), generator)
            publication, state, operation, current_generator = (
                self._design_fastener_graph(body)
            )
            self.assertIs(current_generator, generator)
            self.assertEqual(str(body.VibeCADBodyId), body_id)
            self.assertEqual(operation.ResultOperation, "Modify")
            self.assertEqual(len(operation.InputStates), 1)
            initial_state = operation.InputStates[0]
            self.assertIsNone(initial_state.Operation)
            self.assertIs(state.PreviousState, initial_state)
            self.assertEqual(str(initial_state.BodyId), body_id)
            self.assertAlmostEqual(
                float(initial_state.Shape.Volume),
                initial_volume,
            )
            self.assertEqual(body.Label, "Migrated M3 socket bolt")
            self.assertEqual(
                operation.OutputLabel,
                "Migrated M3 socket bolt",
            )
            self.assertAlmostEqual(
                float(fastener_feature_identity(generator)["length_mm"]),
                12.0,
            )
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            PartDesign.validateDesign(operation)
            names.update(
                {
                    "publication": publication.Name,
                    "state": state.Name,
                    "initial_state": initial_state.Name,
                    "operation": operation.Name,
                }
            )
            identities = {
                "state": str(state.BodyStateId),
                "initial_state": str(initial_state.BodyStateId),
                "operation": str(operation.OperationId),
            }

            document.undo()
            self._process_events()
            body = document.getObject(names["body"])
            generator = document.getObject(names["generator"])
            self.assertIsNotNone(body)
            self.assertIsNotNone(generator)
            self.assertIs(body.Tip, generator)
            self.assertEqual(list(body.Group), [generator])
            self.assertEqual(
                [
                    obj.Name
                    for obj in document.getObject(
                        "VibeCADTimeline"
                    ).Operations
                ],
                legacy_history_names,
            )
            self.assertEqual(
                fastener_feature_identity(generator)["canonical_key"],
                initial_identity["canonical_key"],
            )
            for key in ("publication", "state", "initial_state", "operation"):
                self.assertIsNone(document.getObject(names[key]))

            document.redo()
            self._process_events()
            body = document.getObject(names["body"])
            self.assertIsNotNone(body)
            publication, state, operation, generator = (
                self._design_fastener_graph(body)
            )
            initial_state = operation.InputStates[0]
            self.assertEqual(publication.Name, names["publication"])
            self.assertEqual(state.Name, names["state"])
            self.assertEqual(initial_state.Name, names["initial_state"])
            self.assertEqual(operation.Name, names["operation"])
            self.assertEqual(generator.Name, names["generator"])
            self.assertEqual(str(body.VibeCADBodyId), body_id)
            self.assertEqual(str(state.BodyStateId), identities["state"])
            self.assertEqual(
                str(initial_state.BodyStateId),
                identities["initial_state"],
            )
            self.assertEqual(
                str(operation.OperationId),
                identities["operation"],
            )
            PartDesign.validateDesign(operation)

            with tempfile.TemporaryDirectory() as temporary_directory:
                saved_file = (
                    Path(temporary_directory)
                    / "legacy-model-fastener-migration.FCStd"
                )
                document.saveAs(str(saved_file))
                App.closeDocument(document.Name)
                document = None

                restored_document = App.openDocument(str(saved_file))
                restored_document.UndoMode = True
                self._process_events()
                body = restored_document.getObject(names["body"])
                self.assertIsNotNone(body)
                publication, state, operation, generator = (
                    self._design_fastener_graph(body)
                )
                initial_state = operation.InputStates[0]
                self.assertEqual(publication.Name, names["publication"])
                self.assertEqual(state.Name, names["state"])
                self.assertEqual(initial_state.Name, names["initial_state"])
                self.assertEqual(operation.Name, names["operation"])
                self.assertEqual(generator.Name, names["generator"])
                self.assertEqual(str(body.VibeCADBodyId), body_id)
                self.assertEqual(str(state.BodyStateId), identities["state"])
                self.assertEqual(
                    str(initial_state.BodyStateId),
                    identities["initial_state"],
                )
                self.assertEqual(
                    str(operation.OperationId),
                    identities["operation"],
                )
                self.assertIsNone(generator.getParentGeoFeatureGroup())
                self.assertEqual(list(body.Group), [publication])
                self._assert_exact_timeline_block(
                    restored_document,
                    operation,
                    (state,),
                )
                PartDesign.validateDesign(operation)
        finally:
            Gui.Selection.clearSelection()
            if (
                restored_document is not None
                and restored_document.Name in App.listDocuments()
            ):
                App.closeDocument(restored_document.Name)
            if document is not None and document.Name in App.listDocuments():
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
                    Gui.Selection.addSelection(fixture["host_body"])
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
        import PartDesign
        from PySide import QtGui

        from VibeCADFasteners import (
            HOLE_SCHEMA,
            PROP_HOLE_FASTENER_KEY,
            PROP_HOLE_SCHEMA,
        )

        document = App.newDocument("FastenerRibbonHole")
        try:
            actions = self._ribbon_actions()
            fixture = self._create_matching_hole_fixture(document)
            host_body = fixture["host_body"]
            base = fixture["base"]
            sketch = fixture["sketch"]
            identity = fixture["identity"]
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
            self.assertTrue(Gui.Control.activeDialog())

            hole = document.ActiveObject
            self.assertIsNotNone(hole)
            self.assertEqual(hole.TypeId, "PartDesign::DesignHole")
            self.assertIsNone(hole.getParentGeoFeatureGroup())
            profile = hole.Profile
            profile_object = profile[0] if isinstance(profile, tuple) else profile
            self.assertIs(profile_object, sketch)
            self.assertEqual(hole.ResultOperation, "Cut")
            self.assertEqual(
                list(hole.InputBodyIds),
                [str(host_body.VibeCADBodyId)],
            )
            self.assertEqual(getattr(hole, PROP_HOLE_SCHEMA), HOLE_SCHEMA)
            self.assertEqual(
                getattr(hole, PROP_HOLE_FASTENER_KEY),
                identity["canonical_key"],
            )
            self._finish_native_task(accept=True)

            state = host_body.Tip.CurrentState
            self.assertIs(state.Operation, hole)
            self.assertEqual(state.BodyId, host_body.VibeCADBodyId)
            self.assertFalse(host_body.Shape.isNull())
            self.assertTrue(host_body.Shape.isValid())
            self.assertEqual(len(host_body.Shape.Solids), 1)
            self.assertLess(host_body.Shape.Volume, base.Shape.Volume)
            self._assert_exact_timeline_block(
                document,
                hole,
                (state,),
            )
            PartDesign.validateDesign(hole)
        finally:
            Gui.Selection.clearSelection()
            App.closeDocument(document.Name)

    def test_matching_hole_is_one_global_operation_with_full_lifecycle(
        self,
    ) -> None:
        import FreeCAD as App
        import FreeCADGui as Gui
        import PartDesign
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
            prior_state = prior_tip.CurrentState
            prior_state_name = prior_state.Name
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
            self.assertTrue(Gui.Control.activeDialog())

            hole = document.ActiveObject
            self.assertIsNotNone(hole)
            hole_name = hole.Name
            host_body_name = host_body.Name
            base_name = base.Name
            sketch_name = sketch.Name
            fastener_body_name = fastener_body.Name
            fastener_name = fastener.Name
            self.assertEqual(hole.TypeId, "PartDesign::DesignHole")
            self.assertIsNone(hole.getParentGeoFeatureGroup())
            self.assertEqual(
                list(hole.InputBodyIds),
                [str(host_body.VibeCADBodyId)],
            )
            self._finish_native_task(accept=True)

            publication = host_body.Tip
            state = publication.CurrentState
            publication_name = publication.Name
            state_name = state.Name
            operation_id = str(hole.OperationId)
            state_id = str(state.BodyStateId)
            body_id = str(host_body.VibeCADBodyId)
            self.assertEqual(
                publication.TypeId,
                "PartDesign::DesignBodyPublication",
            )
            self.assertIs(state.Operation, hole)
            self.assertIs(state.PreviousState, prior_state)
            self.assertEqual(
                tuple(controller.Operations),
                operations_before + (state, hole),
            )
            self._assert_exact_timeline_block(
                document,
                hole,
                (state,),
            )
            PartDesign.validateDesign(hole)

            document.undo()
            self._process_events()
            host_body = document.getObject(host_body_name)
            self.assertIsNone(document.getObject(hole_name))
            self.assertIsNone(document.getObject(state_name))
            publication = document.getObject(publication_name)
            self.assertIs(host_body.Tip, publication)
            self.assertIs(
                publication.CurrentState,
                document.getObject(prior_state_name),
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
            publication = document.getObject(publication_name)
            state = document.getObject(state_name)
            self.assertIs(host_body.Tip, publication)
            self.assertIs(publication.CurrentState, state)
            self.assertIs(state.Operation, hole)
            self.assertEqual(str(hole.OperationId), operation_id)
            self.assertEqual(str(state.BodyStateId), state_id)
            self.assertEqual(str(host_body.VibeCADBodyId), body_id)
            self._assert_exact_timeline_block(
                document,
                hole,
                (state,),
            )
            PartDesign.validateDesign(hole)

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
                publication = restored_document.getObject(
                    publication_name
                )
                state = restored_document.getObject(state_name)
                sketch = restored_document.getObject(sketch_name)
                fastener_body = restored_document.getObject(
                    fastener_body_name
                )
                fastener = restored_document.getObject(fastener_name)
                for obj in (
                    host_body,
                    hole,
                    base,
                    publication,
                    state,
                    sketch,
                    fastener_body,
                    fastener,
                ):
                    self.assertIsNotNone(obj)
                self.assertIs(host_body.Tip, publication)
                self.assertIs(publication.CurrentState, state)
                self.assertIs(state.Operation, hole)
                profile = hole.Profile
                profile_object = (
                    profile[0] if isinstance(profile, tuple) else profile
                )
                self.assertIs(profile_object, sketch)
                self.assertEqual(str(hole.OperationId), operation_id)
                self.assertEqual(str(state.BodyStateId), state_id)
                self.assertEqual(str(host_body.VibeCADBodyId), body_id)
                self._assert_exact_timeline_block(
                    restored_document,
                    hole,
                    (state,),
                )
                PartDesign.validateDesign(hole)

                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(hole)
                Gui.runCommand("Std_Delete", 0)
                self._process_events()
                self.assertIsNone(restored_document.getObject(hole_name))
                self.assertIsNone(restored_document.getObject(state_name))
                publication = restored_document.getObject(
                    publication_name
                )
                self.assertIs(
                    publication.CurrentState,
                    restored_document.getObject(prior_state_name),
                )
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
                    publication,
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
                publication = restored_document.getObject(
                    publication_name
                )
                state = restored_document.getObject(state_name)
                self.assertIsNotNone(hole)
                self.assertIs(host_body.Tip, publication)
                self.assertIs(publication.CurrentState, state)
                self.assertIs(state.Operation, hole)
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
                    (state,),
                )
                PartDesign.validateDesign(hole)

                restored_document.redo()
                self._process_events()
                self.assertIsNone(restored_document.getObject(hole_name))
                self.assertIsNone(restored_document.getObject(state_name))
                publication = restored_document.getObject(
                    publication_name
                )
                self.assertIs(
                    publication.CurrentState,
                    restored_document.getObject(prior_state_name),
                )
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
        import PartDesign

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

                    self.assertTrue(Gui.Control.activeDialog())
                    hole = document.ActiveObject
                    self.assertIsNotNone(hole)
                    self.assertEqual(
                        hole.TypeId,
                        "PartDesign::DesignHole",
                    )
                    self.assertIsNone(hole.getParentGeoFeatureGroup())
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
                    self._finish_native_task(accept=True)

                    host_body = fixture["host_body"]
                    state = host_body.Tip.CurrentState
                    self.assertIs(state.Operation, hole)
                    self.assertFalse(host_body.Shape.isNull())
                    self.assertTrue(host_body.Shape.isValid())
                    self.assertEqual(len(host_body.Shape.Solids), 1)
                    self.assertLess(
                        host_body.Shape.Volume,
                        fixture["base"].Shape.Volume,
                    )
                    self._assert_exact_timeline_block(
                        document,
                        hole,
                        (state,),
                    )
                    PartDesign.validateDesign(hole)
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
        import PartDesign

        document = App.newDocument("FastenerRibbonAttach")
        document.UndoMode = True
        restored_document = None
        try:
            document.openTransaction("Create attachment host")
            host_operation = document.addObject(
                "PartDesign::DesignCylinder",
                "AttachmentHost",
            )
            host_edit = PartDesign.beginDesignOperationEdit(host_operation)
            host_operation.Radius = 6
            host_operation.Height = 8
            PartDesign.setDesignOperationTargets(
                host_edit,
                "New Body",
                [],
            )
            document.recompute()
            host_body = PartDesign.finalizeDesignOperationEdit(host_edit)[0]
            document.commitTransaction()
            host_publication = host_body.Tip
            host_state = host_publication.CurrentState
            PartDesign.validateDesign(host_operation)

            actions = self._ribbon_actions()
            self._trigger_catalog_dialog_action(
                actions["VibeCAD_InsertStandardFastener"],
                lambda driver: self._set_catalog_dialog_values(
                    driver,
                    standard="ISO4762",
                    nominal_thread="M3",
                    length_mm=10,
                    label="Attached M3 socket bolt",
                ),
            )
            selected = Gui.Selection.getSelection()
            self.assertEqual(len(selected), 1)
            body = selected[0]
            publication, state, operation, fastener = (
                self._design_fastener_graph(body)
            )
            names = {
                "body": body.Name,
                "publication": publication.Name,
                "state": state.Name,
                "operation": operation.Name,
                "generator": fastener.Name,
            }
            identities = {
                "body": str(body.VibeCADBodyId),
                "state": str(state.BodyStateId),
                "operation": str(operation.OperationId),
            }
            initial_center = body.Shape.Solids[0].CenterOfMass
            circular_edge, selected_curve = max(
                (
                    (index, edge.Curve)
                    for index, edge in enumerate(
                        host_body.Shape.Edges,
                        start=1,
                    )
                    if isinstance(edge.Curve, Part.Circle)
                ),
                key=lambda item: float(item[1].Center.z),
            )
            attachment_center = selected_curve.Center
            sub_name = f"Edge{circular_edge}"

            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(body)
            Gui.Selection.addSelection(host_body, sub_name)
            self._process_events()
            action = actions["VibeCAD_AttachStandardFastener"]
            self.assertTrue(action.isEnabled())
            undo_before = int(document.UndoCount)
            with mock.patch(
                "VibeCADFastenersGui._show_error"
            ) as show_error:
                action.trigger()
                self._process_events()
            self.assertFalse(
                show_error.called,
                str(show_error.call_args),
            )

            self.assertIs(document.getObject(names["body"]), body)
            self.assertIs(document.getObject(names["publication"]), publication)
            self.assertIs(document.getObject(names["state"]), state)
            self.assertIs(document.getObject(names["operation"]), operation)
            self.assertIs(document.getObject(names["generator"]), fastener)
            self.assertIsNone(fastener.getParentGeoFeatureGroup())
            self.assertIs(body.Tip, publication)
            self.assertIs(publication.CurrentState, state)
            attachment = fastener.BaseObject
            self.assertIsNotNone(attachment)
            self.assertIs(attachment[0], host_state)
            exact_subelements = list(attachment[1])
            self.assertEqual(len(exact_subelements), 1)
            self.assertFalse(exact_subelements[0].startswith("?"))
            self.assertEqual(
                host_state.Shape.getElementIndexedName(
                    exact_subelements[0]
                ),
                sub_name,
            )
            self.assertFalse(fastener.Shape.isNull())
            self.assertTrue(fastener.Shape.isValid())
            self.assertEqual(len(fastener.Shape.Solids), 1)
            self.assertAlmostEqual(
                fastener.Placement.Base.x,
                attachment_center.x,
            )
            self.assertAlmostEqual(
                fastener.Placement.Base.y,
                attachment_center.y,
            )
            self.assertAlmostEqual(
                fastener.Placement.Base.z,
                attachment_center.z,
            )
            self.assertFalse(body.Shape.isNull())
            self.assertTrue(body.Shape.isValid())
            self.assertEqual(len(body.Shape.Solids), 1)
            self.assertAlmostEqual(
                body.getGlobalPlacement().Base.distanceToPoint(
                    attachment_center
                ),
                0.0,
            )
            attached_center = body.Shape.Solids[0].CenterOfMass
            local_body_shape = body.Shape.copy()
            local_body_shape.Placement = App.Placement()
            self.assertAlmostEqual(
                body.Shape.Volume,
                fastener.Shape.Solids[0].Volume,
            )
            self.assertAlmostEqual(
                attached_center.distanceToPoint(
                    fastener.Shape.Solids[0].CenterOfMass
                ),
                0.0,
            )
            self.assertAlmostEqual(
                local_body_shape.Solids[0].CenterOfMass.distanceToPoint(
                    operation.OutputShapes[0].Solids[0].CenterOfMass
                ),
                0.0,
            )
            self.assertAlmostEqual(
                local_body_shape.Solids[0].CenterOfMass.distanceToPoint(
                    state.Shape.Solids[0].CenterOfMass
                ),
                0.0,
            )
            self.assertAlmostEqual(
                local_body_shape.Solids[0].CenterOfMass.distanceToPoint(
                    publication.Shape.Solids[0].CenterOfMass
                ),
                0.0,
            )
            self.assertEqual(str(body.VibeCADBodyId), identities["body"])
            self.assertEqual(str(state.BodyStateId), identities["state"])
            self.assertEqual(
                str(operation.OperationId),
                identities["operation"],
            )
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            PartDesign.validateDesign(operation)
            self.assertEqual(int(document.UndoCount), undo_before + 1)
            selected = Gui.Selection.getSelectionEx()
            self.assertEqual(
                [item.Object for item in selected],
                [body, host_body],
            )
            self.assertEqual(list(selected[1].SubElementNames), [sub_name])

            document.undo()
            self._process_events()
            body = document.getObject(names["body"])
            publication, state, operation, fastener = (
                self._design_fastener_graph(body)
            )
            self.assertIsNone(fastener.BaseObject)
            self.assertAlmostEqual(fastener.Placement.Base.Length, 0.0)
            self.assertAlmostEqual(
                body.getGlobalPlacement().Base.Length,
                0.0,
            )
            self.assertAlmostEqual(
                body.Shape.Solids[0].CenterOfMass.distanceToPoint(
                    initial_center
                ),
                0.0,
            )
            self.assertEqual(str(body.VibeCADBodyId), identities["body"])
            self.assertEqual(str(state.BodyStateId), identities["state"])
            self.assertEqual(
                str(operation.OperationId),
                identities["operation"],
            )
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            PartDesign.validateDesign(operation)

            document.redo()
            self._process_events()
            body = document.getObject(names["body"])
            publication, state, operation, fastener = (
                self._design_fastener_graph(body)
            )
            attachment = fastener.BaseObject
            self.assertIsNotNone(attachment)
            self.assertIs(attachment[0], host_state)
            self.assertEqual(list(attachment[1]), exact_subelements)
            self.assertAlmostEqual(
                body.getGlobalPlacement().Base.distanceToPoint(
                    attachment_center
                ),
                0.0,
            )
            self.assertAlmostEqual(
                body.Shape.Solids[0].CenterOfMass.distanceToPoint(
                    attached_center
                ),
                0.0,
            )
            self.assertEqual(str(body.VibeCADBodyId), identities["body"])
            self.assertEqual(str(state.BodyStateId), identities["state"])
            self.assertEqual(
                str(operation.OperationId),
                identities["operation"],
            )
            PartDesign.validateDesign(operation)

            with tempfile.TemporaryDirectory() as temporary_directory:
                saved_file = (
                    Path(temporary_directory)
                    / "attached-standard-fastener.FCStd"
                )
                host_body_name = host_body.Name
                host_state_name = host_state.Name
                document.saveAs(str(saved_file))
                App.closeDocument(document.Name)
                document = None

                restored_document = App.openDocument(str(saved_file))
                restored_document.UndoMode = True
                self._process_events()
                body = restored_document.getObject(names["body"])
                host_body = restored_document.getObject(host_body_name)
                host_state = restored_document.getObject(host_state_name)
                self.assertIsNotNone(body)
                self.assertIsNotNone(host_body)
                self.assertIsNotNone(host_state)
                publication, state, operation, fastener = (
                    self._design_fastener_graph(body)
                )
                attachment = fastener.BaseObject
                self.assertIsNotNone(attachment)
                self.assertIs(attachment[0], host_state)
                self.assertEqual(list(attachment[1]), exact_subelements)
                self.assertEqual(
                    host_state.Shape.getElementIndexedName(
                        attachment[1][0]
                    ),
                    sub_name,
                )
                self.assertAlmostEqual(
                    body.getGlobalPlacement().Base.distanceToPoint(
                        attachment_center
                    ),
                    0.0,
                )
                self.assertAlmostEqual(
                    body.Shape.Solids[0].CenterOfMass.distanceToPoint(
                        fastener.Shape.Solids[0].CenterOfMass
                    ),
                    0.0,
                )
                self.assertEqual(
                    str(body.VibeCADBodyId),
                    identities["body"],
                )
                self.assertEqual(
                    str(state.BodyStateId),
                    identities["state"],
                )
                self.assertEqual(
                    str(operation.OperationId),
                    identities["operation"],
                )
                self._assert_exact_timeline_block(
                    restored_document,
                    operation,
                    (state,),
                )
                PartDesign.validateDesign(operation)
        finally:
            Gui.Selection.clearSelection()
            if (
                restored_document is not None
                and restored_document.Name in App.listDocuments()
            ):
                App.closeDocument(restored_document.Name)
            if document is not None and document.Name in App.listDocuments():
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
                self._create_standard_fastener(
                    document,
                    body_name="LegacyStandardFastenerBody",
                    standard="ISO4762",
                    nominal_thread="M3",
                    length_mm=10,
                )
            )
            fastener_body.Label = "Body-edge M3 socket bolt"
            body_name = fastener_body.Name
            generator_name = fastener.Name
            body_id = str(fastener_body.VibeCADBodyId)
            initial_volume = float(fastener.Shape.Volume)
            self.assertIs(fastener_body.Tip, fastener)
            self.assertEqual(list(fastener_body.Group), [fastener])
            self.assertIs(
                VibeCADFastenersGui._legacy_model_fastener_body(fastener),
                fastener_body,
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
            Gui.Selection.addSelection(host_body, sub_name)
            self._process_events()
            action = actions["VibeCAD_AttachStandardFastener"]
            self.assertTrue(action.isEnabled())
            with mock.patch(
                "VibeCADFastenersGui._show_error"
            ) as show_error:
                action.trigger()
                self._process_events()
            self.assertFalse(
                show_error.called,
                str(show_error.call_args),
            )

            migrated_body = document.getObject(body_name)
            self.assertIs(migrated_body, fastener_body)
            publication, state, operation, migrated_generator = (
                self._design_fastener_graph(migrated_body)
            )
            self.assertIs(migrated_generator, fastener)
            self.assertEqual(migrated_generator.Name, generator_name)
            self.assertEqual(str(migrated_body.VibeCADBodyId), body_id)
            self.assertEqual(operation.ResultOperation, "Modify")
            self.assertEqual(len(operation.InputStates), 1)
            initial_state = operation.InputStates[0]
            self.assertIsNone(initial_state.Operation)
            self.assertIs(state.PreviousState, initial_state)
            self.assertEqual(str(initial_state.BodyId), body_id)
            self.assertAlmostEqual(
                float(initial_state.Shape.Volume),
                initial_volume,
            )
            self.assertEqual(list(migrated_body.Group), [publication])
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )
            self.assertIsNone(fastener.getParentGeoFeatureGroup())
            self.assertIs(fastener_body.Tip, publication)
            self.assertIs(publication.CurrentState, state)
            self.assertIs(state.Operation, operation)
            self.assertIs(host.getParentGeoFeatureGroup(), host_body)
            self.assertIs(host_body.Tip, host)
            attachment = fastener.BaseObject
            self.assertIsNotNone(attachment)
            self.assertIs(attachment[0], host)
            self.assertEqual(
                host.Shape.getElementIndexedName(attachment[1][0]),
                sub_name,
            )
            self.assertFalse(fastener.Shape.isNull())
            self.assertTrue(fastener.Shape.isValid())
            self.assertEqual(len(fastener.Shape.Solids), 1)

            publication_name = publication.Name
            state_name = state.Name
            initial_state_name = initial_state.Name
            operation_name = operation.Name
            state_id = str(state.BodyStateId)
            initial_state_id = str(initial_state.BodyStateId)
            operation_id = str(operation.OperationId)
            document.undo()
            self._process_events()
            legacy_body = document.getObject(body_name)
            legacy_generator = document.getObject(generator_name)
            self.assertIsNotNone(legacy_body)
            self.assertIsNotNone(legacy_generator)
            self.assertIs(legacy_body.Tip, legacy_generator)
            self.assertEqual(list(legacy_body.Group), [legacy_generator])
            self.assertIs(
                VibeCADFastenersGui._legacy_model_fastener_body(
                    legacy_generator
                ),
                legacy_body,
            )
            self.assertIsNone(legacy_generator.BaseObject)
            for name in (
                publication_name,
                state_name,
                initial_state_name,
                operation_name,
            ):
                self.assertIsNone(document.getObject(name))

            document.redo()
            self._process_events()
            fastener_body = document.getObject(body_name)
            self.assertIsNotNone(fastener_body)
            publication, state, operation, fastener = (
                self._design_fastener_graph(fastener_body)
            )
            self.assertEqual(publication.Name, publication_name)
            self.assertEqual(state.Name, state_name)
            self.assertEqual(operation.Name, operation_name)
            self.assertEqual(fastener.Name, generator_name)
            self.assertEqual(str(fastener_body.VibeCADBodyId), body_id)
            self.assertEqual(str(state.BodyStateId), state_id)
            self.assertEqual(str(operation.OperationId), operation_id)
            self.assertEqual(len(operation.InputStates), 1)
            initial_state = operation.InputStates[0]
            self.assertEqual(initial_state.Name, initial_state_name)
            self.assertEqual(str(initial_state.BodyStateId), initial_state_id)
            self.assertIsNone(initial_state.Operation)
            self.assertIs(state.PreviousState, initial_state)
            attachment = fastener.BaseObject
            self.assertIsNotNone(attachment)
            self.assertIs(attachment[0], host)
            self.assertEqual(
                host.Shape.getElementIndexedName(attachment[1][0]),
                sub_name,
            )
            self._assert_exact_timeline_block(
                document,
                operation,
                (state,),
            )

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
            Gui.Selection.addSelection(host_body, sub_name)
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
