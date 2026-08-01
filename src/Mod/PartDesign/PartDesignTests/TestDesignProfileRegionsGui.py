# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI contracts for master-sketch closed-area modeling."""

import unittest

import FreeCAD as App
import FreeCADGui as Gui
import Part
import PartDesign
import Sketcher  # noqa: F401 - registers reusable sketch objects
from PySide import QtCore, QtGui


class TestDesignProfileRegionsGui(unittest.TestCase):
    def setUp(self):
        if not App.GuiUp or Gui.getMainWindow() is None:
            self.skipTest("Requires GUI")
        Gui.activateWorkbench("PartDesignWorkbench")
        self.document = App.newDocument("DesignProfileRegionsGui")
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
        document = App.getDocument("DesignProfileRegionsGui")
        if document is not None:
            App.closeDocument(document.Name)
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

    @classmethod
    def _wait_until(cls, predicate, timeout_ms=5000):
        timer = QtCore.QElapsedTimer()
        timer.start()
        while timer.elapsed() < timeout_ms:
            cls._process_events()
            try:
                result = predicate()
            except RuntimeError:
                result = None
            if result:
                return result
        return None

    def _master_sketch(self):
        sketch = self.document.addObject(
            "Sketcher::SketchObject",
            "MasterSketch",
        )
        sketch.addGeometry(
            Part.Circle(
                App.Vector(0, 0, 0),
                App.Vector(0, 0, 1),
                2,
            ),
            False,
        )
        sketch.addGeometry(
            Part.Circle(
                App.Vector(10, 0, 0),
                App.Vector(0, 0, 1),
                3,
            ),
            False,
        )
        self.document.recompute()
        PartDesign.finalizeDesignDefinition(sketch)
        self.document.recompute()
        self.assertEqual(len(sketch.InternalShape.Faces), 2)
        return sketch

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

    def _close_task(self, standard_button):
        button = self._task_button(standard_button)
        self.assertIsNotNone(button)
        button.click()
        self._process_events(50)
        self.assertFalse(Gui.Control.activeDialog())

    def _profile_button(self):
        button = Gui.getMainWindow().findChild(
            QtGui.QPushButton,
            "DesignProfileSelectRegions",
        )
        self.assertIsNotNone(button)
        self.assertTrue(button.isVisible())
        return button

    def _select_task_region(self, sketch, region):
        button = self._profile_button()
        button.click()
        self._process_events()
        self.assertTrue(button.isChecked())
        self.assertEqual(button.text(), "Done")
        Gui.Selection.addSelection(sketch, region)
        self._process_events()
        button.click()
        self._process_events()
        self.assertFalse(button.isChecked())

    def _begin_edit(self, operation):
        timeline = Gui.getMainWindow().findChild(
            QtGui.QListWidget,
            "VibeCADFeatureTimelineItems",
        )
        self.assertIsNotNone(timeline)

        def operation_item():
            return next(
                (
                    timeline.item(row)
                    for row in range(timeline.count())
                    if timeline.item(row).data(int(QtCore.Qt.UserRole))
                    == operation.Name
                ),
                None,
            )

        item = self._wait_until(operation_item)
        self.assertIsNotNone(item)
        timeline.itemDoubleClicked.emit(item)
        self.assertTrue(
            self._wait_until(lambda: Gui.Control.activeDialog()),
            "Timeline edit did not open the operation task",
        )

    def test_command_and_task_edit_exact_master_sketch_areas(self):
        sketch = self._master_sketch()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch, "InternalFace1")
        self._process_events()
        self.assertTrue(Gui.isCommandActive("PartDesign_DesignExtrude"))
        Gui.runCommand("PartDesign_DesignExtrude", 0)
        self._process_events(50)

        self.assertTrue(Gui.Control.activeDialog())
        operation = self.document.ActiveObject
        self.assertEqual(operation.TypeId, "PartDesign::DesignExtrude")
        self.assertIs(operation.Profile[0], sketch)
        self.assertEqual(list(operation.Profile[1]), ["InternalFace1"])
        summary = Gui.getMainWindow().findChild(
            QtGui.QLabel,
            "DesignProfileRegions",
        )
        self.assertIsNotNone(summary)
        self.assertEqual(summary.text(), "1 selected area(s)")

        operation_name = operation.Name
        self._close_task(QtGui.QDialogButtonBox.Ok)
        operation = self.document.getObject(operation_name)
        bodies = [
            body
            for body in self.document.Objects
            if body.TypeId == "PartDesign::Body"
        ]
        self.assertEqual(len(bodies), 1)
        body_name = bodies[0].Name
        self.assertAlmostEqual(bodies[0].Shape.Volume, 40 * 3.14159265, places=4)

        self._begin_edit(operation)
        self._select_task_region(sketch, "InternalFace2")
        self.assertEqual(list(operation.Profile[1]), ["InternalFace2"])
        self._close_task(QtGui.QDialogButtonBox.Cancel)

        operation = self.document.getObject(operation_name)
        sketch = self.document.getObject(sketch.Name)
        body = self.document.getObject(body_name)
        self.assertEqual(list(operation.Profile[1]), ["InternalFace1"])
        self.assertAlmostEqual(body.Shape.Volume, 40 * 3.14159265, places=4)

        self._begin_edit(operation)
        self._select_task_region(sketch, "InternalFace2")
        self._close_task(QtGui.QDialogButtonBox.Ok)

        operation = self.document.getObject(operation_name)
        body = self.document.getObject(body_name)
        self.assertEqual(list(operation.Profile[1]), ["InternalFace2"])
        self.assertAlmostEqual(body.Shape.Volume, 90 * 3.14159265, places=4)
        PartDesign.validateDesign(operation)

    def test_command_accepts_multiple_areas_but_not_ambiguous_edges(self):
        sketch = self._master_sketch()

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch, "Edge1")
        self._process_events()
        self.assertFalse(Gui.isCommandActive("PartDesign_DesignExtrude"))

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch, "InternalFace1")
        Gui.Selection.addSelection(sketch, "InternalFace2")
        self._process_events()
        self.assertTrue(Gui.isCommandActive("PartDesign_DesignExtrude"))
        Gui.runCommand("PartDesign_DesignExtrude", 0)
        self._process_events(50)

        self.assertTrue(Gui.Control.activeDialog())
        operation = self.document.ActiveObject
        self.assertIs(operation.Profile[0], sketch)
        self.assertEqual(
            list(operation.Profile[1]),
            ["InternalFace1", "InternalFace2"],
        )

        select_areas = self._profile_button()
        select_areas.click()
        self._process_events()
        Gui.Selection.addSelection(sketch)
        self._process_events()
        select_areas.click()
        self._process_events()
        self.assertTrue(select_areas.isChecked())
        self.assertEqual(
            list(operation.Profile[1]),
            ["InternalFace1", "InternalFace2"],
        )

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch, "InternalFace1")
        self._process_events()
        select_areas.click()
        self._process_events()
        self.assertFalse(select_areas.isChecked())
        self.assertEqual(list(operation.Profile[1]), ["InternalFace1"])

        operation_name = operation.Name
        self._close_task(QtGui.QDialogButtonBox.Cancel)
        self.assertIsNone(self.document.getObject(operation_name))

    def test_global_operation_preview_color_follows_result_semantics(self):
        operation = self.document.addObject(
            "PartDesign::DesignExtrude",
            "PreviewSemantics",
        )
        self._process_events()

        def preview_rgb():
            return tuple(float(value) for value in operation.ViewObject.PreviewColor)[:3]

        def assert_preview_rgb(expected):
            for actual, wanted in zip(preview_rgb(), expected):
                self.assertAlmostEqual(actual, wanted, places=6)

        assert_preview_rgb((0.0, 1.0, 0.6))

        operation.ResultOperation = "Cut"
        self._process_events()
        assert_preview_rgb((1.0, 0.0, 0.0))

        operation.ResultOperation = "Intersect"
        self._process_events()
        assert_preview_rgb((1.0, 1.0, 0.0))

        operation.ResultOperation = "Join"
        self._process_events()
        assert_preview_rgb((0.0, 1.0, 0.6))


if __name__ == "__main__":
    unittest.main()
