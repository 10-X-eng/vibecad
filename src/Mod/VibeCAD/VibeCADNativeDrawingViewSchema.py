# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for deterministic standard and broken Drawing views."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingViewState import (
    DRAWING_VIEW_ORIENTATIONS,
    MAX_DRAWING_BREAKS,
    MAX_DRAWING_VIEW_SOURCES,
)


DRAWING_VIEW_CAPABILITY_NAME = "drawing.view"
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


_EXACT_OBJECT = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_POSITION = _closed(
    {
        "x_mm": {"type": "number", "minimum": -10_000.0, "maximum": 10_000.0},
        "y_mm": {"type": "number", "minimum": -10_000.0, "maximum": 10_000.0},
    },
    ("x_mm", "y_mm"),
)
_SCALE = {
    "oneOf": [
        _closed({"kind": {"type": "string", "const": "page"}}, ("kind",)),
        _closed(
            {
                "kind": {"type": "string", "const": "custom"},
                "value": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 1_000.0,
                },
            },
            ("kind", "value"),
        ),
    ]
}


def drawing_view_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_VIEW_CAPABILITY_NAME,
        description=(
            "Create deterministic standard or broken projections from exact "
            "whole-object shapes on one exact Drawing page."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_standard_view",
                description=(
                    "Create one standard orthographic or isometric part view "
                    "without opening the human projection dialog."
                ),
                action_ids=frozenset({"TechDraw_View"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageSourcesAndProjectionSettings",
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "page": _EXACT_OBJECT,
                        "sources": {
                            "type": "array",
                            "items": _EXACT_OBJECT,
                            "minItems": 1,
                            "maxItems": MAX_DRAWING_VIEW_SOURCES,
                        },
                        "orientation": {
                            "type": "string",
                            "enum": list(DRAWING_VIEW_ORIENTATIONS),
                        },
                        "position": _POSITION,
                        "scale": _SCALE,
                        "line_style": {
                            "type": "string",
                            "enum": [
                                "visible",
                                "visible_and_hidden",
                                "hard_only",
                            ],
                        },
                    },
                    (
                        "label",
                        "page",
                        "sources",
                        "orientation",
                        "position",
                        "scale",
                        "line_style",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_broken_view",
                description=(
                    "Create one real TechDraw broken view from exact shape sources "
                    "and exact single-edge or two-line-sketch break definitions."
                ),
                action_ids=frozenset({"TechDraw_BrokenView"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageSourcesBreakDefinitionsAndProjectionSettings"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "label": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 160,
                        },
                        "page": _EXACT_OBJECT,
                        "sources": {
                            "type": "array",
                            "items": _EXACT_OBJECT,
                            "minItems": 1,
                            "maxItems": MAX_DRAWING_VIEW_SOURCES,
                        },
                        "breaks": {
                            "type": "array",
                            "items": _EXACT_OBJECT,
                            "minItems": 1,
                            "maxItems": MAX_DRAWING_BREAKS,
                        },
                        "gap_mm": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 10_000.0,
                        },
                        "orientation": {
                            "type": "string",
                            "enum": list(DRAWING_VIEW_ORIENTATIONS),
                        },
                        "position": _POSITION,
                        "scale": _SCALE,
                        "line_style": {
                            "type": "string",
                            "enum": [
                                "visible",
                                "visible_and_hidden",
                                "hard_only",
                            ],
                        },
                    },
                    (
                        "label",
                        "page",
                        "sources",
                        "breaks",
                        "gap_mm",
                        "orientation",
                        "position",
                        "scale",
                        "line_style",
                    ),
                ),
            ),
        ),
    )


def register_drawing_view_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_view_capability_definition())
