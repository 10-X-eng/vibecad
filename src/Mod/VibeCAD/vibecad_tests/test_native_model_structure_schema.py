# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from VibeCADNativeModelStructureSchema import (
    model_revolution_sketch_capability_definition,
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


def test_structure_contract_is_four_small_intent_focused_tools() -> None:
    definitions = model_structure_capability_definitions()

    assert [definition.name for definition in definitions] == [
        "model.structure",
        "model.sketch",
        "sketch.open",
        "sketch.validate",
    ]
    assert [variant.operation for variant in definitions[0].variants] == [
        "new_component",
        "new_body",
        "sub_shape_binder",
        "clone",
        "separate",
    ]
    assert definitions[3].primary_classification == "read"
    assert all(
        variant.surface_ids == frozenset({"model"})
        for variant in definitions[0].variants
    )
    assert all(
        variant.surface_ids == frozenset({"model", "sketch.setup"})
        for definition in definitions[1:]
        for variant in definition.variants
    )


def test_sketch_contract_is_standalone_noninteractive_and_explicitly_supported() -> None:
    definition = model_structure_capability_definitions()[1]
    assert [variant.operation for variant in definition.variants] == [
        "create_on_base_plane",
        "create_on_face",
        "create_on_datum_plane",
    ]
    plane_branch = definition.provider_schema(("create_on_base_plane",))["parameters"][
        "oneOf"
    ][0]
    validator = Draft202012Validator(plane_branch)
    assert not list(
        validator.iter_errors(
            {
                "operation": "create_on_base_plane",
                "label": "Profile",
                "plane": "XZ",
                "offset_mm": 0,
            }
        )
    )
    assert set(plane_branch["required"]) == {
        "label",
        "plane",
    }
    assert definition.variants[1].provider_supplemental is True

    face_branch = definition.provider_schema(("create_on_face",))[
        "parameters"
    ]["oneOf"][0]
    assert not list(
        Draft202012Validator(face_branch).iter_errors(
            {
                "operation": "create_on_face",
                "label": "Face Profile",
                "target": {"object_name": "Pad", "subelement": "Face3"},
            }
        )
    )
    assert set(face_branch["required"]) == {
        "label",
        "target",
    }
    assert face_branch["properties"]["target"]["properties"]["subelement"][
        "examples"
    ] == ["Face1"]
    assert definition.variants[2].provider_supplemental is True

    datum_branch = definition.provider_schema(("create_on_datum_plane",))[
        "parameters"
    ]["oneOf"][0]
    assert not list(
        Draft202012Validator(datum_branch).iter_errors(
            {
                "operation": "create_on_datum_plane",
                "label": "Datum Profile",
                "target": {"object_name": "DatumPlane"},
            }
        )
    )
    serialized = json.dumps(definition.provider_schema(
        ("create_on_base_plane", "create_on_face", "create_on_datum_plane")
    ), sort_keys=True)
    for forbidden in (
        "body_name",
        "enter_edit",
        "runCommand",
        "document_uid",
    ):
        assert forbidden not in serialized


def test_open_sketch_has_one_target_and_no_creation_fields() -> None:
    definition = model_structure_capability_definitions()[2]
    assert definition.name == "sketch.open"
    assert [variant.operation for variant in definition.variants] == ["open"]
    branch = definition.provider_schema(("open",))["parameters"]["oneOf"][0]
    assert set(branch["required"]) == {"sketch"}
    assert set(branch["properties"]) == {"operation", "sketch"}
    assert branch["additionalProperties"] is False


def test_revolution_sketch_is_a_focused_axis_aware_tool() -> None:
    definition = model_revolution_sketch_capability_definition()

    assert definition.name == "model.revolution_sketch"
    assert [variant.operation for variant in definition.variants] == ["create"]
    branch = definition.provider_schema(("create",))["parameters"]["oneOf"][0]
    assert set(branch["required"]) == {"label", "axis"}
    assert branch["properties"]["axis"]["enum"] == ["X", "Y", "Z"]
    assert not list(
        Draft202012Validator(branch).iter_errors(
            {
                "operation": "create",
                "label": "Turned Profile",
                "axis": "Z",
            }
        )
    )


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
