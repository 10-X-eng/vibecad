# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for VibeCAD's bundled Codex transport."""

from __future__ import annotations

import base64
import json
from pathlib import Path
import sys
import threading
import time
from types import SimpleNamespace

import pytest

import VibeCADCodex as codex
import VibeCADCodexResponses as codex_responses
import VibeCADPreferences as preferences
import VibeCADProvider as provider
import VibeCADSession as session
from VibeCADTools import SafetyLevel


def _tool_schema(name: str) -> dict:
    return {
        "name": name,
        "description": f"Call {name}.",
        "parameters": {
            "type": "object",
            "properties": {
                "model_id": {
                    "type": "string",
                    "description": "Exact model identifier.",
                }
            },
            "required": ["model_id"],
            "additionalProperties": False,
        },
    }


def _surface_context(*names: str, workbench: str = "PartDesignWorkbench") -> dict:
    schemas = [_tool_schema(name) for name in names]
    surface = session._turn_start_tool_surface(workbench, schemas)
    return {
        "provider_tool_schemas": schemas,
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


def _scripted_context() -> dict:
    return _surface_context(
        "vibescript.read_source",
        "vibescript.create_part",
    )


def _part_vibescript_context() -> dict:
    return _surface_context(
        "vibescript.read_source",
        "vibescript.create_program",
        workbench="PartWorkbench",
    )


def test_codex_dynamic_tools_require_a_frozen_turn_start_surface() -> None:
    context = _part_vibescript_context()
    context.pop("provider_tool_surface")
    with pytest.raises(provider.ProviderUnavailable, match="frozen turn-start"):
        provider._codex_dynamic_tool_surface(context)


def test_resumed_surface_turn_reanchors_the_exact_user_request() -> None:
    prompt = (
        "VIBECAD_CONTEXT_JSON\n{}\nEND_VIBECAD_CONTEXT_JSON\n\n"
        "RECENT_CONVERSATION_JSON\n"
        '{"turns":[{"role":"user","content":"Solve the duct and report pressure drop."}],'
        '"omitted_turn_count":0,"truncated_turn_count":0}\n'
        "END_RECENT_CONVERSATION_JSON\n\n"
        "CURRENT_SESSION_EVENT\nAnalysis tools now match the study."
    )

    resumed = provider._codex_prompt_without_replayed_conversation(prompt)

    assert "ACTIVE_USER_REQUEST\nSolve the duct and report pressure drop." in resumed
    assert '"turns":[]' in resumed
    assert resumed.endswith(
        "CURRENT_SESSION_EVENT\nAnalysis tools now match the study."
    )


def test_codex_reuses_unchanged_prompt_sections_with_a_live_guard() -> None:
    context = _part_vibescript_context()
    context.update(
        {
            "document": {
                "name": "Bracket",
                "uid": "doc-1",
                "object_count": 42,
                "revision": 7,
                "notes": "STABLE_STATE_SENTINEL" + "x" * 1024,
            },
            "selection": {
                "selection_count": 1,
                "selection": ["MountingFace"],
            },
            "editable_sources": {
                "schema": "vibecad-editable-sources-v1",
                "domain": "part",
                "source_count": 1,
                "sources": [
                    {
                        "program": "BracketProgram",
                        "label": "STABLE_SOURCE_SENTINEL",
                    }
                ],
                "core_api": {
                    "schema": "vibecad-authoring-contract-v1",
                    "functions": ["STABLE_CONTRACT_SENTINEL" + "x" * 512],
                },
            },
        }
    )
    prompt = session._provider_prompt("Continue.", context)
    previous = provider._codex_prompt_section_digests(prompt)

    optimized, current, reuse = provider._codex_prompt_with_reused_context(
        prompt,
        previous,
        context=context,
    )

    assert current == previous
    assert reuse["reused_sections"] == [
        "active_state",
        "vibescript_authoring_contract",
    ]
    assert reuse["saved_utf8_bytes"] > 0
    assert reuse["saved_estimated_tokens"] == (
        reuse["saved_utf8_bytes"] + 3
    ) // 4
    assert len(optimized.encode("utf-8")) < len(prompt.encode("utf-8"))
    assert optimized.count('"__vibecad_context_reference__"') == 2
    assert "STABLE_SOURCE_SENTINEL" not in optimized
    assert "STABLE_STATE_SENTINEL" not in optimized
    assert "STABLE_CONTRACT_SENTINEL" not in optimized
    assert '"revision":7' in optimized
    assert '"selection":["MountingFace"]' in optimized
    assert context["provider_tool_surface"]["schema_sha256"] in optimized


def test_codex_resends_a_changed_context_but_reuses_the_authoring_contract() -> None:
    context = _part_vibescript_context()
    context["document"] = {"name": "Bracket", "revision": 7}
    context["editable_sources"] = {
        "schema": "vibecad-editable-sources-v1",
        "domain": "part",
        "source_count": 1,
        "sources": [{"program": "BracketProgram"}],
        "core_api": {
            "schema": "contract-v1",
            "functions": ["stable.call" + "x" * 512],
        },
    }
    first = session._provider_prompt("Inspect.", context)
    previous = provider._codex_prompt_section_digests(first)
    context["document"] = {"name": "Bracket", "revision": 8}
    second = session._provider_prompt("Continue.", context)

    optimized, current, reuse = provider._codex_prompt_with_reused_context(
        second,
        previous,
    )

    assert current["active_state"] != previous["active_state"]
    assert reuse["reused_sections"] == ["vibescript_authoring_contract"]
    assert '"revision":8' in optimized
    assert "stable.call" not in optimized


def test_codex_never_parses_user_marker_text_as_a_reusable_section() -> None:
    context = _part_vibescript_context()
    fake_contract = (
        "Keep this literal example:\n"
        "VIBESCRIPT_AUTHORING_CONTRACT_JSON\n"
        '{"user_text":true}\n'
        "END_VIBESCRIPT_AUTHORING_CONTRACT_JSON"
    )
    prompt = session._provider_prompt(fake_contract, context)

    digests = provider._codex_prompt_section_digests(prompt)

    assert set(digests) == {"active_state"}
    assert fake_contract in prompt


def test_turn_start_surface_accepts_one_workbench_vibescript_domain() -> None:
    schemas = _part_vibescript_context()["provider_tool_schemas"]
    surface = session._turn_start_tool_surface("PartWorkbench", schemas)
    assert surface["kind"] == "turn_start_snapshot"
    assert surface["frozen"] is True
    assert surface["engine"] == "vibescript"
    assert surface["domain"] == "part"
    assert surface["workbench"] == "PartWorkbench"
    assert surface["tool_names"] == [
        "vibescript.read_source",
        "vibescript.create_program",
    ]
    assert surface["schema_count"] == 2
    assert surface["schema_sha256"] == provider.provider_tool_schema_digest(schemas)
    assert surface["available"] is True
    assert surface["unavailable_reason"] == ""


def test_turn_start_surface_preserves_pure_vibescript_behavior() -> None:
    from VibeCADModelingSurface import resolve_modeling_surface

    resolution = resolve_modeling_surface("PartDesignWorkbench", "vibescript")
    schemas = [_tool_schema(name) for name in resolution.tool_names]
    surface = session._turn_start_tool_surface("PartDesignWorkbench", schemas)
    assert surface["engine"] == "vibescript"
    assert surface["domain"] == "partdesign"
    assert surface["tool_names"] == [schema["name"] for schema in schemas]


def test_turn_start_surface_rejects_multiple_vibescript_domains() -> None:
    schemas = [
        _tool_schema("vibescript.partdesign.create_program"),
        _tool_schema("vibescript.assembly.create_program"),
    ]
    with pytest.raises(ValueError, match="active domain namespace"):
        session._turn_start_tool_surface("AssemblyWorkbench", schemas)


@pytest.mark.parametrize(
    "schemas",
    (
        [],
        [{"description": "missing name"}],
        [_tool_schema("assembly.solve"), _tool_schema("assembly.solve")],
    ),
)
def test_turn_start_surface_rejects_malformed_declarations(schemas: list[dict]) -> None:
    with pytest.raises(ValueError):
        session._turn_start_tool_surface("AssemblyWorkbench", schemas)


def test_codex_dynamic_tools_preserve_vibecad_namespaces_and_schema() -> None:
    tools, names = provider._codex_dynamic_tool_surface(_scripted_context())
    assert names == {
        ("vibescript", "read_source"): "vibescript.read_source",
        ("vibescript", "create_part"): "vibescript.create_part",
    }
    assert [namespace["name"] for namespace in tools] == ["vibescript"]
    read_tool = tools[0]["tools"][0]
    assert read_tool["name"] == "read_source"
    assert (
        read_tool["inputSchema"]
        == _scripted_context()["provider_tool_schemas"][0]["parameters"]
    )


def test_codex_dynamic_tools_never_reresolve_the_live_modeling_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = _part_vibescript_context()

    def forbidden_live_resolution(*_args, **_kwargs):
        raise AssertionError("A provider worker must use the frozen turn surface.")

    import VibeCADModelingSurface as modeling_surface

    monkeypatch.setattr(
        modeling_surface,
        "resolve_modeling_surface",
        forbidden_live_resolution,
    )

    tools, names = provider._codex_dynamic_tool_surface(context)

    assert tools
    assert names


def test_codex_dynamic_tools_normalize_an_exact_single_schema_branch() -> None:
    context = _scripted_context()
    expected = context["provider_tool_schemas"][0]["parameters"]
    context["provider_tool_schemas"][0]["parameters"] = {"oneOf": [expected]}
    context["provider_tool_surface"] = session._turn_start_tool_surface(
        "PartDesignWorkbench",
        context["provider_tool_schemas"],
    )

    tools, _names = provider._codex_dynamic_tool_surface(context)

    assert tools[0]["tools"][0]["inputSchema"] == expected


def test_codex_dynamic_tools_use_one_workbench_neutral_namespace() -> None:
    tools, names = provider._codex_dynamic_tool_surface(_part_vibescript_context())
    assert names == {
        ("vibescript", "read_source"): "vibescript.read_source",
        ("vibescript", "create_program"): "vibescript.create_program",
    }
    assert [namespace["name"] for namespace in tools] == ["vibescript"]


def test_codex_dynamic_tools_flatten_for_third_party_responses_endpoints() -> None:
    tools, names = provider._codex_dynamic_tool_surface(
        _scripted_context(),
        namespaced=False,
    )

    assert names == {
        ("", "vibescript__read_source"): "vibescript.read_source",
        ("", "vibescript__create_part"): "vibescript.create_part",
    }
    assert [tool["type"] for tool in tools] == ["function", "function"]
    assert [tool["name"] for tool in tools] == [
        "vibescript__read_source",
        "vibescript__create_part",
    ]
    assert tools[0]["inputSchema"] == _scripted_context()[
        "provider_tool_schemas"
    ][0]["parameters"]


@pytest.mark.parametrize(
    ("auth_mode", "base_url", "expected"),
    (
        ("chatgpt", None, True),
        ("api_key", None, True),
        ("api_key", "https://api.openai.com/v1", True),
        ("api_key", "https://us.api.openai.com/v1", True),
        ("api_key", "https://api.x.ai/v1", False),
        ("api_key", "http://127.0.0.1:11434/v1", False),
    ),
)
def test_codex_selects_endpoint_compatible_dynamic_tool_shape(
    auth_mode: str,
    base_url: str | None,
    expected: bool,
) -> None:
    assert (
        provider._codex_uses_namespaced_tools(
            auth_mode=auth_mode,
            base_url=base_url,
        )
        is expected
    )


@pytest.mark.parametrize(
    "base_url",
    (
        None,
        "https://api.x.ai/v1",
        "http://127.0.0.1:11434/v1",
    ),
)
def test_codex_forwards_its_exact_dynamic_tool_call_id(
    monkeypatch,
    base_url: str | None,
) -> None:
    import VibeCADOllama as ollama

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
        def __init__(
            self,
            *,
            notification_handler,
            server_request_handler,
            environment=None,
        ) -> None:
            self.notification_handler = notification_handler
            self.server_request_handler = server_request_handler
            self.environment = environment
            self.namespace = ""
            self.tool = ""
            self.alive = True

        @property
        def stderr_tail(self):
            return []

        def start(self):
            return None

        def request(self, method, params, timeout):
            if method == "thread/start":
                assert "Operate only through the supplied VibeCAD tools" in params[
                    "developerInstructions"
                ]
                dynamic_tool = params["dynamicTools"][0]
                if dynamic_tool["type"] == "namespace":
                    self.namespace = dynamic_tool["name"]
                    self.tool = dynamic_tool["tools"][0]["name"]
                else:
                    self.namespace = ""
                    self.tool = dynamic_tool["name"]
                return {"thread": {"id": "thread-1"}, "model": "gpt-test"}
            if method == "turn/start":
                self.server_request_handler(
                    "item/tool/call",
                    {
                        "callId": "codex-call-42",
                        "namespace": self.namespace,
                        "tool": self.tool,
                        "arguments": {"model_id": "exact-model"},
                    },
                )
                self.notification_handler(
                    "item/completed",
                    {
                        "threadId": "thread-1",
                        "item": {"type": "agentMessage", "text": "Done."},
                    },
                )
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

        def close(self):
            self.alive = False

    monkeypatch.setattr(codex, "CodexAppServerClient", _Client)
    context = _surface_context("core.set_view")
    calls = []

    def runner(tool_name, arguments_json, provider_call_id):
        calls.append((tool_name, arguments_json, provider_call_id))
        return {"ok": True}

    runner.provider_update = lambda: context
    active_provider = provider.CodexProvider(
        model="gpt-test",
        api_key="test-key",
        auth_mode="api_key",
        base_url=base_url,
    )

    result = active_provider.run("Set the view.", context, tool_runner=runner)

    assert result.final_output == "Done."
    assert calls == [
        ("core.set_view", '{"model_id":"exact-model"}', "codex-call-42")
    ]


def test_codex_ends_a_frozen_turn_after_an_exact_cad_transition(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import VibeCADOllama as ollama

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
    interrupted = threading.Event()
    runner_started = threading.Event()
    calls = []
    queued_results = []

    class _Client:
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
            self.response_handler = None
            self.namespace = ""
            self.tool = ""
            self.alive = True

        @property
        def stderr_tail(self):
            return []

        def set_server_response_handler(self, handler):
            self.response_handler = handler

        def start(self):
            return None

        def request(self, method, params, timeout):
            del timeout
            if method == "thread/start":
                dynamic_tool = params["dynamicTools"][0]
                if dynamic_tool["type"] == "namespace":
                    self.namespace = dynamic_tool["name"]
                    self.tool = dynamic_tool["tools"][0]["name"]
                else:
                    self.tool = dynamic_tool["name"]
                return {"thread": {"id": "thread-transition"}, "model": "gpt-test"}
            if method == "turn/start":
                self._call_transition()
                return {"turn": {"id": "turn-transition"}}
            if method == "turn/interrupt":
                interrupted.set()
                self.notification_handler(
                    "turn/completed",
                    {
                        "threadId": params["threadId"],
                        "turnId": params["turnId"],
                        "turn": {"id": params["turnId"], "status": "interrupted"},
                    },
                )
                return {}
            if method == "thread/delete":
                return {}
            raise AssertionError(method)

        def _call_transition(self):
            responses = {}

            def call(key, call_id, workspace):
                responses[key] = self.server_request_handler(
                    "item/tool/call",
                    {
                        "callId": call_id,
                        "namespace": self.namespace,
                        "tool": self.tool,
                        "arguments": {"workspace": workspace},
                    },
                )

            first = threading.Thread(
                target=call,
                args=("first", "transition-call", "assembly"),
            )
            queued = threading.Thread(
                target=call,
                args=("queued", "queued-after-transition", "model"),
            )
            first.start()
            assert runner_started.wait(1.0)
            queued.start()
            first.join(1.0)
            queued.join(1.0)
            assert not first.is_alive() and not queued.is_alive()
            assert self.response_handler is not None
            self.response_handler("item/tool/call")
            queued_results.append(responses["queued"])

        def close(self):
            self.alive = False

    class _Runner:
        def __init__(self, context):
            self.context = context
            self.transition = False

        def __call__(self, tool_name, arguments_json, provider_call_id):
            calls.append((tool_name, json.loads(arguments_json), provider_call_id))
            if provider_call_id == "transition-call":
                runner_started.set()
                time.sleep(0.05)
            self.transition = True
            return {
                "ok": True,
                "workspace": "assembly",
                "next_turn_required": True,
            }

        def provider_update(self):
            return self.context

        def turn_transition_requested(self):
            return self.transition

    monkeypatch.setattr(codex, "CodexAppServerClient", _Client)
    schemas = [_tool_schema("workspace.switch")]
    surface = {
        "kind": "turn_start_snapshot",
        "frozen": True,
        "workbench": "PartDesignWorkbench",
        "engine": "native",
        "domain": "model",
        "surface_id": "model",
        "available": True,
        "unavailable_reason": "",
        "tool_names": ["workspace.switch"],
        "schema_count": 1,
        "schema_sha256": provider.provider_tool_schema_digest(schemas),
    }
    context = {
        "provider_tool_schemas": schemas,
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
    runner = _Runner(context)
    active_provider = provider.CodexProvider(
        model="gpt-test",
        api_key="test-key",
        auth_mode="api_key",
        timeout_seconds=2,
    )

    result = active_provider.run("Continue in Assembly.", context, tool_runner=runner)

    assert interrupted.is_set()
    assert calls == [
        ("workspace.switch", {"workspace": "assembly"}, "transition-call")
    ]
    queued_payload = json.loads(queued_results[0]["contentItems"][0]["text"])
    assert queued_payload["error_code"] == "NATIVE_TURN_TRANSITION_PENDING"
    assert result.final_output == ""
    assert result.raw["cad_transition"] is True


def test_codex_steers_multiple_tool_images_outside_replayed_tool_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    viewport = tmp_path / "viewport.png"
    drawing_page = tmp_path / "drawing-page.png"
    viewport.write_bytes(b"valid-local-image")
    drawing_page.write_bytes(b"second-valid-local-image")
    clients = []

    class _Client:
        def __init__(
            self,
            *,
            notification_handler,
            server_request_handler,
            environment=None,
        ) -> None:
            self.notification_handler = notification_handler
            self.server_request_handler = server_request_handler
            self.environment = environment
            self.alive = True
            self.steer_requests = []
            self.tool_responses = []
            clients.append(self)

        @property
        def stderr_tail(self):
            return []

        def start(self):
            return None

        def request(self, method, params, timeout):
            if method == "thread/start":
                return {"thread": {"id": "thread-visual"}, "model": "gpt-test"}
            if method == "turn/start":
                threading.Thread(target=self._complete_turn, daemon=True).start()
                return {"turn": {"id": "turn-visual"}}
            if method == "turn/steer":
                self.steer_requests.append(params)
                return {"turnId": "turn-visual"}
            if method == "thread/delete":
                return {}
            raise AssertionError(method)

        def _complete_turn(self):
            time.sleep(0.02)
            self.tool_responses.append(
                self.server_request_handler(
                    "item/tool/call",
                    {
                        "callId": "capture-call",
                        "namespace": "core",
                        "tool": "capture_view_screenshot",
                        "arguments": {"model_id": "exact-model"},
                    },
                )
            )
            self.tool_responses.append(
                self.server_request_handler(
                    "item/tool/call",
                    {
                        "callId": "drawing-page-call",
                        "namespace": "core",
                        "tool": "capture_view_screenshot",
                        "arguments": {
                            "page_name": "Page002",
                        },
                    },
                )
            )
            self.notification_handler(
                "item/completed",
                {
                    "threadId": "thread-visual",
                    "turnId": "turn-visual",
                    "item": {"type": "agentMessage", "text": "Done."},
                },
            )
            self.notification_handler(
                "turn/completed",
                {
                    "threadId": "thread-visual",
                    "turnId": "turn-visual",
                    "turn": {"id": "turn-visual", "status": "completed"},
                },
            )

        def close(self):
            self.alive = False

    monkeypatch.setattr(codex, "CodexAppServerClient", _Client)
    context = _surface_context("core.capture_view_screenshot")
    calls = []

    def runner(tool_name, arguments_json, provider_call_id):
        calls.append((tool_name, provider_call_id))
        arguments = json.loads(arguments_json)
        if "page_name" not in arguments:
            return {
                "ok": True,
                "captured": True,
                "new_observation": True,
                "_vibecad_image_attachment": {
                    "path": str(viewport),
                    "name": "current viewport",
                },
            }
        assert tool_name == "core.capture_view_screenshot"
        assert arguments["page_name"] == "Page002"
        return {
            "ok": True,
            "captured": True,
            "new_observation": True,
            "_vibecad_image_attachment": {
                "path": str(drawing_page),
                "name": "Inspection Drawing page",
            },
        }

    runner.provider_update = lambda: context
    active_provider = provider.CodexProvider(
        model="gpt-test",
        api_key="test-key",
        auth_mode="api_key",
        timeout_seconds=2.0,
    )

    result = active_provider.run("Inspect the view.", context, tool_runner=runner)

    assert result.final_output == "Done."
    assert calls == [
        ("core.capture_view_screenshot", "capture-call"),
        ("core.capture_view_screenshot", "drawing-page-call"),
    ]
    assert clients[0].steer_requests == [
        {
            "threadId": "thread-visual",
            "expectedTurnId": "turn-visual",
            "input": [
                {"type": "text", "text": "V:current|current viewport"},
                {
                    "type": "localImage",
                    "path": str(viewport.resolve()),
                    "detail": "original",
                },
            ],
        },
        {
            "threadId": "thread-visual",
            "expectedTurnId": "turn-visual",
            "input": [
                {
                    "type": "text",
                    "text": "V:current|Inspection Drawing page",
                },
                {
                    "type": "localImage",
                    "path": str(drawing_page.resolve()),
                    "detail": "original",
                },
            ],
        },
    ]
    for tool_response in clients[0].tool_responses:
        content_items = tool_response["contentItems"]
        assert [item["type"] for item in content_items] == ["inputText"]
        assert "imageUrl" not in str(content_items)


def test_turn_start_surface_rejects_human_mutation_commands() -> None:
    schemas = [
        _tool_schema("part.measure"),
        _tool_schema("vibescript.part.create_program"),
    ]
    with pytest.raises(ValueError, match="mutation or foreign read"):
        session._turn_start_tool_surface("PartWorkbench", schemas)


def test_codex_dynamic_tools_reject_surface_name_or_schema_drift() -> None:
    name_drift = _part_vibescript_context()
    name_drift["provider_tool_surface"]["tool_names"] = []
    with pytest.raises(provider.ProviderUnavailable, match="do not match"):
        provider._codex_dynamic_tool_surface(name_drift)

    schema_drift = _part_vibescript_context()
    schema_drift["provider_tool_schemas"][0]["description"] = "Changed after freeze."
    with pytest.raises(provider.ProviderUnavailable, match="changed after"):
        provider._codex_dynamic_tool_surface(schema_drift)


def test_codex_dynamic_tools_reject_a_false_scripted_engine_declaration() -> None:
    context = _part_vibescript_context()
    context["provider_tool_surface"]["engine"] = "invalid"
    with pytest.raises(provider.ProviderUnavailable, match="does not match"):
        provider._codex_dynamic_tool_surface(context)


def test_provider_update_keeps_the_turn_surface_frozen_after_workbench_change(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _part_vibescript_context()
    next_context = _scripted_context()
    next_context["workbench"] = "PartDesignWorkbench"
    next_context["modeling_surface"] = {
        "workbench": "PartDesignWorkbench",
        "engine": "vibescript",
        "domain": "partdesign",
        "surface_id": next_context["provider_tool_surface"]["surface_id"],
    }
    monkeypatch.setattr(
        session,
        "_build_context_for_provider",
        lambda *_args: next_context,
    )

    initial_surface = dict(initial["provider_tool_surface"])
    initial_schemas = list(initial["provider_tool_schemas"])
    runner = session.make_provider_tool_runner(
        object(),
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        turn_surface=initial_surface,
        turn_schemas=initial_schemas,
        turn_modeling_surface={
            "workbench": "PartWorkbench",
            "engine": "vibescript",
            "domain": "part",
            "surface_id": initial_surface["surface_id"],
        },
    )

    updated = runner.provider_update()

    assert updated["provider_tool_surface"] == initial_surface
    assert updated["provider_tool_schemas"] == initial_schemas
    assert updated["workbench"] == "PartWorkbench"
    assert updated["modeling_surface"]["invalidated"] is True
    assert updated["modeling_surface"]["next_turn_required"] is True
    assert "vibescript_domain" not in updated


def test_provider_update_keeps_model_assembly_authoring_live_across_ribbons(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    initial = _scripted_context()
    initial_surface = dict(initial["provider_tool_surface"])
    initial_schemas = list(initial["provider_tool_schemas"])
    next_context = {
        **initial,
        "workbench": "AssemblyWorkbench",
        "provider_tool_surface": {
            **initial_surface,
            "workbench": "AssemblyWorkbench",
            "domain": "assembly",
        },
        "modeling_surface": {
            "workbench": "AssemblyWorkbench",
            "engine": "vibescript",
            "domain": "assembly",
            "surface_id": initial_surface["surface_id"],
        },
        "editable_sources": {
            "schema": "vibecad-editable-sources-v1",
            "domain": "assembly",
            "workbench": "AssemblyWorkbench",
            "authoring_domains": ["partdesign", "assembly"],
            "sources": [
                {
                    "source_id": "b" * 32,
                    "domain": "assembly",
                }
            ],
            "all_sources": [
                {
                    "source_id": "a" * 32,
                    "domain": "partdesign",
                },
                {
                    "source_id": "b" * 32,
                    "domain": "assembly",
                },
            ],
        },
    }
    monkeypatch.setattr(
        session,
        "_build_context_for_provider",
        lambda *_args: next_context,
    )

    runner = session.make_provider_tool_runner(
        object(),
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
        turn_surface=initial_surface,
        turn_schemas=initial_schemas,
        turn_modeling_surface={
            "workbench": "PartDesignWorkbench",
            "engine": "vibescript",
            "domain": "partdesign",
            "surface_id": initial_surface["surface_id"],
        },
    )

    updated = runner.provider_update()

    assert updated["provider_tool_surface"] == initial_surface
    assert updated["provider_tool_schemas"] == initial_schemas
    assert updated["workbench"] == "PartDesignWorkbench"
    assert updated["modeling_surface"].get("invalidated") is not True
    assert updated["editable_sources"]["authoring_domains"] == [
        "partdesign",
        "assembly",
    ]
    assert updated["editable_sources"]["domain"] == "partdesign"
    assert updated["editable_sources"]["workbench"] == "PartDesignWorkbench"
    assert [
        item["source_id"] for item in updated["editable_sources"]["sources"]
    ] == ["a" * 32]


def test_codex_dynamic_tools_reject_malformed_or_extended_snapshots() -> None:
    malformed = _part_vibescript_context()
    malformed["provider_tool_schemas"][0]["parameters"] = {"type": "string"}
    malformed["provider_tool_surface"] = session._turn_start_tool_surface(
        "PartWorkbench", malformed["provider_tool_schemas"]
    )
    with pytest.raises(provider.ProviderUnavailable, match="Invalid frozen schema"):
        provider._codex_dynamic_tool_surface(malformed)

    extended = _part_vibescript_context()
    extended["provider_tool_surface"]["unexpected"] = True
    with pytest.raises(provider.ProviderUnavailable, match="unexpected fields"):
        provider._codex_dynamic_tool_surface(extended)


def test_provider_projects_a_multi_variant_tool_to_an_object_root() -> None:
    branches = [
        {
            "type": "object",
            "properties": {
                "operation": {"type": "string", "const": operation},
                field: {"type": field_type},
            },
            "required": ["operation", field],
            "additionalProperties": False,
        }
        for operation, field, field_type in (
            ("first", "count", "integer"),
            ("second", "name", "string"),
        )
    ]

    parameters = provider._provider_tool_parameters(
        {
            "name": "model.example",
            "parameters": {"oneOf": branches},
        }
    )

    assert parameters["type"] == "object"
    assert parameters["properties"]["operation"] == {
        "type": "string",
        "enum": ["first", "second"],
    }
    assert parameters["required"] == ["operation"]
    assert parameters["oneOf"] == branches


def test_tool_runner_revalidates_each_call_against_the_live_surface(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Spec:
        def validate_arguments(self, _args: dict) -> None:
            raise AssertionError("A removed tool must be blocked before validation.")

    class _Registry:
        def __init__(self) -> None:
            self.called = False

        def get(self, _name: str):
            return SimpleNamespace(
                safety=SafetyLevel.READ,
                workbench="PartDesignWorkbench",
                spec=_Spec(),
            )

        def call(self, _name: str, **_args):
            self.called = True
            raise AssertionError("A removed tool must never execute.")

    class _Service:
        def __init__(self) -> None:
            self.registry = _Registry()

        def active_workbench_name(self) -> str:
            return "AssemblyWorkbench"

    service = _Service()
    monkeypatch.setattr(
        session,
        "_live_provider_surface_state",
        lambda _service: {
            "workbench": "AssemblyWorkbench",
            "runtime_state": {"edit_mode": "none"},
            "tool_names": ["assembly.solve"],
        },
    )
    runner = session.make_provider_tool_runner(
        service,
        tool_trace=[],
        progress_callback=None,
        cancellation_check=None,
        steering_check=None,
        question_callback=None,
    )

    result = runner("vibescript.part.create_program", "{}")

    assert result["ok"] is False
    assert result["failure_code"] == "TOOL_NOT_ON_ACTIVE_SURFACE"
    assert result["candidates"] == ["assembly.solve"]
    assert service.registry.called is False


def test_codex_images_use_the_bounded_inline_transport(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    screenshot = tmp_path / "viewport.png"
    screenshot.write_bytes(b"x" * (provider.CODEX_INLINE_IMAGE_MAX_BYTES + 1))
    encoded = b"\xff\xd8" + (b"v" * 1024) + b"\xff\xd9"
    calls: list[tuple[Path, int, bool]] = []

    def encode(
        path: Path,
        *,
        max_bytes: int,
        prefer_jpeg: bool,
    ) -> tuple[str, bytes, dict]:
        calls.append((path, max_bytes, prefer_jpeg))
        return (
            "image/jpeg",
            encoded,
            {
                "resized": True,
                "encoded_format": "jpg",
                "image_size": [1280, 812],
                "size_bytes": len(encoded),
            },
        )

    monkeypatch.setattr(provider, "_provider_encoded_image_payload", encode)
    context = {
        "view_screenshot": {
            "captured": True,
            "new_observation": True,
            "pending_attachment": True,
            "path": str(screenshot),
        }
    }

    turn_input = provider._codex_turn_input("Inspect it.", context)
    tool_output = provider._codex_tool_image_content_items(context)

    assert calls == [
        (screenshot, provider.CODEX_INLINE_IMAGE_MAX_BYTES, True),
        (screenshot, provider.CODEX_INLINE_IMAGE_MAX_BYTES, True),
    ]
    turn_image = turn_input[-1]
    tool_image = tool_output[-1]
    assert turn_image["type"] == "image"
    assert tool_image["type"] == "inputImage"
    assert turn_image["url"] == tool_image["imageUrl"]
    assert turn_image["url"].startswith("data:image/jpeg;base64,")
    assert base64.b64decode(turn_image["url"].partition(",")[2]) == encoded
    assert len(encoded) <= provider.CODEX_INLINE_IMAGE_MAX_BYTES


def test_consumed_view_is_not_attached_to_a_later_provider_turn(
    tmp_path: Path,
) -> None:
    screenshot = tmp_path / "viewport.png"
    screenshot.write_bytes(b"png")
    context = {
        "view_screenshot": {
            "captured": True,
            "pending_attachment": False,
            "path": str(screenshot),
        }
    }

    assert provider._screenshot_image_payload(context) is None
    assert provider._context_image_blocks(context) == []


def test_session_consumes_the_exact_view_after_copying_provider_context() -> None:
    consumed: list[dict] = []
    service = SimpleNamespace(
        consume_view_screenshot_attachment=lambda value: consumed.append(dict(value))
    )
    screenshot = {
        "captured": True,
        "pending_attachment": True,
        "path": "/project/screenshots/view.png",
    }
    context = {"view_screenshot": dict(screenshot)}

    session._consume_context_view_attachment(
        service, context, lambda operation: operation()
    )

    assert consumed == [screenshot]
    assert context["view_screenshot"] == screenshot


def test_session_never_consumes_durable_reference_images() -> None:
    consumed: list[dict] = []
    service = SimpleNamespace(
        consume_reference_image_attachments=lambda value: consumed.append(dict(value))
    )
    references = {
        "count": 1,
        "images": [{"id": "blade", "path": "/project/references/blade.png"}],
    }

    session._consume_context_view_attachment(
        service,
        {"reference_images": references},
        lambda operation: operation(),
    )

    assert consumed == []


def test_codex_resends_the_same_reference_as_image_input_each_turn(
    tmp_path: Path,
) -> None:
    reference = tmp_path / "blade.png"
    reference.write_bytes(b"same-image-bytes")
    context = {
        "reference_images": {
            "count": 1,
            "images": [
                {
                    "id": "blade",
                    "name": "blade.png",
                    "path": str(reference),
                }
            ],
        }
    }

    first = provider._codex_turn_input("first", context)
    second = provider._codex_turn_input("second", context)
    first_images = [item for item in first if item.get("type") == "localImage"]
    second_images = [item for item in second if item.get("type") == "localImage"]

    assert len(first_images) == 1
    assert second_images == first_images
    assert first_images[0] == {
        "type": "localImage",
        "path": str(reference.resolve()),
        "detail": "original",
    }


def test_codex_thread_config_disables_non_vibecad_tool_surfaces() -> None:
    config = codex.vibecad_thread_config()
    assert config["orchestrator.mcp.enabled"] is False
    assert config["orchestrator.skills.enabled"] is False
    assert config["project_doc_max_bytes"] == 0
    assert config["tools.experimental_request_user_input.enabled"] is False
    assert config["skills.include_instructions"] is False
    assert config["features.shell_tool"] is False
    assert config["features.plugins"] is False
    assert config["web_search"] == "disabled"
    assert config["include_collaboration_mode_instructions"] is False
    assert config["features.code_mode"] == {
        "enabled": False,
        "direct_only_tool_namespaces": ["core", "conversation", "view"],
    }


def test_codex_thread_config_enables_only_web_and_skill_capabilities() -> None:
    config = codex.vibecad_thread_config(
        web_search_enabled=True,
        skills_enabled=True,
    )
    assert config["web_search"] == "live"
    assert config["skills.bundled.enabled"] is True
    assert config["skills.include_instructions"] is True
    assert config["orchestrator.skills.enabled"] is False
    assert config["features.shell_tool"] is False
    assert config["features.browser_use"] is False
    assert config["features.computer_use"] is False
    assert config["features.plugins"] is False


def test_codex_thread_config_keeps_one_conversation_mode_with_api_key_provider() -> None:
    config = codex.vibecad_thread_config(
        openai_base_url="https://api.example.test/v1/",
    )

    assert config["include_collaboration_mode_instructions"] is False
    assert config["model_provider"] == codex.CODEX_OPENAI_PROVIDER_ID
    prefix = f"model_providers.{codex.CODEX_OPENAI_PROVIDER_ID}"
    assert config[f"{prefix}.base_url"] == "https://api.example.test/v1"
    assert config[f"{prefix}.env_key"] == codex.CODEX_OPENAI_API_KEY_ENV
    assert config[f"{prefix}.wire_api"] == "responses"
    assert config[f"{prefix}.requires_openai_auth"] is False


def test_codex_environment_uses_only_the_selected_vibecad_api_key(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(codex.CODEX_HOME_ENV, str(tmp_path / "codex-home"))
    monkeypatch.setenv("OPENAI_API_KEY", "ambient-openai")
    monkeypatch.setenv("CODEX_API_KEY", "ambient-codex")

    environment = codex._subprocess_environment(
        {codex.CODEX_OPENAI_API_KEY_ENV: "selected-key"}
    )

    assert "OPENAI_API_KEY" not in environment
    assert "CODEX_API_KEY" not in environment
    assert environment[codex.CODEX_OPENAI_API_KEY_ENV] == "selected-key"


def test_provider_capability_preferences_have_explicit_defaults(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _UnsetPreferences:
        def GetBool(self, _name: str, default: bool) -> bool:
            return default

        def GetString(self, _name: str, default: str) -> str:
            return default

        def GetFloat(self, _name: str, default: float) -> float:
            return default

        def GetInt(self, _name: str, default: int) -> int:
            return default

    settings = preferences.VibeCADSettings()
    assert settings.web_search_enabled is False
    assert settings.design_review_enabled is False
    assert settings.codex_skills_enabled is False

    monkeypatch.setattr(preferences, "preferences", lambda: _UnsetPreferences())
    loaded = preferences.load_settings()
    assert loaded.design_review_enabled is False

    class _OptedInPreferences(_UnsetPreferences):
        def GetBool(self, name: str, default: bool) -> bool:
            if name == "DesignReviewEnabled":
                return True
            return default

    monkeypatch.setattr(preferences, "preferences", lambda: _OptedInPreferences())
    assert preferences.load_settings().design_review_enabled is True


def test_codex_skill_reader_is_scoped_to_enabled_skill_directory(
    tmp_path: Path,
) -> None:
    skill_dir = tmp_path / "skills" / "design-review"
    skill_dir.mkdir(parents=True)
    skill_file = skill_dir / "SKILL.md"
    skill_file.write_text("# Design review\n", encoding="utf-8")
    reference = skill_dir / "references" / "checks.md"
    reference.parent.mkdir()
    reference.write_text("Check interfaces.\n", encoding="utf-8")
    outside = tmp_path / "outside.md"
    outside.write_text("private\n", encoding="utf-8")
    catalog = {
        "design-review": codex.CodexSkill(
            name="design-review",
            description="Review a design.",
            path=skill_file,
        )
    }

    main = codex.read_codex_skill_resource(catalog, name="design-review")
    assert main == {
        "ok": True,
        "skill": "design-review",
        "resource": "SKILL.md",
        "content": "# Design review\n",
    }
    nested = codex.read_codex_skill_resource(
        catalog,
        name="design-review",
        resource="references/checks.md",
    )
    assert nested["ok"] is True
    assert nested["content"] == "Check interfaces.\n"
    escaped = codex.read_codex_skill_resource(
        catalog,
        name="design-review",
        resource="../../outside.md",
    )
    assert escaped["ok"] is False
    assert "inside the skill directory" in escaped["error"]


def test_codex_skill_catalog_uses_personal_root_and_enabled_skills(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    vibecad_home = tmp_path / "vibecad-codex"
    personal_home = tmp_path / "personal-codex"
    personal_root = personal_home / "skills"
    personal_root.mkdir(parents=True)
    skill_file = personal_root / "cad-review" / "SKILL.md"
    skill_file.parent.mkdir()
    skill_file.write_text("# CAD review\n", encoding="utf-8")
    monkeypatch.setenv(codex.CODEX_HOME_ENV, str(vibecad_home))
    monkeypatch.setenv("CODEX_HOME", str(personal_home))

    class _Client:
        def __init__(self) -> None:
            self.calls: list[tuple[str, dict]] = []

        def request(self, method: str, params: dict, timeout: float) -> dict:
            self.calls.append((method, params))
            if method == "skills/extraRoots/set":
                return {}
            if method == "skills/list":
                return {
                    "data": [
                        {
                            "skills": [
                                {
                                    "name": "cad-review",
                                    "description": "Review CAD intent.",
                                    "path": str(skill_file),
                                    "enabled": True,
                                },
                                {
                                    "name": "disabled",
                                    "description": "Disabled.",
                                    "path": str(skill_file),
                                    "enabled": False,
                                },
                            ]
                        }
                    ]
                }
            raise AssertionError(method)

    client = _Client()
    catalog = codex.load_codex_skill_catalog(client, cwd=tmp_path)
    assert list(catalog) == ["cad-review"]
    assert client.calls[0] == (
        "skills/extraRoots/set",
        {"extraRoots": [str(personal_root.resolve())]},
    )
    assert client.calls[1] == (
        "skills/list",
        {"cwds": [str(tmp_path)], "forceReload": True},
    )


def test_current_subscription_reasoning_efforts_are_preserved() -> None:
    assert preferences.normalize_reasoning_effort("max") == "max"
    assert preferences.normalize_reasoning_effort("ultra") == "ultra"


def test_choose_provider_carries_codex_capability_preferences() -> None:
    class _Service:
        def provider_name(self) -> str:
            return "chatgpt"

        def auth_state(self):
            return object()

        def provider_model(self) -> str:
            return "gpt-test"

        def provider_reasoning_effort(self) -> str:
            return "high"

        def web_search_enabled(self) -> bool:
            return True

        def codex_skills_enabled(self) -> bool:
            return True

    selected = session.choose_provider(_Service())
    assert isinstance(selected, provider.CodexProvider)
    assert selected.auth_mode == "chatgpt"
    assert selected.web_search_enabled is True
    assert selected.skills_enabled is True


def test_subscription_provider_identity_is_explicit_and_disables_fallback() -> None:
    selected = provider.CodexProvider(
        model="gpt-5.6-sol",
        auth_mode="chatgpt",
        reasoning_effort="max",
    )

    assert session.provider_execution_identity(selected) == {
        "provider_id": "chatgpt",
        "provider_label": "ChatGPT subscription via Codex",
        "adapter": "CodexProvider",
        "requested_model": "gpt-5.6-sol",
        "model_selection": "explicit",
        "reasoning_effort": "max",
        "model_fallback_allowed": False,
    }


@pytest.mark.parametrize(
    ("provider_name", "provider_type"),
    [
        ("openai", provider.CodexProvider),
        ("anthropic", provider.AnthropicProvider),
    ],
)
def test_choose_provider_enables_web_search_for_api_providers(
    provider_name: str,
    provider_type: type,
) -> None:
    class _Auth:
        can_call_provider = True

    class _Service:
        def provider_name(self) -> str:
            return provider_name

        def auth_state(self):
            return _Auth()

        def provider_model(self) -> str:
            return "test-model"

        def provider_api_key(self) -> str:
            return "test-key"

        def provider_reasoning_effort(self) -> str:
            return "high"

        def provider_base_url(self):
            return None

        def web_search_enabled(self) -> bool:
            return True

        def codex_skills_enabled(self) -> bool:
            return False

        def intent_memory_model(self) -> str:
            return "memory-model"

    selected = session.choose_provider(_Service())
    assert isinstance(selected, provider_type)
    assert selected.web_search_enabled is True
    if provider_name == "openai":
        assert selected.auth_mode == "api_key"
        assert selected.api_key == "test-key"
    else:
        assert selected.compaction_model == "memory-model"


def test_natural_plan_request_uses_the_normal_codex_turn(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
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
            self.alive = True
            _Client.instance = self

        @property
        def stderr_tail(self) -> list[str]:
            return []

        def start(self) -> None:
            return None

        def request(self, method: str, params: dict, timeout: float) -> dict:
            self.requests.append((method, dict(params)))
            if method == "thread/start":
                return {"thread": {"id": "thread-1"}, "model": "gpt-test"}
            if method == "turn/start":
                self.notification_handler(
                    "item/completed",
                    {
                        "threadId": "thread-1",
                        "turnId": "turn-1",
                        "item": {
                            "type": "agentMessage",
                            "text": "Inspect, then revise.",
                        },
                    },
                )
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

    monkeypatch.setattr(codex, "CodexAppServerClient", _Client)
    active_provider = provider.CodexProvider(
        model="gpt-test",
        api_key="secret-test-key",
        auth_mode="api_key",
        base_url="https://api.example.test/v1",
        reasoning_effort="high",
    )
    context = _surface_context("core.set_view")

    result = active_provider.run("Plan the change.", context)

    client = _Client.instance
    assert client is not None
    assert client.environment == {codex.CODEX_OPENAI_API_KEY_ENV: "secret-test-key"}
    assert [method for method, _params in client.requests].count("account/read") == 0
    thread_request = next(
        params for method, params in client.requests if method == "thread/start"
    )
    assert thread_request["modelProvider"] == codex.CODEX_OPENAI_PROVIDER_ID
    assert thread_request["config"]["include_collaboration_mode_instructions"] is False
    assert "Operate only through the supplied VibeCAD tools" in thread_request[
        "developerInstructions"
    ]
    assert all(
        tool["type"] == "function" for tool in thread_request["dynamicTools"]
    )
    turn_request = next(
        params for method, params in client.requests if method == "turn/start"
    )
    assert "collaborationMode" not in turn_request
    assert turn_request["effort"] == "high"
    assert result.final_output == "Inspect, then revise."
    assert "interaction_mode" not in result.raw


def test_codex_plan_then_build_reuses_one_normal_conversation_thread(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _Client:
        instance = None

        def __init__(
            self,
            *,
            notification_handler,
            server_request_handler,
            environment=None,
        ) -> None:
            del server_request_handler, environment
            self.notification_handler = notification_handler
            self.requests: list[tuple[str, dict]] = []
            self.alive = True
            self.turn_number = 0
            _Client.instance = self

        @property
        def stderr_tail(self) -> list[str]:
            return []

        def start(self) -> None:
            return None

        def set_handlers(
            self,
            *,
            notification_handler,
            server_request_handler,
        ) -> None:
            del server_request_handler
            self.notification_handler = notification_handler

        def request(self, method: str, params: dict, timeout: float) -> dict:
            del timeout
            self.requests.append((method, dict(params)))
            if method == "thread/start":
                return {"thread": {"id": "shared-thread"}, "model": "gpt-test"}
            if method == "thread/resume":
                assert params == {"threadId": "shared-thread"}
                return {"thread": {"id": "shared-thread"}, "model": "gpt-test"}
            if method == "turn/start":
                self.turn_number += 1
                turn_id = f"turn-{self.turn_number}"
                self.notification_handler(
                    "item/completed",
                    {
                        "threadId": "shared-thread",
                        "turnId": turn_id,
                        "item": {
                            "type": "agentMessage",
                            "text": (
                                "Plan saved."
                                if self.turn_number == 1
                                else "Plan used."
                            ),
                        },
                    },
                )
                self.notification_handler(
                    "turn/completed",
                    {
                        "threadId": "shared-thread",
                        "turnId": turn_id,
                        "turn": {"id": turn_id, "status": "completed"},
                    },
                )
                return {"turn": {"id": turn_id}}
            raise AssertionError(method)

        def close(self) -> None:
            self.alive = False

    codex.reset_managed_codex_sessions()
    monkeypatch.setattr(codex, "CodexAppServerClient", _Client)
    context = _surface_context("core.set_view")
    context["document"] = {
        "name": "Bracket",
        "revision": 7,
        "notes": "stable-context-" + "x" * 1024,
    }
    context["_vibecad_codex_session"] = {
        "conversation_id": "a" * 32,
        "conversation_path": "/project/conversations/" + "a" * 32 + ".json",
    }
    first_provider = provider.CodexProvider(
        model="gpt-test",
        api_key="test-key",
        auth_mode="api_key",
    )
    second_provider = provider.CodexProvider(
        model="gpt-test",
        api_key="test-key",
        auth_mode="api_key",
    )
    third_provider = provider.CodexProvider(
        model="gpt-test",
        api_key="test-key",
        auth_mode="api_key",
    )
    first_prompt = session._provider_prompt("Make a plan.", context)
    second_prompt = session._provider_prompt(
        "Build it.",
        context,
        recent_conversation=[
            {"role": "user", "content": "Make a plan."},
            {"role": "assistant", "content": "Plan saved."},
        ],
    )
    third_prompt = session._provider_prompt("Double-check it.", context)

    requests_before_cleanup: list[tuple[str, dict]] = []
    try:
        planned = first_provider.run(first_prompt, context)
        built = second_provider.run(second_prompt, context)
        checked = third_provider.run(third_prompt, context)
    finally:
        if _Client.instance is not None:
            requests_before_cleanup = list(_Client.instance.requests)
        codex.reset_managed_codex_sessions()

    client = _Client.instance
    assert client is not None
    assert planned.final_output == "Plan saved."
    assert built.final_output == "Plan used."
    assert checked.final_output == "Plan used."
    methods = [method for method, _params in requests_before_cleanup]
    assert methods.count("thread/start") == 1
    assert methods.count("thread/resume") == 2
    assert methods.count("turn/start") == 3
    assert "thread/delete" not in methods
    turns = [
        params for method, params in requests_before_cleanup if method == "turn/start"
    ]
    assert all("collaborationMode" not in turn for turn in turns)
    first_text_input = next(
        item["text"] for item in turns[0]["input"] if item["type"] == "text"
    )
    assert '"__vibecad_context_reference__"' not in first_text_input
    assert "stable-context-" in first_text_input
    second_turn = turns[1]
    text_input = next(
        item["text"] for item in second_turn["input"] if item["type"] == "text"
    )
    assert '"turns":[]' in text_input
    assert "Plan saved." not in text_input
    assert '"__vibecad_context_reference__"' in text_input
    third_text_input = next(
        item["text"] for item in turns[2]["input"] if item["type"] == "text"
    )
    assert '"__vibecad_context_reference__"' not in third_text_input
    assert "stable-context-" in third_text_input


def test_system_instructions_do_not_forbid_requested_planning() -> None:
    assert "do not narrate plans" not in provider.VIBECAD_SYSTEM_INSTRUCTIONS.lower()


def test_codex_client_initializes_and_reads_account_from_json_rpc(
    tmp_path: Path,
) -> None:
    fake_server = tmp_path / "fake_app_server.py"
    fake_server.write_text(
        """
import json
import sys

for line in sys.stdin:
    message = json.loads(line)
    request_id = message.get("id")
    method = message.get("method")
    if request_id is None:
        continue
    if method == "initialize":
        result = {"userAgent": "fake"}
    elif method == "account/read":
        result = {"account": None, "requiresOpenaiAuth": True}
    else:
        print(json.dumps({"id": request_id, "error": {"code": -1, "message": method}}), flush=True)
        continue
    print(json.dumps({"id": request_id, "result": result}), flush=True)
""".lstrip(),
        encoding="utf-8",
    )
    command = codex.CodexRuntimeCommand(
        argv=(sys.executable, str(fake_server)),
        executable=Path(sys.executable),
        source="test",
        version="test",
    )
    with codex.CodexAppServerClient(command=command) as client:
        result = client.request("account/read", {"refreshToken": False})
    assert result == {"account": None, "requiresOpenaiAuth": True}
