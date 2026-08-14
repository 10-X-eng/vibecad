# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contracts for Drawing cosmetic circles and arcs."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingCosmeticCurveState import (
    DRAWING_COSMETIC_CURVE_KINDS,
    MAX_DRAWING_COSMETIC_RADIUS_MM,
)


DRAWING_COSMETIC_CURVE_CAPABILITY_NAME = "drawing.cosmetic_curve"
DRAWING_COSMETIC_CURVE_OPERATIONS = tuple(
    f"create_{kind}" for kind in DRAWING_COSMETIC_CURVE_KINDS
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
_VERTEX = _closed(
    {
        "subelement": _VERTEX_NAME,
        "expected_element_state_sha256": _SHA256,
    },
    ("subelement", "expected_element_state_sha256"),
)
_RADIUS = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": MAX_DRAWING_COSMETIC_RADIUS_MM,
    "description": "Explicit positive unscaled Drawing-view radius in millimetres.",
}


def _parameters(extra: dict, required: tuple[str, ...]) -> dict:
    return _closed({"page": _PAGE, "view": _VIEW, **extra}, ("page", "view", *required))


def drawing_cosmetic_curve_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
        description=(
            "Create one durable host-styled Drawing cosmetic circle or arc from "
            "named, hash-pinned projected vertices; TechDraw derives all geometry."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_one_point_circle",
                description=(
                    "Create a full circle centered on one exact projected vertex "
                    "with one explicit positive radius."
                ),
                action_ids=frozenset({"TechDraw_CosmeticCircle"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingCenterVertexAndExplicitRadius",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(
                    {"center_vertex": _VERTEX, "radius_mm": _RADIUS},
                    ("center_vertex", "radius_mm"),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_two_point_circle",
                description=(
                    "Create a full circle from an exact center vertex and a distinct "
                    "exact vertex on its radius."
                ),
                action_ids=frozenset({"TechDraw_ExtensionDrawCosmCircle"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingCenterAndRadiusVertices",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(
                    {"center_vertex": _VERTEX, "radius_vertex": _VERTEX},
                    ("center_vertex", "radius_vertex"),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_three_point_circle",
                description=(
                    "Create the unique full circle through three ordered, distinct, "
                    "non-collinear projected vertices."
                ),
                action_ids=frozenset({"TechDraw_ExtensionDrawCosmCircle3Points"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingThreePerimeterVertices",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(
                    {
                        "first_perimeter_vertex": _VERTEX,
                        "second_perimeter_vertex": _VERTEX,
                        "third_perimeter_vertex": _VERTEX,
                    },
                    (
                        "first_perimeter_vertex",
                        "second_perimeter_vertex",
                        "third_perimeter_vertex",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_center_start_end_arc",
                description=(
                    "Create the host counter-clockwise arc from exact center, start, "
                    "and end-angle projected vertices in that order."
                ),
                action_ids=frozenset({"TechDraw_ExtensionDrawCosmArc"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingCenterStartAndEndAngleVertices",
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(
                    {
                        "center_vertex": _VERTEX,
                        "start_vertex": _VERTEX,
                        "end_vertex": _VERTEX,
                    },
                    ("center_vertex", "start_vertex", "end_vertex"),
                ),
            ),
        ),
    )


def register_drawing_cosmetic_curve_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_cosmetic_curve_capability_definition())
