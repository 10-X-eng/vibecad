# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regressions at the managed provider's outbound request boundary."""

import copy
import json
import sys
from types import SimpleNamespace

import pytest

import VibeCADCodex as codex
import VibeCADProvider as provider
import VibeCADSession as session


@pytest.fixture
def wire(monkeypatch):
    class Client:
        instances = []
        requests = []
        on_turn = None
        catalog = {
            "data": [{
                "id": "selected-model",
                "supportedReasoningEfforts": [
                    {"reasoningEffort": effort} for effort in ("low", "medium", "high")
                ],
            }]
        }

        def __init__(self, *, notification_handler, server_request_handler, environment=None):
            self.notification_handler = notification_handler
            self.alive = True
            self.stderr_tail = []
            self.instances.append(self)

        def start(self):
            pass

        def close(self):
            self.alive = False

        def set_handlers(self, *, notification_handler, server_request_handler):
            self.notification_handler = notification_handler

        def request(self, method, params, timeout):
            self.requests.append((method, copy.deepcopy(params)))
            if method in {"thread/start", "thread/resume"}:
                return {"thread": {"id": "thread"}, "model": "selected-model"}
            if method == "model/list":
                return self.catalog
            if method == "turn/start":
                number = sum(name == "turn/start" for name, _ in self.requests)
                turn_id = f"turn-{number}"
                if type(self).on_turn is not None:
                    type(self).on_turn(self)
                self.notification_handler("item/completed", {
                    "threadId": "thread", "turnId": turn_id,
                    "item": {"type": "agentMessage", "text": "Done."},
                })
                self.notification_handler("turn/completed", {
                    "threadId": "thread", "turnId": turn_id,
                    "turn": {"id": turn_id, "status": "completed"},
                })
                return {"turn": {"id": turn_id}}
            return {}

    codex.reset_managed_codex_sessions()
    monkeypatch.setattr(codex, "CodexAppServerClient", Client)
    yield Client
    codex.reset_managed_codex_sessions()


def context():
    schemas = [{"name": "vibescript.read_source", "description": "Read exact source.",
                "parameters": {"type": "object", "properties": {}, "additionalProperties": False}}]
    surface = session._turn_start_tool_surface("PartWorkbench", schemas)
    result = {"provider_tool_schemas": schemas, "provider_tool_surface": surface,
              "modeling_surface": {key: surface[key] for key in (
                  "workbench", "engine", "domain", "surface_id", "available", "unavailable_reason"
              )}}
    result["document"] = {"name": "Part", "revision": 1, "notes": "stable-" + "x" * 2500}
    result["_vibecad_codex_session"] = {"conversation_id": "efficiency-regression"}
    return result


def run(wire, ctx, message="Show the current status.", *, history=None, adaptive=False):
    selected = provider.CodexProvider(
        model="selected-model", api_key="fake", auth_mode="api_key",
        reasoning_effort="high", adaptive_reasoning=adaptive,
    )
    result = selected.run(session._provider_prompt(message, ctx, recent_conversation=history), ctx)
    request = next(params for method, params in reversed(wire.requests) if method == "turn/start")
    return result, request


def reference(request):
    text = next(item["text"] for item in request["input"] if item["type"] == "text")
    section = provider._provider_prompt_section_values(text)["active_state"]
    return json.loads(section).get("__vibecad_context_reference__")


def image_count(request):
    return sum(item["type"] in {"localImage", "image"} for item in request["input"])


def test_wire_references_keep_an_explicit_full_anchor(wire):
    ctx = context()
    turns = [run(wire, ctx)[1] for _ in range(5)]
    assert reference(turns[0]) is None
    for turn in turns[1:]:
        assert reference(turn)["anchor_turn_id"] == "turn-1"
        assert "previous successful turn" not in reference(turn)["instruction"]
    ctx["document"]["revision"] = 2
    assert reference(run(wire, ctx)[1]) is None
    assert reference(run(wire, ctx)[1])["anchor_turn_id"] == "turn-6"


def test_wire_ongoing_conversation_keeps_selected_effort(wire):
    ctx = context()
    run(wire, ctx, "Build a bracket.", adaptive=True)
    result, request = run(wire, ctx, adaptive=True, history=[
        {"role": "user", "content": "Build a bracket."},
        {"role": "assistant", "content": "Working on it."},
    ])
    assert request["effort"] == "high"
    assert result.raw["requested_reasoning_effort"] == "high"
    # A managed follow-up is ongoing even if the caller omits replayed history.
    assert run(wire, ctx, adaptive=True)[1]["effort"] == "high"


@pytest.mark.parametrize("message", [
    "Show status and delete Body.",
    "Check the stress results and explain whether the bracket is safe.",
    "Show how to rotate the part by 90 degrees.",
    "Show status; verify, then export.",
    "List the objects, then remove the last one.",
])
def test_wire_uncertain_and_complex_requests_keep_selected_effort(wire, message):
    result, request = run(wire, context(), message, adaptive=True)
    assert request["effort"] == "high"
    assert result.raw["model"] == "selected-model"


def test_wire_simple_first_turn_can_lower_one_level(wire):
    result, request = run(wire, context(), adaptive=True)
    assert request["effort"] == "medium"
    assert result.raw["requested_reasoning_effort"] == "high"
    assert result.raw["model"] == "selected-model"


def test_wire_missing_selected_model_capabilities_keeps_effort(wire):
    wire.catalog = {"data": [{"id": "other-model", "isDefault": True,
                             "supportedReasoningEfforts": [{"reasoningEffort": "medium"}]}]}
    assert run(wire, context(), adaptive=True)[1]["effort"] == "high"


def test_wire_reinspection_reattaches_without_internal_flags(wire, tmp_path):
    image = tmp_path / "part.png"
    image.write_bytes(b"reference image")
    ctx = context()
    ctx["reference_images"] = {"images": [{"id": "part", "path": str(image)}]}
    assert image_count(run(wire, ctx)[1]) == 1
    assert image_count(run(wire, ctx)[1]) == 0
    assert image_count(run(wire, ctx, "Please reinspect the reference image again.")[1]) == 1


def test_wire_unsafe_reference_is_never_committed_as_delivered(wire, tmp_path):
    image = tmp_path / "unsupported.txt"
    image.write_text("not an image")
    ctx = context()
    ctx["reference_images"] = {"images": [{"id": "unsafe", "path": str(image)}]}
    assert image_count(run(wire, ctx)[1]) == 0
    for runtime in codex._managed_codex_runtimes.values():
        assert not any(runtime.reference_image_deliveries.values())


def test_wire_changed_file_during_delivery_is_not_remembered(wire, tmp_path):
    image = tmp_path / "part.png"
    image.write_bytes(b"before")
    ctx = context()
    ctx["reference_images"] = {"images": [{"id": "part", "path": str(image)}]}
    wire.on_turn = lambda _client: image.write_bytes(b"during")
    run(wire, ctx)
    wire.on_turn = None
    image.write_bytes(b"before")
    assert image_count(run(wire, ctx)[1]) == 1


@pytest.mark.parametrize("provider_class", [provider.GeminiProvider, provider.AnthropicProvider])
def test_unknown_provider_model_never_assumes_reasoning_capabilities(monkeypatch, provider_class):
    captured = []
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        Anthropic=lambda **kwargs: SimpleNamespace(
            models=SimpleNamespace(retrieve=lambda model: {"id": model, "capabilities": None}),
            close=lambda: None,
        )
    ))
    monkeypatch.setattr(provider, "_run_provider_subprocess", lambda **kwargs: (
        captured.append(kwargs) or provider.ProviderResult(final_output="Done", raw={})
    ))
    result = provider_class(
        model="unknown-model", api_key="fake", adaptive_reasoning=True,
    ).run("Show the current status.", {})
    assert captured[0]["reasoning_effort"] == "high"
    assert result.raw["effective_reasoning_effort"] == "high"


def test_gemini_minimal_cannot_adapt_into_an_omitted_effort(monkeypatch):
    captured = []
    monkeypatch.setattr(provider, "_run_provider_subprocess", lambda **kwargs: (
        captured.append(kwargs) or provider.ProviderResult(final_output="Done", raw={})
    ))
    provider.GeminiProvider(
        model="gemini-2.5-flash", api_key="fake", reasoning_effort="minimal",
        adaptive_reasoning=True,
    ).run("Show the current status.", {})
    assert provider._provider_reasoning_effort(captured[0]["reasoning_effort"]) == "minimal"


def test_filtered_assembly_api_returns_only_requested_definitions():
    description = {
        "ok": True, "domain": "assembly", "workbench": "AssemblyWorkbench",
        "runtime_exports": [{"name": name} for name in ("component", "assembly", "solve")],
        "api_details": {name: {"signature": name + "()"} for name in ("component", "assembly", "solve")},
    }
    result = session._filtered_api_payload("vibescript.read_api", description, names=["component"], groups=[])
    assert result["runtime_exports"] == [{"name": "component"}]
    assert result["api_details"] == {"component": {"signature": "component()"}}


@pytest.mark.parametrize("state", [
    {"native_state": {"operations": [{"status": "running"}]}},
    {"editable_sources": {"sources": [{"status": "build_failed"}]}},
    {"native_state": {"last_result": {"ok": False, "error": "invalid shape"}}},
])
def test_wire_nested_unresolved_work_keeps_effort(wire, state):
    ctx = context()
    ctx.update(state)
    assert run(wire, ctx, adaptive=True)[1]["effort"] == "high"


@pytest.mark.parametrize("case", ["success", "missing", "failure", "malformed", "no-thinking", "custom"])
def test_anthropic_uses_selected_model_capabilities_and_fails_closed(monkeypatch, case):
    calls, requests, closed = [], [], []
    def retrieve(model):
        calls.append(model)
        if case == "failure":
            raise RuntimeError("metadata unavailable")
        if case == "missing":
            return {"id": model}
        if case == "malformed":
            return {"capabilities": "unexpected"}
        return {"id": model, "capabilities": {
            "effort": {"supported": True, "medium": {"supported": True}, "high": {"supported": True}},
            "thinking": {"types": {"adaptive": {"supported": case != "no-thinking"}}},
        }}
    monkeypatch.setitem(sys.modules, "anthropic", SimpleNamespace(
        Anthropic=lambda **kwargs: SimpleNamespace(models=SimpleNamespace(retrieve=retrieve),
                                                 close=lambda: closed.append(True))
    ))
    monkeypatch.setattr(provider, "_run_provider_subprocess", lambda **kwargs: (
        requests.append(kwargs) or provider.ProviderResult(final_output="Done", raw={})
    ))
    result = provider.AnthropicProvider(
        model="selected-anthropic-model", api_key="fake", adaptive_reasoning=True,
        base_url="http://localhost:1234" if case == "custom" else None,
    ).run("Show the current status.", {})
    expected = "medium" if case == "success" else "high"
    assert requests[0]["reasoning_effort"] == expected
    assert result.raw["effective_reasoning_effort"] == expected
    assert result.raw["model"] == "selected-anthropic-model"
    assert calls == ([] if case == "custom" else ["selected-anthropic-model"])
    assert closed == ([] if case == "custom" else [True])


@pytest.mark.parametrize("model,endpoint,expected", [
    ("gemini-2.5-flash", None, "medium"),
    ("gemini-flash-latest", None, "high"),
    ("unknown-model", None, "high"),
    ("gemini-2.5-flash", "http://localhost:1234", "high"),
])
def test_gemini_reduction_uses_only_known_model_endpoint_pairs(monkeypatch, model, endpoint, expected):
    captured = []
    monkeypatch.setattr(provider, "_run_provider_subprocess", lambda **kwargs: (
        captured.append(kwargs) or provider.ProviderResult(final_output="Done", raw={})
    ))
    result = provider.GeminiProvider(
        model=model, api_key="fake", base_url=endpoint, adaptive_reasoning=True,
    ).run("Show the current status.", {})
    assert captured[0]["reasoning_effort"] == expected
    assert result.raw["model"] == model


@pytest.mark.parametrize("identity,endpoint", [
    ("grok", "https://api.x.ai/v1"),
    ("openai", "http://localhost:1234/v1"),
    ("openai", "https://other.example/v1"),
])
def test_codex_catalog_is_not_used_for_other_providers(wire, identity, endpoint):
    provider.CodexProvider(
        model="selected-model", api_key="fake", auth_mode="api_key", identity_id=identity,
        base_url=endpoint, adaptive_reasoning=True,
    ).run(session._provider_prompt("Show the current status.", context()), context())
    assert not any(method == "model/list" for method, _ in wire.requests)
    assert next(params for method, params in wire.requests if method == "turn/start")["effort"] == "high"


def test_wire_client_restart_reanchors_text_and_images(wire, tmp_path):
    ctx = context()
    image = tmp_path / "part.png"
    image.write_bytes(b"reference")
    ctx["reference_images"] = {"images": [{"id": "part", "path": str(image)}]}
    run(wire, ctx)
    assert reference(run(wire, ctx)[1]) is not None
    wire.instances[-1].alive = False
    _, request = run(wire, ctx)
    assert reference(request) is None
    assert image_count(request) == 1


def test_production_api_filter_keeps_only_requested_names():
    service = SimpleNamespace(_active_document=lambda: None)
    result = session._run_universal_vibescript_tool(
        service, "AssemblyWorkbench", "vibescript.read_api",
        {"domain": "assembly", "names": ["component"]},
        document_thread_dispatch=None, cancellation_check=None, progress_callback=None,
    )
    visible = provider._provider_visible_tool_result(result, tool_name="vibescript.read_api")
    assert visible["ok"] is True
    assert [item["name"] for item in visible["runtime_exports"]] == ["component"]
    assert set(visible["api_details"]) == {"component"}


@pytest.mark.parametrize("selected,adaptive,expected", [
    ("high", True, "medium"), ("minimal", True, "minimal"), ("high", False, "high"),
])
def test_gemini_effort_reaches_serialized_sdk_request(monkeypatch, selected, adaptive, expected):
    requests = []
    class Client:
        def __init__(self, **kwargs):
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def create(self, **kwargs):
            requests.append(copy.deepcopy(kwargs))
            return iter([SimpleNamespace(choices=[SimpleNamespace(
                delta=SimpleNamespace(content="Done.", tool_calls=[]), finish_reason="stop"
            )])])

        def close(self):
            pass

    class Connection:
        def __init__(self):
            self.messages = []

        def send(self, message):
            self.messages.append(message)

        def close(self):
            pass

    def subprocess_boundary(**kwargs):
        conn = Connection()
        # Exercise the actual child serializer with the same normalization
        # performed by _run_provider_subprocess, without spawning/network IO.
        provider._gemini_child_main(
            conn, kwargs["prompt"], kwargs["context"], kwargs["model"], kwargs["api_key"],
            provider._provider_reasoning_effort(kwargs["reasoning_effort"]),
            10.0, 1, False, kwargs["base_url"],
        )
        assert conn.messages[-1]["type"] == "done"
        return provider.ProviderResult(final_output=conn.messages[-1]["final_output"], raw={})

    monkeypatch.setitem(sys.modules, "openai", SimpleNamespace(OpenAI=Client))
    monkeypatch.setattr(provider, "_run_provider_subprocess", subprocess_boundary)
    result = provider.GeminiProvider(
        model="gemini-2.5-flash", api_key="fake", reasoning_effort=selected,
        adaptive_reasoning=adaptive,
    ).run("Show the current status.", context())
    assert requests[0]["reasoning_effort"] == expected
    assert requests[0]["model"] == "gemini-2.5-flash"
    assert result.raw["effective_reasoning_effort"] == expected


@pytest.mark.parametrize("change", ["content", "label", "name", "order", "replacement"])
def test_wire_changed_reference_identity_reattaches(wire, tmp_path, change):
    images = []
    for name in ("front", "rear"):
        image = tmp_path / (name + ".png")
        image.write_bytes(name.encode())
        images.append({"id": name, "name": name, "label": name, "path": str(image)})
    ctx = context()
    ctx["reference_images"] = {"images": images}
    assert image_count(run(wire, ctx)[1]) == 2
    assert image_count(run(wire, ctx)[1]) == 0
    if change == "content":
        (tmp_path / "front.png").write_bytes(b"new content")
    elif change in {"label", "name"}:
        images[0][change] = "changed"
    elif change == "order":
        images.reverse()
    else:
        image = tmp_path / "replacement.png"
        image.write_bytes(b"replacement")
        images[0] = {"id": "replacement", "path": str(image)}
    assert image_count(run(wire, ctx)[1]) == (2 if change == "order" else 1)


@pytest.mark.parametrize("history", ["{}", '{"turns":null}', '{"turns":[]}'])
def test_incomplete_or_malformed_history_is_not_classified_as_simple(wire, history):
    ctx = context()
    prompt = "RECENT_CONVERSATION_JSON\n" + history + "\nCURRENT_USER_MESSAGE\nShow the current status."
    result = provider.CodexProvider(
        model="selected-model", api_key="fake", auth_mode="api_key", adaptive_reasoning=True,
    ).run(prompt, ctx)
    assert result.raw["effective_reasoning_effort"] == "high"


def test_context_reuse_retains_usage_and_adaptive_reasoning_metadata(wire):
    """Accounting and context reuse must survive the same managed turns."""
    def report_usage(client):
        number = sum(name == "turn/start" for name, _ in wire.requests)
        client.notification_handler("thread/tokenUsage/updated", {
            "threadId": "thread", "turnId": f"turn-{number}",
            "tokenUsage": {
                "last": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120},
                "total": {"inputTokens": number * 100, "outputTokens": number * 20,
                          "totalTokens": number * 120},
            },
        })

    wire.on_turn = report_usage
    ctx = context()
    first, _ = run(wire, ctx, adaptive=True)
    second, request = run(wire, ctx, adaptive=True)
    assert reference(request)["anchor_turn_id"] == "turn-1"
    for result, expected_effort in ((first, "medium"), (second, "high")):
        assert result.raw["model"] == "selected-model"
        assert result.raw["effective_reasoning_effort"] == expected_effort
        assert result.raw["usage"] == result.usage
        assert result.usage["turn"]["total_tokens"] == 120
