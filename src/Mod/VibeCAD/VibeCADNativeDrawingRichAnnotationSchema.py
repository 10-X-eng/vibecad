# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing rich-text annotations."""

from __future__ import annotations

from copy import deepcopy

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingRichAnnotationState import (
    MAX_DRAWING_RICH_ANNOTATION_PROVIDER_CONTENT_CHARACTERS,
)


DRAWING_RICH_ANNOTATION_CAPABILITY_NAME = "drawing.rich_annotation"
DRAWING_RICH_ANNOTATION_OPERATIONS = (
    "create_plain_text",
    "create_rich_text",
    "read_defaults",
)
_ACTIONS = frozenset({"TechDraw_RichTextAnnotation"})
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


_PAGE = _closed(
    {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
    ("object_name", "expected_state_sha256"),
)
_OWNER = {
    "oneOf": [
        _closed(
            {"kind": {"type": "string", "const": "page"}},
            ("kind",),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "view"},
                "object_name": _OBJECT_NAME,
                "expected_owner_state_sha256": _SHA256,
            },
            ("kind", "object_name", "expected_owner_state_sha256"),
        ),
    ],
    "description": (
        "Attach to the page itself, or to one exact page view using the "
        "annotation_owner_state_sha256 published in Drawing context."
    ),
}
_PLACEMENT = _closed(
    {
        "x_mm": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
        "y_mm": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
    },
    ("x_mm", "y_mm"),
)
_WIDTH = {
    "oneOf": [
        _closed(
            {"mode": {"type": "string", "const": "automatic"}},
            ("mode",),
        ),
        _closed(
            {
                "mode": {"type": "string", "const": "fixed"},
                "value_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 1_000_000.0,
                },
            },
            ("mode", "value_mm"),
        ),
    ],
    "description": "Automatic wrapping or one positive width in millimetres.",
}
_COLOR = _closed(
    {
        "red": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "green": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "blue": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    ("red", "green", "blue"),
)
_FRAME = _closed(
    {
        "visible": {"type": "boolean"},
        "line_width_mm": {"type": "number", "minimum": 0.0, "maximum": 100.0},
        "line_style": {
            "type": "string",
            "enum": [
                "no_line",
                "continuous",
                "dash",
                "dot",
                "dash_dot",
                "dash_dot_dot",
            ],
        },
        "color_rgb": _COLOR,
    },
    ("visible", "line_width_mm", "line_style", "color_rgb"),
)


def _parameters(*, rich: bool) -> dict:
    content_name = "html" if rich else "text"
    content = {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_DRAWING_RICH_ANNOTATION_PROVIDER_CONTENT_CHARACTERS,
        "description": (
            "Bounded Qt rich-text HTML with visible text. Images, external resources, "
            "active content, event handlers, media, forms, SVG, stylesheets, CSS URLs, "
            "and non-http(s)/mailto links are rejected."
            if rich
            else "Visible UTF-8 text; TechDraw safely escapes and stores canonical HTML."
        ),
    }
    return _closed(
        {
            "page": deepcopy(_PAGE),
            "owner": deepcopy(_OWNER),
            content_name: content,
            "label": {
                "type": "string",
                "minLength": 1,
                "maxLength": 160,
                "description": (
                    "Preferred document label without padding. FreeCAD may append a "
                    "numeric suffix; the result reports the exact assigned label."
                ),
            },
            "placement_on_page_mm": deepcopy(_PLACEMENT),
            "width": deepcopy(_WIDTH),
            "frame": deepcopy(_FRAME),
        },
        (
            "page",
            "owner",
            content_name,
            "label",
            "placement_on_page_mm",
            "width",
            "frame",
        ),
    )


def drawing_rich_annotation_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_RICH_ANNOTATION_CAPABILITY_NAME,
        description=(
            "Create one exact page- or view-owned Drawing annotation with explicit "
            "placement, semantic wrapping, and complete frame style."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_plain_text",
                description="Create a safely escaped plain-text annotation.",
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageOwnerPlainTextPlacementWidthAndFrame"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(rich=False),
            ),
            NativeCapabilityVariant(
                operation="create_rich_text",
                description="Create a bounded resource-free annotation from safe HTML.",
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageOwnerSafeHtmlPlacementWidthAndFrame"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=_parameters(rich=True),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="read_defaults",
                description=(
                    "Read the human command's automatic-width and complete frame defaults."
                ),
                action_ids=_ACTIONS,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="DrawingRichAnnotationDefaults",
                transaction_behavior="none",
                background_required=False,
                parameters=_closed({}, ()),
                provider_supplemental=True,
            ),
        ),
    )


def register_drawing_rich_annotation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_rich_annotation_capability_definition())
