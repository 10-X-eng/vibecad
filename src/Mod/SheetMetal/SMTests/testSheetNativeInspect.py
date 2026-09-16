# SPDX-License-Identifier: LGPL-2.1-or-later
"""Real native assistant reads use the ribbon's exact sheet feature chain."""

import json
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui

import SheetMetalCutHistory as History
import SheetMetalHistoryOperations as Operations
from SMTests import testPresentation


class TestSheetNativeInspect(unittest.TestCase):
    def setUp(self):
        from VibeCADCore import get_service
        from VibeCADNativeRuntimeContext import NativeRuntimeContext
        from VibeCADNativeUndo import NativeAssistantUndoLedger
        self.fixture = testPresentation.TestPresentation()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.model = self.fixture.fixture
        self.sheet = self.model.sheet
        Operations.capture_revision(self.sheet)
        service = get_service()
        self.context = NativeRuntimeContext(
            service=service, document=self.model.doc, state=service.native_document_state_store(),
            undo_ledger=NativeAssistantUndoLedger(), reauthorize_turn=lambda: None,
            active_document=lambda: App.ActiveDocument, active_surface_id=lambda: "model",
            edit_or_task_active=lambda: False)

    def call(self, operation, **values):
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADNativeSheetMetalInspectRuntime import NativeSheetMetalInspectRuntime
        runtime = NativeSheetMetalInspectRuntime(self.context)
        handler = build_native_capability_registry().implementation("sheet_metal.inspect").handler
        return handler(SimpleNamespace(runtime=runtime, arguments={"operation": operation, **values}))

    def target(self, obj=None):
        obj = self.sheet if obj is None else obj
        return {"document_uid": obj.Document.Uid, "object_name": obj.Name}

    def test_sheet_inspection_exposes_where_to_create_relief_and_bend_sketches(self):
        from dataclasses import replace
        from VibeCADProvider import _provider_visible_tool_result
        before = Operations.capture_revision(self.sheet), self.model.doc.UndoCount
        self.context = replace(self.context, active_surface_id=lambda: "sheet_metal")
        result = _provider_visible_tool_result(
            {**self.call("read_sheet", target=self.target()), "ok": True, "_vibecad_native_result": True},
            tool_name="sheet_metal.inspect")
        guidance = result["sketch_creation"]
        self.assertEqual(guidance["tool"], "workspace.switch")
        self.assertEqual(guidance["arguments"], {"workspace": "sketching"})
        self.assertIn("next turn", guidance["message"])
        self.assertIn("sheet_metal", guidance["message"])
        self.assertEqual((Operations.capture_revision(self.sheet), self.model.doc.UndoCount), before)

    def test_modeling_inspection_explains_where_sheet_edit_tools_become_available(self):
        before = Operations.capture_revision(self.sheet), self.model.doc.UndoCount
        for operation in ("list_sheets", "read_sheet", "list_regions"):
            values = {} if operation == "list_sheets" else {"target": self.target()}
            result = self.call(operation, **values)
            self.assertIn("workspace.switch", result["edit_guidance"])
            self.assertIn("sheet_metal", result["edit_guidance"])
            self.assertIn("next turn", result["edit_guidance"])
        from dataclasses import replace
        self.context = replace(self.context, active_surface_id=lambda: "sheet_metal")
        self.assertNotIn("edit_guidance", self.call("read_sheet", target=self.target()))
        self.assertEqual((Operations.capture_revision(self.sheet), self.model.doc.UndoCount), before)

    def test_repair_route_preserves_exact_source_and_paged_history(self):
        before = Operations.capture_revision(self.sheet), self.model.doc.UndoCount
        result = self.call("read_sheet", target=self.target())
        route = result["repair_workflow"]
        self.assertEqual(route["source"], self.target(self.sheet.SourceFace[0]))
        self.assertEqual(route["history"], {"tool": "sheet_metal.inspect", "arguments": {
            "operation": "list_history", "target": self.target()}})
        self.assertIn("profile", route["message"])
        self.assertIn("sketch.open", route["message"])
        self.assertEqual((Operations.capture_revision(self.sheet), self.model.doc.UndoCount), before)

    def test_sheet_ribbon_prompt_context_keeps_native_tree_and_history_identity(self):
        from VibeCADNativeSnapshot import build_active_snapshot
        hole = self.model.edit(lambda: History.create_circle_step(self.sheet, self.model.bend_pick()[2], 4))
        document = self.model.doc
        revision = self.context.state.current_revision(document.Uid)
        before = (document.UndoCount, document.isTouched(), revision,
                  [(obj.Name, obj.Visibility) for obj in document.Objects if hasattr(obj, "Visibility")])
        with patch("SheetMetalPresentation.prepare_pair", side_effect=AssertionError("remesh")), \
                patch.object(Operations, "inspect", side_effect=AssertionError("full geometry inspection")):
            snapshot = build_active_snapshot(document, "sheet_metal", {
                "document_uid": document.Uid, "structural_revision": revision}, selection={
                "document_uid": document.Uid, "items": [{"object": self.target(hole)}],
                "selected_count": 1})
        domain = snapshot["domain"]
        selected = domain["objects"][0]
        self.assertEqual(selected["object_name"], hole.Name)
        self.assertEqual(selected["category"], "shared_sheet")
        self.assertEqual(selected["editor_command"], "SheetMetal_EditHistoryCut")
        self.assertEqual(selected["predecessor"]["object_name"], self.sheet.Name)
        timeline, = document.findObjects("App::DocumentTimeline")
        self.assertEqual(domain["history"]["position"], timeline.Position)
        self.assertEqual(domain["history"]["operation_count"], len(timeline.Operations))
        self.assertEqual(before, (document.UndoCount, document.isTouched(),
            self.context.state.current_revision(document.Uid),
            [(obj.Name, obj.Visibility) for obj in document.Objects if hasattr(obj, "Visibility")]))

    def test_real_registry_reads_the_same_definition_without_document_changes(self):
        hole = self.model.edit(lambda: History.create_circle_step(self.sheet, self.model.bend_pick()[2], 4))
        before = (self.model.doc.UndoCount, self.model.doc.isTouched(),
                  self.context.state.current_revision(self.model.doc.Uid),
                  [(obj.Name, obj.Visibility) for obj in self.model.doc.Objects if hasattr(obj, "Visibility")])
        with patch.object(History.CircleCutFeature, "execute", side_effect=AssertionError("recompute")), \
                patch("SheetMetalPresentation.prepare_pair", side_effect=AssertionError("remesh")):
            listed = self.call("list_sheets")
            self.assertEqual({entry["target"]["object_name"] for entry in listed["items"]},
                             {self.sheet.Name, hole.Name})
            by_name = {entry["target"]["object_name"]: entry for entry in listed["items"]}
            self.assertFalse(by_name[self.sheet.Name]["visibility"])
            self.assertTrue(by_name[hole.Name]["visibility"])
            overview = self.call("read_sheet", target=self.target(hole))
            history = self.call("list_history", target=self.target(hole))
            cuts = self.call("list_cuts", target=self.target(hole))
            regions = self.call("list_regions", target=self.target(hole), page_size=1)
            json.dumps([listed, overview, history, cuts, regions], allow_nan=False)
        expected = Operations.inspect(hole)
        self.assertEqual(overview["revision"], expected["revision"])
        self.assertEqual(overview["representation"], expected["representation"])
        self.assertTrue(overview["prepared"])
        self.assertNotIn("definition", overview)
        self.assertNotIn("cuts", overview)
        self.assertEqual(history["items"], expected["history"]["states"])
        self.assertEqual(cuts["items"], expected["cuts"])
        self.assertEqual(regions["items"], expected["regions"][:1])
        self.assertEqual(regions["total"], len(expected["regions"]))
        self.assertEqual(regions["next_offset"], 1)
        self.assertEqual(before, (self.model.doc.UndoCount, self.model.doc.isTouched(),
                         self.context.state.current_revision(self.model.doc.Uid),
                         [(obj.Name, obj.Visibility) for obj in self.model.doc.Objects if hasattr(obj, "Visibility")]))

    def test_pages_are_bound_to_the_document_revision(self):
        from VibeCADNativeState import NativeRevisionConflict
        first = self.call("list_regions", target=self.target(), page_size=1)
        revision = first["structural_revision"]
        second = self.call("list_regions", target=self.target(), page_size=1, offset=1,
                           expected_revision=revision)
        self.assertNotEqual(first["items"], second["items"])
        with self.assertRaisesRegex(RuntimeError, "revision"):
            self.call("list_regions", target=self.target(), offset=1)
        self.model.edit(lambda: setattr(self.sheet, "Label", "Changed after first page"))
        with self.assertRaises(NativeRevisionConflict):
            self.call("list_regions", target=self.target(), offset=1, expected_revision=revision)
        for values in ({"offset": -1}, {"offset": True}, {"page_size": 33},
                       {"page_size": 0}, {"expected_revision": True}):
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                self.call("list_sheets", **values)

    def test_provider_returned_target_can_be_used_by_the_next_native_inspection(self):
        from VibeCADProvider import _provider_visible_tool_result
        listing = self.call("list_sheets")
        visible = _provider_visible_tool_result({**listing, "ok": True,
            "_vibecad_native_result": True}, tool_name="sheet_metal.inspect")
        target = next(item["target"] for item in visible["items"]
                      if item["target"]["object_name"] == self.sheet.Name)
        self.assertEqual(target, self.target())
        self.assertTrue(self.call("read_sheet", target=target)["prepared"])

    def test_wrong_document_non_sheet_and_malformed_targets_are_rejected(self):
        from VibeCADNativeTargets import NativeTargetError
        from VibeCADNativeArguments import NativeArgumentError
        with self.assertRaises(NativeTargetError):
            self.call("read_sheet", target={**self.target(), "document_uid": "different-document"})
        with self.assertRaises(NativeTargetError):
            self.call("read_sheet", target=self.target(self.model.doc.BaseBend))
        with self.assertRaises(NativeArgumentError):
            self.call("read_sheet", target={**self.target(), "label": self.sheet.Label})
        other = App.newDocument("SheetInspectOther")
        self.addCleanup(lambda: App.closeDocument(other.Name))
        with self.assertRaisesRegex(RuntimeError, "active"):
            self.call("list_sheets")

    def test_suppressed_state_remains_discoverable_with_its_display_predecessor(self):
        hole = self.model.edit(lambda: History.create_circle_step(self.sheet, self.model.bend_pick()[2], 4))
        self.model.edit(lambda: setattr(hole, "Suppressed", True))
        listing = self.call("list_sheets")
        entry = next(entry for entry in listing["items"] if entry["target"] == self.target(hole))
        self.assertTrue(entry["suppressed"])
        self.assertFalse(entry["active"])
        result = self.call("read_sheet", target=self.target(hole))
        self.assertEqual(result["history"]["display_state"], self.target())

    def test_invalid_geometry_reports_repair_state_and_does_not_publish_regions(self):
        from VibeCADNativeSheetMetalInspectRuntime import NativeSheetMetalInspectError
        hole = self.model.edit(lambda: History.create_circle_step(self.sheet, self.model.bend_pick()[2], 4))
        self.model.edit(lambda: setattr(hole, "Radius", 10000))
        result = self.call("read_sheet", target=self.target(hole))
        self.assertFalse(result["prepared"])
        self.assertTrue(result["error"])
        history = self.call("list_history", target=self.target(hole))
        self.assertEqual(history["items"][-1]["operation_id"], hole.OperationId)
        with self.assertRaises(NativeSheetMetalInspectError) as caught:
            self.call("list_regions", target=self.target(hole))
        self.assertIn("read_sheet", caught.exception.failure()["repair"])

    def test_model_surface_discovers_the_native_tool(self):
        from VibeCADNativeCapabilityRegistry import resolve_native_provider_surface
        from VibeCADNativeRegistry import build_native_capability_registry
        from VibeCADRibbonSurface import read_active_ribbon_surface
        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("PartDesignWorkbench")
        self.fixture.wait_for(lambda: read_active_ribbon_surface().surface_id == "model")
        surface = resolve_native_provider_surface(read_active_ribbon_surface(), build_native_capability_registry())
        self.assertTrue(surface.available, surface.summary())
        self.assertIn("sheet_metal.inspect", surface.tool_names)
        from VibeCADNativeRuntimeRegistry import build_native_runtime_bindings
        from VibeCADNativeSheetMetalInspectRuntime import NativeSheetMetalInspectRuntime
        bindings = build_native_runtime_bindings(self.context, ("sheet_metal.inspect",))
        self.assertIsInstance(bindings["sheet_metal.inspect"], NativeSheetMetalInspectRuntime)
        result = bindings["sheet_metal.inspect"].inspect({"operation": "read_sheet", "target": self.target()})
        self.assertTrue(result["prepared"])
