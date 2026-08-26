# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for one exact TechDraw Draft-source view."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingViewState import DRAWING_VIEW_ORIENTATIONS


DRAWING_DRAFT_CAPABILITY_NAME = "drawing.draft_source_view"
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
_STYLE = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "source"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "override"},
                "line_width_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 100.0,
                },
                "font_size_pt": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 10_000.0,
                },
                "color_rgb": _closed(
                    {
                        "red": {"type": "integer", "minimum": 0, "maximum": 255},
                        "green": {"type": "integer", "minimum": 0, "maximum": 255},
                        "blue": {"type": "integer", "minimum": 0, "maximum": 255},
                    },
                    ("red", "green", "blue"),
                ),
                "line_style": {
                    "type": "string",
                    "enum": ["Solid", "Dashed", "Dashdot", "Dot"],
                },
                "line_spacing": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 100.0,
                    "description": "Dimensionless text line-spacing multiplier.",
                },
            },
            (
                "kind",
                "line_width_mm",
                "font_size_pt",
                "color_rgb",
                "line_style",
                "line_spacing",
            ),
        ),
    ]
}


def drawing_draft_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_DRAFT_CAPABILITY_NAME,
        description="Create one Drawing view from one Draft Workbench object.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_draft_source_view",
                description=(
                    "Render one Draft Workbench object on one exact Drawing page."
                ),
                action_ids=frozenset({"TechDraw_DraftView"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageDraftSourceOrientationPlacementScaleAndStyle"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "page": _EXACT_OBJECT,
                        "source": _EXACT_OBJECT,
                        "orientation": {
                            "type": "string",
                            "enum": list(DRAWING_VIEW_ORIENTATIONS),
                        },
                        "position_on_page_mm": _POSITION,
                        "scale": _SCALE,
                        "style": _STYLE,
                    },
                    (
                        "page",
                        "source",
                        "orientation",
                        "position_on_page_mm",
                        "scale",
                        "style",
                    ),
                ),
            ),
        ),
    )


def register_drawing_draft_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_draft_capability_definition())
