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
        self.assertTrue(self._wait_until(lambda: not Gui.Control.activeDialog()),
                        "The task did not finish accepting or cancelling")

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

    def test_command_repairs_incomplete_persisted_sketch_identity(self):
        sketch = self._master_sketch()
        sketch.setPropertyStatus(
            "VibeCADTimelineRole",
            "-LockDynamic",
        )
        sketch.removeProperty("VibeCADTimelineRole")
        self.assertNotIn("VibeCADTimelineRole", sketch.PropertiesList)

        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch, "InternalFace1")
        self._process_events()
        Gui.runCommand("PartDesign_DesignExtrude", 0)
        self._process_events(50)

        self.assertTrue(Gui.Control.activeDialog())
        operation = self.document.ActiveObject
        self.assertEqual(sketch.VibeCADTimelineRole, "operation")
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(sketch),
            1,
        )
        self._close_task(QtGui.QDialogButtonBox.Ok)
        self.assertEqual(sketch.VibeCADTimelineRole, "operation")
        self.assertEqual(
            self.document.VibeCADTimeline.Operations.count(sketch),
            1,
        )
        PartDesign.validateDesign(operation)

    def test_final_result_checkbox_renders_unpublished_design_output(self):
        from pivy import coin

        sketch = self._master_sketch()
        Gui.Selection.addSelection(sketch, "InternalFace1")
        Gui.runCommand("PartDesign_DesignExtrude", 0)
        self.assertTrue(self._wait_until(lambda: Gui.Control.activeDialog()))
        operation = next(
            obj for obj in self.document.Objects
            if obj.TypeId == "PartDesign::DesignExtrude"
        )
        final = Gui.getMainWindow().findChild(QtGui.QCheckBox, "showFinalCheckBox")
        overlay = Gui.getMainWindow().findChild(
            QtGui.QCheckBox, "showTransparentPreviewCheckBox"
        )
        self.assertIsNotNone(final)
        self.assertIsNotNone(overlay)
        overlay.setChecked(False)
        final.setChecked(True)

        def displayed_bounds():
            action = coin.SoGetBoundingBoxAction(coin.SbViewportRegion(640, 480))
            action.apply(operation.ViewObject.RootNode)
            bounds = action.getBoundingBox()
            return None if bounds.isEmpty() else bounds.getSize().getValue()

        bounds = self._wait_until(displayed_bounds)
        self.assertIsNotNone(bounds, "Final result has no visible scene geometry")
        self.assertAlmostEqual(bounds[0], 4, places=2)
        self.assertAlmostEqual(bounds[1], 4, places=2)
        self.assertAlmostEqual(bounds[2], operation.Length.Value, places=2)
        self.assertTrue(operation.Shape.isNull(), "Preview must not publish the controller Shape")
        final.setChecked(False)
        self._process_events()
        self.assertIsNone(displayed_bounds())
        self._close_task(QtGui.QDialogButtonBox.Cancel)

    def test_cut_suggests_unique_intersecting_body_without_viewport_selection(self):
        body = self.document.addObject("PartDesign::Body", "Target")
        base = body.newObject("PartDesign::Feature", "Base")
        base.Shape = Part.makeBox(10, 10, 10, App.Vector(-5, -5, 0))
        body.Tip = base
        sketch = self._master_sketch()
        Gui.Selection.addSelection(sketch, "InternalFace1")
        Gui.runCommand("PartDesign_DesignExtrude", 0)
        self.assertTrue(self._wait_until(lambda: Gui.Control.activeDialog()))
        operation = next(
            obj for obj in self.document.Objects
            if obj.TypeId == "PartDesign::DesignExtrude"
        )
        mode = Gui.getMainWindow().findChild(QtGui.QComboBox, "DesignResultOperation")
        length = Gui.getMainWindow().findChild(QtGui.QWidget, "lengthEdit")
        self.assertIsNotNone(length)
        self.assertTrue(length.setProperty("rawValue", 5.0))
        self.assertEqual(operation.Length.Value, 5.0)
        mode.setCurrentIndex(mode.findData("Cut"))
        self.assertTrue(
            self._wait_until(lambda: operation.TargetBodyIds == [body.VibeCADBodyId]),
            "The single intersecting Body was not suggested for Cut",
        )
        self.assertTrue(operation.isValid(), operation.getStatusString())
        self._close_task(QtGui.QDialogButtonBox.Ok)
        self.assertAlmostEqual(body.Shape.Volume, 1000 - 20 * 3.14159265, places=4)

    def test_extrude_provides_pickable_end_plane_for_length_dragging(self):
        from pivy import coin

        view = Gui.activeDocument().activeView()
        view.setNavigationType("Gui::InventorNavigationStyle")
        view.setAnimationEnabled(False)
        previous_redirection = view.getViewer().isRedirectedToSceneGraph()
        sketch = self._master_sketch()
        Gui.Selection.addSelection(sketch, "InternalFace1")
        Gui.runCommand("PartDesign_DesignExtrude", 0)
        self.assertTrue(self._wait_until(lambda: Gui.Control.activeDialog()))
        searching = coin.SoBaseKit.isSearchingChildren()
        try:
            coin.SoBaseKit.setSearchingChildren(True)
            search = coin.SoSearchAction()
            search.setType(coin.SoType.fromName("SoLinearDragger"))
            search.setInterest(coin.SoSearchAction.ALL)
            search.apply(Gui.activeDocument().activeView().getSceneGraph())
            draggers = [path.getTail() for path in search.getPaths()]
            self.assertTrue(draggers, "Extrude length dragger is not visible")
            surfaces = [d.getPart("dragSurface", False) for d in draggers]
            self.assertTrue(
                any(s is not None and s.getNumChildren() > 0 for s in surfaces),
                "No pickable end plane is attached to the length dragger",
            )
        finally:
            coin.SoBaseKit.setSearchingChildren(searching)

        operation = next(o for o in self.document.Objects if o.TypeId == "PartDesign::DesignExtrude")
        Gui.getMainWindow().findChild(QtGui.QCheckBox, "showFinalCheckBox").setChecked(True)
        self._process_events(100)
        view = Gui.activeDocument().activeView()
        view.viewAxonometric()
        view.fitAll()
        self._process_events(100)
        viewport = view.graphicsView().viewport()
        initial = operation.Length.Value

        def screen_point(point):
            x, y = view.getPointOnScreen(point)
            _, height = view.getSize()
            ratio = viewport.devicePixelRatioF()
            return QtCore.QPoint(round(x / ratio), round((height - y - 1) / ratio))

        start = screen_point(App.Vector(1.2, 1.2, initial))
        end = screen_point(App.Vector(1.2, 1.2, initial + 3))
        self.assertTrue(viewport.rect().contains(start))

        def mouse(kind, point, button, buttons):
            receiver = view.graphicsView()
            local = viewport.mapTo(receiver, point)
            event = QtGui.QMouseEvent(
                kind, local, viewport.mapToGlobal(point), button, buttons, QtCore.Qt.NoModifier
            )
            QtGui.QApplication.sendEvent(receiver, event)
            self._process_events()

        mouse(QtCore.QEvent.MouseMove, start, QtCore.Qt.NoButton, QtCore.Qt.NoButton)
        mouse(QtCore.QEvent.MouseButtonPress, start, QtCore.Qt.LeftButton, QtCore.Qt.LeftButton)
        self.assertTrue(
            any(d.getPart("dragger", False).getField("active").getValue() for d in draggers),
            "Pressing the end plane did not activate its length dragger",
        )
        for step in range(1, 6):
            point = start + (end - start) * (step / 5)
            mouse(QtCore.QEvent.MouseMove, point, QtCore.Qt.NoButton, QtCore.Qt.LeftButton)
        mouse(QtCore.QEvent.MouseButtonRelease, end, QtCore.Qt.LeftButton, QtCore.Qt.NoButton)
        self.assertGreater(operation.Length.Value, initial, "Dragging the end plane did not extend the feature")
        self._close_task(QtGui.QDialogButtonBox.Cancel)
        self.assertEqual(view.getViewer().isRedirectedToSceneGraph(), previous_redirection)

    def test_area_picker_shows_live_count_and_restores_body_visibility(self):
        body = self.document.addObject("PartDesign::Body", "Target")
        base = body.newObject("PartDesign::Feature", "Base")
        base.Shape = Part.makeBox(10, 10, 10)
        body.Tip = base
        sketch = self._master_sketch()
        body.ViewObject.show()
        Gui.Selection.addSelection(sketch)
        Gui.runCommand("PartDesign_DesignExtrude", 0)
        self.assertTrue(self._wait_until(lambda: Gui.Control.activeDialog()))
        self._profile_button().click()
        self.assertFalse(body.ViewObject.Visibility, "A Body can obscure the selectable sketch areas")
        Gui.Selection.addSelection(sketch, "InternalFace1")
        self._process_events()
        summary = Gui.getMainWindow().findChild(QtGui.QLabel, "DesignProfileRegions")
        self.assertEqual(summary.text(), "1 selected area(s)")
        Gui.Selection.addSelection(sketch, "InternalFace2")
        self._process_events()
        self.assertEqual(summary.text(), "2 selected area(s)")
        self._profile_button().click()
        self._process_events()
        self.assertTrue(body.ViewObject.Visibility)
        self._close_task(QtGui.QDialogButtonBox.Cancel)

    def test_cut_target_suggestions_leave_ambiguous_and_explicit_choices_to_user(self):
        report = next(widget for widget in Gui.getMainWindow().findChildren(QtGui.QTextEdit)
                      if widget.metaObject().className().endswith("ReportOutput"))
        report_start = len(report.toPlainText())
        for name in ("First", "Second"):
            body = self.document.addObject("PartDesign::Body", name)
            base = body.newObject("PartDesign::Feature", name + "Base")
            base.Shape = Part.makeBox(10, 10, 10, App.Vector(-5, -5, 0))
            body.Tip = base
        sketch = self._master_sketch()
        Gui.Selection.addSelection(sketch, "InternalFace1")
        Gui.runCommand("PartDesign_DesignExtrude", 0)
        self.assertTrue(self._wait_until(lambda: Gui.Control.activeDialog()))
        mode = Gui.getMainWindow().findChild(QtGui.QComboBox, "DesignResultOperation")
        mode.setCurrentIndex(mode.findData("Cut"))
        hint = Gui.getMainWindow().findChild(QtGui.QLabel, "DesignTargetHint")
        self.assertTrue(self._wait_until(lambda: "2 Bodies intersect" in hint.text()))
        choices = Gui.getMainWindow().findChild(QtGui.QListWidget, "DesignBodyList")
        self.assertEqual(choices.item(0).checkState(), QtCore.Qt.Unchecked)
        self.assertEqual(choices.item(1).checkState(), QtCore.Qt.Unchecked)
        choices.item(0).setCheckState(QtCore.Qt.Checked)
        self._process_events()
        choices.item(0).setCheckState(QtCore.Qt.Unchecked)
        self._process_events(200)
        self.assertEqual(choices.item(0).checkState(), QtCore.Qt.Unchecked)
        self.assertIn("Check at least one", hint.text())
        operation = next(o for o in self.document.Objects if o.TypeId == "PartDesign::DesignExtrude")
        self.assertIn("Select at least one target Body", operation.getStatusString())
        self._close_task(QtGui.QDialogButtonBox.Cancel)
        self._process_events(100)
        self.assertNotIn("Unhandled Base::Exception", report.toPlainText()[report_start:])

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

    def test_face_attached_extrude_join_accepts_through_task_panel(self):
        self.document.openTransaction("Create supported extrusion profile")
        body = self.document.addObject("PartDesign::Body", "JoinTarget")
        initial = body.newObject("PartDesign::Feature", "JoinInitial")
        initial.Shape = Part.makeBox(10, 10, 10)
        body.Tip = initial
        sketch = self.document.addObject("Sketcher::SketchObject", "SupportedProfile")
        sketch.AttachmentSupport = [(initial, ["Face6"])]
        sketch.MapMode = "FlatFace"
        for a, b in (((2, 2), (6, 2)), ((6, 2), (6, 6)),
                     ((6, 6), (2, 6)), ((2, 6), (2, 2))):
            sketch.addGeometry(Part.LineSegment(App.Vector(*a, 0), App.Vector(*b, 0)), False)
        self.document.recompute()
        PartDesign.finalizeDesignDefinition(sketch)
        self.document.commitTransaction()
        self.document.recompute()
        self._process_events(50)
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(sketch)
        Gui.Selection.addSelection(body)
        Gui.runCommand("PartDesign_DesignExtrude", 0)
        self._process_events(50)
        self.assertTrue(Gui.Control.activeDialog())
        operation = next(obj for obj in self.document.Objects
                         if obj.TypeId == "PartDesign::DesignExtrude")
        self.assertEqual(operation.TypeId, "PartDesign::DesignExtrude")
        self.assertEqual(operation.ResultOperation, "Join")
        self.assertTrue(operation.isValid(), operation.getStatusString())
        expected_volume = 1000 + 16 * operation.Length.Value
        self.assertAlmostEqual(operation.OutputShapes[0].Volume, expected_volume, places=6)
        self._close_task(QtGui.QDialogButtonBox.Ok)
        self.assertTrue(body.Shape.isValid())
        self.assertEqual(len(body.Shape.Solids), 1)
        self.assertAlmostEqual(body.Shape.Volume, expected_volume, places=6)
        PartDesign.validateDesign(operation)

if __name__ == "__main__":
    unittest.main()
