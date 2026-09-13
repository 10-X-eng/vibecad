# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from VibeCADNativeCapabilityRegistry import provider_visible_native_schema
from VibeCADNativeDispatch import _schema_example
from VibeCADNativeModelFeatureSchema import (
    focused_model_feature_capability_definitions,
    model_feature_capability_definition,
)


PROFILE_OPERATIONS = ("extrude", "revolve", "loft", "sweep", "helix")


def _variants():
    definition = model_feature_capability_definition()
    return definition, {variant.operation: variant for variant in definition.variants}


def test_model_feature_owns_each_sketch_driven_body_operation() -> None:
    definition, variants = _variants()

    assert definition.name == "model.feature"
    assert tuple(variants) == ("create",)
    assert variants["create"].action_ids == frozenset(
        f"PartDesign_Design{operation.title()}" for operation in PROFILE_OPERATIONS
    )


def test_model_feature_uses_one_exact_nested_feature_contract() -> None:
    _definition, variants = _variants()

    parameters = variants["create"].parameters
    assert set(parameters["properties"]) == {
        "label",
        "profile",
        "feature",
        "combine",
        "destination_component",
    }
    assert parameters["required"] == ["label", "profile", "feature"]
    feature_branches = parameters["properties"]["feature"]["oneOf"]
    assert tuple(
        branch["properties"]["kind"]["const"] for branch in feature_branches
    ) == PROFILE_OPERATIONS
    assert all(branch["additionalProperties"] is False for branch in feature_branches)

    combine = parameters["properties"]["combine"]
    assert combine["required"] == ["kind", "bodies"]
    assert combine["properties"]["kind"] == {
        "type": "string",
        "enum": ["join", "cut", "intersect"],
    }
    assert combine["properties"]["bodies"]["items"]["required"] == ["object_name"]


def test_revolve_accepts_a_global_axis_or_an_exact_design_reference() -> None:
    _definition, variants = _variants()
    features = variants["create"].parameters["properties"]["feature"]["oneOf"]
    revolve = next(
        feature
        for feature in features
        if feature["properties"]["kind"]["const"] == "revolve"
    )
    axis = revolve["properties"]["axis"]

    global_axis, reference_axis = axis["oneOf"]
    assert global_axis["required"] == ["kind", "axis"]
    assert global_axis["properties"]["kind"]["const"] == "global_axis"
    assert global_axis["properties"]["axis"]["enum"] == ["X", "Y", "Z"]
    assert reference_axis["required"] == ["kind", "object_name", "subelement"]
    assert reference_axis["properties"]["kind"]["const"] == "subelement"
    assert reference_axis["properties"]["subelement"]["pattern"].startswith("^")


def test_model_feature_provider_contract_is_closed_and_compact() -> None:
    definition, variants = _variants()
    assert tuple(variants) == ("create",)
    schema = definition.provider_schema(("create",))
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    branches = schema["parameters"]["oneOf"]
    assert [branch["properties"]["operation"]["const"] for branch in branches] == [
        "create"
    ]
    assert all(branch["additionalProperties"] is False for branch in branches)
    assert len(encoded) < 18_000


def test_model_feature_provider_publishes_one_typed_nested_field_map() -> None:
    definition, _variants_by_name = _variants()
    schema = provider_visible_native_schema(
        definition.provider_schema(("create",))
    )
    branch = schema["parameters"]["oneOf"][0]
    feature = branch["properties"]["feature"]

    assert feature["type"] == "object"
    assert feature["required"] == ["kind"]
    assert feature["properties"]["kind"] == {
        "type": "string",
        "enum": list(PROFILE_OPERATIONS),
    }
    assert feature["description"] == (
        "Fields: extrude=direction,extent; revolve=axis,extent; "
        "loft=sections,ruled,closed; sweep=path,options; "
        "helix=axis,parameters,left_handed,reversed,outside,tolerance."
    )
    assert len(json.dumps(schema, separators=(",", ":"))) < 9_000


def test_model_feature_repair_example_is_valid_at_full_schema_depth() -> None:
    definition, variants = _variants()
    exact = variants["create"].provider_parameters()
    example = _schema_example(exact)

    assert not list(Draft202012Validator(exact).iter_errors(example))
    side = example["feature"]["extent"]["sides"][0]
    assert side == {
        "kind": "length",
        "length_mm": 1.0,
        "taper_degrees": 0.0,
    }


def test_profile_features_publish_focused_direct_contracts() -> None:
    definitions = {
        definition.name: definition
        for definition in focused_model_feature_capability_definitions()
    }

    assert tuple(definitions) == tuple(
        f"model.{operation}" for operation in PROFILE_OPERATIONS
    )
    revolve = provider_visible_native_schema(
        definitions["model.revolve"].provider_schema(("create",))
    )["parameters"]["oneOf"][0]
    assert set(revolve["properties"]) == {
        "label",
        "profile",
        "profile_scope",
        "internal_faces",
        "axis",
        "extent",
        "combine",
        "destination_component",
    }
    assert revolve["required"] == [
        "label",
        "profile",
        "profile_scope",
        "axis",
        "extent",
    ]
    assert revolve["properties"]["extent"]["properties"]["kind"]["enum"] == [
        "angle",
        "up_to_last",
        "up_to_first",
        "up_to_face",
        "two_angles",
    ]
    assert "direction" not in revolve["properties"]

    extrude = provider_visible_native_schema(
        definitions["model.extrude"].provider_schema(("create",))
    )["parameters"]["oneOf"][0]
    assert extrude["required"] == [
        "label",
        "profile",
        "profile_scope",
        "extent",
    ]
    assert extrude["properties"]["extent"]["properties"]["kind"]["enum"] == [
        "length",
        "up_to_last",
        "up_to_first",
        "up_to_face",
        "up_to_shape",
        "two_sides",
    ]
    extrude_example = _schema_example(
        definitions["model.extrude"].variants[0].provider_parameters()
    )
    assert extrude_example["profile"] == {"object_name": "value"}
    assert extrude_example["profile_scope"] == "entire_sketch"
    assert extrude_example["extent"] == {"kind": "length", "length_mm": 1.0}

    encoded_size = sum(
        len(
            json.dumps(
                provider_visible_native_schema(
                    definition.provider_schema(("create",))
                ),
                separators=(",", ":"),
            )
        )
        for definition in definitions.values()
    )
    assert encoded_size < 18_000
