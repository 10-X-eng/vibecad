# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for registering external MCP tool servers with the VibeCAD agent.

These servers are consumed *by* the built-in agent (VibeCAD is the MCP client).
They are unrelated to the mutually exclusive "External MCP control" mode in
which an outside client drives VibeCAD.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import sys
import time
from typing import Any

import pytest

import VibeCADMCPToolServers as servers_module
from VibeCADMCPToolServers import (
    CUA_DRIVER_SERVER_NAME,
    EXTERNAL_TOOL_SCHEMAS_CONTEXT_KEY,
    EXTERNAL_TOOL_SERVERS_CONTEXT_KEY,
    MCP_TOOL_NAMESPACE_PREFIX,
    MCP_TOOL_SERVERS_PREFERENCE_KEY,
    ExternalToolRunner,
    MCPToolServer,
    MCPToolServerConfigError,
    MCPToolServerManager,
    attach_external_tool_schemas,
    cua_driver_server,
    external_tool_name,
    external_tool_schema,
    external_tool_schemas_from_context,
    external_tools_instruction,
    load_mcp_tool_servers,
    mcp_tool_servers_from_json,
    mcp_tool_servers_to_json,
    register_mcp_tool_server,
    save_mcp_tool_servers,
    stdio_server_parameters,
    unregister_mcp_tool_server,
    wrap_tool_runner_with_external_tools,
)


FAKE_SERVER_SCRIPT = Path(__file__).resolve().with_name("fake_mcp_tool_server.py")


class _FakeParameterGroup:
    """Just enough of FreeCAD's ParameterGrp for string preferences."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def GetString(self, key: str, default: str = "") -> str:
        return self.values.get(key, default)

    def SetString(self, key: str, value: str) -> None:
        self.values[key] = value

    def RemString(self, key: str) -> None:
        self.values.pop(key, None)


def _fake_server(
    name: str = "fake",
    *,
    env: dict[str, str] | None = None,
    timeout_seconds: float = 30.0,
    tools: tuple[str, ...] = (),
) -> MCPToolServer:
    return MCPToolServer(
        name=name,
        transport="stdio",
        command=sys.executable,
        args=(str(FAKE_SERVER_SCRIPT),),
        env={"FAKE_MCP_SERVER_NAME": name, **(env or {})},
        timeout_seconds=timeout_seconds,
        tools=tools,
    )


def _frozen_cad_context() -> dict[str, Any]:
    schemas = [
        {
            "name": "core.read_state",
            "description": "Read state.",
            "parameters": {"type": "object", "properties": {}},
        }
    ]
    return {
        "workbench": "PartDesignWorkbench",
        "modeling_surface": {
            "workbench": "PartDesignWorkbench",
            "engine": "vibescript",
            "domain": "partdesign",
            "surface_id": "model",
            "available": True,
        },
        "provider_tool_schemas": schemas,
        "provider_tool_surface": {
            "kind": "turn_start_snapshot",
            "frozen": True,
            "workbench": "PartDesignWorkbench",
            "engine": "vibescript",
            "domain": "partdesign",
            "surface_id": "model",
            "available": True,
            "unavailable_reason": "",
            "tool_names": ["core.read_state"],
            "schema_count": 1,
            "schema_sha256": "abc",
        },
    }


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("VIBECAD_HOME", str(tmp_path))
    instance = MCPToolServerManager(runtime_directory=tmp_path / "mcp-tool-servers")
    try:
        yield instance
    finally:
        instance.shutdown()


# --------------------------------------------------------------------------
# Configuration model and persistence
# --------------------------------------------------------------------------


def test_mcp_tool_server_configuration_round_trips_through_json() -> None:
    stdio = MCPToolServer(
        name="cua-driver",
        transport="stdio",
        command="/opt/cua/cua-driver",
        args=("mcp",),
        env={"DISPLAY": ":0"},
        cwd="/tmp",
        timeout_seconds=45.0,
        tools=("screenshot", "click"),
        description="Desktop automation",
    )
    http = MCPToolServer(
        name="Docs Search",
        transport="http",
        url="https://mcp.example.com/mcp",
        headers={"Authorization": "Bearer ${DOCS_TOKEN}"},
    )

    text = mcp_tool_servers_to_json([stdio, http])
    decoded = json.loads(text)
    assert [entry["name"] for entry in decoded] == ["cua-driver", "Docs Search"]
    assert decoded[0]["args"] == ["mcp"]
    assert decoded[0]["enabled"] is True

    restored = mcp_tool_servers_from_json(text)
    assert restored == [stdio, http]
    assert restored[0].namespace == "mcp_cua_driver"
    assert restored[1].namespace == "mcp_docs_search"
    assert mcp_tool_servers_from_json("") == []


@pytest.mark.parametrize(
    "payload, message",
    (
        ({"name": "", "transport": "stdio", "command": "x"}, "name"),
        ({"name": "a", "transport": "carrier-pigeon", "command": "x"}, "transport"),
        ({"name": "a", "transport": "stdio"}, "command"),
        ({"name": "a", "transport": "http"}, "url"),
        ({"name": "a", "transport": "http", "url": "ftp://x"}, "http"),
        ({"name": "a", "transport": "stdio", "command": "x", "timeout_seconds": 0}, "timeout"),
        ({"name": "a", "transport": "stdio", "command": "x", "args": "mcp"}, "args"),
    ),
)
def test_mcp_tool_server_rejects_invalid_configuration(payload, message) -> None:
    with pytest.raises(MCPToolServerConfigError) as excinfo:
        MCPToolServer.from_dict(payload)
    assert message in str(excinfo.value).lower()


def test_mcp_tool_server_list_rejects_duplicate_names() -> None:
    text = json.dumps(
        [
            {"name": "same", "transport": "stdio", "command": "a"},
            {"name": "Same", "transport": "stdio", "command": "b"},
        ]
    )
    with pytest.raises(MCPToolServerConfigError):
        mcp_tool_servers_from_json(text)


def test_register_and_unregister_persist_in_preferences() -> None:
    pref = _FakeParameterGroup()
    assert load_mcp_tool_servers(pref=pref) == []

    first = MCPToolServer(name="alpha", transport="stdio", command="alpha")
    registered = register_mcp_tool_server(first, pref=pref)
    assert registered == [first]
    assert MCP_TOOL_SERVERS_PREFERENCE_KEY in pref.values

    replacement = MCPToolServer(
        name="alpha", transport="stdio", command="alpha", args=("--v2",)
    )
    second = MCPToolServer(name="beta", transport="http", url="http://127.0.0.1:9/mcp")
    register_mcp_tool_server(replacement, pref=pref)
    register_mcp_tool_server(second, pref=pref)
    assert load_mcp_tool_servers(pref=pref) == [replacement, second]

    assert unregister_mcp_tool_server("ALPHA", pref=pref) == [second]
    assert unregister_mcp_tool_server("missing", pref=pref) == [second]

    save_mcp_tool_servers([], pref=pref)
    assert load_mcp_tool_servers(pref=pref) == []


def test_corrupt_preference_text_loads_as_no_servers_without_raising() -> None:
    pref = _FakeParameterGroup()
    pref.SetString(MCP_TOOL_SERVERS_PREFERENCE_KEY, "{not json")
    assert load_mcp_tool_servers(pref=pref) == []


def test_reset_settings_removes_registered_mcp_tool_servers(monkeypatch) -> None:
    import VibeCADPreferences

    class _Group(_FakeParameterGroup):
        def __getattr__(self, name: str):
            return lambda *args, **kwargs: None

    group = _Group()
    group.SetString(MCP_TOOL_SERVERS_PREFERENCE_KEY, "[]")
    monkeypatch.setattr(VibeCADPreferences, "preferences", lambda: group)
    VibeCADPreferences.reset_settings()
    assert MCP_TOOL_SERVERS_PREFERENCE_KEY not in group.values


# --------------------------------------------------------------------------
# cua-driver registration
# --------------------------------------------------------------------------


def test_cua_driver_preset_registers_the_documented_launch_command() -> None:
    server = cua_driver_server()
    assert server.name == CUA_DRIVER_SERVER_NAME == "cua-driver"
    assert server.transport == "stdio"
    assert server.command == "cua-driver"
    assert server.args == ("mcp",)
    assert server.namespace == "mcp_cua_driver"
    assert server.enabled is True

    compat = cua_driver_server(
        command="/opt/cua/bin/cua-driver", computer_use_compat=True
    )
    assert compat.command == "/opt/cua/bin/cua-driver"
    assert compat.args == ("mcp", "--claude-code-computer-use-compat")

    pref = _FakeParameterGroup()
    register_mcp_tool_server(server, pref=pref)
    (loaded,) = load_mcp_tool_servers(pref=pref)
    assert loaded == server

    parameters = stdio_server_parameters(loaded)
    # An installed binary resolves to its absolute path; otherwise the name stays.
    assert Path(parameters.command).name.lower().startswith("cua-driver")
    assert parameters.args == ["mcp"]

    # The agent addresses cua-driver tools through one stable namespace.
    assert external_tool_name(loaded, "screenshot") == "mcp_cua_driver.screenshot"
    assert external_tool_name(loaded, "history_query") == "mcp_cua_driver.history_query"


@pytest.mark.skipif(
    shutil.which("cua-driver") is None,
    reason="cua-driver is not installed on this machine",
)
def test_cua_driver_live_tools_are_listed(manager) -> None:
    schemas, _routing, statuses = manager.tool_schemas_for_turn([cua_driver_server()])
    assert statuses[0]["ok"], statuses
    assert schemas
    assert all(schema["name"].startswith("mcp_cua_driver.") for schema in schemas)


# --------------------------------------------------------------------------
# Tool naming and schema conversion
# --------------------------------------------------------------------------


def test_external_tool_names_use_the_server_namespace_and_stay_wire_safe() -> None:
    server = _fake_server("Fake Server")
    assert server.namespace == "mcp_fake_server"
    assert external_tool_name(server, "add-numbers") == "mcp_fake_server.add_numbers"
    assert external_tool_name(server, "echo").startswith(MCP_TOOL_NAMESPACE_PREFIX)

    long_a = external_tool_name(server, "a" * 90 + "_first")
    long_b = external_tool_name(server, "a" * 90 + "_second")
    for name in (long_a, long_b):
        domain, separator, operation = name.partition(".")
        assert separator and domain == server.namespace and operation
        # Flattened wire names must fit every provider's 64 character limit.
        assert len(name.replace(".", "_")) <= 64
    assert long_a != long_b
    assert long_a == external_tool_name(server, "a" * 90 + "_first")


def test_external_tool_schema_normalizes_the_mcp_input_schema() -> None:
    from VibeCADProvider import _provider_tool_parameters

    server = _fake_server()
    bare = external_tool_schema(
        server,
        {"name": "picture", "description": "Return a PNG.", "inputSchema": {"type": "object"}},
    )
    assert bare["name"] == "mcp_fake.picture"
    assert bare["parameters"] == {"type": "object", "properties": {}}
    assert bare["description"].startswith("[fake] Return a PNG.")
    assert _provider_tool_parameters(bare)["type"] == "object"

    dotted = external_tool_schema(
        server,
        {
            "name": "echo",
            "description": "x" * 10_000,
            "input_schema": {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                "type": "object",
                "properties": {"text": {"type": "string"}},
                "required": ["text"],
            },
        },
    )
    assert "$schema" not in dotted["parameters"]
    assert dotted["parameters"]["required"] == ["text"]
    assert len(dotted["description"]) < 10_000

    with pytest.raises(ValueError):
        external_tool_schema(server, {"name": "", "inputSchema": {"type": "object"}})
    with pytest.raises(ValueError):
        external_tool_schema(server, {"name": "bad", "inputSchema": {"type": "string"}})


# --------------------------------------------------------------------------
# Live stdio client behaviour against the fake server
# --------------------------------------------------------------------------


def test_manager_lists_and_calls_tools_over_stdio(manager, tmp_path) -> None:
    events: list[dict[str, Any]] = []
    server = _fake_server()
    schemas, routing, statuses = manager.tool_schemas_for_turn(
        [server], progress_callback=events.append
    )
    assert [status["ok"] for status in statuses] == [True]
    assert statuses[0]["tool_count"] == 5
    names = [schema["name"] for schema in schemas]
    assert names == [
        "mcp_fake.echo",
        "mcp_fake.add_numbers",
        "mcp_fake.picture",
        "mcp_fake.fail",
        "mcp_fake.sleep",
    ]
    assert routing["mcp_fake.add_numbers"] == ("fake", "add-numbers")
    assert any(event["event"] == "external_tool_server_ready" for event in events)

    echoed = manager.call("mcp_fake.echo", {"text": "hello"})
    assert echoed["ok"] is True
    assert echoed["server"] == "fake"
    assert echoed["mcp_tool"] == "echo"
    assert echoed["content"] == [{"type": "text", "text": "hello"}]
    assert echoed["structured_content"] == {"echoed": "hello", "server": "fake"}

    summed = manager.call("mcp_fake.add_numbers", {"a": 2, "b": 3})
    assert summed["structured_content"] == {"sum": 5}

    failed = manager.call("mcp_fake.fail", {})
    assert failed["ok"] is False
    assert failed["failure_code"] == "MCP_TOOL_ERROR"
    assert failed["failure_stage"] == "external_process"
    assert "deliberate failure" in failed["error"]

    picture = manager.call("mcp_fake.picture", {})
    assert picture["ok"] is True
    attachment = picture["_vibecad_image_attachment"]
    image_path = Path(attachment["path"])
    assert image_path.is_file() and image_path.suffix == ".png"
    assert attachment["mime_type"] == "image/png"
    assert picture["content"][1]["type"] == "image"
    assert picture["content"][1]["mime_type"] == "image/png"
    assert "data" not in picture["content"][1]

    unknown = manager.call("mcp_fake.nope", {})
    assert unknown["ok"] is False
    assert unknown["failure_code"] == "UNKNOWN_TOOL"


def test_manager_filters_tools_through_the_allowlist(manager) -> None:
    server = _fake_server("filtered", tools=("echo", "picture"))
    schemas, routing, statuses = manager.tool_schemas_for_turn([server])
    assert statuses[0]["ok"]
    assert [schema["name"] for schema in schemas] == [
        "mcp_filtered.echo",
        "mcp_filtered.picture",
    ]
    assert set(routing) == {"mcp_filtered.echo", "mcp_filtered.picture"}


def test_manager_reports_startup_failure_without_blocking_the_turn(manager) -> None:
    events: list[dict[str, Any]] = []
    broken = _fake_server("broken", env={"FAKE_MCP_CRASH_ON_START": "1"}, timeout_seconds=5)
    healthy = _fake_server("healthy")
    started = time.monotonic()
    schemas, routing, statuses = manager.tool_schemas_for_turn(
        [broken, healthy], progress_callback=events.append
    )
    assert time.monotonic() - started < 20
    by_name = {status["name"]: status for status in statuses}
    assert by_name["broken"]["ok"] is False
    assert "refused to start" in by_name["broken"]["error"]
    assert by_name["healthy"]["ok"] is True
    assert all(schema["name"].startswith("mcp_healthy.") for schema in schemas)
    assert all(server == "healthy" for server, _tool in routing.values())
    failed_events = [
        event for event in events if event["event"] == "external_tool_server_failed"
    ]
    assert failed_events and failed_events[0]["name"] == "broken"

    # A failed server is not retried on every turn.
    manager.tool_schemas_for_turn([broken])
    assert manager.snapshot()["broken"]["connect_attempts"] == 1


def test_disabled_servers_are_skipped(manager) -> None:
    disabled = MCPToolServer(
        name="off", transport="stdio", command=sys.executable, enabled=False
    )
    schemas, routing, statuses = manager.tool_schemas_for_turn([disabled])
    assert schemas == [] and routing == {}
    assert statuses == [{"name": "off", "ok": False, "enabled": False, "tool_count": 0, "error": "disabled"}]


def test_manager_enforces_the_per_server_call_timeout(manager) -> None:
    server = _fake_server("slow", timeout_seconds=1.0)
    _schemas, _routing, statuses = manager.tool_schemas_for_turn([server])
    assert statuses[0]["ok"]
    started = time.monotonic()
    result = manager.call("mcp_slow.sleep", {"seconds": 30})
    assert time.monotonic() - started < 10
    assert result["ok"] is False
    assert result["failure_code"] == "MCP_TOOL_TIMEOUT"
    # The server stays usable after a timed-out call.
    assert manager.call("mcp_slow.echo", {"text": "still here"})["ok"] is True


def test_manager_reconnects_after_shutdown(manager) -> None:
    server = _fake_server("again")
    assert manager.tool_schemas_for_turn([server])[2][0]["ok"]
    manager.shutdown()
    assert manager.call("mcp_again.echo", {"text": "x"})["failure_code"] == "MCP_SERVER_UNAVAILABLE"
    assert manager.tool_schemas_for_turn([server])[2][0]["ok"]
    assert manager.call("mcp_again.echo", {"text": "x"})["ok"] is True


# --------------------------------------------------------------------------
# Turn context and tool-runner wrapper
# --------------------------------------------------------------------------


class _FakeManager:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.schemas = [
            {
                "name": "mcp_fake.echo",
                "description": "[fake] Echo.",
                "parameters": {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
            }
        ]
        self.routing = {"mcp_fake.echo": ("fake", "echo")}

    def tool_schemas_for_turn(self, servers, progress_callback=None):
        return (
            list(self.schemas),
            dict(self.routing),
            [{"name": "fake", "ok": True, "enabled": True, "tool_count": 1, "error": ""}],
        )

    def call(self, tool_name, arguments, *, cancellation_check=None):
        self.calls.append((tool_name, dict(arguments)))
        return {
            "ok": True,
            "tool": tool_name,
            "server": "fake",
            "mcp_tool": "echo",
            "content": [{"type": "text", "text": arguments.get("text", "")}],
        }


def test_attach_external_tool_schemas_leaves_the_frozen_cad_surface_untouched() -> None:
    context = _frozen_cad_context()
    before = json.loads(json.dumps(context))
    fake = _FakeManager()
    routing = attach_external_tool_schemas(
        context, servers=[_fake_server()], manager=fake
    )
    assert routing == fake.routing
    assert context["provider_tool_schemas"] == before["provider_tool_schemas"]
    assert context["provider_tool_surface"] == before["provider_tool_surface"]
    assert external_tool_schemas_from_context(context) == fake.schemas
    assert context[EXTERNAL_TOOL_SERVERS_CONTEXT_KEY][0]["name"] == "fake"

    # No registered servers means no new context keys at all.
    plain = _frozen_cad_context()
    assert attach_external_tool_schemas(plain, servers=[], manager=fake) == {}
    assert EXTERNAL_TOOL_SCHEMAS_CONTEXT_KEY not in plain
    assert external_tool_schemas_from_context(plain) == []


def test_external_tool_runner_routes_mcp_tools_and_delegates_the_rest() -> None:
    inner_calls: list[tuple[str, str, str]] = []

    def inner(tool_name: str, arguments_json: str = "{}", provider_call_id: str = "") -> dict[str, Any]:
        inner_calls.append((tool_name, arguments_json, provider_call_id))
        return {"ok": True, "inner": True}

    inner.provider_update = lambda: {"workbench": "PartDesignWorkbench"}
    inner.turn_transition_requested = lambda: False
    closed: list[bool] = []
    inner.close = lambda: closed.append(True)

    context = _frozen_cad_context()
    fake = _FakeManager()
    attach_external_tool_schemas(context, servers=[_fake_server()], manager=fake)
    trace: list[dict[str, Any]] = []
    events: list[dict[str, Any]] = []
    runner = wrap_tool_runner_with_external_tools(
        inner,
        context,
        manager=fake,
        tool_trace=trace,
        progress_callback=events.append,
    )
    assert isinstance(runner, ExternalToolRunner)

    cad = runner("core.read_state", "{}", "call-1")
    assert cad == {"ok": True, "inner": True}
    assert inner_calls == [("core.read_state", "{}", "call-1")]

    external = runner("mcp_fake.echo", json.dumps({"text": "hi"}), "call-2")
    assert external["ok"] is True
    assert fake.calls == [("mcp_fake.echo", {"text": "hi"})]
    assert len(inner_calls) == 1
    assert trace[-1]["tool_name"] == "mcp_fake.echo"
    assert trace[-1]["ok"] is True
    assert trace[-1]["safety"] == "external"
    completed = [event for event in events if event["event"] == "tool_call_completed"]
    assert completed and completed[-1]["tool_name"] == "mcp_fake.echo"

    bad_json = runner("mcp_fake.echo", "{not json", "call-3")
    assert bad_json["ok"] is False
    assert bad_json["failure_code"] == "INVALID_TOOL_ARGUMENTS_JSON"

    unknown = runner("mcp_fake.missing", "{}", "call-4")
    assert unknown["ok"] is False
    assert unknown["failure_code"] == "UNKNOWN_TOOL"
    assert "mcp_fake.echo" in unknown["candidates"]
    assert len(inner_calls) == 1

    refreshed = runner.provider_update()
    assert refreshed["workbench"] == "PartDesignWorkbench"
    assert external_tool_schemas_from_context(refreshed) == fake.schemas
    assert runner.turn_transition_requested() is False
    runner.close()
    assert closed == [True]


def test_external_tool_runner_honours_cancellation() -> None:
    context = _frozen_cad_context()
    fake = _FakeManager()
    attach_external_tool_schemas(context, servers=[_fake_server()], manager=fake)
    runner = wrap_tool_runner_with_external_tools(
        lambda *args: {"ok": True},
        context,
        manager=fake,
        tool_trace=[],
        progress_callback=None,
        cancellation_check=lambda: True,
    )
    result = runner("mcp_fake.echo", json.dumps({"text": "hi"}))
    assert result["ok"] is False
    assert result["failure_code"] == "RUN_CANCELLED"
    assert fake.calls == []


def test_wrapping_without_external_tools_returns_the_original_runner() -> None:
    def inner(*args):
        return {"ok": True}

    context = _frozen_cad_context()
    assert wrap_tool_runner_with_external_tools(inner, context, manager=_FakeManager(), tool_trace=[]) is inner


# --------------------------------------------------------------------------
# Provider wiring
# --------------------------------------------------------------------------


def test_system_instructions_describe_registered_mcp_servers() -> None:
    from VibeCADProvider import (
        MAX_PROVIDER_INSTRUCTIONS_BYTES,
        _provider_instructions,
        _system_instruction_sections,
    )

    plain = _frozen_cad_context()
    plain_sections = _system_instruction_sections(plain)
    assert not any("MCP" in section for section in plain_sections)
    assert external_tools_instruction(plain) == ""

    context = _frozen_cad_context()
    attach_external_tool_schemas(context, servers=[_fake_server()], manager=_FakeManager())
    sections = _system_instruction_sections(context)
    assert len(sections) == len(plain_sections) + 1
    assert "mcp_fake" in sections[-1]
    assert "fake" in sections[-1]
    assert len(_provider_instructions(context).encode("utf-8")) <= MAX_PROVIDER_INSTRUCTIONS_BYTES


def test_codex_declares_external_namespaces_without_changing_the_cad_surface() -> None:
    from VibeCADProvider import (
        _codex_external_dynamic_tools,
        _codex_flat_function_name,
    )

    context = _frozen_cad_context()
    attach_external_tool_schemas(context, servers=[_fake_server()], manager=_FakeManager())

    tools, names = _codex_external_dynamic_tools(context, namespaced=True)
    assert [tool["type"] for tool in tools] == ["namespace"]
    assert tools[0]["name"] == "mcp_fake"
    assert [function["name"] for function in tools[0]["tools"]] == ["echo"]
    assert tools[0]["tools"][0]["inputSchema"]["required"] == ["text"]
    assert names == {("mcp_fake", "echo"): "mcp_fake.echo"}

    flat_tools, flat_names = _codex_external_dynamic_tools(context, namespaced=False)
    flat_name = _codex_flat_function_name("mcp_fake", "echo")
    assert [tool["name"] for tool in flat_tools] == [flat_name]
    assert flat_names == {("", flat_name): "mcp_fake.echo"}

    assert _codex_external_dynamic_tools(_frozen_cad_context(), namespaced=True) == ([], {})


def test_provider_children_declare_external_tools_alongside_cad_tools() -> None:
    from VibeCADProvider import (
        _anthropic_tool_definition,
        _gemini_tool_definition,
        _provider_tool_surface_definitions,
    )

    context = _frozen_cad_context()
    attach_external_tool_schemas(context, servers=[_fake_server()], manager=_FakeManager())

    by_name, definitions = _provider_tool_surface_definitions(
        context, _anthropic_tool_definition, validate=False
    )
    assert by_name == {"core_read_state": "core.read_state", "mcp_fake_echo": "mcp_fake.echo"}
    assert [definition["name"] for definition in definitions] == ["core_read_state", "mcp_fake_echo"]

    gemini_by_name, gemini_definitions = _provider_tool_surface_definitions(
        context, _gemini_tool_definition, validate=False
    )
    assert gemini_by_name == by_name
    assert gemini_definitions[-1]["function"]["name"] == "mcp_fake_echo"

    plain_by_name, _plain = _provider_tool_surface_definitions(
        _frozen_cad_context(), _anthropic_tool_definition, validate=False
    )
    assert plain_by_name == {"core_read_state": "core.read_state"}


def test_session_turn_exposes_registered_mcp_tools_to_the_provider(monkeypatch) -> None:
    import VibeCADSession
    from VibeCADProvider import BaseProvider, ProviderResult

    fake = _FakeManager()
    monkeypatch.setattr(servers_module, "load_mcp_tool_servers", lambda pref=None: [_fake_server()])
    monkeypatch.setattr(servers_module, "get_mcp_tool_server_manager", lambda: fake)

    class _Service:
        def assistant_document_state(self):
            return {"enabled": True, "turn_enabled": True}

    inner_calls: list[str] = []

    def fake_runner(*args, **kwargs):
        def run(tool_name, arguments_json="{}", provider_call_id=""):
            inner_calls.append(tool_name)
            return {"ok": True}

        return run

    monkeypatch.setattr(VibeCADSession, "_build_context_for_provider", lambda *a, **k: _frozen_cad_context())
    monkeypatch.setattr(VibeCADSession, "_persist_session_conversation_turn", lambda *a, **k: {"conversation_id": "c1", "conversation": []})
    monkeypatch.setattr(VibeCADSession, "_load_conversation_for_session", lambda *a, **k: {"conversation_id": "c1", "conversation": []})
    monkeypatch.setattr(VibeCADSession, "_consume_context_view_attachment", lambda *a, **k: None)
    monkeypatch.setattr(VibeCADSession, "_provider_prompt", lambda *a, **k: "PROMPT")
    monkeypatch.setattr(VibeCADSession, "provider_input_budget", lambda *a, **k: {})
    monkeypatch.setattr(VibeCADSession, "make_provider_tool_runner", fake_runner)

    seen: dict[str, Any] = {}

    class _Provider(BaseProvider):
        def run(self, prompt, context, tool_runner=None, cancellation_check=None, progress_callback=None):
            seen["schemas"] = external_tool_schemas_from_context(context)
            seen["external"] = tool_runner("mcp_fake.echo", json.dumps({"text": "hi"}), "1")
            seen["cad"] = tool_runner("core.read_state", "{}", "2")
            return ProviderResult(final_output="done")

    events: list[dict[str, Any]] = []
    response = VibeCADSession._run_session_turn(
        "hello",
        service=_Service(),
        prefer_online=False,
        provider=_Provider(),
        progress_callback=events.append,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        output_authorization_callback=None,
        input_authorization_callback=None,
        session_trigger=None,
        persist_input_as_user=True,
        prompt_section="CURRENT_USER_MESSAGE",
        document_thread_dispatch=None,
    )
    assert response.error is None
    assert seen["schemas"] == fake.schemas
    assert seen["external"]["ok"] is True
    assert fake.calls == [("mcp_fake.echo", {"text": "hi"})]
    assert inner_calls == ["core.read_state"]
    assert [trace["tool_name"] for trace in response.tool_trace] == ["mcp_fake.echo"]
    assert any(event["event"] == "context_build_completed" and event.get("external_tool_count") == 1 for event in events)


# --------------------------------------------------------------------------
# Presets, editor helpers, Preferences page, and GUI events
# --------------------------------------------------------------------------


def test_browser_folder_and_fetch_presets_launch_the_reference_servers() -> None:
    from VibeCADMCPToolServers import (
        fetch_server,
        filesystem_server,
        playwright_browser_server,
    )

    browser = playwright_browser_server(headless=True, downloads_directory="/tmp/dl")
    assert browser.command == "npx"
    assert browser.args == ("-y", "@playwright/mcp@latest", "--headless", "--output-dir", "/tmp/dl")
    assert browser.namespace == "mcp_playwright"

    folder = filesystem_server("/home/me/project")
    assert folder.args == ("-y", "@modelcontextprotocol/server-filesystem", "/home/me/project")
    assert folder.namespace == "mcp_project_files"
    with pytest.raises(MCPToolServerConfigError):
        filesystem_server("")

    fetch = fetch_server()
    assert (fetch.command, fetch.args) == ("uvx", ("mcp-server-fetch",))


def test_editor_helpers_round_trip_arguments_and_key_values() -> None:
    from VibeCADMCPToolServers import (
        format_key_value_lines,
        join_command_arguments,
        parse_key_value_lines,
        split_command_arguments,
    )

    arguments = ("mcp", "--output-dir", "/tmp/my downloads")
    assert split_command_arguments(join_command_arguments(arguments)) == arguments
    assert split_command_arguments("") == ()

    mapping = {"DISPLAY": ":0", "TOKEN": "${CUA_TOKEN}"}
    assert parse_key_value_lines(format_key_value_lines(mapping)) == mapping
    assert parse_key_value_lines("# comment\n\nA = 1\n") == {"A": "1"}
    with pytest.raises(MCPToolServerConfigError):
        parse_key_value_lines("no equals sign")


def test_mcp_preferences_page_edits_and_tests_registered_tool_servers() -> None:
    root = Path(__file__).resolve().parents[4]
    preferences = (root / "src/Mod/VibeCAD/VibeCADPreferences.py").read_text(encoding="utf-8")
    mcp_page = preferences.split("class VibeCADMCPPreferencesPage:", 1)[1].split(
        "class VibeCADPromptStartersPreferencesPage:", 1
    )[0]
    for object_name in (
        "VibeCADPrefMCPToolServers",
        "VibeCADPrefMCPToolServerList",
        "VibeCADPrefMCPToolServerAdd",
        "VibeCADPrefMCPToolServerRemove",
        "VibeCADPrefMCPToolServerTest",
        "VibeCADPrefMCPToolServerAddCuaDriver",
        "VibeCADPrefMCPToolServerAddBrowser",
        "VibeCADPrefMCPToolServerAddFolder",
        "VibeCADPrefMCPToolServerCommand",
        "VibeCADPrefMCPToolServerStatus",
    ):
        assert f'setObjectName("{object_name}")' in mcp_page
    assert "cua_driver_server()" in mcp_page
    assert "playwright_browser_server()" in mcp_page
    assert "filesystem_server(directory)" in mcp_page
    assert "save_mcp_tool_servers(servers)" in mcp_page
    assert "load_mcp_tool_servers()" in mcp_page
    assert 'pref.RemString("MCPToolServers")' in preferences

    gui = (root / "src/Mod/VibeCAD/VibeCADGui.py").read_text(encoding="utf-8")
    assert "shutdown_mcp_tool_servers()" in gui


def test_gui_renders_external_tool_server_progress_events() -> None:
    import VibeCADGui

    ready = VibeCADGui._format_progress_event(
        {"event": "external_tool_server_ready", "name": "cua-driver", "tool_count": 12}
    )
    assert "cua-driver" in ready and "12" in ready
    failed = VibeCADGui._format_progress_event(
        {"event": "external_tool_server_failed", "name": "cua-driver", "error": "exit 3"}
    )
    assert "cua-driver" in failed and "exit 3" in failed
    assert VibeCADGui._progress_event_should_append_thinking(
        {"event": "external_tool_server_failed"}
    )
    assert VibeCADGui._progress_event_should_update_status(
        {"event": "external_tool_server_ready"}
    )
