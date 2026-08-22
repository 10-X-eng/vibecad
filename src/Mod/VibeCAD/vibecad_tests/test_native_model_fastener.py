# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeModelFastener as fastener_module
import VibeCADNativeModelFastenerRuntime as runtime_module
from VibeCADNativeArguments import NativeArgumentError
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeModelFastener import prepare_model_fastener
from VibeCADNativeModelFastenerRuntime import NativeModelFastenerRuntime
from VibeCADNativeModelFastenerSchema import model_fastener_capability_definition
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeDocumentStateStore
from VibeCADNativeUndo import NativeAssistantUndoLedger


class _Document:
    Uid = "document-fastener"
    Name = "DocumentFastener"


def _runtime():
    document = _Document()
    state = NativeDocumentStateStore()
    state.begin_native_authority(document.Uid)
    ledger = NativeAssistantUndoLedger()
    ledger.begin_run("model-fastener-unit")
    context = NativeRuntimeContext(
        service=SimpleNamespace(),
        document=document,
        state=state,
        undo_ledger=ledger,
        reauthorize_turn=lambda: None,
        active_document=lambda: document,
        active_surface_id=lambda: "model",
        edit_or_task_active=lambda: False,
    )
    return NativeModelFastenerRuntime(context), state, document


def _definition() -> dict[str, object]:
    return {
        "standard": "ISO4762",
        "nominal_thread": "M3",
        "length_mm": 10.0,
        "model_thread": False,
        "left_handed": False,
    }


def _arguments() -> dict[str, object]:
    return {
        "operation": "insert_standard_fastener",
        "label": "M3 socket bolt",
        "definition": _definition(),
    }


def _edit_arguments() -> dict[str, object]:
    return {
        "operation": "edit_standard_fastener",
        "target": {"object_name": "M3SocketBolt"},
        "label": "Edited M3 socket bolt",
        "definition": {**_definition(), "length_mm": 12.0},
    }


def _matching_hole_arguments() -> dict[str, object]:
    return {
        "operation": "create_matching_fastener_hole",
        "label": "M3 clearance holes",
        "fastener": {"object_name": "M3SocketBolt"},
        "profile": {"object_name": "HoleLocations"},
        "purpose": "clearance",
        "fit": "normal",
        "targets": [{"object_name": "HostBody"}],
    }


def _attachment_arguments() -> dict[str, object]:
    return {
        "operation": "attach_standard_fastener",
        "fastener": {"object_name": "M3SocketBolt"},
        "host": {
            "object_name": "HostBody",
            "subelement": "Edge7",
        },
    }


def test_fastener_contract_matches_the_model_ribbon_insert_action() -> None:
    definition = model_fastener_capability_definition()
    schema = definition.provider_schema(("insert_standard_fastener",))
    branch = schema["parameters"]["oneOf"][0]
    fastener = branch["properties"]["definition"]

    assert definition.name == "model.fastener"
    assert definition.primary_classification == "mutation"
    assert definition.variants[0].action_ids == frozenset(
        {"VibeCAD_InsertStandardFastener"}
    )
    assert definition.variants[0].background_required is False
    assert set(branch["required"]) == {"label", "definition"}
    assert branch["additionalProperties"] is False
    assert set(fastener["required"]) == set(_definition()) - {"length_mm"}
    assert "catalog_option_overrides" not in fastener["required"]
    assert "length_mm" not in fastener["required"]
    assert fastener["properties"]["length_mm"]["type"] == "number"
    assert (
        fastener["properties"]["catalog_option_overrides"][
            "additionalProperties"
        ]
        is False
    )
    assert (
        fastener["properties"]["catalog_option_overrides"]["required"]
        == []
    )
    serialized = repr(schema)
    for forbidden in ("selection", "workbench", "runCommand", "assembly"):
        assert forbidden not in serialized


def test_fastener_contract_maps_exact_body_editing_to_the_edit_action() -> None:
    definition = model_fastener_capability_definition()
    schema = definition.provider_schema(("edit_standard_fastener",))
    branch = schema["parameters"]["oneOf"][0]

    assert definition.variants[1].action_ids == frozenset(
        {"VibeCAD_EditStandardFastener"}
    )
    assert definition.variants[1].exact_target_type == "PartDesign::Body"
    assert definition.variants[1].background_required is False
    assert set(branch["required"]) == {
        "target",
        "label",
        "definition",
    }
    assert branch["properties"]["target"]["additionalProperties"] is False
    assert branch["properties"]["target"]["required"] == ["object_name"]


def test_fastener_contract_maps_matching_holes_to_exact_reusable_inputs() -> None:
    definition = model_fastener_capability_definition()
    schema = definition.provider_schema(("create_matching_fastener_hole",))
    branch = schema["parameters"]["oneOf"][0]
    variant = definition.variants[2]

    assert variant.action_ids == frozenset(
        {"VibeCAD_CreateMatchingFastenerHole"}
    )
    assert variant.background_required is False
    assert set(branch["required"]) == set(_matching_hole_arguments()) - {
        "operation"
    }
    assert branch["additionalProperties"] is False
    assert branch["properties"]["fastener"]["required"] == ["object_name"]
    assert branch["properties"]["profile"]["required"] == ["object_name"]
    assert branch["properties"]["targets"]["minItems"] == 1
    assert branch["properties"]["targets"]["maxItems"] == 16
    assert branch["properties"]["targets"]["uniqueItems"] is True
    assert branch["properties"]["purpose"]["enum"] == [
        "clearance",
        "tapped",
        "counterbore",
        "countersink",
    ]
    assert branch["properties"]["fit"]["enum"] == [
        "normal",
        "close",
        "loose",
    ]


def test_fastener_contract_maps_attachment_to_one_exact_circular_edge() -> None:
    definition = model_fastener_capability_definition()
    schema = definition.provider_schema(("attach_standard_fastener",))
    branch = schema["parameters"]["oneOf"][0]
    variant = definition.variants[3]

    assert variant.action_ids == frozenset({"VibeCAD_AttachStandardFastener"})
    assert variant.exact_target_type == (
        "RetainedFastenerBody + DesignBody.CircularEdge"
    )
    assert variant.background_required is False
    assert set(branch["required"]) == set(_attachment_arguments()) - {"operation"}
    assert branch["additionalProperties"] is False
    assert branch["properties"]["fastener"]["required"] == ["object_name"]
    host = branch["properties"]["host"]
    assert host["additionalProperties"] is False
    assert set(host["required"]) == {"object_name", "subelement"}
    assert host["properties"]["subelement"]["pattern"] == (
        r"^Edge[1-9][0-9]*$"
    )


def test_fastener_preparation_resolves_the_exact_catalog_constructor(
    monkeypatch,
) -> None:
    observed = {}

    def resolve(**constructor):
        observed.update(constructor)
        return {
            **constructor,
            "nominal_size": constructor["nominal_thread"],
            "canonical_key": "freecad-fasteners:123",
            "part_number": "ISO4762 M3x10",
        }

    monkeypatch.setattr(fastener_module, "resolve_fastener", resolve)

    prepared = prepare_model_fastener(
        {
            **_definition(),
            "catalog_option_overrides": {
                "blind": True,
                "thread_length_mm": 8,
                "number_of_starts": 2,
            },
        }
    )

    assert observed == {
        "standard": "ISO4762",
        "nominal_thread": "M3",
        "length_mm": 10.0,
        "model_thread": False,
        "left_handed": False,
        "options": {
            "blind": True,
            "thread_length_mm": 8.0,
            "number_of_starts": 2,
        },
    }
    assert prepared.identity["canonical_key"] == "freecad-fasteners:123"


@pytest.mark.parametrize(
    ("field", "value"),
    (
        ("standard", ""),
        ("length_mm", True),
        ("model_thread", 1),
        ("left_handed", "false"),
    ),
)
def test_fastener_preparation_rejects_invalid_controls(field, value) -> None:
    definition = _definition()
    definition[field] = value
    with pytest.raises(NativeModelError):
        prepare_model_fastener(definition)


def test_fastener_preparation_rejects_extra_and_invalid_option_fields() -> None:
    definition = _definition()
    definition["hidden"] = True
    with pytest.raises(NativeModelError, match="definition"):
        prepare_model_fastener(definition)

    definition = _definition()
    definition["catalog_option_overrides"] = {"unknown": "value"}
    with pytest.raises(NativeModelError, match="option"):
        prepare_model_fastener(definition)

    definition["catalog_option_overrides"] = {"number_of_starts": True}
    with pytest.raises(NativeModelError, match="number_of_starts"):
        prepare_model_fastener(definition)


def test_fastener_preparation_uses_catalog_defaults_without_overrides(
    monkeypatch,
) -> None:
    observed = {}

    monkeypatch.setattr(
        fastener_module,
        "resolve_fastener",
        lambda **constructor: observed.update(constructor)
        or {
            **constructor,
            "canonical_key": "freecad-fasteners:defaults",
        },
    )

    prepare_model_fastener(_definition())

    assert observed["options"] == {}


def test_fastener_preparation_omits_axial_length_when_the_standard_has_none(
    monkeypatch,
) -> None:
    observed = {}
    definition = _definition()
    del definition["length_mm"]
    monkeypatch.setattr(
        fastener_module,
        "resolve_fastener",
        lambda **constructor: observed.update(constructor)
        or {
            **constructor,
            "canonical_key": "freecad-fasteners:no-length",
        },
    )

    prepare_model_fastener(definition)

    assert observed["length_mm"] is None


def test_fastener_runtime_prepares_before_one_immediate_mutation(monkeypatch) -> None:
    runtime, state, document = _runtime()
    prepared = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "prepare_model_fastener",
        lambda definition: (
            prepared
            if definition == _definition()
            else pytest.fail("wrong fastener definition")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_model_fastener",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_fastener(
        _arguments(),
        ticket=state.begin_call(document.Uid, "model.fastener"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Insert Native Standard Fastener"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "M3 socket bolt"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_model_fastener


def test_fastener_runtime_preflights_and_routes_one_exact_edit(monkeypatch) -> None:
    runtime, state, document = _runtime()
    prepared = object()
    target = object()
    captured = {}
    monkeypatch.setattr(
        runtime_module,
        "prepare_model_fastener",
        lambda definition: (
            prepared
            if definition == _edit_arguments()["definition"]
            else pytest.fail("wrong edited fastener definition")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "preflight_model_fastener_edit",
        lambda target_document, value, candidate: (
            target
            if target_document is document
            and value == {"object_name": "M3SocketBolt"}
            and candidate is prepared
            else pytest.fail("wrong fastener edit preflight")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "edit_model_fastener",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_fastener(
        _edit_arguments(),
        ticket=state.begin_call(document.Uid, "model.fastener"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Edit Native Standard Fastener"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.target is target
    assert draft.label == "Edited M3 socket bolt"
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_model_fastener


def test_fastener_runtime_preflights_and_routes_one_matching_hole(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    prepared = object()
    captured = {}
    arguments = _matching_hole_arguments()
    expected_definition = {
        name: arguments[name]
        for name in ("fastener", "profile", "purpose", "fit", "targets")
    }
    monkeypatch.setattr(
        runtime_module,
        "prepare_matching_fastener_hole",
        lambda target_document, value: (
            prepared
            if target_document is document and value == expected_definition
            else pytest.fail("wrong matching-hole preflight")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "create_matching_fastener_hole",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_fastener(
        arguments,
        ticket=state.begin_call(document.Uid, "model.fastener"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Create Native Matching Fastener Hole"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.label == "M3 clearance holes"
    assert draft.spec is prepared
    assert captured["verify"] is runtime_module.verify_design_operation


def test_fastener_runtime_preflights_and_routes_one_exact_attachment(
    monkeypatch,
) -> None:
    runtime, state, document = _runtime()
    prepared = object()
    captured = {}
    arguments = _attachment_arguments()
    monkeypatch.setattr(
        runtime_module,
        "prepare_model_fastener_attachment",
        lambda target_document, value: (
            prepared
            if target_document is document
            and value
            == {
                "fastener": arguments["fastener"],
                "host": arguments["host"],
            }
            else pytest.fail("wrong fastener-attachment preflight")
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "attach_model_fastener",
        lambda target_document, **kwargs: SimpleNamespace(
            document=target_document,
            **kwargs,
        ),
    )
    monkeypatch.setattr(
        runtime_module,
        "run_immediate_mutation",
        lambda context, **kwargs: captured.update(kwargs) or {"routed": True},
    )

    result = runtime.mutate_fastener(
        arguments,
        ticket=state.begin_call(document.Uid, "model.fastener"),
    )

    assert result == {"routed": True}
    assert captured["transaction_name"] == "Attach Native Standard Fastener"
    draft = captured["mutate"](document)
    assert draft.document is document
    assert draft.prepared is prepared
    assert captured["verify"] is runtime_module.verify_model_fastener_attachment


def test_fastener_runtime_rejects_blank_labels_and_extra_fields(monkeypatch) -> None:
    runtime, state, document = _runtime()
    monkeypatch.setattr(
        runtime_module,
        "prepare_model_fastener",
        lambda _definition: pytest.fail("blank label reached preparation"),
    )
    arguments = _arguments()
    arguments["label"] = "   "
    with pytest.raises(NativeModelError, match="visible"):
        runtime.mutate_fastener(
            arguments,
            ticket=state.begin_call(document.Uid, "model.fastener"),
        )

    arguments = _arguments()
    arguments["selection"] = []
    with pytest.raises(NativeArgumentError, match="do not match"):
        runtime.mutate_fastener(
            arguments,
            ticket=state.begin_call(document.Uid, "model.fastener"),
        )
