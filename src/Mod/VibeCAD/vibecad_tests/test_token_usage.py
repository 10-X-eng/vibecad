# SPDX-License-Identifier: LGPL-2.1-or-later

"""Red/green contracts for OpenAI/Codex token usage accounting."""

from __future__ import annotations

import json
import time

import pytest

import VibeCADCodex as codex
import VibeCADCodexResponses as codex_responses
import VibeCADOllama as ollama
import VibeCADProvider as provider
import VibeCADSession as session
from VibeCADProject import VibeCADConversationStore
from VibeCADTokenUsage import (
    TokenUsageAccumulator,
    format_usage_summary,
    normalize_codex_usage,
    summarize_conversation_usage,
)


def _event(
    *,
    thread_id: str = "thread-1",
    turn_id: str = "turn-1",
    last: dict | None = None,
    total: dict | None = None,
) -> dict:
    return {
        "threadId": thread_id,
        "turnId": turn_id,
        "tokenUsage": {
            "last": last or {},
            "total": total or {},
        },
    }


def _surface_context() -> dict:
    schema = {
        "name": "core.set_view",
        "description": "Set the view.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }
    surface = session._turn_start_tool_surface("PartDesignWorkbench", [schema])
    return {
        "provider_tool_schemas": [schema],
        "provider_tool_surface": surface,
        "modeling_surface": {
            key: surface[key]
            for key in (
                "workbench",
                "engine",
                "domain",
                "surface_id",
                "available",
                "unavailable_reason",
            )
        },
    }


@pytest.mark.parametrize("auth_mode", ("api_key", "chatgpt"))
def test_codex_provider_captures_usage_for_both_authentication_paths(
    monkeypatch: pytest.MonkeyPatch,
    auth_mode: str,
) -> None:
    monkeypatch.setattr(
        codex_responses,
        "codex_responses_base_url",
        lambda value: value,
    )
    monkeypatch.setattr(
        ollama,
        "inspect_model",
        lambda *_args, **_kwargs: {"detected": False, "ok": True},
    )

    class _Client:
        def __init__(self, *, notification_handler, server_request_handler, environment=None):
            self.notification_handler = notification_handler
            self.server_request_handler = server_request_handler
            self.environment = dict(environment or {})
            self.alive = True
            self.events = []

        @property
        def stderr_tail(self):
            return []

        def start(self):
            return None

        def request(self, method, params, timeout):
            del timeout
            if method == "account/read":
                return {"account": {"type": "chatgpt"}}
            if method == "thread/start":
                return {"thread": {"id": "thread-1"}}
            if method == "turn/start":

                def emit():
                    time.sleep(0.02)
                    self.notification_handler(
                        "thread/tokenUsage/updated",
                        _event(
                            last={
                                "inputTokens": 100,
                                "cachedInputTokens": 25,
                                "outputTokens": 40,
                                "reasoningOutputTokens": 10,
                                "totalTokens": 140,
                            },
                            total={
                                "inputTokens": 100,
                                "cachedInputTokens": 25,
                                "outputTokens": 40,
                                "reasoningOutputTokens": 10,
                                "totalTokens": 140,
                            },
                        ),
                    )
                    self.notification_handler(
                        "item/completed",
                        {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "item": {"type": "agentMessage", "text": "Done."},
                        },
                    )
                    self.notification_handler(
                        "turn/completed",
                        {
                            "threadId": "thread-1",
                            "turnId": "turn-1",
                            "turn": {"id": "turn-1", "status": "completed"},
                        },
                    )

                import threading

                threading.Thread(target=emit, daemon=True).start()
                return {"turn": {"id": "turn-1"}}
            if method == "thread/delete":
                return {}
            raise AssertionError(method)

        def close(self):
            self.alive = False

    monkeypatch.setattr(codex, "CodexAppServerClient", _Client)
    events = []
    active_provider = provider.CodexProvider(
        model="gpt-test",
        api_key="test-key" if auth_mode == "api_key" else None,
        auth_mode=auth_mode,
        base_url="https://api.openai.com/v1" if auth_mode == "api_key" else None,
    )

    result = active_provider.run(
        "Set the view.",
        _surface_context(),
        progress_callback=events.append,
    )

    assert result.final_output == "Done."
    assert result.raw["usage"]["auth_mode"] == auth_mode
    assert result.raw["usage"]["turn"]["input_tokens"] == 100
    assert result.raw["usage"]["thread_total"]["total_tokens"] == 140
    assert any(event.get("event") == "provider_usage" for event in events)


@pytest.mark.parametrize("auth_mode", ("api_key", "chatgpt"))
def test_codex_authentication_paths_keep_reported_usage_separate(auth_mode: str) -> None:
    accumulator = TokenUsageAccumulator(
        provider="openai" if auth_mode == "api_key" else "chatgpt",
        auth_mode=auth_mode,
    )
    accumulator.set_thread_id("thread-1")
    accumulator.set_active_turn("turn-1")

    report = accumulator.observe(
        _event(
            last={
                "inputTokens": 100,
                "cachedInputTokens": 25,
                "outputTokens": 40,
                "reasoningOutputTokens": 10,
                "totalTokens": 140,
            },
            total={
                "inputTokens": 100,
                "cachedInputTokens": 25,
                "outputTokens": 40,
                "reasoningOutputTokens": 10,
                "totalTokens": 140,
            },
        )
    )

    assert report is not None
    assert report["auth_mode"] == auth_mode
    assert report["turn"]["input_tokens"] == 100
    assert report["turn"]["cached_input_tokens"] == 25
    assert report["turn"]["output_tokens"] == 40
    assert report["turn"]["reasoning_output_tokens"] == 10
    assert report["thread_total"]["total_tokens"] == 140


def test_missing_usage_fields_are_unknown_and_are_not_zero() -> None:
    normalized = normalize_codex_usage(
        {
            "last": {"inputTokens": 100, "outputTokens": 12},
            "total": {"inputTokens": 100, "outputTokens": 12},
        }
    )

    assert normalized["last"]["input_tokens"] == 100
    assert normalized["last"]["cached_input_tokens"] is None
    assert normalized["last"]["reasoning_output_tokens"] is None
    assert normalized["last"]["total_tokens"] is None
    missing = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    missing.set_thread_id("thread-1")
    missing.set_active_turn("turn-1")
    missing.observe(
        _event(
            last={"inputTokens": 100, "outputTokens": 12},
            total={"inputTokens": 100, "outputTokens": 12},
        )
    )
    rendered = format_usage_summary(
        summarize_conversation_usage(
            [
                {
                    "role": "assistant",
                    "content": "done",
                    "metadata": {"usage": missing.metadata(status="completed")},
                }
            ]
        )
    )
    assert "unknown" in rendered
    assert "cached input 0" not in rendered


def test_duplicate_stream_notifications_do_not_double_count() -> None:
    accumulator = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    accumulator.set_thread_id("thread-1")
    accumulator.set_active_turn("turn-1")
    event = _event(
        last={"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
        total={"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
    )

    assert accumulator.observe(event) is not None
    assert accumulator.observe(event) is None
    report = accumulator.metadata(status="completed")
    assert report["turn"]["input_tokens"] == 100
    assert report["thread_total"]["input_tokens"] == 100


def test_cumulative_thread_counters_are_not_added_to_turn_counters() -> None:
    accumulator = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    accumulator.set_thread_id("thread-1")
    accumulator.set_active_turn("turn-1")
    accumulator.observe(
        _event(
            last={"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
            total={"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
        )
    )
    accumulator.set_active_turn("turn-2")
    accumulator.observe(
        _event(
            turn_id="turn-2",
            last={"inputTokens": 50, "outputTokens": 10, "totalTokens": 60},
            total={"inputTokens": 150, "outputTokens": 30, "totalTokens": 180},
        )
    )

    conversation = [
        {
            "role": "assistant",
            "content": "one",
            "metadata": {"usage": accumulator.metadata(status="completed")},
        },
    ]
    # The accumulator report is the second turn; explicitly retain both
    # snapshots to exercise the conversation-level cumulative rule.
    first = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    first.set_thread_id("thread-1")
    first.set_active_turn("turn-1")
    first.observe(
        _event(
            last={"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
            total={"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
        )
    )
    conversation.insert(
        0,
        {
            "role": "assistant",
            "content": "zero",
            "metadata": {"usage": first.metadata(status="completed")},
        },
    )
    summary = summarize_conversation_usage(conversation)
    assert summary["totals"]["input_tokens"] == 150
    assert summary["totals"]["output_tokens"] == 30


def test_retries_and_resumed_thread_events_use_one_logical_turn() -> None:
    accumulator = TokenUsageAccumulator(provider="chatgpt", auth_mode="chatgpt")
    accumulator.set_thread_id("thread-1")
    # This is a restored cumulative snapshot received during thread/resume.
    assert (
        accumulator.observe(
            _event(
                turn_id="old-turn",
                last={"inputTokens": 80, "outputTokens": 10, "totalTokens": 90},
                total={"inputTokens": 800, "outputTokens": 100, "totalTokens": 900},
            )
        )
        is None
    )
    accumulator.set_active_turn("turn-2")
    current = _event(
        turn_id="turn-2",
        last={"inputTokens": 70, "outputTokens": 15, "totalTokens": 85},
        total={"inputTokens": 870, "outputTokens": 115, "totalTokens": 985},
    )
    assert accumulator.observe(current) is not None
    assert accumulator.observe(current) is None
    # A retry/replay with a lower cumulative counter cannot lower or add to it.
    accumulator.observe(
        _event(
            turn_id="turn-2",
            last={"inputTokens": 70, "outputTokens": 15, "totalTokens": 85},
            total={"inputTokens": 800, "outputTokens": 100, "totalTokens": 900},
        )
    )
    report = accumulator.metadata(status="cancelled")
    assert report["turn"]["total_tokens"] == 85
    assert report["thread_total"]["total_tokens"] == 985
    assert report["complete"] is False


@pytest.mark.parametrize("status", ("failed", "cancelled"))
def test_failed_or_cancelled_usage_is_persisted_without_source_content(
    tmp_path,
    status: str,
) -> None:
    store = VibeCADConversationStore(tmp_path)
    history = store.active_history()
    usage = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    usage.set_thread_id("thread-1")
    usage.set_active_turn("turn-1")
    usage.observe(
        _event(
            last={"inputTokens": 7, "outputTokens": 3},
            total={"inputTokens": 7, "outputTokens": 3},
        )
    )
    unsafe = usage.metadata(status=status)
    unsafe["prompt"] = "do not persist"
    unsafe["images"] = ["secret.png"]
    unsafe["credentials"] = "sk-secret"
    unsafe["source_content"] = "private source"
    saved = store.write_conversation(
        history["conversation_id"],
        [
            {
                "role": "system",
                "content": "The request failed.",
                "metadata": {"usage": unsafe},
            }
        ],
    )
    stored_usage = saved["conversation"][0]["metadata"]["usage"]
    assert stored_usage["status"] == status
    assert stored_usage["turn"]["input_tokens"] == 7
    assert "prompt" not in stored_usage
    assert "images" not in stored_usage
    assert "credentials" not in stored_usage
    assert "source_content" not in stored_usage
    json.dumps(saved)


def test_usage_summary_labels_actual_counts_and_keeps_bytes_estimate_separate() -> None:
    accumulator = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    accumulator.set_thread_id("thread-1")
    accumulator.set_active_turn("turn-1")
    accumulator.observe(
        _event(
            last={
                "inputTokens": 1_000,
                "cachedInputTokens": 300,
                "outputTokens": 200,
                "reasoningOutputTokens": 80,
                "totalTokens": 1_200,
            },
            total={
                "inputTokens": 1_000,
                "cachedInputTokens": 300,
                "outputTokens": 200,
                "reasoningOutputTokens": 80,
                "totalTokens": 1_200,
            },
        )
    )
    summary = format_usage_summary(
        summarize_conversation_usage(
            [
                {
                    "role": "assistant",
                    "content": "done",
                    "metadata": {"usage": accumulator.metadata(status="completed")},
                }
            ]
        )
    )
    assert "Actual provider-reported tokens" in summary
    assert "bytes/4 estimate is not included" in summary
    assert "cached input 300" in summary
    assert "reasoning 80" in summary
    assert "1,500" not in summary


def test_usage_summary_sums_per_turn_counts_when_thread_total_is_unavailable() -> None:
    first = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    first.set_thread_id("ephemeral-thread-1")
    first.set_active_turn("turn-1")
    first.observe(
        _event(
            thread_id="ephemeral-thread-1",
            last={
                "inputTokens": 100,
                "cachedInputTokens": 0,
                "outputTokens": 20,
                "reasoningOutputTokens": 0,
                "totalTokens": 120,
            },
        )
    )
    second = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    second.set_thread_id("ephemeral-thread-2")
    second.set_active_turn("turn-2")
    second.observe(
        _event(
            thread_id="ephemeral-thread-2",
            turn_id="turn-2",
            last={
                "inputTokens": 50,
                "cachedInputTokens": 0,
                "outputTokens": 10,
                "reasoningOutputTokens": 0,
                "totalTokens": 60,
            },
        )
    )

    summary = summarize_conversation_usage(
        [
            {
                "role": "assistant",
                "content": "one",
                "metadata": {"usage": first.metadata(status="completed")},
            },
            {
                "role": "assistant",
                "content": "two",
                "metadata": {"usage": second.metadata(status="completed")},
            },
        ]
    )

    assert summary["totals"]["input_tokens"] == 150
    assert summary["totals"]["output_tokens"] == 30
    assert summary["totals"]["total_tokens"] == 180
    assert summary["complete"] is True


def test_usage_summary_breaks_down_each_model_using_per_turn_counts() -> None:
    first = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    first.set_model("gpt-5.6-sol", source="transport")
    first.set_thread_id("thread-1")
    first.set_active_turn("turn-1")
    first.observe(
        _event(
            last={"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
            total={"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
        )
    )
    second = TokenUsageAccumulator(provider="openai", auth_mode="api_key")
    second.set_model("gpt-5.6-sol", source="transport")
    second.set_thread_id("thread-1")
    second.set_active_turn("turn-2")
    second.observe(
        _event(
            turn_id="turn-2",
            last={"inputTokens": 50, "outputTokens": 10, "totalTokens": 60},
            total={"inputTokens": 150, "outputTokens": 30, "totalTokens": 180},
        )
    )

    summary = summarize_conversation_usage(
        [
            {
                "role": "assistant",
                "content": "one",
                "metadata": {"usage": first.metadata(status="completed")},
            },
            {
                "role": "assistant",
                "content": "two",
                "metadata": {"usage": second.metadata(status="completed")},
            },
        ]
    )

    assert summary["models"]["gpt-5.6-sol"]["input_tokens"] == 150
    assert summary["models"]["gpt-5.6-sol"]["output_tokens"] == 30
    rendered = format_usage_summary(summary)
    assert "By model:" in rendered
    assert "gpt-5.6-sol (transport)" in rendered


def test_usage_graph_data_is_empty_without_actual_usage() -> None:
    from VibeCADTokenUsage import usage_graph_data


    graph = usage_graph_data({"has_usage": False})


    assert graph == {"has_usage": False, "complete": False, "rows": []}






def test_usage_graph_data_preserves_conversation_and_model_counts() -> None:
    from VibeCADTokenUsage import usage_graph_data


    summary = {
        "has_usage": True,
        "complete": False,
        "totals": {
            "input_tokens": 300,
            "cached_input_tokens": 200,
            "output_tokens": 40,
            "reasoning_output_tokens": None,
            "total_tokens": 340,
        },
        "models": {
            "gpt-6-astra": {
                "input_tokens": 300,
                "cached_input_tokens": 200,
                "output_tokens": 40,
                "reasoning_output_tokens": None,
                "total_tokens": 340,
                "model_source": "transport",
            }
        },
        "turns": [],
    }


    graph = usage_graph_data(summary)


    assert graph["has_usage"] is True
    assert graph["complete"] is False
    assert [row["label"] for row in graph["rows"]] == [
        "Conversation",
        "gpt-6-astra (transport)",
    ]
    assert graph["rows"][0]["counts"]["cached_input_tokens"] == 200
    assert graph["rows"][0]["counts"]["reasoning_output_tokens"] is None
