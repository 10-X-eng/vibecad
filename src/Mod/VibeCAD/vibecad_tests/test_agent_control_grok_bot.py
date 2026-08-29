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
    # The brief documents the routes an agent needs.
    for route in (
        "/v1/status",
        "/v1/open",
        "/v1/save",
        "/v1/save-as",
        "/v1/ui/ribbon",
        "/v1/ui/menus",
        "/v1/ui/click",
        "/v1/screenshot",
        "/v1/run",
        "/v1/aero",
    ):
        assert route in text


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
    # Isolate the test from any desktop app installed on the developer machine,
    # then empty PATH so the remaining command-name candidates cannot resolve.
    monkeypatch.setattr(agent, "_default_grok_bot_candidates", lambda: ["grok-bot"])
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


@pytest.mark.parametrize("dispatcher_available", [False, True])
def test_connect_grok_bot_preserves_legacy_server_dispatcher_call_shape(
    tmp_path, monkeypatch, dispatcher_available
) -> None:
    started: list[dict[str, object]] = []
    fail_closed_started: list[dict[str, object]] = []
    enabled: list[bool] = []
    statuses: list[str] = []

    control_stub = SimpleNamespace(
        ensure_server_started=lambda **kwargs: started.append(dict(kwargs)),
        ensure_fail_closed_server_started=lambda **kwargs: fail_closed_started.append(
            dict(kwargs)
        ),
        load_or_create_token=lambda: "test-token",
        server_snapshot=lambda: {
            "running": True,
            "host": "127.0.0.1",
            "port": 8766,
            "base_url": "http://127.0.0.1:8766",
            "token_path": str(tmp_path / "token"),
        },
        endpoint_path=lambda: tmp_path / "endpoint.json",
        write_agent_brief=lambda: tmp_path / "AGENTS.md",
        detect_grok_bot_command=lambda _explicit: None,
    )
    monkeypatch.setitem(sys.modules, "VibeCADAgentControl", control_stub)
    dispatcher = lambda operation: operation()
    gui_stub = SimpleNamespace()
    if dispatcher_available:
        gui_stub._dispatch_to_document_thread = dispatcher
    monkeypatch.setitem(sys.modules, "VibeCADGui", gui_stub)
    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(
            QtWidgets=SimpleNamespace(
                QMessageBox=SimpleNamespace(information=lambda *_args: None)
            )
        ),
    )
    monkeypatch.setattr(
        prefs,
        "preferences",
        lambda: SimpleNamespace(SetString=lambda *_args: None),
    )
    page = SimpleNamespace(
        _grok_bot_connection=None,
        grok_bot_copy=SimpleNamespace(setEnabled=enabled.append),
        grok_bot_status=SimpleNamespace(setText=statuses.append),
        grok_bot_command=SimpleNamespace(text=lambda: ""),
        _launch_grok_bot=lambda *_args: False,
        form=object(),
    )

    prefs.VibeCADPreferencesPage._connect_grok_bot(page)

    expected_start = (
        [{"document_thread_dispatch": dispatcher}]
        if dispatcher_available
        else [{}]
    )
    assert started == expected_start
    assert fail_closed_started == []
    assert page._grok_bot_connection["base_url"] == "http://127.0.0.1:8766"
    assert enabled[-1] is True
    assert statuses[-1].startswith("connected |")


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
