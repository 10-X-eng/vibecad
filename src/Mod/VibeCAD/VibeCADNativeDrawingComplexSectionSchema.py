# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for one profile-driven Drawing section."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_COMPLEX_SECTION_CAPABILITY_NAME = "drawing.complex_section"
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
_VIEW_DIRECTION = _closed(
    {
        "x": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "y": {"type": "number", "minimum": -1.0, "maximum": 1.0},
    },
    ("x", "y"),
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


def drawing_complex_section_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_COMPLEX_SECTION_CAPABILITY_NAME,
        description=(
            "Create one profile-driven section from an exact projected base "
            "view and an exact whole wire or edge. The look direction is "
            "expressed in the base view's coordinates."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_complex_section_view",
                description=(
                    "Create an offset, aligned, or no-parallel complex section "
                    "using the complete selected profile object."
                ),
                action_ids=frozenset({"TechDraw_ComplexSection"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=(
                    "ExactDrawingPageBaseViewWholeProfileStrategyAndScale"
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
                        "symbol": {
                            "type": "string",
                            "minLength": 1,
                            "maxLength": 32,
                        },
                        "page": _EXACT_OBJECT,
                        "base_view": _EXACT_OBJECT,
                        "profile": _EXACT_OBJECT,
                        "view_direction_on_base": _VIEW_DIRECTION,
                        "projection_strategy": {
                            "type": "string",
                            "enum": ["offset", "aligned", "no_parallel"],
                        },
                        "scale": _SCALE,
                    },
                    (
                        "label",
                        "symbol",
                        "page",
                        "base_view",
                        "profile",
                        "view_direction_on_base",
                        "projection_strategy",
                        "scale",
                    ),
                ),
            ),
        ),
    )


def register_drawing_complex_section_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_complex_section_capability_definition())
