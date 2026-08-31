# SPDX-License-Identifier: LGPL-2.1-or-later

"""Agent-facing CLI for the VibeCAD local control channel.

Works two ways:

1. As a plain Python HTTP client against a running VibeCAD GUI
   (``http://127.0.0.1:8766``). No FreeCAD bindings required.
2. In-process through ``FreeCADCmd.exe`` / ``VibeCADCmd.exe`` when the GUI is
   not running (headless open / save / close / run / status). Semantic UI
   activation, screenshots, and Preferences still need the GUI.

The CLI reads the bearer token from the private Agent token file. Do not type
passwords or OAuth codes.
"""

from __future__ import annotations

import argparse
import errno
from http.client import HTTPException
import json
import os
import sys
import time
from typing import Any
import uuid
from urllib import error, request
from urllib.parse import urlsplit


COMMANDS = (
    "status",
    "documents",
    "open",
    "save",
    "save-as",
    "close",
    "ui-ribbon",
    "ui-menus",
    "ui-click",
    "screenshot",
    "run",
    "preferences",
)
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_GUI_UNAVAILABLE = 2
OPERATION_POLL_ATTEMPTS = 5
OPERATION_POLL_INTERVAL_SECONDS = 0.1
_LOOPBACK_HOSTS = frozenset({"127.0.0.1"})


class EndpointSafetyError(RuntimeError):
    """The endpoint file did not describe an authenticated control channel."""


def _control_module():
    import VibeCADAgentControl as control

    return control


def _argv_for_parser(argv: list[str] | None) -> list[str]:
    raw = list(sys.argv[1:] if argv is None else argv)
    extra = str(os.environ.get("VIBECAD_AGENT_ARGS") or "").strip()
    if extra and not raw:
        raw = extra.split()
    commands = set(COMMANDS) | {"-h", "--help"}
    for index, item in enumerate(raw):
        if item in commands:
            return raw[index:]
    # FreeCADCmd may leave the script path as argv[0] when we are given sys.argv.
    if argv is None:
        return raw
    for index, item in enumerate(raw):
        name = str(item).replace("\\", "/").rsplit("/", 1)[-1]
        if name in {"VibeCADAgentCli.py", "vibecad-agent.cmd"}:
            return raw[index + 1 :]
    return raw


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="VibeCADAgentCli",
        description=(
            "Control a running VibeCAD GUI over loopback HTTP, or run the same "
            "commands headless through FreeCADCmd."
        ),
    )
    parser.add_argument(
        "--local",
        action="store_true",
        help="Do not try the live GUI; run in this FreeCAD/VibeCAD process.",
    )
    parser.add_argument(
        "--gui-only",
        action="store_true",
        help="Fail if the live GUI loopback API is not listening.",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=30.0,
        help="HTTP timeout in seconds (default: 30).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("status", help="Report provider, auth, and open documents.")
    sub.add_parser("documents", help="List open documents.")
    sub.add_parser("ui-ribbon", help="Report live semantic ribbon-tab geometry.")
    sub.add_parser("ui-menus", help="Report live semantic top-level menu geometry.")
    sub.add_parser("preferences", help="Show VibeCAD Preferences (GUI only).")

    ui_click_parser = sub.add_parser(
        "ui-click",
        help="Activate one semantic ribbon or menu target without moving the OS cursor.",
    )
    ui_click_parser.add_argument(
        "--kind",
        required=True,
        choices=("ribbon", "menu"),
        help="Target family to activate.",
    )
    ui_click_parser.add_argument("--text", required=True, help="Exact visible target text.")
    ui_click_parser.add_argument(
        "--expected-process-id",
        type=int,
        help="Optional exact VibeCAD GUI process identity precondition.",
    )
    ui_click_parser.add_argument(
        "--expected-index",
        type=int,
        help="Optional semantic target-index precondition.",
    )

    screenshot_parser = sub.add_parser(
        "screenshot",
        help="Capture the visible VibeCAD main window as a PNG.",
    )
    screenshot_parser.add_argument(
        "--path",
        help="Optional absolute .png path; defaults to the private agent home.",
    )
    screenshot_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly allow replacing an existing screenshot.",
    )

    open_parser = sub.add_parser("open", help="Open a document and make it active.")
    open_parser.add_argument("--path", required=True, help="Absolute document path.")

    save_parser = sub.add_parser("save", help="Save an already-named document.")
    save_parser.add_argument("--document", help="Document name; defaults to active.")

    save_as_parser = sub.add_parser(
        "save-as", help="Save the active document to an explicit .FCStd path."
    )
    save_as_parser.add_argument("--path", required=True, help="Absolute .FCStd path.")
    save_as_parser.add_argument("--document", help="Document name; defaults to active.")
    save_as_parser.add_argument(
        "--overwrite",
        action="store_true",
        help="Explicitly allow replacing an existing target file.",
    )

    close_parser = sub.add_parser(
        "close", help="Close a document without silently discarding changes."
    )
    close_parser.add_argument("--document", help="Document name; defaults to active.")
    close_parser.add_argument(
        "--discard-unsaved",
        action="store_true",
        help="Explicitly allow closing a modified document without saving.",
    )

    run_parser = sub.add_parser(
        "run",
        help="Run Python or VibeScript source against the active document.",
    )
    run_parser.add_argument("--path", help="Optional absolute document to open first.")
    run_parser.add_argument("--script", help="Absolute .py / VibeScript file to exec.")
    run_parser.add_argument("--python", help="Inline Python / VibeScript source.")
    run_parser.add_argument(
        "--no-recompute",
        action="store_true",
        help="Do not recompute the active document after the script.",
    )
    return parser


def _command_arguments(args: argparse.Namespace) -> dict[str, Any]:
    if args.command == "open":
        return {"path": args.path}
    if args.command == "save":
        return {"document": args.document}
    if args.command == "save-as":
        return {
            "path": args.path,
            "document": args.document,
            "overwrite": bool(args.overwrite),
        }
    if args.command == "close":
        return {
            "document": args.document,
            "discard_unsaved": bool(args.discard_unsaved),
        }
    if args.command == "ui-click":
        return {
            "kind": args.kind,
            "text": args.text,
            "expected_process_id": args.expected_process_id,
            "expected_index": args.expected_index,
        }
    if args.command == "screenshot":
        return {
            "path": args.path,
            "overwrite": bool(args.overwrite),
        }
    if args.command == "run":
        return {
            "path": args.path,
            "script": args.script,
            "python": args.python,
            "recompute": not args.no_recompute,
        }
    return {}


def _http_route(command: str) -> tuple[str, str]:
    if command in {"status", "documents"}:
        return "GET", f"/v1/{command}"
    if command in {"ui-ribbon", "ui-menus"}:
        return "GET", f"/v1/ui/{command.removeprefix('ui-')}"
    if command == "ui-click":
        return "POST", "/v1/ui/click"
    return "POST", f"/v1/{command}"


def _control_command(command: str) -> str:
    return {
        "save-as": "save_as",
        "ui-ribbon": "ui_ribbon",
        "ui-menus": "ui_menus",
        "ui-click": "ui_click",
    }.get(command, command)


def _endpoint_context() -> tuple[str, str, str, bool]:
    control = _control_module()
    endpoint = control.load_endpoint() or {}
    fail_closed = endpoint.get("fail_closed") is True
    try:
        host = str(endpoint.get("host") or control.AGENT_HOST)
        port = int(endpoint.get("port") or control.configured_port())
        base_url = str(endpoint.get("base_url") or f"http://{host}:{port}")
    except (TypeError, ValueError, OverflowError) as exc:
        raise EndpointSafetyError(
            "The VibeCAD agent endpoint does not contain a valid host and port."
        ) from exc
    if fail_closed:
        try:
            parsed = urlsplit(base_url)
            parsed_port = parsed.port
        except (TypeError, ValueError, OverflowError) as exc:
            raise EndpointSafetyError(
                "The fail-closed VibeCAD endpoint is not a valid loopback HTTP URL."
            ) from exc
        endpoint_host = host.strip().lower()
        url_host = str(parsed.hostname or "").strip().lower()
        if (
            parsed.scheme.lower() != "http"
            or url_host not in _LOOPBACK_HOSTS
            or endpoint_host not in _LOOPBACK_HOSTS
            or url_host != endpoint_host
            or parsed_port is None
            or parsed_port != port
            or not 1 <= port <= 65535
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path not in {"", "/"}
            or parsed.query
            or parsed.fragment
        ):
            raise EndpointSafetyError(
                "The fail-closed VibeCAD endpoint must be the exact "
                "http://127.0.0.1:<port> origin."
            )

    # Fail-closed development endpoints are proven local before this read.
    # Compatibility endpoints retain their original owner-configured authority.
    token = control.load_token() or control.load_or_create_token()
    server_instance_id = str(endpoint.get("server_instance_id") or "")
    return base_url.rstrip("/"), token, server_instance_id, fail_closed


def _endpoint_and_token() -> tuple[str, str]:
    """Preserve the original public helper shape for existing callers."""

    base_url, token, _server_instance_id, _fail_closed = _endpoint_context()
    return base_url, token


def _is_proven_connection_refusal(exc: BaseException) -> bool:
    """Return True only when the request was refused before a server accepted it."""

    pending: list[BaseException] = [exc]
    visited: set[int] = set()
    while pending:
        current = pending.pop()
        if id(current) in visited:
            continue
        visited.add(id(current))
        if isinstance(current, ConnectionRefusedError):
            return True
        if isinstance(current, OSError) and (
            getattr(current, "errno", None) == errno.ECONNREFUSED
            or getattr(current, "winerror", None) == 10061
        ):
            return True
        for candidate in (
            getattr(current, "reason", None),
            getattr(current, "__cause__", None),
            getattr(current, "__context__", None),
        ):
            if isinstance(candidate, BaseException):
                pending.append(candidate)
    return False


def _unresolved_remote_operation(
    operation_id: str,
    *,
    operation_state: str = "unknown",
    detail: str,
) -> dict[str, Any]:
    return {
        "ok": False,
        "failure_code": "REMOTE_OUTCOME_UNRESOLVED",
        "failure_stage": "transport",
        "error": detail,
        "operation_id": operation_id,
        "operation_state": operation_state,
    }


def _poll_operation_completion(
    *,
    base_url: str,
    token: str,
    server_instance_id: str,
    operation_id: str,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Recover one ambiguous POST response without redispatching the mutation."""

    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    operation_state = "unknown"
    detail = (
        "The GUI may have completed the operation, but its authenticated "
        "completion record could not be proven. The operation was not redispatched."
    )
    for attempt in range(OPERATION_POLL_ATTEMPTS):
        status_request = request.Request(
            f"{base_url}/v1/operations/{operation_id}",
            headers=headers,
            method="GET",
        )
        try:
            response = request.urlopen(status_request, timeout=timeout_seconds)
            try:
                payload = json.loads(response.read().decode("utf-8"))
            finally:
                response.close()
        except error.HTTPError as exc:
            try:
                if exc.code not in {404, 409, 425}:
                    detail = f"Operation status polling returned HTTP {exc.code}."
            finally:
                exc.close()
        except (
            error.URLError,
            TimeoutError,
            ConnectionError,
            OSError,
            HTTPException,
            UnicodeDecodeError,
            json.JSONDecodeError,
        ) as exc:
            detail = f"Operation status polling did not complete: {exc}."
        else:
            operation = payload.get("operation") if isinstance(payload, dict) else None
            if isinstance(operation, dict):
                returned_operation_id = str(operation.get("operation_id") or "")
                returned_server_id = str(operation.get("server_instance_id") or "")
                operation_state = str(operation.get("state") or "unknown")
                if returned_operation_id != operation_id:
                    detail = "The operation status response named a different operation."
                elif server_instance_id and returned_server_id != server_instance_id:
                    detail = (
                        "The GUI server instance changed before completion could be proven."
                    )
                elif operation_state == "completed":
                    completed_response = operation.get("response")
                    completed_response_id = (
                        str(completed_response.get("operation_id") or "")
                        if isinstance(completed_response, dict)
                        else ""
                    )
                    if completed_response_id == operation_id:
                        return completed_response
                    detail = (
                        "The operation is marked completed without a response bound "
                        "to the requested operation ID."
                    )
                else:
                    detail = (
                        f"The GUI still reports operation state {operation_state!r}; "
                        "the operation was not redispatched."
                    )
        if attempt + 1 < OPERATION_POLL_ATTEMPTS:
            time.sleep(OPERATION_POLL_INTERVAL_SECONDS)
    return _unresolved_remote_operation(
        operation_id,
        operation_state=operation_state,
        detail=detail,
    )


def call_http(
    command: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any] | None:
    """Return a payload if the GUI answered, or None if nothing is listening."""

    try:
        base_url, token, server_instance_id, fail_closed = _endpoint_context()
    except EndpointSafetyError as exc:
        return {
            "ok": False,
            "failure_code": "ENDPOINT_NOT_LOOPBACK",
            "failure_stage": "transport",
            "error": str(exc),
        }
    method, route = _http_route(command)
    url = f"{base_url}{route}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    data = None
    operation_id = ""
    if method == "POST":
        if fail_closed:
            operation_id = str(uuid.uuid4())
            arguments = {**arguments, "operation_id": operation_id}
        headers["Content-Type"] = "application/json"
        data = json.dumps(arguments).encode("utf-8")
    http_request = request.Request(url, data=data, headers=headers, method=method)
    try:
        response = request.urlopen(http_request, timeout=timeout_seconds)
        try:
            payload = json.loads(response.read().decode("utf-8"))
        finally:
            response.close()
    except error.HTTPError as exc:
        try:
            raw = exc.read() if hasattr(exc, "read") else b""
            try:
                payload = json.loads(raw.decode("utf-8")) if raw else {}
            except (UnicodeDecodeError, json.JSONDecodeError):
                payload = {}
        finally:
            exc.close()
        if isinstance(payload, dict) and payload:
            return payload
        return {
            "ok": False,
            "failure_code": "HTTP_ERROR",
            "failure_stage": "transport",
            "error": f"Agent control HTTP {exc.code}.",
        }
    except (
        error.URLError,
        TimeoutError,
        ConnectionError,
        OSError,
        HTTPException,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        if (
            method != "POST"
            or not fail_closed
            or _is_proven_connection_refusal(exc)
        ):
            return None
        return _poll_operation_completion(
            base_url=base_url,
            token=token,
            server_instance_id=server_instance_id,
            operation_id=operation_id,
            timeout_seconds=timeout_seconds,
        )
    if isinstance(payload, dict):
        return payload
    if method != "POST" or not fail_closed:
        return None
    return _poll_operation_completion(
        base_url=base_url,
        token=token,
        server_instance_id=server_instance_id,
        operation_id=operation_id,
        timeout_seconds=timeout_seconds,
    )


def call_local(command: str, arguments: dict[str, Any]) -> dict[str, Any]:
    control = _control_module()
    action = _control_command(command)
    if action in control.UPSTREAM_COMMANDS:
        return control.dispatch(action, arguments)
    return control.dispatch(
        action,
        arguments,
        allow_headless_direct=True,
        fail_closed=True,
    )


def _gui_unavailable() -> dict[str, Any]:
    return {
        "ok": False,
        "failure_code": "GUI_NOT_RUNNING",
        "failure_stage": "transport",
        "error": (
            "No VibeCAD GUI is listening on the local agent-control port. "
            "Start VibeCAD.exe, or rerun this command through FreeCADCmd.exe "
            "without --gui-only."
        ),
    }


def execute(args: argparse.Namespace) -> dict[str, Any]:
    arguments = _command_arguments(args)
    if not args.local:
        remote = call_http(args.command, arguments, timeout_seconds=args.timeout)
        if remote is not None:
            return remote
        if args.gui_only:
            return _gui_unavailable()
    try:
        return call_local(args.command, arguments)
    except Exception as exc:
        if args.gui_only:
            return _gui_unavailable()
        return {
            "ok": False,
            "failure_code": "LOCAL_UNAVAILABLE",
            "failure_stage": "native_call",
            "error": (
                f"In-process control failed ({exc}). Start VibeCAD.exe and retry, "
                "or invoke this CLI through FreeCADCmd.exe."
            ),
        }


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(_argv_for_parser(argv))
    payload = execute(args)
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    if payload.get("ok"):
        return EXIT_OK
    if payload.get("failure_code") == "GUI_NOT_RUNNING":
        return EXIT_GUI_UNAVAILABLE
    return EXIT_ERROR


if __name__ == "__main__":
    raise SystemExit(main())
