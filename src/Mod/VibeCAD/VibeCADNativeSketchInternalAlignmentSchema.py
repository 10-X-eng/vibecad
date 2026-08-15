# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for the durable Sketch internal-geometry toggle."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import NativeCapabilityVariant
from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema
from VibeCADNativeSketchInternalAlignmentTarget import MAX_TARGETS, OPERATION


def _count_schema() -> dict:
    return {"type": "integer", "minimum": 0, "maximum": 1_000_000}


def internal_alignment_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": _count_schema(),
            "expected_constraint_count": _count_schema(),
            "expected_external_reference_count": _count_schema(),
            "expected_external_geometry_count": _count_schema(),
            "targets": {
                "type": "array",
                "items": parameters_schema(
                    {
                        "geometry_index": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 999_999,
                        },
                        "expected_internal_geometry_count": _count_schema(),
                    },
                    (
                        "geometry_index",
                        "expected_internal_geometry_count",
                    ),
                ),
                "minItems": 1,
                "maxItems": MAX_TARGETS,
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "targets",
        ),
    )


def sketch_internal_alignment_variants() -> tuple[NativeCapabilityVariant, ...]:
    return (
        NativeCapabilityVariant(
            operation=OPERATION,
            description=(
                "Toggle complete durable internal-alignment helpers for exact "
                "supported Sketch curves using expected current helper counts."
            ),
            action_ids=frozenset({"Sketcher_RestoreInternalAlignmentGeometry"}),
            surface_ids=frozenset({"sketch.edit"}),
            exact_target_type="ActiveSketchExactInternalAlignmentTargets",
            transaction_behavior="document",
            background_required=False,
            parameters=internal_alignment_parameters(),
        ),
    )
