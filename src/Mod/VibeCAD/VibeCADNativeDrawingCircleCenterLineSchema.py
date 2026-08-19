# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for Drawing circle centerlines."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingCircleCenterLineState import (
    MAX_DRAWING_CIRCLE_CENTER_LINE_TARGETS,
)


DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME = "drawing.circle_center_lines"
DRAWING_CIRCLE_CENTER_LINE_OPERATIONS = ("create",)
_ACTION = frozenset({"TechDraw_ExtensionCircleCenterLines"})
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
_EDGE = {
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
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
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
_CIRCLE = _closed(
    {
        "subelement": {
            **_EDGE,
            "description": (
                "Exact selected projected circle or circular-arc EdgeN."
            ),
        },
        "expected_element_state_sha256": _SHA256,
    },
    ("subelement", "expected_element_state_sha256"),
)


def drawing_circle_center_line_capability_definition() -> (
    NativeCapabilityDefinition
):
    return NativeCapabilityDefinition(
        name=DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
        description=(
            "Add a horizontal and vertical persistent centerline across each "
            "exact selected projected circle or circular arc."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description="Create persistent center lines for projected circular edges.",
                action_ids=_ACTION,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingCircularEdgesAndPersistentCrossCenterlines"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "view": _VIEW,
                        "circles": {
                            "type": "array",
                            "items": _CIRCLE,
                            "minItems": 1,
                            "maxItems": (
                                MAX_DRAWING_CIRCLE_CENTER_LINE_TARGETS
                            ),
                            "description": (
                                "One to 32 unique selected projected circles "
                                "or circular arcs, in requested result order."
                            ),
                        },
                    },
                    ("page", "view", "circles"),
                ),
            ),
        ),
    )


def register_drawing_circle_center_line_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(
        drawing_circle_center_line_capability_definition()
    )
