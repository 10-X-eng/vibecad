# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native History for shared-sheet creation, using the real timeline widget."""

from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets
from pivy import coin

from SMTests import testPresentation


class TestSheetHistory(unittest.TestCase):
    def setUp(self):
        import SheetMetalGui
        import SheetMetalOperations as Operations
        self.gui, self.operations = SheetMetalGui, Operations
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.model.edit(lambda: self.model.doc.removeObject(self.model.sheet.Name))
        self.source = self.model.doc.BaseBend
        self.source.Visibility = True
        self.gui.ensure_commands_registered()
        Gui.Selection.clearSelection()
        self.addCleanup(Gui.Selection.clearSelection)
        self.before_objects = tuple(self.model.doc.Objects)
        self.before_undo = self.model.doc.UndoCount
        run = Operations.start_creation(self.source, f"Face{self.model.root}",
            expected_revision=Operations.capture_source_revision(self.source))
        self.sheet = self.model.sheet = self.model.doc.getObject(run.status()["object_name"])
        self.fixture.view = self.sheet.ViewObject.Proxy
        self.fixture.wait_for(run.future.done)
        self.assertEqual(run.status()["phase"], "ready", run.status())
        self.fixture.wait_for(lambda: self.fixture.view.ready)

    def timeline(self):
        return next(obj for obj in self.model.doc.Objects if obj.TypeId == "App::DocumentTimeline")

    def button(self, suffix):
        button = Gui.getMainWindow().findChild(QtWidgets.QToolButton,
                                               "VibeCADFeatureTimeline" + suffix)
        self.assertIsNotNone(button)
        self.fixture.wait_for(lambda: button.isVisible() and button.isEnabled())
        button.click()
        self.model.settle()
        self.fixture.wait_for(lambda: not self.model.doc.PresentationUpdateActive)

    def item(self):
        widget = Gui.getMainWindow().findChild(QtWidgets.QListWidget, "VibeCADFeatureTimelineItems")
        self.assertIsNotNone(widget)
        def find():
            return next((widget.item(row) for row in range(widget.count())
                         if widget.item(row).data(QtCore.Qt.UserRole) == self.sheet.Name), None)
        self.fixture.wait_for(lambda: find() is not None)
        return widget, find()

    def test_creation_is_one_persisted_operation_with_exact_replaced_source(self):
        self.assertEqual(self.sheet.VibeCADTimelineRole, "operation")
        self.assertEqual(list(self.sheet.VibeCADTimelineReplacedInputs), [self.source])
        self.assertEqual(self.sheet.VibeCADTimelineEditCommand, "SheetMetal_EditParameters")
        for name in ("VibeCADTimelineRole", "VibeCADTimelineReplacedInputs", "VibeCADTimelineEditCommand"):
            self.assertTrue({"Hidden", "LockDynamic", "NoRecompute"}.issubset(
                self.sheet.getPropertyStatus(name)))
        self.assertEqual(self.model.doc.UndoCount, self.before_undo + 1)
        self.assertEqual(set(self.model.doc.Objects) - set(self.before_objects), {self.sheet})
        self.assertEqual(self.timeline().Operations[-1], self.sheet)
        _, item = self.item()
        self.assertFalse(item.icon().isNull())

    def test_rollback_restores_source_and_blocks_the_future_sheet_editor(self):
        self.button("Previous")
        self.assertFalse(self.sheet.Visibility)
        self.assertTrue(self.source.Visibility)
        self.assertFalse(self.model.doc.isObjectUsableAtCurrentTimelinePosition(self.sheet))
        with self.assertRaisesRegex(RuntimeError, "History"):
            self.operations.capture_revision(self.sheet)
        self.button("End")
        self.assertTrue(self.sheet.Visibility)
        self.assertFalse(self.source.Visibility)
        self.assertTrue(self.model.doc.isObjectUsableAtCurrentTimelinePosition(self.sheet))
        self.operations.capture_revision(self.sheet)
        self.model.assert_valid_pair()

    def test_history_double_click_opens_exact_sheet_dimensions(self):
        widget, item = self.item()
        widget.scrollToItem(item)
        position = QtCore.QPointF(widget.visualItemRect(item).center())
        original, panels = self.gui.SheetPanel, []
        def opened(*args):
            panel = original(*args)
            panels.append(panel)
            return panel
        Gui.Selection.addSelection(self.source)
        with patch.object(self.gui, "SheetPanel", side_effect=opened):
            for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
                         QtCore.QEvent.MouseButtonDblClick):
                event = QtGui.QMouseEvent(kind, position, position, QtCore.Qt.LeftButton,
                                         QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                QtWidgets.QApplication.sendEvent(widget.viewport(), event)
        for panel in panels:
            self.addCleanup(panel.reject)
        self.assertEqual(len(panels), 1)
        self.assertIs(panels[0].sheet, self.sheet)
        self.assertEqual(panels[0].mode, "parameters")
        self.assertEqual(self.model.doc.UndoCount, self.before_undo + 1)

    def test_future_source_cannot_create_a_sheet_at_an_earlier_marker(self):
        self.button("Previous")
        self.button("Previous")
        self.assertFalse(self.model.doc.isObjectUsableAtCurrentTimelinePosition(self.source))
        with self.assertRaisesRegex(RuntimeError, "History"):
            self.operations.capture_source_revision(self.source)

    def test_repeated_undo_redo_retains_one_scene_switch_and_rejects_old_picks(self):
        view = self.fixture.view
        switch = view._switch
        view.switch("flat")
        _, _, point = self.model.bend_pick()
        normal = self.sheet.Proxy._geometry.normal
        ray = coin.SoRayPickAction(coin.SbViewportRegion(800, 600))
        ray.setRay(coin.SbVec3f(*(point + normal*10)), coin.SbVec3f(*(-normal)))
        ray.apply(self.sheet.ViewObject.RootNode)
        pick = view.pick(ray.getPickedPoint())
        del ray
        name = self.sheet.Name
        for _ in range(3):
            self.model.doc.undo()
            self.model.recompute()
            self.fixture.wait_for(lambda: view._closed)
            self.assertEqual(switch.getNumChildren(), 0)
            self.model.doc.redo()
            self.model.recompute()
            self.sheet = self.model.sheet = self.model.doc.getObject(name)
            self.assertIs(self.sheet.ViewObject.Proxy, view)
            self.fixture.wait_for(lambda: view.ready)
            self.assertEqual(view._switch, switch)
            self.assertEqual(switch.getNumChildren(), 2)
            self.assertEqual(view.mode, "flat")
            with self.assertRaisesRegex(RuntimeError, "revision"):
                view.validate_pick(pick)
        active = Gui.getDocument(self.model.doc.Name).activeView()
        active.setAnimationEnabled(False)
        active.viewAxonometric()
        active.fitAll()
        active.redraw()
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / "redo.png")
            active.saveImage(filename, 800, 600, "White")
            image = QtGui.QImage(filename)
            pixels = sum(image.pixelColor(x, y) != QtGui.QColor("white")
                         for x in range(100, 700, 4) for y in range(100, 500, 4))
            self.assertGreater(pixels, 500)

    def test_container_owned_creation_keeps_native_history_and_frame(self):
        for type_id in ("App::Part", "PartDesign::Body"):
            with self.subTest(container=type_id):
                self.model.edit(lambda: self.model.doc.removeObject(self.sheet.Name))
                def group():
                    container = self.model.doc.addObject(type_id, "SheetContainer")
                    # Create in chronological container/source/result order.
                    # Relocating an older source requires the native relocation
                    # command's History reordering, which this fixture doesn't do.
                    self.source = self.model.doc.addObject("Part::Feature", "ContainerSource")
                    self.source.Shape = self.model.doc.BaseBend.Shape.copy()
                    container.addObject(self.source)
                    self.source.Visibility = True
                    return container
                container = self.model.edit(group)
                self.assertFalse(hasattr(self.source, "Proxy"))
                source_brep = self.source.Shape.exportBrepToString()
                run = self.operations.start_creation(self.source, f"Face{self.model.root}",
                    expected_revision=self.operations.capture_source_revision(self.source))
                self.sheet = self.model.sheet = self.model.doc.getObject(run.status()["object_name"])
                self.fixture.view = self.sheet.ViewObject.Proxy
                self.fixture.wait_for(run.future.done)
                self.assertEqual(run.status()["phase"], "ready", run.status())
                self.fixture.wait_for(lambda: self.fixture.view.ready)
                self.assertIs(self.sheet.getParentGeoFeatureGroup(), container)
                self.assertEqual(self.source.Shape.exportBrepToString(), source_brep)
                self.assertNotIn("KFactor", dict(self.sheet.ExpressionEngine))
                self.assertEqual(self.sheet.VibeCADTimelineRole, "operation")
                self.assertIn(self.sheet, self.timeline().Operations)
                self.button("Previous")
                self.assertFalse(self.sheet.Visibility)
                if type_id == "PartDesign::Body":
                    self.assertIs(container.Tip, self.source)
                    self.assert_body_shape(container, self.source)
                else:
                    self.assertTrue(self.source.Visibility)
                self.button("End")
                if type_id == "PartDesign::Body":
                    self.assertIs(container.Tip, self.sheet)
                    self.assert_body_shape(container, self.sheet)
                else:
                    self.assertTrue(self.sheet.Visibility)
                self.assertFalse(self.source.Visibility)
                self.model.assert_valid_pair()

    def assert_body_shape(self, container, result):
        # Body publication may copy/refine its Tip. Compare solid geometry,
        # not OCCT topology identity across those independently owned results.
        shape, expected = container.Shape, result.Shape
        self.assertTrue(shape.isValid())
        self.assertEqual(len(shape.Solids), 1)
        self.assertAlmostEqual(shape.Volume, expected.Volume, places=6)
        self.assertLess(shape.cut(expected).Volume, 1e-6)
        self.assertLess(expected.cut(shape).Volume, 1e-6)

    def test_undo_redo_and_reopen_preserve_replacement_history(self):
        name = self.sheet.Name
        self.model.doc.undo()
        self.model.recompute()
        self.assertIsNone(self.model.doc.getObject(name))
        self.assertTrue(self.source.Visibility)
        self.model.doc.redo()
        self.model.recompute()
        self.sheet = self.model.sheet = self.model.doc.getObject(name)
        self.fixture.view = self.sheet.ViewObject.Proxy
        self.fixture.wait_for(lambda: self.fixture.view.ready)
        self.assertEqual(list(self.sheet.VibeCADTimelineReplacedInputs), [self.source])
        with tempfile.TemporaryDirectory() as directory:
            filename = str(Path(directory) / "sheet-history.FCStd")
            self.model.doc.saveAs(filename)
            self.model.settle()
            App.closeDocument(self.model.doc.Name)
            self.model.doc = App.openDocument(filename)
            self.model.settle()
            self.sheet = self.model.sheet = self.model.doc.getObject(name)
            self.source = self.model.doc.BaseBend
            self.fixture.view = self.sheet.ViewObject.Proxy
            self.fixture.wait_for(lambda: self.fixture.view.ready)
            self.assertEqual(self.sheet.VibeCADTimelineEditCommand, "SheetMetal_EditParameters")
            self.assertEqual(list(self.sheet.VibeCADTimelineReplacedInputs), [self.source])
            self.button("Previous")
            self.assertFalse(self.sheet.Visibility)
            self.assertTrue(self.source.Visibility)
            self.button("End")
            self.assertTrue(self.sheet.Visibility)
            self.assertFalse(self.source.Visibility)
