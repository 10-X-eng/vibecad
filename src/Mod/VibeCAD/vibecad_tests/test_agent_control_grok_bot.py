# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests for the Grok Bot connect helpers in VibeCADAgentControl.

These cover the pure logic behind the Preferences "Connect Grok Bot" button:
writing the AGENTS.md brief and resolving a launchable Grok Bot command. They
do not require the FreeCAD runtime.
"""

from __future__ import annotations

import os
import stat
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import VibeCADAgentControl as agent
import VibeCADPreferences as prefs


@pytest.fixture(autouse=True)
def _isolated_agent_home(tmp_path, monkeypatch):
    monkeypatch.setenv(agent.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    monkeypatch.delenv(agent.AGENT_PORT_ENV, raising=False)
    monkeypatch.delenv(agent.GROK_BOT_CMD_ENV, raising=False)
    yield


def test_write_agent_brief_creates_readable_brief_with_connection() -> None:
    path = agent.write_agent_brief(port=8766)

    assert path == agent.brief_path()
    assert path.name == "AGENTS.md"
    text = path.read_text(encoding="utf-8")
    assert "http://127.0.0.1:8766" in text
    assert str(agent.token_path()) in text
    # The brief documents the routes an agent needs. CAD uses /v1/context
    # and /v1/prompt; /v1/run stays available for non-Aero Python.
    for route in (
        "/v1/status",
        "/v1/open",
        "/v1/run",
        "/v1/context",
        "/v1/prompt",
        "/v1/native",
        "/v1/aero",
        "/v1/screenshot",
        "NATIVE_AUTHORITY_CHANGED",
        "provider_tool_surface",
        "native_state",
        "not_measured",
    ):
        assert route in text
    assert "CAD" in text


def test_write_agent_brief_honors_explicit_port() -> None:
    path = agent.write_agent_brief(port=9123)
    assert "http://127.0.0.1:9123" in path.read_text(encoding="utf-8")


def test_detect_grok_bot_prefers_explicit_existing_path(tmp_path) -> None:
    exe = tmp_path / "grok-bot-app"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    exe.chmod(exe.stat().st_mode | stat.S_IXUSR)

    assert agent.detect_grok_bot_command(str(exe)) == str(exe)


def test_detect_grok_bot_uses_env_when_no_explicit(tmp_path, monkeypatch) -> None:
    exe = tmp_path / "grok.sh"
    exe.write_text("#!/bin/sh\n", encoding="utf-8")
    monkeypatch.setenv(agent.GROK_BOT_CMD_ENV, str(exe))

    assert agent.detect_grok_bot_command() == str(exe)


def test_detect_grok_bot_returns_none_when_missing(monkeypatch) -> None:
    # Empty PATH so the default candidate names cannot resolve.
    monkeypatch.setenv("PATH", "")
    assert agent.detect_grok_bot_command("/no/such/grok-bot/binary") is None


def test_run_script_refuses_aero_repair_exec() -> None:
    blocked = agent.run_script(python="import VibeCADAero\nVibeCADAero.run_analyze(App.ActiveDocument, repair=True)")
    assert blocked["ok"] is False
    assert blocked["failure_code"] == "AERO_USE_V1_AERO"


def test_aero_http_routes_are_registered(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "dispatch",
        lambda command, arguments=None: {
            "ok": True,
            "command": command,
            "arguments": dict(arguments or {}),
        },
    )
    status, payload = agent.handle_http_request("GET", "/v1/aero", {})
    assert status == 200
    assert payload["command"] == "aero"
    status, payload = agent.handle_http_request(
        "POST", "/v1/aero", {"operation": "analyze"}
    )
    assert payload["arguments"]["operation"] == "analyze"


def test_context_and_prompt_http_routes_are_registered(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "dispatch",
        lambda command, arguments=None: {
            "ok": True,
            "command": command,
            "arguments": dict(arguments or {}),
        },
    )
    status, payload = agent.handle_http_request("GET", "/v1/context", {})
    assert status == 200
    assert payload["command"] == "context"
    status, payload = agent.handle_http_request(
        "POST", "/v1/prompt", {"text": "fillet the selected edge"}
    )
    assert status == 200
    assert payload["command"] == "prompt"
    assert payload["arguments"]["text"] == "fillet the selected edge"


def test_native_http_route_is_registered(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "dispatch",
        lambda command, arguments=None: {
            "ok": True,
            "command": command,
            "arguments": dict(arguments or {}),
        },
    )
    status, payload = agent.handle_http_request(
        "POST",
        "/v1/native",
        {"capability": "inspect.query", "arguments": {"operation": "geometry_validity"}},
    )
    assert status == 200
    assert payload["command"] == "native"
    assert payload["arguments"]["capability"] == "inspect.query"


def test_screenshot_http_route_is_registered(monkeypatch) -> None:
    monkeypatch.setattr(
        agent,
        "dispatch",
        lambda command, arguments=None: {
            "ok": True,
            "command": command,
            "arguments": dict(arguments or {}),
        },
    )
    status, payload = agent.handle_http_request("GET", "/v1/screenshot", {})
    assert status == 200
    assert payload["command"] == "screenshot"
    assert payload["arguments"]["capture"] is True
    status, payload = agent.handle_http_request(
        "GET", "/v1/screenshot?capture=false", {}
    )
    assert payload["arguments"]["capture"] is False


def test_screenshot_command_requires_gui(monkeypatch) -> None:
    monkeypatch.setattr(agent, "_gui", lambda: None)
    payload = agent.screenshot_command({"capture": True})
    assert payload["ok"] is False
    assert payload["failure_code"] == "GUI_REQUIRED"


def test_screenshot_command_returns_presentation_attachment(monkeypatch) -> None:
    consumed: list[dict] = []

    class _Service:
        def capture_view_screenshot(self):
            return {"ok": True}

        def view_screenshot_summary(self):
            return {
                "captured": True,
                "artifact": {"path": r"C:\tmp\view.png"},
            }

        def consume_view_screenshot_attachment(self, screenshot):
            consumed.append(dict(screenshot))
            return {"consumed": True}

    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: _Service()),
    )
    payload = agent.screenshot_command({"capture": True})
    assert payload["ok"] is True
    assert payload["attachment"]["path"] == r"C:\tmp\view.png"
    assert payload["attachment"]["presentation_only"] is True
    assert payload["attachment"]["claim_ceiling"] == "not_measured"
    assert payload["screenshot"]["claim_ceiling"] == "not_measured"
    assert consumed[0]["path"] == r"C:\tmp\view.png"


def test_bot_turn_packet_exposes_screenshot_attachment() -> None:
    packet = agent._bot_turn_packet(
        {
            "view_screenshot": {
                "captured": True,
                "artifact": {"path": r"C:\tmp\view.png"},
            },
            "_vibecad_debug": {"must": "not leak"},
        }
    )
    assert packet["attachments"][0]["path"] == r"C:\tmp\view.png"
    assert packet["attachments"][0]["claim_ceiling"] == "not_measured"
    assert packet["view_screenshot"]["path"] == r"C:\tmp\view.png"
    assert "_vibecad_debug" not in packet


def test_native_command_requires_capability() -> None:
    payload = agent.native_command({})
    assert payload["ok"] is False
    assert payload["failure_code"] == "NATIVE_TOOL_REQUIRED"


def test_native_command_requires_gui(monkeypatch) -> None:
    monkeypatch.setattr(agent, "_gui", lambda: None)
    payload = agent.native_command({"capability": "inspect.query"})
    assert payload["ok"] is False
    assert payload["failure_code"] == "GUI_REQUIRED"


def test_native_command_calls_live_dispatcher(monkeypatch) -> None:
    agent._native_sessions.clear()
    calls: list[tuple[str, str, str]] = []

    class _Dispatcher:
        def call(self, tool_name, arguments_json, provider_call_id):
            calls.append((tool_name, arguments_json, provider_call_id))
            return {
                "ok": True,
                "claim_ceiling": "geometry_applied",
                "evidence_state": "pass",
            }

    class _Execution:
        dispatcher = _Dispatcher()

        def close(self):
            return None

    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeDispatch",
        SimpleNamespace(
            NativeDispatchError=type(
                "NativeDispatchError",
                (Exception,),
                {"code": "NATIVE_DISPATCH"},
            )
        ),
    )
    captured: dict = {}

    def _create_live(**kwargs):
        captured.update(kwargs)
        return _Execution()

    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeSessionFactory",
        SimpleNamespace(create_live_native_session_execution=_create_live),
    )
    payload = agent.native_command(
        {
            "capability": "inspect.query",
            "arguments": {"operation": "geometry_validity"},
            "call_id": "call-1",
        }
    )
    assert payload["ok"] is True
    assert payload["claim_ceiling"] == "geometry_applied"
    assert calls[0][0] == "inspect.query"
    assert '"operation":"geometry_validity"' in calls[0][1]
    assert captured["document_thread_dispatch"] is agent._on_document_thread
    assert payload["session_id"]
    assert payload["session_id"] in agent._native_sessions


def test_native_command_reuses_held_session(monkeypatch) -> None:
    agent._native_sessions.clear()
    created: list[int] = []
    closed: list[int] = []

    class _Dispatcher:
        def call(self, tool_name, arguments_json, provider_call_id):
            return {"ok": True, "tool": tool_name}

    class _Execution:
        dispatcher = _Dispatcher()

        def close(self):
            closed.append(1)

    def _create_live(**_kwargs):
        created.append(1)
        return _Execution()

    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeDispatch",
        SimpleNamespace(
            NativeDispatchError=type("NativeDispatchError", (Exception,), {"code": "X"})
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeSessionFactory",
        SimpleNamespace(create_live_native_session_execution=_create_live),
    )
    first = agent.native_command({"capability": "inspect.query", "arguments": {}})
    second = agent.native_command(
        {
            "capability": "inspect.query",
            "arguments": {},
            "session_id": first["session_id"],
        }
    )
    assert first["session_id"] == second["session_id"]
    assert created == [1]
    closed_payload = agent.native_command(
        {"close": True, "session_id": first["session_id"]}
    )
    assert closed_payload["ok"] is True
    assert closed_payload["closed"] is True
    assert closed == [1]
    assert first["session_id"] not in agent._native_sessions


def _held_extrude_session(monkeypatch) -> tuple[list[tuple[str, str, str]], list[int]]:
    agent._native_sessions.clear()
    calls: list[tuple[str, str, str]] = []
    created: list[int] = []

    class _Dispatcher:
        def call(self, tool_name, arguments_json, provider_call_id):
            calls.append((tool_name, arguments_json, provider_call_id))
            if '"stage":"apply"' in arguments_json:
                return {
                    "ok": True,
                    "applied": True,
                    "capability": "model.extrude",
                }
            return {
                "ok": True,
                "applied": False,
                "preview_id": "preview-extrude-1",
                "capability": "model.extrude",
                "expected_revision": 0,
                "claim_ceiling": "geometry_applied",
            }

    class _Execution:
        dispatcher = _Dispatcher()

        def close(self):
            return None

    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeDispatch",
        SimpleNamespace(
            NativeDispatchError=type("NativeDispatchError", (Exception,), {"code": "X"})
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeSessionFactory",
        SimpleNamespace(
            create_live_native_session_execution=lambda **_kwargs: created.append(1)
            or _Execution()
        ),
    )
    return calls, created


def test_held_session_extrude_propose_then_apply(monkeypatch) -> None:
    calls, created = _held_extrude_session(monkeypatch)
    proposed = agent.native_command(
        {
            "capability": "model.extrude",
            "arguments": {
                "operation": "extrude",
                "label": "Pad",
                "stage": "propose",
            },
        }
    )
    assert proposed["ok"] is True
    assert proposed["applied"] is False
    assert proposed["preview_id"] == "preview-extrude-1"
    assert proposed["claim_ceiling"] == "geometry_applied"
    session_id = proposed["session_id"]
    applied = agent.native_command(
        {
            "capability": "model.extrude",
            "session_id": session_id,
            "arguments": {
                "operation": "extrude",
                "label": "Pad",
                "stage": "apply",
                "preview_id": proposed["preview_id"],
            },
        }
    )
    assert applied["ok"] is True
    assert applied["applied"] is True
    assert applied["session_id"] == session_id
    assert created == [1]
    assert [item[0] for item in calls] == ["model.extrude", "model.extrude"]
    assert '"stage":"propose"' in calls[0][1]
    assert '"stage":"apply"' in calls[1][1]
    assert '"preview_id":"preview-extrude-1"' in calls[1][1]
    assert session_id in agent._native_sessions


def test_held_session_extrude_apply_does_not_open_a_second_session(
    monkeypatch,
) -> None:
    _calls, created = _held_extrude_session(monkeypatch)
    first = agent.native_command(
        {
            "capability": "model.extrude",
            "arguments": {"operation": "extrude", "label": "Pad", "stage": "propose"},
        }
    )
    second = agent.native_command(
        {
            "capability": "model.extrude",
            "session_id": first["session_id"],
            "arguments": {
                "operation": "extrude",
                "label": "Pad",
                "stage": "apply",
                "preview_id": first["preview_id"],
            },
        }
    )
    assert created == [1]
    assert first["session_id"] == second["session_id"]
    agent.native_command({"close": True, "session_id": first["session_id"]})
    assert first["session_id"] not in agent._native_sessions


def test_native_command_rejects_invalid_arguments_type() -> None:
    payload = agent.native_command(
        {"capability": "inspect.query", "arguments": "not-an-object"}
    )
    assert payload["ok"] is False
    assert payload["failure_code"] == "NATIVE_ARGUMENTS_INVALID"


def test_native_command_rejects_non_object_arguments() -> None:
    payload = agent.native_command(
        {"capability": "inspect.query", "arguments": ["not-an-object"]}
    )
    assert payload["ok"] is False
    assert payload["failure_code"] == "NATIVE_ARGUMENTS_INVALID"


def test_native_command_passes_operation_into_arguments_json(monkeypatch) -> None:
    calls: list[tuple[str, str, str]] = []

    class _Dispatcher:
        def call(self, tool_name, arguments_json, provider_call_id):
            calls.append((tool_name, arguments_json, provider_call_id))
            return {"ok": True}

    class _Execution:
        dispatcher = _Dispatcher()

        def close(self):
            return None

    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeDispatch",
        SimpleNamespace(
            NativeDispatchError=type(
                "NativeDispatchError",
                (Exception,),
                {"code": "NATIVE_DISPATCH"},
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeSessionFactory",
        SimpleNamespace(
            create_live_native_session_execution=lambda **_kwargs: _Execution()
        ),
    )
    payload = agent.native_command(
        {
            "capability": "inspect.query",
            "operation": "geometry_validity",
            "call_id": "call-2",
        }
    )
    assert payload["ok"] is True
    assert calls[0][0] == "inspect.query"
    assert '"operation":"geometry_validity"' in calls[0][1]


def test_dispatch_native_is_in_commands() -> None:
    assert "native" in agent.COMMANDS
    assert "native_session" in agent.COMMANDS


def test_get_native_session_reports_empty_without_opening_a_turn() -> None:
    agent._native_sessions.clear()
    payload = agent.native_session_command()
    assert payload == {"ok": True, "held": False, "sessions": []}


def test_get_native_session_reports_held_session_without_opening_a_turn(
    monkeypatch,
) -> None:
    agent._native_sessions.clear()
    created: list[int] = []

    class _Dispatcher:
        call_count = 2

        def call(self, *_args, **_kwargs):
            created.append(99)
            return {"ok": True}

        def pending_previews(self):
            return [{"preview_id": "preview-extrude-1", "applied": False}]

    class _Execution:
        dispatcher = _Dispatcher()
        run_id = "run-held"

        def close(self):
            return None

    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeDispatch",
        SimpleNamespace(
            NativeDispatchError=type("NativeDispatchError", (Exception,), {"code": "X"})
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeSessionFactory",
        SimpleNamespace(
            create_live_native_session_execution=lambda **_kwargs: created.append(1)
            or _Execution()
        ),
    )
    opened = agent.native_command({"capability": "model.extrude", "arguments": {}})
    session_id = opened["session_id"]
    created.clear()
    payload = agent.native_session_command({"session_id": session_id})
    assert payload["ok"] is True
    assert payload["held"] is True
    assert payload["session_id"] == session_id
    assert payload["run_id"] == "run-held"
    assert payload["call_count"] == 2
    assert payload["pending_previews"][0]["preview_id"] == "preview-extrude-1"
    assert created == []
    listed = agent.native_session_command()
    assert listed["session_id"] == session_id
    assert listed["held"] is True
    missing = agent.native_session_command({"session_id": "missing"})
    assert missing["ok"] is False
    assert missing["failure_code"] == "NATIVE_SESSION_MISSING"
    status, routed = agent.handle_http_request("GET", "/v1/native/session")
    assert status == 200
    assert routed["session_id"] == session_id
    assert routed["held"] is True


def test_idle_timeout_closes_held_native_session(monkeypatch) -> None:
    agent._native_sessions.clear()
    agent._native_session_last_used.clear()
    closed: list[int] = []

    class _Dispatcher:
        call_count = 1

        def call(self, *_args, **_kwargs):
            return {"ok": True}

        def pending_previews(self):
            return []

    class _Execution:
        dispatcher = _Dispatcher()
        run_id = "run-idle"

        def close(self):
            closed.append(1)

    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeDispatch",
        SimpleNamespace(
            NativeDispatchError=type("NativeDispatchError", (Exception,), {"code": "X"})
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeSessionFactory",
        SimpleNamespace(
            create_live_native_session_execution=lambda **_kwargs: _Execution()
        ),
    )
    monkeypatch.setenv(agent.NATIVE_SESSION_IDLE_ENV, "10")
    opened = agent.native_command({"capability": "model.extrude", "arguments": {}})
    session_id = opened["session_id"]
    assert session_id in agent._native_sessions
    now = agent._native_session_last_used[session_id] + 10
    closed_ids = agent.expire_idle_native_sessions(now=now)
    assert closed_ids == [session_id]
    assert closed == [1]
    assert session_id not in agent._native_sessions
    missing = agent.native_command(
        {
            "capability": "model.extrude",
            "session_id": session_id,
            "arguments": {},
        }
    )
    assert missing["ok"] is False
    assert missing["failure_code"] == "NATIVE_SESSION_MISSING"


def test_idle_timeout_does_not_close_a_fresh_session(monkeypatch) -> None:
    agent._native_sessions.clear()
    agent._native_session_last_used.clear()
    closed: list[int] = []

    class _Dispatcher:
        def call(self, *_args, **_kwargs):
            return {"ok": True}

    class _Execution:
        dispatcher = _Dispatcher()

        def close(self):
            closed.append(1)

    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeDispatch",
        SimpleNamespace(
            NativeDispatchError=type("NativeDispatchError", (Exception,), {"code": "X"})
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeSessionFactory",
        SimpleNamespace(
            create_live_native_session_execution=lambda **_kwargs: _Execution()
        ),
    )
    monkeypatch.setenv(agent.NATIVE_SESSION_IDLE_ENV, "30")
    opened = agent.native_command({"capability": "model.extrude", "arguments": {}})
    used = agent._native_session_last_used[opened["session_id"]]
    assert agent.expire_idle_native_sessions(now=used + 29) == []
    assert opened["session_id"] in agent._native_sessions
    assert closed == []


def test_prompt_command_requires_text() -> None:
    payload = agent.prompt_command({"text": "  "})
    assert payload["ok"] is False
    assert payload["failure_code"] == "PROMPT_TEXT_REQUIRED"


def test_prompt_command_requires_gui(monkeypatch) -> None:
    monkeypatch.setattr(agent, "_gui", lambda: None)
    payload = agent.prompt_command({"text": "fillet the selected edge"})
    assert payload["ok"] is False
    assert payload["failure_code"] == "GUI_REQUIRED"


def test_prompt_command_starts_in_app_build_turn(monkeypatch) -> None:
    started: list[str] = []
    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADGui",
        SimpleNamespace(
            start_assistant_turn=lambda text, **_kwargs: started.append(text) or True
        ),
    )
    payload = agent.prompt_command({"text": "fillet the selected edge"})
    assert payload["ok"] is True
    assert payload["started"] is True
    assert started == ["fillet the selected edge"]


def test_context_command_returns_frozen_catalog_and_native_state(monkeypatch) -> None:
    captured = {
        "workbench": "PartDesignWorkbench",
        "modeling_surface": {"engine": "native", "domain": "model", "available": True},
        "native_state": {
            "structural_revision": 4,
            "last_receipt": {"claim_ceiling": "geometry_applied"},
        },
        "intent": [{"text": "2 mm wall", "status": "active"}],
        "provider_tool_surface": {
            "kind": "turn_start_snapshot",
            "tool_names": ["inspect.query", "document.undo"],
            "schema_sha256": "abc",
        },
        "provider_tool_schemas": [
            {"name": "inspect.query", "parameters": {"properties": {"operation": {}}}}
        ],
        "_vibecad_debug": {"must": "not leak"},
    }
    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADSession",
        SimpleNamespace(_capture_context_for_provider=lambda *_args, **_kwargs: captured),
    )
    payload = agent.context_command()
    assert payload["ok"] is True
    context = payload["context"]
    assert context["native_state"]["structural_revision"] == 4
    assert context["native_state"]["last_receipt"]["claim_ceiling"] == "geometry_applied"
    assert "inspect.query" in context["provider_tool_surface"]["tool_names"]
    assert context["provider_tool_schemas"][0]["name"] == "inspect.query"
    assert context["intent"][0]["text"] == "2 mm wall"
    assert context["native_preview"]["stage"] == ["propose", "apply"]
    assert context["native_preview"]["preview_id"]["required_on"] == "apply"
    assert "model.extrude" in context["native_preview"]["families"]
    assert "model.sketch" not in context["native_preview"]["families"]
    assert "_vibecad_debug" not in context


def test_context_command_returns_intent_when_dispositions_exist(monkeypatch) -> None:
    captured = {
        "workbench": "PartDesignWorkbench",
        "intent": [{"text": "2 mm wall", "status": "active"}],
        "provider_tool_surface": {"tool_names": ["inspect.query"]},
    }
    monkeypatch.setattr(agent, "_gui", lambda: object())
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: object()),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADSession",
        SimpleNamespace(_capture_context_for_provider=lambda *_args, **_kwargs: captured),
    )
    payload = agent.context_command()
    assert payload["ok"] is True
    assert payload["context"]["intent"][0] == {
        "text": "2 mm wall",
        "status": "active",
    }


def test_context_command_handles_missing_gui(monkeypatch) -> None:
    monkeypatch.setattr(agent, "_gui", lambda: None)
    payload = agent.context_command()
    assert payload["ok"] is False
    assert payload["failure_code"] == "GUI_REQUIRED"


def test_windows_default_candidates_target_grok_bot_desktop(monkeypatch) -> None:
    monkeypatch.setattr(agent.sys, "platform", "win32")
    monkeypatch.setenv("ProgramFiles", r"C:\Program Files")

    candidates = agent._default_grok_bot_candidates()

    # The installed Grok Bot desktop app, at Program Files.
    assert r"C:\Program Files\Grok Bot\Grok Bot.exe" in candidates
    assert any(c.endswith(r"\Grok Bot\Grok Bot.exe") for c in candidates)
    # Never probe the bare Grok Build CLI (grok.exe) or a plain "grok" name.
    assert "grok" not in candidates
    assert not any(c.endswith(r"\grok.exe") for c in candidates)


def test_copy_grok_bot_connection_includes_brief_path(monkeypatch) -> None:
    copied: dict[str, str] = {}

    class _Clipboard:
        def setText(self, text: str) -> None:
            copied["text"] = text

    monkeypatch.setitem(
        __import__("sys").modules,
        "PySide",
        SimpleNamespace(
            QtWidgets=SimpleNamespace(
                QApplication=SimpleNamespace(clipboard=lambda: _Clipboard())
            )
        ),
    )
    page = SimpleNamespace(
        _grok_bot_connection={
            "base_url": "http://127.0.0.1:8766",
            "token": "secret-token",
            "token_path": "/tmp/token",
            "endpoint_path": "/v1",
            "brief_path": "/tmp/agent-home/AGENTS.md",
        },
        grok_bot_status=SimpleNamespace(
            setText=lambda text: copied.setdefault("status", text)
        ),
    )

    prefs.VibeCADPreferencesPage._copy_grok_bot_connection(page)

    assert "brief_path: /tmp/agent-home/AGENTS.md" in copied["text"]
    assert "base_url: http://127.0.0.1:8766" in copied["text"]
    assert copied["status"].startswith("copied")


def test_save_settings_persists_grok_bot_command(monkeypatch) -> None:
    stored: dict[str, Any] = {}

    class _Pref:
        def SetString(self, key: str, value: str) -> None:
            stored[key] = value

    monkeypatch.setattr(prefs, "preferences", lambda: _Pref())
    monkeypatch.setattr(prefs, "save_settings", lambda _settings: None)
    monkeypatch.setattr(
        prefs.App,
        "Console",
        SimpleNamespace(PrintWarning=lambda _message: None),
        raising=False,
    )
    page = SimpleNamespace(
        grok_bot_command=SimpleNamespace(text=lambda: " /opt/Grok Bot "),
        _current_settings=lambda: object(),
    )

    prefs.VibeCADPreferencesPage.saveSettings(page)

    assert stored["GrokBotCommand"] == "/opt/Grok Bot"
