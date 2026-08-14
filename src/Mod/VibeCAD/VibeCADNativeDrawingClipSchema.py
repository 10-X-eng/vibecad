# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing clip groups."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDrawingClipState import MAX_DRAWING_CLIP_MEMBERS


DRAWING_CLIP_CAPABILITY_NAME = "drawing.clip_group"
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


_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_POSITION = _closed(
    {
        "x_mm": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
        "y_mm": {"type": "number", "minimum": -1_000_000, "maximum": 1_000_000},
    },
    ("x_mm", "y_mm"),
)
_FRAME = _closed(
    {
        "width_mm": {"type": "number", "exclusiveMinimum": 0, "maximum": 1_000_000},
        "height_mm": {"type": "number", "exclusiveMinimum": 0, "maximum": 1_000_000},
        "show_frame": {"type": "boolean"},
        "clip_children": {"type": "boolean"},
    },
    ("width_mm", "height_mm", "show_frame", "clip_children"),
)
_LABEL = {"type": "string", "minLength": 1, "maxLength": 128}
_MEMBER_IN = _closed(
    {
        "view": _TARGET,
        "position_in_clip_mm": _POSITION,
    },
    ("view", "position_in_clip_mm"),
)
_MEMBER_OUT = _closed(
    {
        "view": _TARGET,
        "position_on_page_mm": _POSITION,
    },
    ("view", "position_on_page_mm"),
)
_MEMBERS_IN = {
    "type": "array",
    "items": _MEMBER_IN,
    "minItems": 1,
    "maxItems": MAX_DRAWING_CLIP_MEMBERS,
}
_MEMBERS_OUT = {
    "type": "array",
    "items": _MEMBER_OUT,
    "minItems": 1,
    "maxItems": MAX_DRAWING_CLIP_MEMBERS,
}


def drawing_clip_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_CLIP_CAPABILITY_NAME,
        description=(
            "Create, populate, ungroup, or configure one exact Drawing clip "
            "group with explicit page-relative and clip-local placement."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_clip_group",
                description=(
                    "Create one nonempty clip group on an exact page and place "
                    "each exact existing view at an explicit clip-local position."
                ),
                action_ids=frozenset({"TechDraw_ClipGroup"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageClipFrameMembersAndPlacements",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _TARGET,
                        "label": _LABEL,
                        "position_on_page_mm": _POSITION,
                        "frame": _FRAME,
                        "members": _MEMBERS_IN,
                    },
                    ("page", "label", "position_on_page_mm", "frame", "members"),
                ),
            ),
            NativeCapabilityVariant(
                operation="add_views",
                description=(
                    "Append exact same-page views to one exact clip group with "
                    "explicit clip-local positions."
                ),
                action_ids=frozenset({"TechDraw_ClipGroupAdd"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageClipGroupAndUngroupedViews",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {"page": _TARGET, "clip_group": _TARGET, "members": _MEMBERS_IN},
                    ("page", "clip_group", "members"),
                ),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="remove_views",
                description=(
                    "Remove exact members from one exact clip group and place "
                    "each view explicitly on the page."
                ),
                action_ids=frozenset({"TechDraw_ClipGroupRemove"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageClipGroupMembersAndExitPlacements",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {"page": _TARGET, "clip_group": _TARGET, "members": _MEMBERS_OUT},
                    ("page", "clip_group", "members"),
                ),
                provider_supplemental=True,
            ),
            NativeCapabilityVariant(
                operation="configure_clip_group",
                description=(
                    "Set the complete label, page position, frame, and child "
                    "clipping state of one exact clip group."
                ),
                action_ids=frozenset({"VibeCAD_DrawingConfigureClipGroup"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="ExactDrawingPageClipGroupAndCompleteFrameState",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "page": _TARGET,
                        "clip_group": _TARGET,
                        "label": _LABEL,
                        "position_on_page_mm": _POSITION,
                        "frame": _FRAME,
                    },
                    ("page", "clip_group", "label", "position_on_page_mm", "frame"),
                ),
                provider_supplemental=True,
            ),
        ),
    )


def register_drawing_clip_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_clip_capability_definition())
