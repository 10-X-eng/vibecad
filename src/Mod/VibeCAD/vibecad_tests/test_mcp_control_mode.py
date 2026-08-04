# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contract tests for VibeCAD's mutually exclusive MCP control mode."""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from types import SimpleNamespace
from unittest import mock

import VibeCADMCP as mcp


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
            self._token = "unit-test-token"
        self._handle_server_event(transition_id, {"event": "listening"})
        self.started.set()


def _wait_until(predicate, timeout: float = 2.0) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for controller state.")


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
    enabled = controller.snapshot(include_token=True)
    assert enabled["state"] == "mcp"
    assert enabled["internal_agent_enabled"] is False
    assert enabled["mcp_enabled"] is True
    assert enabled["token"] == "unit-test-token"

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
    assert disabled["token_available"] is True
    assert controller.snapshot(include_token=True)["token"] == "unit-test-token"


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


def test_host_surface_is_exact_internal_schema_plus_two_controller_tools() -> None:
    schemas = [
        {
            "name": "vibescript.read_source",
            "description": "Read one exact source.",
            "parameters": {
                "type": "object",
                "properties": {"source_id": {"type": "string"}},
                "required": ["source_id"],
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
        session = mcp._HostToolSession(
            lambda operation: operation(), threading.Event()
        )
        listed = session.list_tools()

    listed_tools = listed["tools"]
    assert listed_tools[: len(schemas)] == schemas
    assert [item["name"] for item in listed_tools[len(schemas) :]] == [
        mcp.READ_WORKBENCH_TOOL,
        mcp.SWITCH_WORKBENCH_TOOL,
    ]


def test_bearer_middleware_rejects_missing_token_and_accepts_exact_token() -> None:
    called = []

    async def downstream(scope, receive, send) -> None:
        del receive
        called.append(scope)
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    async def invoke(token: str) -> list[dict]:
        sent = []

        async def send(message: dict) -> None:
            sent.append(message)

        headers = []
        if token:
            headers.append((b"authorization", f"Bearer {token}".encode("ascii")))
        middleware = mcp._BearerTokenMiddleware(downstream, "secret")
        await middleware(
            {"type": "http", "headers": headers},
            lambda: None,
            send,
        )
        return sent

    missing = asyncio.run(invoke(""))
    wrong = asyncio.run(invoke("wrong"))
    accepted = asyncio.run(invoke("secret"))
    assert missing[0]["status"] == 401
    assert wrong[0]["status"] == 401
    assert accepted[0]["status"] == 204
    assert len(called) == 1


def test_mcp_token_persists_in_the_os_credential_store_until_regenerated() -> None:
    class Keyring:
        def __init__(self) -> None:
            self.values = {}
            self.store_count = 0

        def get_password(self, service: str, account: str):
            return self.values.get((service, account))

        def set_password(self, service: str, account: str, value: str) -> None:
            self.store_count += 1
            self.values[(service, account)] = value

    keyring = Keyring()
    with mock.patch.object(mcp, "_mcp_keyring_module", return_value=keyring):
        first = mcp._persistent_mcp_token()
        second = mcp._persistent_mcp_token()
        rotated = mcp._persistent_mcp_token(regenerate=True)
        after_rotation = mcp._persistent_mcp_token()

    assert first == second
    assert rotated == after_rotation
    assert rotated != first
    assert keyring.store_count == 2
    assert len(first) >= 40


def test_missing_keyring_fails_instead_of_creating_an_ephemeral_token() -> None:
    with mock.patch.object(mcp, "_mcp_keyring_module", return_value=None):
        try:
            mcp._persistent_mcp_token()
        except RuntimeError as exc:
            assert "credential-store" in str(exc)
        else:
            raise AssertionError("Missing keyring unexpectedly produced an MCP token.")


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


def test_connection_configuration_contains_auth_without_persisting_token_in_status() -> None:
    controller = mcp.VibeCADControlModeController()
    with controller._lock:
        controller._token = "private-token"
    status = controller.snapshot()
    configuration = controller.connection_configuration()
    assert "private-token" not in json.dumps(status)
    assert configuration["headers"] == {"Authorization": "Bearer private-token"}


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
