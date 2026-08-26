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


DRAWING_LEADER_CAPABILITY_NAME = "drawing.leader_line"
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
    (),
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
    (),
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
    (),
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
                "description": "Paper-space points from arrow tip to tail.",
            },
            "label": {
                "type": "string",
                "minLength": 1,
                "maxLength": 160,
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
        ),
    )


def drawing_leader_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_LEADER_CAPABILITY_NAME,
        description="Add a leader line to a Drawing view.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description="Add a leader line to a Drawing view.",
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageOwnerPointsSymbolsBehaviorAndLineStyle"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_create_parameters(),
            ),
        ),
    )


def register_drawing_leader_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_leader_capability_definition())
