# SPDX-License-Identifier: LGPL-2.1-or-later

"""Agent-facing CLI for the VibeCAD local control channel.

Works two ways:

1. As a plain Python HTTP client against a running VibeCAD GUI
   (``http://127.0.0.1:8766``). No FreeCAD bindings required.
2. In-process through ``FreeCADCmd.exe`` / ``VibeCADCmd.exe`` when the GUI is
   not running (headless open / run / status). Preferences still need the GUI.

The CLI reads the bearer token from the private Agent token file. Do not type
passwords or OAuth codes.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any
from urllib import error, request


COMMANDS = ("status", "documents", "open", "run", "preferences")
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_GUI_UNAVAILABLE = 2


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
    sub.add_parser("preferences", help="Show VibeCAD Preferences (GUI only).")

    open_parser = sub.add_parser("open", help="Open a document and make it active.")
    open_parser.add_argument("--path", required=True, help="Absolute document path.")

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
    return "POST", f"/v1/{command}"


def _endpoint_and_token() -> tuple[str, str]:
    control = _control_module()
    endpoint = control.load_endpoint() or {}
    host = str(endpoint.get("host") or control.AGENT_HOST)
    port = int(endpoint.get("port") or control.configured_port())
    base_url = str(endpoint.get("base_url") or f"http://{host}:{port}")
    token = control.load_token() or control.load_or_create_token()
    return base_url.rstrip("/"), token


def call_http(
    command: str,
    arguments: dict[str, Any],
    *,
    timeout_seconds: float = 30.0,
) -> dict[str, Any] | None:
    """Return a payload if the GUI answered, or None if nothing is listening."""

    base_url, token = _endpoint_and_token()
    method, route = _http_route(command)
    url = f"{base_url}{route}"
    headers = {
        "Accept": "application/json",
        "Authorization": f"Bearer {token}",
    }
    data = None
    if method == "POST":
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
        raw = exc.read() if hasattr(exc, "read") else b""
        try:
            payload = json.loads(raw.decode("utf-8")) if raw else {}
        except json.JSONDecodeError:
            payload = {}
        if isinstance(payload, dict) and payload:
            return payload
        return {
            "ok": False,
            "failure_code": "HTTP_ERROR",
            "failure_stage": "transport",
            "error": f"Agent control HTTP {exc.code}.",
        }
    except (error.URLError, TimeoutError, ConnectionError, OSError):
        return None
    return payload if isinstance(payload, dict) else None


def call_local(command: str, arguments: dict[str, Any]) -> dict[str, Any]:
    return _control_module().dispatch(command, arguments)


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
