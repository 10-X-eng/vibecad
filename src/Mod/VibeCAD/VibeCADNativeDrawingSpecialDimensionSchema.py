# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for specialized projected Drawing dimensions."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityVariant,
)


DRAWING_SPECIAL_DIMENSION_OPERATIONS = (
    "create_horizontal_chamfer",
    "create_vertical_chamfer",
    "create_arc_length_dimension",
)
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_PAGE = _closed(
    {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
    ("object_name", "expected_state_sha256"),
)
_VIEW = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
        "expected_projection_state_sha256": _SHA256,
    },
    (
        "object_name",
        "expected_state_sha256",
        "expected_projection_state_sha256",
    ),
)
_VERTEX = _closed(
    {
        "subelement": {
            "type": "string",
            "pattern": r"^Vertex(0|[1-9][0-9]*)$",
            "maxLength": 32,
        },
    },
    ("subelement",),
)
_EDGE = _closed(
    {
        "subelement": {
            "type": "string",
            "pattern": r"^Edge(0|[1-9][0-9]*)$",
            "maxLength": 32,
        },
    },
    ("subelement",),
)
_BASE = {
    "label": {
        "type": "string",
        "minLength": 1,
        "maxLength": 160,
        "description": (
            "Preferred document label. The result reports FreeCAD's exact assigned label."
        ),
    },
    "page": _PAGE,
    "view": _VIEW,
    "label_position_on_page_mm": {
        **_closed(
            {
                "x_mm": {
                    "type": "number",
                    "minimum": -10_000.0,
                    "maximum": 10_000.0,
                },
                "y_mm": {
                    "type": "number",
                    "minimum": -10_000.0,
                    "maximum": 10_000.0,
                },
            },
            ("x_mm", "y_mm"),
        ),
        "description": "Dimension-label center in page coordinates, in mm.",
    },
}
_CHAMFER = {**_BASE, "first_vertex": _VERTEX, "second_vertex": _VERTEX}
_ARC_LENGTH = {**_BASE, "arc_edge": _EDGE}


def _variant(
    operation: str,
    description: str,
    action_id: str,
    target_type: str,
    parameters: dict,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"drawing"}),
        exact_target_type=target_type,
        transaction_behavior="document",
        background_required=False,
        parameters=_closed(dict(parameters), tuple(parameters)),
    )


def drawing_special_dimension_variants() -> tuple[NativeCapabilityVariant, ...]:
    return (
        _variant(
            "create_horizontal_chamfer",
            "Create a horizontal size-and-angle chamfer from two ordered vertices.",
            "TechDraw_ExtensionCreateHorizChamferDimension",
            "ExactDrawingHorizontalChamferVertices",
            _CHAMFER,
        ),
        _variant(
            "create_vertical_chamfer",
            "Create a vertical size-and-angle chamfer from two ordered vertices.",
            "TechDraw_ExtensionCreateVertChamferDimension",
            "ExactDrawingVerticalChamferVertices",
            _CHAMFER,
        ),
        _variant(
            "create_arc_length_dimension",
            "Create an arc-length dimension from one exact open circular arc.",
            "TechDraw_ExtensionCreateLengthArc",
            "ExactDrawingCircularArcLength",
            _ARC_LENGTH,
        ),
    )
