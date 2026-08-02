# SPDX-License-Identifier: LGPL-2.1-or-later

"""Lifecycle contracts for the VibeCAD Parameters ribbon."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui

SPREADSHEET_COMMANDS = (
    "Spreadsheet_CreateSheet",
    "Spreadsheet_Import",
    "Spreadsheet_Export",
    "Spreadsheet_MergeCells",
    "Spreadsheet_SplitCell",
    "Spreadsheet_CellProperties",
    "Spreadsheet_SetAlias",
    "Spreadsheet_AlignLeft",
    "Spreadsheet_AlignCenter",
    "Spreadsheet_AlignRight",
    "Spreadsheet_AlignTop",
    "Spreadsheet_AlignVCenter",
    "Spreadsheet_AlignBottom",
    "Spreadsheet_StyleBold",
    "Spreadsheet_StyleItalic",
    "Spreadsheet_StyleUnderline",
)

MUTATING_COMMANDS = tuple(
    command for command in SPREADSHEET_COMMANDS if command != "Spreadsheet_Export"
)


@unittest.skipIf(
    not App.GuiUp,
    "VibeCAD Parameters ribbon tests require the GUI",
)
class TestVibeCADSpreadsheetRibbonTools(unittest.TestCase):
    """Parameters tools target one exact sheet and one exact undo step."""

    def setUp(self):
        Gui.activateWorkbench("SpreadsheetWorkbench")
        self._process_events()
        self.documents = []
        self.document = self._new_document("VibeCADParameters")
        self.sheet = self._create_sheet()
        self.sheet.ViewObject.doubleClicked()
        self._process_events(8)
        self.view = self.sheet.ViewObject.getView()
        self.assertIsNotNone(self.view)
        self.content_editor = Gui.getMainWindow().findChild(
            QtGui.QLineEdit,
            "cellContent",
        )
        self.assertIsNotNone(
            self.content_editor,
            "The active spreadsheet must expose its content editor",
        )
        self.document.clearUndos()

    def tearDown(self):
        Gui.Selection.clearSelection()
        for document_name in reversed(self.documents):
            document = App.listDocuments().get(document_name)
            if document is None:
                continue
            transaction = document.getBookedTransactionID()
            if transaction:
                App.closeActiveTransaction(True, transaction)
            App.closeDocument(document_name)
        self.documents = []
        self.document = None
        self.sheet = None
        self.view = None
        self.content_editor = None
        self._process_events()

    @staticmethod
    def _process_events(rounds=5):
        for _ in range(rounds):
            Gui.updateGui()
            QtGui.QApplication.processEvents(
                QtCore.QEventLoop.AllEvents,
                25,
            )

    def _new_document(self, name):
        document = App.newDocument(name)
        document.UndoMode = True
        self.documents.append(document.Name)
        App.setActiveDocument(document.Name)
        self._process_events()
        return document

    def _create_sheet(self):
        before = set(self.document.Objects)
        Gui.runCommand("Spreadsheet_CreateSheet", 0)
        self._process_events(8)
        created = [
            obj
            for obj in self.document.Objects
            if obj not in before and obj.TypeId == "Spreadsheet::Sheet"
        ]
        self.assertEqual(len(created), 1)
        return created[0]

    def _select(self, address):
        endpoints = address.split(":", 1)
        if len(endpoints) == 1:
            endpoints.append(endpoints[0])

        self.view.setCurrentIndex(endpoints[0])
        flags = QtCore.QItemSelectionModel.ClearAndSelect.value
        if endpoints[0] == endpoints[1]:
            self.view.select(endpoints[0], flags)
        else:
            self.view.select(endpoints[0], endpoints[1], flags)
        self._process_events()

    def _set_cell_through_editor(self, address, value):
        self._select(address)
        self.assertTrue(self.content_editor.isEnabled())
        self.content_editor.setText(value)
        self.content_editor.returnPressed.emit()
        self._process_events(12)

    @staticmethod
    def _answer_properties(alias=None, accept=True):
        attempts = {"remaining": 300}

        def answer():
            for widget in QtGui.QApplication.topLevelWidgets():
                if (
                    not widget.isVisible()
                    or "PropertiesDialog" not in widget.metaObject().className()
                ):
                    continue
                if alias is not None:
                    editor = widget.findChild(QtGui.QLineEdit, "alias")
                    if editor is not None:
                        editor.setText(alias)
                        editor.textEdited.emit(alias)
                if accept:
                    widget.accept()
                else:
                    widget.reject()
                return
            attempts["remaining"] -= 1
            if attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, answer)

        QtCore.QTimer.singleShot(0, answer)

    @staticmethod
    def _answer_file_dialog(path, save=False):
        path = Path(path)
        attempts = {"remaining": 600}

        def answer():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QFileDialog):
                    continue
                if not widget.isVisible():
                    continue
                widget.setDirectory(str(path.parent))
                editor = widget.findChild(
                    QtGui.QLineEdit,
                    "fileNameEdit",
                )
                if editor is None:
                    continue
                editor.setText(path.name)
                widget.accept()
                return
            attempts["remaining"] -= 1
            if attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, answer)

        QtCore.QTimer.singleShot(0, answer)

    def _run_with_non_native_dialog(self, command_name):
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Dialog")
        original = preferences.GetBool("DontUseNativeDialog", False)
        try:
            preferences.SetBool("DontUseNativeDialog", True)
            Gui.runCommand(command_name, 0)
            self._process_events(15)
        finally:
            preferences.SetBool("DontUseNativeDialog", original)

    def test_inventory_icons_and_contract_classification(self):
        registered = set(Gui.listCommands())
        self.assertFalse(set(SPREADSHEET_COMMANDS) - registered)
        for command_name in SPREADSHEET_COMMANDS:
            actions = Gui.Command.get(command_name).getAction()
            self.assertTrue(actions, command_name)
            self.assertTrue(
                all(not action.icon().pixmap(24, 24).isNull() for action in actions),
                command_name,
            )
        self.assertEqual(
            set(MUTATING_COMMANDS) | {"Spreadsheet_Export"},
            set(SPREADSHEET_COMMANDS),
        )

    def test_create_is_one_history_operation_with_exact_undo(self):
        document = self._new_document("VibeCADParametersCreate")
        self.document = document
        undo_before = document.UndoCount
        sheet = self._create_sheet()
        timeline = document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertEqual(sheet.VibeCADTimelineRole, "operation")
        self.assertEqual(list(timeline.Operations).count(sheet), 1)
        self.assertEqual(document.UndoCount, undo_before + 1)
        name = sheet.Name
        document.undo()
        self._process_events()
        self.assertIsNone(document.getObject(name))
        document.redo()
        self._process_events()
        self.assertIsNotNone(document.getObject(name))

    def test_cell_edit_alias_and_model_expression_are_exact(self):
        box = self.document.addObject("Part::Box", "DrivenBox")
        self.document.recompute()
        self.document.clearUndos()

        self._set_cell_through_editor("A1", "12 mm")
        self.assertEqual(self.document.UndoCount, 1)
        self.assertEqual(self.sheet.getContents("A1"), "=12 mm")

        self._select("A1")
        self._answer_properties(alias="width")
        Gui.runCommand("Spreadsheet_SetAlias", 0)
        self._process_events(8)
        self.assertEqual(self.sheet.getAlias("A1"), "width")
        self.assertEqual(self.document.UndoCount, 2)

        box.setExpression("Length", f"{self.sheet.Name}.width")
        self.document.recompute()
        self.assertAlmostEqual(box.Length.Value, 12.0)
        self._set_cell_through_editor("A1", "18 mm")
        self.assertAlmostEqual(box.Length.Value, 18.0)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.undo()
        self._process_events()
        self.assertAlmostEqual(box.Length.Value, 12.0)
        self.document.redo()
        self._process_events()
        self.assertAlmostEqual(box.Length.Value, 18.0)

    def test_formatting_commands_are_single_undoable_edits(self):
        for address, value in (
            ("A1", "1"),
            ("A2", "2"),
            ("B1", "3"),
            ("B2", "4"),
        ):
            self.sheet.set(address, value)
        self.document.recompute()
        self.document.clearUndos()
        self._select("A1:B2")

        alignment_cases = (
            ("Spreadsheet_AlignLeft", "left"),
            ("Spreadsheet_AlignCenter", "center"),
            ("Spreadsheet_AlignRight", "right"),
            ("Spreadsheet_AlignTop", "top"),
            ("Spreadsheet_AlignVCenter", "vcenter"),
            ("Spreadsheet_AlignBottom", "bottom"),
        )
        for command_name, expected in alignment_cases:
            with self.subTest(command=command_name):
                undo_before = self.document.UndoCount
                self.assertTrue(Gui.isCommandActive(command_name))
                Gui.runCommand(command_name, 0)
                self._process_events()
                self.assertIn(expected, self.sheet.getAlignment("A1"))
                self.assertEqual(
                    self.document.UndoCount,
                    undo_before + 1,
                )

        style_cases = (
            ("Spreadsheet_StyleBold", "bold"),
            ("Spreadsheet_StyleItalic", "italic"),
            ("Spreadsheet_StyleUnderline", "underline"),
        )
        for command_name, expected in style_cases:
            with self.subTest(command=command_name):
                undo_before = self.document.UndoCount
                Gui.runCommand(command_name, 0)
                self._process_events()
                self.assertIn(expected, self.sheet.getStyle("A1"))
                self.assertEqual(
                    self.document.UndoCount,
                    undo_before + 1,
                )
                Gui.runCommand(command_name, 0)
                self._process_events()
                self.assertNotIn(
                    expected,
                    self.sheet.getStyle("A1") or set(),
                )

    def test_merge_split_and_properties_cancel_do_not_leak_state(self):
        self.sheet.set("A1", "kept")
        self.document.recompute()
        self.document.clearUndos()
        self._select("A1:B2")
        self.assertEqual(self.view.selectedRanges(), ["A1:B2"])
        self.assertTrue(Gui.isCommandActive("Spreadsheet_MergeCells"))
        Gui.runCommand("Spreadsheet_MergeCells", 0)
        self._process_events()
        self.assertEqual(self.sheet.getCellMerge("A1"), ("A1", 2, 2))
        self.assertEqual(self.document.UndoCount, 1)

        self._select("A1")
        Gui.runCommand("Spreadsheet_SplitCell", 0)
        self._process_events()
        self.assertEqual(self.sheet.getCellMerge("A1"), ("A1", 1, 1))
        self.assertEqual(self.document.UndoCount, 2)

        self._select("Z99")
        before = (
            self.document.UndoCount,
            self.sheet.getContents("Z99"),
        )
        self._answer_properties(accept=False)
        Gui.runCommand("Spreadsheet_CellProperties", 0)
        self._process_events()
        self.assertEqual(
            (
                self.document.UndoCount,
                self.sheet.getContents("Z99"),
            ),
            before,
        )
        self.assertFalse(self.document.HasPendingTransaction)

    def test_import_export_are_portable_and_export_is_read_only(self):
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/Spreadsheet")
        old_delimiter = preferences.GetString(
            "ImportExportDelimiter",
            "tab",
        )
        try:
            preferences.SetString("ImportExportDelimiter", "comma")
            with TemporaryDirectory() as temporary_directory:
                source = Path(temporary_directory) / "dimensions.csv"
                source.write_text(
                    "name,value\nwidth,25\nheight,12\n",
                    encoding="utf-8",
                )
                before = set(self.document.Objects)
                undo_before = self.document.UndoCount
                self._answer_file_dialog(source)
                self._run_with_non_native_dialog("Spreadsheet_Import")
                imported = [
                    obj
                    for obj in self.document.Objects
                    if obj not in before and obj.TypeId == "Spreadsheet::Sheet"
                ]
                self.assertEqual(len(imported), 1)
                imported_sheet = imported[0]
                self.assertEqual(imported_sheet.get("A1"), "name")
                self.assertEqual(imported_sheet.VibeCADTimelineRole, "operation")
                self.assertEqual(
                    self.document.UndoCount,
                    undo_before + 1,
                )

                imported_sheet.ViewObject.doubleClicked()
                self._process_events()
                export_path = Path(temporary_directory) / "roundtrip.csv"
                export_undo = self.document.UndoCount
                self._answer_file_dialog(export_path, save=True)
                self._run_with_non_native_dialog("Spreadsheet_Export")
                self.assertTrue(export_path.is_file())
                self.assertIn("width", export_path.read_text(encoding="utf-8"))
                self.assertEqual(self.document.UndoCount, export_undo)
        finally:
            preferences.SetString(
                "ImportExportDelimiter",
                old_delimiter,
            )

    def test_mutators_refuse_caller_owned_transactions(self):
        self.sheet.set("A1", "1")
        self.document.recompute()
        self._select("A1")
        self.assertTrue(Gui.isCommandActive("Spreadsheet_Export"))
        self.document.openTransaction("Caller owned")
        transaction = self.document.getBookedTransactionID()
        for command_name in MUTATING_COMMANDS:
            with self.subTest(command=command_name):
                self.assertFalse(
                    Gui.isCommandActive(command_name),
                    command_name,
                )
        self.assertFalse(Gui.isCommandActive("Spreadsheet_Export"))
        self.assertEqual(
            self.document.getBookedTransactionID(),
            transaction,
        )
        App.closeActiveTransaction(True, transaction)

    def test_save_reopen_preserves_alias_dependency_and_history(self):
        self.sheet.set("A1", "32 mm")
        self.sheet.setAlias("A1", "width")
        box = self.document.addObject("Part::Box", "DrivenBox")
        box.setExpression("Length", f"{self.sheet.Name}.width")
        self.document.recompute()

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "parameters.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            sheet_name = self.sheet.Name
            box_name = box.Name
            App.closeDocument(document_name)
            reopened = App.openDocument(str(path))
            if reopened.Name != document_name:
                self.documents.remove(document_name)
                self.documents.append(reopened.Name)
            self.document = reopened
            self.sheet = reopened.getObject(sheet_name)
            self._process_events(10)

            restored_box = reopened.getObject(box_name)
            timeline = reopened.getObject("VibeCADTimeline")
            self.assertIsNotNone(self.sheet)
            self.assertIsNotNone(restored_box)
            self.assertIsNotNone(timeline)
            self.assertEqual(self.sheet.getAlias("A1"), "width")
            self.assertAlmostEqual(restored_box.Length.Value, 32.0)
            self.assertEqual(self.sheet.VibeCADTimelineRole, "operation")
            self.assertEqual(list(timeline.Operations).count(self.sheet), 1)
            self.assertFalse(reopened.HasPendingTransaction)
