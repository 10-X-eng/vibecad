# SPDX-License-Identifier: LGPL-2.1-or-later

"""End-to-end Codex acceptance gate for human-selected Native ribbons."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
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
from VibeCADRibbonSurface import read_active_ribbon_surface
from VibeCADSession import run_prompt


_SURFACES = (
    ("model", "PartDesignWorkbench", "model.feature"),
    ("assemble", "AssemblyWorkbench", "assembly.structure"),
    ("mesh", "MeshWorkbench", "mesh.modify"),
    ("analyze", "FemWorkbench", "analyze.model"),
    ("manufacture", "CAMWorkbench", "manufacture.job"),
    ("drawing", "TechDrawWorkbench", "drawing.page"),
    ("parameters", "SpreadsheetWorkbench", "parameters.read"),
    ("sketch.setup", "SketcherWorkbench", "sketch.setup"),
    ("sketch.edit", "SketcherWorkbench", "sketch.draw_line"),
)
_LIVE_ACCEPTANCE = os.environ.get("VIBECAD_LIVE_CODEX_ACCEPTANCE") == "1"


class _CrossRibbonCodexClient:
    """In-process app-server double that still traverses the real adapter."""

    expected_surface = ""
    observations: dict[str, dict[str, object]] = {}
    callback_error: BaseException | None = None

    def __init__(
        self,
        *,
        notification_handler,
        server_request_handler,
        environment=None,
    ) -> None:
        del environment
        self.notification_handler = notification_handler
        self.server_request_handler = server_request_handler
        self.alive = True
        self._thread_tools: dict[str, frozenset[str]] = {}

    @property
    def stderr_tail(self):
        return []

    def start(self) -> None:
        return None

    def set_handlers(
        self,
        *,
        notification_handler,
        server_request_handler,
    ) -> None:
        self.notification_handler = notification_handler
        self.server_request_handler = server_request_handler

    @staticmethod
    def _declared_tools(dynamic_tools) -> frozenset[str]:
        return frozenset(
            f"{namespace['name']}.{tool['name']}"
            for namespace in dynamic_tools
            for tool in namespace.get("tools") or []
        )

    def request(self, method, params, timeout):
        del timeout
        if method == "thread/start":
            surface_id = type(self).expected_surface
            assert surface_id, "No human-selected surface was declared for the turn."
            tools = self._declared_tools(params["dynamicTools"])
            required = next(
                tool
                for expected, _workbench, tool in _SURFACES
                if expected == surface_id
            )
            assert "state.read" in tools
            assert required in tools, (surface_id, required, sorted(tools))
            assert ("document.save" in tools) is (surface_id != "sketch.edit")
            thread_id = f"native-cross-ribbon-{surface_id}"
            self._thread_tools[thread_id] = tools
            return {"thread": {"id": thread_id}, "model": "gpt-test"}
        if method == "thread/resume":
            thread_id = str(params["threadId"])
            assert thread_id in self._thread_tools
            return {"thread": {"id": thread_id}, "model": "gpt-test"}
        if method == "turn/start":
            thread_id = str(params["threadId"])
            surface_id = thread_id.removeprefix("native-cross-ribbon-")
            turn_id = f"turn-{surface_id}"

            def callback() -> None:
                try:
                    bridge_result = self.server_request_handler(
                        "item/tool/call",
                        {
                            "callId": f"state-{surface_id}",
                            "namespace": "state",
                            "tool": "read",
                            "arguments": {"operation": "active"},
                        },
                    )
                    content_items = bridge_result["contentItems"]
                    payload = json.loads(content_items[0]["text"])
                    assert payload["ok"] is True, payload
                    assert "vibecad_state_after" not in payload, payload
                    assert payload["surface_id"] == surface_id, payload
                    type(self).observations[surface_id] = {
                        "tools": self._thread_tools[thread_id],
                        "state_surface": payload["surface_id"],
                        "callback_thread": threading.get_ident(),
                    }
                except BaseException as exc:
                    type(self).callback_error = exc

            worker = threading.Thread(
                target=callback,
                name=f"VibeCAD-test-{surface_id}-Codex-callback",
                daemon=True,
            )
            worker.start()
            worker.join(20)
            assert not worker.is_alive(), f"{surface_id} tool callback deadlocked."
            if type(self).callback_error is not None:
                raise type(self).callback_error
            self.notification_handler(
                "item/completed",
                {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "item": {
                        "type": "agentMessage",
                        "text": f"Read the active {surface_id} state.",
                    },
                },
            )
            self.notification_handler(
                "turn/completed",
                {
                    "threadId": thread_id,
                    "turnId": turn_id,
                    "turn": {"id": turn_id, "status": "completed"},
                },
            )
            return {"turn": {"id": turn_id}}
        if method == "thread/delete":
            self._thread_tools.pop(str(params["threadId"]), None)
            return {}
        raise AssertionError(method)

    def close(self) -> None:
        self.alive = False


def _process_events(rounds: int = 16) -> None:
    for _ in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    main_window = Gui.getMainWindow()
    document = None
    provider_worker = None
    poll = QtCore.QTimer()
    timeout = QtCore.QTimer()
    temporary_directory = tempfile.TemporaryDirectory(
        prefix="vibecad-native-cross-ribbon-"
    )
    original_client = CodexModule.CodexAppServerClient
    service = get_service()
    state = {"index": 0}
    result: dict[str, object] = {}
    main_thread = threading.get_ident()

    def finish(code: int) -> None:
        poll.stop()
        timeout.stop()
        CodexModule.CodexAppServerClient = original_client
        CodexModule.reset_managed_codex_sessions()
        try:
            if Gui.activeDocument() is not None and Gui.activeDocument().getInEdit():
                Gui.activeDocument().resetEdit()
            if document is not None and document.Name in App.listDocuments():
                App.closeDocument(document.Name)
        finally:
            temporary_directory.cleanup()
            application.exit(code)

    def activate_surface(surface_id: str, workbench: str) -> None:
        if surface_id == "sketch.edit":
            Gui.activateWorkbench(workbench)
            _process_events()
            assert Gui.activeDocument().setEdit("CrossRibbonSketch")
        else:
            if Gui.activeDocument() is not None and Gui.activeDocument().getInEdit():
                Gui.activeDocument().resetEdit()
            Gui.activateWorkbench(workbench)
        _process_events(24)
        controller = main_window.findChild(
            QtCore.QObject,
            "VibeCADRibbonController",
        )
        assert controller is not None
        surface = read_active_ribbon_surface(controller)
        assert surface.surface_id == surface_id, (surface_id, surface.surface_id)
        assert Gui.activeWorkbench().name() == workbench

    def launch_current_surface() -> None:
        nonlocal provider_worker
        surface_id, workbench, _required_tool = _SURFACES[state["index"]]
        activate_surface(surface_id, workbench)
        if not _LIVE_ACCEPTANCE:
            _CrossRibbonCodexClient.expected_surface = surface_id
            _CrossRibbonCodexClient.callback_error = None
        result.clear()

        def run_provider() -> None:
            try:
                result["worker_thread"] = threading.get_ident()
                result["response"] = run_prompt(
                    (
                        "Do not modify the document. Call state.read exactly once "
                        "with operation active, then report the returned surface_id "
                        f"for the human-selected {surface_id} ribbon."
                        if _LIVE_ACCEPTANCE
                        else f"Read the exact active {surface_id} state."
                    ),
                    service=service,
                    provider=provider,
                    document_thread_dispatch=VibeGui._dispatch_to_document_thread,
                )
            except BaseException as exc:
                result["error"] = exc
                result["traceback"] = traceback.format_exc()

        provider_worker = threading.Thread(
            target=run_provider,
            name=f"VibeCAD-test-{surface_id}-provider",
            daemon=True,
        )
        provider_worker.start()

    def inspect() -> None:
        if provider_worker is not None and provider_worker.is_alive():
            return
        try:
            if "error" in result:
                raise AssertionError(result.get("traceback")) from result["error"]
            surface_id = _SURFACES[state["index"]][0]
            response = result["response"]
            assert response.error is None, response.error
            assert result["worker_thread"] != main_thread
            if _LIVE_ACCEPTANCE:
                assert response.final_output
                assert [trace["tool_name"] for trace in response.tool_trace] == [
                    "state.read"
                ], response.tool_trace
                assert response.tool_trace[0]["ok"] is True
                assert response.context["native_state"]["surface_id"] == surface_id
            else:
                assert response.final_output == f"Read the active {surface_id} state."
                observation = _CrossRibbonCodexClient.observations[surface_id]
                assert observation["callback_thread"] not in {
                    main_thread,
                    result["worker_thread"],
                }
            state["index"] += 1
            if state["index"] < len(_SURFACES):
                launch_current_surface()
                return
            if not _LIVE_ACCEPTANCE:
                assert set(_CrossRibbonCodexClient.observations) == {
                    surface_id for surface_id, _workbench, _tool in _SURFACES
                }
            print(
                (
                    "VIBECAD_NATIVE_CODEX_LIVE_CROSS_RIBBON_GUI_OK "
                    if _LIVE_ACCEPTANCE
                    else "VIBECAD_NATIVE_CODEX_CROSS_RIBBON_GUI_OK "
                )
                + "surfaces=9 human-switch frozen-tools state-read gui-dispatch",
                flush=True,
            )
            finish(0)
        except BaseException:
            traceback.print_exc(file=sys.__stderr__)
            finish(1)

    try:
        get_control_mode_controller().request_mcp_enabled(False)
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        main_window.resize(1440, 900)
        main_window.show()
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeCodexCrossRibbonGate")
        document.UndoMode = 1
        document.addObject("Sketcher::SketchObject", "CrossRibbonSketch")
        document.recompute()
        document.saveAs(
            str(Path(temporary_directory.name) / "native-cross-ribbon.FCStd")
        )
        service.select_modeling_engine("native")
        _process_events(24)

        CodexModule.reset_managed_codex_sessions()
        if _LIVE_ACCEPTANCE:
            provider = CodexProvider(
                model=str(os.environ.get("VIBECAD_LIVE_CODEX_MODEL") or ""),
                auth_mode="chatgpt",
                reasoning_effort="low",
                skills_enabled=False,
            )
        else:
            _CrossRibbonCodexClient.expected_surface = ""
            _CrossRibbonCodexClient.observations = {}
            _CrossRibbonCodexClient.callback_error = None
            CodexModule.CodexAppServerClient = _CrossRibbonCodexClient
            provider = CodexProvider(
                model="gpt-test",
                api_key="test-key",
                auth_mode="api_key",
                skills_enabled=False,
            )
        poll.timeout.connect(inspect)
        poll.start(20)
        timeout.setSingleShot(True)
        timeout.timeout.connect(lambda: finish(1))
        timeout.start(600000 if _LIVE_ACCEPTANCE else 120000)
        launch_current_surface()
    except BaseException:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
