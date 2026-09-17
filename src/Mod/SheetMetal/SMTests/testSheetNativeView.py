# SPDX-License-Identifier: LGPL-2.1-or-later
"""Native assistant presentation switches preserve real geometry and History."""

import unittest
import tempfile
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui

import SheetMetalCutHistory as History
from SMTests import testSheetNativeInspect


class TestSheetNativeView(unittest.TestCase):
    def setUp(self):
        self.fixture = testSheetNativeInspect.TestSheetNativeInspect()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model, self.sheet = self.fixture.model, self.fixture.sheet
        self.context = self.fixture.context

    def call(self, representation, obj=None):
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        runtime = build_native_runtime_bindings(self.context, ("sheet_metal.view",))["sheet_metal.view"]
        handler = build_native_capability_registry().implementation("sheet_metal.view").handler
        return handler(SimpleNamespace(runtime=runtime, arguments={
            "operation": "set_representation", "object_name": self.fixture.target(obj)["object_name"],
            "representation": representation}))

    def snapshot(self):
        doc = self.model.doc
        histories = tuple((timeline.Name, timeline.Position,
                           tuple(obj.Name for obj in timeline.Operations))
                          for timeline in doc.findObjects("App::DocumentTimeline"))
        return (doc.UndoCount, tuple(doc.UndoNames), doc.isTouched(),
                self.context.state.current_revision(doc.Uid), histories,
                tuple((obj.Name, obj.Visibility, tuple(obj.State)) for obj in doc.Objects
                      if hasattr(obj, "Visibility")), self.sheet.PreparedInputHash)

    def test_round_trip_uses_cached_nodes_without_document_or_history_changes(self):
        view = self.sheet.ViewObject.Proxy
        before = self.snapshot()
        nodes = view.cached_nodes
        with patch("SheetMetalPresentation.prepare_pair", side_effect=AssertionError("remesh")), \
                patch("SheetMetalEditable.SheetGeometry.prepare", side_effect=AssertionError("recompute")), \
                patch("SheetMetalEditable._persistent_brep", side_effect=AssertionError("serialize")), \
                patch("SheetMetalHistoryOperations.inspect", side_effect=AssertionError("full inspection")):
            for representation, index in (("flat", 1), ("folded", 0), ("flat", 1), ("flat", 1)):
                result = self.call(representation)
                self.assertEqual(result["representation"], representation)
                self.assertEqual(result["target"], self.fixture.target())
                self.assertEqual(result["display_state"], self.fixture.target())
                self.assertEqual(result["input_hash"], self.sheet.PreparedInputHash)
                self.assertEqual(result["visibility"], self.sheet.Visibility)
                self.assertEqual(view._switch.whichChild.getValue(), index)
                self.assertEqual(view.cached_nodes, nodes)
                self.assertEqual(self.snapshot(), before)

    def test_suppressed_cut_switches_its_actual_display_predecessor(self):
        hole = self.model.edit(lambda: History.create_circle_step(self.sheet, self.model.bend_pick()[2], 4))
        self.model.edit(lambda: setattr(hole, "Suppressed", True))
        self.fixture.fixture.wait_for(lambda: self.sheet.ViewObject.Proxy.ready)
        before = self.snapshot()
        result = self.call("flat", hole)
        self.assertEqual(result["target"], self.fixture.target(hole))
        self.assertEqual(result["display_state"], self.fixture.target())
        self.assertEqual(self.sheet.ViewObject.Proxy.mode, "flat")
        self.assertEqual(self.snapshot(), before)

    def check_reopened_view(self, target):
        import SheetMetalEditable as Editable
        from VibeCADNativeSheetMetalViewRuntime import NativeSheetMetalViewError
        target_name, sheet_name = target.Name, self.sheet.Name
        with tempfile.TemporaryDirectory() as directory:
            self.model.doc.saveAs(str(Path(directory) / "sheet.FCStd"))
            self.model.settle()
            App.closeDocument(self.model.doc.Name)
            self.model.doc = App.openDocument(str(Path(directory) / "sheet.FCStd"))
            self.model.settle()
            self.sheet = self.model.sheet = self.fixture.sheet = self.model.doc.getObject(sheet_name)
            target = self.model.doc.getObject(target_name)
            view = target.ViewObject.Proxy
            self.fixture.fixture.view = view
            self.fixture.fixture.wait_for(lambda: view.ready)
            self.context = self.fixture.context = replace(self.context, document=self.model.doc)
            self.assertIsNone(target.Proxy._geometry)
            with self.assertRaises(RuntimeError):
                view.current()
            with self.assertRaises(RuntimeError):
                Editable.get_state_geometry(target)
            before, nodes = self.snapshot(), view.cached_nodes
            with patch("SheetMetalPresentation.prepare_pair", side_effect=AssertionError("remesh")), \
                    patch("SheetMetalEditable.SheetGeometry.prepare", side_effect=AssertionError("recompute")), \
                    patch("SheetMetalEditable._persistent_brep", side_effect=AssertionError("serialize")):
                for mode in ("flat", "folded", "flat"):
                    result = self.call(mode, target)
                    self.assertEqual(result["representation"], mode)
                    self.assertEqual(result["input_hash"], target.PreparedInputHash)
                    self.assertEqual(view.cached_nodes, nodes)
                    self.assertEqual(self.snapshot(), before)
                # A saved display pair must not grant permission to use an
                # absent edit mapping or publish stale geometry after an edit.
                with self.assertRaises(RuntimeError):
                    view.current()
                self.model.doc.BaseBend.Thickness = 2
                with self.assertRaises(NativeSheetMetalViewError):
                    self.call("folded", target)
                self.assertEqual(view.mode, "flat")

    def test_reopened_sheet_switches_saved_pair_without_preparing_edit_mapping(self):
        self.check_reopened_view(self.sheet)

    def test_reopened_cut_switches_saved_pair_without_preparing_edit_mapping(self):
        hole = self.model.edit(lambda: History.create_circle_step(self.sheet, self.model.bend_pick()[2], 4))
        self.check_reopened_view(hole)

    def test_source_view_repair_preserves_geometry_and_existing_shared_state(self):
        from VibeCADNativeTargets import NativeTargetError
        source = self.model.doc.BaseBend
        before = self.snapshot()
        with patch("SheetMetalPresentation.prepare_pair", side_effect=AssertionError("remesh")), \
                patch("SheetMetalEditable.SheetGeometry.prepare", side_effect=AssertionError("recompute")), \
                patch("SheetMetalHistoryOperations.inspect", side_effect=AssertionError("full inspection")):
            with self.assertRaises(NativeTargetError) as caught:
                self.call("flat", source)
        failure = caught.exception.failure()
        self.assertEqual(failure["exact_target"], self.fixture.target(source))
        self.assertIn("list_sheets", failure["repair"])
        self.assertIn("from_source", failure["repair"])
        self.assertEqual(self.snapshot(), before)
        # Following the first repair step discovers the already existing sheet;
        # a rejected display request must not create another one automatically.
        listed = self.fixture.call("list_sheets")
        self.assertEqual([row["target"] for row in listed["items"]], [self.fixture.target()])
        self.assertEqual(self.call("flat")["representation"], "flat")
        self.assertEqual(self.snapshot(), before)

    def test_invalid_geometry_and_inactive_document_do_not_switch(self):
        from VibeCADNativeSheetMetalViewRuntime import NativeSheetMetalViewError
        hole = self.model.edit(lambda: History.create_circle_step(self.sheet, self.model.bend_pick()[2], 4))
        self.model.edit(lambda: setattr(hole, "Radius", 10000))
        old_mode = hole.ViewObject.Proxy.mode
        with self.assertRaises(NativeSheetMetalViewError):
            self.call("flat", hole)
        self.assertEqual(hole.ViewObject.Proxy.mode, old_mode)
        other = App.newDocument("SheetViewOther")
        self.addCleanup(lambda: App.closeDocument(other.Name))
        with self.assertRaisesRegex(RuntimeError, "active"):
            self.call("flat")
        self.assertEqual(self.sheet.ViewObject.Proxy.mode, "folded")

    def test_live_model_surface_discovers_the_presentation_control(self):
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADRibbonSurface import read_active_ribbon_surface
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("PartDesignWorkbench")
        self.fixture.fixture.wait_for(lambda: read_active_ribbon_surface().surface_id == "model")
        surface = resolve_native_provider_surface(read_active_ribbon_surface(), build_native_capability_registry())
        self.assertTrue(surface.available, surface.summary())
        self.assertIn("sheet_metal.view", surface.tool_names)
        self.assertEqual(self.call("flat")["representation"], "flat")

    def test_native_sheet_workspace_can_reach_assembly_tools_and_return(self):
        from dataclasses import replace
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeWorkspaceRuntime import NativeWorkspaceRuntime
        from VibeCADNativeSurface import NativeSurfaceSnapshot, require_frozen_native_surface
        from VibeCADRibbonSurface import read_active_ribbon_surface
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("SMWorkbench")
        self.fixture.fixture.wait_for(lambda: read_active_ribbon_surface().surface_id == "sheet_metal")
        registry = build_native_capability_registry()
        before = self.snapshot()
        for workspace, surface, tools in (
                ("assembly", "assemble", {"assembly.create", "assembly.insert", "assembly.joint", "assembly.motion_study"}),
                ("sheet_metal", "sheet_metal", {"sheet_metal.create", "sheet_metal.edit", "sheet_metal.view"})):
            observed = read_active_ribbon_surface()
            from VibeCADNativeProviderContext import provider_authorized_native_surface
            provider = provider_authorized_native_surface(
                resolve_native_provider_surface(observed, registry))
            self.assertTrue(provider.available, provider.summary())
            self.assertIn("workspace.switch", provider.tool_names)
            frozen = NativeSurfaceSnapshot.from_surface(observed)
            context = replace(self.context,
                reauthorize_turn=lambda: require_frozen_native_surface(frozen),
                active_surface_id=lambda: read_active_ribbon_surface().surface_id,
                document_thread_dispatch=lambda operation: operation())
            result = NativeWorkspaceRuntime(context).switch({"operation": "switch", "workspace": workspace})
            self.assertTrue(result["next_turn_required"])
            self.assertEqual(read_active_ribbon_surface().surface_id, surface)
            with self.assertRaises(RuntimeError):
                require_frozen_native_surface(frozen)
            next_provider = resolve_native_provider_surface(read_active_ribbon_surface(), registry)
            self.assertTrue(next_provider.available, next_provider.summary())
            self.assertTrue(tools.issubset(next_provider.tool_names), next_provider.tool_names)
            self.assertEqual(self.snapshot(), before)

    def test_each_ribbon_workspace_retains_an_agent_route_to_other_tools(self):
        from dataclasses import replace
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeProviderContext import (
            provider_authorized_native_surface, schemas_for_native_provider_surface,
            provider_visible_native_state)
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeWorkspaceRuntime import NativeWorkspaceRuntime, WORKBENCH_BY_NATIVE_WORKSPACE
        from VibeCADNativeWorkspaceSchema import NATIVE_SURFACE_BY_WORKSPACE
        from VibeCADRibbonSurface import read_active_ribbon_surface
        service = self.context.service
        previous_engine = service.modeling_engine()
        self.addCleanup(lambda: service.select_modeling_engine(previous_engine))
        service.select_modeling_engine("native")
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        registry = build_native_capability_registry()
        context = replace(self.context,
            active_surface_id=lambda: read_active_ribbon_surface().surface_id,
            document_thread_dispatch=lambda operation: operation())
        runtime = NativeWorkspaceRuntime(context)
        for workspace, surface in NATIVE_SURFACE_BY_WORKSPACE.items():
            with self.subTest(workspace=workspace):
                self.assertIn(WORKBENCH_BY_NATIVE_WORKSPACE[workspace], Gui.listWorkbenches())
                if read_active_ribbon_surface().surface_id != surface:
                    result = runtime.switch({"operation": "switch", "workspace": workspace})
                    self.assertTrue(result["next_turn_required"])
                provider = provider_authorized_native_surface(
                    resolve_native_provider_surface(read_active_ribbon_surface(), registry))
                self.assertTrue(provider.available, provider.debug_summary())
                self.assertIn("workspace.switch", provider.tool_names)
                switch = next(schema for schema in schemas_for_native_provider_surface(provider)
                              if schema["name"] == "workspace.switch")
                self.assertIn("tools", switch["description"])
                if surface in {"model", "assemble", "sheet_metal"}:
                    state = provider_visible_native_state(service.native_active_snapshot())
                    self.assertEqual(state["workspace_navigation"]["tool"], switch["name"])
                    self.assertIn("next turn", state["workspace_navigation"]["message"])

    def test_deactivated_assembly_is_not_restored_after_a_later_sketch_edit(self):
        self.deactivated_assembly_sketch_cycle(remove=False)

    def test_dfm_repair_follows_existing_sketch_across_workspaces_and_reanalyzes(self):
        from concurrent.futures import Future
        from unittest.mock import Mock
        from SMTests.testProfileCuts import TestProfileCuts
        from VibeCADNativeModelStructureRuntime import NativeModelStructureRuntime
        from VibeCADNativeSketchControlRuntime import NativeSketchControlRuntime
        from VibeCADNativeSheetMetalManufacturingRuntime import NativeSheetMetalManufacturingRuntime
        from VibeCADNativeWorkspaceRuntime import NativeWorkspaceRuntime
        from VibeCADRibbonSurface import read_active_ribbon_surface
        from VibeCADEditState import active_edit_state
        import SheetMetalHistoryOperations as Shared
        import SheetMetalRMFGManufacturingGui as Manufacturing

        profiles = TestProfileCuts()
        profiles.fixture = self.model
        sketch, radius_index = profiles.slot()
        cut = self.model.edit(lambda: History.create_profile_step(self.sheet, sketch))
        self.fixture.fixture.wait_for(lambda: cut.ViewObject.Proxy.ready)
        original_hash = cut.PreparedInputHash
        original_volume = cut.FlatShape.Volume
        object_names = {obj.Name for obj in self.model.doc.Objects}
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        self.addCleanup(Gui.activeDocument().resetEdit)
        self.context.state.begin_native_authority(self.model.doc.Uid)
        context = replace(self.context,
            active_surface_id=lambda: read_active_ribbon_surface().surface_id,
            edit_or_task_active=lambda: active_edit_state().active or bool(Gui.Control.activeDialog()),
            document_thread_dispatch=lambda action: action())
        workspace = NativeWorkspaceRuntime(context)
        if read_active_ribbon_surface().surface_id != "sheet_metal":
            workspace.switch({"operation": "switch", "workspace": "sheet_metal"})
        manufacturing = NativeSheetMetalManufacturingRuntime(context)
        status = {"quote": {"status": "blocked", "findings": [{"message": "Clearance failure"}]},
                  "can_checkout": False}
        analyzed = []
        def analyze():
            analyzed.append(cut.PreparedInputHash)
            status.update(quote=None, stale=False)
            pending = Future()
            pending.set_result({})
            return pending
        def quote():
            self.assertEqual(analyzed[-1], cut.PreparedInputHash)
            status.update(quote={"status": "ready", "amount_total_cents": 1234})
            pending = Future()
            pending.set_result({})
            return pending
        controller = SimpleNamespace(status=lambda: dict(status), analyze=Mock(side_effect=analyze),
            request_quote=Mock(side_effect=quote), refresh=Mock(), checkout=Mock())
        def run(operation):
            return manufacturing.execute({"operation": operation, "object_name": cut.Name},
                                         asynchronous=True)
        with patch.object(Manufacturing, "manufacturing_controller", return_value=controller):
            failure = run("status")
            self.assertIn("sketch.open", failure["repair_workflow"]["message"])
            overview = self.fixture.call("read_sheet", target=self.fixture.target(cut))
            route = overview["repair_workflow"]
            history = self.fixture.call(**route["history"]["arguments"])
            entry = next(item for item in history["items"] if item["target"]["object_name"] == cut.Name)
            self.assertEqual(entry["profile"], self.fixture.target(sketch))
            for target in ("parameters", "modeling", "sketching"):
                workspace.switch({"operation": "switch", "workspace": target})
            opened = NativeModelStructureRuntime(context).open_sketch({
                "operation": "open", "sketch": {"object_name": entry["profile"]["object_name"]}},
                ticket=context.state.begin_call(context.document_uid, "sketch.open"))
            self.assertTrue(opened["next_turn_required"])
            self.assertEqual(read_active_ribbon_surface().surface_id, "sketch.edit")
            sketch.setDatum(radius_index, App.Units.Quantity("3 mm"))
            finished = NativeSketchControlRuntime(context).control({"operation": "leave",
                "sketch": {"object_name": sketch.Name}, "expected_geometry_count": sketch.GeometryCount,
                "expected_constraint_count": sketch.ConstraintCount},
                ticket=context.state.begin_call(context.document_uid, "sketch.control"))
            self.assertTrue(finished["next_turn_required"])
            self.model.recompute()
            workspace.switch({"operation": "switch", "workspace": "sheet_metal"})
            self.assertNotEqual(cut.PreparedInputHash, original_hash)
            self.assertGreater(cut.FlatShape.Volume, original_volume)
            self.assertIs(History.get_profile(cut), sketch)
            self.assertNotIn("Invalid", cut.State)
            run("analyze").result()
            self.assertEqual(run("quote").result()["quote"]["status"], "ready")
            # A bend-only repair uses the existing sheet operation path, not a new sketch.
            prepared = Shared.prepare(cut, {"operation": "set_parameters", "changes": {"bend_radius": 3}},
                                      expected_revision=Shared.capture_revision(cut))
            pending = Shared.start(prepared)
            self.fixture.fixture.wait_for(pending.future.done)
            pending.future.result()
            self.assertNotEqual(cut.PreparedInputHash, analyzed[-1])
            run("analyze").result()
            run("quote").result()
            self.assertEqual(controller.analyze.call_count, 2)
            self.assertEqual(controller.request_quote.call_count, 2)
            controller.checkout.assert_not_called()
        self.assertEqual({obj.Name for obj in self.model.doc.Objects}, object_names)

    def test_deleted_deactivated_assembly_is_not_an_edit_restore_target(self):
        self.deactivated_assembly_sketch_cycle(remove=True)

    def deactivated_assembly_sketch_cycle(self, *, remove):
        import SketcherGui
        from PySide import QtWidgets
        from VibeCADSurfaceAuthority import deactivate_assembly
        from VibeCADEditState import active_edit_state

        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("AssemblyWorkbench")
        document = self.model.doc
        assembly = document.addObject("Assembly::AssemblyObject", "Assembly")
        sketch = document.addObject("Sketcher::SketchObject", "IndependentSketch")
        self.model.recompute()
        gui = Gui.getDocument(document.Name)
        self.addCleanup(gui.resetEdit)
        self.assertTrue(gui.setEdit(assembly.Name))
        self.assertTrue(deactivate_assembly(gui))
        self.assertFalse(active_edit_state().active)
        if remove:
            self.model.edit(lambda: document.removeObject(assembly.Name))
        report = Gui.getMainWindow().findChild(QtWidgets.QTextEdit, "Report view")
        self.assertIsNotNone(report)
        report.clear()
        for _ in range(3):
            self.assertTrue(gui.setEdit(sketch.Name))
            result = SketcherGui.leaveActiveSketch(document.Name, str(document.Uid), sketch.Name)
            self.model.settle()
            self.assertEqual(result["edit_mode"], "closed")
            self.assertFalse(active_edit_state().active)
            self.assertIsNone(gui.activeView().getActiveObject("assembly"))
            self.assertNotIn("cannot edit detached object", report.toPlainText())

    def test_workspace_switch_deactivates_assembly_and_continues_in_both_directions(self):
        from dataclasses import replace
        import VibeCADGui as VibeGui
        from VibeCADEditState import active_edit_state
        from VibeCADNativeSessionFactory import _edit_or_task_active
        from VibeCADNativeWorkspaceRuntime import NativeWorkspaceRuntime
        from VibeCADRibbonSurface import read_active_ribbon_surface

        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("AssemblyWorkbench")
        document = self.model.doc
        assembly = document.addObject("Assembly::AssemblyObject", "Assembly")
        self.model.recompute()
        self.addCleanup(lambda: Gui.getDocument(document.Name).resetEdit())
        Gui.getDocument(document.Name).setEdit(assembly.Name)
        self.assertIs(active_edit_state().document_object, assembly)
        self.assertFalse(_edit_or_task_active(self.context.service))
        context = replace(self.context,
            active_surface_id=lambda: read_active_ribbon_surface().surface_id,
            edit_or_task_active=lambda: _edit_or_task_active(self.context.service),
            document_thread_dispatch=lambda operation: operation())
        runtime = NativeWorkspaceRuntime(context)
        dock = object()
        with patch.object(VibeGui, "_internal_agent_allowed", return_value=True), \
                patch.object(VibeGui, "_is_assistant_run_active", return_value=False), \
                patch.object(VibeGui, "_is_intent_memory_rebuild_active", return_value=False), \
                patch.object(VibeGui, "_find_dock", return_value=dock), \
                patch.object(VibeGui, "_assistant_panel_is_built", return_value=True), \
                patch.object(self.context.service, "assistant_document_state", return_value={"enabled": True}), \
                patch.object(VibeGui, "_execute_assistant_run") as execute, \
                patch.object(VibeGui, "_warn") as warn:
            before = self.snapshot()
            for workspace in ("sheet_metal", "assembly"):
                with self.subTest(workspace=workspace):
                    # Also reproduce a user leaving the Assembly active while
                    # manually changing ribbon before the agent switches back.
                    Gui.getDocument(document.Name).setEdit(assembly.Name)
                    self.assertIs(active_edit_state().document_object, assembly)
                    result = runtime.switch({"operation": "switch", "workspace": workspace})
                    self.assertFalse(active_edit_state().active)
                    self.assertIsNone(Gui.getDocument(document.Name).activeView().getActiveObject("assembly"))
                    self.assertEqual(self.snapshot(), before)
                    trace = [{"tool_name": "sheet_metal.inspect", "result": {
                        "ok": True, "object_name": self.sheet.Name}}, {
                        "tool_name": "workspace.switch", "result": {"ok": True, **result}}]
                    response = SimpleNamespace(error=None, tool_trace=trace)
                    event = VibeGui._native_surface_continuation_event(response)
                    self.assertIsNotNone(event)
                    self.assertEqual(event["tool_trace"], trace)
                    execute.reset_mock()
                    VibeGui._start_native_surface_continuation(event)
                    execute.assert_called_once_with(dock, self.context.service, continuation_event=event)
                    warn.assert_not_called()
