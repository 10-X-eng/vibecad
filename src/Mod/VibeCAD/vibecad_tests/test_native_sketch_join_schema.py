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
    ).encode()


def test_join_curves_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_geometry_capability_definition()
    schema = definition.provider_schema(("join_curves",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "join_curves",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 8,
        "expected_constraint_count": 4,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "first": {"geometry_index": 3, "endpoint": "end"},
        "second": {"geometry_index": 4, "endpoint": "start"},
    }
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "first": {"geometry_index": -1, "endpoint": "end"}},
        {**valid, "first": {"geometry_index": 1_000_000, "endpoint": "end"}},
        {**valid, "first": {"geometry_index": 3, "endpoint": "whole"}},
        {**valid, "first": {"geometry_index": True, "endpoint": "end"}},
        {**valid, "second": {"geometry_index": 4}},
        {**valid, "expected_external_reference_count": -1},
    ):
        assert list(validator.iter_errors(invalid))
    assert len(_encoded(schema)) <= MAX_NATIVE_SCHEMAS_JSON_BYTES
    all_operations = tuple(variant.operation for variant in definition.variants)
    all_encoded = _encoded(definition.provider_schema(all_operations))
    assert len(all_encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_join_curves_variant_matches_the_live_ribbon_action() -> None:
    variant = next(
        item
        for item in sketch_geometry_capability_definition().variants
        if item.operation == "join_curves"
    )
    assert variant.action_ids == frozenset({"Sketcher_JoinCurves"})
    assert variant.surface_ids == frozenset({"sketch.edit"})
    assert variant.exact_target_type == "ActiveSketchExactCurveEndpointPair"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
