# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contracts for Drawing parallel and perpendicular lines."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_COSMETIC_LINE_CAPABILITY_NAME = "drawing.cosmetic_line"
DRAWING_COSMETIC_LINE_OPERATIONS = (
    "create_parallel",
    "create_perpendicular",
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


def _element(name_schema: dict) -> dict:
    return _closed(
        {
            "subelement": name_schema,
        },
        ("subelement",),
    )


_PARAMETERS = _closed(
    {
        "page": _PAGE,
        "view": _VIEW,
        "reference_edge": _element(_EDGE_NAME),
        "through_vertex": _element(_VERTEX_NAME),
    },
    ("page", "view", "reference_edge", "through_vertex"),
)


def drawing_cosmetic_line_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
        description=(
            "Create one durable host-styled Drawing cosmetic line parallel or "
            "perpendicular to an exact projected straight edge and centered on an "
            "exact projected vertex; TechDraw derives every coordinate and length."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_parallel",
                description=(
                    "Create a same-length cosmetic line parallel to one exact "
                    "projected straight edge and centered on one exact vertex."
                ),
                action_ids=frozenset({"TechDraw_ExtensionLineParallel"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingStraightEdgeAndThroughVertexParallelLine"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_PARAMETERS,
            ),
            NativeCapabilityVariant(
                operation="create_perpendicular",
                description=(
                    "Create a same-length cosmetic line perpendicular to one exact "
                    "projected straight edge and centered on one exact vertex."
                ),
                action_ids=frozenset({"TechDraw_ExtensionLinePerpendicular"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingStraightEdgeAndThroughVertexPerpendicularLine"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_PARAMETERS,
            ),
            NativeCapabilityVariant(
                operation="create_between_vertices",
                description=(
                    "Create one persistent straight cosmetic line through exactly "
                    "two distinct exact projected vertices. TechDraw derives both "
                    "canonical endpoints and applies the current host line format."
                ),
                action_ids=frozenset({"TechDraw_2PointCosmeticLine"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingVertexPairAndCosmeticLine",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "view": _VIEW,
                        "vertices": {
                            "type": "array",
                            "items": _element(_VERTEX_NAME),
                            "minItems": 2,
                            "maxItems": 2,
                            "description": (
                                "Exactly two distinct projected VertexN targets in "
                                "requested endpoint order."
                            ),
                        },
                    },
                    ("page", "view", "vertices"),
                ),
            ),
        ),
    )


def register_drawing_cosmetic_line_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_cosmetic_line_capability_definition())
