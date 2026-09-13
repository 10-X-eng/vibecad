# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from VibeCADNativeCapabilityRegistry import MAX_NATIVE_SCHEMAS_JSON_BYTES
from VibeCADNativeSketchGeometrySchema import sketch_geometry_capability_definition


def _encoded(schema: dict) -> bytes:
    return json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def test_knot_multiplicity_increase_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("increase_bspline_knot_multiplicity",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "increase_bspline_knot_multiplicity",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 8,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "geometry_index": 3,
        "knot_index": 1,
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "geometry_index": [3]},
        {**valid, "geometry_index": -1},
        {**valid, "geometry_index": 1_000_000},
        {**valid, "knot_index": True},
        {**valid, "knot_index": -1},
        {**valid, "knot_index": 1_000_000},
        {**valid, "expected_external_geometry_count": -1},
    ):
        assert list(validator.iter_errors(invalid))

    encoded = _encoded(schema)
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES
    all_operations = tuple(
        variant.operation
        for variant in definition.variants
        if variant.operation
        not in {
            "decrease_bspline_knot_multiplicity",
            "insert_bspline_knot",
            "join_curves",
        }
    )
    all_encoded = _encoded(definition.provider_schema(all_operations))
    assert len(all_encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_variant_matches_the_live_increase_child_action() -> None:
    definition = sketch_geometry_capability_definition()
    variant = next(
        item
        for item in definition.variants
        if item.operation == "increase_bspline_knot_multiplicity"
    )
    assert variant.action_ids == frozenset({"Sketcher_BSplineIncreaseKnotMultiplicity"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactBSplineKnotAndExpectedState"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
