# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for the local agent-control channel."""

from __future__ import annotations

from argparse import Namespace
import json
from pathlib import Path
from types import SimpleNamespace
from urllib import error, request

import pytest

import VibeCADAgentCli as cli
import VibeCADAgentControl as control
import VibeCADAuth as auth


class _Document:
    def __init__(self, name: str, path: str = "", objects: list | None = None) -> None:
        self.Name = name
        self.Label = name
        self.FileName = path
        self.Objects = list(objects or [])
        self.recomputed = False

    def recompute(self) -> None:
        self.recomputed = True


class _App:
    def __init__(self) -> None:
        self.documents: dict[str, _Document] = {}
        self.ActiveDocument: _Document | None = None
        self.opened: list[str] = []

    def listDocuments(self) -> dict[str, _Document]:
        return dict(self.documents)

    def setActiveDocument(self, name: str) -> None:
        self.ActiveDocument = self.documents[name]

    def openDocument(self, path: str) -> _Document:
        self.opened.append(path)
        document = _Document(Path(path).stem, path)
        self.documents[document.Name] = document
        self.ActiveDocument = document
        return document


def _install_app(monkeypatch, app: _App) -> None:
    import FreeCAD

    monkeypatch.setattr(FreeCAD, "listDocuments", app.listDocuments, raising=False)
    monkeypatch.setattr(FreeCAD, "setActiveDocument", app.setActiveDocument, raising=False)
    monkeypatch.setattr(FreeCAD, "openDocument", app.openDocument, raising=False)
    monkeypatch.setattr(FreeCAD, "ActiveDocument", app.ActiveDocument, raising=False)

    def _refresh_active(*_args, **_kwargs):
        FreeCAD.ActiveDocument = app.ActiveDocument
        return None

    original_set = app.setActiveDocument

    def set_active(name: str) -> None:
        original_set(name)
        FreeCAD.ActiveDocument = app.ActiveDocument

    original_open = app.openDocument

    def open_document(path: str) -> _Document:
        document = original_open(path)
        FreeCAD.ActiveDocument = app.ActiveDocument
        return document

    monkeypatch.setattr(FreeCAD, "setActiveDocument", set_active)
    monkeypatch.setattr(FreeCAD, "openDocument", open_document)
    monkeypatch.setattr(app, "setActiveDocument", set_active)
    monkeypatch.setattr(app, "openDocument", open_document)


def test_token_and_endpoint_stay_in_agent_home(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    token = control.load_or_create_token()
    assert len(token) >= 40
    assert control.load_token() == token
    path = control.write_endpoint(host="127.0.0.1", port=8766)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["base_url"] == "http://127.0.0.1:8766"
    assert payload["assistant_disabled_by_this_channel"] is False
    assert "token" not in payload
    assert payload["token_path"] == str(control.token_path())


def test_status_reports_grok_without_secrets(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    import VibeCADGrokAuth as grok

    monkeypatch.setenv(grok.GROK_HOME_ENV, str(tmp_path / "grok-home"))
    grok.store_tokens(
        grok.GrokTokens(
            access_token="secret-access-token",
            refresh_token="secret-refresh-token",
            expires_at=9_999_999_999,
            account=grok.GrokAccount(email="user@x.ai", name="User"),
        )
    )

    class _Settings:
        provider = "grok"
        active_model = "grok-4.6"
        active_base_url = grok.DEFAULT_XAI_API_BASE
        use_online_provider = True
        mcp_enabled = False

    monkeypatch.setattr(control, "_safe_settings", lambda: _Settings())
    payload = control.dispatch("status")
    assert payload["ok"] is True
    assert payload["provider"] == "grok"
    assert payload["assistant_available"] is True
    assert payload["mcp_enabled"] is False
    assert payload["grok"]["signed_in"] is True
    assert payload["grok"]["email"] == "user@x.ai"
    dumped = json.dumps(payload)
    assert "secret-access-token" not in dumped
    assert "secret-refresh-token" not in dumped
    assert "xAI OAuth" in payload["oauth_note"]


def test_open_and_run_python_against_active_document(tmp_path, monkeypatch) -> None:
    app = _App()
    _install_app(monkeypatch, app)
    document_path = tmp_path / "part.FCStd"
    document_path.write_bytes(b"fcstd")
    script_path = tmp_path / "edit.py"
    script_path.write_text(
        "result = App.ActiveDocument.Name\nprint('ran')\n",
        encoding="utf-8",
    )

    opened = control.dispatch("open", {"path": str(document_path)})
    assert opened["ok"] is True
    assert opened["already_open"] is False
    assert opened["opened"]["path"] == str(document_path.resolve())

    again = control.dispatch("open", {"path": str(document_path)})
    assert again["already_open"] is True

    ran = control.dispatch(
        "run",
        {"script": str(script_path), "recompute": True},
    )
    assert ran["ok"] is True
    assert ran["result"] == "part"
    assert "ran" in ran["stdout"]
    assert app.ActiveDocument is not None
    assert app.ActiveDocument.recomputed is True


def test_run_reports_script_errors(monkeypatch) -> None:
    app = _App()
    _install_app(monkeypatch, app)
    payload = control.dispatch("run", {"python": "raise RuntimeError('boom')"})
    assert payload["ok"] is False
    assert payload["failure_code"] == "SCRIPT_FAILED"
    assert "boom" in payload["error"]


def test_open_requires_absolute_existing_path(tmp_path) -> None:
    missing = control.dispatch("open", {"path": str(tmp_path / "missing.FCStd")})
    assert missing["ok"] is False
    assert missing["failure_code"] == "DOCUMENT_NOT_FOUND"
    relative = control.dispatch("open", {"path": "part.FCStd"})
    assert relative["failure_code"] == "DOCUMENT_PATH_NOT_ABSOLUTE"


def test_preferences_require_gui(monkeypatch) -> None:
    import FreeCADGui

    monkeypatch.setattr(FreeCADGui, "showPreferencesByName", None, raising=False)
    monkeypatch.setattr(FreeCADGui, "getMainWindow", None, raising=False)
    monkeypatch.setattr(FreeCADGui, "GuiUp", False, raising=False)
    payload = control.dispatch("preferences")
    assert payload["ok"] is False
    assert payload["failure_code"] == "GUI_REQUIRED"


def test_preferences_open_named_page(monkeypatch) -> None:
    import FreeCADGui

    calls: list[tuple[str, str]] = []

    def show(group: str, page: str) -> None:
        calls.append((group, page))

    monkeypatch.setattr(FreeCADGui, "showPreferencesByName", show, raising=False)
    monkeypatch.setattr(FreeCADGui, "GuiUp", True, raising=False)
    payload = control.dispatch("preferences")
    assert payload == {"ok": True, "opened": "VibeCAD"}
    assert calls == [("VibeCAD", "VibeCAD")]


def test_http_routes_and_bearer_auth(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv(control.AGENT_HOME_ENV, str(tmp_path / "agent-home"))
    monkeypatch.setattr(
        control,
        "_safe_settings",
        lambda: SimpleNamespace(
            provider="chatgpt",
            active_model="",
            active_base_url=None,
            use_online_provider=True,
            mcp_enabled=False,
        ),
    )
    snapshot = control.ensure_server_started(port=0)
    try:
        assert snapshot["running"] is True
        port = snapshot["port"]
        token = control.load_token()
        url = f"http://127.0.0.1:{port}/v1/status"
        with pytest.raises(error.HTTPError) as denied:
            request.urlopen(url, timeout=2)
        assert denied.value.code == 401
        http_request = request.Request(
            url,
            headers={"Authorization": f"Bearer {token}"},
        )
        with request.urlopen(http_request, timeout=2) as response:
            payload = json.loads(response.read().decode("utf-8"))
        assert payload["ok"] is True
        assert payload["provider"] == "chatgpt"
        assert payload["assistant_available"] is True
    finally:
        control.shutdown_server(wait=True)


def test_cli_parses_freecadcmd_argv_and_prefers_http(monkeypatch) -> None:
    captured: dict[str, Any] = {}

    def fake_http(command, arguments, timeout_seconds=30.0):
        captured["command"] = command
        captured["arguments"] = arguments
        captured["timeout"] = timeout_seconds
        return {"ok": True, "via": "http", "command": command}

    monkeypatch.setattr(cli, "call_http", fake_http)
    args = cli.build_parser().parse_args(
        cli._argv_for_parser(
            [
                "FreeCADCmd.exe",
                "C:\\VibeCAD\\Mod\\VibeCAD\\VibeCADAgentCli.py",
                "open",
                "--path",
                "C:\\Models\\part.FCStd",
            ]
        )
    )
    payload = cli.execute(args)
    assert payload == {"ok": True, "via": "http", "command": "open"}
    assert captured["command"] == "open"
    assert captured["arguments"]["path"] == "C:\\Models\\part.FCStd"


def test_cli_gui_only_uses_exit_code_two(monkeypatch) -> None:
    monkeypatch.setattr(cli, "call_http", lambda *args, **kwargs: None)
    args = Namespace(
        command="status",
        local=False,
        gui_only=True,
        timeout=1.0,
        path=None,
        script=None,
        python=None,
        no_recompute=False,
    )
    payload = cli.execute(args)
    assert payload["failure_code"] == "GUI_NOT_RUNNING"
    monkeypatch.setattr(cli, "execute", lambda _args: payload)
    assert cli.main(["--gui-only", "status"]) == cli.EXIT_GUI_UNAVAILABLE


def test_mcp_mode_is_reported_without_being_enabled(monkeypatch) -> None:
    class _Settings:
        provider = "openai"
        active_model = "gpt-5.5"
        active_base_url = None
        use_online_provider = True
        mcp_enabled = True

    monkeypatch.setattr(control, "_safe_settings", lambda: _Settings())
    payload = control.report_status()
    assert payload["mcp_enabled"] is True
    assert payload["assistant_available"] is False


def test_existing_providers_remain_registered() -> None:
    assert set(auth.PROVIDERS) >= {"openai", "anthropic", "chatgpt", "grok"}
    assert control.DEFAULT_AGENT_PORT != 8765
    assert control.DEFAULT_AGENT_PORT == 8766
