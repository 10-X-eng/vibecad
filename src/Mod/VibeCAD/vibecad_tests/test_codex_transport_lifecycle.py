# SPDX-License-Identifier: LGPL-2.1-or-later

"""Codex stdio lifecycle regressions independent of a live provider."""

from __future__ import annotations

import json
import threading
import time
from typing import Any

import pytest

import VibeCADCodex as codex


class _InputPipe:
    def __init__(self) -> None:
        self.closed = False
        self.messages: list[str] = []

    def write(self, value: str) -> None:
        if self.closed:
            raise BrokenPipeError("closed test pipe")
        self.messages.append(value)

    def flush(self) -> None:
        if self.closed:
            raise BrokenPipeError("closed test pipe")

    def close(self) -> None:
        self.closed = True


class _Process:
    def __init__(self) -> None:
        self.stdin = _InputPipe()
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        assert self.returncode is not None
        return self.returncode

    def kill(self) -> None:
        self.returncode = -9


def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            raise AssertionError("Timed out waiting for Codex transport state.")
        time.sleep(0.005)


@pytest.mark.parametrize("handler_fails", [False, True])
def test_close_discards_late_server_request_reply_without_thread_exception(
    monkeypatch: pytest.MonkeyPatch,
    handler_fails: bool,
) -> None:
    started = threading.Event()
    release = threading.Event()
    uncaught: list[BaseException] = []

    def handler(_method: str, _params: dict[str, Any]) -> dict[str, bool]:
        started.set()
        assert release.wait(2.0)
        if handler_fails:
            raise RuntimeError("late tool failure")
        return {"ok": True}

    monkeypatch.setattr(
        threading,
        "excepthook",
        lambda args: uncaught.append(args.exc_value),
    )
    client = codex.CodexAppServerClient(server_request_handler=handler)
    process = _Process()
    client._process = process

    client._dispatch_message(
        {
            "id": 7,
            "method": "item/tool/call",
            "params": {"tool": "conversation.ask_user"},
        }
    )
    assert started.wait(1.0)

    before = time.monotonic()
    client.close(reason="test cancellation during interactive tool")
    elapsed = time.monotonic() - before
    release.set()
    _wait_until(
        lambda: client.shutdown_details["active_server_request_count"] == 0
    )

    assert elapsed < 1.0
    assert uncaught == []
    assert process.stdin.messages == []
    assert client.shutdown_details == {
        "closed": True,
        "reason": "test cancellation during interactive tool",
        "process_exit_code": -15,
        "active_server_request_count": 0,
        "late_server_response_count": 1,
        "discarded_server_request_count": 0,
        "stderr_tail": [
            "Discarded late item/tool/call response after transport shutdown."
        ],
    }


def test_live_server_request_failure_sends_one_error_reply() -> None:
    def handler(_method: str, _params: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("question UI failed")

    client = codex.CodexAppServerClient(server_request_handler=handler)
    process = _Process()
    client._process = process

    client._dispatch_message(
        {"id": 9, "method": "item/tool/call", "params": {}}
    )
    _wait_until(lambda: bool(process.stdin.messages))
    _wait_until(
        lambda: client.shutdown_details["active_server_request_count"] == 0
    )

    messages = [
        json.loads(line)
        for payload in process.stdin.messages
        for line in payload.splitlines()
        if line
    ]
    assert messages == [
        {
            "id": 9,
            "error": {"code": -32000, "message": "question UI failed"},
        }
    ]
    assert client.shutdown_details["late_server_response_count"] == 0
    client.close(reason="test complete")


def test_managed_shutdown_does_not_wait_for_active_turn_lease() -> None:
    entered = threading.Event()
    closed = threading.Event()
    released = threading.Event()

    class _ManagedClient:
        def __init__(
            self,
            *,
            notification_handler,
            server_request_handler,
            environment=None,
        ) -> None:
            del environment
            self.notification_handler = notification_handler
            self.server_request_handler = server_request_handler
            self.alive = True

        def start(self) -> None:
            return None

        def set_handlers(
            self,
            *,
            notification_handler,
            server_request_handler,
        ) -> None:
            self.notification_handler = notification_handler
            self.server_request_handler = server_request_handler

        def close(self) -> None:
            self.alive = False
            closed.set()

    codex.shutdown_managed_codex_sessions()

    def hold_turn_lease() -> None:
        with codex.managed_codex_session(
            runtime_key="shutdown-runtime",
            thread_key="shutdown-thread",
            client_factory=_ManagedClient,
            notification_handler=lambda _method, _params: None,
            server_request_handler=lambda _method, _params: {},
        ):
            entered.set()
            assert closed.wait(2.0)
        released.set()

    worker = threading.Thread(target=hold_turn_lease, daemon=True)
    worker.start()
    assert entered.wait(1.0)

    before = time.monotonic()
    codex.shutdown_managed_codex_sessions()
    elapsed = time.monotonic() - before

    assert elapsed < 0.5
    assert closed.is_set()
    assert released.wait(1.0)
    worker.join(timeout=1.0)
    assert not worker.is_alive()
