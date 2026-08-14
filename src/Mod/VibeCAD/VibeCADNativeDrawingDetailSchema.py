# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for one exact Drawing detail view."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_DETAIL_CAPABILITY_NAME = "drawing.detail_view"
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
_POINT = _closed(
    {
        "x_mm": {"type": "number", "minimum": -1.0e9, "maximum": 1.0e9},
        "y_mm": {"type": "number", "minimum": -1.0e9, "maximum": 1.0e9},
    },
    ("x_mm", "y_mm"),
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
            {"kind": {"type": "string", "const": "automatic"}},
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


def drawing_detail_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_DETAIL_CAPABILITY_NAME,
        description=(
            "Create one magnified detail from an exact projected base view. "
            "The anchor and radius use the base view's local model coordinates; "
            "position uses Drawing page millimetres."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_detail_view",
                description=(
                    "Clip one circular or square preference-driven region and "
                    "place its exact magnified projection on the same page."
                ),
                action_ids=frozenset({"TechDraw_DetailView"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageBaseViewAnchorRadiusPlacementAndScale"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "reference": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 32,
                        },
                        "page": _EXACT_OBJECT,
                        "base_view": _EXACT_OBJECT,
                        "anchor_on_base_mm": _POINT,
                        "radius_mm": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": 1.0e9,
                        },
                        "position_on_page_mm": _POSITION,
                        "scale": _SCALE,
                    },
                    (
                        "reference",
                        "page",
                        "base_view",
                        "anchor_on_base_mm",
                        "radius_mm",
                        "position_on_page_mm",
                        "scale",
                    ),
                ),
            ),
        ),
    )


def register_drawing_detail_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_detail_capability_definition())
