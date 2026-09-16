# SPDX-License-Identifier: LGPL-2.1-or-later
"""Explicit live usability probe; excluded from the default native test suite.

Run through SMTests/run_native.py with this module as the test name. This uses a
private profile, the production provider/session path, no tool filtering, and no
wall-clock deadline. The fixed prompt is a supplemental sheet-metal case, not a
replacement for any NTS benchmark prompt. Preserve its artifacts on failures.
"""

from dataclasses import asdict
import json
import os
import shutil
from pathlib import Path
import threading
import traceback
import unittest

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets


PROMPT = (
    "Create a 1.6 mm thick L-shaped sheet-metal bracket, 70 mm long, with a 50 mm base "
    "and a 25 mm tall flange. Use a 2 mm inside bend radius. I want to switch between "
    "folded and flat views and keep the part editable."
)


def _events():
    Gui.updateGui()
    QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def export_step_artifact(shape, filename):
    # OCCT's STEP writer changes face Checked flags. The oracle must inspect
    # the original model, unaffected by collection of diagnostic artifacts.
    shape.copy().exportStep(str(filename))


class LiveSheetPrompt(unittest.TestCase):
    case_id = "SM-P01"
    prompt = PROMPT
    workbench = "SMWorkbench"

    def create_input(self):
        return App.newDocument("SheetPrompt")

    def open_input(self, document, filename):
        return document

    def test_editable_bracket_from_ordinary_request(self):
        import VibeCADGui as VibeGui
        import VibeCADCodex as Codex
        from VibeCADCore import get_service
        from VibeCADMCP import get_control_mode_controller
        from VibeCADProvider import CodexProvider
        from VibeCADSession import run_prompt, run_native_surface_continuation
        import SheetMetalEditable as Editable

        output = Path(os.environ["VIBECAD_TEST_OUTPUT"])
        model = os.environ.get("VIBECAD_SHEET_PROMPT_MODEL", "qwen3.5:9b")
        auth = os.environ.get("VIBECAD_SHEET_PROMPT_AUTH", "api_key")
        settings = {"case": self.case_id, "prompt": self.prompt, "model": model,
                    "auth_mode": auth, "reasoning_effort": "high", "timeout_seconds": None}
        (output/"case.json").write_text(json.dumps(settings, indent=2))
        get_control_mode_controller().request_mcp_enabled(False)
        VibeGui.ensure_commands_registered()
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        self.assertFalse(App.listDocuments())
        document = self.create_input()
        Gui.activateWorkbench(self.workbench)
        for _ in range(24):
            _events()
        service = get_service()
        service.select_modeling_engine("native")
        service.clear_reference_images()
        document.saveAs(str(output/"input.FCStd"))
        document = self.open_input(document, str(output/"input.FCStd"))
        shutil.copyfile(output/"input.FCStd", output/"fixture.FCStd")
        for _ in range(12):
            _events()
        provider = CodexProvider(model=model, auth_mode=auth,
            api_key="ollama-local" if auth == "api_key" else None,
            base_url=(os.environ.get("VIBECAD_SHEET_PROMPT_BASE_URL", "http://127.0.0.1:11434/v1")
                      if auth == "api_key" else None),
            reasoning_effort="high", timeout_seconds=None,
            web_search_enabled=False, skills_enabled=False)
        result = {}
        def progress(event):
            with (output/"events.jsonl").open("a") as stream:
                stream.write(json.dumps(event, default=str)+"\n")
        def run():
            try:
                responses = []
                response = run_prompt(self.prompt, service=service, provider=provider,
                    progress_callback=progress, document_thread_dispatch=VibeGui._dispatch_to_document_thread)
                responses.append(response)
                while True:
                    continuation = VibeGui._dispatch_to_document_thread(
                        lambda: VibeGui._native_surface_continuation_event(response))
                    if continuation is None:
                        break
                    response = run_native_surface_continuation(continuation, service=service,
                        provider=provider, progress_callback=progress,
                        document_thread_dispatch=VibeGui._dispatch_to_document_thread)
                    responses.append(response)
                result["responses"] = responses
            except BaseException:
                result["exception"] = traceback.format_exc()
        worker = threading.Thread(target=run, name="SheetMetal-live-provider")
        worker.start()
        while worker.is_alive():
            _events()
            worker.join(.025)
        # Only completed provider work reaches artifact collection. No deadline,
        # cancellation timer, checkpoint save, or document mutation interrupts it.
        while document.Recomputing or document.RecomputePending or document.CooperativeMutationActive:
            _events()
        for _ in range(24):
            _events()
        report = {"exception": result.get("exception"),
                  "responses": [asdict(item) for item in result.get("responses", [])]}
        (output/"responses.json").write_text(json.dumps(report, indent=2, default=str))
        document.saveAs(str(output/"result.FCStd"))
        summary = []
        states = []
        for obj in document.Objects:
            shape = getattr(obj, "Shape", None)
            if shape is None or shape.isNull():
                continue
            bounds = shape.BoundBox
            shared = isinstance(getattr(obj, "Proxy", None), Editable.PreparedSheetState)
            record = {"name": obj.Name, "type": obj.TypeId, "state": list(obj.State),
                      "valid": shape.isValid(), "solid_count": len(shape.Solids),
                      "volume_mm3": shape.Volume, "shared_sheet": shared,
                      "bounds_mm": [bounds.XLength, bounds.YLength, bounds.ZLength]}
            if shared:
                states.append(obj)
                flat = obj.FlatShape
                record.update(flat_valid=flat.isValid(), flat_solids=len(flat.Solids),
                    flat_bounds_mm=[flat.BoundBox.XLength, flat.BoundBox.YLength, flat.BoundBox.ZLength])
            summary.append(record)
            export_step_artifact(shape, output/(obj.Name+".step"))
        (output/"geometry.json").write_text(json.dumps(summary, indent=2))
        view = Gui.getDocument(document.Name).activeView()
        view.setAnimationEnabled(False)
        view.viewAxonometric()
        view.fitAll()
        view.redraw()
        view.saveImage(str(output/"result.png"), 1200, 900, "Current")
        Codex.shutdown_managed_codex_sessions()
        try:
            self.assertNotIn("exception", result, result.get("exception"))
            self.assertTrue(result.get("responses"))
            self.assertIsNone(result["responses"][-1].error)
            self.verify_geometry(document, states, summary)
        finally:
            for _ in range(12):
                _events()
            while not document.isClosable():
                _events()
            App.closeDocument(document.Name)

    def verify_geometry(self, document, states, summary):
        import SheetMetalEditable as Editable

        self.assertEqual(len(states), 1, summary)
        sheet = states[0]
        Editable.get_state_geometry(sheet)
        self.assertTrue(sheet.Shape.isValid() and sheet.FlatShape.isValid())
        self.assertEqual(len(sheet.Shape.Solids), 1)
        self.assertEqual(len(sheet.FlatShape.Solids), 1)
        bounds = sheet.Shape.BoundBox
        for actual, expected in zip(sorted((bounds.XLength, bounds.YLength, bounds.ZLength)), (25, 50, 70)):
            self.assertAlmostEqual(actual, expected, places=5)
        flat = sheet.FlatShape.BoundBox
        self.assertAlmostEqual(min(flat.XLength, flat.YLength, flat.ZLength), 1.6, places=5)
        source = sheet.SourceFace[0]
        radius = getattr(source, "radius", getattr(source, "Radius", None))
        self.assertAlmostEqual(float(radius), 2, places=5)
