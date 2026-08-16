# SPDX-License-Identifier: LGPL-2.1-or-later

"""Narrow Responses transport normalization for Codex-backed providers."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import secrets
import threading
from typing import Any
from urllib import error, request
from urllib.parse import urlsplit


_XAI_HOSTS = frozenset({"api.x.ai"})
_FORWARDED_HEADERS = ("Authorization", "Content-Type", "Accept", "User-Agent")
_gateways_lock = threading.RLock()
_gateways: dict[str, "_ResponsesGateway"] = {}


class CodexResponsesCompatibilityError(RuntimeError):
    """Raised when a provider compatibility gateway cannot be started."""


def _requires_xai_normalization(base_url: str | None) -> bool:
    hostname = str(urlsplit(str(base_url or "").strip()).hostname or "").lower()
    return hostname in _XAI_HOSTS


def _normalized_xai_body(body: bytes) -> bytes:
    """Normalize Codex Responses fields that xAI does not accept."""

    try:
        payload = json.loads(body)
    except (TypeError, ValueError):
        return body
    if not isinstance(payload, dict):
        return body
    changed = False
    input_items = payload.get("input")
    if isinstance(input_items, list):
        normalized_input = [
            item
            for item in input_items
            if not isinstance(item, dict) or item.get("type") != "reasoning"
        ]
        if len(normalized_input) != len(input_items):
            payload["input"] = normalized_input
            changed = True
    tools = payload.get("tools")
    if isinstance(tools, list):
        for tool in tools:
            if (
                isinstance(tool, dict)
                and tool.get("type") == "web_search"
                and "external_web_access" in tool
            ):
                tool.pop("external_web_access", None)
                changed = True
    if not changed:
        return body
    return json.dumps(
        payload,
        ensure_ascii=True,
        separators=(",", ":"),
    ).encode("utf-8")


class _GatewayServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, gateway: "_ResponsesGateway") -> None:
        self.gateway = gateway
        super().__init__(("127.0.0.1", 0), _GatewayHandler)


class _GatewayHandler(BaseHTTPRequestHandler):
    server: _GatewayServer
    protocol_version = "HTTP/1.0"

    def do_GET(self) -> None:
        self._forward()

    def do_POST(self) -> None:
        self._forward()

    def _forward(self) -> None:
        gateway = self.server.gateway
        parsed = urlsplit(self.path)
        if not (
            parsed.path == gateway.path_prefix
            or parsed.path.startswith(gateway.path_prefix + "/")
        ):
            self.send_error(404)
            return
        suffix = parsed.path[len(gateway.path_prefix) :] or "/"
        upstream_url = gateway.upstream_base_url + suffix
        if parsed.query:
            upstream_url += "?" + parsed.query

        body: bytes | None = None
        if self.command == "POST":
            try:
                content_length = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                self.send_error(400)
                return
            body = self.rfile.read(max(0, content_length))
            if suffix.rstrip("/").endswith("/responses"):
                body = _normalized_xai_body(body)

        headers = {
            name: self.headers[name]
            for name in _FORWARDED_HEADERS
            if self.headers.get(name)
        }
        outbound = request.Request(
            upstream_url,
            data=body,
            method=self.command,
            headers=headers,
        )
        try:
            upstream: Any = request.urlopen(outbound, timeout=300.0)
        except error.HTTPError as exc:
            upstream = exc
        except Exception as exc:
            response = json.dumps(
                {"error": f"VibeCAD could not reach the configured provider: {exc}"},
                ensure_ascii=True,
                separators=(",", ":"),
            ).encode("utf-8")
            self.send_response(502)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(response)))
            self.end_headers()
            self.wfile.write(response)
            return

        try:
            status = getattr(upstream, "status", None)
            if status is None:
                status = getattr(upstream, "code", 502)
            self.send_response(int(status))
            for name in ("Content-Type", "x-request-id", "cf-ray"):
                value = upstream.headers.get(name)
                if value:
                    self.send_header(name, value)
            self.send_header("Connection", "close")
            self.end_headers()
            while True:
                chunk = upstream.read(64 * 1024)
                if not chunk:
                    break
                self.wfile.write(chunk)
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            upstream.close()
            self.close_connection = True

    def log_message(self, _format: str, *_args: Any) -> None:
        return


class _ResponsesGateway:
    def __init__(self, upstream_base_url: str) -> None:
        self.upstream_base_url = upstream_base_url.rstrip("/")
        self.path_prefix = "/" + secrets.token_urlsafe(24)
        try:
            self.server = _GatewayServer(self)
        except OSError as exc:
            raise CodexResponsesCompatibilityError(
                f"VibeCAD could not start its local xAI compatibility transport: {exc}"
            ) from exc
        self.thread = threading.Thread(
            target=self.server.serve_forever,
            name="VibeCAD-xAI-Responses",
            daemon=True,
        )
        self.thread.start()

    @property
    def base_url(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}{self.path_prefix}"

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=1.0)


def codex_responses_base_url(base_url: str | None) -> str | None:
    """Return the endpoint Codex should use for this Responses provider."""

    clean_base_url = str(base_url or "").strip().rstrip("/")
    if not clean_base_url or not _requires_xai_normalization(clean_base_url):
        return clean_base_url or None
    with _gateways_lock:
        gateway = _gateways.get(clean_base_url)
        if gateway is None:
            gateway = _ResponsesGateway(clean_base_url)
            _gateways[clean_base_url] = gateway
        return gateway.base_url


def shutdown_codex_responses_gateways() -> None:
    """Stop every process-local Responses compatibility gateway."""

    with _gateways_lock:
        gateways = list(_gateways.values())
        _gateways.clear()
    for gateway in gateways:
        try:
            gateway.close()
        except Exception:
            pass
