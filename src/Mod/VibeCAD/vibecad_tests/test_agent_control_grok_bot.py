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
        "/v1/aero",
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


def test_context_command_returns_provider_summary(monkeypatch) -> None:
    summary = {
        "workbench": "PartDesignWorkbench",
        "document": {"name": "Box"},
        "selection": {"selection_count": 0, "selection": []},
    }

    class _Service:
        def provider_context_summary(self):
            return summary

    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: _Service()),
    )
    payload = agent.context_command()
    assert payload["ok"] is True
    assert payload["context"] == summary


def test_context_command_returns_intent_when_dispositions_exist(monkeypatch) -> None:
    import sys

    intent = [{"text": "2 mm wall", "status": "active"}]
    summary = {
        "workbench": "PartDesignWorkbench",
        "document": {"name": "Box"},
        "selection": {"selection_count": 0, "selection": []},
        "intent": intent,
    }

    class _Service:
        def provider_context_summary(self):
            return summary

    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=lambda: _Service()),
    )
    payload = agent.context_command()
    assert payload["ok"] is True
    assert payload["context"]["intent"] == intent
    assert payload["context"]["intent"][0] == {
        "text": "2 mm wall",
        "status": "active",
    }


def test_context_command_handles_missing_gui(monkeypatch) -> None:
    def _unavailable():
        raise RuntimeError("FreeCADGui is unavailable")

    monkeypatch.setattr(agent, "_gui", lambda: None)
    monkeypatch.setitem(
        sys.modules,
        "VibeCADCore",
        SimpleNamespace(get_service=_unavailable),
    )
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
