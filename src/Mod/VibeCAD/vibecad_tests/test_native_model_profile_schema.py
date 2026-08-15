# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from VibeCADNativeModelFeatureSchema import model_feature_capability_definition


PROFILE_KINDS = (
    "extrude",
    "revolve",
    "loft",
    "sweep",
    "helix",
)


def _contract():
    definition = model_feature_capability_definition()
    schema = definition.provider_schema(("profile",))
    branch = schema["parameters"]["oneOf"][0]
    return branch, branch["properties"]["definition"]


def _kind_values(schema):
    if "oneOf" in schema:
        return [
            value
            for branch in schema["oneOf"]
            for value in _kind_values(branch)
        ]
    kind = schema["properties"]["kind"]
    return [kind["const"]] if "const" in kind else kind["enum"]


def test_profile_contract_matches_the_five_current_design_actions() -> None:
    definition = model_feature_capability_definition()
    variant = next(item for item in definition.variants if item.operation == "profile")

    assert variant.operation == "profile"
    _branch, contract = _contract()
    assert tuple(_kind_values(contract)) == PROFILE_KINDS
    assert variant.action_ids == frozenset(
        f"PartDesign_Design{name.title()}" for name in PROFILE_KINDS
    )
    assert variant.surface_ids == frozenset({"model"})


def test_extrude_exposes_current_direction_side_and_termination_controls() -> None:
    _branch, contract = _contract()
    direction = contract["properties"]["direction"]
    extent = contract["properties"]["extent"]["anyOf"][0]

    assert _kind_values(direction) == [
        "sketch_normal",
        "reference_axis",
        "custom_vector",
    ]
    assert [branch["required"] for branch in direction["oneOf"]] == [
        ["kind"],
        ["kind", "target", "along_sketch_normal"],
        ["kind", "vector", "along_sketch_normal"],
    ]
    assert _kind_values(extent) == ["one_side", "symmetric", "two_sides"]
    assert extent["required"] == ["kind", "sides", "reversed"]
    assert extent["additionalProperties"] is False
    assert set(extent["properties"]) == {
        "kind",
        "sides",
        "reversed",
    }
    sides = extent["properties"]["sides"]
    assert (sides["minItems"], sides["maxItems"]) == (1, 2)
    side = sides["items"]
    assert _kind_values(side) == [
        "length",
        "up_to_last",
        "up_to_first",
        "up_to_face",
        "up_to_shape",
    ]
    assert [branch["required"] for branch in side["oneOf"]] == [
        ["kind", "length_mm", "taper_degrees"],
        ["kind", "offset_mm"],
        ["kind", "offset_mm"],
        ["kind", "target", "offset_mm"],
        ["kind", "target", "offset_mm"],
    ]
    assert all(branch["additionalProperties"] is False for branch in side["oneOf"])


def test_revolve_up_to_last_does_not_invent_disabled_direction_controls() -> None:
    _branch, contract = _contract()
    extent = contract["properties"]["extent"]["anyOf"][1]

    assert _kind_values(extent) == [
        "angle",
        "up_to_last",
        "up_to_first",
        "up_to_face",
        "two_angles",
    ]
    assert extent["required"] == ["kind"]
    assert set(extent["properties"]) == {
        "kind",
        "angle_degrees",
        "angle1_degrees",
        "angle2_degrees",
        "target",
        "symmetric",
        "reversed",
    }
    assert "up_to_last uses kind only" in extent["description"]


def test_sweep_and_helix_use_typed_modes_without_unused_option_fields() -> None:
    _branch, contract = _contract()
    sweep_options = contract["properties"]["options"]
    orientation = sweep_options["properties"]["orientation"]
    helix = contract["properties"]["parameters"]

    assert _kind_values(orientation) == [
        "standard",
        "fixed",
        "frenet",
        "auxiliary",
        "binormal",
    ]
    assert _kind_values(helix) == [
        "pitch_height_angle",
        "pitch_turns_angle",
        "height_turns_angle",
        "height_turns_growth",
    ]
    assert "auxiliary_spine" not in sweep_options["properties"]
    assert "binormal" not in sweep_options["properties"]


def test_profile_references_are_exact_bounded_and_noninteractive() -> None:
    definition = model_feature_capability_definition()
    provider_branch = definition.provider_schema(("profile",))["parameters"][
        "oneOf"
    ][0]
    profile = provider_branch["properties"]["profile"]
    regions = profile["properties"]["regions"]
    assert regions["maxItems"] == 64
    assert regions["items"]["pattern"].startswith("^")
    assert profile["additionalProperties"] is False
    _provider_branch, contract = _contract()
    assert contract["additionalProperties"] is False
    assert contract["required"] == ["kind"]

    serialized = json.dumps(provider_branch, sort_keys=True)
    for forbidden in ("selection", "runCommand", "enter_edit", "workbench"):
        assert forbidden not in serialized


def test_repeated_termination_contract_stays_compact_under_surface_budget() -> None:
    definition = model_feature_capability_definition()
    schema = definition.provider_schema(("profile",))
    encoded = json.dumps(
        schema,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")

    assert len(encoded) < 12_500
