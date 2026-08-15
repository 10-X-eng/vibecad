# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json

from jsonschema import Draft202012Validator

from VibeCADNativeActionManifest import classify_native_surface
from VibeCADNativeCapabilityRegistry import MAX_NATIVE_SCHEMAS_JSON_BYTES
from VibeCADNativeSketchInternalAlignmentSchema import (
    sketch_internal_alignment_variants,
)
from VibeCADRibbonSurface import RibbonAction, RibbonGroup, RibbonSurface


def _valid() -> dict:
    return {
        "operation": "restore_internal_alignment_geometry",
        "sketch": {"object_name": "Sketch"},
        "expected_geometry_count": 12,
        "expected_constraint_count": 8,
        "expected_external_reference_count": 1,
        "expected_external_geometry_count": 2,
        "targets": [
            {
                "geometry_index": 3,
                "expected_internal_geometry_count": 0,
            },
            {
                "geometry_index": 8,
                "expected_internal_geometry_count": 4,
            },
        ],
    }


def test_internal_alignment_schema_is_closed_bounded_and_exact() -> None:
    variant = sketch_internal_alignment_variants()[0]
    schema = {
        "type": "function",
        "name": "sketch.geometry",
        "description": variant.description,
        "parameters": {
            "oneOf": [
                {
                    **variant.parameters,
                    "properties": {
                        "operation": {
                            "type": "string",
                            "const": variant.operation,
                        },
                        **variant.parameters["properties"],
                    },
                    "required": ["operation", *variant.parameters["required"]],
                }
            ]
        },
    }
    validator = Draft202012Validator(schema["parameters"])
    valid = _valid()
    assert list(validator.iter_errors(valid)) == []
    for missing in tuple(key for key in valid if key != "operation"):
        invalid = dict(valid)
        del invalid[missing]
        assert list(validator.iter_errors(invalid))
    for invalid in (
        {**valid, "unexpected": True},
        {**valid, "targets": []},
        {**valid, "targets": valid["targets"] * 33},
        {
            **valid,
            "targets": [{"geometry_index": -1, "expected_internal_geometry_count": 0}],
        },
        {
            **valid,
            "targets": [{"geometry_index": 0, "expected_internal_geometry_count": -1}],
        },
        {**valid, "expected_geometry_count": True},
    ):
        assert list(validator.iter_errors(invalid))
    encoded = json.dumps(
        [schema],
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    assert len(encoded) <= MAX_NATIVE_SCHEMAS_JSON_BYTES


def test_internal_alignment_action_is_a_document_mutation_in_geometry() -> None:
    surface = RibbonSurface(
        "sketch.edit",
        1,
        (
            RibbonGroup(
                "Visual",
                (
                    RibbonAction(
                        "Sketcher_RestoreInternalAlignmentGeometry",
                        "Toggle Internal Geometry",
                        True,
                        "command",
                    ),
                ),
            ),
        ),
    )
    plan = classify_native_surface(surface)[0]
    variant = sketch_internal_alignment_variants()[0]
    assert plan.classification.mutation is True
    assert plan.classification.view is False
    assert plan.capability_family == "sketch.edit"
    assert plan.operation_variant == variant.operation
    assert plan.transaction_behavior == "document"
    assert variant.action_ids == frozenset(
        {"Sketcher_RestoreInternalAlignmentGeometry"}
    )
    assert variant.exact_target_type == "ActiveSketchExactInternalAlignmentTargets"
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
