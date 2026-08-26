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


DRAWING_VIEW_CAPABILITY_NAMES = (
    "drawing.standard_view",
    "drawing.projection_group",
    "drawing.broken_view",
)
DRAWING_PROJECTION_GROUP_VIEWS = (
    "front",
    "top",
    "right",
    "left",
    "bottom",
    "rear",
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
        {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1_000.0,
        },
        _closed(
            {"kind": {"type": "string", "const": "page"}},
            ("kind",),
        ),
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


def drawing_view_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    variants = (
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
                        "line_style",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="create_projection_group",
                description=(
                    "Create one page-fitted orthographic view set at a shared "
                    "standard scale."
                ),
                action_ids=frozenset({"TechDraw_ProjectionGroup"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageSourcesProjectionSetAndConvention"
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
                        "front_orientation": {
                            "type": "string",
                            "enum": list(DRAWING_VIEW_ORIENTATIONS),
                        },
                        "views": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "enum": list(DRAWING_PROJECTION_GROUP_VIEWS),
                            },
                            "minItems": 2,
                            "maxItems": len(DRAWING_PROJECTION_GROUP_VIEWS),
                            "uniqueItems": True,
                        },
                        "convention": {
                            "type": "string",
                            "enum": ["first_angle", "third_angle"],
                        },
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
                        "front_orientation",
                        "views",
                        "convention",
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
                        "line_style",
                    ),
                ),
            ),
    )
    descriptions = (
        "Create one standard projected view from exact whole-object shapes.",
        "Create a coordinated orthographic view set from exact whole-object shapes.",
        "Create one broken view from exact shapes and break definitions.",
    )
    return tuple(
        NativeCapabilityDefinition(
            name=name,
            description=description,
            primary_classification="mutation",
            variants=(variant,),
        )
        for name, description, variant in zip(
            DRAWING_VIEW_CAPABILITY_NAMES,
            descriptions,
            variants,
            strict=True,
        )
    )


def register_drawing_view_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in drawing_view_capability_definitions():
        registry.register_definition(definition)
