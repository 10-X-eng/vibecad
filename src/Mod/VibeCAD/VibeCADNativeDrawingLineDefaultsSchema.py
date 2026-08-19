# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for current TechDraw line and placement defaults."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


DRAWING_LINE_DEFAULTS_CAPABILITY_NAME = "drawing.line_defaults"
DRAWING_LINE_DEFAULTS_OPERATIONS = ("read_current",)


def drawing_line_defaults_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=DRAWING_LINE_DEFAULTS_CAPABILITY_NAME,
        description="Read current line styles and dimension placement defaults.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="read_current",
                description=(
                    "Read the active line standard, selected style, exact width and "
                    "color, width choices, cascade spacing, and delta distance."
                ),
                action_ids=frozenset({"TechDraw_ExtensionSelectLineAttributes"}),
                surface_ids=frozenset({"drawing"}),
                exact_target_type="CurrentTechDrawLineAndPlacementDefaults",
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {},
                    "required": [],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_drawing_line_defaults_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(drawing_line_defaults_capability_definition())
