# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contracts for Drawing cosmetic-vertex creation."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingCosmeticVertexState import (
    MAX_DRAWING_VERTEX_OFFSET_MM,
)


DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME = "drawing.cosmetic_vertex"
DRAWING_COSMETIC_VERTEX_OPERATIONS = (
    "create_intersections",
    "create_offset",
    "create_point",
    "create_midpoints",
    "create_quadrants",
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
_EDGE_NAME = {
    "type": "string",
    "pattern": r"^Edge(?:0|[1-9][0-9]*)$",
    "maxLength": 32,
}
_VERTEX_NAME = {
    "type": "string",
    "pattern": r"^Vertex(?:0|[1-9][0-9]*)$",
    "maxLength": 32,
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
_EDGE = _closed(
    {
        "subelement": _EDGE_NAME,
    },
    ("subelement",),
)
_VERTEX = _closed(
    {
        "subelement": _VERTEX_NAME,
    },
    ("subelement",),
)
_OFFSET = _closed(
    {
        "x_mm": {
            "type": "number",
            "minimum": -MAX_DRAWING_VERTEX_OFFSET_MM,
            "maximum": MAX_DRAWING_VERTEX_OFFSET_MM,
        },
        "y_mm": {
            "type": "number",
            "minimum": -MAX_DRAWING_VERTEX_OFFSET_MM,
            "maximum": MAX_DRAWING_VERTEX_OFFSET_MM,
        },
    },
    ("x_mm", "y_mm"),
)
_POINT = _closed(
    {
        "x_mm": {
            "type": "number",
            "minimum": -MAX_DRAWING_VERTEX_OFFSET_MM,
            "maximum": MAX_DRAWING_VERTEX_OFFSET_MM,
        },
        "y_mm": {
            "type": "number",
            "minimum": -MAX_DRAWING_VERTEX_OFFSET_MM,
            "maximum": MAX_DRAWING_VERTEX_OFFSET_MM,
        },
    },
    ("x_mm", "y_mm"),
)


def drawing_cosmetic_vertex_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
        description=(
            "Create durable host-styled Drawing cosmetic vertices at an explicit "
            "unscaled view point or from exact projected geometry. "
            "TechDraw owns coordinate conversion, formatting, and persistence."
        ),
        primary_classification="mutation",
        preserve_operation_branches=True,
        variants=(
            NativeCapabilityVariant(
                operation="create_intersections",
                description=(
                    "Create one persistent cosmetic vertex at every host-derived "
                    "intersection of exactly two distinct projected edges."
                ),
                action_ids=frozenset({"TechDraw_ExtensionVertexAtIntersection"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingIntersectingEdgesAndDerivedCosmeticVertices"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "view": _VIEW,
                        "edges": {
                            "type": "array",
                            "items": _EDGE,
                            "minItems": 2,
                            "maxItems": 2,
                            "description": (
                                "Exactly two unique projected EdgeN targets. "
                                "TechDraw derives and creates every intersection."
                            ),
                        },
                    },
                    ("page", "view", "edges"),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_offset",
                description=(
                    "Create one persistent cosmetic vertex at an explicit unscaled "
                    "X/Y offset from one exact projected vertex. Zero offset is valid."
                ),
                action_ids=frozenset({"TechDraw_CommandAddOffsetVertex"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingProjectedVertexAndExplicitOffset",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "view": _VIEW,
                        "source_vertex": _VERTEX,
                        "offset_mm": _OFFSET,
                    },
                    ("page", "view", "source_vertex", "offset_mm"),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_point",
                description=(
                    "Create one persistent cosmetic vertex at an explicit point "
                    "relative to the Drawing view origin. Coordinates are unscaled "
                    "millimetres in the unrotated X-right/Y-up view frame."
                ),
                action_ids=frozenset({"TechDraw_CosmeticVertex"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingViewAndExplicitCosmeticVertexPoint",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "view": _VIEW,
                        "point_in_view_mm": _POINT,
                    },
                    ("page", "view", "point_in_view_mm"),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_midpoints",
                description=(
                    "Create one persistent cosmetic vertex at the host-derived "
                    "midpoint of each of 1 to 64 exact projected edges."
                ),
                action_ids=frozenset({"TechDraw_Midpoints"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingEdgesAndDerivedMidpointVertices",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "view": _VIEW,
                        "edges": {
                            "type": "array",
                            "items": _EDGE,
                            "minItems": 1,
                            "maxItems": 64,
                            "description": (
                                "One to 64 unique projected EdgeN targets. "
                                "TechDraw derives every canonical midpoint."
                            ),
                        },
                    },
                    ("page", "view", "edges"),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_quadrants",
                description=(
                    "Create three persistent cosmetic vertices at TechDraw's "
                    "ordered quarter-parameter points for each of 1 to 64 exact "
                    "projected edges. For a full circle these are its three new "
                    "quadrant points; the seam point already exists."
                ),
                action_ids=frozenset({"TechDraw_Quadrants"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingEdgesAndDerivedQuadrantVertices",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "view": _VIEW,
                        "edges": {
                            "type": "array",
                            "items": _EDGE,
                            "minItems": 1,
                            "maxItems": 64,
                            "description": (
                                "One to 64 unique projected EdgeN targets. "
                                "TechDraw derives three ordered canonical points "
                                "per edge; the provider supplies no coordinates."
                            ),
                        },
                    },
                    ("page", "view", "edges"),
                ),
            ),
        ),
    )


def register_drawing_cosmetic_vertex_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_cosmetic_vertex_capability_definition())
