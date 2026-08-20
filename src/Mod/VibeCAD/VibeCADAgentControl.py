# SPDX-License-Identifier: LGPL-2.1-or-later

"""Local loopback control channel for an external desktop agent.

This is additive and independent of MCP. Enabling it does not disable the
in-app VibeCAD Assistant, so Grok / ChatGPT / OpenAI / Anthropic can keep
driving the open document while a local agent opens files, runs Python or
VibeScript, shows Preferences, or reads auth status.

The server binds only to 127.0.0.1. Callers authenticate with a bearer token
that VibeCAD writes to a private file the agent can read; the agent never
types passwords or OAuth codes.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import StringIO
import json
import os
from pathlib import Path
import secrets
import shutil
import socket
import sys
import threading
import traceback
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlparse


AGENT_HOST = "127.0.0.1"
DEFAULT_AGENT_PORT = 8766
AGENT_PORT_ENV = "VIBECAD_AGENT_PORT"
AGENT_HOME_ENV = "VIBECAD_AGENT_HOME"
TOKEN_FILENAME = "token"
ENDPOINT_FILENAME = "endpoint.json"
AGENT_BRIEF_FILENAME = "AGENTS.md"
GROK_BOT_CMD_ENV = "VIBECAD_GROK_BOT_CMD"
TOKEN_BYTES = 32
MAX_BODY_BYTES = 1_048_576
COMMANDS = (
    "status",
    "documents",
    "open",
    "run",
    "preferences",
    "aero",
    "context",
    "prompt",
    "native",
    "native_session",
    "screenshot",
)

_native_sessions: dict[str, Any] = {}
_native_sessions_lock = threading.RLock()
_server_lock = threading.RLock()
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None
_bound_port: int | None = None


def agent_home() -> Path:
    override = str(os.environ.get(AGENT_HOME_ENV) or "").strip()
    if override:
        return Path(override).expanduser().resolve()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or (Path.home() / "AppData/Local"))
        return root / "VibeCAD" / "Agent"
    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / "VibeCAD" / "Agent"
    root = Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local/share"))
    return root / "VibeCAD" / "agent"


def token_path() -> Path:
    return agent_home() / TOKEN_FILENAME


def endpoint_path() -> Path:
    return agent_home() / ENDPOINT_FILENAME


def _restrict_file(path: Path) -> None:
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


def _valid_token(value: Any) -> str:
    token = str(value or "").strip()
    if len(token) < 40:
        return ""
    # Keep the alphabet identical to the MCP token so agents can reuse parsers.
    allowed = set("ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_")
    if any(character not in allowed for character in token):
        return ""
    return token


def load_or_create_token() -> str:
    path = token_path()
    if path.is_file():
        existing = _valid_token(path.read_text(encoding="utf-8"))
        if existing:
            return existing
    path.parent.mkdir(parents=True, exist_ok=True)
    token = secrets.token_urlsafe(TOKEN_BYTES)
    path.write_text(token + "\n", encoding="utf-8")
    _restrict_file(path)
    return token


def load_token() -> str:
    path = token_path()
    if not path.is_file():
        return ""
    return _valid_token(path.read_text(encoding="utf-8"))


def write_endpoint(*, host: str, port: int) -> Path:
    path = endpoint_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "host": host,
        "port": int(port),
        "base_url": f"http://{host}:{int(port)}",
        "token_path": str(token_path()),
        "assistant_disabled_by_this_channel": False,
    }
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    _restrict_file(path)
    return path


def load_endpoint() -> dict[str, Any] | None:
    path = endpoint_path()
    if not path.is_file():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def brief_path() -> Path:
    return agent_home() / AGENT_BRIEF_FILENAME


_AGENT_BRIEF_TEMPLATE = """# VibeCAD control brief for Grok Bot

VibeCAD is running on this machine and exposes a local, loopback-only control
channel. Use it to drive VibeCAD without clicking menus.

## Connect

- Base URL: `{base_url}` (127.0.0.1 only)
- Auth: send header `Authorization: Bearer <token>`
- Token file: `{token_path}` (read the file contents; never prompt a human)
- Endpoint file (host/port/base_url/token_path): `{endpoint_path}`

## Routes (all require the bearer token)

| Method | Path | Body | Result |
| --- | --- | --- | --- |
| GET  | `/v1/status`      |                                   | Provider, auth (no secrets), documents, endpoint |
| GET  | `/v1/documents`   |                                   | Open documents |
| POST | `/v1/open`        | `{{"path":"..."}}`                | Open/activate a document |
| POST | `/v1/run`         | `{{"python":"..."}}` or `{{"script":"..."}}` (+ optional `path`, `recompute`) | Run against the active document |
| GET  | `/v1/context`     |                                   | Frozen ribbon catalog + native_state + presentation screenshot path |
| GET  | `/v1/screenshot`  | optional `?capture=true`          | Capture viewport PNG path. Pixels are not measurements. |
| POST | `/v1/prompt`      | `{{"text":"..."}}`                | Start an in-app Build turn (same path as in-app Grok) |
| POST | `/v1/native`      | `{{"capability":"...","arguments":{{...}},"session_id":"..."}}` | Same Native dispatcher; reuse session_id until `{{"close":true,"session_id":"..."}}` |
| GET  | `/v1/native/session` | optional `?session_id=...`     | Report the held Native session. Does not open a new turn. |
| GET  | `/v1/aero`        |                                   | Flight card + AeroReport stamps |
| POST | `/v1/aero`        | `{{"operation":"analyze"}}` (also section, vlm, export_jsbsim, report, propose_repairs, apply_repairs, reject_repairs, flight_card) | Same Aero wrapper as in-app Grok |
| POST | `/v1/preferences` |                                   | Show VibeCAD Preferences |

Use `/v1/native` to mutate or inspect CAD with the same Native dispatcher
as in-app Grok (receipts and claim ceilings included). The document must
be in Native modeling mode, not VibeScript, or you get
`NATIVE_AUTHORITY_CHANGED`. Use `/v1/aero` for aerodynamics. Use `/v1/context` and `/v1/prompt` to read facts or start a
chat turn. Do not `exec` CAD or Aero through `/v1/run`. `/v1/run` remains
for non-CAD Python.

`run` executes Python in the VibeCAD process with `App`/`FreeCAD` (and
`Gui`/`FreeCADGui` when the GUI is up). Assign `result` or `__result__` to
return a JSON value. Stdout, stderr, and exceptions come back in the payload.

CAD path for Grok Bot (same quality as in-app Grok):
1. GET `/v1/context` for the frozen ribbon (`provider_tool_surface.tool_names`,
   `provider_tool_schemas`), `native_state`, and `native_preview` (`stage` /
   `preview_id` for allowlisted families only). Do not invent capability names.
2. GET `/v1/screenshot` for a presentation-only PNG path. Open that file.
   Pixels never prove dimensions, CL, or airworthiness (`claim_ceiling=not_measured`).
3. POST `/v1/native` using a name from that freeze. Keep returning
   `session_id` on later calls so undo and the 256-call budget stay on one
   Native turn. GET `/v1/native/session` to inspect the held session without
   opening a new turn. Close with `{{"close":true,"session_id":"..."}}`.
4. POST `/v1/prompt` `{{"text":"..."}}` to start an in-app Build turn if you
   need the chat loop.
`model.extrude`, `model.revolve`, `model.helix`, `model.loft`,
`model.sweep`, dressup fillet/chamfer/thickness/draft, boolean
`cut`/`join`/`intersect`, transform `scale`, pattern
`linear`/`circular`/`mirror`, `model.hole`, and history
`delete_features` use `stage=propose` then `stage=apply` on `/v1/native`.
Suppress and sketch still live-commit.

Peak Aero loop for Grok Bot (same quality as in-app Grok):
1. GET `/v1/aero` for the stamped flight card.
2. POST `/v1/aero` `analyze` (does not move CAD).
3. GET `/v1/aero` again. Do not invent mass, CL, or airworthiness.
4. `propose_repairs` then `apply_repairs` only if the user wants CAD changes.
5. Appearance claims still need isometric + front + top screenshots. Pixels
   never prove aero numbers. Claim ceiling is always not_airworthy.

## Example

```bash
TOKEN="$(cat '{token_path}')"
curl -s -H "Authorization: Bearer $TOKEN" {base_url}/v1/status
curl -s -H "Authorization: Bearer $TOKEN" {base_url}/v1/context
curl -s -H "Authorization: Bearer $TOKEN" {base_url}/v1/screenshot
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{{"capability":"inspect.query","arguments":{{"operation":"validity"}}}}' \\
  {base_url}/v1/native
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{{"text":"fillet the selected edge"}}' \\
  {base_url}/v1/prompt
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{{"python":"result = App.ActiveDocument and App.ActiveDocument.Name"}}' \\
  {base_url}/v1/run
```

## Rules

- Loopback only; do not expose this port off the machine.
- Never type passwords or OAuth codes. Sign-in stays in VibeCAD Preferences.
- Do not enable MCP; it disables the in-app Assistant.
"""


def write_agent_brief(*, host: str = AGENT_HOST, port: int | None = None) -> Path:
    """Write an AGENTS.md brief telling a local agent how to drive VibeCAD.

    The brief is written next to the token/endpoint files so a desktop agent
    such as Grok Bot can read the connection details and the available routes.
    """

    resolved_port = int(port or _bound_port or configured_port())
    base_url = f"http://{host}:{resolved_port}"
    path = brief_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    content = _AGENT_BRIEF_TEMPLATE.format(
        base_url=base_url,
        token_path=str(token_path()),
        endpoint_path=str(endpoint_path()),
    )
    path.write_text(content, encoding="utf-8")
    _restrict_file(path)
    return path


def _resolve_command(candidate: str) -> str | None:
    if not candidate or not candidate.strip():
        return None
    candidate = candidate.strip()
    direct = Path(candidate).expanduser()
    if direct.is_file():
        return str(direct)
    found = shutil.which(candidate)
    if found:
        return found
    return None


def _default_grok_bot_candidates() -> list[str]:
    """Well-known locations for the Grok Bot desktop app.

    Deliberately narrow: the Grok Bot desktop app is ``Grok Bot.exe`` under
    ``Program Files`` on Windows. We do not probe bare names like ``grok``
    because that resolves to the separate Grok Build CLI (``grok.exe``), which
    is a different tool and must not be launched here.
    """

    candidates: list[str] = []
    if sys.platform == "win32":
        program_files = os.environ.get("ProgramFiles", "") or r"C:\Program Files"
        candidates.append(program_files.rstrip("\\") + r"\Grok Bot\Grok Bot.exe")
        literal = r"C:\Program Files\Grok Bot\Grok Bot.exe"
        if literal not in candidates:
            candidates.append(literal)
    elif sys.platform == "darwin":
        candidates.append("/Applications/Grok Bot.app/Contents/MacOS/Grok Bot")
    else:
        candidates.extend(["grok-bot", "grokbot"])
    return candidates


def detect_grok_bot_command(explicit: str = "") -> str | None:
    """Resolve a runnable Grok Bot command, or None when none is found.

    Resolution order: an explicit path/command, the ``VIBECAD_GROK_BOT_CMD``
    environment variable, then common executable names and per-OS install
    locations. Returns an absolute path (or a name found on ``PATH``).
    """

    ordered: list[str] = []
    if explicit and explicit.strip():
        ordered.append(explicit.strip())
    env_cmd = os.environ.get(GROK_BOT_CMD_ENV, "").strip()
    if env_cmd:
        ordered.append(env_cmd)
    ordered.extend(_default_grok_bot_candidates())
    for candidate in ordered:
        resolved = _resolve_command(candidate)
        if resolved:
            return resolved
    return None


def failure(
    code: str,
    message: str,
    *,
    stage: str = "precondition",
    **extra: Any,
) -> dict[str, Any]:
    payload = {
        "ok": False,
        "failure_code": str(code),
        "failure_stage": str(stage),
        "error": str(message),
    }
    payload.update(extra)
    return payload


def _app() -> Any:
    import FreeCAD as App

    return App


def _gui() -> Any | None:
    try:
        import FreeCADGui as Gui
    except Exception:
        return None
    if Gui is None:
        return None
    if not bool(getattr(Gui, "showPreferencesByName", None)) and not bool(
        getattr(Gui, "getMainWindow", None)
    ):
        if not bool(getattr(Gui, "GuiUp", False)):
            return None
    return Gui


def _on_document_thread(operation: Callable[[], Any]) -> Any:
    dispatch = _document_thread_dispatch
    if dispatch is None:
        return operation()
    return dispatch(operation)


def _document_summary(document: Any) -> dict[str, Any]:
    return {
        "document": str(getattr(document, "Name", "") or ""),
        "label": str(getattr(document, "Label", "") or ""),
        "path": str(getattr(document, "FileName", "") or ""),
        "active": document is getattr(_app(), "ActiveDocument", None),
        "object_count": len(list(getattr(document, "Objects", []) or [])),
    }


def _all_documents() -> list[dict[str, Any]]:
    listing = getattr(_app(), "listDocuments", None)
    documents = listing() if callable(listing) else {}
    if not isinstance(documents, dict):
        return []
    return [
        _document_summary(document)
        for _name, document in sorted(documents.items(), key=lambda item: str(item[0]))
    ]


def _resolve_existing_path(raw: str, *, kind: str) -> tuple[Path | None, dict[str, Any] | None]:
    text = str(raw or "").strip()
    if not text:
        return None, failure(
            f"{kind}_PATH_REQUIRED",
            f"{kind.lower()} path is required.",
            stage="schema",
        )
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None, failure(
            f"{kind}_PATH_NOT_ABSOLUTE",
            f"{kind.lower()} path must be absolute.",
            stage="schema",
        )
    candidate = candidate.resolve()
    if not candidate.is_file():
        return None, failure(
            f"{kind}_NOT_FOUND",
            f"No file exists at {candidate}.",
        )
    return candidate, None


def _documents_at_path(path: Path) -> list[Any]:
    listing = getattr(_app(), "listDocuments", None)
    documents = listing() if callable(listing) else {}
    if not isinstance(documents, dict):
        return []
    matches = []
    for document in documents.values():
        raw = str(getattr(document, "FileName", "") or "").strip()
        if not raw:
            continue
        try:
            if Path(raw).expanduser().resolve() == path:
                matches.append(document)
        except OSError:
            continue
    return matches


def _safe_settings() -> Any | None:
    try:
        from VibeCADPreferences import load_settings

        return load_settings()
    except Exception:
        return None


def _auth_snapshot(provider: str) -> dict[str, Any]:
    try:
        from VibeCADAuth import resolve_auth_state

        state = resolve_auth_state(provider=provider)
    except Exception as exc:
        return {
            "status": "unavailable",
            "source": None,
            "message": str(exc),
            "can_call_provider": False,
        }
    return {
        "status": getattr(getattr(state, "status", None), "value", str(state.status)),
        "source": state.source,
        "message": state.message,
        "can_call_provider": bool(state.can_call_provider),
        "redacted_key": state.redacted_key,
    }


def _grok_account_snapshot() -> dict[str, Any]:
    try:
        from VibeCADGrokAuth import cached_account

        account = cached_account()
    except Exception as exc:
        return {"signed_in": False, "error": str(exc)}
    if not isinstance(account, dict):
        return {"signed_in": False}
    return {
        "signed_in": True,
        "email": str(account.get("email") or ""),
        "name": str(account.get("name") or ""),
        "type": "grok",
    }


def _aero_status_snapshot() -> dict[str, Any]:
    try:
        from VibeCADAeroContext import document_aero_summary
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    document = getattr(_app(), "ActiveDocument", None)
    return document_aero_summary(document)


def report_status() -> dict[str, Any]:
    settings = _safe_settings()
    provider = (
        str(getattr(settings, "provider", "") or "").strip().lower()
        if settings is not None
        else ""
    )
    mcp_enabled = bool(getattr(settings, "mcp_enabled", False)) if settings else False
    gui = _gui()
    endpoint = load_endpoint() or {}
    return {
        "ok": True,
        "channel": "vibecad-agent-control",
        "gui_up": bool(gui is not None and getattr(gui, "GuiUp", True)),
        "assistant_available": not mcp_enabled,
        "mcp_enabled": mcp_enabled,
        "provider": provider or None,
        "model": str(getattr(settings, "active_model", "") or "") or None,
        "base_url": getattr(settings, "active_base_url", None) if settings else None,
        "use_online_provider": (
            bool(settings.use_online_provider) if settings is not None else None
        ),
        "auth": _auth_snapshot(provider) if provider else None,
        "grok": _grok_account_snapshot(),
        "documents": _all_documents(),
        "endpoint": {
            "host": endpoint.get("host") or AGENT_HOST,
            "port": endpoint.get("port") or _bound_port or DEFAULT_AGENT_PORT,
            "base_url": endpoint.get("base_url")
            or f"http://{AGENT_HOST}:{_bound_port or DEFAULT_AGENT_PORT}",
            "token_path": str(token_path()),
        },
        "aero": _aero_status_snapshot(),
        "oauth_note": (
            "Grok uses real xAI OAuth at https://auth.x.ai. xAI does not publish "
            "a VibeCAD-specific OAuth app; VibeCAD reuses the official Grok CLI "
            "public client. Sign-in happens in Preferences, not through this API."
        ),
    }


def list_documents() -> dict[str, Any]:
    documents = _all_documents()
    return {
        "ok": True,
        "document_count": len(documents),
        "documents": documents,
    }


def open_document(path: str) -> dict[str, Any]:
    candidate, error = _resolve_existing_path(path, kind="DOCUMENT")
    if error is not None:
        return error
    assert candidate is not None
    App = _app()
    matching = _documents_at_path(candidate)
    if matching:
        document = matching[0]
        App.setActiveDocument(str(document.Name))
        return {
            "ok": True,
            "already_open": True,
            "opened": _document_summary(document),
        }
    opener = getattr(App, "openDocument", None)
    if not callable(opener):
        return failure(
            "DOCUMENT_OPEN_UNAVAILABLE",
            "FreeCAD openDocument is unavailable in this process.",
            stage="native_call",
        )
    document = opener(str(candidate))
    if document is None:
        return failure(
            "DOCUMENT_OPEN_FAILED",
            f"VibeCAD could not open {candidate}.",
            stage="native_call",
        )
    App.setActiveDocument(str(document.Name))
    return {
        "ok": True,
        "already_open": False,
        "opened": _document_summary(document),
    }


def _json_safe(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(key): _json_safe(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_safe(item) for item in value]
    return str(value)


def run_script(
    *,
    python: str | None = None,
    script: str | None = None,
    path: str | None = None,
    recompute: bool = True,
) -> dict[str, Any]:
    source = str(python or "")
    script_path: Path | None = None
    if script:
        script_path, error = _resolve_existing_path(script, kind="SCRIPT")
        if error is not None:
            return error
        assert script_path is not None
        source = script_path.read_text(encoding="utf-8")
    if not source.strip():
        return failure(
            "SCRIPT_REQUIRED",
            "Pass python source or an absolute script path.",
            stage="schema",
        )
    lowered = source.replace(" ", "")
    if "apply_repairs(" in lowered or "repair=True" in lowered:
        return failure(
            "AERO_USE_V1_AERO",
            "Aero CAD changes go through POST /v1/aero, not /v1/run exec.",
            stage="schema",
        )
    opened = None
    if path:
        opened = open_document(path)
        if not opened.get("ok"):
            return opened
    App = _app()
    namespace: dict[str, Any] = {
        "__name__": "__vibecad_agent__",
        "__file__": str(script_path) if script_path is not None else "<agent>",
        "App": App,
        "FreeCAD": App,
    }
    gui = _gui()
    if gui is not None:
        namespace["Gui"] = gui
        namespace["FreeCADGui"] = gui
    stdout = StringIO()
    stderr = StringIO()
    try:
        compiled = compile(source, namespace["__file__"], "exec")
        with redirect_stdout(stdout), redirect_stderr(stderr):
            exec(compiled, namespace, namespace)
        if recompute:
            document = getattr(App, "ActiveDocument", None)
            recompute_call = getattr(document, "recompute", None)
            if callable(recompute_call):
                recompute_call()
    except Exception as exc:
        return failure(
            "SCRIPT_FAILED",
            str(exc),
            stage="script",
            stdout=stdout.getvalue(),
            stderr=stderr.getvalue() + traceback.format_exc(),
            opened=opened,
        )
    result = namespace.get("result", namespace.get("__result__"))
    return {
        "ok": True,
        "stdout": stdout.getvalue(),
        "stderr": stderr.getvalue(),
        "result": _json_safe(result),
        "opened": opened,
        "active_document": (
            _document_summary(App.ActiveDocument)
            if getattr(App, "ActiveDocument", None) is not None
            else None
        ),
    }


def aero_command(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Same Aero wrapper the in-app Grok Native tools use."""

    args = dict(arguments or {})
    operation = str(args.get("operation") or "context").strip() or "context"
    try:
        import VibeCADAero
        from VibeCADAeroContext import document_aero_summary
    except Exception as exc:
        return failure("AERO_UNAVAILABLE", str(exc), stage="precondition")
    document = getattr(_app(), "ActiveDocument", None)
    if operation == "context":
        card = VibeCADAero.flight_card(document) if document is not None else {"ok": False}
        return {
            "ok": True,
            "aero": document_aero_summary(document),
            "flight_card": card if card.get("ok") else card,
        }
    runners = {
        "analyze": lambda: VibeCADAero.run_analyze(document, repair=False),
        "section": lambda: VibeCADAero.run_section(document),
        "vlm": lambda: VibeCADAero.run_vlm(document),
        "export_jsbsim": lambda: VibeCADAero.export_jsbsim(document),
        "report": lambda: VibeCADAero.write_last_report(document),
        "propose_repairs": lambda: VibeCADAero.propose_repairs(document),
        "apply_repairs": lambda: VibeCADAero.apply_repairs(document),
        "reject_repairs": lambda: VibeCADAero.reject_repairs(document),
        "flight_card": lambda: VibeCADAero.flight_card(document),
    }
    runner = runners.get(operation)
    if runner is None:
        return failure(
            "AERO_OPERATION_UNKNOWN",
            f"Unknown Aero operation {operation!r}.",
            stage="schema",
        )
    return runner()


_BOT_TURN_KEYS = (
    "workbench",
    "modeling_surface",
    "native_state",
    "native_preview",
    "document",
    "selection",
    "view_screenshot",
    "reference_images",
    "aero",
    "intent",
    "provider_tool_surface",
    "provider_tool_schemas",
    "editable_sources",
)


def _bot_turn_packet(captured: Mapping[str, Any]) -> dict[str, Any]:
    """Same freeze packet in-app Grok gets, without private _vibecad_* keys."""

    packet: dict[str, Any] = {}
    for key in _BOT_TURN_KEYS:
        if key not in captured:
            continue
        value = captured[key]
        if key == "intent":
            packet[key] = value if isinstance(value, list) else []
            continue
        if value not in (None, "", [], {}):
            packet[key] = value
    if packet.get("native_state") or packet.get("provider_tool_schemas"):
        from VibeCADNativeState import native_preview_catalog

        packet["native_preview"] = native_preview_catalog()
    screenshot = packet.get("view_screenshot")
    attachment = _presentation_attachment(screenshot if isinstance(screenshot, Mapping) else {})
    if attachment is not None:
        packet["attachments"] = [attachment]
        if isinstance(screenshot, dict) and not screenshot.get("path"):
            screenshot = dict(screenshot)
            screenshot["path"] = attachment["path"]
            screenshot["presentation_only"] = True
            screenshot["claim_ceiling"] = "not_measured"
            packet["view_screenshot"] = screenshot
    return packet


def context_command() -> dict[str, Any]:
    """Same frozen ribbon catalog and native_state as in-app Grok."""

    if _gui() is None:
        return failure(
            "GUI_REQUIRED",
            "Reading CAD context requires the running VibeCAD GUI. "
            "Start VibeCAD.exe and call GET /v1/context from Grok Bot.",
            stage="precondition",
        )
    try:
        from VibeCADCore import get_service
        from VibeCADSession import _capture_context_for_provider

        captured = _capture_context_for_provider(get_service())
    except Exception as exc:
        return failure("CONTEXT_UNAVAILABLE", str(exc), stage="precondition")
    return {"ok": True, "context": _json_safe(_bot_turn_packet(captured))}


def _presentation_attachment(screenshot: Mapping[str, Any]) -> dict[str, Any] | None:
    if not screenshot.get("captured"):
        return None
    path = str(screenshot.get("path") or "")
    artifact = screenshot.get("artifact")
    if not path and isinstance(artifact, Mapping):
        path = str(artifact.get("path") or "")
    inner = screenshot.get("_vibecad_image_attachment")
    if not path and isinstance(inner, Mapping):
        path = str(inner.get("path") or "")
    if not path:
        return None
    return {
        "path": path,
        "mime_type": "image/png",
        "presentation_only": True,
        "artifact_class": "presentation",
        "claim_ceiling": "not_measured",
    }


def screenshot_command(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Capture or return the last viewport PNG. Pixels are not measurements."""

    args = dict(arguments or {})
    capture = bool(args.get("capture", True))
    if _gui() is None:
        return failure(
            "GUI_REQUIRED",
            "Viewport screenshots require the running VibeCAD GUI.",
            stage="precondition",
        )
    try:
        from VibeCADCore import get_service

        service = get_service()
        if capture and callable(getattr(service, "capture_view_screenshot", None)):
            captured = service.capture_view_screenshot()
            if isinstance(captured, dict) and captured.get("ok") is False:
                return failure(
                    "SCREENSHOT_FAILED",
                    str(captured.get("error") or "Viewport capture failed."),
                    stage="native_call",
                )
        summary = service.view_screenshot_summary()
        attachment = _presentation_attachment(summary if isinstance(summary, Mapping) else {})
        consume = getattr(service, "consume_view_screenshot_attachment", None)
        if attachment is not None and callable(consume):
            consume({"captured": True, "path": attachment["path"]})
    except Exception as exc:
        return failure("SCREENSHOT_UNAVAILABLE", str(exc), stage="precondition")
    if attachment is None:
        return failure(
            "SCREENSHOT_MISSING",
            "No viewport screenshot is available. Retry GET /v1/screenshot.",
            stage="precondition",
        )
    return {
        "ok": True,
        "screenshot": {
            "captured": True,
            "path": attachment["path"],
            "presentation_only": True,
            "artifact_class": "presentation",
            "claim_ceiling": "not_measured",
        },
        "attachment": attachment,
    }


def prompt_command(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Start an in-app Grok Build turn with the same path as the Assistant."""

    text = str((arguments or {}).get("text") or "").strip()
    if not text:
        return failure(
            "PROMPT_TEXT_REQUIRED",
            'Pass JSON {"text":"..."} to start an in-app Build turn.',
            stage="schema",
        )
    if _gui() is None:
        return failure(
            "GUI_REQUIRED",
            "Starting an in-app Build turn requires the running VibeCAD GUI. "
            "Start VibeCAD.exe and call POST /v1/prompt from Grok Bot.",
            stage="precondition",
        )
    try:
        import VibeCADGui

        starter = getattr(VibeCADGui, "start_assistant_turn", None)
        if not callable(starter):
            starter = getattr(VibeCADGui, "start_aero_designer_turn", None)
        started = bool(starter(text)) if callable(starter) else False
    except Exception as exc:
        return failure("PROMPT_UNAVAILABLE", str(exc), stage="precondition")
    if not started:
        return failure(
            "PROMPT_NOT_STARTED",
            "VibeCAD could not start an in-app Build turn. Open the Assistant "
            "panel and save the active document first.",
            stage="precondition",
        )
    return {"ok": True, "started": True}


def _summarize_native_session(session_id: str, execution: Any) -> dict[str, Any]:
    dispatcher = getattr(execution, "dispatcher", None)
    pending: list[Any] = []
    try:
        lister = getattr(dispatcher, "pending_previews", None)
        if callable(lister):
            pending = list(lister() or [])
    except Exception:
        pending = []
    summary: dict[str, Any] = {
        "ok": True,
        "held": True,
        "session_id": session_id,
        "run_id": str(getattr(execution, "run_id", "") or ""),
        "call_count": int(getattr(dispatcher, "call_count", 0) or 0),
        "pending_previews": pending,
    }
    turn = getattr(execution, "turn", None)
    turn_summary = getattr(turn, "summary", None)
    if callable(turn_summary):
        try:
            summary["turn"] = dict(turn_summary())
        except Exception:
            pass
    return summary


def native_session_command(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Report held Native sessions. Does not create a turn."""

    args = dict(arguments or {})
    wanted = str(args.get("session_id") or "").strip()
    with _native_sessions_lock:
        held = dict(_native_sessions)
    if wanted:
        execution = held.get(wanted)
        if execution is None:
            return failure(
                "NATIVE_SESSION_MISSING",
                f"No held Native session {wanted}.",
                stage="precondition",
            )
        return _summarize_native_session(wanted, execution)
    sessions = [
        _summarize_native_session(session_id, execution)
        for session_id, execution in held.items()
    ]
    if not sessions:
        return {"ok": True, "held": False, "sessions": []}
    payload = dict(sessions[0]) if len(sessions) == 1 else {"ok": True, "held": True}
    payload["sessions"] = sessions
    return payload


def _close_native_session(session_id: str) -> bool:
    with _native_sessions_lock:
        execution = _native_sessions.pop(session_id, None)
    if execution is None:
        return False
    closer = getattr(execution, "close", None)
    if callable(closer):
        try:
            closer()
        except Exception:
            pass
    return True


def native_command(arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    """Run one Native capability through the same dispatcher as in-app Grok."""

    args = dict(arguments or {})
    session_id = str(args.get("session_id") or "").strip()
    if args.get("close"):
        if not session_id:
            return failure(
                "NATIVE_SESSION_REQUIRED",
                'Pass JSON {"close":true,"session_id":"..."}.',
                stage="schema",
            )
        closed = _close_native_session(session_id)
        return {"ok": True, "closed": closed, "session_id": session_id}
    tool = str(args.get("capability") or args.get("tool") or "").strip()
    if not tool:
        return failure(
            "NATIVE_TOOL_REQUIRED",
            'Pass JSON {"capability":"inspect.query","arguments":{...}}.',
            stage="schema",
        )
    extra = args.get("arguments")
    if extra is None:
        extra = {}
    if not isinstance(extra, dict):
        return failure(
            "NATIVE_ARGUMENTS_INVALID",
            "Native arguments must be a JSON object.",
            stage="schema",
        )
    payload_args = dict(extra)
    operation = args.get("operation")
    if operation not in (None, "") and "operation" not in payload_args:
        payload_args["operation"] = operation
    if _gui() is None:
        return failure(
            "GUI_REQUIRED",
            "POST /v1/native requires the running VibeCAD GUI.",
            stage="precondition",
        )
    try:
        from VibeCADCore import get_service
        from VibeCADNativeDispatch import NativeDispatchError
        from VibeCADNativeSessionFactory import create_live_native_session_execution
    except Exception as exc:
        return failure("NATIVE_UNAVAILABLE", str(exc), stage="precondition")
    execution = None
    created = False
    if session_id:
        with _native_sessions_lock:
            execution = _native_sessions.get(session_id)
        if execution is None:
            return failure(
                "NATIVE_SESSION_MISSING",
                f"No held Native session {session_id}.",
                stage="precondition",
            )
    else:
        try:
            service = get_service()
            execution = create_live_native_session_execution(
                service=service,
                document_thread_dispatch=_on_document_thread,
            )
        except NativeDispatchError as exc:
            code = str(getattr(exc, "code", "") or "NATIVE_DISPATCH")
            return failure(code, str(exc), stage="precondition")
        except Exception as exc:
            return failure("NATIVE_UNAVAILABLE", str(exc), stage="precondition")
        session_id = secrets.token_hex(16)
        created = True
        with _native_sessions_lock:
            _native_sessions[session_id] = execution
    encoded = json.dumps(payload_args, ensure_ascii=True, separators=(",", ":"))
    call_id = str(args.get("call_id") or secrets.token_hex(16))
    try:
        result = execution.dispatcher.call(tool, encoded, call_id)
    except NativeDispatchError as exc:
        if created:
            _close_native_session(session_id)
        code = str(getattr(exc, "code", "") or "NATIVE_DISPATCH")
        return failure(code, str(exc), stage="precondition")
    except Exception as exc:
        if created:
            _close_native_session(session_id)
        return failure("NATIVE_UNAVAILABLE", str(exc), stage="precondition")
    if not isinstance(result, dict):
        if created:
            _close_native_session(session_id)
        return failure(
            "NATIVE_RESULT_INVALID",
            "Native dispatcher returned a non-object result.",
            stage="internal",
        )
    held = dict(result)
    held["session_id"] = session_id
    return held


def show_preferences() -> dict[str, Any]:
    gui = _gui()
    show = getattr(gui, "showPreferencesByName", None) if gui is not None else None
    if not callable(show):
        return failure(
            "GUI_REQUIRED",
            "Showing Preferences requires the running VibeCAD GUI. "
            "Start VibeCAD.exe and call the loopback API, or open "
            "Edit → Preferences → VibeCAD yourself.",
            stage="precondition",
        )
    show("VibeCAD", "VibeCAD")
    return {"ok": True, "opened": "VibeCAD"}


def dispatch(command: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    action = str(command or "").strip().lower()
    args = dict(arguments or {})
    if action not in COMMANDS:
        return failure(
            "COMMAND_UNKNOWN",
            f"Unknown command {command!r}; expected one of {list(COMMANDS)}.",
            stage="schema",
        )
    if action == "status":
        return report_status()
    if action == "documents":
        return _on_document_thread(list_documents)
    if action == "open":
        return _on_document_thread(lambda: open_document(str(args.get("path") or "")))
    if action == "run":
        return _on_document_thread(
            lambda: run_script(
                python=args.get("python"),
                script=args.get("script"),
                path=args.get("path"),
                recompute=bool(args.get("recompute", True)),
            )
        )
    if action == "aero":
        return _on_document_thread(lambda: aero_command(args))
    if action == "context":
        return _on_document_thread(context_command)
    if action == "prompt":
        return _on_document_thread(lambda: prompt_command(args))
    if action == "native":
        return _on_document_thread(lambda: native_command(args))
    if action == "native_session":
        return _on_document_thread(lambda: native_session_command(args))
    if action == "screenshot":
        return _on_document_thread(lambda: screenshot_command(args))
    return _on_document_thread(show_preferences)


def configured_port() -> int:
    raw = str(os.environ.get(AGENT_PORT_ENV) or "").strip()
    if raw:
        try:
            port = int(raw)
        except ValueError as exc:
            raise RuntimeError(
                f"{AGENT_PORT_ENV} must be an integer port, not {raw!r}."
            ) from exc
        if not 1 <= port <= 65535:
            raise RuntimeError(f"{AGENT_PORT_ENV} is out of range: {port}.")
        return port
    return DEFAULT_AGENT_PORT


def _bind_listener(host: str, port: int) -> socket.socket:
    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        if os.name == "nt" and hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind((host, int(port)))
        listener.listen(64)
        listener.set_inheritable(False)
    except OSError:
        listener.close()
        raise
    return listener


def _authorize(handler: BaseHTTPRequestHandler) -> bool:
    expected = load_or_create_token()
    header = str(handler.headers.get("Authorization") or "")
    prefix = "Bearer "
    offered = header[len(prefix) :] if header.startswith(prefix) else ""
    return secrets.compare_digest(_valid_token(offered), expected)


def handle_http_request(
    method: str,
    path: str,
    body: dict[str, Any] | None = None,
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    payload = dict(body or {})
    if method == "GET" and route in {"/v1/status", "/status"}:
        return 200, dispatch("status")
    if method == "GET" and route in {"/v1/documents", "/documents"}:
        return 200, dispatch("documents")
    if method == "POST" and route in {"/v1/open", "/open"}:
        return 200, dispatch("open", payload)
    if method == "POST" and route in {"/v1/run", "/run"}:
        return 200, dispatch("run", payload)
    if method == "GET" and route in {"/v1/context", "/context"}:
        return 200, dispatch("context")
    if method == "GET" and route in {"/v1/screenshot", "/screenshot"}:
        query = parse_qs(parsed.query)
        capture_raw = str((query.get("capture") or ["true"])[0] or "true").strip().lower()
        capture = capture_raw not in {"0", "false", "no"}
        return 200, dispatch("screenshot", {"capture": capture})
    if method == "POST" and route in {"/v1/screenshot", "/screenshot"}:
        return 200, dispatch("screenshot", payload)
    if method == "POST" and route in {"/v1/prompt", "/prompt"}:
        return 200, dispatch("prompt", payload)
    if method == "GET" and route in {"/v1/native/session", "/native/session"}:
        query = parse_qs(parsed.query)
        session_id = str((query.get("session_id") or [""])[0] or "").strip()
        return 200, dispatch("native_session", {"session_id": session_id})
    if method == "POST" and route in {"/v1/native", "/native"}:
        return 200, dispatch("native", payload)
    if method == "GET" and route in {"/v1/aero", "/aero"}:
        return 200, dispatch("aero", {"operation": "context"})
    if method == "POST" and route in {"/v1/aero", "/aero"}:
        return 200, dispatch("aero", payload)
    if method == "POST" and route in {"/v1/preferences", "/preferences"}:
        return 200, dispatch("preferences")
    return 404, failure(
        "ROUTE_UNKNOWN",
        f"Unsupported {method} {route}.",
        stage="schema",
    )


class _AgentRequestHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: Any) -> None:  # noqa: A003
        return None

    def _client_is_loopback(self) -> bool:
        host = str(self.client_address[0] if self.client_address else "")
        return host in {"127.0.0.1", "::1", "localhost"}

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        raw = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(raw)

    def _read_json_body(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        if length > MAX_BODY_BYTES:
            raise ValueError("Request body is too large.")
        raw = self.rfile.read(length)
        if not raw:
            return {}
        payload = json.loads(raw.decode("utf-8"))
        if not isinstance(payload, dict):
            raise ValueError("JSON body must be an object.")
        return payload

    def _handle(self, method: str) -> None:
        if not self._client_is_loopback():
            self._write_json(
                403,
                failure("LOOPBACK_ONLY", "Agent control accepts only 127.0.0.1."),
            )
            return
        if not _authorize(self):
            self._write_json(
                401,
                failure(
                    "UNAUTHORIZED",
                    "Pass Authorization: Bearer <token> using the token file "
                    f"at {token_path()}.",
                    stage="auth",
                    token_path=str(token_path()),
                ),
            )
            return
        try:
            body = self._read_json_body() if method == "POST" else {}
            status, payload = handle_http_request(method, self.path, body)
        except ValueError as exc:
            self._write_json(400, failure("REQUEST_INVALID", str(exc), stage="schema"))
            return
        except Exception as exc:
            self._write_json(
                500,
                failure("INTERNAL_ERROR", str(exc), stage="native_call"),
            )
            return
        self._write_json(status, payload)

    def do_GET(self) -> None:  # noqa: N802
        self._handle("GET")

    def do_POST(self) -> None:  # noqa: N802
        self._handle("POST")


def server_snapshot() -> dict[str, Any]:
    with _server_lock:
        return {
            "running": _server is not None,
            "host": AGENT_HOST,
            "port": _bound_port,
            "base_url": (
                f"http://{AGENT_HOST}:{_bound_port}" if _bound_port else None
            ),
            "token_path": str(token_path()),
        }


def ensure_server_started(
    *,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None,
    host: str = AGENT_HOST,
    port: int | None = None,
) -> dict[str, Any]:
    """Start the loopback server once. Safe to call from GUI startup."""

    global _server, _server_thread, _document_thread_dispatch, _bound_port
    with _server_lock:
        if document_thread_dispatch is not None:
            _document_thread_dispatch = document_thread_dispatch
        if _server is not None and _bound_port:
            return server_snapshot()
        load_or_create_token()
        requested = DEFAULT_AGENT_PORT if port is None else int(port)
        if port is None:
            requested = configured_port()
        last_error: Exception | None = None
        listener = None
        bound = requested
        candidates = (
            [requested] if port is not None else list(range(requested, requested + 10))
        )
        for candidate in candidates:
            try:
                listener = _bind_listener(host, candidate)
                bound = int(listener.getsockname()[1])
                last_error = None
                break
            except OSError as exc:
                last_error = exc
                listener = None
        if listener is None:
            raise RuntimeError(
                f"VibeCAD agent control could not bind {host}:{requested}: {last_error}"
            )
        try:
            server = ThreadingHTTPServer((host, bound), _AgentRequestHandler, False)
            try:
                server.socket.close()
            except OSError:
                pass
            server.socket = listener
            server.server_bind = lambda: None  # type: ignore[method-assign]
            server.server_activate()
        except Exception:
            listener.close()
            raise
        _server = server
        _bound_port = bound
        write_endpoint(host=host, port=bound)
        thread = threading.Thread(
            target=server.serve_forever,
            name="VibeCAD-AgentControl",
            daemon=True,
        )
        _server_thread = thread
        thread.start()
        return server_snapshot()


def shutdown_server(*, wait: bool = False) -> None:
    global _server, _server_thread, _bound_port
    with _server_lock:
        server = _server
        thread = _server_thread
        _server = None
        _server_thread = None
        _bound_port = None
    if server is not None:
        server.shutdown()
        server.server_close()
    if wait and thread is not None:
        thread.join(timeout=5.0)
