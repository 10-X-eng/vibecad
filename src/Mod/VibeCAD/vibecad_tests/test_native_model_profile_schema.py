# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeModelFeatureSchema import model_feature_capability_definition


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
