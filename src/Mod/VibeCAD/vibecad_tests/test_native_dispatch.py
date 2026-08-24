# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

import pytest

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
    NativeProviderSurface,
    provider_visible_native_schema,
)
from VibeCADNativeDispatch import NativeTurnDispatcher
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeSurface import NativeSurfaceSnapshot
from VibeCADNativeTargets import NativeTargetError
from VibeCADNativeTurn import NativeTurnSnapshot


class _Document:
    Uid = "document-a"


def _parameters() -> dict:
    return {
        "type": "object",
        "properties": {
            "value": {"type": "integer", "minimum": 0, "maximum": 100}
        },
        "required": ["value"],
        "additionalProperties": False,
    }


def _dispatcher(handler, **overrides):
    definition = overrides.get("definition")
    if definition is None:
        definition = NativeCapabilityDefinition(
            name="test.execute",
            description="Execute one exact bounded test operation.",
            primary_classification="read",
            variants=(
                NativeCapabilityVariant(
                    operation="read",
                    description="Read one bounded test value.",
                    action_ids=frozenset({"VibeCAD_Test"}),
                    surface_ids=frozenset({"model"}),
                    exact_target_type=None,
                    transaction_behavior="none",
                    background_required=False,
                    parameters=_parameters(),
                ),
            ),
        )
    name = definition.name
    registry = NativeCapabilityRegistry()
    registry.register_definition(definition)
    registry.register_implementation(NativeCapabilityImplementation(name, handler))
    operations = overrides.get(
        "operations",
        tuple(variant.operation for variant in definition.variants),
    )
    schema = definition.provider_schema(operations)
    if overrides.get("provider_visible", False):
        schema = provider_visible_native_schema(schema)
    schemas = (schema,)
    provider_surface = NativeProviderSurface(
        snapshot=NativeSurfaceSnapshot(
            surface_id="model",
            revision=7,
            manifest_sha256="a" * 64,
            command_ids=("VibeCAD_Test",),
            available_command_ids=("VibeCAD_Test",),
            unavailable_command_ids=(),
        ),
        available=True,
        unavailable_reason="",
        tool_names=(name,),
        schemas=schemas,
        human_only_action_ids=(),
        missing_definition_names=(),
        missing_implementation_names=(),
        incomplete_definition_names=(),
    )
    turn = NativeTurnSnapshot.from_provider_surface(provider_surface)
    document = overrides.get("document", _Document())
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    debug = overrides.get("debug", [])
    dispatcher = NativeTurnDispatcher(
        document=document,
        state=state,
        registry=registry,
        turn=turn,
        runtimes=overrides.get(
            "runtimes",
            {name: overrides.get("runtime", object())},
        ),
        reauthorize_turn=overrides.get("reauthorize_turn", lambda: None),
        active_document=overrides.get("active_document", lambda: document),
        debug_sink=debug.append,
    )
    return dispatcher, state, debug


def _arguments(value: int) -> str:
    return json.dumps({"operation": "read", "value": value})


def _mutation_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="test.execute",
        description="Execute one exact bounded test mutation.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="read",
                description="Execute one exact bounded test mutation.",
                action_ids=frozenset({"VibeCAD_Test"}),
                surface_ids=frozenset({"model"}),
                exact_target_type=None,
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(),
            ),
        ),
    )


def test_dispatch_resolves_one_hidden_frozen_operation() -> None:
    observed = []
    dispatcher, _state, _debug = _dispatcher(
        lambda call: observed.append(dict(call.arguments)) or {"value": 4},
        provider_visible=True,
    )

    response = dispatcher.call(
        "test.execute",
        json.dumps({"value": 4}),
        "hidden-operation-call",
    )

    assert response["ok"] is True
    assert observed == [{"operation": "read", "value": 4}]


def test_dispatch_injects_one_host_ticket_and_returns_concise_success() -> None:
    calls = []

    def handler(call):
        calls.append(call)
        return {
            "value": call.arguments["value"],
            "revision": call.ticket.expected_revision,
        }

    dispatcher, _state, _debug = _dispatcher(handler)

    result = dispatcher.call("test.execute", _arguments(12), "provider-call-1")

    assert result == {"ok": True, "revision": 0, "value": 12}
    assert len(calls) == 1
    assert calls[0].ticket.capability_name == "test.execute"
    assert len(calls[0].ticket.idempotency_token) == 32


def test_dispatch_refuses_a_document_change_outside_the_frozen_turn() -> None:
    calls = []
    dispatcher, state, _debug = _dispatcher(
        lambda call: calls.append(call) or {"value": call.arguments["value"]}
    )
    state.note_structural_change(_Document.Uid)

    result = dispatcher.call(
        "test.execute", _arguments(12), "provider-call-1"
    )

    assert result == {
        "ok": False,
        "error_code": "NATIVE_REVISION_CONFLICT",
        "error": (
            "The document changed outside this Native turn. Start a new turn "
            "from its current state."
        ),
        "current_revision": 1,
        "repair": {"next_turn_required": True},
    }
    assert calls == []


def test_background_job_status_survives_its_owned_document_commit() -> None:
    definition = NativeCapabilityDefinition(
        name="native.job",
        description="Read one exact background job.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="status",
                description="Read one exact background job.",
                action_ids=frozenset({"VibeCAD_NativeBackgroundJob"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="NativeBackgroundJobId",
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "job_id": {
                            "type": "string",
                            "minLength": 32,
                            "maxLength": 32,
                        },
                    },
                    "required": ["job_id"],
                    "additionalProperties": False,
                },
            ),
        ),
    )
    calls = []
    dispatcher, state, _debug = _dispatcher(
        lambda call: calls.append(call) or {"phase": "completed"},
        definition=definition,
    )
    state.note_structural_change(_Document.Uid)

    result = dispatcher.call(
        "native.job",
        json.dumps({"operation": "status", "job_id": "a" * 32}),
        "background-status-call",
    )

    assert result == {"ok": True, "phase": "completed"}
    assert len(calls) == 1


def test_dispatch_advances_with_its_own_successful_mutations() -> None:
    holder = {}
    tickets = []

    def handler(call):
        tickets.append(call.ticket.expected_revision)
        holder["state"].note_structural_change(call.ticket.document_uid)
        return {"value": call.arguments["value"]}

    dispatcher, state, _debug = _dispatcher(
        handler,
        definition=_mutation_definition(),
    )
    holder["state"] = state

    first = dispatcher.call(
        "test.execute", _arguments(1), "provider-call-1"
    )
    second = dispatcher.call(
        "test.execute", _arguments(2), "provider-call-2"
    )

    assert first == {"ok": True, "value": 1}
    assert second == {"ok": True, "value": 2}
    assert tickets == [0, 1]


def test_dispatch_adopts_its_own_completed_background_mutation() -> None:
    calls = []

    def handler(call):
        calls.append(call)
        return {"value": call.arguments["value"]}

    dispatcher, state, _debug = _dispatcher(
        handler,
        definition=_mutation_definition(),
    )
    first = dispatcher.call("test.execute", _arguments(1), "provider-call-1")
    ticket = calls[0].ticket
    state.authorize_mutation(ticket)
    state.begin_mutation_observation(ticket)
    state.note_structural_change(ticket.document_uid)
    prepared = state.prepare_mutation_completion(ticket, {"value": 1})
    state.commit_mutation_observation(ticket)
    state.complete_prepared_mutation(prepared)

    second = dispatcher.call("test.execute", _arguments(2), "provider-call-2")

    assert first == {"ok": True, "value": 1}
    assert second == {"ok": True, "value": 2}
    assert calls[1].ticket.expected_revision == 1


def test_dispatch_still_refuses_change_after_its_background_receipt() -> None:
    calls = []

    def handler(call):
        calls.append(call)
        return {"value": call.arguments["value"]}

    dispatcher, state, _debug = _dispatcher(
        handler,
        definition=_mutation_definition(),
    )
    dispatcher.call("test.execute", _arguments(1), "provider-call-1")
    ticket = calls[0].ticket
    state.authorize_mutation(ticket)
    state.begin_mutation_observation(ticket)
    state.note_structural_change(ticket.document_uid)
    prepared = state.prepare_mutation_completion(ticket, {"value": 1})
    state.commit_mutation_observation(ticket)
    state.complete_prepared_mutation(prepared)
    state.note_structural_change(ticket.document_uid)

    result = dispatcher.call("test.execute", _arguments(2), "provider-call-2")

    assert result["error_code"] == "NATIVE_REVISION_CONFLICT"
    assert result["current_revision"] == 2
    assert len(calls) == 1


def test_single_purpose_tool_infers_its_frozen_operation() -> None:
    calls = []
    dispatcher, _state, _debug = _dispatcher(
        lambda call: calls.append(call) or {"value": call.arguments["value"]}
    )

    result = dispatcher.call(
        "test.execute",
        json.dumps({"value": 12}),
        "provider-call-1",
    )

    assert result == {"ok": True, "value": 12}
    assert calls[0].arguments == {"operation": "read", "value": 12}


def test_multi_purpose_tool_still_requires_a_frozen_operation() -> None:
    definition = NativeCapabilityDefinition(
        name="test.execute",
        description="Execute one exact test operation.",
        primary_classification="read",
        variants=tuple(
            NativeCapabilityVariant(
                operation=operation,
                description=f"Execute {operation}.",
                action_ids=frozenset({f"VibeCAD_Test_{operation}"}),
                surface_ids=frozenset({"model"}),
                exact_target_type=None,
                transaction_behavior="none",
                background_required=False,
                parameters=_parameters(),
            )
            for operation in ("read", "measure")
        ),
    )
    dispatcher, _state, _debug = _dispatcher(
        lambda _call: pytest.fail("handler must not execute"),
        definition=definition,
    )

    result = dispatcher.call(
        "test.execute",
        json.dumps({"value": 12}),
        "provider-call-1",
    )

    assert result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert result["argument_error"]["expected"] == ["read", "measure"]


def test_read_capability_cannot_report_success_after_a_structural_change() -> None:
    state_holder = {}

    def handler(call):
        state_holder["state"].note_structural_change(call.ticket.document_uid)
        return {"value": call.arguments["value"]}

    dispatcher, state, _debug = _dispatcher(handler)
    state_holder["state"] = state

    result = dispatcher.call("test.execute", _arguments(12), "provider-call-1")

    assert result == {
        "ok": False,
        "error_code": "NATIVE_READ_SIDE_EFFECT",
        "error": (
            "A read-only Native capability changed the document; its result was "
            "rejected."
        ),
        "current_revision": 1,
        "repair": {
            "operation": "read",
            "revision_before": 0,
            "revision_after": 1,
        },
    }


def test_target_type_diagnostic_reaches_the_provider_as_structured_data() -> None:
    def handler(_call):
        raise NativeTargetError(
            "The exact target has an unsupported type.",
            exact_target={"document_uid": "document-a", "object_name": "Sketch"},
            actual_type="Sketcher::SketchObject",
            accepted_types=("PartDesign::Feature", "Part::Feature"),
        )

    dispatcher, _state, _debug = _dispatcher(handler)

    result = dispatcher.call("test.execute", _arguments(12), "provider-call-1")

    assert result["error_code"] == "NATIVE_TARGET_INVALID"
    assert result["actual_type"] == "Sketcher::SketchObject"
    assert result["accepted_types"] == ["PartDesign::Feature", "Part::Feature"]
    assert result["exact_target"]["object_name"] == "Sketch"


def test_duplicate_provider_call_returns_exact_prior_result_without_execution() -> None:
    execution_count = 0

    def handler(call):
        nonlocal execution_count
        execution_count += 1
        return {"value": call.arguments["value"], "execution": execution_count}

    dispatcher, _state, _debug = _dispatcher(handler)
    first = dispatcher.call("test.execute", _arguments(4), "provider-call-1")
    repeated = dispatcher.call(
        "test.execute",
        '{"value":4,"operation":"read"}',
        "provider-call-1",
    )

    assert repeated == first == {"ok": True, "value": 4, "execution": 1}
    assert execution_count == 1
    assert dispatcher.call_count == 1


def test_provider_call_id_cannot_be_reused_for_changed_arguments_or_tool() -> None:
    dispatcher, _state, _debug = _dispatcher(lambda call: {"value": call.arguments["value"]})
    assert dispatcher.call("test.execute", _arguments(4), "provider-call-1")["ok"]

    changed = dispatcher.call("test.execute", _arguments(5), "provider-call-1")
    renamed = dispatcher.call("other.execute", _arguments(4), "provider-call-1")

    assert changed["error_code"] == "NATIVE_CALL_ID_REUSED"
    assert renamed["error_code"] == "NATIVE_CALL_ID_REUSED"


def test_schema_and_surface_failures_happen_before_the_handler() -> None:
    calls = []
    dispatcher, _state, _debug = _dispatcher(lambda call: calls.append(call) or {})

    invalid = dispatcher.call(
        "test.execute",
        '{"operation":"read","value":101}',
        "provider-call-1",
    )
    extra = dispatcher.call(
        "test.execute",
        '{"operation":"read","value":3,"command":"VibeCAD_Test"}',
        "provider-call-2",
    )
    unavailable = dispatcher.call("test.other", "{}", "provider-call-3")

    assert invalid["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert extra["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert unavailable["error_code"] == "NATIVE_TOOL_UNAVAILABLE"
    assert calls == []


def test_schema_failure_example_resolves_nested_union_to_one_valid_payload() -> None:
    support = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "base_plane"},
                    "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"]},
                    "offset_mm": {
                        "type": "number",
                        "minimum": -1000,
                        "maximum": 1000,
                    },
                },
                "required": ["kind", "plane", "offset_mm"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "datum_plane"},
                    "target": {
                        "type": "object",
                        "properties": {
                            "object_name": {"type": "string", "maxLength": 128}
                        },
                        "required": ["object_name"],
                        "additionalProperties": False,
                    },
                },
                "required": ["kind", "target"],
                "additionalProperties": False,
            },
        ]
    }
    definition = NativeCapabilityDefinition(
        name="test.execute",
        description="Execute one exact nested-union test operation.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="read",
                description="Read one exact supported object.",
                action_ids=frozenset({"VibeCAD_Test"}),
                surface_ids=frozenset({"model"}),
                exact_target_type=None,
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {"support": support},
                    "required": ["support"],
                    "additionalProperties": False,
                },
            ),
        ),
    )
    dispatcher, _state, _debug = _dispatcher(
        lambda _call: pytest.fail("handler must not execute"),
        definition=definition,
    )

    result = dispatcher.call(
        "test.execute",
        json.dumps({"operation": "read", "support": None}),
        "provider-call-1",
    )

    example = result["argument_error"]["valid_example"]
    assert example == {
        "operation": "read",
        "support": {"kind": "base_plane", "plane": "XY", "offset_mm": 0.0},
    }


def test_schema_failure_reports_selected_nested_union_leaf() -> None:
    support = {
        "oneOf": [
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "base_plane"},
                    "plane": {"type": "string", "enum": ["XY", "XZ", "YZ"]},
                },
                "required": ["kind", "plane"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "kind": {"type": "string", "const": "face"},
                    "object_name": {"type": "string", "maxLength": 128},
                },
                "required": ["kind", "object_name"],
                "additionalProperties": False,
            },
        ]
    }
    definition = NativeCapabilityDefinition(
        name="test.execute",
        description="Read one support.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="read",
                description="Read one support.",
                action_ids=frozenset({"VibeCAD_Test"}),
                surface_ids=frozenset({"model"}),
                exact_target_type=None,
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {"support": support},
                    "required": ["support"],
                    "additionalProperties": False,
                },
            ),
        ),
    )
    dispatcher, _state, _debug = _dispatcher(
        lambda _call: pytest.fail("handler must not execute"),
        definition=definition,
    )

    result = dispatcher.call(
        "test.execute",
        json.dumps(
            {
                "operation": "read",
                "support": {
                    "kind": "base_plane",
                    "plane": "XY",
                    "offset_mm": 2.0,
                },
            }
        ),
        "provider-call-1",
    )

    assert result["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert result["argument_error"]["path"] == ["support"]
    assert result["argument_error"]["rule"] == "additionalProperties"
    assert result["argument_error"]["expected"] is False
    assert len(json.dumps(result["argument_error"])) < 1000


def test_compact_multi_variant_schema_is_revalidated_against_exact_branch() -> None:
    def parameters(unit: str) -> dict:
        return {
            "type": "object",
            "properties": {
                "dimension": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "number"},
                        "unit": {"type": "string", "const": unit},
                    },
                    "required": ["value", "unit"],
                    "additionalProperties": False,
                }
            },
            "required": ["dimension"],
            "additionalProperties": False,
        }

    definition = NativeCapabilityDefinition(
        name="test.execute",
        description="Execute one exact dimensional test operation.",
        primary_classification="read",
        variants=tuple(
            NativeCapabilityVariant(
                operation=operation,
                description=f"Read one {unit} dimensional value.",
                action_ids=frozenset({f"VibeCAD_Test_{operation}"}),
                surface_ids=frozenset({"model"}),
                exact_target_type=None,
                transaction_behavior="none",
                background_required=False,
                parameters=parameters(unit),
            )
            for operation, unit in (("linear", "mm"), ("angular", "deg"))
        ),
    )
    calls = []
    dispatcher, _state, _debug = _dispatcher(
        lambda call: calls.append(call) or {"unit": call.arguments["dimension"]["unit"]},
        definition=definition,
    )

    invalid = dispatcher.call(
        "test.execute",
        json.dumps(
            {
                "operation": "linear",
                "dimension": {"value": 5.0, "unit": "deg"},
            }
        ),
        "provider-call-1",
    )
    valid = dispatcher.call(
        "test.execute",
        json.dumps(
            {
                "operation": "linear",
                "dimension": {"value": 5.0, "unit": "mm"},
            }
        ),
        "provider-call-2",
    )

    assert invalid["error_code"] == "NATIVE_ARGUMENTS_INVALID"
    assert valid == {"ok": True, "unit": "mm"}
    assert len(calls) == 1


def test_missing_or_unbounded_provider_call_id_is_refused() -> None:
    dispatcher, _state, _debug = _dispatcher(lambda _call: {})

    assert dispatcher.call("test.execute", _arguments(1), "")["error_code"] == (
        "NATIVE_CALL_ID_INVALID"
    )
    assert dispatcher.call("test.execute", _arguments(1), "x" * 257)[
        "error_code"
    ] == "NATIVE_CALL_ID_INVALID"
    assert dispatcher.call_count == 0


def test_turn_and_document_are_reauthorized_before_ticket_creation() -> None:
    class _SurfaceChanged(RuntimeError):
        def failure(self):
            return {
                "error_code": "NATIVE_SURFACE_CHANGED",
                "message": "The human changed ribbons.",
                "current_surface": "mesh",
                "private": "not provider-visible",
            }

    dispatcher, state, _debug = _dispatcher(
        lambda _call: pytest.fail("handler must not execute"),
        reauthorize_turn=lambda: (_ for _ in ()).throw(_SurfaceChanged()),
    )
    result = dispatcher.call("test.execute", _arguments(1), "provider-call-1")

    assert result == {
        "ok": False,
        "error_code": "NATIVE_SURFACE_CHANGED",
        "error": "The human changed ribbons.",
        "current_surface": "mesh",
    }
    assert state.snapshot(_Document.Uid)["recent_receipts"] == []

    inactive, _state, _debug = _dispatcher(
        lambda _call: pytest.fail("handler must not execute"),
        active_document=lambda: None,
    )
    assert inactive.call("test.execute", _arguments(1), "provider-call-2")[
        "error_code"
    ] == "NATIVE_DOCUMENT_CHANGED"


def test_surface_change_inside_handler_is_refused_before_success_returns() -> None:
    checks = 0
    calls = []

    class _SurfaceChanged(RuntimeError):
        def failure(self):
            return {
                "error_code": "NATIVE_SURFACE_CHANGED",
                "message": "The human changed ribbons.",
                "current_surface": "mesh",
                "repair": {"resume_next_turn": True},
            }

    def reauthorize():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise _SurfaceChanged()

    dispatcher, _state, _debug = _dispatcher(
        lambda call: calls.append(call) or {"value": call.arguments["value"]},
        reauthorize_turn=reauthorize,
    )

    result = dispatcher.call("test.execute", _arguments(3), "provider-call-1")

    assert result["error_code"] == "NATIVE_SURFACE_CHANGED"
    assert result["current_surface"] == "mesh"
    assert len(calls) == 1
    assert checks == 2


def test_surface_change_between_calls_refuses_the_second_handler() -> None:
    checks = 0
    calls = []

    class _SurfaceChanged(RuntimeError):
        def failure(self):
            return {
                "error_code": "NATIVE_SURFACE_CHANGED",
                "message": "The human changed ribbons.",
                "current_surface": "drawing",
            }

    def reauthorize():
        nonlocal checks
        checks += 1
        if checks >= 3:
            raise _SurfaceChanged()

    dispatcher, _state, _debug = _dispatcher(
        lambda call: calls.append(call.arguments["value"])
        or {"value": call.arguments["value"]},
        reauthorize_turn=reauthorize,
    )

    first = dispatcher.call("test.execute", _arguments(1), "provider-call-1")
    second = dispatcher.call("test.execute", _arguments(2), "provider-call-2")

    assert first == {"ok": True, "value": 1}
    assert second["error_code"] == "NATIVE_SURFACE_CHANGED"
    assert calls == [1]
    assert checks == 3


def test_exact_edit_control_requires_and_accepts_one_turn_invalidating_transition() -> None:
    definition = NativeCapabilityDefinition(
        name="test.edit_control",
        description="Finish one exact contextual edit task.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="leave",
                description="Finish the exact contextual edit task.",
                action_ids=frozenset({"VibeCAD_Test"}),
                surface_ids=frozenset({"model"}),
                exact_target_type="EditTask",
                transaction_behavior="edit_control",
                background_required=False,
                parameters=_parameters(),
            ),
        ),
    )
    checks = 0

    class _SurfaceChanged(RuntimeError):
        def failure(self):
            return {
                "error_code": "NATIVE_SURFACE_CHANGED",
                "message": "The contextual editor closed.",
                "current_surface": "model",
                "repair": {"resume_next_turn": True},
            }

    def reauthorize():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise _SurfaceChanged()

    dispatcher, _state, _debug = _dispatcher(
        lambda _call: {
            "next_surface": "model",
            "next_turn_required": True,
            "closed": True,
        },
        definition=definition,
        reauthorize_turn=reauthorize,
    )

    result = dispatcher.call(
        "test.edit_control",
        json.dumps({"operation": "leave", "value": 1}),
        "provider-call-1",
    )

    assert result == {
        "ok": True,
        "next_surface": "model",
        "next_turn_required": True,
        "closed": True,
    }
    assert checks == 2


def test_surface_control_accepts_one_turn_invalidating_workspace_transition() -> None:
    definition = NativeCapabilityDefinition(
        name="test.surface_control",
        description="Switch to one exact Native workspace surface.",
        primary_classification="view",
        variants=(
            NativeCapabilityVariant(
                operation="switch",
                description="Switch surfaces after the current turn.",
                action_ids=frozenset({"VibeCAD_Test"}),
                surface_ids=frozenset({"model"}),
                exact_target_type=None,
                transaction_behavior="surface_control",
                background_required=False,
                parameters=_parameters(),
            ),
        ),
    )
    checks = 0

    class _SurfaceChanged(RuntimeError):
        def failure(self):
            return {
                "error_code": "NATIVE_SURFACE_CHANGED",
                "message": "The Native workspace changed.",
                "current_surface": "assemble",
            }

    def reauthorize():
        nonlocal checks
        checks += 1
        if checks == 2:
            raise _SurfaceChanged()

    dispatcher, _state, _debug = _dispatcher(
        lambda _call: {
            "workspace": "assembly",
            "next_turn_required": True,
        },
        definition=definition,
        reauthorize_turn=reauthorize,
    )

    result = dispatcher.call(
        "test.surface_control",
        json.dumps({"operation": "switch", "value": 1}),
        "provider-call-1",
    )

    assert result == {
        "ok": True,
        "workspace": "assembly",
        "next_turn_required": True,
    }
    assert checks == 2


def test_edit_control_cannot_claim_success_without_invalidating_the_turn() -> None:
    definition = NativeCapabilityDefinition(
        name="test.edit_control",
        description="Finish one exact contextual edit task.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="leave",
                description="Finish the exact contextual edit task.",
                action_ids=frozenset({"VibeCAD_Test"}),
                surface_ids=frozenset({"model"}),
                exact_target_type="EditTask",
                transaction_behavior="edit_control",
                background_required=False,
                parameters=_parameters(),
            ),
        ),
    )
    dispatcher, _state, _debug = _dispatcher(
        lambda _call: {
            "next_surface": "model",
            "next_turn_required": True,
        },
        definition=definition,
    )

    result = dispatcher.call(
        "test.edit_control",
        json.dumps({"operation": "leave", "value": 1}),
        "provider-call-1",
    )

    assert result["error_code"] == "NATIVE_EDIT_CONTROL_FAILED"


def test_full_handler_diagnostic_goes_only_to_debug_sink() -> None:
    secret_detail = "internal topology traceback detail"

    def handler(_call):
        raise RuntimeError(secret_detail)

    dispatcher, _state, debug = _dispatcher(handler)
    result = dispatcher.call("test.execute", _arguments(1), "provider-call-1")

    assert result["ok"] is False
    assert result["error_code"] == "NATIVE_CALL_FAILED"
    assert secret_detail not in result["error"]
    assert set(result) == {"ok", "error_code", "error"}
    assert debug[0]["diagnostic"] == secret_detail
    assert debug[0]["exception_type"] == "RuntimeError"


def test_debug_sink_preserves_bounded_explicit_exception_causes() -> None:
    def handler(_call):
        try:
            raise ValueError("kernel rejected the selected face")
        except ValueError as cause:
            raise RuntimeError("profile operation failed") from cause

    dispatcher, _state, debug = _dispatcher(handler)
    result = dispatcher.call("test.execute", _arguments(1), "provider-call-1")

    assert result == {
        "ok": False,
        "error_code": "NATIVE_CALL_FAILED",
        "error": "Native capability execution failed.",
    }
    assert debug[0]["causes"] == [
        {
            "exception_type": "ValueError",
            "diagnostic": "kernel rejected the selected face",
        }
    ]


def test_non_json_or_oversized_handler_results_are_rejected_and_cached() -> None:
    values = iter(({"bad": object()}, {"large": "x" * (70 * 1024)}))
    dispatcher, _state, _debug = _dispatcher(lambda _call: next(values))

    invalid = dispatcher.call("test.execute", _arguments(1), "provider-call-1")
    repeated = dispatcher.call("test.execute", _arguments(1), "provider-call-1")
    oversized = dispatcher.call("test.execute", _arguments(2), "provider-call-2")

    assert invalid["error_code"] == "NATIVE_RESULT_INVALID"
    assert repeated == invalid
    assert oversized["error_code"] == "NATIVE_RESULT_TOO_LARGE"


def test_runtime_binding_set_must_exactly_match_the_frozen_tools() -> None:
    with pytest.raises(Exception, match="bindings do not match"):
        dispatcher, _state, _debug = _dispatcher(lambda _call: {}, runtimes={})
        del dispatcher
