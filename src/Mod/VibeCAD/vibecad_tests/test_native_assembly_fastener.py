# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeAssemblyFastenerRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeAssemblyFastener import NativeAssemblyFastenerError
from VibeCADNativeAssemblyFastenerRuntime import NativeAssemblyFastenerRuntime
from VibeCADNativeAssemblyFastenerSchema import (
    ASSEMBLY_FASTENER_CAPABILITY_NAME,
    assembly_fastener_capability_definition,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "assembly-fastener-document"
    Name = "AssemblyFastenerDocument"


def _runtime() -> tuple[
    NativeAssemblyFastenerRuntime,
    NativeDocumentStateStore,
    _Document,
]:
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("assembly-fastener-unit")
    context = NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "assemble",
        edit_or_task_active=lambda: False,
    )
    return NativeAssemblyFastenerRuntime(context), state, document


def _definition() -> dict[str, object]:
    return {
        "standard": "ISO4762",
        "nominal_thread": "M6",
        "length_mm": 25.0,
        "model_thread": False,
        "left_handed": False,
        "options": {},
    }


def _arguments() -> dict[str, object]:
    return {
        "operation": "insert_standard_fastener",
        "assembly": {"object_name": "MainAssembly"},
        "label": "M6 socket bolt",
        "definition": _definition(),
        "expected_state_sha256": "a" * 64,
        "expected_component_count": 2,
        "expected_grounded_count": 1,
        "expected_joint_count": 1,
    }


def test_assembly_fastener_contract_is_exact_and_has_no_placement_guessing() -> None:
    definition = assembly_fastener_capability_definition()
    variant = definition.variants[0]
    schema = definition.provider_schema(("insert_standard_fastener",))
    branch = schema["parameters"]["oneOf"][0]
    fields = set(branch["properties"])

    assert definition.name == ASSEMBLY_FASTENER_CAPABILITY_NAME
    assert definition.primary_classification == "mutation"
    assert variant.action_ids == frozenset({"VibeCAD_InsertStandardFastener"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.transaction_behavior == "document"
    assert branch["additionalProperties"] is False
    assert fields == {
        "operation",
        "assembly",
        "label",
        "definition",
        "expected_state_sha256",
        "expected_component_count",
        "expected_grounded_count",
        "expected_joint_count",
    }
    assert fields.isdisjoint(
        {"placement", "position", "path", "file", "command", "workbench"}
    )


def test_assembly_fastener_runtime_preflights_then_routes_one_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    prepared = object()
    captured = {}

    def preflight(target_document, spec):
        assert target_document is document
        assert spec.assembly_ref.object_name == "MainAssembly"
        assert spec.label == "M6 socket bolt"
        assert spec.definition == _definition()
        assert spec.expected_state_sha256 == "a" * 64
        assert spec.expected_component_count == 2
        assert spec.expected_grounded_count == 1
        assert spec.expected_joint_count == 1
        return prepared

    monkeypatch.setattr(
        runtime_module,
        "preflight_insert_assembly_fastener",
        preflight,
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_fastener(
        _arguments(),
        ticket=state.begin_call(document.Uid, ASSEMBLY_FASTENER_CAPABILITY_NAME),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Insert Native Assembly Fastener"
    assert captured["mutate"].keywords == {"prepared": prepared}
    assert captured["verify"] is runtime_module.verify_inserted_assembly_fastener


def test_assembly_fastener_runtime_rejects_extra_authority_before_preflight() -> None:
    runtime, state, document = _runtime()
    arguments = _arguments()
    arguments["placement"] = {
        "origin_mm": [0.0, 0.0, 0.0],
        "rotation": {"axis": [0.0, 0.0, 1.0], "degrees": 0.0},
    }

    with pytest.raises(NativeArgumentError, match="do not match"):
        runtime.mutate_fastener(
            arguments,
            ticket=state.begin_call(
                document.Uid,
                ASSEMBLY_FASTENER_CAPABILITY_NAME,
            ),
        )


def test_assembly_fastener_runtime_requires_one_exact_assembly_reference() -> None:
    runtime, state, document = _runtime()
    arguments = _arguments()
    arguments["assembly"] = {
        "object_name": "MainAssembly",
        "fallback": "any",
    }

    with pytest.raises(NativeAssemblyFastenerError, match="assembly"):
        runtime.mutate_fastener(
            arguments,
            ticket=state.begin_call(
                document.Uid,
                ASSEMBLY_FASTENER_CAPABILITY_NAME,
            ),
        )
