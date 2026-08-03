# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression coverage for provider subprocess lifecycle races."""

from __future__ import annotations

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
    anthropic_module = SimpleNamespace(
        Anthropic=lambda **_kwargs: SimpleNamespace(
            messages=SimpleNamespace(stream=lambda **_kwargs: _EmptyAnthropicStream())
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


def test_instructions_include_vibescript_guidance_only_in_vibescript_mode() -> None:
    context = _vibescript_mode_context()
    guidance = provider._vibescript_authoring_instruction(context)
    instructions = provider._provider_instructions(context)
    assert instructions.startswith(provider.VIBECAD_SYSTEM_INSTRUCTIONS)
    assert "Default to catalog fasteners." in provider.VIBECAD_SYSTEM_INSTRUCTIONS
    assert (
        "A correction changes only the named geometry"
        in provider.VIBECAD_SYSTEM_INSTRUCTIONS
    )
    assert "assembly retention" in provider.VIBECAD_SYSTEM_INSTRUCTIONS
    assert guidance
    assert guidance in instructions
    assert "api.extrude" in guidance
    assert "cross-section stays constant" in guidance
    assert "api.loft only when" in guidance
    assert "cross-section genuinely changes" in guidance

    assembly_guidance = provider._vibescript_authoring_instruction(
        _vibescript_mode_context("AssemblyWorkbench", "assembly")
    )
    assert "cross-section stays constant" not in assembly_guidance
    assert "cross-section genuinely changes" not in assembly_guidance

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
    assert all(block["cache_control"] == {"type": "ephemeral"} for block in blocks)

    other_blocks = provider._anthropic_system_blocks(
        {"provider_tool_schemas": [{"name": "core.set_view"}]}
    )
    assert [block["text"] for block in other_blocks] == [
        provider.VIBECAD_SYSTEM_INSTRUCTIONS
    ]


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
    assert "validated inputs" in text
    assert "vibescript.read_source" in text
    assert "vibescript.read_api" in text
    assert "vibescript.read_geometry" in text
    assert "vibescript.read_placement" in text
    assert "complete updated source" in text


def test_vibescript_guidance_keeps_lifecycle_rules_concise_across_domains() -> None:
    partdesign = provider._vibescript_authoring_instruction(_vibescript_mode_context())
    assembly = provider._vibescript_authoring_instruction(
        _vibescript_mode_context("AssemblyWorkbench", "assembly")
    )
    for instruction in (partdesign, assembly):
        assert "vibescript.read_source" in instruction
        assert "vibescript.read_api" in instruction
        assert "vibescript.read_geometry" in instruction
        assert "vibescript.read_placement" in instruction
        assert "vibescript.edit_source" in instruction
        assert "set_inputs" in instruction
        assert "reconfigure_program" in instruction
        assert "one editable part or program" in instruction
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
    assert "vibecad_result_boundary" not in visible
    assert "_vibecad_complete_source_result" not in visible


def test_partdesign_vibescript_guidance_defaults_to_native_editable_history() -> None:
    partdesign = provider._vibescript_authoring_instruction(_vibescript_mode_context())
    assert "editable native Body history" in partdesign
    assert "api.sketch for planar feature profiles" in partdesign
    assert "line_3d, arc_3d, wire" in partdesign
    assert "nonplanar, imported, repair, or standalone geometry" in partdesign

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
    assert visible["editable_sources"] == editable_sources
    assert "vibescript_domain" not in visible


def test_editable_source_manifests_complete_after_document_thread_capture(
    monkeypatch,
) -> None:
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
        "build",
        dispatch,
    )

    assert context["editable_sources"]["domain"] == "part"
    assert context["editable_sources"]["source_count"] == 0


def test_assembly_turn_injects_copy_ready_available_components(
    monkeypatch,
) -> None:
    import VibeCADComponentCatalog as component_catalog

    schemas = [
        _context_schema("vibescript.read_source"),
        _context_schema("vibescript.create_program"),
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
    reference = {"document_uid": "part-uid", "object_name": "Bracket"}
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
    models = [{"model_id": "b" * 32, "name": "Rotor"}]
    monkeypatch.setattr(
        session,
        "provider_tool_schemas",
        lambda _service, _wb, **_kwargs: [
            _context_schema("vibescript.read_source"),
            _context_schema("vibescript.create_program"),
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
