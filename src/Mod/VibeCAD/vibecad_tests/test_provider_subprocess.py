# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression coverage for provider subprocess lifecycle races."""

from __future__ import annotations

import json
import sys
from types import SimpleNamespace

import pytest

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


def test_linux_provider_uses_clean_spawn_instead_of_gui_process_fork() -> None:
    if sys.platform != "linux":
        pytest.skip("Linux-specific provider process contract")

    python_executable = provider._provider_spawn_python_executable()
    assert python_executable
    assert "python" in python_executable.rsplit("/", 1)[-1].lower()
    assert provider._provider_multiprocessing_context().get_start_method() == "spawn"


def test_provider_stream_deltas_are_batched_before_gui_delivery() -> None:
    now = [0.0]
    events: list[dict[str, object]] = []
    batcher = provider._ProviderStreamDeltaBatcher(
        events.append,
        provider="Anthropic",
        turn=3,
        flush_seconds=0.075,
        clock=lambda: now[0],
    )

    for fragment in ("one", " ", "small", " ", "update"):
        batcher.append("provider_reasoning_delta", fragment)
    assert events == []

    now[0] = 0.08
    batcher.append("provider_reasoning_delta", ".")
    batcher.append("provider_text_delta", "Result")
    batcher.append("provider_text_delta", " ready")
    batcher.flush()

    assert events == [
        {
            "event": "provider_reasoning_delta",
            "provider": "Anthropic",
            "turn": 3,
            "text": "one small update.",
        },
        {
            "event": "provider_text_delta",
            "provider": "Anthropic",
            "turn": 3,
            "text": "Result ready",
        },
    ]


class _CollectingConnection:
    def __init__(self) -> None:
        self.messages: list[dict[str, object]] = []
        self.closed = False

    def send(self, message: dict[str, object]) -> None:
        self.messages.append(message)

    def close(self) -> None:
        self.closed = True


class _EmptyAnthropicStream:
    def __enter__(self):
        return self

    def __exit__(self, *_args) -> None:
        return None

    def __iter__(self):
        return iter(())

    @staticmethod
    def get_final_message():
        return SimpleNamespace(content=[], stop_reason="end_turn")


def test_anthropic_empty_completion_returns_explicit_error(monkeypatch) -> None:
    requests: list[dict[str, object]] = []

    def stream(**request):
        requests.append(request)
        return _EmptyAnthropicStream()

    anthropic_module = SimpleNamespace(
        Anthropic=lambda **_kwargs: SimpleNamespace(
            messages=SimpleNamespace(stream=stream)
        ),
        BadRequestError=type("BadRequestError", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_module)
    monkeypatch.setattr(
        provider, "_validate_provider_wire_surface", lambda _context: None
    )
    connection = _CollectingConnection()

    provider._anthropic_child_main(
        connection,
        "Inspect the selected model.",
        {"provider_tool_schemas": []},
        "test-model",
        "test-key",
        None,
        1.0,
        1,
        False,
    )

    terminal = [
        message for message in connection.messages if message.get("type") == "error"
    ]
    assert len(terminal) == 1
    assert "without any user-visible text" in str(terminal[0]["error"])
    assert requests[0]["cache_control"] == {"type": "ephemeral"}
    assert connection.closed


def test_anthropic_child_forwards_the_exact_tool_use_id(monkeypatch) -> None:
    tool_block = SimpleNamespace(
        type="tool_use",
        id="anthropic-call-17",
        name="state_read",
        input={"scope": "selection", "include_geometry": False},
    )
    text_block = SimpleNamespace(type="text", text="Done.")
    responses = iter(
        (
            SimpleNamespace(content=[tool_block], stop_reason="tool_use"),
            SimpleNamespace(content=[text_block], stop_reason="end_turn"),
        )
    )

    class _Stream:
        def __init__(self, response):
            self.response = response

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def __iter__(self):
            return iter(())

        def get_final_message(self):
            return self.response

    requests: list[dict[str, object]] = []

    def stream(**request):
        requests.append(request)
        return _Stream(next(responses))

    anthropic_module = SimpleNamespace(
        Anthropic=lambda **_kwargs: SimpleNamespace(
            messages=SimpleNamespace(stream=stream)
        ),
        BadRequestError=type("BadRequestError", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_module)
    monkeypatch.setattr(
        provider,
        "_validate_provider_wire_surface",
        lambda _context: None,
    )

    context = {
        "workbench": "PartDesignWorkbench",
        "modeling_surface": {
            "workbench": "PartDesignWorkbench",
            "engine": "vibescript",
            "domain": "partdesign",
            "available": True,
        },
        "provider_tool_schemas": [
            {
                "name": "state.read",
                "description": "Read exact state.",
                "parameters": {
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            }
        ]
    }

    class _Connection(_CollectingConnection):
        def recv(self):
            return {
                "type": "tool_result",
                "result": {"ok": True},
                "context": context,
            }

    connection = _Connection()
    provider._anthropic_child_main(
        connection,
        "Read state.",
        context,
        "test-model",
        "test-key",
        None,
        1.0,
        3,
        False,
    )

    tool_messages = [
        message for message in connection.messages if message.get("type") == "tool"
    ]
    assert tool_messages == [
        {
            "type": "tool",
            "tool_name": "state.read",
            "arguments_json": '{"scope":"selection","include_geometry":false}',
            "provider_call_id": "anthropic-call-17",
        }
    ]
    second_messages = requests[1]["messages"]
    tool_result_message = next(
        message
        for message in reversed(second_messages)
        if message.get("role") == "user"
        and isinstance(message.get("content"), list)
        and message["content"]
        and message["content"][0].get("type") == "tool_result"
    )
    tool_result = tool_result_message["content"][0]
    assert tool_result == {
        "type": "tool_result",
        "tool_use_id": "anthropic-call-17",
        "content": '{"ok":true}',
    }
    assert any(message.get("type") == "done" for message in connection.messages)
    assert connection.closed


def test_anthropic_turn_compaction_packet_excludes_deterministic_payloads() -> None:
    prompt = (
        "VIBECAD_CONTEXT_JSON\n"
        '{"active_state":{"raw_geometry":"DO_NOT_SEND_CAD_STATE"}}\n'
        "END_VIBECAD_CONTEXT_JSON\n\n"
        "RECENT_CONVERSATION_JSON\n"
        '{"turns":[{"role":"user","content":"Keep the mounting datum."}],'
        '"omitted_turn_count":0,"truncated_turn_count":0}\n'
        "END_RECENT_CONVERSATION_JSON\n\n"
        "CURRENT_USER_MESSAGE\nRebuild the bracket with native features."
    )
    event = provider._anthropic_compaction_tool_event(
        "vibescript.edit_source",
        {
            "source_id": "source-1",
            "expected_revision": "revision-1",
            "source": "DO_NOT_SEND_SOURCE = 'raw source text'",
            "input_schema": {"DO_NOT_SEND_SCHEMA": True},
        },
        {
            "ok": False,
            "failure_code": "BUILD_FAILED",
            "error": "Pocket removed no material.",
            "stdout": "DO_NOT_SEND_LOG",
            "vibecad_state_after": {"DO_NOT_SEND_STATE": True},
        },
    )

    packet = provider._anthropic_turn_compaction_packet(
        prompt=prompt,
        tool_events=[event],
        assistant_progress=["I inspected the mounting face."],
        previous_compaction=None,
        generation=1,
    )
    encoded = json.dumps(packet, sort_keys=True)

    assert "Rebuild the bracket with native features." in encoded
    assert "Keep the mounting datum." in encoded
    assert "Pocket removed no material." in encoded
    assert "DO_NOT_SEND_CAD_STATE" not in encoded
    assert "DO_NOT_SEND_SOURCE" not in encoded
    assert "raw source text" not in encoded
    assert "DO_NOT_SEND_SCHEMA" not in encoded
    assert "DO_NOT_SEND_LOG" not in encoded
    assert "DO_NOT_SEND_STATE" not in encoded
    assert (
        len(encoded.encode("utf-8"))
        <= provider.ANTHROPIC_TURN_COMPACTION_MAX_INPUT_BYTES
    )


def test_anthropic_turn_compaction_runs_on_its_named_worker_thread() -> None:
    calls: list[dict[str, object]] = []

    class _Messages:
        @staticmethod
        def create(**request):
            calls.append(
                {
                    "request": request,
                    "thread": provider.threading.current_thread().name,
                }
            )
            return SimpleNamespace(
                content=[
                    SimpleNamespace(
                        type="tool_use",
                        name="commit_turn_compaction",
                        input={
                            "current_request": "Continue.",
                            "requirements": [],
                            "completed_actions": [],
                            "live_artifacts": [],
                            "open_issues": [],
                            "next_action": "Continue.",
                        },
                    )
                ],
                stop_reason="tool_use",
            )

    anthropic_module = SimpleNamespace(
        Anthropic=lambda **_kwargs: SimpleNamespace(messages=_Messages())
    )
    result = provider._anthropic_compact_turn_in_thread(
        anthropic_module=anthropic_module,
        client_kwargs={"api_key": "test-key"},
        model="memory-model",
        packet={"current_request": "Continue."},
        debug_context={},
        base_url=None,
        generation=1,
    )

    assert result["next_action"] == "Continue."
    assert len(calls) == 1
    assert calls[0]["thread"] == "VibeCAD-Anthropic-Turn-Compaction"
    request = calls[0]["request"]
    assert request["model"] == "memory-model"
    assert request["tool_choice"] == {
        "type": "tool",
        "name": "commit_turn_compaction",
    }


class _SequenceAnthropicMessages:
    def __init__(self, responses: list[object]) -> None:
        self.responses = iter(responses)

    def stream(self, **_kwargs):
        response = next(self.responses)

        class _Stream:
            def __enter__(self):
                return self

            def __exit__(self, *_args) -> None:
                return None

            def __iter__(self):
                return iter(())

            @staticmethod
            def get_final_message():
                return response

        return _Stream()


def test_anthropic_thinking_only_max_tokens_compacts_and_continues(
    monkeypatch,
) -> None:
    responses = [
        SimpleNamespace(
            content=[
                SimpleNamespace(
                    type="thinking",
                    thinking="private reasoning must not become compaction input",
                    signature="signed",
                )
            ],
            stop_reason="max_tokens",
        ),
        SimpleNamespace(
            content=[SimpleNamespace(type="text", text="Finished cleanly.")],
            stop_reason="end_turn",
        ),
    ]
    messages = _SequenceAnthropicMessages(responses)
    anthropic_module = SimpleNamespace(
        Anthropic=lambda **_kwargs: SimpleNamespace(messages=messages),
        BadRequestError=type("BadRequestError", (Exception,), {}),
    )
    monkeypatch.setitem(sys.modules, "anthropic", anthropic_module)
    monkeypatch.setattr(
        provider, "_validate_provider_wire_surface", lambda _context: None
    )
    compaction_calls: list[dict[str, object]] = []

    def compact(**kwargs):
        compaction_calls.append(kwargs)
        return {
            "current_request": "Inspect the selected model.",
            "requirements": [],
            "completed_actions": [],
            "live_artifacts": [],
            "open_issues": [],
            "next_action": "Return the result.",
        }

    monkeypatch.setattr(provider, "_anthropic_compact_turn_in_thread", compact)
    connection = _CollectingConnection()

    provider._anthropic_child_main(
        connection,
        "Inspect the selected model.",
        {
            "provider_tool_schemas": [],
            "_vibecad_provider_options": {
                "compaction_model": "memory-model"
            },
        },
        "interactive-model",
        "test-key",
        None,
        1.0,
        2,
        False,
    )

    terminal = [
        message
        for message in connection.messages
        if message.get("type") in {"done", "error"}
    ]
    assert terminal == [
        {"type": "done", "final_output": "Finished cleanly.", "raw": None}
    ]
    assert len(compaction_calls) == 1
    assert compaction_calls[0]["model"] == "memory-model"
    packet_text = json.dumps(compaction_calls[0]["packet"])
    assert "private reasoning" not in packet_text
    progress_events = [
        message.get("event", {})
        for message in connection.messages
        if message.get("type") == "progress"
    ]
    assert any(
        event.get("event") == "anthropic_turn_compaction_started"
        for event in progress_events
    )
    assert any(
        event.get("event") == "anthropic_turn_compaction_completed"
        for event in progress_events
    )
    assert connection.closed


def _vibescript_mode_context(
    workbench: str = "PartDesignWorkbench",
    domain: str = "partdesign",
) -> dict[str, object]:
    return {
        "workbench": workbench,
        "modeling_surface": {
            "workbench": workbench,
            "engine": "vibescript",
            "domain": domain,
            "available": True,
        },
        "provider_tool_schemas": [
            {
                "name": f"vibescript.{domain}.create_program",
                "description": "Create a VibeScript model.",
                "parameters": {"type": "object"},
            }
        ],
    }


def test_system_instructions_are_surface_neutral_and_compact() -> None:
    text = provider.VIBECAD_SYSTEM_INSTRUCTIONS
    assert "CURRENT_USER_MESSAGE controls" in text
    assert "Use only exposed tools and exact returned state" in text
    assert "Preserve existing identity and history" in text
    assert "Never claim work or verification not performed" in text
    assert len(text.encode("utf-8")) < 1_000
    for surface_specific in (
        "core.set_view",
        "screenshot",
        "isometric",
        "Aero",
        "catalog",
        "parametric geometry",
        "STEP",
        "STL",
    ):
        assert surface_specific not in text


def test_provider_instructions_stay_within_deterministic_byte_limit() -> None:
    empty = provider._provider_instructions({})
    assert empty == provider.VIBECAD_SYSTEM_INSTRUCTIONS
    assert (
        len(empty.encode("utf-8")) <= provider.MAX_PROVIDER_INSTRUCTIONS_BYTES
    )
    for context in (
        _vibescript_mode_context(),
        _vibescript_mode_context("AssemblyWorkbench", "assembly"),
    ):
        instructions = provider._provider_instructions(context)
        assert (
            len(instructions.encode("utf-8"))
            <= provider.MAX_PROVIDER_INSTRUCTIONS_BYTES
        )


def test_instructions_include_vibescript_guidance_only_in_vibescript_mode() -> None:
    context = _vibescript_mode_context()
    guidance = provider._vibescript_authoring_instruction(context)
    instructions = provider._provider_instructions(context)
    assert instructions.startswith(provider.VIBECAD_SYSTEM_INSTRUCTIONS)
    assert "catalog" not in provider.VIBECAD_SYSTEM_INSTRUCTIONS
    assert "do not search component" in guidance
    assert (
        "a correction changes only the named result"
        in provider.VIBECAD_SYSTEM_INSTRUCTIONS
    )
    assert "Preserve existing identity and history" in provider.VIBECAD_SYSTEM_INSTRUCTIONS
    assert guidance
    assert guidance in instructions
    assert "Extrude constant sections" in guidance
    assert "loft only changing sections" in guidance

    assembly_guidance = provider._vibescript_authoring_instruction(
        _vibescript_mode_context("AssemblyWorkbench", "assembly")
    )
    assert "VIBESCRIPT MODEL + ASSEMBLY" in assembly_guidance
    assert "Extrude constant sections" in assembly_guidance
    assert "loft only changing sections" in assembly_guidance
    assert "occurrences, joints, and motion" in assembly_guidance

    for other_context in (
        {},
        {"provider_tool_schemas": []},
        {"provider_tool_schemas": [{"name": "partdesign.pad"}]},
    ):
        other = provider._provider_instructions(other_context)
        assert guidance not in other
        assert other.startswith(provider.VIBECAD_SYSTEM_INSTRUCTIONS)


def test_system_blocks_carry_vibescript_guidance_only_in_vibescript_mode() -> None:
    context = _vibescript_mode_context()
    guidance = provider._vibescript_authoring_instruction(context)
    blocks = provider._anthropic_system_blocks(context)
    texts = [block["text"] for block in blocks]
    assert texts == [
        provider.VIBECAD_SYSTEM_INSTRUCTIONS,
        guidance,
    ]
    assert "cache_control" not in blocks[0]
    assert blocks[-1]["cache_control"] == {"type": "ephemeral"}

    other_blocks = provider._anthropic_system_blocks(
        {"provider_tool_schemas": [{"name": "core.set_view"}]}
    )
    assert [block["text"] for block in other_blocks] == [
        provider.VIBECAD_SYSTEM_INSTRUCTIONS
    ]
    assert other_blocks[-1]["cache_control"] == {"type": "ephemeral"}


def test_anthropic_durable_images_form_a_cached_prefix(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_context_image_blocks",
        lambda _context: [
            ("R1/2:first.png", "image/png", "first"),
            ("R2/2:second.png", "image/png", "second"),
            ("V:current", "image/jpeg", "viewport"),
        ],
    )
    monkeypatch.setattr(
        provider,
        "_context_image_delivery_notes",
        lambda _context: [],
    )

    content = provider._anthropic_user_content("CURRENT REQUEST", {})

    assert isinstance(content, list)
    assert [block["type"] for block in content] == [
        "text",
        "image",
        "text",
        "image",
        "text",
        "text",
        "image",
    ]
    assert content[0]["text"] == "R1/2:first.png"
    assert "cache_control" not in content[1]
    assert content[2]["text"] == "R2/2:second.png"
    assert content[3]["cache_control"] == {"type": "ephemeral"}
    assert content[4] == {"type": "text", "text": "CURRENT REQUEST"}
    assert content[5]["text"] == "V:current"
    assert "cache_control" not in content[6]


def test_anthropic_view_repin_does_not_repeat_durable_references(monkeypatch) -> None:
    seen_contexts: list[dict[str, object]] = []

    def image_blocks(context):
        seen_contexts.append(dict(context))
        blocks = []
        if context.get("reference_images"):
            blocks.append(("R1/1:reference.png", "image/png", "reference"))
        if context.get("view_screenshot"):
            blocks.append(("V:current", "image/jpeg", "viewport"))
        return blocks

    monkeypatch.setattr(provider, "_context_image_blocks", image_blocks)
    content = provider._anthropic_visual_repin_content(
        {"reference_images": {"images": [{"name": "reference.png"}]}},
        {"captured": True, "new_observation": True},
    )

    assert "reference_images" not in seen_contexts[0]
    assert [block.get("text") for block in content if block["type"] == "text"] == [
        "Current viewport observation captured after the preceding CAD operation.",
        "V:current",
    ]


def test_anthropic_compaction_resume_reattaches_durable_references(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        provider,
        "_context_image_blocks",
        lambda context: (
            [("R1/1:reference.png", "image/png", "reference")]
            if context.get("reference_images")
            else []
        ),
    )
    monkeypatch.setattr(
        provider,
        "_context_image_delivery_notes",
        lambda _context: [],
    )

    content = provider._anthropic_compaction_resume_content(
        {"current_request": "Finish the bracket."},
        {
            "reference_images": {
                "count": 1,
                "images": [{"name": "reference.png"}],
            }
        },
    )

    assert isinstance(content, list)
    assert content[0] == {"type": "text", "text": "R1/1:reference.png"}
    assert content[1]["type"] == "image"
    assert content[1]["cache_control"] == {"type": "ephemeral"}
    assert '"Finish the bracket."' in content[2]["text"]


def test_anthropic_tools_rely_on_the_cumulative_system_cache_prefix() -> None:
    definitions = [
        {
            "name": "core__inspect",
            "description": "Inspect exact state.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
        {
            "name": "core__set_view",
            "description": "Set the view.",
            "input_schema": {
                "type": "object",
                "properties": {},
                "additionalProperties": False,
            },
        },
    ]

    cad_only = provider._anthropic_request_tools(definitions, False)
    with_web = provider._anthropic_request_tools(definitions, True)

    assert "cache_control" not in definitions[-1]
    assert all("cache_control" not in tool for tool in cad_only)
    assert all("cache_control" not in tool for tool in with_web)
    assert with_web[-1]["name"] == "web_search"
    assert provider._anthropic_request_tools([], False) == []


def test_anthropic_recent_conversation_forms_a_growing_cached_prefix() -> None:
    prompt = session._provider_prompt(
        "Continue with only the mounting holes.",
        _vibescript_mode_context(),
        recent_conversation=[
            {"role": "user", "content": "Build the bracket."},
            {"role": "assistant", "content": "The bracket body is complete."},
        ],
    )

    content = provider._anthropic_user_content(prompt, {})

    assert isinstance(content, list)
    assert len(content) == 3
    assert content[0]["type"] == "text"
    assert '"role":"user"' in content[0]["text"]
    assert "Build the bracket." in content[0]["text"]
    assert "cache_control" not in content[0]
    assert content[1]["type"] == "text"
    assert '"role":"assistant"' in content[1]["text"]
    assert "The bracket body is complete." in content[1]["text"]
    assert content[1]["cache_control"] == {"type": "ephemeral"}
    assert content[2]["type"] == "text"
    assert "Continue with only the mounting holes." in content[2]["text"]
    assert "Build the bracket." not in content[2]["text"]
    assert "The bracket body is complete." not in content[2]["text"]
    assert '"turns":[]' in content[2]["text"]
    assert '"delivered_turn_count":2' in content[2]["text"]
    assert (
        '"turn_delivery":"preceding_RECENT_CONVERSATION_TURN_JSON_blocks"'
        in content[2]["text"]
    )


def test_anthropic_authoring_contract_joins_the_static_cached_prefix() -> None:
    context = _vibescript_mode_context()
    context["editable_sources"] = {
        "schema": "vibecad-editable-sources-v1",
        "source_count": 1,
        "sources": [{"program": "BracketProgram"}],
        "core_api": {
            "schema": "vibecad-authoring-contract-v1",
            "functions": ["STABLE_CONTRACT_SENTINEL" + ("x" * 512)],
        },
    }
    prompt = session._provider_prompt("Build the bracket.", context)

    content = provider._anthropic_user_content(prompt, {})

    assert isinstance(content, list)
    assert len(content) == 2
    assert "VIBESCRIPT_AUTHORING_CONTRACT_JSON" in content[0]["text"]
    assert "STABLE_CONTRACT_SENTINEL" in content[0]["text"]
    assert content[0]["cache_control"] == {"type": "ephemeral"}
    assert "Build the bracket." in content[1]["text"]
    assert "STABLE_CONTRACT_SENTINEL" not in content[1]["text"]
    assert '"delivered_as_preceding_content_block":true' in content[1]["text"]


def test_anthropic_cache_layout_stays_within_four_breakpoints(monkeypatch) -> None:
    monkeypatch.setattr(
        provider,
        "_context_image_blocks",
        lambda _context: [("R1/1:reference.png", "image/png", "reference")],
    )
    monkeypatch.setattr(
        provider,
        "_context_image_delivery_notes",
        lambda _context: [],
    )
    context = _vibescript_mode_context()
    context["editable_sources"] = {
        "schema": "vibecad-editable-sources-v1",
        "source_count": 1,
        "sources": [{"program": "BracketProgram"}],
        "core_api": {
            "schema": "vibecad-authoring-contract-v1",
            "functions": ["stable.call"],
        },
    }
    prompt = session._provider_prompt(
        "Continue.",
        context,
        recent_conversation=[
            {"role": "user", "content": "Build it."},
            {"role": "assistant", "content": "Built."},
        ],
    )
    request = {
        "cache_control": {"type": "ephemeral"},
        "system": provider._anthropic_system_blocks(context),
        "tools": provider._anthropic_request_tools(
            [
                {
                    "name": "core__inspect",
                    "description": "Inspect.",
                    "input_schema": {"type": "object"},
                }
            ],
            False,
        ),
        "messages": [
            {
                "role": "user",
                "content": provider._anthropic_user_content(prompt, context),
            }
        ],
    }

    def breakpoint_count(value) -> int:
        if isinstance(value, dict):
            return int("cache_control" in value) + sum(
                breakpoint_count(item)
                for key, item in value.items()
                if key != "cache_control"
            )
        if isinstance(value, list):
            return sum(breakpoint_count(item) for item in value)
        return 0

    assert breakpoint_count(request) == 4
    user_content = request["messages"][0]["content"]
    reference_image = next(
        block for block in user_content if block.get("type") == "image"
    )
    contract = next(
        block
        for block in user_content
        if block.get("type") == "text"
        and "VIBESCRIPT_AUTHORING_CONTRACT_JSON" in block.get("text", "")
        and "delivered_as_preceding_content_block" not in block.get("text", "")
    )
    assert "cache_control" not in reference_image
    assert contract["cache_control"] == {"type": "ephemeral"}


def test_anthropic_response_summary_reports_cache_token_usage() -> None:
    response = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="Done.")],
        stop_reason="end_turn",
        usage={
            "input_tokens": 240,
            "output_tokens": 18,
            "cache_creation_input_tokens": 1200,
            "cache_read_input_tokens": 8400,
            "cache_creation": {
                "ephemeral_5m_input_tokens": 1200,
                "ephemeral_1h_input_tokens": 0,
            },
        },
    )

    summary = provider._anthropic_response_summary(response)

    assert summary["token_usage"] == response.usage


def test_both_wire_formats_do_not_inject_intent_memory() -> None:
    context = _vibescript_mode_context()
    context["intent_memory_enabled"] = True
    context["intent_memory"] = {"revision": "r1"}

    guidance = provider._vibescript_authoring_instruction(context)
    instructions = provider._provider_instructions(context)
    assert guidance in instructions
    assert "VIBECAD INTENT MEMORY" not in instructions

    blocks = provider._anthropic_system_blocks(context)
    assert len(blocks) == 2
    assert blocks[1]["text"] == guidance


def test_vibescript_guidance_contains_only_cad_authoring_text() -> None:
    context = _vibescript_mode_context()
    text = provider._vibescript_authoring_instruction(context).lower()
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
    for removed_contract in ("params", "new_body", "new_sketch", "sketchbuilder"):
        assert removed_contract not in text
    assert "vibescript_authoring_contract_json" in text
    assert "read_operation" in text
    assert "read_source" in text
    assert "edit_source" in text
    assert "build_program" in text
    assert "set_inputs" in text
    assert "api.name" in text


def test_vibescript_guidance_keeps_lifecycle_rules_concise_across_domains() -> None:
    partdesign = provider._vibescript_authoring_instruction(_vibescript_mode_context())
    assembly = provider._vibescript_authoring_instruction(
        _vibescript_mode_context("AssemblyWorkbench", "assembly")
    )
    for instruction in (partdesign, assembly):
        assert "failed create without program/revision saved nothing" in instruction
        assert "read_source before edit_source" in instruction
        assert "build_program runs unchanged code" in instruction
        assert "set_inputs" in instruction
        assert "reconfigure_program" not in instruction
        assert "before writing the first program" not in instruction
        assert "after success" not in instruction


def test_complete_source_reads_are_not_cut_down_to_the_normal_tool_result_limit() -> (
    None
):
    source = "value = 1\n" * 5000
    visible = provider._provider_visible_tool_result(
        {
            "ok": True,
            "source_id": "a" * 32,
            "current_revision": "b" * 64,
            "source": source,
            "_vibecad_complete_source_result": True,
        }
    )

    assert visible["source"] == source
    assert "source_id" not in visible
    assert "vibecad_result_boundary" not in visible
    assert "_vibecad_complete_source_result" not in visible


def test_native_mutation_result_keeps_cad_facts_and_hides_host_bookkeeping() -> None:
    visible = provider._provider_visible_tool_result(
        {
            "ok": True,
            "changed": True,
            "mode": "edit",
            "operation": "trajectory_compound",
            "trajectory": {
                "document_uid": "document-uid",
                "object_id": 42,
                "object_name": "TrajectorySequence",
                "type_id": "Robot::TrajectoryCompound",
            },
            "sources": [
                {
                    "document_uid": "document-uid",
                    "object_id": 41,
                    "object_name": "EdgeTrajectory",
                    "type_id": "Robot::Edge2TracObject",
                }
            ],
            "feature": {
                "kind": "compound",
                "sources": [
                    {
                        "document_uid": "document-uid",
                        "object_id": 41,
                        "object_name": "EdgeTrajectory",
                        "type_id": "Robot::Edge2TracObject",
                    }
                ],
            },
            "waypoint_count": 2,
            "waypoint": {
                "name": "Pt",
                "state_sha256": "c" * 64,
            },
            "trajectory_state_sha256": "a" * 64,
            "trajectory_setup_state_sha256": "b" * 64,
            "receipt": {
                "capability": "robot.path_sequence",
                "revision_before": 10,
                "revision_after": 11,
                "changed": ["TrajectorySequence"],
            },
            "assistant_undo_available": True,
        }
    )

    assert visible == {
        "ok": True,
        "changed": True,
        "mode": "edit",
        "operation": "trajectory_compound",
        "trajectory": {
            "object_name": "TrajectorySequence",
            "type_id": "Robot::TrajectoryCompound",
        },
        "feature": {
            "kind": "compound",
            "sources": [
                {
                    "object_name": "EdgeTrajectory",
                    "type_id": "Robot::Edge2TracObject",
                }
            ],
        },
        "waypoint_count": 2,
        "waypoint": {"name": "Pt"},
        "assistant_undo_available": True,
    }


def test_native_mutation_result_keeps_exact_follow_up_target() -> None:
    digest = "d" * 64

    visible = provider._provider_visible_tool_result(
        {
            "_vibecad_native_result": True,
            "ok": True,
            "changed": True,
            "analysis_target": {
                "object_name": "Analysis",
                "expected_state_sha256": digest,
                "expected_member_count": 3,
            },
            "analysis_state_sha256": "e" * 64,
        }
    )

    assert visible == {
        "ok": True,
        "changed": True,
        "analysis_target": {
            "object_name": "Analysis",
            "expected_state_sha256": digest,
            "expected_member_count": 3,
        },
    }


def test_native_mutation_result_turns_object_state_into_a_copyable_target() -> None:
    digest = "d" * 64

    visible = provider._provider_visible_tool_result(
        {
            "_vibecad_native_result": True,
            "ok": True,
            "page": {
                "object_name": "Page",
                "state_sha256": digest,
                "view_count": 1,
            },
        }
    )

    assert visible == {
        "ok": True,
        "page": {
            "object_name": "Page",
            "expected_state_sha256": digest,
            "view_count": 1,
        },
    }


def test_terminal_native_job_compacts_its_mutation_result() -> None:
    digest = "e" * 64

    visible = provider._provider_visible_tool_result(
        {
            "ok": True,
            "job": {
                "job_id": "a" * 32,
                "terminal": True,
                "result": {
                    "page": {
                        "object_name": "Page",
                        "state_sha256": digest,
                    },
                    "receipt": {"capability": "drawing.projection_group"},
                },
            },
        }
    )

    assert visible["job"]["result"] == {
        "page": {
            "object_name": "Page",
        }
    }


def test_native_drawing_inspection_keeps_one_copyable_projection_target() -> None:
    projection = "a" * 64
    element = "b" * 64

    visible = provider._provider_visible_tool_result(
        {
            "_vibecad_native_result": True,
            "ok": True,
            "expected_element_count": 1,
            "elements": [
                {
                    "name": "Edge1",
                    "element_state_sha256": element,
                    "visible": True,
                }
            ],
            "view": {
                "object_name": "Front",
                "type_id": "TechDraw::DrawViewPart",
                "view_state_sha256": "c" * 64,
                "projection_state_sha256": projection,
            },
        },
        tool_name="drawing.projected_geometry",
    )

    assert visible == {
        "ok": True,
        "expected_element_count": 1,
        "elements": [
            {
                "name": "Edge1",
                "visible": True,
            }
        ],
        "view": {
            "object_name": "Front",
            "type_id": "TechDraw::DrawViewPart",
        },
    }


def test_native_drawing_ready_page_result_is_minimal() -> None:
    visible = provider._provider_visible_tool_result(
        {
            "_vibecad_native_result": True,
            "ok": True,
            "page": {"object_name": "Page", "state_sha256": "a" * 64},
            "ready": True,
            "issues": [],
            "rendered_item_count": 4,
            "items": [{"object_name": "Front"}],
            "clipping": {"count": 0, "items": [], "truncated": False},
            "outside_drawing_area": {
                "count": 0,
                "items": [],
                "truncated": False,
            },
            "collisions": {"count": 0, "pairs": [], "truncated": False},
            "duplicate_scene_items": {
                "count": 0,
                "object_names": [],
                "truncated": False,
            },
            "references": {"count": 0, "items": [], "truncated": False},
            "duplicate_dimensions": {
                "count": 0,
                "groups": [],
                "truncated": False,
            },
            "template_fields": {"count": 20, "empty_count": 4},
            "update_status": {"current": True, "state_messages": ["Up-to-date"]},
        },
        tool_name="drawing.page_readiness",
    )

    assert visible == {
        "ok": True,
        "page": {"object_name": "Page"},
        "ready": True,
        "issues": [],
    }


def test_native_drawing_page_readiness_preserves_failures() -> None:
    failure = {
        "_vibecad_native_result": True,
        "ok": False,
        "error": "The document changed outside this Native turn.",
        "error_code": "NATIVE_REVISION_CONFLICT",
        "current_revision": 65,
        "repair": {"next_turn_required": True},
    }

    visible = provider._provider_visible_tool_result(
        failure,
        tool_name="drawing.page_readiness",
    )

    assert visible == {
        "ok": False,
        "error": "The document changed outside this Native turn.",
        "error_code": "NATIVE_REVISION_CONFLICT",
        "current_revision": 65,
        "repair": {"next_turn_required": True},
    }


def test_native_drawing_page_readiness_compacts_collision_graph() -> None:
    items = [
        {
            "object_name": name,
            "type_id": type_id,
            "parent_object_name": parent,
            "bounds_mm": bounds,
            "within_page": True,
            "within_drawing_area": True,
        }
        for name, type_id, parent, bounds in (
            (
                "Dimension",
                "TechDraw::DrawViewDimension",
                "Front",
                {"min_x_mm": 20.0, "min_y_mm": 40.0, "max_x_mm": 80.0, "max_y_mm": 55.0},
            ),
            (
                "Dimension001",
                "TechDraw::DrawViewDimension",
                "Front",
                {"min_x_mm": 50.0, "min_y_mm": 45.0, "max_x_mm": 100.0, "max_y_mm": 60.0},
            ),
            (
                "Top",
                "TechDraw::DrawProjGroupItem",
                "ProjectionGroup",
                {"min_x_mm": 40.0, "min_y_mm": 50.0, "max_x_mm": 120.0, "max_y_mm": 90.0},
            ),
        )
    ]
    for index, x_mm in enumerate((30.0, 60.0)):
        items[index].update(
            {
                "label_bounds_mm": {
                    "min_x_mm": x_mm - 4.0,
                    "min_y_mm": 52.0,
                    "max_x_mm": x_mm + 4.0,
                    "max_y_mm": 58.0,
                },
                "label_position_in_view_mm": {"x_mm": x_mm - 40.0, "y_mm": 5.0},
                "view_origin_on_page_mm": {"x_mm": 40.0, "y_mm": 50.0},
                "label_position_on_page_mm": {"x_mm": x_mm, "y_mm": 55.0},
            }
        )
    pairs = [
        {
            "first_object_name": first,
            "second_object_name": second,
            "first_type_id": "unused",
            "second_type_id": "unused",
            "overlap_bounds_mm": {
                "min_x_mm": 50.0,
                "min_y_mm": 50.0,
                "max_x_mm": 60.0,
                "max_y_mm": 55.0,
            },
        }
        for first, second in (
            ("Dimension", "Dimension001"),
            ("Dimension", "Top"),
            ("Dimension001", "Top"),
        )
    ]
    visible = provider._provider_visible_tool_result(
        {
            "_vibecad_native_result": True,
            "ok": True,
            "page": {"object_name": "Page", "state_sha256": "a" * 64},
            "page_bounds_mm": {
                "min_x_mm": 0.0,
                "min_y_mm": 0.0,
                "max_x_mm": 297.0,
                "max_y_mm": 210.0,
            },
            "drawing_bounds_mm": {
                "min_x_mm": 20.0,
                "min_y_mm": 10.0,
                "max_x_mm": 287.0,
                "max_y_mm": 200.0,
            },
            "ready": False,
            "issues": ["item_collisions"],
            "rendered_item_count": 3,
            "items": items,
            "offset": 0,
            "next_offset": None,
            "clipping": {"count": 0, "items": [], "truncated": False},
            "outside_drawing_area": {
                "count": 0,
                "items": [],
                "truncated": False,
            },
            "collisions": {"count": 3, "pairs": pairs, "truncated": False},
            "duplicate_scene_items": {
                "count": 0,
                "object_names": [],
                "truncated": False,
            },
            "references": {"count": 0, "items": [], "truncated": False},
            "duplicate_dimensions": {
                "count": 0,
                "groups": [],
                "truncated": False,
            },
            "template_fields": {"count": 20, "empty_count": 4},
            "update_status": {"current": True, "state_messages": ["Up-to-date"]},
        },
        tool_name="drawing.page_readiness",
    )

    assert visible == {
        "ok": True,
        "page": {"object_name": "Page"},
        "ready": False,
        "issues": ["item_collisions"],
        "drawing_bounds_mm": {
            "min_x_mm": 20.0,
            "min_y_mm": 10.0,
            "max_x_mm": 287.0,
            "max_y_mm": 200.0,
        },
        "rendered_item_count": 3,
        "collisions": {
            "count": 3,
            "objects": [
                {
                    "object_name": "Dimension",
                    "label_position_on_page_mm": items[0][
                        "label_position_on_page_mm"
                    ],
                    "collides_with": ["Dimension001", "Top"],
                },
                {
                    "object_name": "Dimension001",
                    "label_position_on_page_mm": items[1][
                        "label_position_on_page_mm"
                    ],
                    "collides_with": ["Dimension", "Top"],
                },
                {
                    "object_name": "Top",
                    "bounds_mm": items[2]["bounds_mm"],
                    "placement_target": {"object_name": "ProjectionGroup"},
                    "collides_with": ["Dimension", "Dimension001"],
                },
            ],
            "truncated": False,
        },
    }

    assert len(json.dumps(visible, separators=(",", ":"))) < 1300


def test_native_noop_result_hides_host_bookkeeping_without_a_receipt() -> None:
    visible = provider._provider_visible_tool_result(
        {
            "_vibecad_native_result": True,
            "ok": True,
            "changed": False,
            "operation": "restore_home_pos",
            "robot": {
                "document_uid": "document-uid",
                "object_name": "Robot",
                "type_id": "Robot::RobotObject",
            },
            "robot_state_sha256": "a" * 64,
            "setup_state_sha256": "b" * 64,
            "axes_degrees": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
        }
    )

    assert visible == {
        "ok": True,
        "changed": False,
        "operation": "restore_home_pos",
        "robot": {
            "object_name": "Robot",
            "type_id": "Robot::RobotObject",
        },
        "axes_degrees": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0],
    }


def test_source_write_result_is_compact_readable_and_actionable() -> None:
    visible = provider._provider_visible_tool_result(
        {
            "ok": True,
            "source_id": "a" * 32,
            "program_id": "a" * 32,
            "program": "Design/partdesign/Motor Mount",
            "working_revision": "b" * 64,
            "live_outputs": {
                "Mount": {
                    "label": "Motor Mount",
                    "object_name": "VibePartdesign_Mount",
                    "type_id": "PartDesign::Body",
                    "facts": {
                        "shape_type": "Solid",
                        "solid_count": 1,
                        "face_count": 10,
                        "edge_count": 24,
                        "volume_mm3": 1234.5,
                        "face_details": [{"index": index} for index in range(100)],
                        "edge_details": [{"index": index} for index in range(200)],
                    },
                }
            },
            "outputs": [{"name": "Mount", "duplicate": True}],
            "model_state": {"status": "accepted", "accepted_is_current": True},
            "_vibecad_source_lifecycle_result": True,
        }
    )

    assert visible == {
        "ok": True,
        "program": "Design/partdesign/Motor Mount",
        "revision": "b" * 64,
        "outputs": [
                {
                    "name": "Mount",
                    "label": "Motor Mount",
                    "geometry": {
                    "shape_type": "Solid",
                    "solid_count": 1,
                    "face_count": 10,
                    "edge_count": 24,
                    "volume_mm3": 1234.5,
                },
            }
        ],
        "state": {"status": "accepted", "accepted_is_current": True},
    }


def test_background_source_result_is_compacted_once_with_collision_signal() -> None:
    collision = {
        "status": "complete",
        "analysis_complete": True,
        "collision_free": False,
        "evaluated_frame_count": 61,
        "colliding_frame_count": 8,
        "colliding_pair_count": 2,
        "first_collision": {
            "first_component": "Base",
            "second_component": "Arm",
            "frame_index": 1,
            "time_s": 0.0,
        },
        "pairs": [
            {
                "first_component": "Base",
                "second_component": "Arm",
                "intervals": [{"frame": index, "payload": "x" * 1000}],
            }
            for index in range(100)
        ],
        "warning_count": 0,
        "warnings": [],
    }
    visible = provider._provider_visible_tool_result(
        {
            "ok": True,
            "operation": {
                "operation_id": "operation-1",
                "status": "succeeded",
            },
            "operation_succeeded": True,
            "result": {
                "ok": True,
                "program": "Robot/assembly/Arm",
                "working_revision": "c" * 64,
                "live_outputs": {
                    "MotionDemo": {
                        "object_name": "VibeAssembly_MotionDemo",
                        "output_type": "simulation",
                        "assembly_data": {"collision_summary": collision},
                    }
                },
                "outputs": [
                    {
                        "name": "MotionDemo",
                        "assembly_data": {"collision_summary": collision},
                    }
                ],
                "_vibecad_source_lifecycle_result": True,
            },
        }
    )

    assert visible["operation"] == {
        "operation_id": "operation-1",
        "status": "succeeded",
    }
    assert visible["operation_succeeded"] is True
    terminal = visible["result"]
    assert terminal["program"] == "Robot/assembly/Arm"
    assert terminal["revision"] == "c" * 64
    assert len(terminal["outputs"]) == 1
    assert terminal["outputs"][0]["collision_summary"] == {
        "status": "complete",
        "analysis_complete": True,
        "collision_free": False,
        "evaluated_frame_count": 61,
        "colliding_frame_count": 8,
        "colliding_pair_count": 2,
        "first_collision": collision["first_collision"],
        "warning_count": 0,
    }
    assert "pairs" not in json.dumps(terminal)
    assert "payload" not in json.dumps(terminal)


def test_partdesign_vibescript_guidance_defaults_to_native_editable_history() -> None:
    partdesign = provider._vibescript_authoring_instruction(_vibescript_mode_context())
    assert "Source defines editable native history" in partdesign
    assert "use sketch plus a feature for other planar profiles" in partdesign
    assert "direct 3D topology only for nonplanar or standalone geometry" in partdesign

    assembly = provider._vibescript_authoring_instruction(
        _vibescript_mode_context("AssemblyWorkbench", "assembly")
    )
    assert "must be an api.sketch" not in assembly


class _ProviderContextService:
    def __init__(
        self,
        workbench: str,
        base_context: dict[str, object],
        *,
        engine: str = "vibescript",
    ) -> None:
        self.workbench = workbench
        self.base_context = base_context
        self.engine = engine

    def provider_context_summary(self) -> dict[str, object]:
        return dict(self.base_context)

    def provider_turn_document_summary(self) -> dict[str, object]:
        return dict(self.base_context.get("document") or {})

    def provider_turn_selection_summary(self) -> dict[str, object]:
        return dict(self.base_context.get("selection") or {})

    def view_screenshot_summary(self) -> dict[str, object]:
        return dict(self.base_context.get("view_screenshot") or {})

    def provider_reference_image_attachments(self) -> dict[str, object]:
        return dict(self.base_context.get("reference_images") or {})

    def active_workbench_name(self) -> str:
        return self.workbench

    def modeling_engine(self) -> str:
        return self.engine

    def _active_document(self):
        return None

    def provider_debug_config(self) -> dict[str, object]:
        return {"enabled": False}

    def provider_name(self) -> str:
        return "openai"

    def intent_memory_snapshot(self) -> dict[str, object]:
        return {"enabled": False}


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


def test_vibescript_model_context_includes_only_the_editable_source_index(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        session,
        "provider_engine_from_service",
        lambda service: service.modeling_engine(),
    )
    schemas = [
        _context_schema("vibescript.read_source"),
        _context_schema("vibescript.read_api"),
        _context_schema("vibescript.create_program"),
    ]
    monkeypatch.setattr(
        session,
        "provider_tool_schemas",
        lambda _service, _wb, **_kwargs: schemas,
    )
    service = _ProviderContextService(
        "PartWorkbench",
        {"cad_state": {}},
    )
    editable_sources = {
        "schema": "vibecad-editable-sources-v1",
        "domain": "part",
        "sources": [
            {
                "source_id": "a" * 32,
                "program": "Design/part/Body Source",
                "status": "accepted",
                "current_revision": "b" * 64,
                "affected_outputs": [],
            }
        ],
    }
    monkeypatch.setattr(
        session.vibescript_domains,
        "capture_editable_sources_snapshot",
        lambda _service, domain: {
            "_vibecad_deferred_vibescript_program_index": True,
            "domain": domain,
        },
    )
    monkeypatch.setattr(
        session.vibescript_domains,
        "complete_editable_sources_snapshot",
        lambda snapshot: {
            **editable_sources,
            "domain": snapshot["domain"],
        },
    )

    context = session._context_for_provider(service)

    assert context["editable_sources"] == editable_sources
    assert "vibescript_domain" not in context
    assert "partdesign" not in context
    visible = provider._model_visible_context(context)
    assert visible["editable_sources"] == {
        "schema": "vibecad-editable-sources-v1",
        "domain": "part",
        "sources": [
            {
                "program": "Design/part/Body Source",
                "status": "accepted",
            }
        ],
    }
    assert "source_id" not in json.dumps(visible)
    assert "vibescript_domain" not in visible


def test_native_context_replaces_legacy_document_and_selection_summaries(
    monkeypatch,
) -> None:
    from VibeCADModelingSurface import ModelingSurface

    schema = _context_schema("state.read")
    resolution = ModelingSurface(
        workbench="PartDesignWorkbench",
        engine="native",
        domain="model",
        surface_id="vibecad/surface/native/model/7/abc",
        core_tool_names=(),
        cad_tool_names=("state.read",),
        available=True,
        unavailable_reason="",
    )
    native_surface = SimpleNamespace(schemas=(schema,))
    monkeypatch.setitem(
        sys.modules,
        "VibeCADNativeProviderContext",
        SimpleNamespace(
            resolve_production_native_surface=lambda: (object(), native_surface),
            schemas_for_native_provider_surface=lambda *_args, **_kwargs: [schema],
            native_active_state=lambda service: service.native_active_snapshot(),
            provider_authorized_native_surface=lambda surface, _state, **_kwargs: surface,
            provider_visible_native_state=lambda state: state,
        ),
    )
    monkeypatch.setattr(
        session,
        "modeling_surface_from_native_provider",
        lambda _workbench, _surface: resolution,
    )
    service = _ProviderContextService(
        "PartDesignWorkbench",
        {
            "document": {"legacy": "must not leak"},
            "selection": {"legacy": "must not leak"},
        },
        engine="native",
    )
    service.native_active_snapshot = lambda: {
        "surface_id": "model",
        "document": {"document_uid": "document-a", "document_name": "Part"},
        "structural_revision": 9,
        "domain": {"kind": "model", "counts": {"bodies": 1}},
        "working_set": [],
    }

    context = session._context_for_provider(service)
    visible = provider._model_visible_context(context)

    assert "document" not in context
    assert "selection" not in context
    assert context["native_state"]["structural_revision"] == 9
    assert visible == {
        "work": "modeling",
        "state": {"domain": {"kind": "model", "counts": {"bodies": 1}}},
        "document": {"name": "Part"},
    }
    assert "native_state" not in visible
    assert "document-a" not in json.dumps(visible)
    assert session._provider_state_payload(context) == visible
    assert "must not leak" not in json.dumps(context)
    assert provider._provider_state_after_tool(context) == {}


def test_editable_source_manifests_complete_after_document_thread_capture(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        session,
        "provider_engine_from_service",
        lambda service: service.modeling_engine(),
    )
    schemas = [
        _context_schema("vibescript.read_source"),
        _context_schema("vibescript.read_api"),
        _context_schema("vibescript.create_program"),
    ]
    monkeypatch.setattr(
        session,
        "provider_tool_schemas",
        lambda _service, _wb, **_kwargs: schemas,
    )
    service = _ProviderContextService("PartWorkbench", {})
    state = {"on_document_thread": False}

    def capture(_service, domain):
        assert state["on_document_thread"] is True
        return {
            "_vibecad_deferred_vibescript_program_index": True,
            "domain": domain,
        }

    def complete(snapshot):
        assert state["on_document_thread"] is False
        return {
            "schema": "vibecad-editable-sources-v1",
            "domain": snapshot["domain"],
            "source_count": 0,
            "sources": [],
        }

    def dispatch(operation):
        state["on_document_thread"] = True
        try:
            return operation()
        finally:
            state["on_document_thread"] = False

    monkeypatch.setattr(
        session.vibescript_domains,
        "capture_editable_sources_snapshot",
        capture,
    )
    monkeypatch.setattr(
        session.vibescript_domains,
        "complete_editable_sources_snapshot",
        complete,
    )

    context = session._build_context_for_provider(
        service,
        None,
        dispatch,
    )

    assert context["editable_sources"]["domain"] == "part"
    assert context["editable_sources"]["source_count"] == 0


def test_assembly_turn_injects_copy_ready_available_components(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        session,
        "provider_engine_from_service",
        lambda service: service.modeling_engine(),
    )
    import VibeCADComponentCatalog as component_catalog

    schemas = [
        _context_schema("vibescript.read_source"),
        _context_schema("vibescript.create_part"),
        _context_schema("vibescript.create_assembly"),
        _context_schema("component_catalog.search"),
    ]
    monkeypatch.setattr(
        session,
        "provider_tool_schemas",
        lambda _service, _wb, **_kwargs: schemas,
    )
    monkeypatch.setattr(
        session.vibescript_domains,
        "capture_editable_sources_snapshot",
        lambda _service, domain: {
            "_vibecad_deferred_vibescript_program_index": True,
            "domain": domain,
        },
    )
    monkeypatch.setattr(
        session.vibescript_domains,
        "complete_editable_sources_snapshot",
        lambda snapshot: {
            "schema": "vibecad-editable-sources-v1",
            "domain": snapshot["domain"],
            "sources": [],
        },
    )
    reference = {"document_uid": "assembly-uid", "object_name": "Bracket"}
    monkeypatch.setattr(
        component_catalog,
        "capture_component_catalog",
        lambda _service: {
            "owner_document_uid": "assembly-uid",
            "project_directory": "",
            "owner_file": "",
            "open_document_files": [],
            "open_candidates": [
                {
                    "document_label": "Parts",
                    "object_name": "Bracket",
                    "label": "Motor Bracket",
                    "type_id": "PartDesign::Body",
                    "source": "open_document",
                    "live_validated": True,
                    "portable": True,
                    "reference": reference,
                }
            ],
        },
    )
    service = _ProviderContextService("AssemblyWorkbench", {})

    context = session._context_for_provider(service)
    visible = provider._model_visible_context(context)

    assert visible["available_components"]["component_count"] == 1
    assert visible["available_components"]["components"][0]["reference"] == reference
    assert context["_vibecad_component_catalog"]["schema"] == (
        "vibecad-component-catalog-snapshot-v1"
    )


def test_vibescript_context_is_absent_when_the_workbench_has_no_surface(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        session,
        "provider_engine_from_service",
        lambda service: service.modeling_engine(),
    )
    monkeypatch.setattr(
        session,
        "provider_tool_schemas",
        lambda _service, _wb, **_kwargs: [_context_schema("core.set_view")],
    )
    service = _ProviderContextService(
        "TestWorkbench",
        {"cad_state": {}, "draft": {"objects": []}},
    )

    context = session._context_for_provider(service)

    assert "vibescript" not in context


def test_partdesign_does_not_inject_a_model_manifest_at_turn_start(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        session,
        "provider_engine_from_service",
        lambda service: service.modeling_engine(),
    )
    models = [{"model_id": "b" * 32, "name": "Rotor"}]
    monkeypatch.setattr(
        session,
        "provider_tool_schemas",
        lambda _service, _wb, **_kwargs: [
                _context_schema("vibescript.read_source"),
                _context_schema("vibescript.create_part"),
        ],
    )
    service = _ProviderContextService(
        "PartDesignWorkbench",
        {"cad_state": {}, "partdesign": {"models": models}},
    )

    context = session._context_for_provider(service)

    assert "partdesign" not in context
    assert "vibescript" not in context
    assert context["editable_sources"]["domain"] == "partdesign"
