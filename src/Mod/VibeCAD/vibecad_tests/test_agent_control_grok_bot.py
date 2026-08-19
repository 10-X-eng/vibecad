# SPDX-License-Identifier: LGPL-2.1-or-later

"""Tests for the Grok Bot connect helpers in VibeCADAgentControl.

These cover the pure logic behind the Preferences "Connect Grok Bot" button:
writing the AGENTS.md brief and resolving a launchable Grok Bot command. They
do not require the FreeCAD runtime.
"""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

import VibeCADAgentControl as agent


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
    for route in ("/v1/status", "/v1/open", "/v1/run"):
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
    # Empty PATH so the default candidate names cannot resolve.
    monkeypatch.setenv("PATH", "")
    assert agent.detect_grok_bot_command("/no/such/grok-bot/binary") is None


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
