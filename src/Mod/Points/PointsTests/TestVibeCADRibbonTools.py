# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD lifecycle contracts for native Points commands on Mesh."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import Points
from PySide import QtCore, QtGui

POINTS_COMMANDS = (
    "Points_Import",
    "Points_Export",
    "Points_Convert",
    "Points_Structure",
    "Points_Merge",
    "Points_PolyCut",
)

OPERATION_COMMANDS = {
    "Points_Import",
    "Points_Convert",
    "Points_Structure",
    "Points_Merge",
}

IN_PLACE_COMMANDS = {"Points_PolyCut"}
READ_ONLY_COMMANDS = {"Points_Export"}


@unittest.skipIf(not App.GuiUp, "VibeCAD Points ribbon tests require the GUI")
class TestVibeCADPointsRibbonTools(unittest.TestCase):
    """Every shipped point-cloud tool must own one exact lifecycle."""

    def setUp(self):
        Gui.activateWorkbench("MeshWorkbench")
        self._process_events()
        self.documents = []
        self.document = self._new_document("VibeCADPointsRibbon")
        self.first = self._add_points(
            self.document,
            "FirstCloud",
            (
                (0.0, 0.0, 0.0),
                (4.0, 0.0, 0.0),
                (0.0, 4.0, 0.0),
                (4.0, 4.0, 0.0),
            ),
        )
        self.second = self._add_points(
            self.document,
            "SecondCloud",
            (
                (10.0, 0.0, 0.0),
                (14.0, 0.0, 0.0),
                (10.0, 4.0, 0.0),
                (14.0, 4.0, 0.0),
            ),
        )
        self.box = self.document.addObject("Part::Feature", "BoxSource")
        self.box.Shape = Part.makeBox(5.0, 4.0, 3.0)
        self.second_box = self.document.addObject(
            "Part::Feature",
            "SecondBoxSource",
        )
        self.second_box.Shape = Part.makeBox(
            3.0,
            3.0,
            3.0,
            App.Vector(12.0, 0.0, 0.0),
        )
        self.document.recompute()
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
        Gui.activateView("Gui::View3DInventor", True)
        self._process_events()
        return document

    @staticmethod
    def _add_points(document, name, coordinates):
        kernel = Points.Points()
        kernel.addPoints([App.Vector(*coordinate) for coordinate in coordinates])
        feature = document.addObject("Points::Feature", name)
        feature.Points = kernel
        return feature

    def _select(self, *objects):
        Gui.Selection.clearSelection()
        for obj in objects:
            Gui.Selection.addSelection(obj)
        self._process_events()

    def _accept_input_dialog(self, value, before_accept=None):
        attempts = {"remaining": 200}

        def accept():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QInputDialog):
                    continue
                if not widget.isVisible():
                    continue
                if before_accept:
                    before_accept()
                widget.setDoubleValue(float(value))
                widget.accept()
                return
            attempts["remaining"] -= 1
            if attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, accept)

        QtCore.QTimer.singleShot(0, accept)

    def _accept_file_dialog(self, path):
        path = Path(path)
        attempts = {"remaining": 1000}

        def accept():
            for widget in QtGui.QApplication.topLevelWidgets():
                if not isinstance(widget, QtGui.QFileDialog):
                    continue
                if not widget.isVisible():
                    continue
                widget.setDirectory(str(path.parent))
                file_name = widget.findChild(
                    QtGui.QLineEdit,
                    "fileNameEdit",
                )
                if file_name is None:
                    continue
                file_name.setText(path.name)
                widget.accept()
                return
            attempts["remaining"] -= 1
            if attempts["remaining"] > 0:
                QtCore.QTimer.singleShot(5, accept)

        QtCore.QTimer.singleShot(0, accept)

    def _run_with_non_native_dialog(self, command_name):
        preferences = App.ParamGet("User parameter:BaseApp/Preferences/Dialog")
        original = preferences.GetBool("DontUseNativeDialog", False)
        try:
            preferences.SetBool("DontUseNativeDialog", True)
            Gui.runCommand(command_name, 0)
            self._process_events(15)
        finally:
            preferences.SetBool("DontUseNativeDialog", original)

    def _assert_single_operation(self, result):
        self.assertEqual(result.VibeCADTimelineRole, "operation")
        self.assertNotIn(
            "VibeCADTimelineReplacedInputs",
            result.PropertiesList,
        )
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertIsNotNone(timeline)
        self.assertEqual(list(timeline.Operations).count(result), 1)

    def test_exact_inventory_icons_and_contract_classification(self):
        commands = set(Gui.listCommands())
        self.assertFalse(set(POINTS_COMMANDS) - commands)
        for command_name in POINTS_COMMANDS:
            actions = Gui.Command.get(command_name).getAction()
            self.assertTrue(actions, command_name)
            self.assertFalse(actions[0].icon().isNull(), command_name)
            self.assertFalse(
                actions[0].icon().pixmap(24, 24).isNull(),
                command_name,
            )

        contracts = (
            OPERATION_COMMANDS,
            IN_PLACE_COMMANDS,
            READ_ONLY_COMMANDS,
        )
        self.assertEqual(set().union(*contracts), set(POINTS_COMMANDS))
        for index, contract in enumerate(contracts):
            for other in contracts[index + 1 :]:
                self.assertFalse(contract & other)

    def test_mutators_refuse_caller_owned_work_but_export_remains_read_only(self):
        selections = {
            "Points_Convert": (self.box,),
            "Points_Structure": (self.first,),
            "Points_Merge": (self.first, self.second),
            "Points_PolyCut": (self.first,),
        }
        for command_name, selection in selections.items():
            self._select(*selection)
            self.assertTrue(Gui.isCommandActive(command_name), command_name)
            self.document.openTransaction("Caller owned")
            transaction = self.document.getBookedTransactionID()
            self.assertFalse(Gui.isCommandActive(command_name), command_name)
            Gui.runCommand(command_name, 0)
            self._process_events()
            self.assertEqual(
                self.document.getBookedTransactionID(),
                transaction,
                command_name,
            )
            App.closeActiveTransaction(True, transaction)

        self.assertTrue(Gui.isCommandActive("Points_Import"))
        self.document.openTransaction("Caller owned import")
        transaction = self.document.getBookedTransactionID()
        self.assertFalse(Gui.isCommandActive("Points_Import"))
        App.closeActiveTransaction(True, transaction)

        self._select(self.first)
        self.document.openTransaction("Caller owned export")
        transaction = self.document.getBookedTransactionID()
        self.assertTrue(Gui.isCommandActive("Points_Export"))
        App.closeActiveTransaction(True, transaction)

    def test_merge_is_one_source_preserving_undoable_operation(self):
        self._select(self.first, self.second)
        objects_before = set(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Points_Merge", 0)
        self._process_events(8)

        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "Points::Feature")
        self.assertEqual(result.Points.CountPoints, 8)
        self.assertEqual(list(result.Sources), [self.first, self.second])
        self._assert_single_operation(result)
        self.assertTrue(self.first.Visibility)
        self.assertTrue(self.second.Visibility)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

        result_name = result.Name
        self.document.undo()
        self._process_events()
        self.assertIsNone(self.document.getObject(result_name))
        self.document.redo()
        self._process_events()
        restored = self.document.getObject(result_name)
        self.assertIsNotNone(restored)
        self.assertEqual(
            list(restored.Sources),
            [self.first, self.second],
        )
        self._assert_single_operation(restored)

    def test_structure_is_one_linked_grid_operation(self):
        self._select(self.first)
        objects_before = set(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Points_Structure", 0)
        self._process_events(8)

        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        self.assertEqual(len(created), 1)
        result = created[0]
        self.assertEqual(result.TypeId, "Points::Structured")
        self.assertIs(result.Source, self.first)
        self.assertEqual((result.Width, result.Height), (2, 2))
        self.assertEqual(result.Points.CountPoints, 4)
        self._assert_single_operation(result)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

    def test_convert_uses_launch_selection_and_groups_batch_as_one_operation(self):
        self._select(self.box, self.second_box)
        objects_before = set(self.document.Objects)
        undo_before = self.document.UndoCount
        self._accept_input_dialog(
            0.5,
            before_accept=lambda: self._select(self.first),
        )
        Gui.runCommand("Points_Convert", 0)
        self._process_events(15)

        created = [
            obj
            for obj in self.document.Objects
            if obj not in objects_before and obj.TypeId != "App::DocumentTimeline"
        ]
        outputs = [obj for obj in created if obj.TypeId == "Points::Feature"]
        controllers = [obj for obj in created if obj.TypeId == "Mesh::OutputGroup"]
        self.assertEqual(len(outputs), 2)
        self.assertEqual(len(controllers), 1)
        controller = controllers[0]
        self.assertEqual(
            list(controller.Sources),
            [self.box, self.second_box],
        )
        self.assertEqual(set(controller.Group), set(outputs))
        self.assertEqual(controller.VibeCADTimelineRole, "operation")
        self.assertEqual(
            {output.Source for output in outputs},
            {self.box, self.second_box},
        )
        for output in outputs:
            self.assertGreater(output.Points.CountPoints, 0)
            self.assertEqual(output.VibeCADTimelineRole, "resource")
            self.assertIs(output.VibeCADTimelineOwner, controller)
        timeline = self.document.getObject("VibeCADTimeline")
        self.assertEqual(list(timeline.Operations).count(controller), 1)
        self.assertEqual(self.document.UndoCount, undo_before + 1)

    def test_import_is_portable_standalone_history_and_export_is_read_only(self):
        with TemporaryDirectory() as temporary_directory:
            source = Path(temporary_directory) / "portable-points.asc"
            source.write_text(
                "0 0 0\n4 0 0\n0 4 0\n4 4 0\n",
                encoding="utf-8",
            )
            objects_before = set(self.document.Objects)
            undo_before = self.document.UndoCount
            self._accept_file_dialog(source)
            self._run_with_non_native_dialog("Points_Import")

            created = [
                obj
                for obj in self.document.Objects
                if obj not in objects_before and obj.TypeId == "Points::Feature"
            ]
            self.assertEqual(len(created), 1)
            imported = created[0]
            self.assertEqual(imported.Points.CountPoints, 4)
            self.assertEqual(
                list(imported.VibeCADExternalInputs),
                [source.name],
            )
            self._assert_single_operation(imported)
            self.assertEqual(self.document.UndoCount, undo_before + 1)

            exported = Path(temporary_directory) / "roundtrip.asc"
            self._select(imported)
            objects_before_export = tuple(self.document.Objects)
            undo_before_export = self.document.UndoCount
            self._accept_file_dialog(exported)
            self._run_with_non_native_dialog("Points_Export")
            self.assertTrue(exported.exists())
            self.assertEqual(tuple(self.document.Objects), objects_before_export)
            self.assertEqual(self.document.UndoCount, undo_before_export)

    def test_linked_operation_survives_save_reopen_with_history_identity(self):
        self._select(self.first, self.second)
        Gui.runCommand("Points_Merge", 0)
        self._process_events(8)
        result = next(
            obj
            for obj in self.document.Objects
            if obj.TypeId == "Points::Feature" and "Sources" in obj.PropertiesList
        )
        result_name = result.Name
        first_name = self.first.Name
        second_name = self.second.Name

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "points-history.FCStd"
            self.document.recompute()
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            reopened = App.openDocument(str(path))
            self.document = reopened
            if document_name != reopened.Name:
                self.documents.remove(document_name)
                self.documents.append(reopened.Name)
            self._process_events(10)

            restored = reopened.getObject(result_name)
            self.assertIsNotNone(restored)
            self.assertEqual(
                [source.Name for source in restored.Sources],
                [first_name, second_name],
            )
            timeline = reopened.getObject("VibeCADTimeline")
            self.assertIsNotNone(timeline)
            self.assertEqual(list(timeline.Operations).count(restored), 1)
            self.assertEqual(restored.VibeCADTimelineRole, "operation")

    def test_polycut_editor_can_be_closed_with_its_document_without_mutation(self):
        self._select(self.first, self.second)
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        Gui.runCommand("Points_PolyCut", 0)
        self._process_events(8)

        self.assertFalse(Gui.isCommandActive("Points_PolyCut"))
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(self.document.UndoCount, undo_before)

        document_name = self.document.Name
        App.closeDocument(document_name)
        self.documents.remove(document_name)
        self.document = self._new_document("VibeCADPointsAfterCutClose")
        self.assertIsNotNone(Gui.activeDocument())
        self.assertFalse(self.document.HasPendingTransaction)

    def test_hidden_empty_and_cross_document_targets_are_not_interactive(self):
        self.first.Visibility = False
        self._select(self.first)
        self.assertFalse(Gui.isCommandActive("Points_PolyCut"))
        self.first.Visibility = True

        empty = self._add_points(self.document, "EmptyCloud", ())
        self.document.recompute()
        self._select(empty)
        for command_name in (
            "Points_Export",
            "Points_Structure",
            "Points_PolyCut",
        ):
            self.assertFalse(Gui.isCommandActive(command_name), command_name)

        other = self._new_document("VibeCADPointsOther")
        other_points = self._add_points(
            other,
            "OtherCloud",
            ((0.0, 0.0, 0.0),),
        )
        other.recompute()
        App.setActiveDocument(self.document.Name)
        self._select(self.first, other_points)
        self.assertFalse(Gui.isCommandActive("Points_Merge"))
        self.assertTrue(Gui.isCommandActive("Points_Export"))
        self.assertTrue(Gui.isCommandActive("Points_PolyCut"))
