# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for provider-specific Codex Responses normalization."""

from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import threading
from urllib import request

import VibeCADCodexResponses as responses


def test_xai_replay_normalization_preserves_tool_history() -> None:
    payload = {
        "model": "grok-4.6",
        "tools": [
            {"type": "web_search", "external_web_access": True},
            {"type": "function", "name": "core__set_view"},
        ],
        "input": [
            {"role": "user", "content": "Set the view."},
            {
                "type": "reasoning",
                "content": None,
                "encrypted_content": None,
                "summary": [{"type": "summary_text", "text": "Inspect view."}],
            },
            {
                "type": "function_call",
                "call_id": "call-1",
                "name": "core__set_view",
                "arguments": "{}",
            },
            {
                "type": "function_call_output",
                "call_id": "call-1",
                "output": "{\"ok\":true}",
            },
        ],
    }

    normalized = json.loads(
        responses._normalized_xai_body(json.dumps(payload).encode("utf-8"))
    )

    assert [item.get("type") for item in normalized["input"]] == [
        None,
        "function_call",
        "function_call_output",
    ]
    assert normalized["input"][1:] == payload["input"][2:]
    assert normalized["tools"] == [
        {"type": "web_search"},
        {"type": "function", "name": "core__set_view"},
    ]
    assert normalized["model"] == "grok-4.6"


def test_xai_gateway_normalizes_only_responses_input() -> None:
    received: dict[str, object] = {}

    class _UpstreamHandler(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length", "0"))
            received["path"] = self.path
            received["authorization"] = self.headers.get("Authorization")
            received["payload"] = json.loads(self.rfile.read(length))
            body = b'{"ok":true}'
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            return

    upstream = ThreadingHTTPServer(("127.0.0.1", 0), _UpstreamHandler)
    upstream_thread = threading.Thread(target=upstream.serve_forever, daemon=True)
    upstream_thread.start()
    host, port = upstream.server_address[:2]
    gateway = responses._ResponsesGateway(f"http://{host}:{port}/v1")
    try:
        outbound = {
            "input": [
                {"role": "user", "content": "Set the view."},
                {"type": "reasoning", "summary": []},
                {
                    "type": "function_call_output",
                    "call_id": "call-1",
                    "output": "done",
                },
            ]
        }
        http_request = request.Request(
            gateway.base_url + "/responses",
            data=json.dumps(outbound).encode("utf-8"),
            headers={
                "Authorization": "Bearer test-secret",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with request.urlopen(http_request, timeout=5.0) as reply:
            assert reply.status == 200
            assert json.loads(reply.read()) == {"ok": True}

        assert received["path"] == "/v1/responses"
        assert received["authorization"] == "Bearer test-secret"
        forwarded = received["payload"]
        assert isinstance(forwarded, dict)
        assert [item.get("type") for item in forwarded["input"]] == [
            None,
            "function_call_output",
        ]
    finally:
        gateway.close()
        upstream.shutdown()
        upstream.server_close()
        upstream_thread.join(timeout=1.0)


def test_xai_gateway_scope_is_exact() -> None:
    assert responses._requires_xai_normalization("https://api.x.ai/v1") is True
    assert responses._requires_xai_normalization("https://api.openai.com/v1") is False
    assert responses._requires_xai_normalization("http://127.0.0.1:11434/v1") is False
    assert responses._requires_xai_normalization("https://api.x.ai.example/v1") is False
