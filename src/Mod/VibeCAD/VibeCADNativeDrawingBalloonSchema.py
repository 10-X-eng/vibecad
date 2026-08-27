# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact projected Drawing balloons."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_BALLOON_CAPABILITY_NAME = "drawing.balloon"
DRAWING_BALLOON_OPERATIONS = (
    "create",
    "set_text",
    "set_style",
    "move_bubble",
)
DRAWING_BALLOON_SHAPES = (
    "Circular",
    "None",
    "Triangle",
    "Inspection",
    "Hexagon",
    "Square",
    "Rectangle",
    "Line",
)
DRAWING_BALLOON_LEADER_ENDS = (
    "Filled arrow",
    "Open arrow",
    "Tick",
    "Dot",
    "Open circle",
    "Fork",
    "Filled triangle",
    "None",
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


def drawing_balloon_capability_definition() -> NativeCapabilityDefinition:
    balloon_target = _closed(
        {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
        ("object_name", "expected_state_sha256"),
    )
    text = {
        "type": "string",
        "minLength": 1,
        "maxLength": 512,
        "description": "Visible balloon text.",
    }
    bubble_offset = {
        **_closed(
            {
                "x_mm": {
                    "type": "number",
                    "minimum": -1000.0,
                    "maximum": 1000.0,
                },
                "y_mm": {
                    "type": "number",
                    "minimum": -1000.0,
                    "maximum": 1000.0,
                },
            },
            ("x_mm", "y_mm"),
        ),
        "description": (
            "Bubble displacement from the anchor in scaled view coordinates: "
            "+X right and +Y up, in millimetres."
        ),
    }
    create_parameters = {
        "label": {
            "type": "string",
            "minLength": 1,
            "maxLength": 160,
            "description": (
                "Preferred document label. The result reports FreeCAD's exact assigned label."
            ),
        },
        "text": {
            **text,
            "description": (
                "Visible balloon text; explicit text does not consume auto-numbering."
            ),
        },
        "page": _closed(
            {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
            ("object_name", "expected_state_sha256"),
        ),
        "view": _closed(
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
        ),
        "anchor": _closed(
            {
                "subelement": {
                    "type": "string",
                    "pattern": r"^(?:Edge|Vertex)(?:0|[1-9][0-9]*)$",
                    "maxLength": 32,
                },
            },
            ("subelement",),
        ),
        "bubble_offset_in_view_mm": bubble_offset,
    }
    style = _closed(
        {
            "bubble_shape": {"type": "string", "enum": list(DRAWING_BALLOON_SHAPES)},
            "leader_end": {
                "type": "string",
                "enum": list(DRAWING_BALLOON_LEADER_ENDS),
            },
            "bubble_scale": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1000.0,
            },
            "leader_end_scale": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 1000.0,
            },
            "kink_length_mm": {
                "type": "number",
                "minimum": -1000.0,
                "maximum": 1000.0,
            },
            "font_size_mm": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 1000.0,
            },
            "line_width_mm": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 100.0,
            },
            "line_visible": {"type": "boolean"},
            "color_rgb": _closed(
                {
                    "red": {"type": "integer", "minimum": 0, "maximum": 255},
                    "green": {"type": "integer", "minimum": 0, "maximum": 255},
                    "blue": {"type": "integer", "minimum": 0, "maximum": 255},
                },
                ("red", "green", "blue"),
            ),
        },
        (
            "bubble_shape",
            "leader_end",
            "bubble_scale",
            "leader_end_scale",
            "kink_length_mm",
            "font_size_mm",
            "line_width_mm",
            "line_visible",
            "color_rgb",
        ),
    )
    action_ids = frozenset({"TechDraw_Balloon"})
    return NativeCapabilityDefinition(
        name=DRAWING_BALLOON_CAPABILITY_NAME,
        description=(
            "Create one Balloon annotation at the midpoint of an exact projected edge "
            "or at an exact projected vertex."
        ),
        primary_classification="mutation",
        preserve_operation_branches=True,
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description=(
                    "Create one exact projected Balloon with explicit text and placement."
                ),
                action_ids=action_ids,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingProjectedBalloonAnchorAndPlacement",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    dict(create_parameters),
                    tuple(create_parameters),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_text",
                description="Replace the text of one exact existing Balloon.",
                action_ids=action_ids,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingBalloonAndReplacementText",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {"balloon": balloon_target, "text": text},
                    ("balloon", "text"),
                ),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="set_style",
                description=(
                    "Replace all human-editable style fields of one exact existing Balloon."
                ),
                action_ids=action_ids,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingBalloonAndCompleteEditableStyle",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {"balloon": balloon_target, "style": style},
                    ("balloon", "style"),
                ),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="move_bubble",
                description=(
                    "Move one exact existing Balloon bubble relative to its persisted anchor."
                ),
                action_ids=action_ids,
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingBalloonAndViewSpaceBubbleOffset",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "balloon": balloon_target,
                        "bubble_offset_in_view_mm": bubble_offset,
                    },
                    ("balloon", "bubble_offset_in_view_mm"),
                ),
                provider_supplemental=True,
            ),
        ),
    )


def register_drawing_balloon_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_balloon_capability_definition())
