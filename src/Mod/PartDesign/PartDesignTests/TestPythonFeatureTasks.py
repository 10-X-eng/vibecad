# SPDX-License-Identifier: LGPL-2.1-or-later

"""Semantic-history contracts for Python-backed native Model tools."""

from pathlib import Path
import shutil
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import PartDesign
from PySide import QtCore, QtGui


class TestPythonFeatureTasks(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("PythonFeatureTasks")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self._process_events()

    def tearDown(self):
        if Gui.Control.activeDialog():
            cancel = self._task_button(QtGui.QDialogButtonBox.Cancel)
            if cancel is not None:
                cancel.click()
                self._process_events()
            else:
                Gui.Control.closeDialog()
        Gui.Selection.clearSelection()
        if App.getDocument("PythonFeatureTasks") is not None:
            App.closeDocument("PythonFeatureTasks")
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

    def _wait_until(self, predicate, attempts=100):
        for _attempt in range(attempts):
            value = predicate()
            if value:
                return value
            self._process_events(10)
        return predicate()

    def _task_button(self, standard_button):
        self._process_events()
        for box in Gui.getMainWindow().findChildren(
            QtGui.QDialogButtonBox,
        ):
            if not box.isVisible():
                continue
            button = box.button(standard_button)
            if (
                button is not None
                and button.isVisible()
                and button.isEnabled()
            ):
                return button
        return None

    def _finish_task(self, standard_button):
        button = self._task_button(standard_button)
        self.assertIsNotNone(button)
        button.click()
        self.assertTrue(
            self._wait_until(
                lambda: not Gui.Control.activeDialog()
                and self.document.getBookedTransactionID() == 0
                and not self.document.HasPendingTransaction
            )
        )

    @staticmethod
    def _accept_next_message():
        attempts = [0]

        def accept():
            attempts[0] += 1
            for widget in QtGui.QApplication.topLevelWidgets():
                if (
                    isinstance(widget, QtGui.QMessageBox)
                    and widget.isVisible()
                ):
                    yes = widget.button(QtGui.QMessageBox.Yes)
                    if yes is not None:
                        yes.click()
                    else:
                        widget.accept()
                    return
            if attempts[0] < 100:
                QtCore.QTimer.singleShot(10, accept)

        QtCore.QTimer.singleShot(0, accept)

    def _proxy_object(self, proxy_type):
        matches = [
            obj
            for obj in self.document.Objects
            if getattr(getattr(obj, "Proxy", None), "Type", "")
            == proxy_type
        ]
        self.assertEqual(
            len(matches),
            1,
            [(obj.Name, obj.TypeId) for obj in matches],
        )
        return matches[0]

    def _timeline_item(self, object_name):
        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline)

        def find_item():
            for row in range(timeline.count()):
                item = timeline.item(row)
                if item.data(QtCore.Qt.UserRole) == object_name:
                    return item
            return None

        item = self._wait_until(find_item)
        self.assertIsNotNone(item)
        return timeline, item

    def _assert_operation(self, operation):
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertEqual(
            operation.getTypeIdOfProperty("VibeCADTimelineRole"),
            "App::PropertyString",
        )
        self.assertTrue(
            {"Hidden", "LockDynamic", "NoRecompute"}.issubset(
                operation.getPropertyStatus("VibeCADTimelineRole")
            )
        )
        self.assertIn(
            "Hidden",
            operation.getEditorMode("VibeCADTimelineRole"),
        )
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            operation.PropertiesList,
        )
        if "VibeCADTimelineOwner" in operation.PropertiesList:
            self.assertIsNone(operation.VibeCADTimelineOwner)

    def _save_reopen(self, operation, resources=()):
        operation_name = operation.Name
        resource_names = [resource.Name for resource in resources]
        with tempfile.TemporaryDirectory() as temporary_directory:
            saved = Path(temporary_directory) / "python_feature.FCStd"
            reopened = (
                Path(temporary_directory)
                / "python_feature_reopened.FCStd"
            )
            self.document.saveAs(str(saved))
            shutil.copy2(saved, reopened)
            restored_document = App.openDocument(str(reopened), True)
            try:
                restored_operation = restored_document.getObject(
                    operation_name
                )
                self.assertIsNotNone(restored_operation)
                self._assert_operation(restored_operation)
                for resource_name in resource_names:
                    restored_resource = restored_document.getObject(
                        resource_name
                    )
                    self.assertIsNotNone(restored_resource)
                    self.assertEqual(
                        restored_resource.VibeCADTimelineRole,
                        "resource",
                    )
                    self.assertIs(
                        restored_resource.VibeCADTimelineOwner,
                        restored_operation,
                    )
                    self.assertNotIn(
                        "VibeCADTimelineReplacedInputs",
                        restored_resource.PropertiesList,
                    )
            finally:
                App.closeDocument(restored_document.Name)
                App.setActiveDocument(self.document.Name)
                self._process_events()

    def _visible_teeth_control(self):
        return next(
            (
                widget
                for widget in Gui.getMainWindow().findChildren(
                    QtGui.QSpinBox,
                    "spinBox_NumberOfTeeth",
                )
                if widget.isVisible()
            ),
            None,
        )

    def _exercise_profile_feature(self, command_name, proxy_type):
        original_objects = tuple(self.document.Objects)
        original_undo_count = int(self.document.UndoCount)

        Gui.Selection.clearSelection()
        self.assertTrue(Gui.isCommandActive(command_name))
        Gui.runCommand(command_name, 0)
        started = self._wait_until(
            lambda: Gui.Control.activeDialog()
            and self.document.getBookedTransactionID() != 0
        )
        self.assertTrue(
            started,
            (
                f"dialog={bool(Gui.Control.activeDialog())}; "
                f"transaction={self.document.getBookedTransactionID()}; "
                f"pending={self.document.HasPendingTransaction}; "
                f"objects={[obj.Name for obj in self.document.Objects]}"
            ),
        )
        cancelled_transaction = int(
            self.document.getBookedTransactionID()
        )
        self._finish_task(QtGui.QDialogButtonBox.Cancel)
        self.assertNotEqual(cancelled_transaction, 0)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertEqual(
            int(self.document.UndoCount),
            original_undo_count,
        )

        Gui.runCommand(command_name, 0)
        started = self._wait_until(
            lambda: Gui.Control.activeDialog()
            and self.document.getBookedTransactionID() != 0
        )
        self.assertTrue(
            started,
            (
                f"dialog={bool(Gui.Control.activeDialog())}; "
                f"transaction={self.document.getBookedTransactionID()}; "
                f"pending={self.document.HasPendingTransaction}; "
                f"objects={[obj.Name for obj in self.document.Objects]}"
            ),
        )
        transaction_id = int(self.document.getBookedTransactionID())
        operation = self._proxy_object(proxy_type)
        parameter = self._visible_teeth_control()
        self.assertIsNotNone(parameter)
        parameter.setValue(parameter.value() + 1)
        self._process_events()
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            transaction_id,
        )
        operation_name = operation.Name
        accepted_teeth = parameter.value()
        self._finish_task(QtGui.QDialogButtonBox.Ok)

        operation = self.document.getObject(operation_name)
        self.assertIsNotNone(operation)
        self._assert_operation(operation)
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertFalse(operation.Shape.isNull())
        self.assertTrue(operation.Shape.isValid())
        self.assertEqual(operation.NumberOfTeeth, accepted_teeth)
        self.assertEqual(
            int(self.document.UndoCount),
            original_undo_count + 1,
        )
        timeline, _item = self._timeline_item(operation.Name)
        visible_names = {
            timeline.item(row).data(QtCore.Qt.UserRole)
            for row in range(timeline.count())
        }
        self.assertIn(operation.Name, visible_names)
        self.assertTrue(
            operation.ViewObject.Proxy.supportsDocumentTimelineEdit()
        )
        self._save_reopen(operation)

        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(operation_name))
        self.document.redo()
        self._process_events()
        operation = self.document.getObject(operation_name)
        self.assertIsNotNone(operation)
        self._assert_operation(operation)

        timeline, item = self._timeline_item(operation.Name)
        original_teeth = operation.NumberOfTeeth
        editor_undo_count = int(self.document.UndoCount)
        timeline.itemDoubleClicked.emit(item)
        self.assertTrue(
            self._wait_until(
                lambda: Gui.Control.activeDialog()
                and Gui.activeDocument().getInEdit() is not None
                and Gui.activeDocument().getInEdit().Object is operation
                and self.document.getBookedTransactionID() != 0
            )
        )
        editor_transaction = int(
            self.document.getBookedTransactionID()
        )
        parameter = self._visible_teeth_control()
        self.assertIsNotNone(parameter)
        parameter.setValue(original_teeth + 3)
        self._process_events()
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            editor_transaction,
        )
        self._finish_task(QtGui.QDialogButtonBox.Cancel)
        operation = self.document.getObject(operation_name)
        self.assertEqual(operation.NumberOfTeeth, original_teeth)
        self.assertEqual(int(self.document.UndoCount), editor_undo_count)

        timeline, item = self._timeline_item(operation.Name)
        timeline.itemDoubleClicked.emit(item)
        self.assertTrue(
            self._wait_until(
                lambda: Gui.Control.activeDialog()
                and Gui.activeDocument().getInEdit() is not None
                and Gui.activeDocument().getInEdit().Object is operation
            )
        )
        parameter = self._visible_teeth_control()
        self.assertIsNotNone(parameter)
        parameter.setValue(original_teeth + 2)
        self._finish_task(QtGui.QDialogButtonBox.Ok)
        operation = self.document.getObject(operation_name)
        self.assertEqual(operation.NumberOfTeeth, original_teeth + 2)
        self.assertEqual(
            int(self.document.UndoCount),
            editor_undo_count + 1,
        )
        self.document.undo()
        self._process_events()
        self.assertEqual(
            self.document.getObject(operation_name).NumberOfTeeth,
            original_teeth,
        )

    def test_involute_gear_is_one_editable_persistent_operation(self):
        self._exercise_profile_feature(
            "PartDesign_InvoluteGear",
            "InvoluteGear",
        )

    def test_sprocket_is_one_editable_persistent_operation(self):
        self._exercise_profile_feature(
            "PartDesign_Sprocket",
            "Sprocket",
        )

    def test_shaft_is_one_global_operation_with_reusable_profile(self):
        original_objects = tuple(self.document.Objects)
        original_undo_count = int(self.document.UndoCount)

        Gui.Selection.clearSelection()
        self.assertTrue(Gui.isCommandActive("PartDesign_WizardShaft"))
        Gui.runCommand("PartDesign_WizardShaft", 0)
        started = self._wait_until(
            lambda: Gui.Control.activeDialog()
            and self.document.getBookedTransactionID() != 0
        )
        self.assertTrue(
            started,
            (
                f"dialog={bool(Gui.Control.activeDialog())}; "
                f"transaction={self.document.getBookedTransactionID()}; "
                f"pending={self.document.HasPendingTransaction}; "
                f"objects={[obj.Name for obj in self.document.Objects]}"
            ),
        )
        cancelled_transaction = int(
            self.document.getBookedTransactionID()
        )
        self._finish_task(QtGui.QDialogButtonBox.Cancel)
        self.assertNotEqual(cancelled_transaction, 0)
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertEqual(
            int(self.document.UndoCount),
            original_undo_count,
        )

        Gui.runCommand("PartDesign_WizardShaft", 0)
        started = self._wait_until(
            lambda: Gui.Control.activeDialog()
            and self.document.getBookedTransactionID() != 0
        )
        self.assertTrue(
            started,
            (
                f"dialog={bool(Gui.Control.activeDialog())}; "
                f"transaction={self.document.getBookedTransactionID()}; "
                f"pending={self.document.HasPendingTransaction}; "
                f"objects={[obj.Name for obj in self.document.Objects]}"
            ),
        )
        transaction_id = int(self.document.getBookedTransactionID())
        operation = self.document.getObject("RevolutionShaft")
        profile = self.document.getObject("SketchShaft")
        output_bodies = [
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::Body"
        ]
        self.assertIsNotNone(operation)
        self.assertIsNotNone(profile)
        self.assertEqual(operation.TypeId, "PartDesign::DesignRevolve")
        self.assertEqual(len(output_bodies), 1)
        self.assertEqual(
            int(self.document.getBookedTransactionID()),
            transaction_id,
        )
        self._finish_task(QtGui.QDialogButtonBox.Ok)

        operation = self.document.getObject("RevolutionShaft")
        profile = self.document.getObject("SketchShaft")
        body = next(
            obj
            for obj in self.document.Objects
            if obj.TypeId == "PartDesign::Body"
        )
        self._assert_operation(operation)
        self._assert_operation(profile)
        self.assertEqual(operation.TypeId, "PartDesign::DesignRevolve")
        self.assertIsNone(operation.getParentGeoFeatureGroup())
        self.assertIsNone(profile.getParentGeoFeatureGroup())
        self.assertIn("VibeCADDefinitionId", profile.PropertiesList)
        self.assertEqual(str(profile.DesignId), str(operation.DesignId))
        self.assertNotIn(
            "VibeCADTimelineOwner",
            profile.PropertiesList,
        )
        linked_profile, profile_subelements = operation.Profile
        self.assertIs(linked_profile, profile)
        self.assertEqual(tuple(profile_subelements), ())
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self.assertTrue(operation.Shape.isNull())
        self.assertEqual(operation.ResultOperation, "New Body")
        self.assertEqual(operation.InputStates, [])
        self.assertEqual(operation.OutputPreviousInputIndices, [-1])
        self.assertEqual(operation.OutputPresence, (True,))
        self.assertEqual(operation.OutputBodyIds, [body.VibeCADBodyId])
        self.assertIsNotNone(body.Tip)
        self.assertIsNotNone(body.Tip.CurrentState)
        self.assertIs(body.Tip.CurrentState.Operation, operation)
        self.assertFalse(body.Shape.isNull())
        self.assertTrue(body.Shape.isValid())
        self.assertEqual(
            int(self.document.UndoCount),
            original_undo_count + 1,
        )
        self._timeline_item(operation.Name)
        timeline, _item = self._timeline_item(profile.Name)
        visible_names = {
            timeline.item(row).data(QtCore.Qt.UserRole)
            for row in range(timeline.count())
        }
        self.assertIn(operation.Name, visible_names)
        self.assertIn(profile.Name, visible_names)

        operation_name = operation.Name
        profile_name = profile.Name
        body_name = body.Name
        publication_name = body.Tip.Name
        state_name = body.Tip.CurrentState.Name
        with tempfile.TemporaryDirectory() as temporary_directory:
            saved = Path(temporary_directory) / "shaft.FCStd"
            reopened = Path(temporary_directory) / "shaft_reopened.FCStd"
            self.document.saveAs(str(saved))
            shutil.copy2(saved, reopened)
            restored_document = App.openDocument(str(reopened), True)
            try:
                restored_operation = restored_document.getObject(operation_name)
                restored_profile = restored_document.getObject(profile_name)
                restored_body = restored_document.getObject(body_name)
                restored_publication = restored_document.getObject(
                    publication_name
                )
                restored_state = restored_document.getObject(state_name)
                self.assertIsNotNone(restored_operation)
                self.assertIsNotNone(restored_profile)
                self.assertIsNotNone(restored_body)
                self.assertIsNotNone(restored_publication)
                self.assertIsNotNone(restored_state)
                self._assert_operation(restored_operation)
                self._assert_operation(restored_profile)
                self.assertIs(restored_body.Tip, restored_publication)
                self.assertIs(
                    restored_publication.CurrentState,
                    restored_state,
                )
                self.assertIs(restored_state.Operation, restored_operation)
                self.assertTrue(restored_body.Shape.isValid())
                PartDesign.validateDesign(restored_operation)
            finally:
                App.closeDocument(restored_document.Name)
                App.setActiveDocument(self.document.Name)
                self._process_events()

        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(operation_name))
        self.assertIsNone(self.document.getObject(profile_name))
        self.document.redo()
        self._process_events()
        operation = self.document.getObject(operation_name)
        profile = self.document.getObject(profile_name)
        body = self.document.getObject(body_name)
        self.assertIsNotNone(operation)
        self.assertIsNotNone(profile)
        self.assertIsNotNone(body)
        self._assert_operation(operation)
        self._assert_operation(profile)
        self.assertIs(body.Tip.CurrentState.Operation, operation)
        PartDesign.validateDesign(operation)

        constraint_names = [
            obj.Name
            for obj in self.document.Objects
            if obj.Name.startswith("ShaftConstraint")
        ]
        self.assertEqual(len(constraint_names), 2)
        delete_undo_count = int(self.document.UndoCount)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        self._accept_next_message()
        Gui.runCommand("Std_Delete", 0)
        self._process_events()
        self.assertIsNotNone(
            self.document.getObject(operation_name),
            "a Body-producing operation with live analysis consumers must "
            "refuse destructive deletion",
        )
        self.assertIsNotNone(self.document.getObject(body_name))
        self.assertEqual(int(self.document.UndoCount), delete_undo_count)

        Gui.Selection.clearSelection()
        for constraint_name in constraint_names:
            Gui.Selection.addSelection(
                self.document.getObject(constraint_name)
            )
        Gui.runCommand("Std_Delete", 0)
        self._process_events()
        for constraint_name in constraint_names:
            self.assertIsNone(self.document.getObject(constraint_name))
        self.assertEqual(
            int(self.document.UndoCount),
            delete_undo_count + 1,
        )

        operation = self.document.getObject(operation_name)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(operation)
        Gui.runCommand("Std_Delete", 0)
        self._process_events()
        self.assertIsNone(self.document.getObject(operation_name))
        self.assertIsNotNone(
            self.document.getObject(profile_name),
            "deleting a consuming operation must retain its reusable sketch",
        )
        self.assertIsNone(self.document.getObject(body_name))
        self.assertEqual(
            int(self.document.UndoCount),
            delete_undo_count + 2,
        )
        self.document.undo()
        self._process_events()
        restored_operation = self.document.getObject(operation_name)
        restored_profile = self.document.getObject(profile_name)
        self.assertIsNotNone(restored_operation)
        self.assertIsNotNone(restored_profile)
        restored_body = self.document.getObject(body_name)
        self.assertIsNotNone(restored_body)
        self._assert_operation(restored_operation)
        self._assert_operation(restored_profile)
        self.assertIs(
            restored_body.Tip.CurrentState.Operation,
            restored_operation,
        )
        PartDesign.validateDesign(restored_operation)

        self.document.undo()
        self._process_events()
        for constraint_name in constraint_names:
            self.assertIsNotNone(self.document.getObject(constraint_name))
        PartDesign.validateDesign(restored_operation)

    def test_python_feature_commands_refuse_caller_transactions(self):
        for command_name in (
            "PartDesign_InvoluteGear",
            "PartDesign_Sprocket",
            "PartDesign_WizardShaft",
        ):
            with self.subTest(command_name=command_name):
                before = tuple(self.document.Objects)
                undo_count = int(self.document.UndoCount)
                self.document.openTransaction(
                    f"Caller owns {command_name}"
                )
                caller_transaction = int(
                    self.document.getBookedTransactionID()
                )
                self.assertNotEqual(caller_transaction, 0)
                self.assertFalse(Gui.isCommandActive(command_name))
                Gui.runCommand(command_name, 0)
                self._process_events()
                self.assertEqual(
                    int(self.document.getBookedTransactionID()),
                    caller_transaction,
                )
                self.assertEqual(tuple(self.document.Objects), before)
                self.assertFalse(Gui.Control.activeDialog())
                App.closeActiveTransaction(True, caller_transaction)
                self._process_events()
                self.assertEqual(
                    int(self.document.UndoCount),
                    undo_count,
                )


if __name__ == "__main__":
    unittest.main()
