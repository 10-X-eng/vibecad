# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact-element schema fragments for Native Sketch constraints."""

from __future__ import annotations

from VibeCADNativeDesignSchema import parameters_schema


def element_schema(
    positions: tuple[str, ...] = ("whole", "start", "end", "center"),
) -> dict:
    return parameters_schema(
        {
            "geometry_index": {
                "oneOf": [
                    {"type": "integer", "minimum": 0, "maximum": 999_999},
                    {"type": "integer", "enum": [-1, -2]},
                    {
                        "type": "integer",
                        "minimum": -1_000_000,
                        "maximum": -2001,
                    },
                    {"type": "integer", "minimum": -1999, "maximum": -3},
                ]
            },
            "position": {
                "type": "string",
                "enum": list(positions),
            },
        },
        ("geometry_index", "position"),
    )
