# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for provider-native research and isolated design review."""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import VibeCADCodex as codex
import VibeCADDesignReview as design_review
import VibeCADIntentMemoryCompiler as intent_compiler
import VibeCADProvider as provider


def _review(*, verdict: str = "ready", severity: str | None = None) -> dict:
    findings = []
    if severity:
        findings.append(
            {
                "severity": severity,
                "category": "interfaces",
                "issue": "The mating interface is undefined.",
                "consequence": "The components cannot be assembled reliably.",
                "required_change": "Define datums, fit, and retained clearances.",
            }
        )
    return {
        "verdict": verdict,
        "summary": "The proposal is internally coherent.",
        "strengths": ["The component boundaries are explicit."],
        "findings": findings,
        "required_revisions": [],
        "questions_for_user": [],
    }


class _StructuredCodexClient:
    payload: dict = {}
    instance = None

    def __init__(
        self,
        *,
        notification_handler,
        server_request_handler,
        environment=None,
    ) -> None:
        self.notification_handler = notification_handler
        self.server_request_handler = server_request_handler
        self.environment = dict(environment or {})
        self.requests: list[tuple[str, dict]] = []
        self.tool_name = ""
        self.alive = True
        _StructuredCodexClient.instance = self

    def start(self) -> None:
        return None

    def request(self, method: str, params: dict, timeout: float) -> dict:
        self.requests.append((method, dict(params)))
        if method == "thread/start":
            self.tool_name = params["dynamicTools"][0]["name"]
            return {"thread": {"id": "thread-1"}, "model": "gpt-test"}
        if method == "turn/start":
            result = self.server_request_handler(
                "item/tool/call",
                {
                    "namespace": None,
                    "tool": self.tool_name,
                    "arguments": dict(self.payload),
                },
            )
            assert result["success"] is True
            self.notification_handler(
                "turn/completed",
                {
                    "threadId": "thread-1",
                    "turn": {"id": "turn-1", "status": "completed"},
                },
            )
            return {"turn": {"id": "turn-1"}}
        if method == "thread/delete":
            return {}
        raise AssertionError(method)

    def close(self) -> None:
        self.alive = False


def test_anthropic_web_search_uses_direct_current_server_tool() -> None:
    cad_tool = {"name": "vibecad_test", "input_schema": {"type": "object"}}
    assert provider._anthropic_request_tools([cad_tool], False) == [cad_tool]
    assert provider._anthropic_request_tools([cad_tool], True) == [
        cad_tool,
        {
            "type": "web_search_20260318",
            "name": "web_search",
            "max_uses": 5,
            "allowed_callers": ["direct"],
        },
    ]


def test_anthropic_citations_are_rendered_as_clickable_markdown_sources() -> None:
    block = SimpleNamespace(
        type="text",
        text="Use the current material datasheet.",
        model_dump=lambda **_kwargs: {
            "type": "text",
            "text": "Use the current material datasheet.",
            "citations": [
                {
                    "type": "web_search_result_location",
                    "url": "https://example.com/material",
                    "title": "Material datasheet",
                }
            ],
        },
    )
    text = provider._anthropic_final_text([block])
    assert "[Material datasheet](https://example.com/material)" in text


def test_anthropic_server_tool_blocks_round_trip_without_losing_state() -> None:
    block = SimpleNamespace(
        type="web_search_tool_result",
        model_dump=lambda **_kwargs: {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://example.com",
                    "encrypted_content": "opaque",
                }
            ],
        },
    )
    assert provider._anthropic_assistant_request_content([block]) == [
        {
            "type": "web_search_tool_result",
            "tool_use_id": "srvtoolu_1",
            "content": [
                {
                    "type": "web_search_result",
                    "url": "https://example.com",
                    "encrypted_content": "opaque",
                }
            ],
        }
    ]


def test_design_review_rejects_a_false_ready_verdict() -> None:
    with pytest.raises(RuntimeError, match="blocking or major"):
        design_review._validate_review(_review(verdict="ready", severity="major"))


def test_design_review_accepts_structured_revision_findings() -> None:
    review = _review(verdict="revise", severity="blocking")
    assert design_review._validate_review(review) == review


def test_anthropic_review_schema_removes_only_unsupported_constraints() -> None:
    compiled = design_review._anthropic_strict_schema(
        design_review.REVIEW_RESULT_SCHEMA
    )
    assert "maxItems" not in str(compiled)
    assert "minLength" not in str(compiled)
    assert compiled["required"] == design_review.REVIEW_RESULT_SCHEMA["required"]
    assert compiled["properties"]["verdict"]["enum"] == ["ready", "revise"]


def test_design_review_prompt_contains_only_review_inputs_and_live_facts() -> None:
    prompt = design_review._review_prompt(
        "Create a manufacturable impeller with a retained shaft interface.",
        "A revolved hub carries separately authored full and splitter blades. "
        "Blade roots overlap the hub and every repeated interface is verified.",
        {
            "cad_state": {"document": "Impeller"},
            "conversation": {"conversation": [{"content": "not duplicated"}]},
        },
    )
    assert '"customer_intent"' in prompt
    assert '"design_draft"' in prompt
    assert '"cad_state"' in prompt
    assert "not duplicated" not in prompt


def test_openai_design_review_runs_through_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _StructuredCodexClient.payload = _review()
    monkeypatch.setattr(codex, "CodexAppServerClient", _StructuredCodexClient)

    result = design_review.run_design_review(
        provider="openai",
        model="gpt-test",
        api_key="selected-key",
        base_url="https://api.example.test/v1",
        reasoning_effort="high",
        customer_intent="Make a bracket.",
        design_draft="Use a bent plate with two mounting holes.",
        context={},
    )

    client = _StructuredCodexClient.instance
    assert result == _review()
    assert client.environment == {
        codex.CODEX_OPENAI_API_KEY_ENV: "selected-key"
    }
    assert all(method != "account/read" for method, _params in client.requests)
    request = next(
        params for method, params in client.requests if method == "thread/start"
    )
    assert request["modelProvider"] == codex.CODEX_OPENAI_PROVIDER_ID
    assert "selected-key" not in json.dumps(request)


def test_openai_intent_memory_compiler_runs_through_codex(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    update = {
        "base_revision": "0" * 64,
        "turn_dispositions": [
            {
                "turn_id": "turn-1",
                "durable": False,
                "entry_ids": [],
            }
        ],
        "upserts": [],
        "supersessions": [],
    }
    _StructuredCodexClient.payload = update
    monkeypatch.setattr(codex, "CodexAppServerClient", _StructuredCodexClient)

    result = intent_compiler.compile_intent_memory_update(
        provider="openai",
        model="gpt-test",
        api_key="selected-key",
        base_url="https://api.example.test/v1",
        memory={"exists": False, "revision": "0" * 64, "entries": []},
        uncovered_turns=[
            {
                "turn_id": "turn-1",
                "role": "user",
                "content": "That was just a status question.",
            }
        ],
    )

    client = _StructuredCodexClient.instance
    assert result == update
    assert client.environment == {
        codex.CODEX_OPENAI_API_KEY_ENV: "selected-key"
    }
    assert all(method != "account/read" for method, _params in client.requests)
    request = next(
        params for method, params in client.requests if method == "thread/start"
    )
    assert request["modelProvider"] == codex.CODEX_OPENAI_PROVIDER_ID
    assert "selected-key" not in json.dumps(request)
