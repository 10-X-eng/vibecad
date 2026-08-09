# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from VibeCADNativeCapabilityRegistry import MAX_NATIVE_SCHEMAS_JSON_BYTES
from VibeCADNativeSketchControlSchema import sketch_control_capability_definition


def test_leave_schema_is_closed_bounded_and_exact() -> None:
    definition = sketch_control_capability_definition()
    schema = definition.provider_schema(("leave",))
    validator = Draft202012Validator(schema["parameters"])
    valid = {
        "operation": "leave",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 4,
        "expected_constraint_count": 3,
    }

    assert definition.primary_classification == "mutation"
    assert definition.variants[0].action_ids == {"Sketcher_LeaveSketch"}
    assert definition.variants[0].surface_ids == {"sketch.edit"}
    assert definition.variants[0].transaction_behavior == "edit_control"
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "sketch": {"object_name": "Sketch", "label": "Sketch"}},
        {**valid, "expected_geometry_count": -1},
        {**valid, "expected_constraint_count": 1_000_001},
    ):
        assert list(validator.iter_errors(invalid))
    assert len(
        json.dumps(
            [schema],
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ) <= MAX_NATIVE_SCHEMAS_JSON_BYTES
