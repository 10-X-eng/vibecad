# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for explicit Drawing item placement."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_PLACEMENT_CAPABILITY_NAMES = (
    "drawing.place_views",
    "drawing.place_dimension_labels",
    "drawing.place_notes",
)
MAX_DRAWING_PLACEMENT_ITEMS = 64
_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_PLACEMENT_OBJECT_NAME = {
    **_OBJECT_NAME,
    "description": "Top-level view or projected child object_name from page state.",
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
_POSITION = _closed(
    {
        "x_mm": {"type": "number", "minimum": -10_000.0, "maximum": 10_000.0},
        "y_mm": {"type": "number", "minimum": -10_000.0, "maximum": 10_000.0},
    },
    ("x_mm", "y_mm"),
)
_VIEW = _closed(
    {
        "object_name": _PLACEMENT_OBJECT_NAME,
        "expected_placement_state_sha256": _SHA256,
        "position_on_page_mm": _POSITION,
    },
    ("object_name", "expected_placement_state_sha256", "position_on_page_mm"),
)
_DIMENSION = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_placement_state_sha256": _SHA256,
        "label_position_on_page_mm": _POSITION,
    },
    (
        "object_name",
        "expected_placement_state_sha256",
        "label_position_on_page_mm",
    ),
)
_NOTE = _closed(
    {
        "object_name": {
            **_OBJECT_NAME,
            "description": "page.views[] item with rich_annotation state",
        },
        "expected_placement_state_sha256": _SHA256,
        "position_on_page_mm": _POSITION,
    },
    ("object_name", "expected_placement_state_sha256", "position_on_page_mm"),
)


def drawing_placement_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    variants = (
        NativeCapabilityVariant(
            operation="place_views",
            description=(
                "Place Drawing views. Projected child positions translate their "
                "projection group."
            ),
            action_ids=frozenset({"VibeCAD_DrawingPlaceViews"}),
            surface_ids=frozenset({"drawing"}),
            exact_target_type="ExactDrawingPageAndViewPositions",
            transaction_behavior="document",
            background_required=False,
            parameters=_closed(
                {
                    "page": _PAGE,
                    "views": {
                        "type": "array",
                        "items": _VIEW,
                        "minItems": 1,
                        "maxItems": MAX_DRAWING_PLACEMENT_ITEMS,
                    },
                },
                ("page", "views"),
            ),
        ),
        NativeCapabilityVariant(
            operation="place_dimension_labels",
            description="Move existing Drawing dimension labels to page coordinates.",
            action_ids=frozenset({"VibeCAD_DrawingPlaceDimensionLabels"}),
            surface_ids=frozenset({"drawing"}),
            exact_target_type="ExactDrawingPageAndDimensionLabelPositions",
            transaction_behavior="document",
            background_required=False,
            parameters=_closed(
                {
                    "page": _PAGE,
                    "dimensions": {
                        "type": "array",
                        "items": _DIMENSION,
                        "minItems": 1,
                        "maxItems": MAX_DRAWING_PLACEMENT_ITEMS,
                    },
                },
                ("page", "dimensions"),
            ),
        ),
        NativeCapabilityVariant(
            operation="place_notes",
            description="Move existing Drawing notes to page coordinates.",
            action_ids=frozenset({"VibeCAD_DrawingPlaceNotes"}),
            surface_ids=frozenset({"drawing"}),
            exact_target_type="ExactDrawingPageAndNotePositions",
            transaction_behavior="document",
            background_required=False,
            parameters=_closed(
                {
                    "page": _PAGE,
                    "notes": {
                        "type": "array",
                        "items": _NOTE,
                        "minItems": 1,
                        "maxItems": MAX_DRAWING_PLACEMENT_ITEMS,
                    },
                },
                ("page", "notes"),
            ),
        ),
    )
    return tuple(
        NativeCapabilityDefinition(
            name=name,
            description=variant.description,
            primary_classification="mutation",
            variants=(variant,),
        )
        for name, variant in zip(
            DRAWING_PLACEMENT_CAPABILITY_NAMES,
            variants,
            strict=True,
        )
    )


def register_drawing_placement_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in drawing_placement_capability_definitions():
        registry.register_shared_definition(definition)


__all__ = [
    "DRAWING_PLACEMENT_CAPABILITY_NAMES",
    "MAX_DRAWING_PLACEMENT_ITEMS",
    "drawing_placement_capability_definitions",
    "register_drawing_placement_capability_definitions",
]
