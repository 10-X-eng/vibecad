# SPDX-License-Identifier: LGPL-2.1-or-later
"""Restored edit mappings are detached work, never a document mutation."""

import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

import FreeCAD as App

import SheetMetalEditable as Editable
import SheetMetalCutHistory as History
import SheetMetalHistoryOperations as Shared
import SheetMetalPreparation as Preparation
from SMTests import testPresentation, testProfileCuts


class TestSheetPreparation(unittest.TestCase):
    def setUp(self):
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.sheet = self.model.sheet

    def reopen(self, target):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        filename = str(Path(directory.name) / "mapping.FCStd")
        name = target.Name
        self.model.doc.saveAs(filename)
        self.model.settle()
        App.closeDocument(self.model.doc.Name)
        self.model.doc = App.openDocument(filename)
        self.model.settle()
        self.sheet = self.model.sheet = self.model.doc.getObject("EditableSheet")
        target = self.model.doc.getObject(name)
        self.fixture.view = target.ViewObject.Proxy
        self.fixture.wait_for(lambda: self.fixture.view.ready)
        self.assertIsNone(target.Proxy._geometry)
        return target

    def snapshot(self):
        doc = self.model.doc
        return (Shared.capture_revision(self.sheet), doc.UndoCount, tuple(doc.UndoNames),
                doc.isTouched(), tuple((obj.Name, tuple(obj.State),
                    getattr(obj, "Visibility", None), getattr(obj, "PreparedInputHash", None))
                    for obj in doc.Objects))

    def prepare(self, target):
        run = Preparation.start_preparation(target, expected_revision=Shared.capture_revision(target))
        self.fixture.wait_for(run.future.done)
        return run.future.result()

    def test_reopened_legacy_cut_prepares_off_gui_without_document_changes(self):
        self.model.edit(lambda: Editable.add_circle_cut(self.sheet, self.model.bend_pick()[2], 2))
        target = self.reopen(self.sheet)
        before = self.snapshot()
        folded, flat = target.Shape, target.FlatShape
        calls, original = [], Preparation._build

        def build(*args):
            calls.append(threading.get_ident())
            return original(*args)

        with patch.object(Preparation, "_build", side_effect=build), \
                patch.object(Editable.EditableSheetFeature, "execute", side_effect=AssertionError("recompute")):
            result = self.prepare(target)
        self.assertEqual(result["input_hash"], target.PreparedInputHash)
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(target.Shape.isSame(folded))
        self.assertTrue(target.FlatShape.isSame(flat))
        geometry = Editable.get_state_geometry(target)
        self.assertTrue(geometry.folded.isValid())
        self.assertAlmostEqual(geometry.flat.Volume, flat.Volume, places=5)
        self.assertTrue(calls)
        self.assertTrue(all(identity != threading.get_ident() for identity in calls))
        # The prepared cache supports a real subsequent edit, not only a read.
        pick = next(region.flat_face().CenterOfMass for region in geometry.mapping.regions
                    if region.kind == "plane")
        hole = self.model.edit(lambda: History.create_circle_step(target, pick, 1))
        self.assertTrue(History.get_prepared(hole).folded.isValid())
        self.assertLess(hole.FlatShape.Volume, flat.Volume)

    def test_reopened_mixed_chain_and_suppression_share_one_mapping(self):
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, _ = profiles.slot()
        slot = self.model.edit(lambda: History.create_profile_step(self.sheet, sketch))
        center = next(region.flat_face().CenterOfMass for region in slot.Proxy._geometry.mapping.regions
                      if region.kind == "plane")
        hole = self.model.edit(lambda: History.create_circle_step(slot, center, 2))
        self.model.edit(lambda: setattr(hole, "Suppressed", True))
        target = self.reopen(hole)
        before = self.snapshot()
        self.prepare(target)
        self.assertEqual(self.snapshot(), before)
        root, steps = History._chain(target)
        geometry = Editable.get_state_geometry(root)
        for step in steps:
            current = Editable.get_state_geometry(step)
            self.assertIs(current.mapping, geometry.mapping)
            self.assertAlmostEqual(current.flat.Volume, step.FlatShape.Volume, places=5)
        self.assertIs(target.Proxy._geometry, target.BaseSheet.Proxy._geometry)

    def delayed(self):
        started, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        original = Preparation._build

        def build(*args):
            started.set()
            release.wait()
            return original(*args)

        return started, release, build

    def test_source_edit_rejects_pending_mapping_without_partial_publication(self):
        target = self.reopen(self.sheet)
        started, release, build = self.delayed()
        with patch.object(Preparation, "_build", side_effect=build):
            run = Preparation.start_preparation(target, expected_revision=Shared.capture_revision(target))
            self.fixture.wait_for(started.is_set)
            self.model.doc.BaseBend.Thickness = 2
            release.set()
            self.fixture.wait_for(run.future.done)
        with self.assertRaises(RuntimeError):
            run.future.result()
        self.assertIsNone(target.Proxy._geometry)

    def test_cancelled_preparation_never_publishes(self):
        target = self.reopen(self.sheet)
        before = self.snapshot()
        started, release, build = self.delayed()
        with patch.object(Preparation, "_build", side_effect=build):
            run = Preparation.start_preparation(target, expected_revision=Shared.capture_revision(target))
            self.fixture.wait_for(started.is_set)
            self.assertTrue(run.future.cancel())
            release.set()
            self.fixture.wait_for(lambda: run.finished)
        self.assertIsNone(target.Proxy._geometry)
        self.assertEqual(self.snapshot(), before)

    def test_saved_fingerprint_mismatch_rejects_all_mapping_publication(self):
        target = self.reopen(self.sheet)
        target.PreparedInputHash = "0" * 64
        target.purgeTouched()
        with self.assertRaisesRegex(RuntimeError, "fingerprint"):
            self.prepare(target)
        self.assertIsNone(target.Proxy._geometry)

    def test_saved_cut_values_require_exact_native_float_serialization(self):
        import json
        from types import SimpleNamespace
        saved = {"id": "saved-cut", "kind": "circle",
                 "center": [2.9560017383895093e-12, -497.7478758388962], "radius": 3.0}
        current = {**saved, "center": [2.956e-12, saved["center"][1]]}
        step = SimpleNamespace(Operation=json.dumps(saved))
        with patch.object(History, "_operation", return_value=current):
            self.assertEqual(Preparation._restored_operation(step), saved)
            for description in ("not-json", "null", json.dumps({**saved, "id": "other-cut"}),
                                json.dumps({**saved, "radius": 3.0001}),
                                json.dumps({**saved, "center": [0.0001, saved["center"][1]]})):
                with self.subTest(description=description):
                    step.Operation = description
                    self.assertEqual(Preparation._restored_operation(step), current)

    def test_ready_mapping_does_not_run_geometry_again(self):
        original = Editable.get_state_geometry(self.sheet)
        with patch.object(Preparation, "_build", side_effect=AssertionError("repeat geometry")):
            self.prepare(self.sheet)
            # Existing direct execute callers can still consume a warm cache;
            # only rebuilding a missing cache needs native recompute ownership.
            self.assertIs(Preparation.prepare_for_recompute(self.sheet), original)
        self.assertIs(Editable.get_state_geometry(self.sheet), original)

    def test_reopened_profile_edit_rebuilds_cold_ancestors_in_native_recompute(self):
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, radius = profiles.slot()
        cut = self.model.edit(lambda: History.create_profile_step(self.sheet, sketch))
        sketch_name = sketch.Name
        cut = self.reopen(cut)
        sketch = self.model.doc.getObject(sketch_name)
        before = self.sheet.Shape, self.sheet.FlatShape, self.sheet.PreparedInputHash
        volume = cut.FlatShape.Volume
        calls, build = [], Preparation._build

        def observe(*args):
            calls.append(threading.get_ident())
            return build(*args)

        with patch.object(Preparation, "_build", side_effect=observe):
            self.model.edit(lambda: sketch.setDatum(radius, App.Units.Quantity("3 mm")))
        self.assertNotIn("Invalid", cut.State)
        self.assertGreater(cut.FlatShape.Volume, volume)
        self.assertTrue(History.get_prepared(cut).folded.isValid())
        self.assertTrue(self.sheet.Shape.isSame(before[0]))
        self.assertTrue(self.sheet.FlatShape.isSame(before[1]))
        self.assertEqual(self.sheet.PreparedInputHash, before[2])
        self.assertTrue(calls)
        self.assertTrue(all(thread != threading.get_ident() for thread in calls))
        with patch.object(Preparation, "_build", side_effect=AssertionError("repeat unfold")):
            self.model.edit(lambda: sketch.setDatum(radius, App.Units.Quantity("2 mm")))
        self.assertNotIn("Invalid", cut.State)
        self.model.doc.undo()
        self.model.recompute()
        self.assertNotIn("Invalid", cut.State)
        self.model.doc.redo()
        self.model.recompute()
        self.assertNotIn("Invalid", cut.State)

    def test_reopened_circle_edit_rebuilds_cold_mixed_predecessors(self):
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, _ = profiles.slot()
        cut = self.model.edit(lambda: History.create_profile_step(self.sheet, sketch))
        geometry = History.get_prepared(cut)
        center = next(region.flat_face().CenterOfMass for region in geometry.mapping.regions
                      if region.kind == "plane")
        hole = self.model.edit(lambda: History.create_circle_step(cut, center, 1))
        hole = self.reopen(hole)
        volume = hole.FlatShape.Volume
        self.model.edit(lambda: setattr(hole, "Radius", 1.5))
        self.assertNotIn("Invalid", hole.State)
        self.assertLess(hole.FlatShape.Volume, volume)
        self.assertTrue(History.get_prepared(hole).folded.isValid())

    def test_reopened_profile_can_enter_and_leave_sketch_without_preparing_sheet(self):
        import FreeCADGui as Gui
        import SketcherGui
        profiles = testProfileCuts.TestProfileCuts()
        profiles.fixture = self.model
        sketch, _ = profiles.slot()
        cut = self.model.edit(lambda: History.create_profile_step(self.sheet, sketch))
        name = sketch.Name
        cut = self.reopen(cut)
        document = self.model.doc
        sketch = document.getObject(name)
        before = cut.PreparedInputHash, cut.FlatShape.Volume
        for _ in range(3):
            self.assertTrue(Gui.getDocument(document.Name).setEdit(sketch.Name))
            self.addCleanup(lambda: Gui.getDocument(self.model.doc.Name).resetEdit())
            result = SketcherGui.leaveActiveSketch(document.Name, str(document.Uid), sketch.Name)
            self.assertEqual(result["edit_mode"], "closed")
            self.model.settle()
            self.assertNotIn("Invalid", cut.State)
            self.assertEqual(cut.PreparedInputHash, before[0])
            self.assertAlmostEqual(cut.FlatShape.Volume, before[1], places=5)

    def test_recompute_preparation_does_not_bypass_saved_hash_or_dirty_inputs(self):
        hole = self.model.edit(lambda: History.create_circle_step(self.sheet, self.model.bend_pick()[2], 2))
        hole = self.reopen(hole)
        with self.assertRaisesRegex(RuntimeError, "native recompute ownership"):
            Preparation.prepare_for_recompute(self.sheet)
        self.sheet.PreparedInputHash = "wrong-saved-hash"
        self.sheet.purgeTouched()
        before = hole.Shape, hole.FlatShape
        self.model.edit(lambda: setattr(hole, "Radius", 3))
        self.assertIn("Invalid", hole.State)
        self.assertIsNone(self.sheet.Proxy._geometry)
        self.assertTrue(hole.Shape.isSame(before[0]))
        self.assertTrue(hole.FlatShape.isSame(before[1]))
        self.sheet.touch()
        self.model.recompute()
        self.assertNotIn("Invalid", hole.State)
        self.assertTrue(History.get_prepared(hole).folded.isValid())

    def test_prepared_ancestor_is_reused_for_a_restored_descendant(self):
        hole = self.model.edit(lambda: History.create_circle_step(self.sheet, self.model.bend_pick()[2], 2))
        target = self.reopen(hole)
        self.prepare(self.sheet)
        original = Editable.get_state_geometry(self.sheet)
        with patch.object(Editable.SheetGeometry, "prepare", side_effect=AssertionError("repeat unfold")):
            self.prepare(target)
        self.assertIs(Editable.get_state_geometry(self.sheet), original)
        self.assertIs(target.Proxy._base_geometry, original)
        self.assertIs(History.get_prepared(target).mapping, original.mapping)

    def test_closed_document_rejects_pending_mapping(self):
        target = self.reopen(self.sheet)
        started, release, build = self.delayed()
        with patch.object(Preparation, "_build", side_effect=build):
            run = Preparation.start_preparation(target, expected_revision=Shared.capture_revision(target))
            self.fixture.wait_for(started.is_set)
            App.closeDocument(self.model.doc.Name)
            # The fixture owns this replacement too; no user document is used.
            self.model.doc = App.newDocument("PreparationOther")
            release.set()
            self.fixture.wait_for(run.future.done)
        with self.assertRaises(RuntimeError):
            run.future.result()
        self.assertEqual(len(self.model.doc.Objects), 0)

    def panel(self, target):
        from SheetMetalGui import SheetPanel
        panel = SheetPanel(target, "cuts", history=True)
        self.addCleanup(panel.close)
        return panel

    def test_panel_prepares_reopened_sheet_and_can_add_a_native_cut(self):
        from SheetMetalGui import SheetPanel
        target = self.reopen(self.sheet)
        panel = self.panel(target)
        self.assertFalse(panel.prepare_button.isHidden())
        before = self.snapshot()
        panel.prepare_button.click()
        self.assertIsNotNone(panel.preparation_run, panel.message.text())
        self.fixture.wait_for(panel.preparation_run.future.done)
        panel.preparation_run.future.result()
        self.assertEqual(self.snapshot(), before)
        self.assertTrue(panel.prepare_button.isHidden())
        self.assertTrue(panel.apply_button.isEnabled())
        self.fixture.wait_for(lambda: target.ViewObject.Proxy._published[1] is not None
                              and not target.ViewObject.Proxy.pending)
        self.assertEqual(target.ViewObject.Proxy.current(), target.PreparedInputHash)
        geometry = Editable.get_state_geometry(target)
        center = next(region.flat_face().CenterOfMass for region in geometry.mapping.regions
                      if region.kind == "plane")
        panel._submit({"operation": "add_circle", "center": list(center), "radius": 1,
                       "representation": "flat", "region": None})
        self.assertIsNotNone(panel.run, panel.message.text())
        self.fixture.wait_for(panel.run.future.done)
        self.assertEqual(panel.run.future.result()["phase"], "ready")
        self.assertIs(panel.sheet.BaseSheet, target)
        self.assertLess(panel.sheet.FlatShape.Volume, target.FlatShape.Volume)
        self.assertEqual(self.model.doc.UndoCount, before[1] + 1)
        # Prepared documents do not present an unnecessary preparation action.
        ready_panel = SheetPanel(panel.sheet, "cuts", history=True)
        self.addCleanup(ready_panel.close)
        self.assertTrue(ready_panel.prepare_button.isHidden())

    def test_panel_closure_cancels_only_its_uncommitted_preparation(self):
        target = self.reopen(self.sheet)
        panel = self.panel(target)
        started, release, build = self.delayed()
        with patch.object(Preparation, "_build", side_effect=build):
            panel.prepare_button.click()
            run = panel.preparation_run
            self.fixture.wait_for(started.is_set)
            self.assertFalse(panel.apply_button.isEnabled())
            self.assertFalse(panel.prepare_button.isEnabled())
            panel.close()
            self.assertTrue(run.future.cancelled())
            release.set()
            self.fixture.wait_for(lambda: run.finished)
        self.assertIsNone(target.Proxy._geometry)

    def test_panel_reports_source_change_and_restores_its_controls(self):
        target = self.reopen(self.sheet)
        panel = self.panel(target)
        started, release, build = self.delayed()
        with patch.object(Preparation, "_build", side_effect=build):
            panel.prepare_button.click()
            run = panel.preparation_run
            self.fixture.wait_for(started.is_set)
            self.model.doc.BaseBend.Thickness = 2
            release.set()
            self.fixture.wait_for(run.future.done)
        with self.assertRaises(RuntimeError):
            run.future.result()
        self.assertIsNone(target.Proxy._geometry)
        self.assertTrue(panel.prepare_button.isEnabled())
        self.assertTrue(panel.fields.isEnabled())
        self.assertTrue(panel.refresh_button.isEnabled())
        self.assertNotEqual(panel.message.text(), "Ready")

    def native_dispatcher(self):
        import json
        import FreeCADGui as Gui
        from VibeCADCore import get_service
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeProviderContext import provider_authorized_native_surface
        from VibeCADNativeDispatch import NativeTurnDispatcher
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        from VibeCADNativeRuntimeContext import NativeRuntimeContext
        from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
        from VibeCADNativeTurn import NativeTurnSnapshot
        from VibeCADNativeUndo import NativeAssistantUndoLedger
        from VibeCADRibbonSurface import read_active_ribbon_surface
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("SMWorkbench")
        self.fixture.wait_for(lambda: read_active_ribbon_surface().surface_id == "sheet_metal")
        registry = build_native_capability_registry()
        observed = read_active_ribbon_surface()
        frozen = NativeSurfaceSnapshot.from_surface(observed)
        provider = provider_authorized_native_surface(resolve_native_provider_surface(observed, registry))
        self.assertTrue(provider.available, provider.debug_summary())
        service = get_service()
        context = NativeRuntimeContext(service=service, document=self.model.doc,
            state=service.native_document_state_store(), undo_ledger=NativeAssistantUndoLedger(),
            reauthorize_turn=lambda: require_frozen_native_surface(frozen),
            active_document=lambda: App.ActiveDocument,
            active_surface_id=lambda: read_active_ribbon_surface().surface_id,
            edit_or_task_active=lambda: bool(Gui.Control.activeDialog()))
        context.state.begin_native_authority(self.model.doc.Uid)
        context.undo_ledger.begin_run("native-sheet-preparation-test")
        turn = NativeTurnSnapshot.from_provider_surface(provider)
        dispatcher = NativeTurnDispatcher(document=self.model.doc, state=context.state,
            registry=registry, turn=turn, runtimes=build_native_runtime_bindings(context, turn.tool_names),
            reauthorize_turn=context.guard, active_document=context.active_document)

        def call(name, arguments, call_id):
            return dispatcher.call_async(name, json.dumps(arguments), call_id,
                                         document_dispatch=lambda work: work())

        return call

    def native_wait(self, result):
        from concurrent.futures import Future
        if isinstance(result, Future):
            self.fixture.wait_for(result.done)
            return result.result()
        return result

    def test_native_preparation_is_a_read_and_next_cut_has_one_undo(self):
        from concurrent.futures import Future
        sheet = self.reopen(self.sheet)
        call = self.native_dispatcher()
        target = {"document_uid": self.model.doc.Uid, "object_name": sheet.Name}
        before = self.snapshot()
        pending = call("sheet_metal.inspect", {"operation": "prepare", "target": target}, "prepare-restored")
        self.assertIsInstance(pending, Future)
        prepared = self.native_wait(pending)
        self.assertTrue(prepared["ok"], prepared)
        self.assertTrue(prepared["prepared"])
        self.assertEqual(prepared["target"], target)
        self.assertNotIn("receipt", prepared)
        self.assertEqual(self.snapshot(), before)
        regions = self.native_wait(call("sheet_metal.inspect", {
            "operation": "list_regions", "target": target}, "inspect-prepared"))
        self.assertTrue(regions["ok"], regions)
        self.assertTrue(regions["items"])
        geometry = Editable.get_state_geometry(sheet)
        center = next(region.flat_face().CenterOfMass for region in geometry.mapping.regions
                      if region.kind == "plane")
        result = self.native_wait(call("sheet_metal.edit", {
            "operation": "add_circle", "object_name": sheet.Name,
            "center": list(center), "radius": 1}, "cut-restored"))
        self.assertTrue(result["ok"], result)
        self.assertTrue(result["assistant_undo_available"])
        self.assertEqual(self.model.doc.UndoCount, before[1] + 1)
        hole = self.model.doc.getObject(result["object_name"])
        self.assertLess(hole.FlatShape.Volume, sheet.FlatShape.Volume)

    def test_reopened_inspection_explains_missing_mapping_instead_of_reporting_valid(self):
        import SheetMetalOperations as Operations
        sheet = self.reopen(self.sheet)
        call = self.native_dispatcher()
        target = {"document_uid": self.model.doc.Uid, "object_name": sheet.Name}
        before = self.snapshot()
        for inspect in (Operations.inspect, Shared.inspect):
            with self.subTest(reader=inspect.__module__):
                result = inspect(sheet)
                self.assertFalse(result["prepared"])
                self.assertIn("prepare", result["error"].lower())
        result = self.native_wait(call("sheet_metal.inspect", {
            "operation": "read_sheet", "target": target}, "read-unprepared"))
        self.assertTrue(result["ok"], result)
        self.assertFalse(result["prepared"])
        self.assertIn("prepare", result["error"].lower())
        result = self.native_wait(call("sheet_metal.inspect", {
            "operation": "list_regions", "target": target}, "regions-unprepared"))
        self.assertFalse(result["ok"], result)
        self.assertIn("prepare", result["error"].lower())
        self.assertEqual(self.snapshot(), before)
        prepared = self.native_wait(call("sheet_metal.inspect", {
            "operation": "prepare", "target": target}, "prepare-inspected"))
        self.assertTrue(prepared["ok"], prepared)
        result = self.native_wait(call("sheet_metal.inspect", {
            "operation": "read_sheet", "target": target}, "read-ready"))
        self.assertTrue(result["prepared"])
        self.assertNotIn("error", result)
        self.assertEqual(self.snapshot(), before)

    def test_native_preparation_rejects_a_source_edit_while_pending(self):
        sheet = self.reopen(self.sheet)
        call = self.native_dispatcher()
        started, release, build = self.delayed()
        with patch.object(Preparation, "_build", side_effect=build):
            pending = call("sheet_metal.inspect", {"operation": "prepare", "target": {
                "document_uid": self.model.doc.Uid, "object_name": sheet.Name}}, "stale-preparation")
            self.fixture.wait_for(started.is_set)
            self.model.doc.BaseBend.Thickness = 2
            release.set()
            result = self.native_wait(pending)
        self.assertFalse(result["ok"], result)
        self.assertIsNone(sheet.Proxy._geometry)
