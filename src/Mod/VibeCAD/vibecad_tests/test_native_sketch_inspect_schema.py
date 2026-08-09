# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from VibeCADNativeCapabilityRegistry import MAX_NATIVE_SCHEMAS_JSON_BYTES
from VibeCADNativeSketchInspectSchema import sketch_inspect_capability_definition


def _encoded(schema: dict) -> bytes:
    return json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def test_select_constraints_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_inspect_capability_definition()
    schema = definition.provider_schema(("select_constraints",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "select_constraints",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 8,
        "expected_constraint_count": 5,
        "expected_external_geometry_count": 2,
        "selection": [
            {"geometry_index": 3, "position": "whole"},
            {"geometry_index": -3, "position": "start"},
        ],
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "selection": []},
        {**valid, "selection": valid["selection"] * 17},
        {**valid, "selection": [{"geometry_index": -2000, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": True, "position": "whole"}]},
        {**valid, "selection": [{"geometry_index": 0, "position": "mid"}]},
        {**valid, "expected_external_geometry_count": -1},
    ):
        assert list(validator.iter_errors(invalid))
    assert len(_encoded(schema)) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_select_constraints_variant_matches_only_the_live_human_action() -> None:
    definition = sketch_inspect_capability_definition()
    assert definition.name == "sketch.inspect"
    assert definition.primary_classification == "read"
    assert len(definition.variants) == 2
    variant = definition.variants[0]
    assert variant.operation == "select_constraints"
    assert variant.action_ids == frozenset({"Sketcher_SelectConstraints"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactElementSelection"
    assert variant.transaction_behavior == "none"
    assert variant.background_required is False


def test_select_elements_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_inspect_capability_definition()
    schema = definition.provider_schema(("select_elements",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "select_elements",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 8,
        "expected_constraint_count": 5,
        "expected_external_geometry_count": 2,
        "constraints": [
            {
                "constraint_index": 3,
                "expected_type": "Coincident",
                "expected_name": "JoinedEndpoint",
            },
            {
                "constraint_index": 4,
                "expected_type": "Horizontal",
                "expected_name": "",
            },
        ],
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "constraints": []},
        {**valid, "constraints": valid["constraints"] * 17},
        {
            **valid,
            "constraints": [
                {
                    "constraint_index": -1,
                    "expected_type": "Coincident",
                    "expected_name": "",
                }
            ],
        },
        {
            **valid,
            "constraints": [
                {
                    "constraint_index": 0,
                    "expected_type": "",
                    "expected_name": "",
                }
            ],
        },
        {
            **valid,
            "constraints": [
                {
                    "constraint_index": 0,
                    "expected_type": "Horizontal",
                    "expected_name": "",
                    "unexpected": True,
                }
            ],
        },
    ):
        assert list(validator.iter_errors(invalid))
    assert len(_encoded(schema)) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_select_elements_variant_matches_only_the_live_human_action() -> None:
    definition = sketch_inspect_capability_definition()
    variant = definition.variants[1]
    assert variant.operation == "select_elements"
    assert variant.action_ids == frozenset(
        {"Sketcher_SelectElementsAssociatedWithConstraints"}
    )
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactConstraintSelection"
    assert variant.transaction_behavior == "none"
    assert variant.background_required is False
