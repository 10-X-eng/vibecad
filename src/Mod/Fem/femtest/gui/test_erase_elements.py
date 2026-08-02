# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeCAD behavior contracts for the FEM Erase Elements task."""

from pathlib import Path
import tempfile
import unittest

import Fem
import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui


@unittest.skipIf(not App.GuiUp, "Erase Elements task tests require the GUI")
class TestEraseElementsTask(unittest.TestCase):
    def setUp(self):
        Gui.activateWorkbench("FemWorkbench")
        self.document = App.newDocument("VibeCADEraseElements")
        self.document.UndoMode = True
        Gui.activateView("Gui::View3DInventor", True)

        self.source = self.document.addObject("Fem::FemMeshObject", "SourceMesh")
        self.source.FemMesh = self._two_face_mesh()

        self.alternate = self.document.addObject(
            "Fem::FemMeshObject",
            "AlternateMesh",
        )
        self.alternate.FemMesh = self._one_face_mesh()
        self.document.recompute()

    def tearDown(self):
        if Gui.Control.activeDialog():
            try:
                Gui.Control.activeTaskDialog().reject()
            except (AttributeError, RuntimeError):
                Gui.Control.closeDialog()
            self._process_events()

        Gui.Selection.clearSelection()
        if self.document is not None:
            booked = int(self.document.getBookedTransactionID())
            if booked:
                App.closeActiveTransaction(True, booked)
            if self.document.Name in App.listDocuments():
                App.closeDocument(self.document.Name)
        self.document = None
        self._process_events()

    @staticmethod
    def _two_face_mesh():
        mesh = Fem.FemMesh()
        for node_id, point in enumerate(
            ((0, 0, 0), (1, 0, 0), (0, 1, 0), (1, 1, 0)),
            1,
        ):
            mesh.addNode(*point, node_id)
        mesh.addFace([1, 2, 3], 11)
        mesh.addFace([2, 4, 3], 12)
        return mesh

    @staticmethod
    def _one_face_mesh():
        mesh = Fem.FemMesh()
        mesh.addNode(0, 0, 0, 21)
        mesh.addNode(2, 0, 0, 22)
        mesh.addNode(0, 2, 0, 23)
        mesh.addFace([21, 22, 23], 31)
        return mesh

    @staticmethod
    def _mesh_signature(mesh):
        nodes = tuple(
            sorted(
                (
                    int(node_id),
                    float(point.x),
                    float(point.y),
                    float(point.z),
                )
                for node_id, point in mesh.Nodes.items()
            )
        )
        elements = tuple(
            (
                int(element_id),
                tuple(int(node_id) for node_id in mesh.getElementNodes(element_id)),
            )
            for element_id in mesh.Faces
        )
        return nodes, elements

    @staticmethod
    def _process_events(rounds=5):
        for _ in range(rounds):
            Gui.updateGui()
            application = QtGui.QApplication.instance()
            if application is not None:
                application.processEvents(
                    QtCore.QEventLoop.AllEvents,
                    25,
                )

    def _start_task(self):
        App.setActiveDocument(self.document.Name)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.source)
        Gui.runCommand("FEM_CreateElementsSet")
        self._process_events()
        self.assertTrue(Gui.Control.activeDialog())
        self.assertIsNotNone(self.document.getObject("ElementsSet"))

    def _button(self, object_name):
        matches = [
            button
            for button in Gui.getMainWindow().findChildren(QtGui.QToolButton)
            if button.objectName() == object_name
        ]
        self.assertEqual(
            len(matches),
            1,
            f"Expected one active Erase Elements button named {object_name}",
        )
        return matches[0]

    def _copy_alternate_mesh(self):
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.alternate)
        self._button("toolButton_Copy").click()
        self._process_events()

    @staticmethod
    def _accept_next_modal():
        accepted = []

        def accept_modal():
            modal = QtGui.QApplication.activeModalWidget()
            if modal is None:
                QtCore.QTimer.singleShot(10, accept_modal)
                return
            accepted.append(modal.windowTitle())
            modal.accept()

        QtCore.QTimer.singleShot(0, accept_modal)
        return accepted

    def test_copy_restore_and_cancel_are_one_correctable_task(self):
        original_objects = tuple(self.document.Objects)
        original_undo_count = int(self.document.UndoCount)
        self._start_task()
        operation = self.document.getObject("ElementsSet")

        self._copy_alternate_mesh()
        preview = self.document.getObject("FilteredMesh")
        self.assertIsNotNone(preview)
        self.assertEqual(preview.FemMesh.FaceCount, 1)
        self.assertEqual(self.source.FemMesh.FaceCount, 2)
        self.assertIs(operation.FemMesh, preview)
        self.assertTrue(
            Gui.Control.activeDialog(),
            "Copy must keep Erase Elements open for another edit",
        )

        self._button("toolButton_Restore").click()
        self._process_events()
        self.assertIs(self.document.getObject("FilteredMesh"), preview)
        self.assertEqual(preview.FemMesh.FaceCount, 2)
        self.assertTrue(
            Gui.Control.activeDialog(),
            "Restore must keep Erase Elements open for another edit",
        )

        Gui.Control.activeTaskDialog().reject()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(tuple(self.document.Objects), original_objects)
        self.assertEqual(int(self.document.UndoCount), original_undo_count)
        self.assertEqual(self.source.FemMesh.FaceCount, 2)
        self.assertTrue(self.source.ViewObject.Visibility)

    def test_invalid_copy_keeps_the_task_open_for_correction(self):
        self._start_task()
        Gui.Selection.clearSelection()
        accepted = self._accept_next_modal()

        self._button("toolButton_Copy").click()
        self._process_events(20)

        self.assertEqual(len(accepted), 1)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertIsNone(self.document.getObject("FilteredMesh"))
        self.assertNotEqual(int(self.document.getBookedTransactionID()), 0)

    def test_accept_commits_the_exact_preview_as_one_transaction(self):
        original_undo_count = int(self.document.UndoCount)
        self._start_task()
        operation = self.document.getObject("ElementsSet")

        self._copy_alternate_mesh()
        preview = self.document.getObject("FilteredMesh")
        self.assertIsNotNone(preview)

        Gui.Control.activeTaskDialog().accept()
        self._process_events()

        self.assertFalse(Gui.Control.activeDialog())
        self.assertIs(self.document.getObject(operation.Name), operation)
        self.assertIs(self.document.getObject(preview.Name), preview)
        self.assertIs(operation.FemMesh, preview)
        self.assertEqual(preview.FemMesh.FaceCount, 1)
        self.assertEqual(self.source.FemMesh.FaceCount, 2)
        self.assertEqual(int(self.document.UndoCount), original_undo_count + 1)

    def test_filtered_mesh_follows_operation_timeline_and_reopens(self):
        source_signature = self._mesh_signature(self.source.FemMesh)
        self._start_task()
        operation = self.document.getObject("ElementsSet")
        self._copy_alternate_mesh()
        preview = self.document.getObject("FilteredMesh")
        self.assertIsNotNone(preview)

        Gui.Control.activeTaskDialog().accept()
        self._process_events()
        self.assertFalse(Gui.Control.activeDialog())
        self.assertEqual(preview.VibeCADTimelineRole, "resource")
        self.assertIs(preview.VibeCADTimelineOwner, operation)
        self.assertEqual(
            preview.getTypeIdOfProperty("VibeCADTimelineOwner"),
            "App::PropertyLinkHidden",
        )
        self.assertNotIn(operation, preview.OutList)
        self.assertIn(
            "Hidden",
            preview.getEditorMode("VibeCADTimelineRole"),
        )
        self.assertIn(
            "Hidden",
            preview.getEditorMode("VibeCADTimelineOwner"),
        )
        self.assertEqual(
            list(operation.VibeCADTimelineReplacedInputs),
            [self.source],
        )
        self.assertEqual(operation.VibeCADTimelineRole, "operation")
        self.assertEqual(
            operation.getTypeIdOfProperty("VibeCADTimelineRole"),
            "App::PropertyString",
        )
        self.assertIn(
            "Hidden",
            operation.getEditorMode("VibeCADTimelineRole"),
        )
        self.assertEqual(
            operation.getTypeIdOfProperty("VibeCADTimelineReplacedInputs"),
            "App::PropertyLinkListHidden",
        )
        self.assertIn(
            "Hidden",
            operation.getEditorMode("VibeCADTimelineReplacedInputs"),
        )
        self.assertEqual(self._mesh_signature(self.source.FemMesh), source_signature)

        timeline = self.document.getObject("VibeCADTimeline")
        operations = list(timeline.Operations)
        self.assertIn(preview, operations)
        block_start = operations.index(preview)
        operation_boundary = operations.index(operation) + 1
        self.assertEqual(operation_boundary, block_start + 2)

        main_window = Gui.getMainWindow()
        timeline_items = main_window.findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        previous = main_window.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelinePrevious",
        )
        end = main_window.findChild(
            QtGui.QToolButton,
            "VibeCADFeatureTimelineEnd",
        )
        self.assertIsNotNone(timeline_items)
        self.assertIsNotNone(previous)
        self.assertIsNotNone(end)

        def visible_names():
            return {
                timeline_items.item(row).data(QtCore.Qt.UserRole)
                for row in range(timeline_items.count())
                if timeline_items.item(row).data(QtCore.Qt.UserRole)
            }

        self.assertIn(operation.Name, visible_names())
        self.assertNotIn(preview.Name, visible_names())
        end.click()
        self._process_events()
        self.assertEqual(timeline.Position, len(operations))
        self.assertFalse(self.source.Visibility)
        self.assertTrue(preview.Visibility)

        previous.click()
        self._process_events()
        self.assertEqual(timeline.Position, block_start)
        self.assertTrue(self.source.Visibility)
        self.assertFalse(preview.Visibility)

        previous.click()
        self._process_events()
        self.assertLess(timeline.Position, block_start)
        self.assertTrue(self.source.Visibility)
        self.assertFalse(preview.Visibility)
        self.assertEqual(self._mesh_signature(self.source.FemMesh), source_signature)

        end.click()
        self._process_events()
        self.assertEqual(timeline.Position, operation_boundary)
        self.assertFalse(self.source.Visibility)
        self.assertTrue(preview.Visibility)

        operation_name = operation.Name
        preview_name = preview.Name
        source_name = self.source.Name
        saved_position = timeline.Position
        with tempfile.TemporaryDirectory() as temporary_directory:
            saved_file = Path(temporary_directory) / "filtered_mesh_timeline.FCStd"
            self.document.saveAs(str(saved_file))
            App.closeDocument(self.document.Name)
            self.document = App.openDocument(str(saved_file))
            self._process_events(10)

            restored_operation = self.document.getObject(operation_name)
            restored_preview = self.document.getObject(preview_name)
            restored_source = self.document.getObject(source_name)
            restored_timeline = self.document.getObject("VibeCADTimeline")
            self.assertEqual(
                restored_preview.VibeCADTimelineRole,
                "resource",
            )
            self.assertIs(
                restored_preview.VibeCADTimelineOwner,
                restored_operation,
            )
            self.assertEqual(
                restored_preview.getTypeIdOfProperty("VibeCADTimelineOwner"),
                "App::PropertyLinkHidden",
            )
            self.assertNotIn(
                restored_operation,
                restored_preview.OutList,
            )
            self.assertEqual(
                list(restored_operation.VibeCADTimelineReplacedInputs),
                [restored_source],
            )
            self.assertEqual(
                restored_operation.VibeCADTimelineRole,
                "operation",
            )
            self.assertEqual(
                restored_operation.getTypeIdOfProperty(
                    "VibeCADTimelineReplacedInputs"
                ),
                "App::PropertyLinkListHidden",
            )
            self.assertEqual(restored_timeline.Position, saved_position)
            self.assertFalse(restored_source.Visibility)
            self.assertTrue(restored_preview.Visibility)
            self.assertEqual(
                self._mesh_signature(restored_source.FemMesh),
                source_signature,
            )

            previous.click()
            self._process_events()
            self.assertEqual(restored_timeline.Position, block_start)
            self.assertTrue(restored_source.Visibility)
            self.assertFalse(restored_preview.Visibility)
            self.assertEqual(
                self._mesh_signature(restored_source.FemMesh),
                source_signature,
            )

            end.click()
            self._process_events()
            self.assertEqual(restored_timeline.Position, operation_boundary)
            self.assertFalse(restored_source.Visibility)
            self.assertTrue(restored_preview.Visibility)


if __name__ == "__main__":
    unittest.main()
