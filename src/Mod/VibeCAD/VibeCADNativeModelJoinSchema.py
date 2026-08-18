# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for the three retained Part Join actions."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import LABEL_SCHEMA, OBJECT_NAME_SCHEMA, parameters_schema


_MODEL_SURFACE = frozenset({"model"})
_OBJECT = parameters_schema({"object_name": OBJECT_NAME_SCHEMA}, ("object_name",))
_TOLERANCE = {
    "type": "number",
    "minimum": 0.0,
    "maximum": 1_000_000.0,
}


def _connect_definition():
    return parameters_schema(
        {
            "sources": {
                "type": "array",
                "items": _OBJECT,
                "minItems": 1,
                "maxItems": 32,
                "uniqueItems": True,
            },
            "refine": {"type": "boolean"},
            "tolerance_mm": _TOLERANCE,
        },
        ("sources", "refine", "tolerance_mm"),
    )


def _ordered_pair_definition():
    return parameters_schema(
        {
            "base": _OBJECT,
            "tool": _OBJECT,
            "refine": {"type": "boolean"},
            "tolerance_mm": _TOLERANCE,
        },
        ("base", "tool", "refine", "tolerance_mm"),
    )


def model_join_capability_definition() -> NativeCapabilityDefinition:
    pair = _ordered_pair_definition()
    return NativeCapabilityDefinition(
        name="model.join",
        description="Connect, embed, or cut out Part shapes.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="connect",
                description=(
                    "Connect ordered current shapes while preserving voids; one source "
                    "is allowed only when it is a multi-child Compound."
                ),
                action_ids=frozenset({"Part_JoinConnect"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="OrderedExactCurrentWholeShapes",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _connect_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="embed",
                description="Embed one exact current tool shape into an ordered base shape.",
                action_ids=frozenset({"Part_JoinEmbed"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="OrderedExactCurrentBaseAndTool",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": pair},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="cutout",
                description="Cut an exact fitting recess for an ordered tool in a base shape.",
                action_ids=frozenset({"Part_JoinCutout"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="OrderedExactCurrentBaseAndTool",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": pair},
                    ("label", "definition"),
                ),
            ),
        ),
    )


def register_model_join_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_join_capability_definition())
