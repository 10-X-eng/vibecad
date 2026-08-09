# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider schema for the exact Sketch Carbon Copy operation."""

from __future__ import annotations

from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema


def _count_schema() -> dict:
    return {"type": "integer", "minimum": 0, "maximum": 1_000_000}


def carbon_copy_parameters() -> dict:
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
            "source_sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_source_geometry_count": _count_schema(),
            "expected_source_constraint_count": _count_schema(),
            "expected_source_external_reference_count": _count_schema(),
            "expected_source_external_geometry_count": _count_schema(),
            "geometry_mode": {
                "type": "string",
                "enum": ["construction", "regular"],
            },
            "reference_permission": {
                "type": "string",
                "enum": [
                    "same_body_aligned",
                    "cross_body_aligned",
                    "unaligned",
                ],
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "source_sketch",
            "expected_source_geometry_count",
            "expected_source_constraint_count",
            "expected_source_external_reference_count",
            "expected_source_external_geometry_count",
            "geometry_mode",
            "reference_permission",
        ),
    )
