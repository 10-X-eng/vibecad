# SPDX-License-Identifier: LGPL-2.1-or-later

"""Local loopback control channel for an external desktop agent.

This is additive and independent of MCP. Enabling it does not disable the
in-app VibeCAD Assistant, so Grok / ChatGPT / OpenAI / Anthropic can keep
driving the open document while a local agent performs guarded native-file
round trips, captures the visible window, activates semantic Qt targets without
controlling the physical cursor, runs authorized compatibility scripts, shows
Preferences, or reads auth status.

The server binds only to 127.0.0.1. Callers authenticate with a bearer token
that VibeCAD writes to a private file the agent can read; the agent never
types passwords or OAuth codes.
"""

from __future__ import annotations

from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timezone
import hashlib
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
from typing import Any, Callable
from urllib.parse import urlparse


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
    "save",
    "save_as",
    "close",
    "ui_ribbon",
    "ui_menus",
    "ui_click",
    "screenshot",
    "run",
    "preferences",
    "aero",
)
UPSTREAM_COMMANDS = frozenset(
    {"status", "documents", "open", "run", "preferences", "aero"}
)

_server_lock = threading.RLock()
_server: ThreadingHTTPServer | None = None
_server_thread: threading.Thread | None = None
_document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None
_document_operation_gate = threading.Lock()
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
channel. Use exact semantic VibeCAD targets without taking over the human's
physical cursor.

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
| POST | `/v1/save`        | optional `{{"document":"Name"}}` | Save an already-named document |
| POST | `/v1/save-as`     | `{{"path":"...","overwrite":false}}` | Save to an explicit .FCStd path |
| POST | `/v1/close`       | optional `{{"document":"Name","discard_unsaved":false}}` | Close without silently discarding changes |
| GET  | `/v1/ui/ribbon`   |                                   | Live semantic tab names and screen geometry |
| GET  | `/v1/ui/menus`    |                                   | Live top-level menu names and screen geometry |
| POST | `/v1/ui/click`    | `{{"kind":"ribbon","text":"Model"}}` | Activate a semantic Qt target without moving the physical cursor |
| GET/POST | `/v1/screenshot` | optional `{{"path":"...png","overwrite":false}}` | Capture the visible VibeCAD window |
| POST | `/v1/run`         | `{{"python":"..."}}` or `{{"script":"..."}}` (+ optional `path`, `recompute`) | Run against the active document |
| GET  | `/v1/aero`        |                                   | Flight card + AeroReport stamps |
| POST | `/v1/aero`        | `{{"operation":"analyze"}}` (also section, vlm, export_jsbsim, report, propose_repairs, apply_repairs, reject_repairs, flight_card) | Same Aero wrapper as in-app Grok |
| POST | `/v1/preferences` |                                   | Show VibeCAD Preferences |

Use `/v1/aero` for aerodynamics. Do not `exec` Analyze or `apply_repairs`
through `/v1/run`. `/v1/run` remains for non-Aero Python.

`run` executes Python in the VibeCAD process with `App`/`FreeCAD` (and
`Gui`/`FreeCADGui` when the GUI is up). Assign `result` or `__result__` to
return a JSON value. Stdout, stderr, and exceptions come back in the payload.

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
curl -s -H "Authorization: Bearer $TOKEN" -H 'Content-Type: application/json' \\
  -d '{{"python":"result = App.ActiveDocument and App.ActiveDocument.Name"}}' \\
  {base_url}/v1/run
```

## Rules

- Loopback only; do not expose this port off the machine.
- UI activation is in-process Qt only; never move, click, confine, hide, or
  block the human's physical cursor.
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
    """Preserve the original public dispatch behavior for existing callers."""

    dispatch = _document_thread_dispatch
    if dispatch is None:
        return operation()
    return dispatch(operation)


def _on_document_thread_fail_closed(
    operation: Callable[[], Any],
    *,
    allow_headless_direct: bool = False,
) -> Any:
    """Run one document operation without GUI-thread re-entry.

    The gate is acquired before a worker can enqueue work through Qt. This is
    intentionally non-reentrant: FreeCAD restore code pumps Qt events, so a
    second request must fail busy rather than enter a partially restored
    document. Direct execution is reserved for the explicitly requested local
    FreeCADCmd/headless adapter; the GUI HTTP server always supplies a document
    thread dispatcher.
    """

    if not _document_operation_gate.acquire(blocking=False):
        return failure(
            "DOCUMENT_OPERATION_BUSY",
            "Another VibeCAD document operation is still in progress; retry after it completes.",
            stage="precondition",
        )
    try:
        dispatch = _document_thread_dispatch
        if not callable(dispatch):
            if allow_headless_direct and _app_gui_up_state() is False:
                return _execute_document_operation(operation)
            return failure(
                "DOCUMENT_THREAD_UNAVAILABLE",
                "The VibeCAD GUI document-thread dispatcher is unavailable; no document state was accessed.",
                stage="precondition",
            )
        return dispatch(lambda: _execute_document_operation(operation))
    finally:
        _document_operation_gate.release()


def _execute_document_operation(operation: Callable[[], Any]) -> Any:
    """Fail before document access when FreeCAD is inside native restore."""

    try:
        restoring = getattr(_app(), "isRestoring")
    except Exception:
        restoring = None
    if not callable(restoring):
        return failure(
            "DOCUMENT_RESTORE_STATE_UNAVAILABLE",
            "VibeCAD cannot verify the native document-restore state; no document state was accessed.",
            stage="precondition",
        )
    try:
        if bool(restoring()):
            return failure(
                "DOCUMENT_RESTORE_IN_PROGRESS",
                "FreeCAD is restoring a document; retry after the native restore completes.",
                stage="precondition",
            )
    except Exception:
        return failure(
            "DOCUMENT_RESTORE_STATE_UNAVAILABLE",
            "VibeCAD cannot verify the native document-restore state; no document state was accessed.",
            stage="precondition",
        )
    return operation()


def _document_summary(document: Any) -> dict[str, Any]:
    return {
        "document": str(getattr(document, "Name", "") or ""),
        "label": str(getattr(document, "Label", "") or ""),
        "path": str(getattr(document, "FileName", "") or ""),
        "active": document is getattr(_app(), "ActiveDocument", None),
        "object_count": len(list(getattr(document, "Objects", []) or [])),
        "modified": _document_modified(document),
    }


def _gui_document(document: Any) -> Any | None:
    """Return the GUI document that owns persisted view-provider state."""

    gui = _gui()
    getter = getattr(gui, "getDocument", None) if gui is not None else None
    if not callable(getter):
        return None
    try:
        return getter(str(getattr(document, "Name", "") or ""))
    except Exception:
        return None


def _clear_gui_modified_after_verified_save(document: Any) -> bool:
    """Normalize App-level save behavior after its file postconditions pass.

    ``Document.save()`` and ``saveAs()`` persist ``GuiDocument.xml`` through
    FreeCAD's save observer, but unlike the native File -> Save command they do
    not clear ``GuiDocument.Modified``. This function is called only after the
    requested file and path association have been verified. Any later App or
    view-provider edit sets the native GUI flag again.
    """

    gui_document = _gui_document(document)
    if gui_document is None:
        return False
    try:
        gui_document.Modified = False
        return not bool(gui_document.Modified)
    except Exception:
        return False


def _app_gui_up_state() -> bool | None:
    """Return FreeCAD's App-level GUI authority, or None when it is unsafe."""

    try:
        value = getattr(_app(), "GuiUp")
    except Exception:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, int) and value in (0, 1):
        return bool(value)
    return None


def _document_modified(document: Any) -> bool:
    """Return FreeCAD's native persisted dirty state, failing closed.

    ``Document.isSaved()`` means only that a document has a file name in the
    supported FreeCAD builds. In a GUI process, the GUI document's ``Modified``
    flag is the authoritative guard because the native file also persists
    ``GuiDocument.xml`` and view-provider properties. The explicit
    ``FreeCADCmd``/headless adapter has no GUI document and the native App
    binding exposes no equivalent document-modified flag, so generic dirty
    queries fail closed there as well. A successful headless save is handled by
    the narrower verified-save postcondition below.
    """

    is_saved = getattr(document, "isSaved", None)
    if callable(is_saved):
        try:
            if not bool(is_saved()):
                return True
        except Exception:
            return True
    elif not str(getattr(document, "FileName", "") or "").strip():
        return True

    gui_document = _gui_document(document)
    if gui_document is not None:
        try:
            return bool(gui_document.Modified)
        except Exception:
            return True
    return True


def _verified_save_summary(document: Any) -> dict[str, Any]:
    """Summarize a save after its native call and file postconditions passed.

    The headless DocumentPy binding has no document-level ``Modified`` flag.
    Only this operation-scoped postcondition may report it clean, and only when
    App-level ``GuiUp`` is authoritatively false. Generic status and close
    queries remain fail-closed without a GUI document.
    """

    summary = _document_summary(document)
    if _app_gui_up_state() is False and _gui_document(document) is None:
        summary["modified"] = False
    return summary


def _partial_document_save_failure(document: Any) -> dict[str, Any] | None:
    """Reject saves that FreeCAD would acknowledge without writing a file."""

    try:
        partial = getattr(document, "Partial")
    except Exception:
        return failure(
            "DOCUMENT_PARTIAL_STATE_UNKNOWN",
            "VibeCAD could not verify whether the document is partially loaded; refusing to save it.",
            stage="precondition",
        )
    if not isinstance(partial, (bool, int)) or int(partial) not in (0, 1):
        return failure(
            "DOCUMENT_PARTIAL_STATE_UNKNOWN",
            "VibeCAD could not verify whether the document is partially loaded; refusing to save it.",
            stage="precondition",
        )
    if bool(partial):
        return failure(
            "DOCUMENT_PARTIAL",
            "FreeCAD cannot durably save a partially loaded document. Fully load it before saving.",
            stage="precondition",
        )
    return None


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


def _selected_document(name: str = "") -> tuple[Any | None, dict[str, Any] | None]:
    App = _app()
    requested = str(name or "").strip()
    if not requested:
        document = getattr(App, "ActiveDocument", None)
        if document is None:
            return None, failure(
                "DOCUMENT_REQUIRED",
                "No active document is available.",
            )
        return document, None

    listing = getattr(App, "listDocuments", None)
    documents = listing() if callable(listing) else {}
    document = documents.get(requested) if isinstance(documents, dict) else None
    if document is None:
        return None, failure(
            "DOCUMENT_NOT_OPEN",
            f"No open document is named {requested!r}.",
        )
    return document, None


def _resolve_save_path(raw: str) -> tuple[Path | None, dict[str, Any] | None]:
    text = str(raw or "").strip()
    if not text:
        return None, failure(
            "SAVE_PATH_REQUIRED",
            "Save As requires an explicit path.",
            stage="schema",
        )
    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None, failure(
            "SAVE_PATH_NOT_ABSOLUTE",
            "Save As path must be absolute.",
            stage="schema",
        )
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        return None, failure(
            "SAVE_PATH_INVALID",
            f"Save As path cannot be resolved: {exc}",
            stage="schema",
        )
    if candidate.suffix.lower() != ".fcstd":
        return None, failure(
            "SAVE_EXTENSION_UNSUPPORTED",
            "Agent-control Save As supports native .FCStd documents only.",
            stage="schema",
        )
    if not candidate.parent.is_dir():
        return None, failure(
            "SAVE_PARENT_NOT_FOUND",
            f"Save As parent directory does not exist: {candidate.parent}.",
        )
    return candidate, None


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


def save_document(name: str = "") -> dict[str, Any]:
    """Save an already-named document and verify the native file exists."""

    document, error = _selected_document(name)
    if error is not None:
        return error
    assert document is not None
    partial_failure = _partial_document_save_failure(document)
    if partial_failure is not None:
        return partial_failure
    raw_path = str(getattr(document, "FileName", "") or "").strip()
    if not raw_path:
        return failure(
            "SAVE_AS_REQUIRED",
            "The selected document has no file path; use POST /v1/save-as.",
        )
    path = Path(raw_path).expanduser()
    if not path.is_absolute():
        return failure(
            "SAVE_PATH_NOT_ABSOLUTE",
            "The selected document's file path is not absolute.",
        )
    path = path.resolve()
    saver = getattr(document, "save", None)
    if not callable(saver):
        return failure(
            "DOCUMENT_SAVE_UNAVAILABLE",
            "FreeCAD document save is unavailable in this process.",
            stage="native_call",
        )
    try:
        outcome = saver()
    except Exception as exc:
        return failure(
            "DOCUMENT_SAVE_FAILED",
            str(exc),
            stage="native_call",
        )
    if outcome is False or not path.is_file():
        return failure(
            "DOCUMENT_SAVE_FAILED",
            f"VibeCAD did not produce the expected document file at {path}.",
            stage="native_call",
        )
    _clear_gui_modified_after_verified_save(document)
    summary = _verified_save_summary(document)
    if summary["modified"]:
        return failure(
            "DOCUMENT_STILL_MODIFIED",
            "VibeCAD saved the file but the document still reports unsaved changes.",
            stage="postcondition",
            saved=summary,
        )
    return {
        "ok": True,
        "saved": summary,
        "file": {
            "path": str(path),
            "size": path.stat().st_size,
        },
    }


def save_document_as(
    path: str,
    *,
    name: str = "",
    overwrite: bool = False,
) -> dict[str, Any]:
    """Save a document to an explicit native path, protecting files by default."""

    allow_overwrite = overwrite is True
    candidate, error = _resolve_save_path(path)
    if error is not None:
        return error
    assert candidate is not None
    if candidate.exists() and not allow_overwrite:
        return failure(
            "SAVE_TARGET_EXISTS",
            f"Refusing to overwrite existing file {candidate}; pass overwrite=true explicitly.",
        )
    if candidate.exists() and not candidate.is_file():
        return failure(
            "SAVE_TARGET_INVALID",
            f"Save As target is not a file: {candidate}.",
        )
    document, error = _selected_document(name)
    if error is not None:
        return error
    assert document is not None
    partial_failure = _partial_document_save_failure(document)
    if partial_failure is not None:
        return partial_failure
    saver = getattr(document, "saveAs", None)
    if not callable(saver):
        return failure(
            "DOCUMENT_SAVE_AS_UNAVAILABLE",
            "FreeCAD document saveAs is unavailable in this process.",
            stage="native_call",
        )
    try:
        outcome = saver(str(candidate))
    except Exception as exc:
        return failure(
            "DOCUMENT_SAVE_AS_FAILED",
            str(exc),
            stage="native_call",
        )
    if outcome is False or not candidate.is_file():
        return failure(
            "DOCUMENT_SAVE_AS_FAILED",
            f"VibeCAD did not produce the expected document file at {candidate}.",
            stage="native_call",
        )
    try:
        actual = Path(str(getattr(document, "FileName", "") or "")).expanduser().resolve()
    except OSError:
        actual = None
    if actual != candidate:
        return failure(
            "DOCUMENT_SAVE_AS_PATH_MISMATCH",
            "VibeCAD saved a file but did not associate the document with the requested path.",
            stage="postcondition",
            expected_path=str(candidate),
            saved_as=_document_summary(document),
        )
    _clear_gui_modified_after_verified_save(document)
    summary = _verified_save_summary(document)
    if summary["modified"]:
        return failure(
            "DOCUMENT_STILL_MODIFIED",
            "VibeCAD saved the file but the document still reports unsaved changes.",
            stage="postcondition",
            saved_as=summary,
        )
    return {
        "ok": True,
        "overwrote": allow_overwrite,
        "saved_as": summary,
        "file": {
            "path": str(candidate),
            "size": candidate.stat().st_size,
        },
    }


def close_document(name: str = "", *, discard_unsaved: bool = False) -> dict[str, Any]:
    """Close a document, refusing to discard changes unless explicitly allowed."""

    allow_discard = discard_unsaved is True
    document, error = _selected_document(name)
    if error is not None:
        return error
    assert document is not None
    document_name = str(getattr(document, "Name", "") or "")
    if _document_modified(document) and not allow_discard:
        return failure(
            "DOCUMENT_MODIFIED",
            (
                f"Document {document_name!r} has unsaved changes; save it or "
                "pass discard_unsaved=true explicitly."
            ),
        )
    App = _app()
    closer = getattr(App, "closeDocument", None)
    if not callable(closer):
        return failure(
            "DOCUMENT_CLOSE_UNAVAILABLE",
            "FreeCAD closeDocument is unavailable in this process.",
            stage="native_call",
        )
    try:
        closer(document_name)
    except Exception as exc:
        return failure(
            "DOCUMENT_CLOSE_FAILED",
            str(exc),
            stage="native_call",
        )
    listing = getattr(App, "listDocuments", None)
    documents = listing() if callable(listing) else {}
    if isinstance(documents, dict) and document_name in documents:
        return failure(
            "DOCUMENT_CLOSE_FAILED",
            f"Document {document_name!r} is still open after closeDocument.",
            stage="postcondition",
        )
    return {
        "ok": True,
        "closed": document_name,
        "discarded_unsaved": allow_discard,
        "documents": _all_documents(),
    }


def ui_ribbon_snapshot() -> dict[str, Any]:
    """Return live, screen-global geometry for the human-visible ribbon tabs."""

    gui = _gui()
    if gui is None or not bool(getattr(gui, "GuiUp", True)):
        return failure(
            "GUI_REQUIRED",
            "Ribbon geometry requires the running VibeCAD GUI.",
        )
    try:
        from PySide import QtWidgets

        main_window = gui.getMainWindow()
        tabs = (
            main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
            if main_window is not None
            else None
        )
        if tabs is None:
            return failure(
                "RIBBON_TABS_UNAVAILABLE",
                "The VibeCADRibbonTabs semantic target is unavailable.",
            )
        selected_index = int(tabs.currentIndex())
        items: list[dict[str, Any]] = []
        for index in range(int(tabs.count())):
            rect = tabs.tabRect(index)
            top_left = tabs.mapToGlobal(rect.topLeft())
            center = tabs.mapToGlobal(rect.center())
            text = str(tabs.tabText(index) or "").replace("&", "").strip()
            items.append(
                {
                    "index": index,
                    "text": text,
                    "workbench": str(tabs.tabData(index) or "").strip(),
                    "enabled": bool(tabs.isTabEnabled(index)),
                    "selected": index == selected_index,
                    "screen_rect": {
                        "left": int(top_left.x()),
                        "top": int(top_left.y()),
                        "width": int(rect.width()),
                        "height": int(rect.height()),
                        "center_x": int(center.x()),
                        "center_y": int(center.y()),
                    },
                }
            )
        selected_text = next(
            (item["text"] for item in items if item["selected"]),
            "",
        )
        return {
            "ok": True,
            "process_id": os.getpid(),
            "window_handle": int(main_window.winId()),
            "object_name": str(tabs.objectName() or "VibeCADRibbonTabs"),
            "visible": bool(tabs.isVisible()),
            "window_title": str(main_window.windowTitle() or ""),
            "selected_index": selected_index,
            "selected_text": selected_text,
            "tabs": items,
        }
    except Exception as exc:
        return failure(
            "RIBBON_SNAPSHOT_FAILED",
            str(exc),
            stage="native_call",
        )


def ui_menu_snapshot() -> dict[str, Any]:
    """Return live, screen-global geometry for top-level application menus."""

    gui = _gui()
    if gui is None or not bool(getattr(gui, "GuiUp", True)):
        return failure(
            "GUI_REQUIRED",
            "Menu geometry requires the running VibeCAD GUI.",
        )
    try:
        main_window = gui.getMainWindow()
        menu_bar = main_window.menuBar() if main_window is not None else None
        if menu_bar is None:
            return failure(
                "MENU_BAR_UNAVAILABLE",
                "The VibeCAD top-level menu bar is unavailable.",
            )
        items: list[dict[str, Any]] = []
        for index, action in enumerate(menu_bar.actions()):
            rect = menu_bar.actionGeometry(action)
            top_left = menu_bar.mapToGlobal(rect.topLeft())
            center = menu_bar.mapToGlobal(rect.center())
            menu = action.menu()
            text = str(action.text() or "").replace("&", "").strip()
            items.append(
                {
                    "index": index,
                    "text": text,
                    "enabled": bool(action.isEnabled()),
                    "visible": bool(action.isVisible()),
                    "menu_visible": bool(menu is not None and menu.isVisible()),
                    "screen_rect": {
                        "left": int(top_left.x()),
                        "top": int(top_left.y()),
                        "width": int(rect.width()),
                        "height": int(rect.height()),
                        "center_x": int(center.x()),
                        "center_y": int(center.y()),
                    },
                }
            )
        return {
            "ok": True,
            "process_id": os.getpid(),
            "window_handle": int(main_window.winId()),
            "object_name": str(menu_bar.objectName() or "VibeCADMenuBar"),
            "visible": bool(menu_bar.isVisible()),
            "window_title": str(main_window.windowTitle() or ""),
            "menus": items,
        }
    except Exception as exc:
        return failure(
            "MENU_SNAPSHOT_FAILED",
            str(exc),
            stage="native_call",
        )


def _cursor_coordinates(QtGui: Any) -> dict[str, int]:
    point = QtGui.QCursor.pos()
    return {"x": int(point.x()), "y": int(point.y())}


def ui_click_target(
    kind: str,
    text: str,
    *,
    expected_process_id: Any = None,
    expected_index: Any = None,
) -> dict[str, Any]:
    """Activate a Qt target while leaving the user's OS cursor untouched."""

    target_kind = str(kind or "").strip().lower().replace("-", "_")
    if target_kind in {"tab", "ribbon_tab"}:
        target_kind = "ribbon"
    if target_kind not in {"ribbon", "menu"}:
        return failure(
            "UI_TARGET_KIND_INVALID",
            "kind must be 'ribbon' or 'menu'.",
            stage="schema",
        )
    target_text = str(text or "").strip()
    if not target_text:
        return failure(
            "UI_TARGET_TEXT_REQUIRED",
            "text must name one visible semantic UI target.",
            stage="schema",
        )
    try:
        required_pid = int(expected_process_id or 0)
    except (TypeError, ValueError):
        return failure(
            "UI_PROCESS_ID_INVALID",
            "expected_process_id must be an integer.",
            stage="schema",
        )
    if required_pid and required_pid != os.getpid():
        return failure(
            "UI_PROCESS_MISMATCH",
            f"Expected VibeCAD PID {required_pid}, but this GUI is PID {os.getpid()}.",
            stage="precondition",
        )
    try:
        required_index = None if expected_index is None else int(expected_index)
    except (TypeError, ValueError):
        return failure(
            "UI_TARGET_INDEX_INVALID",
            "expected_index must be an integer when provided.",
            stage="schema",
        )

    gui = _gui()
    if gui is None or not bool(getattr(gui, "GuiUp", True)):
        return failure(
            "GUI_REQUIRED",
            "UI clicking requires the running VibeCAD GUI.",
        )
    try:
        from PySide import QtCore, QtGui, QtWidgets

        try:
            from PySide import QtTest
        except ImportError:
            try:
                from PySide6 import QtTest
            except ImportError:
                from PySide2 import QtTest

        main_window = gui.getMainWindow()
        if main_window is None:
            return failure(
                "MAIN_WINDOW_UNAVAILABLE",
                "The VibeCAD main window is unavailable.",
            )
        cursor_before = _cursor_coordinates(QtGui)
        left_button = QtCore.Qt.LeftButton
        no_modifier = QtCore.Qt.NoModifier

        if target_kind == "ribbon":
            menu_bar = main_window.menuBar()
            if menu_bar is not None:
                for menu_action in menu_bar.actions():
                    open_menu = menu_action.menu()
                    if open_menu is not None and open_menu.isVisible():
                        open_menu.close()
                menu_bar.setActiveAction(None)
                QtWidgets.QApplication.processEvents()
            widget = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
            if widget is None or not bool(widget.isVisible()):
                return failure(
                    "RIBBON_TABS_UNAVAILABLE",
                    "The visible VibeCADRibbonTabs semantic target is unavailable.",
                )
            matches = [
                index
                for index in range(int(widget.count()))
                if str(widget.tabText(index) or "").replace("&", "").strip()
                == target_text
            ]
            if len(matches) != 1:
                return failure(
                    "UI_TARGET_NOT_UNIQUE",
                    f"Expected exactly one ribbon tab named {target_text!r}; found {len(matches)}.",
                    stage="precondition",
                )
            target_index = matches[0]
            if required_index is not None and required_index != target_index:
                return failure(
                    "UI_TARGET_INDEX_MISMATCH",
                    f"Ribbon tab {target_text!r} is index {target_index}, not {required_index}.",
                    stage="precondition",
                )
            if not bool(widget.isTabEnabled(target_index)):
                return failure(
                    "UI_TARGET_DISABLED",
                    f"Ribbon tab {target_text!r} is disabled.",
                    stage="precondition",
                )
            selected_before = str(
                widget.tabText(int(widget.currentIndex())) or ""
            ).replace("&", "").strip()
            click_point = widget.tabRect(target_index).center()
            QtTest.QTest.mouseClick(widget, left_button, no_modifier, click_point)
            QtWidgets.QApplication.processEvents()
            selected_after = str(
                widget.tabText(int(widget.currentIndex())) or ""
            ).replace("&", "").strip()
            verified = int(widget.currentIndex()) == target_index
            details: dict[str, Any] = {
                "target_kind": target_kind,
                "target_text": target_text,
                "target_index": target_index,
                "selected_before": selected_before,
                "selected_after": selected_after,
                "click_queued": False,
            }
        else:
            widget = main_window.menuBar()
            if widget is None or not bool(widget.isVisible()):
                return failure(
                    "MENU_BAR_UNAVAILABLE",
                    "The visible VibeCAD top-level menu bar is unavailable.",
                )
            actions = list(widget.actions())
            matches = [
                (index, action)
                for index, action in enumerate(actions)
                if str(action.text() or "").replace("&", "").strip()
                == target_text
            ]
            if len(matches) != 1:
                return failure(
                    "UI_TARGET_NOT_UNIQUE",
                    (
                        f"Expected exactly one top-level menu named {target_text!r}; "
                        f"found {len(matches)}."
                    ),
                    stage="precondition",
                )
            target_index, action = matches[0]
            if required_index is not None and required_index != target_index:
                return failure(
                    "UI_TARGET_INDEX_MISMATCH",
                    f"Menu {target_text!r} is index {target_index}, not {required_index}.",
                    stage="precondition",
                )
            if not bool(action.isEnabled()) or not bool(action.isVisible()):
                return failure(
                    "UI_TARGET_DISABLED",
                    f"Top-level menu {target_text!r} is disabled or hidden.",
                    stage="precondition",
                )
            target_menu = action.menu()
            if target_menu is None:
                return failure(
                    "UI_TARGET_HAS_NO_MENU",
                    f"Top-level action {target_text!r} has no menu.",
                    stage="precondition",
                )
            for candidate in actions:
                candidate_menu = candidate.menu()
                if candidate_menu is not None and candidate_menu.isVisible():
                    candidate_menu.close()
            QtWidgets.QApplication.processEvents()
            menu_visible_before = bool(target_menu.isVisible())
            action_rect = widget.actionGeometry(action)
            popup_point = widget.mapToGlobal(
                QtCore.QPoint(action_rect.left(), action_rect.bottom())
            )
            # Native Windows menu tracking can block the HTTP request when a
            # synthetic press/release opens a top-level popup. QMenu.popup is
            # the non-blocking Qt-native equivalent: the cyan virtual cursor
            # supplies the visible press state while the user's OS pointer is
            # sampled only for evidence and is never moved or clicked.
            widget.setActiveAction(action)
            target_menu.popup(popup_point)
            QtWidgets.QApplication.processEvents()
            menu_visible_after = bool(target_menu.isVisible())
            verified = menu_visible_after
            details = {
                "target_kind": target_kind,
                "target_text": target_text,
                "target_index": target_index,
                "menu_visible_before": menu_visible_before,
                "menu_visible": menu_visible_after,
                "click_queued": False,
            }

        cursor_after = _cursor_coordinates(QtGui)
        details.update(
            {
                "input_method": (
                    "qt_in_process_mouse_click"
                    if target_kind == "ribbon"
                    else "qt_in_process_menu_popup"
                ),
                "physical_cursor_control": "none",
                "physical_cursor_before": cursor_before,
                "physical_cursor_after": cursor_after,
                "physical_cursor_unchanged": cursor_before == cursor_after,
                "semantic_verified": bool(verified),
                "process_id": os.getpid(),
            }
        )
        if not verified and not bool(details.get("click_queued")):
            payload = failure(
                "UI_CLICK_NOT_APPLIED",
                f"Qt click did not activate {target_kind} target {target_text!r}.",
                stage="postcondition",
            )
            payload.update(details)
            return payload
        return {"ok": True, **details}
    except Exception as exc:
        return failure(
            "UI_CLICK_FAILED",
            str(exc),
            stage="native_call",
        )


def _resolve_screenshot_path(
    raw: str,
) -> tuple[Path | None, dict[str, Any] | None]:
    text = str(raw or "").strip()
    if not text:
        directory = agent_home() / "screenshots"
        try:
            directory.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            return None, failure(
                "SCREENSHOT_DIRECTORY_FAILED",
                f"Could not prepare the screenshot directory: {exc}",
                stage="filesystem",
            )
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")
        return (directory / f"vibecad-window-{timestamp}.png").resolve(), None

    candidate = Path(text).expanduser()
    if not candidate.is_absolute():
        return None, failure(
            "SCREENSHOT_PATH_NOT_ABSOLUTE",
            "An explicit screenshot path must be absolute.",
            stage="schema",
        )
    try:
        candidate = candidate.resolve()
    except OSError as exc:
        return None, failure(
            "SCREENSHOT_PATH_INVALID",
            f"Screenshot path cannot be resolved: {exc}",
            stage="schema",
        )
    if candidate.suffix.lower() != ".png":
        return None, failure(
            "SCREENSHOT_EXTENSION_UNSUPPORTED",
            "Agent-control screenshots use the .png format only.",
            stage="schema",
        )
    if not candidate.parent.is_dir():
        return None, failure(
            "SCREENSHOT_PARENT_NOT_FOUND",
            f"Screenshot parent directory does not exist: {candidate.parent}.",
        )
    return candidate, None


def capture_screenshot(
    path: str = "",
    *,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Capture the visible VibeCAD main window to a native PNG file."""

    allow_overwrite = overwrite is True
    candidate, error = _resolve_screenshot_path(path)
    if error is not None:
        return error
    assert candidate is not None
    if candidate.exists() and not allow_overwrite:
        return failure(
            "SCREENSHOT_TARGET_EXISTS",
            (
                f"Refusing to overwrite existing screenshot {candidate}; "
                "pass overwrite=true explicitly."
            ),
        )
    if candidate.exists() and not candidate.is_file():
        return failure(
            "SCREENSHOT_TARGET_INVALID",
            f"Screenshot target is not a file: {candidate}.",
        )

    gui = _gui()
    if gui is None or not bool(getattr(gui, "GuiUp", True)):
        return failure(
            "GUI_REQUIRED",
            "Screenshot capture requires the running VibeCAD GUI.",
        )
    try:
        main_window = gui.getMainWindow()
        if main_window is None:
            return failure(
                "MAIN_WINDOW_UNAVAILABLE",
                "The VibeCAD main window is unavailable.",
            )
        is_visible = getattr(main_window, "isVisible", None)
        if callable(is_visible) and not bool(is_visible()):
            return failure(
                "MAIN_WINDOW_NOT_VISIBLE",
                "The VibeCAD main window is not visible.",
                stage="precondition",
            )
        pixmap = main_window.grab()
        width = int(pixmap.width())
        height = int(pixmap.height())
        if width <= 0 or height <= 0:
            return failure(
                "SCREENSHOT_EMPTY",
                "VibeCAD returned an empty main-window image.",
                stage="postcondition",
            )
        if not bool(pixmap.save(str(candidate), "PNG")):
            return failure(
                "SCREENSHOT_SAVE_FAILED",
                f"Qt could not save the VibeCAD screenshot at {candidate}.",
                stage="native_call",
            )
    except Exception as exc:
        return failure(
            "SCREENSHOT_CAPTURE_FAILED",
            str(exc),
            stage="native_call",
        )

    if not candidate.is_file() or candidate.stat().st_size <= 0:
        return failure(
            "SCREENSHOT_SAVE_FAILED",
            f"The expected screenshot was not produced at {candidate}.",
            stage="postcondition",
        )
    digest = hashlib.sha256(candidate.read_bytes()).hexdigest()
    return {
        "ok": True,
        "capture": {
            "path": str(candidate),
            "size": candidate.stat().st_size,
            "sha256": digest,
            "width": width,
            "height": height,
            "window_title": str(main_window.windowTitle() or ""),
            "window_handle": int(main_window.winId()),
            "process_id": os.getpid(),
        },
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


def dispatch(
    command: str,
    arguments: dict[str, Any] | None = None,
    *,
    allow_headless_direct: bool = False,
    fail_closed: bool = False,
) -> dict[str, Any]:
    action = str(command or "").strip().lower()
    args = dict(arguments or {})
    if action not in COMMANDS:
        return failure(
            "COMMAND_UNKNOWN",
            f"Unknown command {command!r}; expected one of {list(COMMANDS)}.",
            stage="schema",
        )
    effective_fail_closed = bool(fail_closed or action not in UPSTREAM_COMMANDS)

    def on_document_thread(operation: Callable[[], Any]) -> Any:
        if not effective_fail_closed:
            return _on_document_thread(operation)
        return _on_document_thread_fail_closed(
            operation,
            allow_headless_direct=allow_headless_direct,
        )

    if action == "status":
        if not effective_fail_closed:
            return report_status()
        return on_document_thread(report_status)
    if action == "documents":
        return on_document_thread(list_documents)
    if action == "open":
        return on_document_thread(lambda: open_document(str(args.get("path") or "")))
    if action == "save":
        return on_document_thread(
            lambda: save_document(str(args.get("document") or ""))
        )
    if action == "save_as":
        return on_document_thread(
            lambda: save_document_as(
                str(args.get("path") or ""),
                name=str(args.get("document") or ""),
                overwrite=args.get("overwrite") is True,
            )
        )
    if action == "close":
        return on_document_thread(
            lambda: close_document(
                str(args.get("document") or ""),
                discard_unsaved=args.get("discard_unsaved") is True,
            )
        )
    if action == "ui_ribbon":
        return on_document_thread(ui_ribbon_snapshot)
    if action == "ui_menus":
        return on_document_thread(ui_menu_snapshot)
    if action == "ui_click":
        return on_document_thread(
            lambda: ui_click_target(
                str(args.get("kind") or ""),
                str(args.get("text") or ""),
                expected_process_id=args.get("expected_process_id"),
                expected_index=args.get("expected_index"),
            )
        )
    if action == "screenshot":
        return on_document_thread(
            lambda: capture_screenshot(
                str(args.get("path") or ""),
                overwrite=args.get("overwrite") is True,
            )
        )
    if action == "run":
        return on_document_thread(
            lambda: run_script(
                python=args.get("python"),
                script=args.get("script"),
                path=args.get("path"),
                recompute=bool(args.get("recompute", True)),
            )
        )
    if action == "aero":
        return on_document_thread(lambda: aero_command(args))
    return on_document_thread(show_preferences)


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
    *,
    fail_closed: bool = False,
) -> tuple[int, dict[str, Any]]:
    parsed = urlparse(path)
    route = parsed.path.rstrip("/") or "/"
    payload = dict(body or {})

    def routed_dispatch(
        command: str,
        arguments: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if fail_closed:
            return dispatch(command, arguments, fail_closed=True)
        if arguments is None:
            return dispatch(command)
        return dispatch(command, arguments)

    if method == "GET" and route in {"/v1/status", "/status"}:
        return 200, routed_dispatch("status")
    if method == "GET" and route in {"/v1/documents", "/documents"}:
        return 200, routed_dispatch("documents")
    if method == "POST" and route in {"/v1/open", "/open"}:
        return 200, routed_dispatch("open", payload)
    if method == "POST" and route in {"/v1/save", "/save"}:
        return 200, routed_dispatch("save", payload)
    if method == "POST" and route in {"/v1/save-as", "/save-as"}:
        return 200, routed_dispatch("save_as", payload)
    if method == "POST" and route in {"/v1/close", "/close"}:
        return 200, routed_dispatch("close", payload)
    if method == "GET" and route in {"/v1/ui/ribbon", "/ui/ribbon"}:
        return 200, routed_dispatch("ui_ribbon")
    if method == "GET" and route in {"/v1/ui/menus", "/ui/menus"}:
        return 200, routed_dispatch("ui_menus")
    if method == "POST" and route in {"/v1/ui/click", "/ui/click"}:
        return 200, routed_dispatch("ui_click", payload)
    if method == "GET" and route in {"/v1/screenshot", "/screenshot"}:
        return 200, routed_dispatch("screenshot")
    if method == "POST" and route in {"/v1/screenshot", "/screenshot"}:
        return 200, routed_dispatch("screenshot", payload)
    if method == "POST" and route in {"/v1/run", "/run"}:
        return 200, routed_dispatch("run", payload)
    if method == "GET" and route in {"/v1/aero", "/aero"}:
        return 200, routed_dispatch("aero", {"operation": "context"})
    if method == "POST" and route in {"/v1/aero", "/aero"}:
        return 200, routed_dispatch("aero", payload)
    if method == "POST" and route in {"/v1/preferences", "/preferences"}:
        return 200, routed_dispatch("preferences")
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
            status, payload = handle_http_request(
                method,
                self.path,
                body,
                fail_closed=bool(
                    getattr(self.server, "vibecad_fail_closed", False)
                ),
            )
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


def server_is_fail_closed() -> bool:
    """Report the additive server mode without changing the legacy snapshot."""

    with _server_lock:
        return bool(getattr(_server, "vibecad_fail_closed", False))


def _server_port_candidates(requested: int, *, explicit: bool) -> tuple[int, ...]:
    """Return the requested port and bounded automatic fallbacks."""

    if explicit:
        return (requested,)
    return tuple(range(requested, min(65535, requested + 9) + 1))


def _ensure_server_started(
    *,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None,
    host: str = AGENT_HOST,
    port: int | None = None,
    fail_closed: bool,
) -> dict[str, Any]:
    """Start one legacy or explicitly fail-closed loopback server."""

    global _server, _server_thread, _document_thread_dispatch, _bound_port
    with _server_lock:
        if _server is not None and _bound_port:
            running_fail_closed = bool(
                getattr(_server, "vibecad_fail_closed", False)
            )
            if fail_closed and not running_fail_closed:
                raise RuntimeError(
                    "VibeCAD agent control is already running in compatibility mode; "
                    "restart it before requesting fail-closed development control."
                )
            if document_thread_dispatch is not None:
                if (fail_closed or running_fail_closed) and not callable(
                    document_thread_dispatch
                ):
                    raise RuntimeError(
                        "VibeCAD agent control requires a callable document-thread dispatcher."
                    )
                if (
                    running_fail_closed
                    and not fail_closed
                    and document_thread_dispatch is not _document_thread_dispatch
                ):
                    raise RuntimeError(
                        "The compatibility server starter cannot replace the active "
                        "fail-closed document-thread dispatcher."
                    )
                _document_thread_dispatch = document_thread_dispatch
            return server_snapshot()
        if fail_closed and not callable(document_thread_dispatch):
            raise RuntimeError(
                "VibeCAD agent control requires the GUI document-thread dispatcher before startup."
            )
        if document_thread_dispatch is not None:
            _document_thread_dispatch = document_thread_dispatch
        load_or_create_token()
        requested = DEFAULT_AGENT_PORT if port is None else int(port)
        if port is None:
            requested = configured_port()
        last_error: Exception | None = None
        listener = None
        bound = requested
        candidates = _server_port_candidates(requested, explicit=port is not None)
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
            setattr(server, "vibecad_fail_closed", bool(fail_closed))
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


def ensure_server_started(
    *,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None = None,
    host: str = AGENT_HOST,
    port: int | None = None,
) -> dict[str, Any]:
    """Start the compatibility server with the original public defaults."""

    return _ensure_server_started(
        document_thread_dispatch=document_thread_dispatch,
        host=host,
        port=port,
        fail_closed=False,
    )


def ensure_fail_closed_server_started(
    *,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any] | None,
    host: str = AGENT_HOST,
    port: int | None = None,
) -> dict[str, Any]:
    """Start the opt-in development server with strict document serialization."""

    return _ensure_server_started(
        document_thread_dispatch=document_thread_dispatch,
        host=host,
        port=port,
        fail_closed=True,
    )


def shutdown_server(*, wait: bool = False) -> None:
    global _server, _server_thread, _document_thread_dispatch, _bound_port
    with _server_lock:
        server = _server
        thread = _server_thread
        was_fail_closed = bool(
            getattr(server, "vibecad_fail_closed", False)
        )
        _server = None
        _server_thread = None
        if was_fail_closed:
            _document_thread_dispatch = None
        _bound_port = None
    if server is not None:
        server.shutdown()
        server.server_close()
    if wait and thread is not None:
        thread.join(timeout=5.0)
