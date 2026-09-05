# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cumulative request budgeting without changing signed tool conversations."""

import json
import sys
from types import SimpleNamespace

import pytest
import VibeCADProvider as provider

from .test_provider_subprocess import _CollectingConnection, _SequenceAnthropicMessages


@pytest.mark.parametrize("engine", ["gemini", "anthropic"])
@pytest.mark.parametrize("budget", [100000, 0])
def test_productive_history_is_bounded_before_request(monkeypatch, engine, budget):
    requests = []
    context = {
        "modeling_surface": {"engine": "native"},
        "native_state": {"revision": 1},
        "provider_tool_schemas": [{
            "name": "state.read", "description": "Read",
            "parameters": {"type": "object", "properties": {"target": {"type": "string"}}},
        }],
        "_vibecad_provider_options": {"history_budget_bytes": budget},
    }
    class Connection(_CollectingConnection):
        def recv(self):
            state = json.loads(json.dumps(context))
            state["native_state"]["revision"] = len(requests) + 1
            state.pop("_vibecad_provider_options", None)
            return {"type": "tool_result", "context": state,
                    "result": {"ok": True, "revision": len(requests), "payload": "x" * 20000}}
    def response():
        n = len(requests)
        if n > 12:
            return SimpleNamespace(content=[SimpleNamespace(type="text", text="Done")], stop_reason="end_turn")
        return SimpleNamespace(content=[
            SimpleNamespace(type="thinking", thinking="reason", signature="signed"),
            SimpleNamespace(type="tool_use", id=f"call-{n}", name="state_read", input={"target": str(n)})
        ], stop_reason="tool_use")
    class Messages:
        def stream(self, **kwargs):
            requests.append(json.loads(json.dumps(kwargs)))
            return _SequenceAnthropicMessages([response()]).stream(**kwargs)
        def create(self, **kwargs):
            requests.append(json.loads(json.dumps(kwargs)))
            n = len(requests)
            call = SimpleNamespace(index=0, id=f"call-{n}", type="function",
                function=SimpleNamespace(name="state_read", arguments=json.dumps({"target": str(n)})),
                extra_content={"google": {"thought_signature": f"signed-{n}"}})
            return iter([SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="Done" if n > 12 else None,
                                      tool_calls=[] if n > 12 else [call]),
                finish_reason="stop" if n > 12 else "tool_calls")])])
    messages = Messages()
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        Anthropic=lambda **kw: SimpleNamespace(messages=messages),
        BadRequestError=type("BadRequestError", (Exception,), {})))
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(
        OpenAI=lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=messages), close=lambda: None)))
    monkeypatch.setattr(provider, "_validate_provider_wire_surface", lambda c: None)
    conn = Connection()
    child = provider._gemini_child_main if engine == "gemini" else provider._anthropic_child_main
    child(conn, "Keep the width exactly 25 mm.", context, "mock", "key", None, 1.0, 14, False)
    assert conn.messages[-1]["type"] == "done"
    assert conn.messages[-1]["final_output"] == "Done"
    assert len(requests) == 13
    sizes = [provider._provider_json_bytes(r) for r in requests]
    if budget:
        assert max(sizes) <= budget
        assert "vibecad_history_reference" in json.dumps(requests[-1])
        assert "Keep the width exactly 25 mm." in json.dumps(requests[-1])
        snapshot = json.loads(requests[-1]["messages"][-1]["content"])
        assert snapshot["vibecad_history_live_state"]["native_state"]["revision"] == 13
    else:
        assert sizes[-1] > 240000
    last = requests[-1]["messages"]
    if engine == "gemini":
        calls = [c for m in last for c in m.get("tool_calls", [])]
        results = [m["tool_call_id"] for m in last if m["role"] == "tool"]
        assert [c["id"] for c in calls] == results
        assert all(c["extra_content"]["google"]["thought_signature"] == f"signed-{i}"
                   for i, c in enumerate(calls, 1))
    else:
        blocks = [b for m in last if isinstance(m["content"], list) for b in m["content"]]
        assert [b["id"] for b in blocks if b["type"] == "tool_use"] == [
            b["tool_use_id"] for b in blocks if b["type"] == "tool_result"]
        assert all(b["signature"] == "signed" for b in blocks if b["type"] == "thinking")
    print(engine, budget, "requests", len(requests), "cumulative_bytes", sum(sizes), "largest_bytes", max(sizes))

@pytest.mark.parametrize("engine", ["gemini", "anthropic"])
@pytest.mark.parametrize("kind", ["failure", "pending", "source", "api", "nested_revision"])
def test_history_budget_preserves_critical_results(monkeypatch, engine, kind):
    protected = {"ok": True, "payload": "protected" * 2500}
    name = "state_read"
    if kind == "failure": protected["ok"] = False
    if kind == "pending": protected["job"] = {"id": "job-1", "status": "running"}
    if kind == "source": protected["source"] = "exact code" * 2500
    if kind == "api": name = "vibescript_read_api"
    if kind == "nested_revision":
        protected = {"ok": True, "payload": {"revision": 42, "data": "exact" * 4000}}
    payloads = [protected] + [{"ok": True, "data": "x" * 20000} for _ in range(4)]
    messages = [{"role": "user", "content": "Preserve every user constraint."}]
    for i, payload in enumerate(payloads):
        call_id = str(i)
        if engine == "gemini":
            messages.extend([
                {"role": "assistant", "tool_calls": [{"id": call_id, "function": {"name": name, "arguments": "{}"},
                                                      "extra_content": {"google": {"thought_signature": "signed"}}}]},
                {"role": "tool", "tool_call_id": call_id, "content": json.dumps(payload)},
            ])
        else:
            messages.extend([
                {"role": "assistant", "content": [{"type": "thinking", "thinking": "reason", "signature": "signed"},
                    {"type": "tool_use", "id": call_id, "name": name, "input": {}}]},
                {"role": "user", "content": [{"type": "tool_result", "tool_use_id": call_id, "content": json.dumps(payload)}]},
            ])
        name = "state_read"
    request = {"messages": messages, "tools": [{"name": "state_read"}]}
    original = json.loads(json.dumps(request))
    updated, accounting = provider._provider_budget_history(request, {
        "_vibecad_provider_options": {"history_budget_bytes": 130000},
        "native_state": {"revision": 99, "background_job": {"id": "live", "status": "running"}},
    }, provider=engine, state={}, output_reserve_tokens=8192)
    assert accounting["compacted_results"] > 0
    assert updated["messages"][2] == original["messages"][2]
    assert request == original  # No rewriting earlier captured requests.
    assert updated["messages"][0] == original["messages"][0]
    assert updated["tools"] == original["tools"]
    snapshot = json.loads(updated["messages"][-1]["content"])
    assert snapshot["vibecad_history_live_state"]["native_state"]["revision"] == 99


@pytest.mark.parametrize("part", ["messages", "system", "tools", "image", "source"])
def test_history_budget_counts_irreducible_payloads(part):
    request = {"messages": [{"role": "user", "content": "keep me"}]}
    if part == "messages":
        request["messages"][0]["content"] = "prompt" * 1000
    elif part == "image":
        request["messages"][0]["content"] = [{"type": "image", "source": {"type": "base64", "data": "a" * 6000}}]
    elif part == "source":
        request["messages"].extend([
            {"role": "assistant", "tool_calls": [{"id": "read", "function": {"name": "read_source", "arguments": "{}"}}]},
            {"role": "tool", "tool_call_id": "read", "content": json.dumps({"ok": True, "source": "exact" * 1500})},
        ])
    else:
        request[part] = [{"text": "required" * 1000}]
    original = json.loads(json.dumps(request))
    with pytest.raises(provider._ProviderHistoryBudgetExceeded) as error:
        provider._provider_budget_history(request, {
            "_vibecad_provider_options": {"history_budget_bytes": 1024}
        }, provider="gemini", state={}, output_reserve_tokens=8192)
    assert error.value.accounting["request_json_bytes"] > 1024
    assert request == original


def test_history_context_window_reserves_output():
    request = {"messages": [{"role": "user", "content": "x" * 5000}]}
    with pytest.raises(provider._ProviderHistoryBudgetExceeded) as error:
        provider._provider_budget_history(request, {
            "_vibecad_provider_options": {"history_budget_bytes": 100000, "context_window_tokens": 9000}
        }, provider="anthropic", state={}, output_reserve_tokens=8192)
    accounting = error.value.accounting
    assert accounting["history_limit_bytes"] == (9000 - 8192) * 4
    assert accounting["estimated_total_tokens"] == accounting["estimated_input_tokens"] + 8192
    assert "measured" not in accounting["estimator"]


@pytest.mark.parametrize("engine", ["gemini", "anthropic"])
def test_oversized_input_stops_before_generation(monkeypatch, engine):
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        raise AssertionError("oversized request reached the model")
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(
        OpenAI=lambda **kw: SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None)))
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        Anthropic=lambda **kw: SimpleNamespace(messages=SimpleNamespace(stream=create)),
        BadRequestError=type("BadRequestError", (Exception,), {})))
    monkeypatch.setattr(provider, "_validate_provider_wire_surface", lambda c: None)
    conn = _CollectingConnection()
    child = provider._gemini_child_main if engine == "gemini" else provider._anthropic_child_main
    child(conn, "required user instruction" * 1000, {
        "provider_tool_schemas": [],
        "_vibecad_provider_options": {"history_budget_bytes": 1024},
    }, "mock", "key", None, 1.0, 2, False)
    assert requests == []
    assert conn.messages[-1]["type"] == "done"
    assert conn.messages[-1]["raw"]["reason"] == "input_budget"
    assert conn.closed


def test_anthropic_parent_preserves_history_configuration(monkeypatch):
    captured = []
    def run(**kwargs):
        captured.append(kwargs["context"]["_vibecad_provider_options"])
        return provider.ProviderResult(final_output="done", raw=None)
    monkeypatch.setattr(provider, "_run_provider_subprocess", run)
    provider.AnthropicProvider().run("hello", {
        "_vibecad_provider_options": {"history_budget_bytes": 100000, "context_window_tokens": 200000}
    })
    assert captured[0]["history_budget_bytes"] == 100000
    assert captured[0]["context_window_tokens"] == 200000

def test_gemini_available_usage_is_separate_from_budget_estimates(monkeypatch):
    requests = []
    def create(**kwargs):
        requests.append(kwargs)
        return iter([
            SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="Done", tool_calls=[]), finish_reason="stop")]),
            SimpleNamespace(choices=[], usage={"prompt_tokens": 321, "completion_tokens": 12, "total_tokens": 333}),
        ])
    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(
        OpenAI=lambda **kw: SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=create)), close=lambda: None)))
    monkeypatch.setattr(provider, "_validate_provider_wire_surface", lambda c: None)
    conn = _CollectingConnection()
    provider._gemini_child_main(conn, "hello", {"provider_tool_schemas": []},
                                "mock", "key", None, 1.0, 2, False)
    assert len(requests) == 1
    events = [m["event"] for m in conn.messages if m.get("type") == "progress"]
    completed = next(e for e in events if e["event"] == "gemini_stream_completed")
    assert completed["token_usage"] == {"prompt_tokens": 321, "completion_tokens": 12, "total_tokens": 333}
    budget = next(e for e in events if e["event"] == "provider_history_budget")
    assert "estimated_input_tokens" in budget
    assert "token_usage" not in budget
