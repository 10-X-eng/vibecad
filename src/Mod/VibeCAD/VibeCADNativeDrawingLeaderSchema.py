# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing Leader Lines."""

from __future__ import annotations

from copy import deepcopy

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingLeaderState import MAX_DRAWING_LEADER_POINTS


DRAWING_ANNOTATION_CAPABILITY_NAME = "drawing.annotation"
DRAWING_LEADER_OPERATIONS = ("leader_line", "read_leader_defaults")
_ACTIONS = frozenset({"TechDraw_LeaderLine"})
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
_ARROWS = [
    "filled_arrow",
    "open_arrow",
    "tick",
    "dot",
    "open_circle",
    "fork",
    "filled_triangle",
    "none",
]
_LINE_STYLES = [
    "no_line",
    "continuous",
    "dash",
    "dot",
    "dash_dot",
    "dash_dot_dot",
]


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
_OWNER = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_owner_state_sha256": _SHA256,
    },
    ("object_name", "expected_owner_state_sha256"),
)
_POINT = _closed(
    {
        "x_mm": {"type": "number", "minimum": 0.0, "maximum": 1_000_000.0},
        "y_mm": {"type": "number", "minimum": 0.0, "maximum": 1_000_000.0},
    },
    ("x_mm", "y_mm"),
)
_SYMBOLS = _closed(
    {
        "start": {"type": "string", "enum": _ARROWS},
        "end": {"type": "string", "enum": _ARROWS},
    },
    ("start", "end"),
)
_BEHAVIOR = _closed(
    {
        "scalable": {
            "type": "boolean",
            "description": "Scale segment lengths when the owner view scale changes.",
        },
        "auto_horizontal": {
            "type": "boolean",
            "description": (
                "Force the rendered final segment horizontal while retaining the "
                "requested final-segment length."
            ),
        },
        "rotates_with_owner": {
            "type": "boolean",
            "description": "Rotate all leader segments when the owner view rotates.",
        },
    },
    ("scalable", "auto_horizontal", "rotates_with_owner"),
)
_COLOR = _closed(
    {
        "red": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "green": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "blue": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    ("red", "green", "blue"),
)
_LINE = _closed(
    {
        "line_width_mm": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 100.0,
        },
        "line_style": {"type": "string", "enum": _LINE_STYLES},
        "color_rgb": _COLOR,
    },
    ("line_width_mm", "line_style", "color_rgb"),
)


def _create_parameters() -> dict:
    return _closed(
        {
            "page": deepcopy(_PAGE),
            "owner": deepcopy(_OWNER),
            "points_on_page_mm": {
                "type": "array",
                "minItems": 2,
                "maxItems": MAX_DRAWING_LEADER_POINTS,
                "items": deepcopy(_POINT),
                "description": "Ordered distinct paper-space points from arrow tip to tail.",
            },
            "label": {
                "type": "string",
                "minLength": 1,
                "maxLength": 160,
                "description": (
                    "Preferred document label without padding. FreeCAD may append a "
                    "numeric suffix; the result reports the exact assigned label."
                ),
            },
            "symbols": deepcopy(_SYMBOLS),
            "behavior": deepcopy(_BEHAVIOR),
            "line": deepcopy(_LINE),
        },
        (
            "page",
            "owner",
            "points_on_page_mm",
            "label",
            "symbols",
            "behavior",
            "line",
        ),
    )


def drawing_leader_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_ANNOTATION_CAPABILITY_NAME,
        description=(
            "Create one exact owner-linked Drawing Leader Line from ordered absolute "
            "page points with explicit symbols, behavior, and complete line style."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="leader_line",
                description=(
                    "Create a Leader Line on one exact page and owner view. The result "
                    "reports the actual rendered points after scale, rotation, and "
                    "automatic-horizontal behavior."
                ),
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageOwnerPointsSymbolsBehaviorAndLineStyle"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_create_parameters(),
            ),
            NativeCapabilityVariant(
                operation="read_leader_defaults",
                description=(
                    "Read the human Leader Line command's symbols, behavior, color, "
                    "width, and line-style defaults."
                ),
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="DrawingLeaderDefaults",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed({}, ()),
                provider_supplemental=True,
            ),
        ),
    )


def register_drawing_leader_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_leader_capability_definition())
