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
    ASSEMBLY_FASTENER_EDIT_CAPABILITY_NAME,
    assembly_fastener_capability_definition,
    assembly_fastener_edit_capability_definition,
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
    }


def _arguments() -> dict[str, object]:
    return {
        "operation": "insert_standard_fastener",
        "label": "M6 socket bolt",
        "definition": _definition(),
    }


def _edit_arguments() -> dict[str, object]:
    return {
        "operation": "edit_standard_fastener",
        "occurrence": {"object_name": "SocketBoltOccurrence"},
        "label": "Edited M6 socket bolt",
        "definition": _definition(),
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
        "label",
        "definition",
    }
    assert fields.isdisjoint(
        {"placement", "position", "path", "file", "command", "workbench"}
    )
    fastener = branch["properties"]["definition"]
    assert "catalog_option_overrides" in fastener["properties"]
    assert "catalog_option_overrides" not in fastener["required"]
    assert "options" not in fastener["properties"]
    assert tuple(value.operation for value in definition.variants) == (
        "insert_standard_fastener",
    )


def test_assembly_fastener_edit_contract_requires_exact_selected_graph_state() -> None:
    definition = assembly_fastener_edit_capability_definition()
    variant = definition.variants[0]
    schema = definition.provider_schema(("edit_standard_fastener",))
    branch = schema["parameters"]["oneOf"][0]
    fields = set(branch["properties"])

    assert definition.name == ASSEMBLY_FASTENER_EDIT_CAPABILITY_NAME
    assert variant.action_ids == frozenset({"VibeCAD_EditStandardFastener"})
    assert variant.surface_ids == frozenset({"assemble"})
    assert variant.transaction_behavior == "document"
    assert branch["additionalProperties"] is False
    assert fields == {
        "operation",
        "occurrence",
        "label",
        "definition",
    }
    assert fields.isdisjoint(
        {"placement", "position", "path", "file", "command", "workbench"}
    )


def test_assembly_fastener_insert_and_edit_are_separate_focused_tools() -> None:
    insert = assembly_fastener_capability_definition()
    edit = assembly_fastener_edit_capability_definition()

    assert tuple(value.operation for value in insert.variants) == (
        "insert_standard_fastener",
    )
    assert tuple(value.operation for value in edit.variants) == (
        "edit_standard_fastener",
    )
    assert "occurrence" not in insert.provider_schema(
        ("insert_standard_fastener",)
    )["parameters"]["oneOf"][0]["properties"]
    assert "occurrence" in edit.provider_schema(
        ("edit_standard_fastener",)
    )["parameters"]["oneOf"][0]["properties"]


def test_assembly_fastener_runtime_preflights_then_routes_one_mutation(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    prepared = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "read_active_assembly",
        lambda target_document: SimpleNamespace(Name="MainAssembly"),
    )

    def preflight(target_document, spec):
        assert target_document is document
        assert spec.assembly_ref.object_name == "MainAssembly"
        assert spec.label == "M6 socket bolt"
        assert spec.definition == _definition()
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


def test_assembly_fastener_runtime_routes_exact_edit_graph(monkeypatch) -> None:
    runtime, state, document = _runtime()
    prepared = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "read_active_assembly",
        lambda target_document: SimpleNamespace(Name="MainAssembly"),
    )

    def preflight(target_document, spec):
        assert target_document is document
        assert spec.assembly_ref.object_name == "MainAssembly"
        assert spec.occurrence_ref.object_name == "SocketBoltOccurrence"
        assert spec.label == "Edited M6 socket bolt"
        assert spec.definition == _definition()
        return prepared

    monkeypatch.setattr(
        runtime_module,
        "preflight_edit_assembly_fastener",
        preflight,
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_fastener(
        _edit_arguments(),
        ticket=state.begin_call(
            document.Uid,
            ASSEMBLY_FASTENER_EDIT_CAPABILITY_NAME,
        ),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Edit Native Assembly Fastener"
    assert captured["mutate"].keywords == {"prepared": prepared}
    assert captured["verify"] is runtime_module.verify_edited_assembly_fastener


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


def test_assembly_fastener_runtime_requires_an_active_assembly(monkeypatch) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(runtime_module, "read_active_assembly", lambda _document: None)

    with pytest.raises(NativeAssemblyFastenerError, match="No Assembly is active"):
        runtime.mutate_fastener(
            _arguments(),
            ticket=state.begin_call(
                document.Uid,
                ASSEMBLY_FASTENER_CAPABILITY_NAME,
            ),
        )
