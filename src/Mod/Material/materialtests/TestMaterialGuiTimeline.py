# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI contracts for material and appearance property edits."""

from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import FreeCAD as App
import FreeCADGui as Gui
import MatGui  # noqa: F401 - registers the GUI commands
from PySide import QtCore, QtWidgets


DOCUMENT_IN_PLACE_COMMANDS = {
    "Std_SetAppearance",
    "Std_SetMaterial",
}

READ_ONLY_COMMANDS = {
    "Materials_InspectAppearance",
    "Materials_InspectMaterial",
}

LIBRARY_COMMANDS = {
    "Material_Edit",
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


def _material_indexes(tree):
    model = tree.model()
    indexes = []

    def visit(parent=QtCore.QModelIndex()):
        for row in range(model.rowCount(parent)):
            index = model.index(row, 0, parent)
            uuid = str(index.data(QtCore.Qt.UserRole) or "")
            flags = index.flags()
            if (
                uuid
                and flags & QtCore.Qt.ItemIsEnabled
                and flags & QtCore.Qt.ItemIsSelectable
            ):
                indexes.append((uuid, index))
            visit(index)

    visit()
    return indexes


def _select_material(tree, index):
    tree.selectionModel().setCurrentIndex(
        index,
        QtCore.QItemSelectionModel.ClearAndSelect,
    )
    tree.scrollTo(index)


class MaterialGuiTimelineTest(unittest.TestCase):
    """Appearance is one target-stable in-place transaction."""

    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("MaterialGuiTimeline")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)
        self.documents = [self.document.Name]
        self.temporary_directories = []
        self.first = self.document.addObject("Part::Box", "First")
        self.second = self.document.addObject("Part::Box", "Second")
        self.second.Placement.Base.x = 20
        self.document.recompute()
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
        for temporary_directory in self.temporary_directories:
            temporary_directory.cleanup()

    def _create_linked_definition(self):
        source = App.newDocument("MaterialDefinition")
        self.documents.append(source.Name)
        definition = source.addObject("Part::Box", "Definition")
        source.recompute()
        temporary_directory = TemporaryDirectory()
        self.temporary_directories.append(temporary_directory)
        source.saveAs(
            str(Path(temporary_directory.name) / "definition.FCStd"),
        )
        self.document.saveAs(
            str(Path(temporary_directory.name) / "occurrence.FCStd"),
        )

        occurrence = self.document.addObject("App::Link", "DefinitionOccurrence")
        occurrence.LinkedObject = definition
        self.document.recompute()
        App.setActiveDocument(self.document.Name)
        _update_gui()
        return source, definition, occurrence

    def test_material_command_history_contracts_are_complete_and_disjoint(self):
        contracts = (
            DOCUMENT_IN_PLACE_COMMANDS,
            READ_ONLY_COMMANDS,
            LIBRARY_COMMANDS,
        )
        expected = set().union(*contracts)
        self.assertFalse(expected - set(Gui.listCommands()))
        for index, contract in enumerate(contracts):
            for other in contracts[index + 1 :]:
                self.assertFalse(contract & other)

    def test_material_editors_refuse_a_caller_owned_transaction(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.first)
        self.document.openTransaction("Caller owned")
        transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(transaction_id, 0)
        try:
            for command_name in (
                "Std_SetAppearance",
                "Std_SetMaterial",
            ):
                Gui.runCommand(command_name, 0)
                _update_gui(3)
                self.assertFalse(
                    Gui.Control.activeDialog(),
                    command_name,
                )
                self.assertEqual(
                    self.document.getBookedTransactionID(),
                    transaction_id,
                    command_name,
                )
        finally:
            self.document.abortTransaction()

        self.assertFalse(self.document.HasPendingTransaction)

    def test_physical_material_task_keeps_its_launch_target_and_cancels_atomically(
        self,
    ):
        self.document.UndoMode = False
        controller = _timeline(self.document)
        operations_before = tuple(controller.Operations)
        undo_before = self.document.UndoCount
        first_before = str(self.first.ShapeMaterial.UUID)
        second_before = str(self.second.ShapeMaterial.UUID)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.first)
        Gui.runCommand("Std_SetMaterial", 0)
        _update_gui(8)
        self.assertTrue(Gui.Control.activeDialog())
        material_widget = Gui.getMainWindow().findChild(
            QtWidgets.QWidget,
            "widgetMaterial",
        )
        self.assertIsNotNone(material_widget)
        tree = material_widget.findChild(QtWidgets.QTreeView)
        self.assertIsNotNone(tree)
        alternatives = [
            (uuid, index)
            for uuid, index in _material_indexes(tree)
            if uuid not in {first_before, second_before}
        ]
        if len(alternatives) < 2:
            Gui.Control.activeTaskDialog().reject()
            self.skipTest("The installed physical-material catalog has fewer than two alternatives")

        first_uuid, first_index = alternatives[0]
        _select_material(tree, first_index)
        _update_gui(5)
        self.assertEqual(str(self.first.ShapeMaterial.UUID), first_uuid)
        self.assertEqual(str(self.second.ShapeMaterial.UUID), second_before)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.second)
        second_uuid, second_index = alternatives[1]
        _select_material(tree, second_index)
        _update_gui(5)
        self.assertEqual(str(self.first.ShapeMaterial.UUID), second_uuid)
        self.assertEqual(str(self.second.ShapeMaterial.UUID), second_before)

        Gui.Control.activeTaskDialog().reject()
        _update_gui(5)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(str(self.first.ShapeMaterial.UUID), first_before)
        self.assertEqual(str(self.second.ShapeMaterial.UUID), second_before)
        self.assertEqual(tuple(controller.Operations), operations_before)
        self.assertEqual(self.document.UndoCount, undo_before)
        self.assertFalse(self.document.UndoMode)
        self.assertFalse(self.document.HasPendingTransaction)

    def test_physical_material_accept_is_undoable_and_persistent(self):
        first_before = str(self.first.ShapeMaterial.UUID)
        second_before = str(self.second.ShapeMaterial.UUID)
        first_name = self.first.Name
        second_name = self.second.Name
        undo_before = self.document.UndoCount

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.first)
        Gui.runCommand("Std_SetMaterial", 0)
        _update_gui(8)
        self.assertTrue(Gui.Control.activeDialog())
        material_widget = Gui.getMainWindow().findChild(
            QtWidgets.QWidget,
            "widgetMaterial",
        )
        self.assertIsNotNone(material_widget)
        tree = material_widget.findChild(QtWidgets.QTreeView)
        self.assertIsNotNone(tree)
        alternatives = [
            (uuid, index)
            for uuid, index in _material_indexes(tree)
            if uuid not in {first_before, second_before}
        ]
        if not alternatives:
            Gui.Control.activeTaskDialog().reject()
            self.skipTest(
                "The installed physical-material catalog has no alternative",
            )

        accepted_uuid, accepted_index = alternatives[0]
        _select_material(tree, accepted_index)
        _update_gui(5)
        self.assertEqual(
            str(self.first.ShapeMaterial.UUID),
            accepted_uuid,
        )
        Gui.Control.activeTaskDialog().accept()
        _update_gui(5)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertEqual(str(self.second.ShapeMaterial.UUID), second_before)

        self.document.undo()
        _update_gui()
        self.assertEqual(str(self.first.ShapeMaterial.UUID), first_before)
        self.document.redo()
        _update_gui()
        self.assertEqual(str(self.first.ShapeMaterial.UUID), accepted_uuid)

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "physical-material.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            App.setActiveDocument(reopened.Name)
            _update_gui(5)

            self.assertEqual(
                str(reopened.getObject(first_name).ShapeMaterial.UUID),
                accepted_uuid,
            )
            self.assertEqual(
                str(reopened.getObject(second_name).ShapeMaterial.UUID),
                second_before,
            )

    def test_linked_physical_material_cancel_rolls_back_both_documents(self):
        source, definition, occurrence = self._create_linked_definition()
        material_before = str(definition.ShapeMaterial.UUID)
        occurrence_undo_before = self.document.UndoCount
        source_undo_before = source.UndoCount

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(occurrence)
        Gui.runCommand("Std_SetMaterial", 0)
        _update_gui(8)
        self.assertTrue(Gui.Control.activeDialog())
        transaction_id = self.document.getBookedTransactionID()
        self.assertNotEqual(transaction_id, 0)
        self.assertEqual(source.getBookedTransactionID(), transaction_id)

        material_widget = Gui.getMainWindow().findChild(
            QtWidgets.QWidget,
            "widgetMaterial",
        )
        self.assertIsNotNone(material_widget)
        tree = material_widget.findChild(QtWidgets.QTreeView)
        alternatives = [
            (uuid, index)
            for uuid, index in _material_indexes(tree)
            if uuid != material_before
        ]
        if not alternatives:
            Gui.Control.activeTaskDialog().reject()
            self.skipTest(
                "The installed physical-material catalog has no alternative",
            )

        changed_uuid, changed_index = alternatives[0]
        _select_material(tree, changed_index)
        _update_gui(5)
        self.assertEqual(str(definition.ShapeMaterial.UUID), changed_uuid)
        self.assertEqual(str(occurrence.ShapeMaterial.UUID), changed_uuid)
        self.assertTrue(self.document.HasPendingTransaction)
        self.assertTrue(source.HasPendingTransaction)
        self.assertNotIn("ShapeMaterial", occurrence.PropertiesList)

        Gui.Control.activeTaskDialog().reject()
        _update_gui(5)
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(str(definition.ShapeMaterial.UUID), material_before)
        self.assertEqual(self.document.UndoCount, occurrence_undo_before)
        self.assertEqual(source.UndoCount, source_undo_before)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(source.getBookedTransactionID(), 0)
        self.assertFalse(self.document.HasPendingTransaction)
        self.assertFalse(source.HasPendingTransaction)

    def test_linked_physical_material_accept_groups_undo_and_persists(self):
        source, definition, occurrence = self._create_linked_definition()
        material_before = str(definition.ShapeMaterial.UUID)
        definition_name = definition.Name
        occurrence_name = occurrence.Name
        occurrence_undo_before = self.document.UndoCount
        source_undo_before = source.UndoCount

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(occurrence)
        Gui.runCommand("Std_SetMaterial", 0)
        _update_gui(8)
        self.assertTrue(Gui.Control.activeDialog())
        material_widget = Gui.getMainWindow().findChild(
            QtWidgets.QWidget,
            "widgetMaterial",
        )
        self.assertIsNotNone(material_widget)
        tree = material_widget.findChild(QtWidgets.QTreeView)
        alternatives = [
            (uuid, index)
            for uuid, index in _material_indexes(tree)
            if uuid != material_before
        ]
        if not alternatives:
            Gui.Control.activeTaskDialog().reject()
            self.skipTest(
                "The installed physical-material catalog has no alternative",
            )

        accepted_uuid, accepted_index = alternatives[0]
        _select_material(tree, accepted_index)
        _update_gui(5)
        Gui.Control.activeTaskDialog().accept()
        _update_gui(5)

        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(str(definition.ShapeMaterial.UUID), accepted_uuid)
        self.assertEqual(self.document.UndoCount, occurrence_undo_before + 1)
        self.assertEqual(source.UndoCount, source_undo_before + 1)
        self.assertEqual(self.document.getBookedTransactionID(), 0)
        self.assertEqual(source.getBookedTransactionID(), 0)

        App.setActiveDocument(self.document.Name)
        Gui.runCommand("Std_Undo", 0)
        _update_gui(5)
        self.assertEqual(str(definition.ShapeMaterial.UUID), material_before)
        Gui.runCommand("Std_Redo", 0)
        _update_gui(5)
        self.assertEqual(str(definition.ShapeMaterial.UUID), accepted_uuid)

        with TemporaryDirectory() as temporary_directory:
            source_path = Path(temporary_directory) / "definition.FCStd"
            occurrence_path = Path(temporary_directory) / "occurrence.FCStd"
            source.saveAs(str(source_path))
            self.document.saveAs(str(occurrence_path))
            source_document_name = source.Name
            occurrence_document_name = self.document.Name

            App.closeDocument(occurrence_document_name)
            self.documents.remove(occurrence_document_name)
            App.closeDocument(source_document_name)
            self.documents.remove(source_document_name)

            reopened_source = App.openDocument(str(source_path))
            self.documents.append(reopened_source.Name)
            reopened_occurrence = App.openDocument(str(occurrence_path))
            self.documents.append(reopened_occurrence.Name)
            self.document = reopened_occurrence
            self.first = reopened_occurrence.getObject("First")
            self.second = reopened_occurrence.getObject("Second")
            App.setActiveDocument(reopened_occurrence.Name)
            _update_gui(8)

            reopened_definition = reopened_source.getObject(definition_name)
            reopened_link = reopened_occurrence.getObject(occurrence_name)
            self.assertEqual(
                str(reopened_definition.ShapeMaterial.UUID),
                accepted_uuid,
            )
            self.assertEqual(
                str(reopened_link.ShapeMaterial.UUID),
                accepted_uuid,
            )

    def test_appearance_is_target_stable_atomic_and_persistent(self):
        controller = _timeline(self.document)
        operations_before = tuple(controller.Operations)
        operation_names_before = tuple(
            operation.Name for operation in operations_before
        )
        objects_before = tuple(self.document.Objects)
        undo_before = self.document.UndoCount
        first_name = self.first.Name
        second_name = self.second.Name

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.first)
        Gui.runCommand("Std_SetAppearance", 0)
        _update_gui(5)
        self.assertTrue(Gui.Control.activeDialog())
        transparency = Gui.getMainWindow().findChild(
            QtWidgets.QSpinBox,
            "spinTransparency",
        )
        self.assertIsNotNone(transparency)

        transparency.setValue(35)
        _update_gui()
        self.assertEqual(self.first.ViewObject.Transparency, 35)
        self.assertEqual(self.second.ViewObject.Transparency, 0)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.second)
        transparency.setValue(65)
        _update_gui()
        self.assertEqual(self.first.ViewObject.Transparency, 65)
        self.assertEqual(self.second.ViewObject.Transparency, 0)

        Gui.Control.activeTaskDialog().reject()
        _update_gui()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(tuple(self.document.Objects), objects_before)
        self.assertEqual(tuple(controller.Operations), operations_before)
        self.assertEqual(self.document.UndoCount, undo_before + 1)
        self.assertFalse(self.document.HasPendingTransaction)

        self.document.undo()
        _update_gui()
        self.assertEqual(self.first.ViewObject.Transparency, 0)
        self.assertEqual(self.second.ViewObject.Transparency, 0)
        self.document.redo()
        _update_gui()
        self.assertEqual(self.first.ViewObject.Transparency, 65)
        self.assertEqual(self.second.ViewObject.Transparency, 0)

        with TemporaryDirectory() as temporary_directory:
            path = Path(temporary_directory) / "appearance.FCStd"
            self.document.saveAs(str(path))
            document_name = self.document.Name
            App.closeDocument(document_name)
            self.documents.remove(document_name)
            reopened = App.openDocument(str(path))
            self.documents.append(reopened.Name)
            App.setActiveDocument(reopened.Name)
            _update_gui(5)

            self.assertEqual(
                reopened.getObject(first_name).ViewObject.Transparency,
                65,
            )
            self.assertEqual(
                reopened.getObject(second_name).ViewObject.Transparency,
                0,
            )
            self.assertEqual(
                tuple(_timeline(reopened).Operations),
                tuple(
                    reopened.getObject(operation_name)
                    for operation_name in operation_names_before
                ),
            )

    def test_appearance_refuses_deleted_and_same_name_replacement_targets(self):
        first_name = self.first.Name
        first_id = self.first.ID

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.first)
        Gui.runCommand("Std_SetAppearance", 0)
        _update_gui(5)
        self.assertTrue(Gui.Control.activeDialog())
        transparency = Gui.getMainWindow().findChild(
            QtWidgets.QSpinBox,
            "spinTransparency",
        )
        self.assertIsNotNone(transparency)

        transparency.setValue(20)
        _update_gui()
        self.assertEqual(self.first.ViewObject.Transparency, 20)

        self.document.removeObject(first_name)
        self.document.recompute()
        _update_gui(5)
        transparency.setValue(35)
        _update_gui()
        self.assertEqual(self.second.ViewObject.Transparency, 0)

        replacement = self.document.addObject("Part::Box", first_name)
        self.assertEqual(replacement.Name, first_name)
        self.assertNotEqual(replacement.ID, first_id)
        replacement.ViewObject.Transparency = 7
        self.document.recompute()
        _update_gui(5)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.second)
        transparency.setValue(65)
        _update_gui()
        self.assertEqual(replacement.ViewObject.Transparency, 7)
        self.assertEqual(self.second.ViewObject.Transparency, 0)

        Gui.Control.activeTaskDialog().reject()
        _update_gui()
        self.assertFalse(Gui.Control.activeDialog())

    def test_closing_target_document_never_retargets_reused_names(self):
        document_name = self.document.Name
        object_name = self.first.Name

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.first)
        Gui.runCommand("Std_SetAppearance", 0)
        _update_gui(5)
        self.assertTrue(Gui.Control.activeDialog())

        App.closeDocument(document_name)
        self.assertNotIn(document_name, App.listDocuments())
        self.documents.remove(document_name)
        _update_gui(10)

        replacement_document = App.newDocument(document_name)
        self.assertEqual(replacement_document.Name, document_name)
        self.documents.append(replacement_document.Name)
        replacement = replacement_document.addObject("Part::Box", object_name)
        replacement_document.recompute()
        App.setActiveDocument(replacement_document.Name)
        _update_gui(5)

        if Gui.Control.activeDialog():
            transparency = Gui.getMainWindow().findChild(
                QtWidgets.QSpinBox,
                "spinTransparency",
            )
            self.assertIsNotNone(transparency)
            transparency.setValue(75)
            _update_gui()
            Gui.Control.activeTaskDialog().reject()
            _update_gui()

        self.assertEqual(replacement.ViewObject.Transparency, 0)
        self.document = replacement_document
        self.first = replacement
