# SPDX-License-Identifier: LGPL-2.1-or-later
"""Failed geometry keeps its error while the assistant resumes from fresh state."""

import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import FreeCAD as App
import FreeCADGui as Gui


class TestSheetNativeRecovery(unittest.TestCase):
    def test_profile_sketch_dispatch_uses_the_compact_provider_schema(self):
        import VibeCADGui as VibeGui
        import VibeCADSession as Session
        from VibeCADCore import get_service
        from VibeCADNativeSessionFactory import create_native_session_execution
        from SMTests.live_sheet_prompt import _events

        previous = Gui.activeWorkbench().name()
        self.addCleanup(lambda: Gui.activateWorkbench(previous))
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("SheetProfileDispatch")
        def close_document():
            while not document.isClosable():
                _events()
            App.closeDocument(document.Name)
        self.addCleanup(close_document)
        document.UndoMode = 1
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        sketch = document.addObject("Sketcher::SketchObject", "ReliefProfile")
        document.recompute()
        document.saveAs(str(Path(os.environ["VIBECAD_TEST_OUTPUT"]) / "profile-dispatch.FCStd"))
        self.assertTrue(Gui.activeDocument().setEdit(sketch.Name))
        self.addCleanup(lambda: Gui.getDocument(document.Name).resetEdit())
        for _ in range(24):
            _events()
        service = get_service()
        service.select_modeling_engine("native")
        context = Session._context_for_provider(service)
        execution = create_native_session_execution(
            service=service, expected_surface=context["provider_tool_surface"],
            expected_schemas=context["provider_tool_schemas"],
            expected_authorization=context["_native_turn_authorization"],
            document_thread_dispatch=VibeGui._dispatch_to_document_thread)
        self.addCleanup(execution.close)
        results = []
        def call(name, arguments):
            result = execution.dispatcher.call(name, json.dumps(arguments), f"profile-{len(results)}")
            results.append(result)
            self.assertTrue(result.get("ok"), result)
            return result
        state = call("sketch.inspect", {"operation": "read_state"})
        line = call("sketch.draw_line", {"operation": "create_line", "revision": state["revision"],
            "start_mm": {"x": 0, "y": 0}, "end_mm": {"x": 10, "y": 0}})
        call("sketch.draw_line", {"operation": "create_polyline", "revision": line["revision"],
            "vertices_mm": [{"x": 20, "y": 0}, {"x": 30, "y": 0}, {"x": 30, "y": 10}],
            "closed": False})
        self.assertEqual(sketch.GeometryCount, 3)
        self.assertTrue(all(abs(geometry.length()-10) < 1e-7 for geometry in sketch.Geometry))

    def test_failed_geometry_can_be_inspected_in_a_fresh_assistant_turn(self):
        import VibeCADGui as VibeGui
        import VibeCADSession as Session
        from VibeCADCore import get_service
        from VibeCADMCP import get_control_mode_controller
        from VibeCADProvider import BaseProvider, ProviderResult
        from SMTests.live_sheet_flange_prompt import LiveSheetFlangePrompt
        from SMTests.live_sheet_prompt import _events

        fixture = LiveSheetFlangePrompt()
        self.addCleanup(fixture.doCleanups)
        document = fixture.create_input()
        output = Path(os.environ["VIBECAD_TEST_OUTPUT"])
        document.saveAs(str(output / "before-recovery.FCStd"))
        get_control_mode_controller().request_mcp_enabled(False)
        VibeGui.ensure_commands_registered()
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        VibeGui._connect_workbench_activation()
        service = get_service()
        service.select_modeling_engine("native")
        Gui.activateWorkbench("SMWorkbench")
        VibeGui.show_assistant_for_active_workbench()
        for _ in range(24):
            _events()
        calls, results = [], []
        document_uid = str(document.Uid)

        class Provider(BaseProvider):
            def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
                calls.append({"prompt": prompt, "workbench": context.get("workbench")})
                if len(calls) == 1:
                    results.append(tool_runner("sheet_metal.edit", json.dumps({
                        "operation": "add_circle", "object_name": fixture.cut_name,
                        "center": [0, 0, 0], "radius": 10000}), "failed-cut"))
                results.append(tool_runner("state.read", '{"operation":"active"}', "inspect-state"))
                if len(calls) == 2:
                    target = results[0]["object_name"]
                    cuts = tool_runner("sheet_metal.inspect", json.dumps({
                        "operation": "list_cuts", "target": {
                            "document_uid": document_uid, "object_name": target}}), "inspect-failed-cut")
                    results.append(cuts)
                    matching = [item for item in cuts.get("items", ()) if item["object_name"] == target]
                    if cuts.get("ok") and len(matching) == 1:
                        results.append(tool_runner("sheet_metal.edit", json.dumps({
                            "operation": "update_circle", "object_name": target,
                            "operation_id": matching[0]["id"], "radius": 4}), "repair-radius"))
                tool_runner.provider_update()
                return ProviderResult(final_output="Inspected the current document state.")

        with patch.object(Session, "choose_provider", return_value=Provider()):
            VibeGui._execute_assistant_run(VibeGui._find_dock(), service,
                prompt="Add the requested hole and inspect the result.")
            for _ in range(32):
                _events()
                while VibeGui._is_assistant_run_active():
                    _events()
        (output / "recovery.json").write_text(json.dumps({"calls": calls, "results": results}, indent=2))
        self.assertFalse(results[0]["ok"], results)
        self.assertTrue(results[0]["parameters_committed"], results)
        self.assertNotIn("receipt", results[0])
        self.assertEqual(results[1]["error_code"], "NATIVE_REVISION_CONFLICT")
        self.assertEqual(len(calls), 2, calls)
        self.assertTrue(results[2]["ok"], results)
        self.assertEqual(len(results), 5, results)
        self.assertTrue(results[4]["ok"], results)
        self.assertIn("receipt", results[4])
        repaired = document.getObject(results[0]["object_name"])
        self.assertEqual(results[4]["object_name"], repaired.Name)
        self.assertTrue(repaired.Shape.isValid())
        self.assertTrue(repaired.FlatShape.isValid())
        self.assertTrue(all(call["workbench"] == "SMWorkbench" for call in calls))
        self.assertEqual(document.getObject(fixture.cut_name).Shape.exportBrepToString(), fixture.original_brep)
        foreign = {**results[1], "document_uid": "a-different-document"}
        self.assertIsNone(VibeGui._native_surface_continuation_event(SimpleNamespace(
            error=None, tool_trace=[{"tool_name": "state.read", "result": foreign}])))
