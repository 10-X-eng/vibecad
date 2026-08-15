# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for exact Drawing view stacking."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_STACK_CAPABILITY_NAME = "drawing.stack"
MAX_DRAWING_STACK_TARGETS = 32
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
    {"object_name": _OBJECT_NAME, "expected_state_sha256": _SHA256},
    ("object_name", "expected_state_sha256"),
)
_PARAMETERS = _closed(
    {
        "page": _TARGET,
        "views": {
            "type": "array",
            "items": _TARGET,
            "minItems": 1,
            "maxItems": MAX_DRAWING_STACK_TARGETS,
        },
    },
    ("page", "views"),
)
_EXACT_TARGET = "ExactDrawingPageAndOrderedGraphicalStackViews"


def drawing_stack_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_STACK_CAPABILITY_NAME,
        description=(
            "Move exact Drawing views in their real page or owner graphical "
            "stack. Ordered targets are applied sequentially like the human commands."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="stack_top",
                description=(
                    "Move each exact view to the top of its graphical sibling scope. "
                    "For multiple views, later entries finish above earlier entries."
                ),
                action_ids=frozenset({"TechDraw_StackTop"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=_EXACT_TARGET,
                transaction_behavior="document",
                background_required=False,
                parameters=_PARAMETERS,
            ),
            NativeCapabilityVariant(
                operation="stack_bottom",
                description=(
                    "Move each exact view to the bottom of its graphical sibling scope. "
                    "For multiple views, later entries finish below earlier entries."
                ),
                action_ids=frozenset({"TechDraw_StackBottom"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=_EXACT_TARGET,
                transaction_behavior="document",
                background_required=False,
                parameters=_PARAMETERS,
            ),
            NativeCapabilityVariant(
                operation="stack_up",
                description="Move each exact Drawing view up by one stack level.",
                action_ids=frozenset({"TechDraw_StackUp"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=_EXACT_TARGET,
                transaction_behavior="document",
                background_required=False,
                parameters=_PARAMETERS,
            ),
            NativeCapabilityVariant(
                operation="stack_down",
                description="Move each exact Drawing view down by one stack level.",
                action_ids=frozenset({"TechDraw_StackDown"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type=_EXACT_TARGET,
                transaction_behavior="document",
                background_required=False,
                parameters=_PARAMETERS,
            ),
        ),
    )


def register_drawing_stack_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_stack_capability_definition())
