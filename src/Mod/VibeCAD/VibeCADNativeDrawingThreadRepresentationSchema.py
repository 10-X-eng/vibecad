# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contracts for Drawing thread representations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingThreadRepresentationState import (
    MAX_DRAWING_THREAD_BOTTOM_TARGETS,
)


DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME = "drawing.thread_representation"
DRAWING_THREAD_REPRESENTATION_OPERATIONS = (
    "create_hole_side",
    "create_hole_bottom",
    "create_bolt_side",
    "create_bolt_bottom",
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
        "expected_element_state_sha256": _SHA256,
    },
    ("subelement", "expected_element_state_sha256"),
)


def _side_parameters() -> dict:
    return _closed(
        {
            "page": _PAGE,
            "view": _VIEW,
            "boundary_edges": {
                "type": "array",
                "items": _EDGE,
                "minItems": 2,
                "maxItems": 2,
                "description": (
                    "Exactly two unique nonzero parallel projected straight "
                    "edges, in the intended first/second boundary order."
                ),
            },
        },
        ("page", "view", "boundary_edges"),
    )


def _bottom_parameters() -> dict:
    return _closed(
        {
            "page": _PAGE,
            "view": _VIEW,
            "circles": {
                "type": "array",
                "items": _EDGE,
                "minItems": 1,
                "maxItems": MAX_DRAWING_THREAD_BOTTOM_TARGETS,
                "description": (
                    "One to 32 unique projected full-circle edges in requested "
                    "result order. Circular arcs are not accepted."
                ),
            },
        },
        ("page", "view", "circles"),
    )


def drawing_thread_representation_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
        description=(
            "Create conventional host-styled cosmetic thread representations "
            "from exact projected Drawing geometry. The host owns factors, arc "
            "span, line weights, color, visibility, and persistent geometry."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_hole_side",
                description=(
                    "Create two thin solid thread boundaries at the host 1.176 "
                    "hole factor plus one graphic-weight solid thread-end line."
                ),
                action_ids=frozenset({"TechDraw_ExtensionThreadHoleSide"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingParallelHoleBoundariesAndSideThreadLines"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_side_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_hole_bottom",
                description=(
                    "Create one persistent 270-degree host-styled thread arc at "
                    "the 1.176 hole factor for each exact projected full circle."
                ),
                action_ids=frozenset({"TechDraw_ExtensionThreadHoleBottom"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=("ExactDrawingFullHoleCirclesAndBottomThreadArcs"),
                transaction_behavior="document",
                background_required=False,
                parameters=_bottom_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_bolt_side",
                description=(
                    "Create two thin solid thread boundaries at the host 0.85 "
                    "bolt factor; this convention has no thread-end line."
                ),
                action_ids=frozenset({"TechDraw_ExtensionThreadBoltSide"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingParallelBoltBoundariesAndSideThreadLines"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_side_parameters(),
            ),
            NativeCapabilityVariant(
                operation="create_bolt_bottom",
                description=(
                    "Create one persistent 270-degree host-styled thread arc at "
                    "the 0.85 bolt factor for each exact projected full circle."
                ),
                action_ids=frozenset({"TechDraw_ExtensionThreadBoltBottom"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=("ExactDrawingFullBoltCirclesAndBottomThreadArcs"),
                transaction_behavior="document",
                background_required=False,
                parameters=_bottom_parameters(),
            ),
        ),
    )


def register_drawing_thread_representation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_thread_representation_capability_definition())
