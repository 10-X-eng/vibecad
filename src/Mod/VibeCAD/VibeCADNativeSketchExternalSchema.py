# SPDX-License-Identifier: LGPL-2.1-or-later

"""Compact provider parameters for exact Sketch external-geometry actions."""

from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema


def external_geometry_parameters() -> dict:
    source = {
        "oneOf": [
            parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            parameters_schema(
                {
                    "object_name": OBJECT_NAME_SCHEMA,
                    "subelement": {
                        "type": "string",
                        "pattern": "^(Face|Edge|Vertex)[1-9][0-9]*$",
                        "maxLength": 128,
                    },
                },
                ("object_name", "subelement"),
            ),
        ]
    }
    count = {"type": "integer", "minimum": 0, "maximum": 1_000_000}
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": count,
            "expected_constraint_count": count,
            "expected_external_reference_count": count,
            "expected_external_geometry_count": count,
            "source": source,
            "role": {"type": "string", "enum": ["defining", "reference"]},
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_reference_count",
            "expected_external_geometry_count",
            "source",
            "role",
        ),
    )
