# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live Ollama acceptance runner for the real VibeScript provider path."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import threading
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADCodex as CodexModule
import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADMCP import get_control_mode_controller
from VibeCADProvider import CodexProvider
from VibeCADSession import run_prompt


def _shape_summary(document) -> dict:
    objects = []
    solid_count = 0
    for obj in document.Objects:
        shape = getattr(obj, "Shape", None)
        if shape is None or bool(shape.isNull()):
            continue
        solids = int(len(shape.Solids))
        solid_count += solids
        bounds = shape.BoundBox
        objects.append(
            {
                "name": str(obj.Name),
                "label": str(obj.Label),
                "type_id": str(obj.TypeId),
                "solids": solids,
                "valid": bool(shape.isValid()),
                "bounds_mm": [
                    float(bounds.XLength),
                    float(bounds.YLength),
                    float(bounds.ZLength),
                ],
            }
        )
    return {
        "document_object_count": int(len(document.Objects)),
        "shape_object_count": len(objects),
        "solid_count": solid_count,
        "objects": objects,
    }


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    prompt = str(os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_PROMPT") or "").strip()
    artifact = Path(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_ARTIFACT")
        or "/tmp/vibecad-ollama-acceptance.FCStd"
    ).expanduser().resolve()
    model = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_MODEL") or "qwen3.5:9b"
    ).strip()
    base_url = str(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_BASE_URL")
        or "http://127.0.0.1:11434/v1"
    ).strip()
    timeout_seconds = int(
        os.environ.get("VIBECAD_OLLAMA_ACCEPTANCE_TIMEOUT_SECONDS") or "900"
    )
    result: dict[str, object] = {}
    provider_worker = None
    poll = QtCore.QTimer()
    timeout = QtCore.QTimer()
    document = None

    def finish(code: int) -> None:
        poll.stop()
        timeout.stop()
        CodexModule.reset_managed_codex_sessions()
        application.exit(code)

    try:
        if not prompt:
            raise RuntimeError("VIBECAD_OLLAMA_ACCEPTANCE_PROMPT is required.")
        artifact.parent.mkdir(parents=True, exist_ok=True)
        get_control_mode_controller().request_mcp_enabled(False)
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        Gui.getMainWindow().resize(1440, 900)
        Gui.getMainWindow().show()
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("OllamaVibeScriptAcceptance")
        document.UndoMode = 1
        document.saveAs(str(artifact))
        service = get_service()
        service.select_modeling_engine("vibescript")
        CodexModule.reset_managed_codex_sessions()
        provider = CodexProvider(
            model=model,
            api_key="ollama-local",
            auth_mode="api_key",
            reasoning_effort="medium",
            timeout_seconds=float(timeout_seconds),
            base_url=base_url,
            web_search_enabled=False,
            skills_enabled=False,
        )

        def run_provider() -> None:
            try:
                result["response"] = run_prompt(
                    prompt,
                    service=service,
                    provider=provider,
                    document_thread_dispatch=VibeGui._dispatch_to_document_thread,
                )
            except BaseException as exc:
                result["error"] = exc
                result["traceback"] = traceback.format_exc()

        provider_worker = threading.Thread(
            target=run_provider,
            name="VibeCAD-live-Ollama-provider",
            daemon=True,
        )
        provider_worker.start()

        def inspect() -> None:
            if provider_worker is not None and provider_worker.is_alive():
                return
            try:
                if "error" in result:
                    raise AssertionError(result.get("traceback")) from result["error"]
                response = result["response"]
                if response.error:
                    raise AssertionError(response.error)
                document.recompute()
                document.save()
                view = Gui.activeDocument().activeView()
                view.viewAxonometric()
                view.fitAll()
                screenshot = artifact.with_suffix(".png")
                view.saveImage(str(screenshot), 1440, 900, "Current")
                summary = {
                    "ok": True,
                    "model": model,
                    "artifact": str(artifact),
                    "screenshot": str(screenshot),
                    "final_output": response.final_output,
                    "tool_trace": [
                        {
                            "tool": item.get("tool_name"),
                            "ok": item.get("ok"),
                            "failure_code": item.get("failure_code"),
                        }
                        for item in response.tool_trace
                    ],
                    "shape_summary": _shape_summary(document),
                }
                print(
                    "VIBECAD_OLLAMA_LIVE_ACCEPTANCE "
                    + json.dumps(summary, ensure_ascii=True, separators=(",", ":")),
                    flush=True,
                )
                finish(0)
            except BaseException:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        poll.timeout.connect(inspect)
        poll.start(100)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: finish(1))
        timeout.start(timeout_seconds * 1000)
    except BaseException:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
