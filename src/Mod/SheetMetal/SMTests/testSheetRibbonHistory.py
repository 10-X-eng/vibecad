# SPDX-License-Identifier: LGPL-2.1-or-later
"""Ribbon edits create native History states through the shared async service."""

import unittest
import os
from pathlib import Path
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import SheetMetalGui
import SheetMetalCutHistory as History
from SMTests import testPresentation, testProfileCuts


class TestSheetRibbonHistory(unittest.TestCase):
    def setUp(self):
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.base = self.model.sheet
        self.addCleanup(Gui.Selection.clearSelection)

    def panel(self, mode="cuts", sheet=None):
        panel = SheetMetalGui.SheetPanel(self.base if sheet is None else sheet, mode, True)
        self.addCleanup(panel.close)
        return panel

    def finish(self, panel):
        self.assertIsNotNone(panel.run, panel.message.text())
        self.fixture.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.status()["phase"], "ready", panel.run.status())
        self.fixture.wait_for(lambda: panel.sheet.ViewObject.Proxy.ready)
        return panel.sheet

    def pick(self, panel, point=None):
        sheet = panel.sheet
        view = Gui.getDocument(self.model.doc.Name).activeView()
        view.setAnimationEnabled(False)
        sheet.ViewObject.Proxy.switch("flat")
        geometry = History.get_prepared(sheet)
        view.setCameraOrientation(App.Rotation(App.Vector(0, 0, 1), geometry.normal))
        for obj in self.model.doc.Objects:
            if hasattr(obj, "Visibility"):
                obj.Visibility = obj is sheet
        view.fitAll()
        view.redraw()
        self.model.settle()
        panel.refresh()
        point = self.model.bend_pick()[2] if point is None else point
        panel.use_pick(sheet.ViewObject.Proxy.pick_screen(view, view.getPointOnScreen(point)))

    def hole(self, panel, point=None, radius=4):
        self.pick(panel, point)
        panel.radius.setValue(radius)
        panel.apply_button.click()
        return self.finish(panel)

    def test_ribbon_command_creates_one_native_hole_and_preserves_flat_view(self):
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("SMWorkbench")
        controller = Gui.getMainWindow().findChild(QtCore.QObject, "VibeCADRibbonController")
        self.fixture.wait_for(lambda: controller.property("VibeCADActiveSurfaceId") == "sheet_metal")
        SheetMetalGui.ensure_commands_registered()
        Gui.Selection.clearSelection()
        Gui.Selection.addSelection(self.base)
        original, panels = SheetMetalGui.SheetPanel, []
        def opened(*args):
            panel = original(*args)
            panels.append(panel)
            return panel
        with patch.object(SheetMetalGui, "SheetPanel", side_effect=opened):
            Gui.runCommand("SheetMetal_EditCuts")
        self.assertEqual(len(panels), 1)
        panel = panels[0]
        self.addCleanup(panel.reject)
        self.pick(panel)
        before = self.model.doc.UndoCount
        panel.radius.setValue(4)
        panel.apply_button.click()
        step = self.finish(panel)
        self.assertIsInstance(step.Proxy, History.CircleCutFeature)
        self.assertIs(step.BaseSheet, self.base)
        self.assertEqual(step.ViewObject.Proxy.mode, "flat")
        self.assertEqual(self.model.doc.UndoCount, before+1)
        self.assertEqual(self.base.Definition, '{"operations":[],"version":1}')
        self.assertEqual(step.VibeCADTimelineRole, "operation")
        self.assertFalse(self.base.Visibility)
        from SMTests.testSheetTree import TestSheetTree
        tree = TestSheetTree()
        tree.fixture, tree.model = self.fixture, self.model
        tree.sheet, tree.view = step, step.ViewObject.Proxy
        tree.row("representation:flat")
        Gui.updateGui()
        Gui.getDocument(self.model.doc.Name).activeView().redraw()
        QtWidgets.QApplication.sync()
        window = Gui.getMainWindow()
        self.assertTrue(window.windowHandle().screen().grabWindow(window.winId()).save(
            str(Path(os.environ["VIBECAD_TEST_OUTPUT"])/"sheet-native-cuts-panel.png")))

    def test_deferred_native_runner_creates_the_same_tree_and_history_state(self):
        import SheetMetalHistoryOperations as Operations
        from VibeCADCore import get_service
        from VibeCADNativeMutation import NativeMutationRunner
        doc = self.model.doc
        prepared = Operations.prepare(self.base,
            {"operation": "add_circle", "center": list(self.model.bend_pick()[2]), "radius": 4},
            expected_revision=Operations.capture_revision(self.base))
        state = get_service().native_document_state_store()
        state.begin_native_authority(doc.Uid)
        ticket = state.begin_call(doc.Uid, "sheet_metal.edit")
        self.addCleanup(lambda: state.cancel_mutation(ticket))
        runner = NativeMutationRunner(state)
        executions, requests = [], []
        undo, volume = doc.UndoCount, self.base.Shape.Volume
        def transaction(**kwargs):
            execution = runner.start_deferred(ticket=ticket, reauthorize_turn=lambda: None, **kwargs)
            executions.append(execution)
            return execution.result
        def queue(document):
            self.assertIs(document, doc)
            self.assertFalse(doc.HasPendingTransaction)
            self.assertEqual(doc.getBookedTransactionID(), 0)
            self.assertIsNone(state.completed_mutation_receipt(ticket))
            requests.append(doc.recomputeAsyncTracked())
        run = Operations.start(prepared, transaction_runner=transaction, recompute_queue=queue)
        self.fixture.wait_for(run.future.done)
        self.assertEqual(run.status()["phase"], "ready", run.status())
        self.assertEqual(len(executions), 1)
        self.assertEqual(len(requests), 1)
        self.assertTrue(requests[0]["origin"])
        self.assertIsNone(state.completed_mutation_receipt(ticket))
        self.assertEqual(doc.UndoCount, undo+1)
        step = doc.getObject(run.status()["object_name"])
        self.assertIsInstance(step.Proxy, History.CircleCutFeature)
        self.assertIs(step.BaseSheet, self.base)
        self.assertLess(step.Shape.Volume, volume)
        self.assertEqual(step.VibeCADTimelineRole, "operation")
        self.assertEqual(step.VibeCADTimelineEditCommand, "SheetMetal_EditHistoryCut")
        self.assertIn(step, doc.findObjects("App::DocumentTimeline")[0].Operations)
        self.assertEqual(executions[0].prepared.created[0].object_name, step.Name)
        self.fixture.wait_for(lambda: step.ViewObject.Proxy.ready)
        from SMTests.testSheetTree import TestSheetTree
        tree = TestSheetTree()
        tree.fixture, tree.model = self.fixture, self.model
        tree.sheet, tree.view = step, step.ViewObject.Proxy
        tree.row("representation:flat")
        tree.row("representation:folded")

    def test_failed_custom_queue_leaves_an_editable_history_entry(self):
        import SheetMetalHistoryOperations as Operations
        prepared = Operations.prepare(self.base,
            {"operation": "add_circle", "center": list(self.model.bend_pick()[2]), "radius": 4},
            expected_revision=Operations.capture_revision(self.base))
        undo = self.model.doc.UndoCount
        def unavailable(document):
            raise RuntimeError("queue unavailable")
        run = Operations.start(prepared, recompute_queue=unavailable)
        self.assertTrue(run.future.done())
        self.assertEqual(run.status()["phase"], "failed")
        self.assertEqual(self.model.doc.UndoCount, undo+1)
        step = self.model.doc.getObject(run.status()["object_name"])
        self.assertIsInstance(step.Proxy, History.CircleCutFeature)
        self.assertEqual(step.VibeCADTimelineEditCommand, "SheetMetal_EditHistoryCut")
        self.model.recompute()
        self.assertTrue(History.get_prepared(step).folded.isValid())

    def test_panel_follows_new_states_and_resizes_an_earlier_hole(self):
        from SheetMetalEditGeometry import SheetGeometry
        panel = self.panel()
        first = self.hole(panel)
        center = next(region.flat_face().CenterOfMass for region in first.Proxy._geometry.mapping.regions
                      if region.kind == "plane")
        second = self.hole(panel, center, 2)
        self.assertIs(second.BaseSheet, first)
        self.assertEqual(panel.operations.count(), 2)
        panel.operations.setCurrentIndex(panel.operations.findData(first.OperationId))
        count, volume = len(self.model.doc.Objects), second.Shape.Volume
        panel.radius.setValue(3)
        with patch.object(SheetGeometry, "prepare", side_effect=AssertionError("repeat Unfold")):
            panel.resize_button.click()
            self.finish(panel)
        self.assertIs(panel.sheet, second)
        self.assertEqual(float(first.Radius), 3)
        self.assertEqual(len(self.model.doc.Objects), count)
        self.assertGreater(second.Shape.Volume, volume)

    def test_inspection_identifies_native_history_and_exact_shared_sketch(self):
        import json
        import SheetMetalHistoryOperations as Operations
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, _ = profiles.slot()
        profile = self.model.edit(lambda: History.create_profile_step(self.base, sketch))
        center = next(region.flat_face().CenterOfMass
                      for region in profile.Proxy._geometry.mapping.regions if region.kind == "plane")
        hole = self.model.edit(lambda: History.create_circle_step(profile, center, 2))
        self.model.edit(lambda: [setattr(obj, "Label", "Same label") for obj in (profile, hole, sketch)])
        timeline = self.model.doc.findObjects("App::DocumentTimeline")[0]
        before = (self.model.doc.UndoCount, self.model.doc.isTouched(),
                  tuple(self.model.doc.Objects), Gui.Selection.getSelection(),
                  [(obj.Name, obj.Visibility) for obj in self.model.doc.Objects
                   if hasattr(obj, "Visibility")])
        with patch.object(History.CircleCutFeature, "execute", side_effect=AssertionError("recompute")), \
                patch("SheetMetalPresentation.prepare_pair", side_effect=AssertionError("remesh")):
            result = Operations.inspect(hole)
            json.dumps(result, allow_nan=False)
        history = result["history"]
        self.assertEqual(history["position"], timeline.Position)
        self.assertEqual(history["operation_count"], len(timeline.Operations))
        entries = history["states"]
        self.assertEqual([entry["target"]["object_name"] for entry in entries],
                         [self.base.Name, profile.Name, hole.Name])
        self.assertEqual([entry["kind"] for entry in entries], ["sheet", "profile", "circle"])
        for entry, obj in zip(entries, (self.base, profile, hole)):
            self.assertEqual(entry["target"]["document_uid"], self.model.doc.Uid)
            self.assertEqual(entry["label"], obj.Label)
            self.assertEqual(entry["timeline_index"], list(timeline.Operations).index(obj))
            self.assertEqual(entry["active"], self.model.doc.isObjectUsableAtCurrentTimelinePosition(obj))
        self.assertEqual(entries[1]["predecessor"], entries[0]["target"])
        self.assertEqual(entries[2]["predecessor"], entries[1]["target"])
        self.assertEqual(entries[1]["profile"],
                         {"document_uid": self.model.doc.Uid, "object_name": sketch.Name})
        self.assertEqual(entries[1]["operation_id"], profile.OperationId)
        self.assertEqual(entries[2]["operation_id"], hole.OperationId)
        self.assertEqual(entries[1]["editor_command"], "SheetMetal_EditHistoryProfile")
        self.assertEqual(entries[2]["editor_command"], "SheetMetal_EditHistoryCut")
        self.assertEqual(before, (self.model.doc.UndoCount, self.model.doc.isTouched(),
                         tuple(self.model.doc.Objects), Gui.Selection.getSelection(),
                         [(obj.Name, obj.Visibility) for obj in self.model.doc.Objects
                          if hasattr(obj, "Visibility")]))

    def test_inspection_retains_suppressed_history_state_and_display_identity(self):
        import SheetMetalHistoryOperations as Operations
        hole = self.model.edit(lambda: History.create_circle_step(self.base, self.model.bend_pick()[2], 4))
        self.model.edit(lambda: setattr(hole, "Suppressed", True))
        self.fixture.wait_for(lambda: self.base.ViewObject.Proxy.ready)
        Operations.switch(hole, "flat")
        result = Operations.inspect(hole)
        self.assertEqual(result["representation"], "flat")
        self.assertEqual(result["history"]["display_state"]["object_name"], self.base.Name)
        entry = result["history"]["states"][-1]
        self.assertEqual(entry["target"]["object_name"], hole.Name)
        self.assertTrue(entry["suppressed"])
        self.assertFalse(entry["active"])
        self.assertEqual(result["definition"]["operations"], [])

    def test_inspection_keeps_missing_sketch_cut_available_for_repair(self):
        import SheetMetalHistoryOperations as Operations
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, _ = profiles.slot()
        step = self.model.edit(lambda: History.create_profile_step(self.base, sketch))
        operation_id = step.OperationId
        self.model.edit(lambda: setattr(step, History._profile_key(step), None))
        result = Operations.inspect(step)
        self.assertFalse(result["prepared"])
        entry = result["history"]["states"][-1]
        self.assertEqual(entry["operation_id"], operation_id)
        self.assertIsNone(entry["profile"])
        self.assertTrue(entry["profile_error"])
        self.assertEqual(entry["editor_command"], "SheetMetal_EditHistoryProfile")

    def test_inspection_uses_restored_document_identities(self):
        import tempfile
        import SheetMetalHistoryOperations as Operations
        hole = self.model.edit(lambda: History.create_circle_step(self.base, self.model.bend_pick()[2], 4))
        name, base_name, operation_id = hole.Name, self.base.Name, hole.OperationId
        with tempfile.TemporaryDirectory() as directory:
            path = str(Path(directory) / "inspect-history.FCStd")
            self.model.doc.saveAs(path)
            self.model.settle()
            App.closeDocument(self.model.doc.Name)
            self.model.doc = App.openDocument(path)
            self.model.settle()
            self.base = self.model.sheet = self.model.doc.getObject(base_name)
            self.fixture.view = self.base.ViewObject.Proxy
            hole = self.model.doc.getObject(name)
            self.base.touch()
            self.model.recompute()
            result = Operations.inspect(hole)
            entry = result["history"]["states"][-1]
            self.assertEqual(entry["operation_id"], operation_id)
            self.assertEqual(entry["target"],
                             {"document_uid": self.model.doc.Uid, "object_name": name})
            self.assertEqual(entry["predecessor"],
                             {"document_uid": self.model.doc.Uid, "object_name": base_name})
            self.assertEqual(entry["editor_command"], "SheetMetal_EditHistoryCut")

    def test_sketch_creation_and_suppression_keep_the_original_profile(self):
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, _ = profiles.slot()
        panel = self.panel()
        panel.profiles.setCurrentIndex(panel.profiles.findData(sketch.Name))
        panel.profile_button.click()
        step = self.finish(panel)
        self.assertIsInstance(step.Proxy, History.ProfileCutFeature)
        self.assertIs(History.get_profile(step), sketch)
        self.assertEqual(panel.operations.count(), 1)
        self.assertIn("Suppress", panel.remove_button.text())
        panel.remove_button.click()
        self.fixture.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.status()["phase"], "ready", panel.run.status())
        self.assertTrue(step.Suppressed)
        self.assertIs(self.model.doc.getObject(sketch.Name), sketch)
        self.assertIn("Restore", panel.remove_button.text())
        panel.remove_button.click()
        self.finish(panel)
        self.assertFalse(step.Suppressed)

    def test_material_and_dimensions_from_tip_prepare_the_whole_chain(self):
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, _ = profiles.slot()
        step = self.model.edit(lambda: History.create_profile_step(self.base, sketch))
        self.fixture.wait_for(lambda: step.ViewObject.Proxy.ready)
        panel = self.panel("parameters", step)
        panel.parameters["thickness"].setValue(2)
        panel.apply_button.click()
        self.finish(panel)
        self.assertIs(panel.sheet, step)
        self.assertAlmostEqual(History.get_prepared(step).mapping.thickness, 2)
        material = self.panel("materials", step)
        material.material.setText("Steel")
        material.k_factor.setValue(.35)
        material.apply_button.click()
        self.finish(material)
        self.assertEqual(self.base.Material, "Steel")
        self.assertEqual(self.base.KFactor, .35)

    def test_stale_panel_cannot_create_a_cut_after_an_external_edit(self):
        panel = self.panel()
        self.pick(panel)
        self.model.edit(lambda: setattr(self.base, "Material", "Changed"))
        before = len(self.model.doc.Objects), self.model.doc.UndoCount
        panel.apply_button.click()
        self.assertIsNone(panel.run)
        self.assertEqual(before, (len(self.model.doc.Objects), self.model.doc.UndoCount))

    def test_direct_legacy_panel_still_edits_its_original_shared_definition(self):
        panel = SheetMetalGui.SheetPanel(self.base, "cuts")
        self.addCleanup(panel.close)
        result = self.hole(panel)
        self.assertIs(result, self.base)
        self.assertEqual(panel.operations.count(), 1)
        self.assertIn("Remove", panel.remove_button.text())

    def test_failed_new_hole_remains_in_the_panel_and_can_be_repaired(self):
        panel = self.panel()
        self.pick(panel)
        panel.radius.setValue(1000)
        panel.apply_button.click()
        self.assertIsNotNone(panel.run)
        self.fixture.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.status()["phase"], "failed", panel.run.status())
        step = panel.sheet
        self.assertIsNot(step, self.base)
        self.assertIn("Invalid", step.State)
        self.assertEqual(panel.operations.currentData(), step.OperationId)
        panel.radius.setValue(3)
        panel.resize_button.click()
        self.finish(panel)
        self.assertIs(panel.sheet, step)
        self.assertTrue(History.get_prepared(step).folded.isValid())

    def test_panel_opens_native_sketch_and_releases_its_task_dialog(self):
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, _ = profiles.slot()
        panel = self.panel()
        Gui.Control.showDialog(panel)
        self.addCleanup(panel.reject)
        gui = Gui.getDocument(self.model.doc.Name)
        def finish_editor():
            if gui.getInEdit() is not None:
                gui.resetEdit()
            self.model.settle()
        self.addCleanup(finish_editor)
        panel.profiles.setCurrentIndex(panel.profiles.findData(sketch.Name))
        panel.profile_button.click()
        step = self.finish(panel)
        panel.edit_profile_button.click()
        self.assertTrue(panel._closed)
        self.assertIsNotNone(gui.getInEdit())
        self.assertIs(gui.getInEdit().Object, sketch)
        self.assertIs(History.get_profile(step), sketch)

    def test_native_panel_keeps_legacy_cut_edits_compatible(self):
        import SheetMetalEditable as Editable
        identity = self.model.edit(lambda: Editable.add_circle_cut(self.base, self.model.bend_pick()[2], 4))
        self.fixture.wait_for(lambda: self.base.ViewObject.Proxy.ready)
        panel = self.panel()
        self.assertEqual(panel.operations.currentData(), identity)
        self.assertIn("Remove", panel.remove_button.text())
        panel.radius.setValue(3)
        panel.resize_button.click()
        self.finish(panel)
        self.assertIs(panel.sheet, self.base)
        panel.remove_button.click()
        self.finish(panel)
        self.assertEqual(panel.operations.count(), 0)

    def test_suppressed_editor_cannot_restore_a_future_cut(self):
        import SheetMetalHistoryOperations as Commands
        from SMTests.testSheetCutHistory import TestSheetCutHistory
        panel = self.panel()
        step = self.hole(panel)
        panel.remove_button.click()
        self.fixture.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.status()["phase"], "ready", panel.run.status())
        revision = Commands.capture_revision(step)
        helper = TestSheetCutHistory()
        helper.fixture, helper.model = self.fixture, self.model
        helper.button("Previous")
        before = self.model.doc.UndoCount, step.Suppressed
        with self.assertRaisesRegex(RuntimeError, "History"):
            Commands.prepare(step, {"operation": "set_suppressed", "operation_id": step.OperationId,
                                    "suppressed": False}, expected_revision=revision)
        self.assertEqual(before, (self.model.doc.UndoCount, step.Suppressed))

    def test_panel_view_switches_do_not_inspect_or_serialize_the_feature_chain(self):
        panel = self.panel()
        step = self.hole(panel)
        before = self.model.doc.UndoCount, step.Visibility, step.PreparedInputHash
        nodes = step.ViewObject.Proxy.cached_nodes
        with patch.object(History, "_chain", side_effect=AssertionError("inspect History")), \
                patch.object(History, "_edit_snapshot", side_effect=AssertionError("serialize sketch")), \
                patch("SheetMetalPresentation.prepare_pair", side_effect=AssertionError("remesh")):
            panel.switch("folded")
            self.assertEqual(step.ViewObject.Proxy.mode, "folded")
            panel.switch("flat")
            self.assertEqual(step.ViewObject.Proxy.mode, "flat")
        self.assertEqual(nodes, step.ViewObject.Proxy.cached_nodes)
        self.assertEqual(before, (self.model.doc.UndoCount, step.Visibility, step.PreparedInputHash))

    def test_deleted_native_panel_reports_closed_without_mutating(self):
        panel = self.panel()
        step = self.hole(panel)
        self.model.edit(lambda: self.model.doc.removeObject(step.Name))
        before = self.model.doc.UndoCount, len(self.model.doc.Objects)
        panel.refresh()
        self.assertIn("closed", panel.message.text())
        panel.radius.setValue(3)
        panel.resize_cut()
        self.assertIn("closed", panel.message.text())
        self.assertEqual(before, (self.model.doc.UndoCount, len(self.model.doc.Objects)))

    def test_history_rollback_finishes_pending_creation_as_superseded(self):
        import weakref
        import SheetMetalOperations as Operations
        from SMTests.testSheetCutHistory import TestSheetCutHistory
        panel = self.panel()
        self.pick(panel)
        with patch.object(Operations, "_poll"):
            panel.apply_button.click()
            run = panel.run
            self.assertIsNotNone(run)
            self.model.settle()
            helper = TestSheetCutHistory()
            helper.fixture, helper.model = self.fixture, self.model
            helper.button("Previous")
        Operations._poll(weakref.ref(run))
        self.assertTrue(run.future.done())
        self.assertEqual(run.status()["phase"], "superseded", run.status())

    def test_prepared_edit_cannot_redirect_its_legacy_target_to_another_document(self):
        from dataclasses import replace
        import SheetMetalHistoryOperations as Commands
        other = testPresentation.TestPresentation()
        self.addCleanup(other.doCleanups)
        other.setUp()
        foreign = other.fixture.sheet
        request = Commands.prepare(self.base, {"operation": "set_material", "material": "Wrong"},
                                   expected_revision=Commands.capture_revision(self.base))
        forged = replace(request, _legacy=replace(request._legacy, _sheet=foreign))
        before = self.base.Material, foreign.Material, self.model.doc.UndoCount, other.fixture.doc.UndoCount
        with self.assertRaises((RuntimeError, ValueError)):
            Commands.start(forged)
        self.assertEqual(before, (self.base.Material, foreign.Material,
                                  self.model.doc.UndoCount, other.fixture.doc.UndoCount))

    def test_missing_sketch_keeps_its_panel_open_for_replacement(self):
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, _ = profiles.slot()
        panel = self.panel()
        Gui.Control.showDialog(panel)
        self.addCleanup(panel.reject)
        panel.profiles.setCurrentIndex(panel.profiles.findData(sketch.Name))
        panel.profile_button.click()
        step = self.finish(panel)
        self.model.edit(lambda: self.model.doc.removeObject(sketch.Name))
        panel.refresh()
        panel.edit_profile_button.click()
        self.assertFalse(panel._closed)
        self.assertTrue(Gui.Control.activeDialog())
        self.assertIn("missing", panel.message.text())
        replacement, _ = profiles.slot()
        panel.refresh()
        panel.profiles.setCurrentIndex(panel.profiles.findData(replacement.Name))
        panel.replace_profile_button.click()
        self.finish(panel)
        self.assertIs(History.get_profile(step), replacement)
        self.assertTrue(Gui.Control.activeDialog())
