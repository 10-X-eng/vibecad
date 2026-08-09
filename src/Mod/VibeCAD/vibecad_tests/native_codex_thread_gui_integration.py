# SPDX-License-Identifier: LGPL-2.1-or-later

"""End-to-end Native Codex thread-affinity regression gate."""

from __future__ import annotations

import json
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
from VibeCADSession import run_prompt
from vibecad_tests.native_sketch_geometry_gui_support import (
    line_arguments,
    process_events,
)


class _ThreadedCodexClient:
    arguments: dict = {}
    callback_thread = 0
    callback_error: BaseException | None = None
    callback_result: dict | None = None

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
        self.namespace = ""
        self.tool = ""

    @property
    def stderr_tail(self):
        return []

    def start(self) -> None:
        return None

    def request(self, method, params, timeout):
        del timeout
        if method == "thread/start":
            for namespace in params["dynamicTools"]:
                if namespace.get("name") != "sketch":
                    continue
                for tool in namespace.get("tools") or []:
                    if tool.get("name") == "geometry":
                        self.namespace = "sketch"
                        self.tool = "geometry"
                        break
            assert self.namespace and self.tool
            return {"thread": {"id": "native-thread"}, "model": "gpt-test"}
        if method == "turn/start":
            def callback() -> None:
                type(self).callback_thread = threading.get_ident()
                try:
                    type(self).callback_result = self.server_request_handler(
                        "item/tool/call",
                        {
                            "callId": "native-codex-line",
                            "namespace": self.namespace,
                            "tool": self.tool,
                            "arguments": dict(type(self).arguments),
                        },
                    )
                except BaseException as exc:
                    type(self).callback_error = exc

            worker = threading.Thread(
                target=callback,
                name="VibeCAD-test-Codex-tool-callback",
                daemon=True,
            )
            worker.start()
            worker.join(20)
            assert not worker.is_alive(), "Codex tool callback deadlocked."
            if type(self).callback_error is not None:
                raise type(self).callback_error
            self.notification_handler(
                "item/completed",
                {
                    "threadId": "native-thread",
                    "turnId": "native-turn",
                    "item": {"type": "agentMessage", "text": "Line created."},
                },
            )
            self.notification_handler(
                "turn/completed",
                {
                    "threadId": "native-thread",
                    "turnId": "native-turn",
                    "turn": {"id": "native-turn", "status": "completed"},
                },
            )
            return {"turn": {"id": "native-turn"}}
        if method == "thread/delete":
            return {}
        raise AssertionError(method)

    def close(self) -> None:
        self.alive = False


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temp_directory = tempfile.TemporaryDirectory(prefix="vibecad-native-codex-thread-")
    original_client = CodexModule.CodexAppServerClient
    provider_worker = None
    poll = QtCore.QTimer()
    main_thread = threading.get_ident()
    result: dict[str, object] = {}

    def finish(code: int) -> None:
        poll.stop()
        CodexModule.CodexAppServerClient = original_client
        try:
            if document is not None and document.Name in App.listDocuments():
                if Gui.activeDocument() is not None:
                    Gui.activeDocument().resetEdit()
                App.closeDocument(document.Name)
        finally:
            temp_directory.cleanup()
            application.exit(code)

    try:
        get_control_mode_controller().request_mcp_enabled(False)
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeCodexThreadGate")
        document.UndoMode = 1
        service = get_service()
        service.select_modeling_engine("native")
        sketch = document.addObject("Sketcher::SketchObject", "NativeCodexSketch")
        document.recompute()
        document.saveAs(str(Path(temp_directory.name) / "native-codex.FCStd"))
        process_events(20)
        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(24)

        _ThreadedCodexClient.arguments = line_arguments(
            sketch,
            geometry_count=0,
            start=(-5.0, 0.0),
            end=(5.0, 0.0),
        )
        _ThreadedCodexClient.callback_thread = 0
        _ThreadedCodexClient.callback_error = None
        _ThreadedCodexClient.callback_result = None
        CodexModule.CodexAppServerClient = _ThreadedCodexClient
        provider = CodexProvider(
            model="gpt-test",
            api_key="test-key",
            auth_mode="api_key",
            skills_enabled=False,
        )

        def run_provider() -> None:
            try:
                result["worker_thread"] = threading.get_ident()
                result["response"] = run_prompt(
                    "Create one exact line in the active Sketch.",
                    service=service,
                    provider=provider,
                    document_thread_dispatch=VibeGui._dispatch_to_document_thread,
                )
            except BaseException as exc:
                result["error"] = exc
                result["traceback"] = traceback.format_exc()

        provider_worker = threading.Thread(
            target=run_provider,
            name="VibeCAD-test-Codex-provider-loop",
            daemon=True,
        )
        provider_worker.start()

        def inspect() -> None:
            if provider_worker is not None and provider_worker.is_alive():
                return
            try:
                if "error" in result:
                    raise AssertionError(result.get("traceback")) from result["error"]
                response = result.get("response")
                assert response is not None
                assert response.error is None
                assert int(sketch.GeometryCount) == 1
                assert result["worker_thread"] != main_thread
                assert _ThreadedCodexClient.callback_thread not in {
                    0,
                    main_thread,
                    result["worker_thread"],
                }
                assert _ThreadedCodexClient.callback_result is not None
                print(
                    "VIBECAD_NATIVE_CODEX_THREAD_GUI_OK "
                    "frozen-surface provider-worker nested-callback gui-dispatch",
                    flush=True,
                )
                finish(0)
            except BaseException:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        poll.timeout.connect(inspect)
        poll.start(20)
        QtCore.QTimer.singleShot(30000, lambda: finish(1))
    except BaseException:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
