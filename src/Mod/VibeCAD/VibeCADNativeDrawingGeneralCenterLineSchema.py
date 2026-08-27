# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact provider contract for face, two-edge, and two-vertex centerlines."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME = "drawing.centerline"
DRAWING_GENERAL_CENTER_LINE_OPERATIONS = (
    "create_face",
    "create_between_edges",
    "create_between_vertices",
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


def _source(prefix: str) -> dict:
    return _closed(
        {
            "subelement": {
                "type": "string",
                "pattern": rf"^{prefix}(?:0|[1-9][0-9]*)$",
                "maxLength": 32,
            },
        },
        ("subelement",),
    )


def _parameters(field: str, item: dict, minimum: int, maximum: int) -> dict:
    return _closed(
        {
            "page": _PAGE,
            "view": _VIEW,
            field: {
                "type": "array",
                "items": item,
                "minItems": minimum,
                "maxItems": maximum,
                "description": (
                    "Ordered unique exact projected targets. TechDraw derives the "
                    "centerline and applies the current host centerline defaults."
                ),
            },
        },
        ("page", "view", field),
    )


def drawing_general_center_line_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_GENERAL_CENTER_LINE_CAPABILITY_NAME,
        description=(
            "Create one persistent host-styled Drawing centerline from exact "
            "exact faces, two edges, or two vertices. TechDraw owns "
            "orientation repair, geometry derivation, defaults, and persistence."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_face",
                description=(
                    "Create one vertical host-default centerline through the combined "
                    "bounds of 1 to 64 exact projected faces."
                ),
                action_ids=frozenset({"TechDraw_FaceCenterLine"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingFacesAndDerivedCenterLine",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters("faces", _source("Face"), 1, 64),
            ),
            NativeCapabilityVariant(
                operation="create_between_edges",
                description=(
                    "Create one host-default centerline between exactly two distinct "
                    "projected edges, with impossible orientation repaired by TechDraw."
                ),
                action_ids=frozenset({"TechDraw_2LineCenterLine"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingEdgePairAndDerivedCenterLine",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters("edges", _source("Edge"), 2, 2),
            ),
            NativeCapabilityVariant(
                operation="create_between_vertices",
                description=(
                    "Create one host-default centerline between exactly two distinct "
                    "projected vertices, with impossible orientation repaired by TechDraw."
                ),
                action_ids=frozenset({"TechDraw_2PointCenterLine"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingVertexPairAndDerivedCenterLine",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters("vertices", _source("Vertex"), 2, 2),
            ),
        ),
    )


def register_drawing_general_center_line_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_general_center_line_capability_definition())
