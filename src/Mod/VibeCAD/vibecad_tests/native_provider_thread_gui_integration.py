# SPDX-License-Identifier: LGPL-2.1-or-later

"""End-to-end GUI-thread gate for one Native provider tool call."""

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

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADMCP import get_control_mode_controller
from VibeCADProvider import BaseProvider, ProviderResult
from VibeCADSession import run_prompt
from vibecad_tests.native_sketch_geometry_gui_support import (
    line_arguments,
    process_events,
)


class _ThreadedToolProvider(BaseProvider):
    """Match Codex's nested tool-callback thread without external I/O."""

    def __init__(self, arguments_json: str) -> None:
        self.arguments_json = arguments_json
        self.provider_thread = 0
        self.tool_thread = 0
        self.tool_result: dict | None = None
        self.tool_error: BaseException | None = None

    def run(
        self,
        prompt,
        context,
        tool_runner=None,
        cancellation_check=None,
        progress_callback=None,
    ) -> ProviderResult:
        del prompt, cancellation_check, progress_callback
        self.provider_thread = threading.get_ident()
        assert context["provider_tool_surface"]["engine"] == "native"
        assert context["provider_tool_surface"]["frozen"] is True
        assert callable(tool_runner)

        def call_tool() -> None:
            self.tool_thread = threading.get_ident()
            try:
                self.tool_result = tool_runner(
                    "sketch.geometry",
                    self.arguments_json,
                    "threaded-provider-create-line",
                )
            except BaseException as exc:
                self.tool_error = exc

        callback = threading.Thread(
            target=call_tool,
            name="VibeCAD-test-provider-tool-callback",
            daemon=True,
        )
        callback.start()
        callback.join(20)
        assert not callback.is_alive(), "Native provider tool callback deadlocked."
        if self.tool_error is not None:
            raise self.tool_error
        assert self.tool_result is not None
        assert self.tool_result.get("ok") is True, self.tool_result
        return ProviderResult("Native provider thread gate completed.")


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    temp_directory = tempfile.TemporaryDirectory(
        prefix="vibecad-native-provider-thread-"
    )
    worker = None
    poll = QtCore.QTimer()
    main_thread = threading.get_ident()
    result: dict[str, object] = {}
    exit_code = 1

    def finish(code: int) -> None:
        nonlocal exit_code
        exit_code = code
        poll.stop()
        try:
            if document is not None and document.Name in App.listDocuments():
                if Gui.activeDocument() is not None:
                    Gui.activeDocument().resetEdit()
                App.closeDocument(document.Name)
        finally:
            temp_directory.cleanup()
            application.exit(exit_code)

    try:
        get_control_mode_controller().request_mcp_enabled(False)
        VibeGui._ensure_document_thread_invoker()
        VibeGui._connect_document_observer()
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("NativeProviderThreadGate")
        document.UndoMode = 1
        service = get_service()
        service.select_modeling_engine("native")
        sketch = document.addObject("Sketcher::SketchObject", "ThreadedNativeSketch")
        sketch.Label = "Threaded Native provider Sketch"
        document.recompute()
        document.saveAs(str(Path(temp_directory.name) / "provider-thread.FCStd"))
        process_events(20)
        assert Gui.activeDocument().setEdit(sketch.Name)
        process_events(24)

        provider = _ThreadedToolProvider(
            json.dumps(
                line_arguments(
                    sketch,
                    geometry_count=0,
                    start=(-4.0, 0.0),
                    end=(4.0, 0.0),
                ),
                separators=(",", ":"),
            )
        )

        def run_provider() -> None:
            try:
                result["response"] = run_prompt(
                    "Create one exact line in the active Sketch.",
                    service=service,
                    provider=provider,
                    document_thread_dispatch=VibeGui._dispatch_to_document_thread,
                )
            except BaseException as exc:
                result["error"] = exc
                result["traceback"] = traceback.format_exc()

        worker = threading.Thread(
            target=run_provider,
            name="VibeCAD-test-provider-loop",
            daemon=True,
        )
        worker.start()

        def inspect_result() -> None:
            if worker is not None and worker.is_alive():
                return
            try:
                if "error" in result:
                    raise AssertionError(result.get("traceback")) from result["error"]
                response = result.get("response")
                assert response is not None
                assert response.error is None
                assert int(sketch.GeometryCount) == 1
                assert provider.provider_thread not in {0, main_thread}
                assert provider.tool_thread not in {
                    0,
                    main_thread,
                    provider.provider_thread,
                }
                print(
                    "VIBECAD_NATIVE_PROVIDER_THREAD_GUI_OK "
                    "provider-worker nested-tool-callback gui-dispatch",
                    flush=True,
                )
                finish(0)
            except BaseException:
                traceback.print_exc(file=sys.__stderr__)
                finish(1)

        poll.timeout.connect(inspect_result)
        poll.start(20)
        QtCore.QTimer.singleShot(30000, lambda: finish(1))
    except BaseException:
        traceback.print_exc(file=sys.__stderr__)
        finish(1)


QtCore.QTimer.singleShot(1000, _run)
