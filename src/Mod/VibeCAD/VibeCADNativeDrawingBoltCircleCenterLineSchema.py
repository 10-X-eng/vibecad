# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for Drawing bolt-circle centerlines."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingBoltCircleCenterLineState import (
    MAX_DRAWING_BOLT_CIRCLE_TARGETS,
    MIN_DRAWING_BOLT_CIRCLE_TARGETS,
)


DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME = (
    "drawing.bolt_circle_center_lines"
)
DRAWING_BOLT_CIRCLE_CENTER_LINE_OPERATIONS = ("create",)
_ACTION = frozenset({"TechDraw_ExtensionHoleCircle"})
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
_HOLE = _closed(
    {
        "subelement": {
            **_EDGE,
            "description": "Exact selected projected hole circle or circular-arc EdgeN.",
        },
        "expected_element_state_sha256": _SHA256,
    },
    ("subelement", "expected_element_state_sha256"),
)


def drawing_bolt_circle_center_line_capability_definition() -> (
    NativeCapabilityDefinition
):
    return NativeCapabilityDefinition(
        name=DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
        description=(
            "Create one persistent bolt-pattern circle and one radial center mark "
            "through each of three or more exact projected circular holes."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description=(
                    "Derive the pattern from the first three ordered hash-pinned "
                    "hole centers, then add one host-styled radial mark per hole. "
                    "Report whether all centers lie on the pattern without "
                    "rejecting extra off-pattern holes the human command accepts."
                ),
                action_ids=_ACTION,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactOrderedDrawingHoleCirclesAndDerivedBoltCircle"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _PAGE,
                        "view": _VIEW,
                        "holes": {
                            "type": "array",
                            "items": _HOLE,
                            "minItems": MIN_DRAWING_BOLT_CIRCLE_TARGETS,
                            "maxItems": MAX_DRAWING_BOLT_CIRCLE_TARGETS,
                            "description": (
                                "Three to 32 unique projected circular holes in "
                                "requested result order. The first three centers "
                                "define the pattern circle."
                            ),
                        },
                    },
                    ("page", "view", "holes"),
                ),
            ),
        ),
    )


def register_drawing_bolt_circle_center_line_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(
        drawing_bolt_circle_center_line_capability_definition()
    )
