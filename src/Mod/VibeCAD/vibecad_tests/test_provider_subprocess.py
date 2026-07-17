# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression coverage for provider subprocess lifecycle races."""

from __future__ import annotations

import json
import sys
from types import ModuleType, SimpleNamespace

import VibeCADProvider as provider
import VibeCADSession as session


class _DelayedPipeMessage:
    def __init__(self) -> None:
        self.poll_results = iter((False, True, True))
        self.poll_timeouts: list[float] = []
        self.closed = False

    def poll(self, timeout: float) -> bool:
        self.poll_timeouts.append(timeout)
        return next(self.poll_results)

    def recv(self) -> dict[str, object]:
        return {"type": "done", "final_output": "ok", "raw": None}

    def close(self) -> None:
        self.closed = True


class _ChildPipe:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


class _ExitedProcess:
    def __init__(self) -> None:
        self.daemon = False
        self.exitcode = 0
        self.pid = 1234
        self.started = False
        self.join_timeouts: list[float] = []

    def start(self) -> None:
        self.started = True

    def is_alive(self) -> bool:
        return False

    def join(self, timeout: float) -> None:
        self.join_timeouts.append(timeout)


class _FakeMultiprocessingContext:
    def __init__(self) -> None:
        self.parent_conn = _DelayedPipeMessage()
        self.child_conn = _ChildPipe()
        self.process = _ExitedProcess()

    def Pipe(self):
        return self.parent_conn, self.child_conn

    def Process(self, **_kwargs):
        return self.process


def _unused_child(*_args) -> None:
    raise AssertionError("The fake process must not execute its target.")


def test_clean_exit_drains_delayed_final_pipe_message(monkeypatch) -> None:
    context = _FakeMultiprocessingContext()
    monkeypatch.setattr(
        provider,
        "_provider_multiprocessing_context",
        lambda **_kwargs: context,
    )

    result = provider._run_provider_subprocess(
        prompt="smoke",
        context={},
        tool_runner=None,
        model="smoke",
        api_key=None,
        reasoning_effort=None,
        timeout_seconds=1.0,
        max_turns=1,
        clear_inherited_modules=False,
        event_pump=lambda: None,
        child_main=_unused_child,
        provider_label="test provider",
    )

    assert result.final_output == "ok"
    assert context.process.started
    assert context.child_conn.closed
    assert context.parent_conn.closed
    assert 0.2 in context.parent_conn.poll_timeouts


def _vibescript_mode_context() -> dict[str, object]:
    return {
        "provider_tool_schemas": [
            {
                "name": "vibescript.create_model",
                "description": "Create a VibeScript model.",
                "parameters": {"type": "object"},
            }
        ]
    }


def test_instructions_include_vibescript_guidance_only_in_vibescript_mode() -> None:
    instructions = provider._provider_instructions(_vibescript_mode_context())
    assert instructions.startswith(provider.VIBECAD_SYSTEM_INSTRUCTIONS)
    assert provider.VIBESCRIPT_AUTHORING_INSTRUCTIONS in instructions

    for other_context in (
        {},
        {"provider_tool_schemas": []},
        {"provider_tool_schemas": [{"name": "build123d.create_model"}]},
        {"provider_tool_schemas": [{"name": "openscad.create_model"}]},
        {"provider_tool_schemas": [{"name": "partdesign.pad"}]},
    ):
        other = provider._provider_instructions(other_context)
        assert provider.VIBESCRIPT_AUTHORING_INSTRUCTIONS not in other
        assert other.startswith(provider.VIBECAD_SYSTEM_INSTRUCTIONS)


def test_system_blocks_carry_vibescript_guidance_only_in_vibescript_mode() -> None:
    blocks = provider._anthropic_system_blocks(_vibescript_mode_context())
    texts = [block["text"] for block in blocks]
    assert texts == [
        provider.VIBECAD_SYSTEM_INSTRUCTIONS,
        provider.VIBESCRIPT_AUTHORING_INSTRUCTIONS,
    ]
    assert all(block["cache_control"] == {"type": "ephemeral"} for block in blocks)

    other_blocks = provider._anthropic_system_blocks(
        {"provider_tool_schemas": [{"name": "build123d.create_model"}]}
    )
    assert [block["text"] for block in other_blocks] == [
        provider.VIBECAD_SYSTEM_INSTRUCTIONS
    ]


def test_both_wire_formats_order_vibescript_guidance_before_intent_memory() -> None:
    context = _vibescript_mode_context()
    context["intent_memory_enabled"] = True
    context["intent_memory"] = {"revision": "r1"}

    instructions = provider._provider_instructions(context)
    assert instructions.index(
        provider.VIBESCRIPT_AUTHORING_INSTRUCTIONS
    ) < instructions.index("VIBECAD INTENT MEMORY")

    blocks = provider._anthropic_system_blocks(context)
    assert len(blocks) == 3
    assert blocks[1]["text"] == provider.VIBESCRIPT_AUTHORING_INSTRUCTIONS
    assert blocks[2]["text"].startswith("VIBECAD INTENT MEMORY")


def test_vibescript_guidance_contains_only_cad_authoring_text() -> None:
    text = provider.VIBESCRIPT_AUTHORING_INSTRUCTIONS.lower()
    for foreign_term in (
        "anthropic",
        "openai",
        "claude",
        "gpt",
        "gemini",
        "provider",
        "vendor",
        "llm",
        "api key",
    ):
        assert foreign_term not in text, (
            f"VibeScript guidance must stay CAD-only; found {foreign_term!r}"
        )


class _ProviderContextService:
    def __init__(self, workbench: str, base_context: dict[str, object]) -> None:
        self.workbench = workbench
        self.base_context = base_context
        self.vibescript_context_calls = 0

    def provider_context_summary(self) -> dict[str, object]:
        return dict(self.base_context)

    def active_workbench_name(self) -> str:
        return self.workbench

    def provider_debug_config(self) -> dict[str, object]:
        return {"enabled": False}

    def provider_name(self) -> str:
        return "openai"

    def intent_memory_snapshot(self) -> dict[str, object]:
        return {"enabled": False}

    def vibescript_context(self) -> dict[str, object]:
        self.vibescript_context_calls += 1
        return {"models": [{"model_id": "a" * 32, "name": "Impeller"}]}


def _context_schema(name: str) -> dict[str, object]:
    return {
        "name": name,
        "description": f"Call {name}.",
        "parameters": {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        },
    }


def test_vibescript_model_context_follows_its_surfaced_tools(
    monkeypatch,
) -> None:
    schemas = [
        _context_schema("assembly.solve"),
        _context_schema("vibescript.inspect_model"),
    ]
    monkeypatch.setattr(session, "provider_tool_schemas", lambda _service, _wb: schemas)
    service = _ProviderContextService(
        "AssemblyWorkbench",
        {"cad_state": {}, "assembly": {"assemblies": []}},
    )

    context = session._context_for_provider(service)

    assert context["vibescript"]["models"] == [
        {"model_id": "a" * 32, "name": "Impeller"}
    ]
    assert (
        "not automatically reconciled" in context["vibescript"]["regeneration_warning"]
    )
    assert service.vibescript_context_calls == 1
    assert (
        provider._model_visible_context(context)["vibescript"] == context["vibescript"]
    )


def test_vibescript_context_is_absent_when_its_tools_are_not_surfaced(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        session,
        "provider_tool_schemas",
        lambda _service, _wb: [_context_schema("bim.list_structure")],
    )
    service = _ProviderContextService(
        "BIMWorkbench",
        {"cad_state": {}, "bim": {"buildings": []}},
    )

    context = session._context_for_provider(service)

    assert "vibescript" not in context
    assert service.vibescript_context_calls == 0


def test_partdesign_keeps_one_vibescript_model_context_without_duplication(
    monkeypatch,
) -> None:
    models = [{"model_id": "b" * 32, "name": "Rotor"}]
    monkeypatch.setattr(
        session,
        "provider_tool_schemas",
        lambda _service, _wb: [_context_schema("vibescript.inspect_model")],
    )
    service = _ProviderContextService(
        "PartDesignWorkbench",
        {"cad_state": {}, "partdesign": {"models": models}},
    )

    context = session._context_for_provider(service)

    assert context["partdesign"] == {"models": models}
    assert "vibescript" not in context
    assert service.vibescript_context_calls == 0


class _ResponsesItem:
    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = dict(payload)
        for key, value in payload.items():
            setattr(self, key, value)

    def model_dump(self, *, mode: str, exclude_none: bool) -> dict[str, object]:
        assert mode == "json"
        assert exclude_none
        return dict(self.payload)


class _ResponsesStream:
    def __init__(self, events: list[SimpleNamespace]) -> None:
        self.events = events
        self.closed = False

    def __iter__(self):
        return iter(self.events)

    def close(self) -> None:
        self.closed = True


class _FakeResponses:
    def __init__(self) -> None:
        self.requests: list[dict[str, object]] = []

    def create(self, **request):
        self.requests.append(request)
        if len(self.requests) == 1:
            reasoning = _ResponsesItem(
                {
                    "type": "reasoning",
                    "id": "reasoning_1",
                    "summary": [],
                    "encrypted_content": "opaque-reasoning-state",
                }
            )
            function_call = _ResponsesItem(
                {
                    "type": "function_call",
                    "id": "function_1",
                    "call_id": "call_1",
                    "name": "test_echo",
                    "arguments": json.dumps({"value": "hello"}),
                    "status": "completed",
                }
            )
            completed = SimpleNamespace(
                id="response_1",
                output=[reasoning, function_call],
                output_text="",
            )
            return _ResponsesStream(
                [
                    SimpleNamespace(
                        type="response.output_item.done",
                        item=function_call,
                    ),
                    SimpleNamespace(type="response.completed", response=completed),
                ]
            )
        completed = SimpleNamespace(
            id="response_2",
            output=[
                _ResponsesItem(
                    {
                        "type": "message",
                        "id": "message_1",
                        "role": "assistant",
                        "status": "completed",
                        "content": [
                            {
                                "type": "output_text",
                                "text": "finished",
                                "annotations": [],
                            }
                        ],
                    }
                )
            ],
            output_text="finished",
        )
        return _ResponsesStream(
            [SimpleNamespace(type="response.completed", response=completed)]
        )


class _FakeOpenAI:
    instance = None

    def __init__(self, **_kwargs) -> None:
        self.responses = _FakeResponses()
        _FakeOpenAI.instance = self


class _OpenAIChildConnection:
    def __init__(self, context: dict[str, object]) -> None:
        self.context = context
        self.sent: list[dict[str, object]] = []
        self.closed = False

    def send(self, message: dict[str, object]) -> None:
        self.sent.append(message)

    def recv(self) -> dict[str, object]:
        return {
            "type": "tool_result",
            "result": {"ok": True, "echo": "hello"},
            "context": self.context,
        }

    def close(self) -> None:
        self.closed = True


def test_openai_tool_loop_manages_response_history_without_response_ids(
    monkeypatch,
) -> None:
    openai_module = ModuleType("openai")
    openai_module.OpenAI = _FakeOpenAI
    monkeypatch.setitem(sys.modules, "openai", openai_module)
    context = {
        "provider_tool_schemas": [
            {
                "name": "test.echo",
                "description": "Return the supplied value.",
                "parameters": {
                    "type": "object",
                    "properties": {"value": {"type": "string"}},
                    "required": ["value"],
                    "additionalProperties": False,
                },
            }
        ]
    }
    connection = _OpenAIChildConnection(context)

    provider._openai_child_main(
        connection,
        prompt="Use the tool.",
        context=context,
        model="test-model",
        api_key="test-key",
        reasoning_effort="high",
        timeout_seconds=None,
        max_turns=3,
        clear_inherited_modules=False,
    )

    requests = _FakeOpenAI.instance.responses.requests
    assert len(requests) == 2
    assert all("previous_response_id" not in request for request in requests)
    assert all(request["instructions"] for request in requests)
    assert all(
        request["include"] == ["reasoning.encrypted_content"] for request in requests
    )
    second_input = requests[1]["input"]
    assert [item["type"] for item in second_input[1:]] == [
        "reasoning",
        "function_call",
        "function_call_output",
    ]
    assert second_input[1]["encrypted_content"] == "opaque-reasoning-state"
    tool_output = json.loads(second_input[-1]["output"])
    assert tool_output["ok"] is True
    assert tool_output["echo"] == "hello"
    assert any(message.get("type") == "done" for message in connection.sent)
    assert connection.closed
