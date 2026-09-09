# SPDX-License-Identifier: LGPL-2.1-or-later

"""Count real SDK transport attempts without contacting Anthropic."""

from __future__ import annotations

import json

import pytest

import VibeCADProvider as provider

anthropic = pytest.importorskip("anthropic")
httpx = pytest.importorskip("httpx")


def _event(kind, **fields):
    return f"event: {kind}\ndata: {json.dumps({'type': kind, **fields})}\n\n".encode()


def _response_events():
    return [
        _event("message_start", message={
            "id": "msg_test", "type": "message", "role": "assistant",
            "model": "test-model", "content": [], "stop_reason": None,
            "stop_sequence": None, "usage": {"input_tokens": 1, "output_tokens": 0},
        }),
        _event("content_block_start", index=0,
               content_block={"type": "text", "text": ""}),
        _event("content_block_delta", index=0,
               delta={"type": "text_delta", "text": "Recovered."}),
        _event("content_block_stop", index=0),
        _event("message_delta", delta={"stop_reason": "end_turn", "stop_sequence": None},
               usage={"output_tokens": 1}),
        _event("message_stop"),
    ]


@pytest.fixture
def run_transport(monkeypatch):
    real_client = anthropic.Anthropic
    clients = []

    def run(outcomes, *, metadata_failures=0):
        requests = []
        metadata = []
        sleeps = []
        messages = []

        class InterruptedStream(httpx.SyncByteStream):
            def __iter__(self):
                yield b"".join(_response_events()[:3])
                raise httpx.ReadError("peer closed connection")

        def handle(request):
            if request.method == "GET":
                metadata.append(request)
                if len(metadata) <= metadata_failures:
                    return httpx.Response(503, json={
                        "type": "error", "error": {
                            "type": "overloaded_error", "message": "Temporary"
                        },
                    })
                return httpx.Response(200, json={
                    "id": "test-model", "type": "model",
                    "display_name": "Test", "created_at": "2026-01-01T00:00:00Z",
                    "max_tokens": 8192,
                })
            requests.append(request)
            outcome = outcomes[min(len(requests) - 1, len(outcomes) - 1)]
            if outcome == "timeout":
                raise httpx.ReadTimeout("read timed out", request=request)
            if outcome == "connect":
                raise httpx.ConnectError("connection failed", request=request)
            if outcome == "broken":
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      stream=InterruptedStream())
            if outcome == "ok":
                return httpx.Response(200, headers={"content-type": "text/event-stream"},
                                      content=b"".join(_response_events()))
            status, headers = outcome if isinstance(outcome, tuple) else (outcome, {})
            return httpx.Response(status, headers=headers, json={
                "type": "error", "error": {"type": "api_error", "message": "Temporary"},
            })

        def make_client(**kwargs):
            client = real_client(
                **kwargs, http_client=httpx.Client(transport=httpx.MockTransport(handle))
            )
            clients.append(client)
            return client

        class Connection:
            def send(self, message):
                messages.append(message)

            def close(self):
                pass

        monkeypatch.setattr(anthropic, "Anthropic", make_client)
        monkeypatch.setattr(provider, "_validate_provider_wire_surface", lambda _: None)
        monkeypatch.setattr(provider.time, "sleep", sleeps.append)
        provider._anthropic_child_main(
            Connection(), "Inspect the model.", {"provider_tool_schemas": []},
            "test-model", "fake-key", None, 1.0, 1, False,
        )
        return requests, metadata, messages, sleeps

    yield run
    for client in clients:
        client.close()


@pytest.mark.parametrize("failure", ["timeout", "connect", "broken", 408, 409, 429, 503])
def test_stream_total_transport_attempts_are_bounded(run_transport, failure):
    requests, _, messages, _ = run_transport([failure])
    expected_attempts = 3 if isinstance(failure, int) else 6
    assert len(requests) == expected_attempts
    assert messages[-1]["type"] == "error"
    retries = [
        message["event"] for message in messages
        if message.get("event", {}).get("event") == "anthropic_stream_retrying"
    ]
    assert [event["next_attempt"] for event in retries] == list(
        range(2, expected_attempts + 1)
    )
    assert [event["transport_attempt_count"] for event in retries] == list(
        range(1, expected_attempts)
    )
    assert all(event["response_content_observed"] == (failure == "broken")
               for event in retries)
    assert not any(message["type"] == "tool" for message in messages)


def test_stream_mixed_failures_can_recover_on_last_attempt(run_transport):
    requests, _, messages, _ = run_transport(
        ["timeout", 503, "broken", 429, "connect", "ok"]
    )
    assert len(requests) == 6
    assert messages[-1] == {
        "type": "done", "final_output": "Recovered.", "raw": None
    }


@pytest.mark.parametrize("status", [400, 401, 403, 422])
def test_stream_permanent_errors_are_not_retried(run_transport, status):
    requests, _, messages, sleeps = run_transport([status])
    assert len(requests) == 1
    assert messages[-1]["type"] == "error"
    assert sleeps == []


def test_stream_obeys_explicit_do_not_retry(run_transport):
    requests, _, messages, sleeps = run_transport([(503, {"x-should-retry": "false"})])
    assert len(requests) == 1
    assert messages[-1]["type"] == "error"
    assert sleeps == []


@pytest.mark.parametrize("headers,delay", [
    ({"retry-after": "2"}, 2.0),
    ({"retry-after-ms": "1250", "retry-after": "2"}, 1.25),
    ({"retry-after": "Thu, 01 Jan 1970 00:16:43 GMT"}, 3.0),
])
def test_stream_respects_server_retry_delay(run_transport, monkeypatch, headers, delay):
    monkeypatch.setattr(provider.time, "time", lambda: 1000.0)
    requests, _, messages, sleeps = run_transport([(429, headers), "ok"])
    assert len(requests) == 2
    assert messages[-1]["type"] == "done"
    assert sleeps == [delay]


def test_metadata_retains_sdk_retries(run_transport):
    requests, metadata, messages, _ = run_transport(["ok"], metadata_failures=2)
    assert len(metadata) == 3
    assert len(requests) == 1
    assert messages[-1]["type"] == "done"


@pytest.mark.parametrize("value", ["invalid", "NaN", "Infinity", "-1", "61"])
def test_stream_invalid_retry_delay_uses_bounded_backoff(run_transport, value):
    requests, _, messages, sleeps = run_transport(
        [(429, {"retry-after": value}), "ok"]
    )
    assert len(requests) == 2
    assert messages[-1]["type"] == "done"
    assert sleeps == [0.5]


def test_stream_status_retry_limit_survives_mixed_failures(run_transport):
    requests, _, messages, _ = run_transport(
        [503, "timeout", 429, "broken", 503, "ok"]
    )
    assert len(requests) == 5
    assert messages[-1]["type"] == "error"
