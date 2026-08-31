# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contract tests for VibeCAD's mutually exclusive MCP control mode."""

from __future__ import annotations

import errno
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

import pytest

import VibeCADMCP as mcp
from VibeCADMCPToolNames import (
    MCPToolNameError,
    mcp_tool_wire_name,
    mcp_wire_tool_schemas,
)


class _Event:
    def __init__(self) -> None:
        self.set_called = False

    def set(self) -> None:
        self.set_called = True


class _Process:
    pid = 2718


class _DeterministicController(mcp.VibeCADControlModeController):
    def __init__(self) -> None:
        super().__init__()
        self.started = threading.Event()
        self.fake_process = _Process()

    def _start_process(self, transition_id: int) -> None:
        with self._lock:
            if transition_id != self._transition_id:
                return
            self._process = self.fake_process
            self._shutdown_event = _Event()
            self._tool_cancellation = threading.Event()
        self._handle_server_event(transition_id, {"event": "listening"})
        self.started.set()


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for controller state.")


def test_mcp_wire_names_are_concise_pascal_case() -> None:
    assert mcp_tool_wire_name("vibescript.create_program") == "CreateProgram"
    assert mcp_tool_wire_name("vibescript.read_source") == "ReadSource"
    assert mcp_tool_wire_name("core.set_view") == "SetView"
    assert mcp_tool_wire_name("conversation.ask_user") == "AskUser"
    assert mcp_tool_wire_name("assembly.fastener") == "AssemblyFastener"
    assert mcp_tool_wire_name("model.fastener") == "ModelFastener"
    assert mcp_tool_wire_name("state.read") == "StateRead"
    assert mcp_tool_wire_name("vibecad.manage_document") == "ManageDocument"


def test_mcp_wire_surface_rejects_ambiguous_names() -> None:
    schemas = [
        {"name": "first.read_source", "parameters": {"type": "object"}},
        {"name": "second.read_source", "parameters": {"type": "object"}},
    ]

    with pytest.raises(MCPToolNameError, match="both advertise as 'ReadSource'"):
        mcp_wire_tool_schemas(schemas)


def test_mcp_ipc_address_rejects_live_owner_and_removes_stale_socket(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    address = str(tmp_path / "mcp.sock")
    Path(address).touch()
    probe_results = iter((0, errno.ECONNREFUSED))

    class _Probe:
        def settimeout(self, _timeout: float) -> None:
            return None

        def connect_ex(self, _address: str) -> int:
            return next(probe_results)

        def close(self) -> None:
            return None

    monkeypatch.setattr(mcp.socket, "socket", lambda *_args: _Probe())
    monkeypatch.setattr(mcp.socket, "AF_UNIX", object(), raising=False)

    with pytest.raises(RuntimeError, match="another VibeCAD instance"):
        mcp._prepare_mcp_ipc_address(address, "AF_UNIX")
    assert Path(address).exists()
    mcp._prepare_mcp_ipc_address(address, "AF_UNIX")
    assert not Path(address).exists()


def test_application_shutdown_waits_for_mcp_lifecycle_threads() -> None:
    joins = []

    class LifecycleThread:
        def __init__(self, name: str) -> None:
            self.name = name

        def join(self, timeout: float) -> None:
            joins.append((self.name, timeout))

    controller = mcp.VibeCADControlModeController()
    controller._start_thread = LifecycleThread("start")
    controller._monitor_thread = LifecycleThread("monitor")

    controller.shutdown(wait=True)

    assert controller._application_shutting_down is True
    assert joins == [
        ("start", mcp.MCP_START_TIMEOUT_SECONDS + 1.0),
        ("monitor", mcp.MCP_STOP_TIMEOUT_SECONDS + 2.0),
    ]


def test_control_mode_state_machine_never_enables_both_controllers() -> None:
    controller = _DeterministicController()
    controller.configure_host(
        document_thread_dispatch=lambda operation: operation(),
        internal_active=lambda: False,
        cancel_internal=lambda: None,
    )

    initial = controller.snapshot()
    assert initial["internal_agent_enabled"] is True
    assert initial["mcp_enabled"] is False

    controller.request_mcp_enabled(True)
    assert controller.started.wait(2.0)
    enabled = controller.snapshot()
    assert enabled["state"] == "mcp"
    assert enabled["internal_agent_enabled"] is False
    assert enabled["mcp_enabled"] is True

    controller.request_mcp_enabled(False)
    stopping = controller.snapshot()
    assert stopping["state"] == "stopping_mcp"
    assert stopping["internal_agent_enabled"] is False
    assert stopping["mcp_enabled"] is False
    assert controller._shutdown_event.set_called is True
    assert controller._tool_cancellation.is_set() is True
    controller._finish_process(controller.fake_process)

    disabled = controller.snapshot()
    assert disabled["state"] == "internal"
    assert disabled["internal_agent_enabled"] is True
    assert disabled["mcp_enabled"] is False


def test_cancelled_pre_spawn_transition_returns_to_internal_mode() -> None:
    internal_running = threading.Event()
    internal_running.set()
    controller = mcp.VibeCADControlModeController()
    controller.configure_host(
        document_thread_dispatch=lambda operation: operation(),
        internal_active=internal_running.is_set,
        cancel_internal=lambda: None,
    )

    controller.request_mcp_enabled(True)
    assert controller.snapshot()["state"] == "starting_mcp"
    controller.request_mcp_enabled(False)
    _wait_until(lambda: controller.snapshot()["state"] == "internal")
    assert controller.internal_agent_allowed() is True


def test_rapid_pre_spawn_reenable_converges_to_one_mcp_controller() -> None:
    internal_running = threading.Event()
    internal_running.set()
    controller = _DeterministicController()
    controller.configure_host(
        document_thread_dispatch=lambda operation: operation(),
        internal_active=internal_running.is_set,
        cancel_internal=lambda: None,
    )

    controller.request_mcp_enabled(True)
    controller.request_mcp_enabled(False)
    controller.request_mcp_enabled(True)
    internal_running.clear()
    assert controller.started.wait(2.0)
    _wait_until(lambda: controller.snapshot()["state"] == "mcp")
    assert controller.snapshot()["internal_agent_enabled"] is False


def test_start_failure_keeps_internal_agent_blocked_until_child_is_gone() -> None:
    controller = _DeterministicController()
    controller.configure_host(
        document_thread_dispatch=lambda operation: operation(),
        internal_active=lambda: False,
        cancel_internal=lambda: None,
    )
    controller.request_mcp_enabled(True)
    assert controller.started.wait(2.0)
    transition_id = controller._transition_id

    controller._fail_start(transition_id, "synthetic failure")
    failed = controller.snapshot()
    assert failed["state"] == "stopping_mcp"
    assert failed["internal_agent_enabled"] is False
    controller._finish_process(controller.fake_process)
    recovered = controller.snapshot()
    assert recovered["state"] == "internal"
    assert recovered["internal_agent_enabled"] is True
    assert recovered["last_error"] == "synthetic failure"


def test_ipc_collision_does_not_disable_persisted_mcp_preference() -> None:
    events = []
    controller = mcp.VibeCADControlModeController()
    controller._event_callback = events.append
    controller._transition_id = 3
    controller._desired_mode = mcp.ControlMode.MCP
    controller._state = mcp.ControllerState.STARTING_MCP

    controller._handle_server_event(
        3,
        {
            "event": "error",
            "failure_code": "MCP_IPC_UNAVAILABLE",
            "error": "another VibeCAD instance owns the local broker",
        },
    )

    assert controller.snapshot()["state"] == "internal"
    assert events[-1]["event"] == "mcp_error"
    assert events[-1]["rollback_preference"] is False


def test_unconfigured_host_rejects_mcp_and_requests_preference_rollback() -> None:
    events = []
    controller = mcp.VibeCADControlModeController()
    controller._event_callback = events.append

    snapshot = controller.request_mcp_enabled(True)

    assert snapshot["state"] == "internal"
    assert snapshot["desired_mode"] == "internal"
    assert snapshot["internal_agent_enabled"] is True
    assert snapshot["connection_state"] == "error"
    assert events[-1]["event"] == "mcp_error"
    assert events[-1]["rollback_preference"] is True


def test_host_surface_is_exact_internal_schema_plus_controller_tools() -> None:
    schemas = [
        {
            "name": "vibescript.read_source",
            "description": "Read one exact source.",
            "parameters": {
                "type": "object",
                "properties": {"program": {"type": "string"}},
                "required": ["program"],
                "additionalProperties": False,
            },
        },
        {
            "name": "vibescript.edit_source",
            "description": "Edit one exact source.",
            "parameters": {"type": "object", "properties": {}},
        },
    ]
    service = SimpleNamespace(active_workbench_name=lambda: "PartDesignWorkbench")
    resolution = SimpleNamespace(
        workbench="PartDesignWorkbench",
        engine="vibescript:partdesign",
        domain="partdesign",
        surface_id="partdesign-v1",
        available=True,
        unavailable_reason="",
    )
    modules = {
        "FreeCAD": SimpleNamespace(ActiveDocument=None),
        "VibeCADCore": SimpleNamespace(get_service=lambda: service),
        "VibeCADModelingSurface": SimpleNamespace(
            provider_engine_from_service=lambda _service: "vibescript",
            resolve_modeling_surface=lambda _workbench, _engine: resolution,
            resolve_service_surface=lambda _service, _workbench: resolution
        ),
        "VibeCADSession": SimpleNamespace(
            _minimal_runtime_state=lambda _service: {},
            provider_tool_schemas=lambda *_args, **_kwargs: schemas,
        ),
        "VibeCADVibeScriptDomains": SimpleNamespace(
            get_vibescript_pack=lambda _workbench: None
        ),
    }
    with mock.patch.dict(sys.modules, modules):
        session = mcp._HostToolSession(lambda operation: operation(), threading.Event())
        listed = session.list_tools()

    listed_tools = listed["tools"]
    assert listed_tools[: len(schemas)] == schemas
    assert [item["name"] for item in listed_tools[len(schemas) :]] == [
        mcp.READ_WORKBENCH_TOOL,
        mcp.RECOVER_DOCUMENTS_TOOL,
        mcp.MANAGE_DOCUMENT_TOOL,
    ]


def test_host_surface_uses_analyze_provider_without_changing_source_authority() -> None:
    schemas = [
        {
            "name": "analyze.inspect",
            "description": "Inspect the active study.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    ]
    service = SimpleNamespace(active_workbench_name=lambda: "FemWorkbench")
    authoring_resolution = SimpleNamespace(
        workbench="FemWorkbench",
        engine="vibescript",
        domain="fem",
        surface_id="vibescript-fem",
        available=True,
        unavailable_reason="",
    )
    provider_resolution = SimpleNamespace(
        workbench="FemWorkbench",
        engine="native",
        domain="analyze",
        surface_id="native-analyze",
        available=True,
        unavailable_reason="",
    )
    modules = {
        "FreeCAD": SimpleNamespace(ActiveDocument=None),
        "VibeCADCore": SimpleNamespace(get_service=lambda: service),
        "VibeCADModelingSurface": SimpleNamespace(
            provider_engine_from_service=lambda _service: "native",
            resolve_modeling_surface=(
                lambda _workbench, _engine: provider_resolution
            ),
            resolve_service_surface=(
                lambda _service, _workbench: authoring_resolution
            ),
        ),
        "VibeCADSession": SimpleNamespace(
            _minimal_runtime_state=lambda _service: {},
            provider_tool_schemas=lambda *_args, **_kwargs: schemas,
        ),
        "VibeCADVibeScriptDomains": SimpleNamespace(
            get_vibescript_pack=lambda _workbench: None
        ),
    }
    with mock.patch.dict(sys.modules, modules):
        session = mcp._HostToolSession(lambda operation: operation(), threading.Event())
        listed = session.list_tools()

    assert listed["modeling_surface"]["engine"] == "native"
    assert listed["modeling_surface"]["domain"] == "analyze"
    assert listed["tools"][0]["name"] == "analyze.inspect"


def test_host_runner_forwards_the_exact_frozen_provider_surface() -> None:
    schemas = [
        {
            "name": "model.feature",
            "description": "Create one exact feature.",
            "parameters": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        }
    ]
    frozen_surface = {
        "kind": "turn_start_snapshot",
        "frozen": True,
        "workbench": "PartDesignWorkbench",
        "engine": "native",
        "domain": "model",
        "surface_id": "native-model-surface",
        "available": True,
        "unavailable_reason": "",
        "tool_names": ["model.feature"],
        "schema_count": 1,
        "schema_sha256": "a" * 64,
    }
    context = {
        "provider_tool_surface": frozen_surface,
        "provider_tool_schemas": schemas,
        "modeling_surface": {
            "workbench": "PartDesignWorkbench",
            "engine": "native",
            "domain": "model",
            "surface_id": "native-model-surface",
            "available": True,
        },
    }
    snapshot = {
        "workbench": "PartDesignWorkbench",
        "modeling_surface": context["modeling_surface"],
        "schemas": schemas,
        "document_identity": "document-a",
        "source_authority_digest": "b" * 64,
    }
    captured = {}
    runner = object()

    def make_runner(_service, **kwargs):
        captured.update(kwargs)
        return runner

    session_module = SimpleNamespace(
        _build_context_for_provider=lambda *_args, **_kwargs: context,
        make_provider_tool_runner=make_runner,
    )
    host = mcp._HostToolSession(
        lambda operation: operation(),
        threading.Event(),
    )
    host._service = object()
    with mock.patch.dict(sys.modules, {"VibeCADSession": session_module}):
        resolved = host._runner_for(snapshot)

    assert resolved is runner
    assert captured["turn_surface"] == frozen_surface
    assert captured["turn_schemas"] == schemas
    assert captured["turn_modeling_surface"] == context["modeling_surface"]


def test_read_workbench_returns_only_live_ribbon_workbenches() -> None:
    entries = [
        ("Model", "PartDesignWorkbench"),
        ("Assemble", "AssemblyWorkbench"),
        ("Mesh", "MeshWorkbench"),
        ("Analyze", "FemWorkbench"),
        ("Manufacture", "CAMWorkbench"),
        ("Drawing", "TechDrawWorkbench"),
        ("Parameters", "SpreadsheetWorkbench"),
        ("Aero", "VibeCADAeroWorkbench"),
        ("Sketch", ""),
    ]

    class Tabs:
        def count(self) -> int:
            return len(entries)

        def tabText(self, index: int) -> str:
            return entries[index][0]

        def tabData(self, index: int) -> str:
            return entries[index][1]

    tabs = Tabs()
    main_window = SimpleNamespace(
        findChild=lambda _kind, name: tabs if name == "VibeCADRibbonTabs" else None
    )
    gui = SimpleNamespace(
        activeWorkbench=lambda: SimpleNamespace(name=lambda: "PartDesignWorkbench"),
        getMainWindow=lambda: main_window,
    )
    with mock.patch.dict(
        sys.modules,
        {
            "FreeCADGui": gui,
            "PySide": SimpleNamespace(
                QtWidgets=SimpleNamespace(QTabBar=object)
            ),
        },
    ):
        session = mcp._HostToolSession(lambda operation: operation(), threading.Event())
        result = session.call_tool(mcp.READ_WORKBENCH_TOOL, {})["result"]

    assert result == {
        "ok": True,
        "active_workbench": "PartDesignWorkbench",
        "available_workbenches": [
            {"name": workbench, "label": label}
            for label, workbench in entries
            if workbench
        ],
    }


def test_manage_document_rejects_unknown_action_before_gui_dispatch() -> None:
    dispatched = []
    session = mcp._HostToolSession(
        lambda operation: dispatched.append(operation), threading.Event()
    )

    result = session.call_tool(
        mcp.MANAGE_DOCUMENT_TOOL,
        {"action": "guess"},
    )["result"]

    assert result["ok"] is False
    assert result["failure_code"] == "DOCUMENT_ACTION_INVALID"
    assert dispatched == []


def test_read_operation_uses_existing_runner_without_live_surface_refresh() -> None:
    session = mcp._HostToolSession(lambda operation: operation(), threading.Event())
    calls = []

    def runner(name: str, arguments: str) -> dict:
        calls.append((name, json.loads(arguments)))
        return {
            "ok": True,
            "operation": {"operation_id": "operation-7", "status": "running"},
        }

    session._runner = runner
    session._live_surface = mock.Mock(
        side_effect=AssertionError("status reads must not refresh the live surface")
    )
    provider = SimpleNamespace(_provider_visible_tool_result=lambda result: result)
    with mock.patch.dict(sys.modules, {"VibeCADProvider": provider}):
        result = session.call_tool(
            "vibescript.read_operation",
            {"operation_id": "operation-7", "wait_seconds": 0},
        )["result"]

    assert result["ok"] is True
    assert result["operation"]["status"] == "running"
    assert calls == [
        (
            "vibescript.read_operation",
            {"operation_id": "operation-7", "wait_seconds": 0},
        )
    ]
    session._live_surface.assert_not_called()


def test_zero_wait_mcp_operation_read_returns_cached_state_while_host_is_busy() -> None:
    release_refresh = threading.Event()
    refresh_started = threading.Event()

    class Proxy:
        def __init__(self) -> None:
            self.status_calls = 0

        def request(self, method: str, **parameters):
            assert method == "call_tool"
            if parameters["name"] == "vibescript.create_program":
                return {
                    "result": {
                        "ok": True,
                        "operation": {
                            "operation_id": "operation-3",
                            "status": "running",
                            "progress": {"event": "queued"},
                        },
                    },
                    "image_attachment": None,
                }
            assert parameters == {
                "name": "vibescript.read_operation",
                "arguments": {
                    "operation_id": "operation-3",
                    "wait_seconds": 0,
                },
            }
            self.status_calls += 1
            refresh_started.set()
            assert release_refresh.wait(2.0)
            return {
                "result": {
                    "ok": True,
                    "operation": {
                        "operation_id": "operation-3",
                        "status": "succeeded",
                    },
                    "operation_succeeded": True,
                    "result": {"ok": True, "accepted_revision": "a" * 64},
                },
                "image_attachment": None,
            }

    proxy = Proxy()
    cache = mcp._ServerOperationStatusCache(proxy, threading.Event())
    created = cache.request(
        "call_tool",
        name="vibescript.create_program",
        arguments={},
    )
    assert created["result"]["operation"]["status"] == "running"

    started = time.monotonic()
    status = cache.request(
        "call_tool",
        name="vibescript.read_operation",
        arguments={"operation_id": "operation-3", "wait_seconds": 0},
    )
    assert time.monotonic() - started < 0.1
    assert status["result"]["operation"]["status"] == "running"
    assert refresh_started.wait(1.0)

    release_refresh.set()
    deadline = time.monotonic() + 2.0
    while time.monotonic() < deadline:
        status = cache.request(
            "call_tool",
            name="vibescript.read_operation",
            arguments={"operation_id": "operation-3", "wait_seconds": 0},
        )
        if status["result"]["operation"]["status"] == "succeeded":
            break
        time.sleep(0.01)
    assert status["result"]["operation"]["status"] == "succeeded"
    assert proxy.status_calls == 1


def test_manage_document_save_clears_native_gui_modified_state() -> None:
    class Document:
        Name = "Robot"
        Label = "Robot"
        FileName = "/tmp/Robot.FCStd"
        Objects = [object()]

        def __init__(self) -> None:
            self.saved = False

        def save(self) -> None:
            self.saved = True

    document = Document()
    gui_document = SimpleNamespace(Modified=True)
    app = SimpleNamespace(
        ActiveDocument=document,
        listDocuments=lambda: {document.Name: document},
    )
    gui = SimpleNamespace(
        getDocument=lambda name: gui_document if name == document.Name else None
    )
    qt_widgets = SimpleNamespace(
        QApplication=SimpleNamespace(topLevelWidgets=lambda: [])
    )
    with mock.patch.dict(
        sys.modules,
        {
            "FreeCAD": app,
            "FreeCADGui": gui,
            "PySide": SimpleNamespace(QtWidgets=qt_widgets),
        },
    ):
        session = mcp._HostToolSession(lambda operation: operation(), threading.Event())
        result = session.call_tool(
            mcp.MANAGE_DOCUMENT_TOOL,
            {"action": "save", "document": document.Name},
        )["result"]

    assert result["ok"] is True
    assert result["save_completed"] is True
    assert result["saved"]["modified"] is False
    assert document.saved is True
    assert gui_document.Modified is False


def test_manage_document_new_creates_named_saved_document(tmp_path: Path) -> None:
    target = tmp_path / "Fresh Robot.FCStd"
    documents = {}

    class Document:
        def __init__(self, name: str) -> None:
            self.Name = name
            self.Label = name
            self.FileName = ""
            self.Objects = []

        def saveAs(self, path: str) -> None:
            self.FileName = path
            Path(path).touch()

    gui_documents = {}
    app = SimpleNamespace(ActiveDocument=None)

    def new_document(name: str) -> Document:
        document = Document(name)
        documents[name] = document
        gui_documents[name] = SimpleNamespace(Modified=True)
        app.ActiveDocument = document
        return document

    def set_active_document(name: str) -> None:
        app.ActiveDocument = documents[name]

    app.listDocuments = lambda: documents
    app.newDocument = new_document
    app.setActiveDocument = set_active_document
    app.closeDocument = lambda name: documents.pop(name, None)
    gui = SimpleNamespace(getDocument=lambda name: gui_documents.get(name))
    qt_widgets = SimpleNamespace(
        QApplication=SimpleNamespace(topLevelWidgets=lambda: [])
    )
    with mock.patch.dict(
        sys.modules,
        {
            "FreeCAD": app,
            "FreeCADGui": gui,
            "PySide": SimpleNamespace(QtWidgets=qt_widgets),
        },
    ):
        session = mcp._HostToolSession(lambda operation: operation(), threading.Event())
        result = session.call_tool(
            mcp.MANAGE_DOCUMENT_TOOL,
            {"action": "new", "path": str(target)},
        )["result"]

    assert result["ok"] is True
    assert result["save_completed"] is True
    assert result["created"] == {
        "document": "Fresh_Robot",
        "label": "Fresh Robot",
        "path": str(target),
        "active": True,
        "modified": False,
        "object_count": 0,
    }
    assert target.is_file()


def test_manage_document_reports_duplicate_paths_and_rejects_ambiguous_save(
    tmp_path: Path,
) -> None:
    target = tmp_path / "Robot.FCStd"
    target.touch()

    class Document:
        Label = "Robot"
        FileName = str(target)
        Objects = []

        def __init__(self, name: str) -> None:
            self.Name = name
            self.saved = False

        def save(self) -> None:
            self.saved = True

    first = Document("Robot")
    second = Document("Robot001")
    documents = {first.Name: first, second.Name: second}
    gui_documents = {
        first.Name: SimpleNamespace(Modified=True),
        second.Name: SimpleNamespace(Modified=True),
    }
    app = SimpleNamespace(
        ActiveDocument=first,
        listDocuments=lambda: documents,
    )
    gui = SimpleNamespace(getDocument=lambda name: gui_documents.get(name))
    qt_widgets = SimpleNamespace(
        QApplication=SimpleNamespace(topLevelWidgets=lambda: [])
    )
    with mock.patch.dict(
        sys.modules,
        {
            "FreeCAD": app,
            "FreeCADGui": gui,
            "PySide": SimpleNamespace(QtWidgets=qt_widgets),
        },
    ):
        session = mcp._HostToolSession(lambda operation: operation(), threading.Event())
        listed = session.call_tool(mcp.MANAGE_DOCUMENT_TOOL, {"action": "list"})[
            "result"
        ]
        saved = session.call_tool(
            mcp.MANAGE_DOCUMENT_TOOL,
            {"action": "save", "document": first.Name},
        )["result"]

    assert listed["path_conflicts"] == [
        {"path": str(target), "documents": ["Robot", "Robot001"]}
    ]
    assert saved["ok"] is False
    assert saved["failure_code"] == "DOCUMENT_PATH_OPEN_BY_ANOTHER_DOCUMENT"
    assert first.saved is False


def test_recover_documents_runs_both_native_dialog_stages() -> None:
    class Item:
        def __init__(self) -> None:
            self.values = ["Robot Arm", "Not yet recovered"]

        def text(self, column: int) -> str:
            return self.values[column]

        def toolTip(self, _column: int) -> str:
            return ""

    class Button:
        def __init__(self, callback=None, *, enabled: bool = True) -> None:
            self.callback = callback
            self.enabled = enabled

        def click(self) -> None:
            self.callback()

        def isEnabled(self) -> bool:
            return self.enabled

    class Tree:
        def topLevelItemCount(self) -> int:
            return 1

        def topLevelItem(self, _index: int) -> Item:
            return item

    class ButtonBox:
        def button(self, role: int) -> Button:
            return ok if role == QtWidgets.QDialogButtonBox.Ok else cancel

    class Dialog:
        visible = True
        clicks = 0

        def objectName(self) -> str:
            return "Gui::Dialog::DocumentRecovery"

        def isVisible(self) -> bool:
            return self.visible

        def findChild(self, _kind, name: str):
            return tree if name == "treeWidget" else buttons

    item = Item()
    tree = Tree()
    buttons = ButtonBox()
    dialog = Dialog()
    cancel = Button(enabled=True)

    def accept() -> None:
        dialog.clicks += 1
        if dialog.clicks == 1:
            item.values[1] = "Successfully recovered"
            cancel.enabled = False
        else:
            dialog.visible = False

    ok = Button(accept)
    QtWidgets = SimpleNamespace(
        QApplication=SimpleNamespace(
            topLevelWidgets=lambda: [dialog],
            processEvents=lambda: None,
        ),
        QTreeWidget=Tree,
        QDialogButtonBox=SimpleNamespace(
            Ok=1,
            Cancel=2,
        ),
    )
    with mock.patch.dict(
        sys.modules,
        {"PySide": SimpleNamespace(QtWidgets=QtWidgets)},
    ):
        session = mcp._HostToolSession(lambda operation: operation(), threading.Event())
        result = session.call_tool(mcp.RECOVER_DOCUMENTS_TOOL, {})["result"]

    assert result == {
        "ok": True,
        "recovery_pending": True,
        "documents": [{"document": "Robot Arm", "status": "Successfully recovered"}],
    }
    assert dialog.clicks == 2


def test_server_host_proxy_serializes_concurrent_requests() -> None:
    class Connection:
        def __init__(self) -> None:
            self.request = None
            self.active = 0
            self.maximum_active = 0

        def send(self, request) -> None:
            self.request = request
            self.active += 1
            self.maximum_active = max(self.maximum_active, self.active)

        def recv(self):
            time.sleep(0.02)
            request = self.request
            assert isinstance(request, dict)
            self.active -= 1
            return {
                "request_id": request["request_id"],
                "ok": True,
                "payload": {"method": request["method"]},
            }

    connection = Connection()
    proxy = mcp._ServerHostProxy(connection)
    results = []
    threads = [
        threading.Thread(
            target=lambda: results.append(proxy.request("list_tools")), daemon=True
        )
        for _index in range(6)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(2.0)

    assert len(results) == 6
    assert connection.maximum_active == 1


def test_connection_configuration_is_a_clean_stdio_launch_specification() -> None:
    controller = mcp.VibeCADControlModeController()
    status = controller.snapshot()
    configuration = controller.connection_configuration()
    assert "token" not in json.dumps(status).lower()
    assert "token" not in json.dumps(configuration).lower()
    assert set(configuration) == {"command", "args"}
    assert configuration["command"]
    assert configuration["args"][-1].endswith("VibeCADMCPStdio.py")


def test_model_and_assembly_do_not_emit_a_false_mcp_surface_change() -> None:
    class Generation:
        def __init__(self) -> None:
            self.value = 0
            self.lock = threading.Lock()

        def get_lock(self):
            return self.lock

    controller = mcp.VibeCADControlModeController()
    generation = Generation()
    with controller._lock:
        controller._surface_generation = generation

    controller.notify_tool_surface_changed("PartDesignWorkbench")
    assert generation.value == 1
    controller.notify_tool_surface_changed("AssemblyWorkbench")
    assert generation.value == 1
    controller.notify_tool_surface_changed("MeshWorkbench")
    assert generation.value == 2
