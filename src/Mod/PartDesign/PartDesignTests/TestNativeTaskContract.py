# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD contracts for native modal-task ownership and rollback.

These are deliberately user-facing contracts rather than inherited FreeCAD
implementation tests.  A native task may borrow document and GUI state while
it is open, but Cancel must restore that state exactly and Accept is the only
path allowed to publish a replayable macro.
"""

from pathlib import Path
import os
import tempfile
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign  # noqa: F401 - registers native Part Design types
import Sketcher
from PySide import QtCore, QtGui


def _source_checkout_root():
    return next(
        (
            candidate
            for candidate in Path(__file__).resolve().parents
            if (candidate / "CMakeLists.txt").is_file()
            and (candidate / "src/Mod").is_dir()
        ),
        None,
    )


class TestNativeTaskSourceContract(unittest.TestCase):
    """Keep common task checkpoints out of the exported class ABI."""

    def test_task_dialog_checkpoint_state_is_out_of_the_abi_layout(self):
        """Checkpoint storage must not enlarge every downstream TaskDialog."""

        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("TaskDialog source checkout is unavailable")

        header = (
            source_root / "src/Gui/TaskView/TaskDialog.h"
        ).read_text(encoding="utf-8")
        implementation = (
            source_root / "src/Gui/TaskView/TaskDialog.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn(
            "static std::map<TaskDialog*, DialogState>& dialogStates();",
            header,
        )
        self.assertIn(
            "static auto* states = new std::map<TaskDialog*, DialogState>;",
            implementation,
        )

        instance_layout_end = header.index("friend class TaskDialogAttorney;")
        instance_layout_start = header.rindex(
            "    std::string documentName;",
            0,
            instance_layout_end,
        )
        instance_layout = header[
            instance_layout_start:instance_layout_end
        ]
        for forbidden in (
            "InteractionState",
            "DialogState",
            "MacroCapture",
            "std::optional",
            "std::shared_ptr",
            "commandInvocation",
            "checkpoint",
        ):
            self.assertNotIn(
                forbidden,
                instance_layout,
                f"{forbidden} became per-instance TaskDialog ABI state",
            )

    def test_edit_transaction_owner_is_document_pimpl_state(self):
        """Edit teardown must close only the transaction explicitly adopted."""

        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("Gui::Document source checkout is unavailable")

        header = (source_root / "src/Gui/Document.h").read_text(
            encoding="utf-8"
        )
        document = (source_root / "src/Gui/Document.cpp").read_text(
            encoding="utf-8"
        )
        task_dialog = (
            source_root / "src/Gui/TaskView/TaskDialog.cpp"
        ).read_text(encoding="utf-8")

        private_layout = header[
            header.index("private:\n    bool trySetEdit"):
        ]
        self.assertNotIn("_editTransactionId", private_layout)
        public_edit_surface = header[
            header.index("bool setEdit("):
            header.index("private:\n    bool trySetEdit")
        ]
        self.assertNotIn(
            "adoptEditTransaction",
            public_edit_surface,
            "Edit transaction transfer became public API",
        )
        self.assertIn("int _editTransactionId;", document)
        self.assertIn("bool _editTransactionLocked;", document)
        self.assertIn(
            "bool Document::adoptEditTransaction(int transactionId)",
            document,
        )
        adopt_body = document[
            document.index(
                "bool Document::adoptEditTransaction(int transactionId)"
            ):
            document.index(
                "void Document::resetIfEditing()"
            )
        ]
        self.assertEqual(
            1,
            adopt_body.count("d->_editTransactionId = transactionId;"),
        )
        self.assertLess(
            adopt_body.index("getDocument()->lockTransaction();"),
            adopt_body.index("d->_editTransactionId = transactionId;"),
        )
        self.assertLess(
            adopt_body.index("return false;"),
            adopt_body.index("d->_editTransactionId = transactionId;"),
            "Failed transfer can write edit transaction ownership",
        )
        self.assertIn(
            "getDocument()->getBookedTransactionID()\n"
            "                == editTransactionId",
            document,
        )
        self.assertIn(
            "guiDocument->adoptEditTransaction(",
            task_dialog,
        )
        standalone_adoption = task_dialog[
            task_dialog.index("void TaskDialog::adoptCommandInteractionState"):
            task_dialog.index(
                "bool TaskDialog::restoreCommandInteractionState",
            )
        ]
        self.assertIn(
            "else if (!guiDocument->getEditViewProvider()",
            standalone_adoption,
        )
        self.assertIn(
            "&& !document->isTransactionLocked())",
            standalone_adoption,
        )
        self.assertIn(
            "lockStandaloneTransaction();",
            standalone_adoption,
        )
        reset_body = document[
            document.index("void Document::_resetEdit()"):
            document.index(
                "ViewProvider* Document::getInEdit(",
                document.index("void Document::_resetEdit()"),
            )
        ]
        self.assertLess(
            reset_body.index("releaseEditTransactionLock();"),
            reset_body.index(
                "App::GetApplication().abortTransaction(editTransactionId)"
            ),
        )

        app_document = (
            source_root / "src/App/Document.cpp"
        ).read_text(encoding="utf-8")
        set_active = app_document[
            app_document.index(
                "int Document::setActiveTransaction(TransactionName name"
            ):
            app_document.index(
                "void Document::lockTransaction()",
                app_document.index(
                    "int Document::setActiveTransaction(TransactionName name"
                ),
            )
        ]
        self.assertIn("if (isTransactionLocked()", set_active)

    def test_gui_cancel_defers_exact_edit_rollback_until_teardown(self):
        """TaskView Cancel marks its adopted edit; App abort stays external."""

        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("Gui::Document source checkout is unavailable")

        implementation = (
            source_root / "src/Gui/Document.cpp"
        ).read_text(encoding="utf-8")
        header = (
            source_root / "src/Gui/Document.h"
        ).read_text(encoding="utf-8")
        task_view = (
            source_root / "src/Gui/TaskView/TaskView.cpp"
        ).read_text(encoding="utf-8")

        self.assertIn("int prepareCancelEdit();", header)
        self.assertIn("void clearCancelEdit(int transactionId);", header)
        prepare_cancel = implementation[
            implementation.index("int Document::prepareCancelEdit()"):
            implementation.index(
                "void Document::clearCancelEdit(int transactionId)"
            )
        ]
        self.assertIn("d->_editTransactionLocked", prepare_cancel)
        self.assertIn(
            "getDocument()->getBookedTransactionID()\n"
            "            != d->_editTransactionId",
            prepare_cancel,
        )
        self.assertIn("d->_abortEditTransaction = true;", prepare_cancel)

        reject = task_view[
            task_view.index("void TaskView::reject(App::Document* doc)"):
            task_view.index(
                "void TaskView::helpRequested(App::Document* doc)"
            )
        ]
        prepare_mark = reject.index("guiDocument->prepareCancelEdit()")
        task_reject = reject.index(
            "success = foundTaskInfo->ActiveDialog->reject();"
        )
        self.assertLess(prepare_mark, task_reject)
        self.assertGreaterEqual(
            reject.count("clearUnconsumedEditCancel();"),
            2,
            "Throwing or declined rejects must not poison a later OK",
        )

        abort_command = implementation[
            implementation.index("void Document::abortCommand()"):
            implementation.index(
                "bool Document::hasPendingCommand() const",
                implementation.index("void Document::abortCommand()"),
            )
        ]
        self.assertNotIn("prepareCancelEdit()", abort_command)
        self.assertNotIn("_abortEditTransaction", abort_command)
        self.assertIn("getDocument()->abortTransaction();", abort_command)

    def test_synchronous_command_closes_only_its_captured_transaction(self):
        """Undo-disabled commands must never use a bare document close."""

        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("TaskDialog source checkout is unavailable")

        implementation = (
            source_root / "src/Gui/TaskView/TaskDialog.cpp"
        ).read_text(encoding="utf-8")
        end_invocation = implementation[
            implementation.index(
                "void TaskDialog::endCommandInvocation(bool commandSucceeded)"
            ):
            implementation.index(
                "bool TaskDialog::hasOwnedEnclosingTransaction",
            )
        ]
        capture = end_invocation.index(
            "state.commandTransactionId = currentTransactionId;"
        )
        failure = end_invocation.index(
            "if (outermost && !state.taskAdopted && !commandSucceeded)"
        )
        self.assertLess(capture, failure)

        finish = implementation[
            implementation.index(
                "bool TaskDialog::finishCommandTransaction("
            ):
            implementation.index(
                "void TaskDialog::restoreOriginalUndoMode",
            )
        ]
        self.assertNotIn("document->commitTransaction();", finish)
        self.assertNotIn("document->abortTransaction();", finish)
        self.assertIn(
            "App::GetApplication().commitTransaction(",
            finish,
        )
        self.assertIn(
            "App::GetApplication().abortTransaction(",
            finish,
        )

    def test_tree_deletion_purges_parent_child_caches_before_dereference(self):
        """Rollback deletion must remove raw child identities synchronously."""

        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("Tree source checkout is unavailable")

        implementation = (
            source_root / "src/Gui/Tree.cpp"
        ).read_text(encoding="utf-8")
        delete_start = implementation.index(
            "void TreeWidget::_slotDeleteObject("
        )
        delete_body = implementation[
            delete_start:
            implementation.index(
                "\nbool DocumentItem::populateObject(",
                delete_start,
            )
        ]
        purge = delete_body.index(
            "parentDataIt->second->childSet.erase(obj);"
        )
        erase_table = delete_body.index(
            "ObjectTable.erase(itEntry);",
            purge,
        )
        self.assertLess(purge, erase_table)
        child_loop = delete_body[
            delete_body.index("for (auto child : data->children)"):
        ]
        membership = child_loop.index("ObjectTable.contains(child)")
        dereference = child_loop.index("child->getDocument()")
        self.assertLess(membership, dereference)

    def test_direct_default_edit_refuses_a_caller_booked_transaction(self):
        """The context-menu edit entry must use the common ownership gate."""

        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("ViewProvider source checkout is unavailable")

        implementation = (
            source_root / "src/Gui/ViewProviderDocumentObject.cpp"
        ).read_text(encoding="utf-8")
        start = implementation.index(
            "void ViewProviderDocumentObject::startDefaultEditMode()"
        )
        end = implementation.index(
            "void ViewProviderDocumentObject::addDefaultAction",
            start,
        )
        entry = implementation[start:end]
        ownership_gate = entry.index("hasOwnedEnclosingTransaction")
        begin = entry.index("beginCommandInvocation")
        open_command = entry.index("document->openCommand")
        self.assertLess(ownership_gate, begin)
        self.assertLess(ownership_gate, open_command)

    def test_move_feature_rejects_body_containers_before_dependency_expansion(self):
        """A Body derives from Part::Feature but must never be moved into itself."""

        source_root = _source_checkout_root()
        if source_root is None:
            self.skipTest("Part Design command source checkout is unavailable")

        implementation = (
            source_root / "src/Mod/PartDesign/Gui/CommandBody.cpp"
        ).read_text(encoding="utf-8")

        candidate_start = implementation.index(
            "bool isMoveFeatureCandidate("
        )
        candidate = implementation[
            candidate_start:
            implementation.index(
                "\n}\n",
                candidate_start,
            )
        ]
        self.assertIn(
            "object->isDerivedFrom<Part::Feature>()",
            candidate,
        )
        self.assertIn(
            "PartDesign::Body::isAllowed(object)",
            candidate,
        )

        activation_start = implementation.index(
            "void CmdPartDesignMoveFeature::activated(int iMsg)"
        )
        activation = implementation[
            activation_start:
            implementation.index(
                "bool CmdPartDesignMoveFeature::isActive()",
                activation_start,
            )
        ]
        admission = activation.index(
            "std::ranges::all_of(\n"
            "            selection,"
        )
        feature_extraction = activation.index(
            "std::ranges::transform(",
            admission,
        )
        dependency_expansion = activation.index(
            "PartDesignGui::collectMovableDependencies("
        )
        self.assertLess(admission, feature_extraction)
        self.assertLess(feature_extraction, dependency_expansion)

        active_start = implementation.index(
            "bool CmdPartDesignMoveFeature::isActive()"
        )
        active = implementation[
            active_start:
            implementation.index(
                "DEF_STD_CMD_A(CmdPartDesignMoveFeatureInTree)",
                active_start,
            )
        ]
        self.assertIn("Gui::Selection().getSelectionEx(", active)
        self.assertIn("isMoveFeatureCandidate(", active)


class TestNativeTaskContract(unittest.TestCase):
    """Exercise the common TaskDialog contract through real native actions."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")

        self.tree_preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/TreeView"
        )
        self.previous_organized_tree = self.tree_preferences.GetBool(
            "OrganizeModelByType",
            True,
        )
        # The typed VibeCAD browser intentionally presents Bodies and Sketches,
        # while edit history lives in the timeline.  Disable that presentation
        # only for the test that must drive the legacy native TreeWidget's
        # double-click entry point.
        self.tree_preferences.SetBool("OrganizeModelByType", False)

        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("NativeTaskContract")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self._process_events(50)

    def tearDown(self):
        Gui.Selection.clearSelection()
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
            self._process_events()

        document = getattr(self, "document", None)
        if document is not None and App.getDocument(document.Name) is not None:
            if document.HasPendingTransaction:
                document.abortTransaction()
            App.closeDocument(document.Name)
        self.tree_preferences.SetBool(
            "OrganizeModelByType",
            self.previous_organized_tree,
        )
        self._process_events(50)

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

    def _wait_until(self, predicate, timeout_ms=5000):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            self._process_events()
            try:
                result = predicate()
            except RuntimeError:
                # Tree refreshes replace Python wrappers for QTreeWidgetItems.
                result = None
            if result:
                return result
        return None

    def _task_button(self, standard_button):
        self._process_events()
        for box in Gui.getMainWindow().findChildren(QtGui.QDialogButtonBox):
            if not box.isVisible():
                continue
            button = box.button(standard_button)
            if button is not None and button.isVisible() and button.isEnabled():
                return button
        return None

    def _cancel_task(self):
        button = self._task_button(QtGui.QDialogButtonBox.Cancel)
        if button is None:
            button = self._task_button(QtGui.QDialogButtonBox.Close)
        self.assertIsNotNone(button)
        button.click()
        self._process_events(60)
        self.assertFalse(Gui.Control.activeDialog())

    def _accept_task(self):
        button = self._task_button(QtGui.QDialogButtonBox.Ok)
        self.assertIsNotNone(button)
        button.click()
        self._process_events(60)
        self.assertFalse(Gui.Control.activeDialog())

    def _accept_next_modal(self):
        attempts = [0]

        def accept():
            attempts[0] += 1
            modal = QtGui.QApplication.activeModalWidget()
            if modal is None:
                if attempts[0] < 200:
                    QtCore.QTimer.singleShot(5, accept)
                return
            modal.accept()

        QtCore.QTimer.singleShot(0, accept)

    def _new_box_body(self, body_name, feature_name):
        body = self.document.addObject("PartDesign::Body", body_name)
        box = body.newObject("PartDesign::AdditiveBox", feature_name)
        box.Length = 11.0
        box.Width = 7.0
        box.Height = 5.0
        body.Tip = box
        self.document.recompute()
        self.assertTrue(box.isValid(), box.getStatusString())
        self.assertFalse(box.Shape.isNull())
        return body, box

    @staticmethod
    def _selection_state():
        return tuple(
            (
                item.DocumentName,
                item.ObjectName,
                tuple(item.SubElementNames),
                tuple(
                    (point.x, point.y, point.z)
                    for point in item.PickedPoints
                ),
            )
            for item in Gui.Selection.getSelectionEx()
        )

    @staticmethod
    def _find_model_index(model, parent, label):
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            if model.data(index, QtCore.Qt.DisplayRole) == label:
                return index
            found = TestNativeTaskContract._find_model_index(
                model,
                index,
                label,
            )
            if found is not None:
                return found
        return None

    def _tree_model_index(self, label):
        for tree in Gui.getMainWindow().findChildren(QtGui.QTreeWidget):
            try:
                # Both the standalone Tree View and Combo View can own a
                # synchronized model tree.  Mouse events sent to a hidden
                # instance are intentionally ignored by Qt and do not model a
                # native user gesture.
                if not tree.isVisible() or not tree.viewport().isVisible():
                    continue
                model = tree.model()
                document_index = self._find_model_index(
                    model,
                    QtCore.QModelIndex(),
                    self.document.Label,
                )
                if document_index is None:
                    continue
                index = self._find_model_index(
                    model,
                    document_index,
                    label,
                )
                if index is not None:
                    return (
                        tree,
                        QtCore.QPersistentModelIndex(index),
                    )
            except RuntimeError:
                continue
        return None

    def _double_click_native_tree_item(self, label):
        last_error = None
        for _attempt in range(20):
            result = self._wait_until(
                lambda: self._tree_model_index(label),
                1000,
            )
            if result is None:
                continue
            tree, index = result
            try:
                stage = "parent traversal"
                ancestor = index.parent()
                while ancestor.isValid():
                    tree.expand(ancestor)
                    ancestor = ancestor.parent()
                stage = "scroll"
                tree.scrollTo(index)
                # Expansion can schedule a presentation rebuild, so dispatch
                # the double-click in this event turn while the resolved
                # native item is still valid.
                stage = "item rectangle"
                position = tree.visualRect(index).center()
                global_position = tree.viewport().mapToGlobal(position)
                try:
                    event = QtGui.QMouseEvent(
                        QtCore.QEvent.MouseButtonDblClick,
                        QtCore.QPointF(position),
                        QtCore.QPointF(global_position),
                        QtCore.Qt.LeftButton,
                        QtCore.Qt.LeftButton,
                        QtCore.Qt.NoModifier,
                    )
                except TypeError:
                    # Qt 5's constructor does not accept a separate global
                    # position.
                    event = QtGui.QMouseEvent(
                        QtCore.QEvent.MouseButtonDblClick,
                        QtCore.QPointF(position),
                        QtCore.Qt.LeftButton,
                        QtCore.Qt.LeftButton,
                        QtCore.Qt.NoModifier,
                    )
                stage = "double-click dispatch"
                QtGui.QApplication.sendEvent(tree.viewport(), event)
                self._process_events(60)
                return
            except RuntimeError as error:
                # Presentation refresh deleted an item between lookup and use.
                last_error = f"{stage}: {error!r}"
                continue
        self.fail(
            f"Native tree omitted a stable item for {label!r}: {last_error}"
        )

    def _start_macro_recording(self, directory, name):
        def start():
            widgets = list(QtGui.QApplication.topLevelWidgets())
            main_window = Gui.getMainWindow()
            if main_window is not None:
                widgets.extend(main_window.findChildren(QtGui.QDialog))
            dialog = next(
                (
                    widget
                    for widget in widgets
                    if widget.isVisible()
                    and (
                        widget.objectName()
                        == "Gui::Dialog::DlgMacroRecord"
                        or (
                            widget.findChild(
                                QtGui.QLineEdit,
                                "lineEditMacroPath",
                            )
                            is not None
                            and widget.findChild(
                                QtGui.QPushButton,
                                "buttonStart",
                            )
                            is not None
                        )
                    )
                ),
                None,
            )
            if dialog is None:
                QtCore.QTimer.singleShot(10, start)
                return
            path = dialog.findChild(QtGui.QLineEdit, "lineEditMacroPath")
            filename = dialog.findChild(QtGui.QLineEdit, "lineEditPath")
            button = dialog.findChild(QtGui.QPushButton, "buttonStart")
            self.assertIsNotNone(path)
            self.assertIsNotNone(filename)
            self.assertIsNotNone(button)
            path.setText(str(directory) + os.sep)
            filename.setText(name)
            button.click()

        QtCore.QTimer.singleShot(0, start)
        Gui.runCommand("Std_DlgMacroRecord", 0)
        self._process_events(40)

    def _stop_macro_recording(self, path):
        Gui.runCommand("Std_DlgMacroRecord", 0)
        self._process_events(40)
        self.assertTrue(path.is_file(), path)
        return path.read_text(encoding="utf-8")

    def test_standalone_task_owns_its_exact_launch_transaction(self):
        """Panels without an edit ViewProvider still own their launch work."""

        document = self.document
        command_state = {
            "name": None,
            "transaction": 0,
        }

        class StandalonePanel:
            def __init__(self):
                self.form = QtGui.QWidget()
                self.form.setWindowTitle("Standalone task contract")

            def getStandardButtons(self):
                return (
                    QtGui.QDialogButtonBox.Ok
                    | QtGui.QDialogButtonBox.Cancel
                )

            def accept(self):
                return True

            def reject(self):
                return True

        class StandaloneCommand:
            def GetResources(self):
                return {"MenuText": "Standalone task contract command"}

            def Activated(self):
                document.openTransaction(
                    "Standalone task contract transaction"
                )
                command_state["transaction"] = (
                    document.getBookedTransactionID()
                )
                document.addObject(
                    "App::FeaturePython",
                    command_state["name"],
                )
                Gui.Control.showDialog(
                    StandalonePanel(),
                    Gui.activeDocument(),
                )

            def IsActive(self):
                return True

        command_name = (
            f"VibeCAD_TestStandaloneTask_{id(self):x}"
        )
        Gui.addCommand(command_name, StandaloneCommand())

        initial_undo_count = document.UndoCount
        command_state["name"] = "StandaloneCanceledResult"
        Gui.runCommand(command_name, 0)
        self._process_events(60)

        canceled_transaction = command_state["transaction"]
        self.assertNotEqual(0, canceled_transaction)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertTrue(
            Gui.Control.ownsCommandTransaction(
                Gui.activeDocument(),
                canceled_transaction,
            )
        )
        self.assertIsNotNone(
            document.getObject("StandaloneCanceledResult")
        )

        # The panel owns and locks this exact transaction. An unrelated broad
        # abort attempt must not invalidate the panel before the user decides.
        document.abortTransaction()
        self.assertEqual(
            canceled_transaction,
            document.getBookedTransactionID(),
        )
        self.assertIsNotNone(
            document.getObject("StandaloneCanceledResult")
        )

        self._cancel_task()
        self.assertIsNone(
            document.getObject("StandaloneCanceledResult")
        )
        self.assertFalse(document.HasPendingTransaction)
        self.assertEqual(initial_undo_count, document.UndoCount)

        command_state["name"] = "StandaloneAcceptedResult"
        Gui.runCommand(command_name, 0)
        self._process_events(60)

        accepted_transaction = command_state["transaction"]
        self.assertNotEqual(0, accepted_transaction)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertTrue(
            Gui.Control.ownsCommandTransaction(
                Gui.activeDocument(),
                accepted_transaction,
            )
        )

        self._accept_task()
        self.assertIsNotNone(
            document.getObject("StandaloneAcceptedResult")
        )
        self.assertFalse(document.HasPendingTransaction)
        self.assertEqual(initial_undo_count + 1, document.UndoCount)

        document.undo()
        self._process_events()
        self.assertIsNone(
            document.getObject("StandaloneAcceptedResult")
        )
        document.redo()
        self._process_events()
        self.assertIsNotNone(
            document.getObject("StandaloneAcceptedResult")
        )

    def test_move_feature_never_treats_a_body_as_its_own_member(self):
        source, feature = self._new_box_body("SourceBody", "SourceFeature")
        target = self.document.addObject("PartDesign::Body", "TargetBody")
        source_members = tuple(source.Group)
        target_members = tuple(target.Group)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source)
        self._process_events()

        self.assertFalse(Gui.isCommandActive("PartDesign_MoveFeature"))
        Gui.runCommand("PartDesign_MoveFeature", 0)
        self._process_events()

        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(tuple(source.Group), source_members)
        self.assertEqual(tuple(target.Group), target_members)
        self.assertEqual(feature.getParentGeoFeatureGroup(), source)

    def test_cancel_does_not_close_an_unrelated_existing_transaction(self):
        """A non-editing task never owns the transaction it merely observes."""

        probe = self.document.addObject("Part::Feature", "TransactionProbe")
        probe.addProperty("App::PropertyString", "ContractValue")
        probe.ContractValue = "before unrelated transaction"
        probe.Shape = Part.makeBox(3, 4, 5)
        self.document.recompute()
        Gui.Selection.addSelection(probe)
        self._process_events()

        self.document.openTransaction(
            "Unrelated caller transaction"
        )
        transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(transaction_id, 0)
        probe.ContractValue = "inside unrelated transaction"
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction_id,
        )

        try:
            Gui.runCommand("Part_CheckGeometry", 0)
            self._process_events(60)
            self.assertTrue(Gui.Control.activeDialog())
            self.assertEqual(
                self.document.getBookedTransactionID(),
                transaction_id,
            )

            self._cancel_task()

            self.assertTrue(
                self.document.HasPendingTransaction,
                "Closing a read-only task aborted its caller's transaction",
            )
            self.assertEqual(
                self.document.getBookedTransactionID(),
                transaction_id,
            )
            self.assertEqual(
                probe.ContractValue,
                "inside unrelated transaction",
            )
        finally:
            if self.document.HasPendingTransaction:
                self.document.abortTransaction()

        self.assertEqual(
            probe.ContractValue,
            "before unrelated transaction",
        )

    def test_existing_sketch_cancel_preserves_a_later_unrelated_transaction(self):
        """An edit with no owner cannot abort work opened later by a caller."""

        sketch = self.document.addObject(
            "Sketcher::SketchObject",
            "ExistingSketch",
        )
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(0, 0, 0),
                App.Vector(8, 3, 0),
            ),
            False,
        )
        probe = self.document.addObject("Part::Feature", "EditOwnerProbe")
        probe.addProperty("App::PropertyString", "ContractValue")
        probe.ContractValue = "before unrelated transaction"
        probe.Shape = Part.makeBox(2, 3, 4)
        self.document.recompute()

        self.assertEqual(
            0,
            self.document.getBookedTransactionID(),
        )
        self.assertTrue(Gui.activeDocument().setEdit(sketch.Name))
        self._process_events(80)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertEqual(
            0,
            self.document.getBookedTransactionID(),
        )

        self.document.openTransaction("Later unrelated transaction")
        unrelated_transaction = self.document.getBookedTransactionID()
        self.assertNotEqual(0, unrelated_transaction)
        probe.ContractValue = "inside unrelated transaction"
        self.assertTrue(self.document.HasPendingTransaction)

        try:
            Gui.runCommand("Sketcher_CancelSketch", 0)
            self._process_events(80)

            self.assertFalse(Gui.Control.activeDialog())
            self.assertTrue(
                self.document.HasPendingTransaction,
                "Sketch Cancel aborted a transaction it never adopted",
            )
            self.assertEqual(
                unrelated_transaction,
                self.document.getBookedTransactionID(),
            )
            self.assertEqual(
                "inside unrelated transaction",
                probe.ContractValue,
            )
        finally:
            if self.document.HasPendingTransaction:
                self.document.abortTransaction()

        self.assertEqual("before unrelated transaction", probe.ContractValue)

    def test_direct_app_close_does_not_adopt_synchronous_successor(self):
        """A Python App close completes T even when its callback opens S."""

        document = self.document

        class SuccessorObserver:
            def __init__(self):
                self.armed = False
                self.successor = 0
                self.error = None

            def slotCloseTransaction(self, abort):
                if not self.armed or abort or self.successor:
                    return
                try:
                    document.openTransaction("Direct App close successor")
                    self.successor = document.getBookedTransactionID()
                    document.addObject(
                        "App::FeaturePython",
                        "DirectAppCloseSuccessor",
                    )
                except Exception as error:
                    self.error = error

        observer = SuccessorObserver()

        class DirectCloseCommand:
            def GetResources(self):
                return {"MenuText": "Direct exact-close contract command"}

            def Activated(self):
                document.openTransaction("Direct App exact close")
                transaction_id = document.getBookedTransactionID()
                document.addObject(
                    "App::FeaturePython",
                    "DirectAppCloseAccepted",
                )
                observer.armed = True
                App.closeActiveTransaction(False, transaction_id)

            def IsActive(self):
                return True

        command_name = "VibeCAD_TestDirectAppExactClose"
        Gui.addCommand(command_name, DirectCloseCommand())
        App.addDocumentObserver(observer)
        try:
            Gui.runCommand(command_name, 0)
            self._process_events()
            self.assertIsNone(observer.error)
            self.assertNotEqual(observer.successor, 0)
            self.assertEqual(
                document.getBookedTransactionID(),
                observer.successor,
            )
            self.assertIsNotNone(
                document.getObject("DirectAppCloseSuccessor")
            )
        finally:
            App.removeDocumentObserver(observer)
            observer.armed = False
            if document.getBookedTransactionID() == observer.successor:
                App.closeActiveTransaction(True, observer.successor)

    def test_existing_sketch_cancel_with_no_owner_leaves_no_transaction(self):
        """Directly editing an existing sketch does not invent ownership."""

        sketch = self.document.addObject(
            "Sketcher::SketchObject",
            "NoOwnerSketch",
        )
        sketch.addGeometry(
            Part.Circle(
                App.Vector(0, 0, 0),
                App.Vector(0, 0, 1),
                4,
            ),
            False,
        )
        self.document.recompute()
        self.assertEqual(
            0,
            self.document.getBookedTransactionID(),
        )

        self.assertTrue(Gui.activeDocument().setEdit(sketch.Name))
        self._process_events(80)
        self.assertTrue(Gui.Control.activeDialog())
        Gui.runCommand("Sketcher_CancelSketch", 0)
        self._process_events(80)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(
            0,
            self.document.getBookedTransactionID(),
        )
        self.assertIsNotNone(self.document.getObject(sketch.Name))

    def test_new_sketch_cancel_aborts_its_transferred_transaction_only(self):
        """A provisional sketch transfers its creation transaction to edit."""

        Gui.activateWorkbench("SketcherWorkbench")
        Gui.Selection.clearSelection()
        original_objects = tuple(self.document.Objects)
        original_undo_count = self.document.UndoCount

        self._accept_next_modal()
        Gui.runCommand("Sketcher_NewSketch", 0)
        self._process_events(100)

        created = [
            obj for obj in self.document.Objects
            if obj not in original_objects
            and getattr(obj, "ViewObject", None) is not None
        ]
        self.assertEqual(1, len(created))
        self.assertTrue(
            created[0].isDerivedFrom("Sketcher::SketchObject")
        )
        self.assertTrue(Gui.Control.activeDialog())
        edit_transaction = self.document.getBookedTransactionID()
        self.assertNotEqual(0, edit_transaction)
        self.assertTrue(self.document.HasPendingTransaction)

        Gui.runCommand("Sketcher_CancelSketch", 0)
        self._process_events(100)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(original_objects, tuple(self.document.Objects))
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(
            0,
            self.document.getBookedTransactionID(),
        )
        self.assertEqual(original_undo_count, self.document.UndoCount)

    def test_owned_edit_transaction_cannot_be_replaced_or_closed(self):
        """A live provisional edit keeps its exact rollback journal."""

        Gui.activateWorkbench("SketcherWorkbench")
        Gui.Selection.clearSelection()
        original_objects = tuple(self.document.Objects)
        original_undo_count = self.document.UndoCount

        self._accept_next_modal()
        Gui.runCommand("Sketcher_NewSketch", 0)
        self._process_events(100)

        self.assertTrue(Gui.Control.activeDialog())
        edit_transaction = self.document.getBookedTransactionID()
        self.assertNotEqual(0, edit_transaction)
        self.assertTrue(self.document.HasPendingTransaction)

        replacement = self.document.openTransaction(
            "External replacement attempt"
        )
        self.assertIsNone(replacement)
        self.assertEqual(
            edit_transaction,
            self.document.getBookedTransactionID(),
        )

        application_replacement = App.setActiveTransaction(
            "External application replacement attempt"
        )
        self.assertEqual(0, application_replacement)
        self.assertEqual(
            edit_transaction,
            self.document.getBookedTransactionID(),
        )

        self.document.commitTransaction()
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertEqual(
            edit_transaction,
            self.document.getBookedTransactionID(),
        )

        self.document.abortTransaction()
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertEqual(
            edit_transaction,
            self.document.getBookedTransactionID(),
        )

        Gui.runCommand("Sketcher_CancelSketch", 0)
        self._process_events(100)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(original_objects, tuple(self.document.Objects))
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(
            0,
            self.document.getBookedTransactionID(),
        )
        self.assertEqual(original_undo_count, self.document.UndoCount)

    def test_generic_gui_abort_cannot_poison_accept(self):
        """A failed GUI sub-command is not the task panel's Cancel button."""

        Gui.activateWorkbench("SketcherWorkbench")
        Gui.Selection.clearSelection()
        original_objects = tuple(self.document.Objects)
        original_undo_count = self.document.UndoCount

        self._accept_next_modal()
        Gui.runCommand("Sketcher_NewSketch", 0)
        self._process_events(100)

        created = [
            obj for obj in self.document.Objects
            if obj not in original_objects
            and getattr(obj, "ViewObject", None) is not None
        ]
        self.assertEqual(1, len(created))
        transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(0, transaction_id)
        self.assertTrue(Gui.Control.activeDialog())

        Gui.activeDocument().abortCommand()

        self.assertTrue(Gui.Control.activeDialog())
        self.assertIs(self.document.getObject(created[0].Name), created[0])
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertEqual(
            transaction_id,
            self.document.getBookedTransactionID(),
        )

        self._accept_task()

        self.assertFalse(Gui.Control.activeDialog())
        self.assertIs(self.document.getObject(created[0].Name), created[0])
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(0, self.document.getBookedTransactionID())
        self.assertGreater(self.document.UndoCount, original_undo_count)

    def test_external_app_abort_cannot_poison_accept(self):
        """An arbitrary App abort attempt cannot turn OK into Cancel."""

        Gui.activateWorkbench("SketcherWorkbench")
        Gui.Selection.clearSelection()
        original_objects = tuple(self.document.Objects)

        self._accept_next_modal()
        Gui.runCommand("Sketcher_NewSketch", 0)
        self._process_events(100)

        created = [
            obj for obj in self.document.Objects
            if obj not in original_objects
            and getattr(obj, "ViewObject", None) is not None
        ]
        self.assertEqual(1, len(created))
        transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(0, transaction_id)

        self.document.abortTransaction()
        self.assertEqual(
            transaction_id,
            self.document.getBookedTransactionID(),
        )
        self.assertTrue(self.document.HasPendingTransaction)

        self._accept_task()

        self.assertFalse(Gui.Control.activeDialog())
        self.assertIs(self.document.getObject(created[0].Name), created[0])
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(0, self.document.getBookedTransactionID())

    def test_tree_edit_cancel_without_undo_restores_the_complete_feature(self):
        """Double-click editing is exact even when user Undo is disabled."""

        body, box = self._new_box_body("EditableBody", "EditableBox")
        other_body, other = self._new_box_body("OtherBody", "OtherBox")
        box.addProperty("App::PropertyString", "ContractText")
        box.ContractText = "original text"
        box.Label = "Editable native box"
        body.Tip = box
        body.Visibility = True
        box.Visibility = True
        other_body.Visibility = False
        other.Visibility = False
        self.document.recompute()

        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(
            self.document.Name,
            box.Name,
            "Face1",
            2.0,
            3.0,
            5.0,
        )
        self._process_events()

        original = {
            "length": box.Length.Value,
            "width": box.Width.Value,
            "height": box.Height.Value,
            "placement": App.Placement(box.Placement),
            "label": box.Label,
            "text": box.ContractText,
            "shape": box.Shape.exportBrepToString(),
            "body_tip": body.Tip,
            "body_group": tuple(body.Group),
            "visibility": (
                body.Visibility,
                box.Visibility,
                other_body.Visibility,
                other.Visibility,
            ),
            "selection": self._selection_state(),
            "active_body": Gui.activeView().getActiveObject("pdbody"),
            "active_object": self.document.ActiveObject,
        }
        self.document.clearUndos()
        self.document.UndoMode = False
        Gui.activeDocument().Modified = False
        self.assertFalse(Gui.activeDocument().Modified)

        self._double_click_native_tree_item(box.Label)
        self.assertTrue(
            Gui.Control.activeDialog(),
            "Native tree double-click did not enter feature edit mode",
        )
        self.assertTrue(
            self.document.HasPendingTransaction,
            "Existing-feature edit has no private rollback transaction",
        )

        box.Length = 37.0
        box.Width = 13.0
        box.Height = 2.0
        box.Placement = App.Placement(
            App.Vector(17, -9, 4),
            App.Rotation(App.Vector(0, 1, 0), 31),
        )
        box.Label = "Canceled replacement label"
        box.ContractText = "canceled replacement text"
        self.document.recompute()
        body.Tip = None
        body.Visibility = False
        box.Visibility = False
        other_body.Visibility = True
        other.Visibility = True
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(other, "Edge1")
        Gui.activeView().setActiveObject("pdbody", other_body)
        temporary = self.document.addObject(
            "Part::Feature",
            "CanceledTaskTemporary",
        )
        temporary.Shape = Part.makeSphere(2.0)
        self.assertTrue(Gui.activeDocument().Modified)
        self.assertNotEqual(
            box.Shape.exportBrepToString(),
            original["shape"],
        )

        self._cancel_task()

        self.assertFalse(self.document.HasPendingTransaction)
        self.assertFalse(self.document.UndoMode)
        self.assertEqual(self.document.UndoCount, 0)
        self.assertEqual(box.Length.Value, original["length"])
        self.assertEqual(box.Width.Value, original["width"])
        self.assertEqual(box.Height.Value, original["height"])
        self.assertTrue(box.Placement.isSame(original["placement"], 1e-12))
        self.assertEqual(box.Label, original["label"])
        self.assertEqual(box.ContractText, original["text"])
        self.assertEqual(
            box.Shape.exportBrepToString(),
            original["shape"],
        )
        self.assertIs(body.Tip, original["body_tip"])
        self.assertEqual(tuple(body.Group), original["body_group"])
        self.assertEqual(
            (
                body.Visibility,
                box.Visibility,
                other_body.Visibility,
                other.Visibility,
            ),
            original["visibility"],
        )
        self.assertEqual(self._selection_state(), original["selection"])
        self.assertIs(
            Gui.activeView().getActiveObject("pdbody"),
            original["active_body"],
        )
        self.assertIs(
            self.document.ActiveObject,
            original["active_object"],
        )
        self.assertIsNone(
            self.document.getObject("CanceledTaskTemporary"),
        )
        self.assertFalse(
            Gui.activeDocument().Modified,
            "Cancel changed the document's saved/dirty state",
        )

    def test_tree_edit_refuses_a_caller_booked_transaction(self):
        """A direct tree edit never finishes a transaction it did not open."""

        body, box = self._new_box_body(
            "CallerTransactionBody",
            "CallerTransactionBox",
        )
        box.Label = "Caller transaction editable box"
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(box, "Face1")
        self._process_events()
        original = {
            "objects": tuple(self.document.Objects),
            "selection": self._selection_state(),
            "tip": body.Tip,
            "visibility": (body.Visibility, box.Visibility),
            "undo_count": self.document.UndoCount,
        }

        self.document.openTransaction("Caller-owned tree transaction")
        caller_transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(caller_transaction_id, 0)
        self.assertFalse(self.document.HasPendingTransaction)
        try:
            self._double_click_native_tree_item(box.Label)
            self.assertEqual(
                self.document.getBookedTransactionID(),
                caller_transaction_id,
            )
            self.assertFalse(self.document.HasPendingTransaction)
            self.assertFalse(Gui.Control.activeDialog())
            self.assertIsNone(Gui.activeDocument().getInEdit())
            self.assertEqual(tuple(self.document.Objects), original["objects"])
            self.assertEqual(self._selection_state(), original["selection"])
            self.assertIs(body.Tip, original["tip"])
            self.assertEqual(
                (body.Visibility, box.Visibility),
                original["visibility"],
            )
            self.assertEqual(
                self.document.UndoCount,
                original["undo_count"],
            )
        finally:
            if (
                self.document.getBookedTransactionID()
                == caller_transaction_id
            ):
                self.document.abortTransaction()

    def test_reset_edit_closes_task_before_a_successor_transaction(self):
        """No stale task can later accept or cancel a caller's transaction."""

        body = self.document.addObject(
            "PartDesign::Body",
            "ResetEditBody",
        )
        probe = self.document.addObject(
            "Part::Feature",
            "ResetEditProbe",
        )
        probe.addProperty("App::PropertyString", "ContractValue")
        probe.ContractValue = "before successor"
        probe.Shape = Part.makeBox(2, 3, 4)
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(body)

        Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())
        accepted = self.document.ActiveObject
        task_transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(task_transaction_id, 0)

        Gui.activeDocument().resetEdit()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertIs(self.document.getObject(accepted.Name), accepted)
        self.assertIn(accepted, body.Group)

        self.document.openTransaction(
            "Caller transaction after task teardown"
        )
        successor_id = self.document.getBookedTransactionID()
        self.assertNotEqual(successor_id, 0)
        self.assertNotEqual(successor_id, task_transaction_id)
        probe.ContractValue = "inside successor"

        try:
            self._process_events()
            self.assertFalse(Gui.Control.activeDialog())
            self.assertEqual(
                self.document.getBookedTransactionID(),
                successor_id,
            )
            self.assertTrue(self.document.HasPendingTransaction)
            self.assertEqual(probe.ContractValue, "inside successor")
            self.assertIs(self.document.getObject(accepted.Name), accepted)
        finally:
            if self.document.getBookedTransactionID() == successor_id:
                self.document.abortTransaction()

        self.assertEqual(probe.ContractValue, "before successor")
        self.assertIs(self.document.getObject(accepted.Name), accepted)

    def test_deleting_inactive_task_document_preserves_live_selection(self):
        """Deleting task doc A cannot restore A state over active doc B."""

        sketch = self.document.addObject(
            "Sketcher::SketchObject",
            "DeletedTaskSketch",
        )
        sketch.addGeometry(
            Part.LineSegment(
                App.Vector(0, 0, 0),
                App.Vector(6, 4, 0),
            ),
            False,
        )
        self.document.recompute()
        other = App.newDocument("NativeTaskDeletionOther")
        other_probe = other.addObject(
            "Part::Feature",
            "LiveSelectionProbe",
        )
        other_probe.Shape = Part.makeBox(2, 3, 4)
        other.recompute()
        deleted_document_name = self.document.Name
        App.setActiveDocument(self.document.Name)
        self._process_events(60)
        macro_recording = False
        macro_path = None

        try:
            with tempfile.TemporaryDirectory(
                prefix="vibecad-task-document-delete-"
            ) as directory:
                directory = Path(directory)
                macro_path = directory / "DeletedTaskDocument.FCMacro"
                self._start_macro_recording(
                    directory,
                    "DeletedTaskDocument",
                )
                macro_recording = True
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(sketch)
                Gui.runCommand("Sketcher_ValidateSketch", 0)
                self._process_events(60)
                self.assertTrue(Gui.Control.activeDialog())

                App.setActiveDocument(other.Name)
                self._process_events(60)
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(other_probe, "Face1")
                expected_selection = self._selection_state()

                App.closeDocument(deleted_document_name)
                self.document = None
                self._process_events(80)

                self.assertEqual(App.ActiveDocument.Name, other.Name)
                self.assertEqual(
                    self._selection_state(),
                    expected_selection,
                )
                self.assertFalse(Gui.Control.activeDialog())
                macro_recording = False
                macro = self._stop_macro_recording(macro_path)

            self.assertNotIn(
                "Gui.runCommand('Sketcher_ValidateSketch'",
                macro,
            )
        finally:
            if macro_recording and macro_path is not None:
                macro_recording = False
                try:
                    self._stop_macro_recording(macro_path)
                except (AssertionError, RuntimeError):
                    pass
            documents = App.listDocuments()
            if deleted_document_name in documents:
                App.closeDocument(deleted_document_name)
                self.document = None
            if other.Name in App.listDocuments():
                App.closeDocument(other.Name)
            self._process_events(60)

    def test_closing_definition_prunes_only_cross_document_selection(self):
        """Closing A removes B's resolved A refs without clearing local B."""

        temporary_directory = tempfile.TemporaryDirectory(
            prefix="vibecad-cross-document-selection-"
        )
        source = self.document.addObject(
            "Part::Feature",
            "CrossDocumentSource",
        )
        source.Shape = Part.makeBox(4, 5, 6)
        self.document.recompute()
        self.document.saveAs(
            str(Path(temporary_directory.name) / "source.FCStd")
        )
        source_document_name = self.document.Name

        other = App.newDocument("NativeTaskCrossSelectionOther")
        other.saveAs(
            str(Path(temporary_directory.name) / "selection.FCStd")
        )
        local = other.addObject("Part::Feature", "LocalSelection")
        local.Shape = Part.makeBox(3, 4, 5, App.Vector(20, 0, 0))
        linked = other.addObject("App::Link", "ExternalSelection")
        linked.LinkedObject = source
        other.recompute()
        other.save()
        App.setActiveDocument(other.Name)
        self._process_events(60)

        try:
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(local, "Face1")
            Gui.Selection.addSelection(linked, "Face1")
            self._process_events()
            before = Gui.Selection.getSelectionEx(other.Name)
            self.assertEqual(
                {item.ObjectName for item in before},
                {local.Name, linked.Name},
            )

            App.closeDocument(source_document_name)
            self.document = None
            self._process_events(80)

            after = Gui.Selection.getSelectionEx(other.Name)
            self.assertEqual(
                tuple(item.ObjectName for item in after),
                (local.Name,),
            )
        finally:
            documents = App.listDocuments()
            if source_document_name in documents:
                App.closeDocument(source_document_name)
                self.document = None
            if other.Name in App.listDocuments():
                other.save()
                App.closeDocument(other.Name)
            temporary_directory.cleanup()
            self._process_events(60)

    def test_cancel_discards_macro_trace_and_accept_publishes_it(self):
        body = self.document.addObject("PartDesign::Body", "MacroBody")
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(body)
        self._process_events()

        with tempfile.TemporaryDirectory(
            prefix="vibecad-native-task-macro-"
        ) as directory:
            directory = Path(directory)

            canceled_path = directory / "CanceledNativeTask.FCMacro"
            self._start_macro_recording(directory, "CanceledNativeTask")
            Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
            self._process_events()
            self.assertTrue(Gui.Control.activeDialog())
            canceled_name = self.document.ActiveObject.Name
            self._cancel_task()
            canceled_macro = self._stop_macro_recording(canceled_path)

            self.assertNotIn(f"_tv_{canceled_name}", canceled_macro)
            self.assertNotIn("PartDesign::AdditiveBox", canceled_macro)
            self.assertNotIn(
                "Gui.runCommand('PartDesign_CompPrimitiveAdditive'",
                canceled_macro,
            )

            accepted_path = directory / "AcceptedNativeTask.FCMacro"
            self._start_macro_recording(directory, "AcceptedNativeTask")
            Gui.activeView().setActiveObject("pdbody", body)
            Gui.Selection.clearSelection()
            Gui.Selection.addSelection(body)
            Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
            self._process_events()
            self.assertTrue(Gui.Control.activeDialog())
            accepted = self.document.ActiveObject
            accepted_name = accepted.Name
            self._accept_task()
            accepted_macro = self._stop_macro_recording(accepted_path)

        self.assertIs(body.Tip, accepted)
        self.assertIn(accepted, body.Group)
        self.assertIn(accepted_name, accepted_macro)
        self.assertIn("PartDesign::AdditiveBox", accepted_macro)
        self.assertEqual(
            accepted_macro.count("PartDesign::AdditiveBox"),
            1,
            accepted_macro,
        )
        self.assertNotIn(
            "Gui.runCommand('PartDesign_CompPrimitiveAdditive'",
            accepted_macro,
        )

    def test_command_state_refresh_is_observationally_pure(self):
        """Checking ribbon enablement must not activate the selected Body."""

        active_body, _active_result = self._new_box_body(
            "StateRefreshActiveBody",
            "StateRefreshActiveResult",
        )
        selected_body, _selected_result = self._new_box_body(
            "StateRefreshSelectedBody",
            "StateRefreshSelectedResult",
        )
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(selected_body, "Edge1")
        self._process_events()

        with tempfile.TemporaryDirectory(
            prefix="vibecad-command-state-purity-"
        ) as directory:
            directory = Path(directory)
            path = directory / "CommandStatePurity.FCMacro"
            self._start_macro_recording(directory, "CommandStatePurity")
            # Selection updates may legitimately refresh every action. Put the
            # intended active Body back immediately before the explicit state
            # query so this assertion isolates the query itself.
            Gui.activeView().setActiveObject("pdbody", active_body)
            expected = (
                self.document.ActiveObject,
                Gui.activeView().getActiveObject("pdbody"),
                self._selection_state(),
                self.document.getBookedTransactionID(),
                self.document.HasPendingTransaction,
            )

            for _iteration in range(10):
                self.assertTrue(
                    Gui.isCommandActive("PartDesign_Chamfer")
                )
                self.assertTrue(
                    Gui.Command.get("PartDesign_Chamfer")
                    .getAction()[0]
                    .isEnabled()
                )
                self._process_events()

            actual = (
                self.document.ActiveObject,
                Gui.activeView().getActiveObject("pdbody"),
                self._selection_state(),
                self.document.getBookedTransactionID(),
                self.document.HasPendingTransaction,
            )
            macro = self._stop_macro_recording(path)

        self.assertEqual(actual, expected)
        self.assertIs(actual[1], active_body)
        self.assertNotIn("setActiveObject", macro)
        self.assertNotIn("Gui.runCommand('PartDesign_Chamfer'", macro)

    def test_inactive_task_never_captures_another_documents_trace(self):
        """Switching documents scopes task macro capture to the shown panel."""

        body = self.document.addObject(
            "PartDesign::Body",
            "SwitchedMacroBody",
        )
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", body)
        other = App.newDocument("NativeTaskMacroOther")
        other.addObject("Part::Feature", "OtherDocumentProbe").Shape = (
            Part.makeBox(2, 3, 4)
        )
        other.recompute()
        App.setActiveDocument(self.document.Name)
        self._process_events(60)

        def switch_and_emit(probe):
            App.setActiveDocument(other.Name)
            self._process_events(60)
            self.assertFalse(Gui.Control.activeDialog())
            Gui.doCommandSkip(probe)
            self._process_events()
            App.setActiveDocument(self.document.Name)
            self._process_events(60)
            self.assertTrue(Gui.Control.activeDialog())

        try:
            with tempfile.TemporaryDirectory(
                prefix="vibecad-task-document-switch-"
            ) as directory:
                directory = Path(directory)

                canceled_path = directory / "SwitchedCanceledTask.FCMacro"
                self._start_macro_recording(
                    directory,
                    "SwitchedCanceledTask",
                )
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(body)
                Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
                self._process_events(60)
                self.assertTrue(Gui.Control.activeDialog())
                canceled_name = self.document.ActiveObject.Name
                cancel_probe = (
                    "vibecad_other_document_cancel_trace_probe = True"
                )
                switch_and_emit(cancel_probe)
                self._cancel_task()
                canceled_macro = self._stop_macro_recording(canceled_path)

                self.assertIn(cancel_probe, canceled_macro)
                self.assertEqual(canceled_macro.count(cancel_probe), 1)
                self.assertNotIn(canceled_name, canceled_macro)
                self.assertNotIn("PartDesign::AdditiveBox", canceled_macro)

                accepted_path = directory / "SwitchedAcceptedTask.FCMacro"
                self._start_macro_recording(
                    directory,
                    "SwitchedAcceptedTask",
                )
                Gui.Selection.clearSelection()
                Gui.Selection.addSelection(body)
                Gui.runCommand("PartDesign_CompPrimitiveAdditive", 0)
                self._process_events(60)
                self.assertTrue(Gui.Control.activeDialog())
                accepted = self.document.ActiveObject
                accept_probe = (
                    "vibecad_other_document_accept_trace_probe = True"
                )
                switch_and_emit(accept_probe)
                self._accept_task()
                accepted_macro = self._stop_macro_recording(accepted_path)

            self.assertIn(accept_probe, accepted_macro)
            self.assertEqual(accepted_macro.count(accept_probe), 1)
            self.assertIn(accepted.Name, accepted_macro)
            self.assertEqual(
                accepted_macro.count("PartDesign::AdditiveBox"),
                1,
                accepted_macro,
            )
        finally:
            if App.getDocument(other.Name) is not None:
                App.closeDocument(other.Name)
            if App.getDocument(self.document.Name) is not None:
                App.setActiveDocument(self.document.Name)
            self._process_events(60)

    def test_synchronous_explicit_trace_has_no_command_fallback(self):
        body, _box = self._new_box_body(
            "MacroBinderBody",
            "MacroBinderBodyResult",
        )
        source = self.document.addObject(
            "Part::Feature",
            "MacroBinderSource",
        )
        source.Shape = Part.makeBox(
            6,
            7,
            8,
            App.Vector(20, 0, 0),
        )
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(source, "Face1")
        self._process_events()
        self.assertTrue(Gui.isCommandActive("PartDesign_SubShapeBinder"))
        self.document.clearUndos()
        self.document.UndoMode = False

        with tempfile.TemporaryDirectory(
            prefix="vibecad-sync-command-macro-"
        ) as directory:
            directory = Path(directory)
            path = directory / "SynchronousExplicitTrace.FCMacro"
            self._start_macro_recording(
                directory,
                "SynchronousExplicitTrace",
            )
            Gui.runCommand("PartDesign_SubShapeBinder", 0)
            self._process_events(60)
            macro = self._stop_macro_recording(path)

        binder = self.document.ActiveObject
        self.assertEqual(binder.TypeId, "PartDesign::SubShapeBinder")
        self.assertEqual(
            macro.count("PartDesign::SubShapeBinder"),
            1,
            macro,
        )
        self.assertNotIn(
            "Gui.runCommand('PartDesign_SubShapeBinder'",
            macro,
        )
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(0, self.document.getBookedTransactionID())
        self.assertFalse(self.document.UndoMode)
        self.assertEqual(0, self.document.UndoCount)

    def test_nested_group_trace_has_no_parent_or_child_fallback(self):
        body = self.document.addObject(
            "PartDesign::Body",
            "MacroDatumBody",
        )
        self.document.recompute()
        Gui.activeView().setActiveObject("pdbody", body)
        Gui.Selection.clearSelection()
        self._process_events()
        self.assertTrue(Gui.isCommandActive("PartDesign_CompDatums"))

        with tempfile.TemporaryDirectory(
            prefix="vibecad-group-command-macro-"
        ) as directory:
            directory = Path(directory)
            path = directory / "NestedGroupTrace.FCMacro"
            self._start_macro_recording(directory, "NestedGroupTrace")
            undo_before_launch = self.document.UndoCount
            Gui.runCommand("PartDesign_CompDatums", 0)
            self._process_events(60)
            self.assertTrue(Gui.Control.activeDialog())
            datum = self.document.ActiveObject
            self.assertEqual(datum.TypeId, "PartDesign::Plane")
            datum_name = datum.Name
            undo_before_accept = self.document.UndoCount
            self._accept_next_modal()
            self._accept_task()
            macro = self._stop_macro_recording(path)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertIsNone(Gui.activeDocument().getInEdit())
        self.assertIs(self.document.getObject(datum_name), datum)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(0, self.document.getBookedTransactionID())
        self.assertTrue(body.Visibility)
        # The live task journal is already included in UndoCount.  Accept
        # converts that same journal into one durable entry; it does not create
        # a second record.
        self.assertEqual(self.document.UndoCount, undo_before_accept)
        self.assertEqual(self.document.UndoCount, undo_before_launch + 1)
        self.assertEqual(
            macro.count("PartDesign::Plane"),
            1,
            macro,
        )
        self.assertNotIn(
            "Gui.runCommand('PartDesign_CompDatums'",
            macro,
        )
        self.assertNotIn(
            "Gui.runCommand('PartDesign_Plane'",
            macro,
        )
        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(datum_name))

if __name__ == "__main__":
    unittest.main()
