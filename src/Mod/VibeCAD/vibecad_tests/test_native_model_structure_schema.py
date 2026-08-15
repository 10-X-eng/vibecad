# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeModelStructureSchema import (
    model_structure_capability_definitions,
)


def _schemas():
    return {
        definition.name: definition.provider_schema(
            tuple(variant.operation for variant in definition.variants)
        )
        for definition in model_structure_capability_definitions()
    }


def _structure_branch(operation: str):
    structure = model_structure_capability_definitions()[0]
    return structure.provider_schema((operation,))["parameters"]["oneOf"][0]


def test_structure_contract_is_three_small_intent_focused_tools() -> None:
    definitions = model_structure_capability_definitions()

    assert [definition.name for definition in definitions] == [
        "model.structure",
        "model.sketch",
        "sketch.validate",
    ]
    assert [variant.operation for variant in definitions[0].variants] == [
        "new_component",
        "new_body",
        "sub_shape_binder",
        "clone",
        "separate",
    ]
    assert definitions[2].primary_classification == "read"
    assert all(
        variant.surface_ids == frozenset({"model"})
        for definition in definitions
        for variant in definition.variants
    )


def test_sketch_contract_is_standalone_noninteractive_and_explicitly_supported() -> None:
    branch = _schemas()["model.sketch"]["parameters"]["oneOf"][0]
    support = branch["properties"]["support"]
    support_kinds = [
        item["properties"]["kind"]["const"] for item in support["oneOf"]
    ]

    assert support_kinds == ["base_plane", "datum_plane", "planar_face"]
    assert set(branch["required"]) == {"operation", "label", "support"}
    serialized = json.dumps(branch, sort_keys=True)
    for forbidden in (
        "body_name",
        "enter_edit",
        "runCommand",
        "document_uid",
    ):
        assert forbidden not in serialized


def test_reference_and_clone_targets_are_exact_and_bounded() -> None:
    branches = {
        operation: _structure_branch(operation)
        for operation in ("sub_shape_binder", "clone")
    }
    references = branches["sub_shape_binder"]["properties"]["references"]
    assert (references["minItems"], references["maxItems"]) == (1, 32)
    subelements = references["items"]["properties"]["subelements"]
    assert subelements["maxItems"] == 64
    assert subelements["items"]["pattern"].startswith("^")
    assert branches["clone"]["properties"]["source_body"][
        "additionalProperties"
    ] is False


def test_separate_contract_has_only_exact_human_controls() -> None:
    branch = _structure_branch("separate")

    assert set(branch["required"]) == {
        "operation",
        "label",
        "source",
        "destination_component",
    }
    assert branch["additionalProperties"] is False
    assert branch["properties"]["source"]["additionalProperties"] is False
    assert branch["properties"]["destination_component"]["oneOf"][1] == {
        "type": "null"
    }
    serialized = json.dumps(branch, sort_keys=True)
    for forbidden in ("refine", "subelement", "selection", "runCommand"):
        assert forbidden not in serialized
