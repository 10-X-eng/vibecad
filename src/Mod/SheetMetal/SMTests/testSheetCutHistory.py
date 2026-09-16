# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real cut states in native History, sharing one folded/flat feature chain."""

import tempfile
import threading
import unittest
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
import Part
from PySide import QtCore, QtGui, QtWidgets

from SMTests import testPresentation


class TestSheetCutHistory(unittest.TestCase):
    def setUp(self):
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.base = self.model.sheet
        self.addCleanup(Gui.Selection.clearSelection)

    def history(self):
        import SheetMetalCutHistory
        return SheetMetalCutHistory

    def cut(self, base=None, radius=4, center=None):
        history = self.history()
        base = self.base if base is None else base
        if center is None:
            center = self.model.bend_pick()[2]
        result = self.model.edit(lambda: history.create_circle_step(base, center, radius))
        self.fixture.wait_for(lambda: result.ViewObject.Proxy.ready)
        return result

    def button(self, suffix):
        button = Gui.getMainWindow().findChild(QtWidgets.QToolButton,
                                               "VibeCADFeatureTimeline" + suffix)
        self.assertIsNotNone(button)
        self.fixture.wait_for(lambda: button.isVisible() and button.isEnabled())
        button.click()
        self.model.settle()
        self.fixture.wait_for(lambda: not self.model.doc.PresentationUpdateActive)

    def test_each_cut_is_one_real_state_and_reuses_the_prepared_mapping(self):
        history = self.history()
        initial = self.base.Shape.Volume
        mapping = self.base.Proxy._geometry.mapping
        first = self.cut()
        second_center = next(region.flat_face().CenterOfMass for region in mapping.regions
                             if region.kind == "plane")
        # Adding a second cut must not replay Unfold or recompute the first cut.
        from SheetMetalEditGeometry import SheetGeometry
        calls, previous = [], first.Proxy._geometry
        original_cut = SheetGeometry.cut
        def cut(geometry, profile):
            calls.append(geometry)
            return original_cut(geometry, profile)
        with patch("SheetMetalEditGeometry.SheetGeometry.prepare", side_effect=AssertionError("unfold")), \
                patch.object(SheetGeometry, "cut", autospec=True, side_effect=cut):
            second = self.cut(first, radius=2, center=second_center)
        self.assertEqual(calls, [previous])
        self.assertIs(first.Proxy._geometry, previous)
        self.assertIs(first.BaseSheet, self.base)
        self.assertIs(second.BaseSheet, first)
        self.assertGreater(initial, first.Shape.Volume)
        self.assertGreater(first.Shape.Volume, second.Shape.Volume)
        for step in (first, second):
            self.assertEqual(step.VibeCADTimelineRole, "operation")
            self.assertEqual(list(step.VibeCADTimelineReplacedInputs), [step.BaseSheet])
            self.assertIs(history.get_prepared(step).mapping, mapping)
            for shape in (step.Shape, step.FlatShape):
                self.assertTrue(shape.isValid())
                self.assertEqual(len(shape.Solids), 1)
        timeline = next(obj for obj in self.model.doc.Objects if obj.TypeId == "App::DocumentTimeline")
        self.assertEqual(list(timeline.Operations[-2:]), [first, second])
        self.button("Previous")
        self.assertFalse(second.Visibility)
        self.assertTrue(first.Visibility)
        self.assertFalse(self.base.Visibility)
        self.button("Previous")
        self.assertFalse(first.Visibility)
        self.assertTrue(self.base.Visibility)
        self.assertAlmostEqual(self.base.Shape.Volume, initial, places=6)
        self.button("End")
        self.assertTrue(second.Visibility)
        self.assertFalse(first.Visibility)
        self.assertFalse(self.base.Visibility)

    def test_radius_edit_recomputes_downstream_once_off_the_gui_thread(self):
        history = self.history()
        first = self.cut()
        center = next(region.flat_face().CenterOfMass for region in self.base.Proxy._geometry.mapping.regions
                      if region.kind == "plane")
        second = self.cut(first, radius=2, center=center)
        original = second.Shape.Volume
        executed = []
        gui_thread = threading.get_ident()
        from SheetMetalEditGeometry import SheetGeometry
        original_cut = SheetGeometry.cut
        def cut(geometry, profile):
            executed.append((geometry, threading.get_ident()))
            return original_cut(geometry, profile)
        with patch.object(SheetGeometry, "cut", autospec=True, side_effect=cut), \
                patch.object(SheetGeometry, "prepare", side_effect=AssertionError("unfold")):
            self.model.edit(lambda: history.update_circle_step(first, radius=5))
        self.assertEqual([geometry for geometry, _ in executed],
                         [self.base.Proxy._geometry, first.Proxy._geometry])
        self.assertTrue(all(thread != gui_thread for _, thread in executed))
        self.assertLess(second.Shape.Volume, original)
        self.model.doc.undo()
        self.model.recompute()
        self.assertAlmostEqual(second.Shape.Volume, original, places=6)
        self.model.doc.redo()
        self.model.recompute()
        self.assertLess(second.Shape.Volume, original)

    def test_suppression_bypasses_one_cut_and_keeps_the_next_cut(self):
        history = self.history()
        first = self.cut()
        center = next(region.flat_face().CenterOfMass for region in self.base.Proxy._geometry.mapping.regions
                      if region.kind == "plane")
        second = self.cut(first, radius=2, center=center)
        volume = second.Shape.Volume
        self.model.edit(lambda: setattr(first, "Suppressed", True))
        self.assertAlmostEqual(first.Shape.Volume, self.base.Shape.Volume, places=6)
        self.assertGreater(second.Shape.Volume, volume)
        self.assertLess(second.Shape.Volume, self.base.Shape.Volume)
        self.assertEqual(len(history.definition(second)["operations"]), 1)
        self.model.edit(lambda: setattr(first, "Suppressed", False))
        self.assertAlmostEqual(second.Shape.Volume, volume, places=6)

    def test_step_views_are_cached_and_save_reopen_keeps_native_dependencies(self):
        history = self.history()
        step = self.cut()
        view = step.ViewObject.Proxy
        before = self.model.doc.UndoCount, self.model.doc.isTouched(), step.Operation, step.Visibility
        nodes = view.cached_nodes
        with patch.object(step.Proxy, "execute", side_effect=AssertionError("view recompute")), \
                patch("SheetMetalPresentation.prepare_pair", side_effect=AssertionError("view remesh")):
            for _ in range(20):
                view.switch("flat")
                view.switch("folded")
        self.assertEqual(nodes, view.cached_nodes)
        self.assertEqual(before, (self.model.doc.UndoCount, self.model.doc.isTouched(),
                                  step.Operation, step.Visibility))
        name, base_name = step.Name, self.base.Name
        volumes = step.Shape.Volume, step.FlatShape.Volume
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "cut-history.FCStd")
            self.model.doc.saveAs(path)
            self.model.settle()
            App.closeDocument(self.model.doc.Name)
            self.model.doc = App.openDocument(path)
            self.model.settle()
            self.base = self.model.sheet = self.model.doc.getObject(base_name)
            self.fixture.view = self.base.ViewObject.Proxy
            step = self.model.doc.getObject(name)
            self.assertIs(step.BaseSheet, self.base)
            self.assertAlmostEqual(step.Shape.Volume, volumes[0], places=6)
            self.assertAlmostEqual(step.FlatShape.Volume, volumes[1], places=6)
            step.touch()
            self.base.touch()
            self.model.recompute()
            self.assertTrue(history.get_prepared(step).folded.isValid())
            self.model.edit(lambda: history.update_circle_step(step, radius=5))
            self.assertLess(step.Shape.Volume, volumes[0])

    def test_failed_cut_stays_editable_and_can_be_repaired(self):
        history = self.history()
        step = self.cut()
        self.model.edit(lambda: history.update_circle_step(step, radius=1000))
        self.assertIn("Invalid", step.State)
        self.model.edit(lambda: history.update_circle_step(step, radius=3))
        self.assertNotIn("Invalid", step.State)
        self.assertTrue(history.get_prepared(step).folded.isValid())

    def test_future_bases_are_rejected_before_creation(self):
        history = self.history()
        step = self.cut()
        self.button("Previous")
        count = len(self.model.doc.Objects)
        with self.assertRaisesRegex(RuntimeError, "History"):
            self.model.edit(lambda: history.create_circle_step(step, App.Vector(), 2))
        self.assertEqual(len(self.model.doc.Objects), count)

    def test_history_editor_edits_exact_cut_asynchronously_and_can_reload_stale_values(self):
        import SheetMetalCutGui as CutGui
        step = self.cut()
        widget = Gui.getMainWindow().findChild(QtWidgets.QListWidget, "VibeCADFeatureTimelineItems")
        self.assertIsNotNone(widget)
        def item():
            return next((widget.item(row) for row in range(widget.count())
                         if widget.item(row).data(QtCore.Qt.UserRole) == step.Name), None)
        self.fixture.wait_for(lambda: item() is not None)
        widget.scrollToItem(item())
        position = QtCore.QPointF(widget.visualItemRect(item()).center())
        panels, original = [], CutGui.CutPanel
        def panel(*args):
            result = original(*args)
            panels.append(result)
            return result
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.base)
        with patch.object(CutGui, "CutPanel", side_effect=panel):
            for kind in (QtCore.QEvent.MouseButtonPress, QtCore.QEvent.MouseButtonRelease,
                         QtCore.QEvent.MouseButtonDblClick):
                event = QtGui.QMouseEvent(kind, position, position, QtCore.Qt.LeftButton,
                                         QtCore.Qt.LeftButton, QtCore.Qt.NoModifier)
                QtWidgets.QApplication.sendEvent(widget.viewport(), event)
        self.assertEqual(len(panels), 1)
        editor = panels[0]
        self.addCleanup(editor.reject)
        self.assertIs(editor.step, step)
        undo = self.model.doc.UndoCount
        editor.radius.setValue(5)
        editor.apply_button.click()
        self.assertIsNotNone(editor.run, editor.message.text())
        self.fixture.wait_for(editor.run.future.done)
        self.assertEqual(editor.run.status()["phase"], "ready", editor.run.status())
        self.assertEqual(float(step.Radius), 5)
        self.assertEqual(self.model.doc.UndoCount, undo + 1)
        active = Gui.getDocument(self.model.doc.Name).activeView()
        active.setAnimationEnabled(False)
        active.viewAxonometric()
        active.fitAll()
        Gui.updateGui()
        active.redraw()
        QtWidgets.QApplication.sync()
        screen = Gui.getMainWindow().windowHandle().screen()
        self.assertTrue(screen.grabWindow(Gui.getMainWindow().winId()).save(
            str(Path(os.environ["VIBECAD_TEST_OUTPUT"]) / "sheet-cut-history-editor.png")))
        self.model.edit(lambda: self.history().update_circle_step(step, radius=3))
        editor.radius.setValue(6)
        editor.apply_button.click()
        self.assertEqual(float(step.Radius), 3)
        self.assertIn("changed", editor.message.text())
        editor.reload_button.click()
        self.assertEqual(editor.radius.value(), 3)

    def test_foreign_document_editor_cannot_redirect_to_the_active_document(self):
        import SheetMetalCutGui as CutGui
        step = self.cut()
        other = App.newDocument("OtherCutHistory")
        self.addCleanup(lambda: App.closeDocument(other.Name))
        self.addCleanup(lambda: Gui.Control.closeDialog() if Gui.Control.activeDialog() else None)
        with self.assertRaisesRegex(RuntimeError, "Activate"):
            CutGui.open_editor(step)
        self.assertFalse(Gui.Control.activeDialog())

    def test_cut_tree_rows_switch_views_and_open_the_exact_cut_editor(self):
        import SheetMetalCutGui as CutGui
        import SheetMetalOperations as Operations
        from SMTests.testSheetTree import TestSheetTree
        step = self.cut()
        tree = TestSheetTree()
        tree.fixture, tree.model = self.fixture, self.model
        tree.sheet, tree.view = step, step.ViewObject.Proxy
        tree.row("cut")
        before = (Operations.capture_revision(step), self.model.doc.UndoCount,
                  len(self.model.doc.Objects), step.Visibility)
        nodes = tree.view.cached_nodes
        tree.activate("representation:flat")
        self.assertEqual(tree.view.mode, "flat")
        tree.activate("representation:folded")
        self.assertEqual(tree.view.mode, "folded")
        self.assertEqual(nodes, tree.view.cached_nodes)
        self.assertEqual(before, (Operations.capture_revision(step), self.model.doc.UndoCount,
                                 len(self.model.doc.Objects), step.Visibility))
        original, opened = CutGui.CutPanel, []
        def panel(*args):
            result = original(*args)
            opened.append(result)
            return result
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.base)
        with patch.object(CutGui, "CutPanel", side_effect=panel):
            tree.activate("cut")
        self.assertEqual(len(opened), 1)
        self.addCleanup(opened[0].reject)
        self.assertIs(opened[0].step, step)
        self.model.edit(lambda: self.history().update_circle_step(step, radius=7))
        def radius_updated():
            rows = tree.rows()
            if "cut" not in rows:
                return False
            _, index = rows["cut"]
            return index.sibling(index.row(), 1).data() == "Radius 7 mm"
        self.fixture.wait_for(radius_updated)

    def test_upstream_changes_invalidate_the_tip_before_recompute(self):
        history = self.history()
        step = self.cut()
        old_geometry = history.get_prepared(step)
        def change():
            self.base.KFactor = .35
            self.base.Material = "Steel"
            self.model.doc.BaseBend.Thickness = 2
            with self.assertRaises(RuntimeError):
                history.get_prepared(step)
        self.model.edit(change)
        geometry = history.get_prepared(step)
        self.assertIsNot(geometry, old_geometry)
        self.assertIs(geometry.mapping, self.base.Proxy._geometry.mapping)
        self.assertAlmostEqual(geometry.mapping.thickness, 2)
        self.assertEqual(len(history.definition(step)["operations"]), 1)
        self.assertTrue(step.Shape.isValid())
        self.assertTrue(step.FlatShape.isValid())

    def test_folded_edit_updates_the_same_cut_identity_and_developed_parameters(self):
        history = self.history()
        region, folded, _ = self.model.bend_pick()
        step = self.cut()
        before = step.Shape.Volume, step.OperationId, step.CenterU, step.CenterV, len(self.model.doc.Objects)
        self.model.edit(lambda: history.update_circle_step(
            step, radius=3, center=folded, representation="folded", region=region))
        self.assertGreater(step.Shape.Volume, before[0])
        self.assertEqual(step.OperationId, before[1])
        self.assertAlmostEqual(step.CenterU, before[2])
        self.assertAlmostEqual(step.CenterV, before[3])
        self.assertEqual(len(self.model.doc.Objects), before[4])
        self.assertEqual(len(history.definition(step)["operations"]), 1)

    def test_existing_view_commands_switch_cut_states_without_document_changes(self):
        import SheetMetalGui
        step = self.cut()
        SheetMetalGui.ensure_commands_registered()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(step)
        before = self.model.doc.UndoCount, step.Operation, step.PreparedInputHash, step.Visibility
        nodes = step.ViewObject.Proxy.cached_nodes
        with patch("SheetMetalPresentation.prepare_pair", side_effect=AssertionError("remesh")):
            Gui.runCommand("SheetMetal_ViewFlat")
            self.assertEqual(step.ViewObject.Proxy.mode, "flat")
            Gui.runCommand("SheetMetal_ViewFolded")
            self.assertEqual(step.ViewObject.Proxy.mode, "folded")
        self.assertEqual(step.ViewObject.Proxy.cached_nodes, nodes)
        self.assertEqual(before, (self.model.doc.UndoCount, step.Operation,
                                  step.PreparedInputHash, step.Visibility))

    def test_creation_does_not_reimport_an_existing_shared_cut_state(self):
        import SheetMetalOperations as Operations
        step = self.cut()
        face = next(i + 1 for i, face in enumerate(step.Shape.Faces)
                    if isinstance(face.Surface, Part.Plane) and face.Area > 100)
        revision = Operations.capture_source_revision(step)
        before = len(self.model.doc.Objects), self.model.doc.UndoCount
        with patch("SheetMetalPresentation.create_presented_sheet", side_effect=AssertionError("create")):
            with self.assertRaisesRegex(ValueError, "already"):
                Operations.start_creation(step, f"Face{face}", expected_revision=revision)
        self.assertEqual(before, (len(self.model.doc.Objects), self.model.doc.UndoCount))

    def test_mutations_reject_worker_thread_access_before_document_changes(self):
        history = self.history()
        step = self.cut()
        center = self.model.bend_pick()[2]
        before = len(self.model.doc.Objects), float(step.Radius), self.model.doc.UndoCount
        def attempt():
            with ThreadPoolExecutor(max_workers=1) as worker:
                for action in (lambda: history.update_circle_step(step, radius=3),
                               lambda: history.create_circle_step(self.base, center, 2)):
                    with self.assertRaisesRegex(RuntimeError, "GUI thread"):
                        worker.submit(action).result()
        self.model.edit(attempt)
        self.assertEqual(before, (len(self.model.doc.Objects), float(step.Radius), self.model.doc.UndoCount))

    def test_changed_output_cannot_be_used_as_prepared_cut_geometry(self):
        history = self.history()
        step = self.cut()
        displaced = step.Shape.copy()
        displaced.translate(App.Vector(100, 0, 0))
        step.Shape = displaced
        with self.assertRaisesRegex(RuntimeError, "Prepare"):
            history.get_prepared(step)

    def test_deleted_cut_finishes_its_deferred_edit_as_closed(self):
        import SheetMetalOperations as Operations
        import weakref
        step = self.cut()
        # Hold only the completion callback, after native geometry is allowed
        # to settle. Deletion can occur before that GUI callback is delivered.
        with patch.object(Operations, "_poll"):
            run = self.history().start_radius_edit(step, 5,
                expected_revision=Operations.capture_revision(step))
            self.model.settle()
            self.model.edit(lambda: self.model.doc.removeObject(step.Name))
        Operations._poll(weakref.ref(run))
        self.assertTrue(run.future.done())
        self.assertEqual(run.status()["phase"], "closed")

    def test_deleted_cut_editor_reports_closed_without_redirecting_or_mutating(self):
        import SheetMetalCutGui as CutGui
        import SheetMetalOperations as Operations
        step = self.cut()
        editor = CutGui.CutPanel(step, Operations.capture_revision(step))
        self.addCleanup(editor.closed)
        self.model.edit(lambda: self.model.doc.removeObject(step.Name))
        before = self.model.doc.UndoCount, len(self.model.doc.Objects)
        editor.reload()
        self.assertIn("closed", editor.message.text())
        editor.apply()
        self.assertIn("closed", editor.message.text())
        self.assertIsNone(editor.run)
        self.assertEqual(before, (self.model.doc.UndoCount, len(self.model.doc.Objects)))
