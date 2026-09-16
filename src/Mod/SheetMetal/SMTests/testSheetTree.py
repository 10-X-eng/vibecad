# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real native tree rows backed by the shared sheet, not extra model objects."""

import os
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets

from SMTests import testPresentation


class TestSheetTree(unittest.TestCase):
    def setUp(self):
        import SheetMetalPresentation as Presentation
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.model.edit(lambda: self.model.doc.removeObject(self.model.sheet.Name))
        self.sheet = self.model.sheet = self.model.edit(lambda: Presentation.create_presented_sheet(
            self.model.doc.BaseBend, f"Face{self.model.root}", tree_details=True))
        self.fixture.view = self.view = self.sheet.ViewObject.Proxy
        self.assertEqual(self.sheet.ViewObject.TypeId, "PartGui::ViewProviderCachedDetailsPython")
        self.fixture.wait_for(lambda: self.view.ready)
        self.addCleanup(Gui.Selection.clearSelection)

    def rows(self):
        result = {}
        self.last_rows = []
        if self.model.doc.PresentationUpdateActive:
            return result
        for tree in Gui.getMainWindow().findChildren(QtWidgets.QTreeWidget):
            if not tree.isVisible():
                continue
            model = tree.model()
            pending = [QtCore.QModelIndex()]
            while pending:
                parent = pending.pop()
                for row in range(model.rowCount(parent)):
                    index = model.index(row, 0, parent)
                    self.last_rows.append((parent.data(), index.data(), str(index.data(QtCore.Qt.UserRole))))
                    if parent.data() == self.sheet.Label:
                        key = index.data(QtCore.Qt.UserRole)
                        if isinstance(key, str):
                            result[key] = tree, QtCore.QPersistentModelIndex(index)
                    pending.append(index)
        return result

    def row(self, key):
        try:
            self.fixture.wait_for(lambda: key in self.rows())
        except AssertionError:
            Path(os.environ["VIBECAD_TEST_OUTPUT"], "tree-rows.json").write_text(json.dumps({
                "expected_parent": self.sheet.Label, "rows": self.last_rows,
                "provider_rows": self.view.getTreeViewDetails()}, indent=2))
            raise
        tree, persistent = self.rows()[key]
        item = QtCore.QModelIndex(persistent)
        parent = item.parent()
        while parent.isValid():
            tree.expand(parent)
            parent = parent.parent()
        tree.scrollTo(item)
        return tree, item

    def activate(self, key):
        tree, item = self.row(key)
        position = QtCore.QPointF(tree.visualRect(item).center())
        event = QtGui.QMouseEvent(QtCore.QEvent.MouseButtonDblClick, position, position,
                                 QtCore.Qt.LeftButton, QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
        QtWidgets.QApplication.sendEvent(tree.viewport(), event)

    def test_folded_and_flat_rows_activate_without_model_or_history_changes(self):
        import SheetMetalOperations as Operations
        self.row("representation:flat")
        before = (Operations.capture_revision(self.sheet), self.model.doc.UndoCount,
                  len(self.model.doc.Objects), self.sheet.Definition, self.sheet.Visibility)
        nodes = self.view.cached_nodes
        with patch.object(self.sheet.Proxy, "execute", side_effect=AssertionError("recompute")), \
                patch.object(self.view, "getTreeViewDetails", wraps=self.view.getTreeViewDetails) as details:
            self.activate("representation:flat")
            self.assertEqual(self.view.mode, "flat")
            self.activate("representation:folded")
            self.assertEqual(self.view.mode, "folded")
            QtWidgets.QApplication.processEvents()
            details.assert_not_called()
        self.assertEqual(nodes, self.view.cached_nodes)
        self.assertEqual(before, (Operations.capture_revision(self.sheet), self.model.doc.UndoCount,
                                 len(self.model.doc.Objects), self.sheet.Definition, self.sheet.Visibility))

    def test_cut_rows_use_stable_operation_identity_and_open_the_exact_editor(self):
        import SheetMetalEditable as Editable
        import SheetMetalGui
        _, _, flat = self.model.bend_pick()
        identity = self.model.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 5))
        self.fixture.wait_for(lambda: self.view.ready)
        key = "operation:" + identity
        _, item = self.row(key)
        self.assertIn("Hole", item.data())
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.model.doc.BaseBend)
        original, opened = SheetMetalGui.SheetPanel, []
        def panel(*args):
            value = original(*args)
            opened.append(value)
            return value
        with patch.object(SheetMetalGui, "SheetPanel", side_effect=panel):
            self.activate(key)
        self.assertEqual(len(opened), 1)
        self.addCleanup(opened[0].reject)
        self.assertIs(opened[0].sheet, self.sheet)
        self.assertEqual(opened[0].operations.currentData(), identity)
        self.assertEqual(opened[0].radius.value(), 5)
        active = Gui.getDocument(self.model.doc.Name).activeView()
        active.setAnimationEnabled(False)
        active.viewAxonometric()
        active.fitAll()
        Gui.updateGui()
        active.redraw()
        QtWidgets.QApplication.sync()
        main = Gui.getMainWindow()
        self.assertTrue(main.windowHandle().screen().grabWindow(main.winId()).save(
            str(Path(os.environ["VIBECAD_TEST_OUTPUT"])/"sheet-tree-editor.png")))

    def test_removed_cut_row_cannot_edit_a_different_operation(self):
        import SheetMetalEditable as Editable
        _, _, flat = self.model.bend_pick()
        identity = self.model.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 5))
        self.row("operation:" + identity)
        self.model.edit(lambda: Editable.remove_operation(self.sheet, identity))
        self.fixture.wait_for(lambda: "operation:" + identity not in self.rows())
        with self.assertRaisesRegex(ValueError, "no longer"):
            self.view.activateTreeViewDetail("operation:" + identity)
        self.assertFalse(Gui.Control.activeDialog())

    def test_editor_detail_does_not_open_inside_another_transaction(self):
        self.addCleanup(lambda: Gui.Control.closeDialog() if Gui.Control.activeDialog() else None)
        self.model.doc.openTransaction("Other caller")
        try:
            with self.assertRaisesRegex(RuntimeError, "transaction"):
                self.view.activateTreeViewDetail("material")
            self.assertFalse(Gui.Control.activeDialog())
        finally:
            self.model.doc.abortTransaction()

    def test_rows_follow_radius_and_material_changes(self):
        import SheetMetalEditable as Editable
        _, _, flat = self.model.bend_pick()
        identity = self.model.edit(lambda: Editable.add_circle_cut(self.sheet, flat, 5))
        self.row("operation:" + identity)
        self.model.edit(lambda: Editable.update_circle_cut(self.sheet, identity, radius=7))
        def current_radius():
            rows = self.rows()
            if "operation:" + identity not in rows:
                return False
            _, index = rows["operation:" + identity]
            return index.sibling(index.row(), 1).data() == "Radius 7 mm"
        self.fixture.wait_for(current_radius)
        self.model.edit(lambda: setattr(self.sheet, "Material", "Steel"))
        def current_material():
            rows = self.rows()
            if "material" not in rows:
                return False
            _, index = rows["material"]
            return index.sibling(index.row(), 1).data() == "Steel"
        self.fixture.wait_for(current_material)

    def test_tree_action_never_redirects_to_another_active_document(self):
        other = App.newDocument("OtherTreeDocument")
        self.addCleanup(lambda: App.closeDocument(other.Name))
        undo, mode = self.model.doc.UndoCount, self.view.mode
        with self.assertRaisesRegex(RuntimeError, "Activate"):
            self.view.activateTreeViewDetail("representation:flat")
        self.assertEqual(self.view.mode, mode)
        self.assertEqual(self.model.doc.UndoCount, undo)
        self.assertEqual(len(other.Objects), 0)

    def test_reopen_restores_rows_and_preserves_representation_only_switching(self):
        self.row("representation:flat")
        count = len(self.model.doc.Objects)
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory)/"tree.FCStd")
            self.model.doc.saveAs(path)
            self.model.settle()
            App.closeDocument(self.model.doc.Name)
            self.model.doc = App.openDocument(path)
            self.model.settle()
            self.sheet = self.model.sheet = self.model.doc.getObject("EditableSheet")
            self.fixture.view = self.view = self.sheet.ViewObject.Proxy
            self.fixture.wait_for(lambda: self.view.ready)
            self.assertEqual(len(self.model.doc.Objects), count)
            undo = self.model.doc.UndoCount
            self.activate("representation:flat")
            self.assertEqual(self.view.mode, "flat")
            self.assertEqual(self.model.doc.UndoCount, undo)
            self.assertFalse(self.model.doc.isTouched())
