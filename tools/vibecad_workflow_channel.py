# SPDX-License-Identifier: LGPL-2.1-or-later
"""Loopback click channel used by the VibeCAD workflow harness.

This is the same authenticated HTTP surface the visible tour uses:
``GET /v1/status``, ``GET /v1/documents``, ``GET /v1/ui/ribbon``,
``GET /v1/ui/menus``, ``POST /v1/ui/click``, and ``POST /v1/run``.
Clicks stay on that one route. Nothing here drives the OS mouse.
"""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit


LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
TREE_INSPECT_PYTHON = """
doc = App.ActiveDocument
result = {
    "document": None if doc is None else str(doc.Name),
    "objects": []
    if doc is None
    else [
        {
            "name": str(obj.Name),
            "type_id": str(obj.TypeId),
            "label": str(getattr(obj, "Label", "") or ""),
        }
        for obj in list(doc.Objects)
    ],
}
"""


def require_loopback_url(base_url: str) -> str:
    parsed = urlsplit(str(base_url or "").strip())
    host = str(parsed.hostname or "").strip().lower()
    if (
        parsed.scheme.lower() != "http"
        or host not in LOOPBACK_HOSTS
        or parsed.username is not None
        or parsed.password is not None
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "The workflow harness only talks to an authenticated loopback "
            "http://127.0.0.1:<port> agent."
        )
    return str(base_url).rstrip("/")


def click_input_method(kind: str) -> str:
    return {
        "ribbon": "qt_in_process_mouse_click",
        "menu": "qt_in_process_menu_popup",
        "action": "qt_in_process_action_trigger",
        "command": "qt_in_process_action_trigger",
        "button": "qt_in_process_action_trigger",
    }.get(str(kind or "").strip().lower(), "")


class AgentClickChannel:
    """HTTP client for the existing loopback ``/v1/ui/click`` path."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        timeout_seconds: float = 30.0,
    ) -> None:
        self.base_url = require_loopback_url(base_url)
        self.token = str(token or "").strip()
        if len(self.token) < 40:
            raise ValueError("The checkout-scoped bearer token is invalid.")
        self.timeout_seconds = float(timeout_seconds)

    def request(self, method: str, route: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        headers = {
            "Accept": "application/json",
            "Authorization": f"Bearer {self.token}",
        }
        data = None
        if method == "POST":
            headers["Content-Type"] = "application/json"
            data = json.dumps(body or {}).encode("utf-8")
        http_request = request.Request(
            f"{self.base_url}{route}",
            data=data,
            headers=headers,
            method=method,
        )
        try:
            response = request.urlopen(http_request, timeout=self.timeout_seconds)
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
                "error": f"Agent control HTTP {exc.code}.",
            }
        if not isinstance(payload, dict):
            return {
                "ok": False,
                "failure_code": "INVALID_RESPONSE",
                "error": "Agent control did not return a JSON object.",
            }
        return payload

    def status(self) -> dict[str, Any]:
        return self.request("GET", "/v1/status")

    def documents(self) -> dict[str, Any]:
        return self.request("GET", "/v1/documents")

    def click(
        self,
        kind: str,
        text: str,
        *,
        expected_process_id: int | None = None,
        expected_index: int | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"kind": kind, "text": text}
        if expected_process_id is not None:
            body["expected_process_id"] = expected_process_id
        if expected_index is not None:
            body["expected_index"] = expected_index
        return self.request("POST", "/v1/ui/click", body)

    def inspect_tree(self) -> dict[str, Any]:
        return self.request("POST", "/v1/run", {"python": TREE_INSPECT_PYTHON, "recompute": False})


class FakeAgentState:
    """In-memory GUI/document state for CI when no display is available."""

    def __init__(self, export_dir: str) -> None:
        self.export_dir = export_dir
        self.documents: list[dict[str, Any]] = []
        self.active_index = -1
        self.selected_ribbon = "Model"
        self.exported_path = ""
        self.click_count = 0

    def active_document(self) -> dict[str, Any] | None:
        if self.active_index < 0 or self.active_index >= len(self.documents):
            return None
        return self.documents[self.active_index]

    def document_summaries(self) -> list[dict[str, Any]]:
        summaries = []
        for index, document in enumerate(self.documents):
            summaries.append(
                {
                    "document": document["name"],
                    "label": document["name"],
                    "path": "",
                    "active": index == self.active_index,
                    "object_count": len(document["objects"]),
                    "modified": True,
                }
            )
        return summaries

    def tree_result(self) -> dict[str, Any]:
        document = self.active_document()
        if document is None:
            return {"document": None, "objects": []}
        return {
            "document": document["name"],
            "objects": list(document["objects"]),
        }

    def click_payload(self, body: dict[str, Any]) -> dict[str, Any]:
        kind = str(body.get("kind") or "").strip().lower().replace("-", "_")
        if kind in {"tab", "ribbon_tab"}:
            kind = "ribbon"
        if kind in {"command", "button"}:
            kind = "action"
        text = str(body.get("text") or "").strip()
        self.click_count += 1
        if kind not in {"ribbon", "menu", "action"}:
            return {
                "ok": False,
                "failure_code": "UI_TARGET_KIND_INVALID",
                "error": "kind must be 'ribbon', 'menu', or 'action'.",
            }
        if not text:
            return {
                "ok": False,
                "failure_code": "UI_TARGET_TEXT_REQUIRED",
                "error": "text must name one visible semantic UI target.",
            }

        details = {
            "target_kind": kind,
            "target_text": text,
            "target_index": 0,
            "click_queued": False,
            "focus_restored": True,
            "active_window_unchanged": True,
            "popup_restored": True,
            "active_action_restored": True,
            "interaction_restored": True,
            "input_method": click_input_method(kind),
            "physical_cursor_control": "none",
            "physical_cursor_unchanged": True,
            "semantic_verified": True,
        }

        if kind == "ribbon":
            if text not in {"Model", "Sketch", "Aero"}:
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_NOT_UNIQUE",
                    "error": f"Expected exactly one ribbon tab named {text!r}; found 0.",
                }
            self.selected_ribbon = text
            details["selected_after"] = text
            return {"ok": True, **details}

        if kind == "menu":
            if text != "File":
                return {
                    "ok": False,
                    "failure_code": "UI_TARGET_NOT_UNIQUE",
                    "error": f"Expected exactly one top-level menu named {text!r}; found 0.",
                }
            details.update(
                {
                    "menu_visible": True,
                    "menu_open_after": False,
                    "preview_duration_milliseconds": 240,
                }
            )
            return {"ok": True, **details}

        return self._click_action(text, details)

    def _click_action(self, text: str, details: dict[str, Any]) -> dict[str, Any]:
        if text in {"Std_New", "New"}:
            name = f"Unnamed{len(self.documents) + 1}"
            self.documents.append({"name": name, "objects": []})
            self.active_index = len(self.documents) - 1
            details["object_name"] = "Std_New"
            return {"ok": True, **details}

        document = self.active_document()
        if document is None:
            return {
                "ok": False,
                "failure_code": "UI_CLICK_NOT_APPLIED",
                "error": f"Qt click did not activate action target {text!r}.",
                **details,
                "semantic_verified": False,
            }

        type_ids = [str(obj["type_id"]) for obj in document["objects"]]
        if text in {"Sketcher_NewSketch", "Create Sketch", "Sketch"}:
            document["objects"].append(
                {
                    "name": "Sketch",
                    "type_id": "Sketcher::SketchObject",
                    "label": "Sketch",
                }
            )
            details["object_name"] = "Sketcher_NewSketch"
            return {"ok": True, **details}

        if text in {"PartDesign_Pad", "Pad"}:
            if "Sketcher::SketchObject" not in type_ids:
                return {
                    "ok": False,
                    "failure_code": "UI_CLICK_NOT_APPLIED",
                    "error": "Pad has no sketch to consume.",
                    **details,
                    "semantic_verified": False,
                }
            document["objects"].append(
                {
                    "name": "Pad",
                    "type_id": "PartDesign::Pad",
                    "label": "Pad",
                }
            )
            details["object_name"] = "PartDesign_Pad"
            return {"ok": True, **details}

        if text in {"Std_Export", "Export"}:
            path = Path(self.export_dir) / f"{document['name']}.step"
            path.write_text("ISO-10303-21; /* fake STEP from workflow harness */\n", encoding="utf-8")
            self.exported_path = str(path)
            details["object_name"] = "Std_Export"
            details["exported_path"] = self.exported_path
            return {"ok": True, **details}

        return {
            "ok": False,
            "failure_code": "UI_TARGET_NOT_UNIQUE",
            "error": f"Expected exactly one action named {text!r}; found 0.",
        }


class _FakeHandler(BaseHTTPRequestHandler):
    server: "FakeAgentServer"

    def log_message(self, _format: str, *_args: object) -> None:
        return

    def _read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        raw = self.rfile.read(length)
        try:
            payload = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            return {}
        return payload if isinstance(payload, dict) else {}

    def _write_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        expected = f"Bearer {self.server.token}"
        return self.headers.get("Authorization") == expected

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write_json(401, {"ok": False, "failure_code": "UNAUTHORIZED"})
            return
        state = self.server.state
        if self.path == "/v1/status":
            self._write_json(
                200,
                {
                    "ok": True,
                    "channel": "vibecad-agent-control",
                    "gui_up": True,
                    "documents": state.document_summaries(),
                    "exported_path": state.exported_path,
                },
            )
            return
        if self.path == "/v1/documents":
            documents = state.document_summaries()
            self._write_json(
                200,
                {
                    "ok": True,
                    "document_count": len(documents),
                    "documents": documents,
                },
            )
            return
        if self.path == "/v1/ui/ribbon":
            self._write_json(
                200,
                {
                    "ok": True,
                    "selected_text": state.selected_ribbon,
                    "tabs": [
                        {"text": "Model", "index": 0, "enabled": True},
                        {"text": "Sketch", "index": 1, "enabled": True},
                    ],
                },
            )
            return
        if self.path == "/v1/ui/menus":
            self._write_json(
                200,
                {
                    "ok": True,
                    "menus": [
                        {
                            "text": "File",
                            "index": 0,
                            "enabled": True,
                            "visible": True,
                            "menu_visible": False,
                        }
                    ],
                },
            )
            return
        self._write_json(404, {"ok": False, "failure_code": "NOT_FOUND"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._write_json(401, {"ok": False, "failure_code": "UNAUTHORIZED"})
            return
        body = self._read_json()
        state = self.server.state
        if self.path == "/v1/ui/click":
            self._write_json(200, state.click_payload(body))
            return
        if self.path == "/v1/run":
            self._write_json(
                200,
                {
                    "ok": True,
                    "result": state.tree_result(),
                    "exported_path": state.exported_path,
                },
            )
            return
        self._write_json(404, {"ok": False, "failure_code": "NOT_FOUND"})


class FakeAgentServer(ThreadingHTTPServer):
    def __init__(self, token: str, state: FakeAgentState) -> None:
        super().__init__(("127.0.0.1", 0), _FakeHandler)
        self.token = token
        self.state = state


def start_fake_channel(export_dir: str, token: str) -> tuple[FakeAgentServer, str, FakeAgentState]:
    state = FakeAgentState(export_dir)
    server = FakeAgentServer(token, state)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address[:2]
    return server, f"http://{host}:{port}", state
