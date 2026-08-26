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


DRAWING_NOTE_CAPABILITY_NAMES = (
    "drawing.note",
    "drawing.rich_note",
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
    "default": "page",
    "oneOf": [
        {"type": "string", "const": "page"},
        _closed(
            {
                "object_name": _OBJECT_NAME,
                "expected_owner_state_sha256": _SHA256,
            },
            ("object_name", "expected_owner_state_sha256"),
        ),
    ],
}
_PLACEMENT = _closed(
    {
        "x_mm": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
        "y_mm": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
    },
    ("x_mm", "y_mm"),
)
_PLACEMENT["description"] = (
    "Page coordinates in mm; use template_geometry width and height."
)
_WIDTH = {
    "default": "automatic",
    "description": "Width in mm wraps text; automatic keeps one line.",
    "oneOf": [
        {"type": "string", "enum": ["auto", "automatic"]},
        {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "maximum": 1_000_000.0,
        },
    ],
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
    (),
)


def _parameters(*, rich: bool) -> dict:
    content_name = "html" if rich else "text"
    content = {
        "type": "string",
        "minLength": 1,
        "maxLength": MAX_DRAWING_RICH_ANNOTATION_PROVIDER_CONTENT_CHARACTERS,
    }
    return _closed(
        {
            "page": deepcopy(_PAGE),
            content_name: content,
            "label": {
                "type": "string",
                "minLength": 1,
                "maxLength": 160,
            },
            "placement_on_page_mm": deepcopy(_PLACEMENT),
            "owner": deepcopy(_OWNER),
            "width": deepcopy(_WIDTH),
            "frame": deepcopy(_FRAME),
        },
        (
            "page",
            content_name,
            "label",
            "placement_on_page_mm",
        ),
    )


def drawing_rich_annotation_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    definitions = []
    for name, rich, description, target_type in (
        (
            "drawing.note",
            False,
            "Create a plain-text Drawing note.",
            "ExactDrawingPageOwnerPlainTextPlacementWidthAndFrame",
        ),
        (
            "drawing.rich_note",
            True,
            "Create a formatted Drawing note.",
            "ExactDrawingPageOwnerHtmlPlacementWidthAndFrame",
        ),
    ):
        definitions.append(
            NativeCapabilityDefinition(
                name=name,
                description=description,
                primary_classification="mutation",
                variants=(
                    NativeCapabilityVariant(
                        operation="create",
                        description=description,
                        action_ids=_ACTIONS,
                        surface_ids=frozenset({"drawing"}),
                        exact_target_type=target_type,
                        transaction_behavior="document",
                        background_required=False,
                        parameters=_parameters(rich=rich),
                    ),
                ),
            )
        )
    return tuple(definitions)


def register_drawing_rich_annotation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in drawing_rich_annotation_capability_definitions():
        registry.register_definition(definition)
