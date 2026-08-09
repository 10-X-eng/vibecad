# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for standalone Model boolean operations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import LABEL_SCHEMA, OBJECT_NAME_SCHEMA, parameters_schema


def _section_definition():
    operand = parameters_schema(
        {"object_name": OBJECT_NAME_SCHEMA},
        ("object_name",),
    )
    return parameters_schema(
        {
            "operands": {
                "type": "array",
                "items": operand,
                "minItems": 2,
                "maxItems": 2,
                "uniqueItems": True,
            }
        },
        ("operands",),
    )


def _combine_definition():
    body = parameters_schema(
        {"object_name": OBJECT_NAME_SCHEMA},
        ("object_name",),
    )
    return parameters_schema(
        {
            "mode": {
                "type": "string",
                "enum": ["join", "cut", "intersect"],
            },
            "result_body": body,
            "tool_bodies": {
                "type": "array",
                "items": body,
                "minItems": 1,
                "maxItems": 15,
                "uniqueItems": True,
            },
            "keep_tools": {"type": "boolean"},
        },
        ("mode", "result_body", "tool_bodies", "keep_tools"),
    )


def _split_definition():
    splitter = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "subelements": {
                "type": "array",
                "items": {
                    "type": "string",
                    "maxLength": 64,
                    "pattern": r"^(?:Face|Shell|Solid)[1-9][0-9]*$",
                },
                "minItems": 0,
                "maxItems": 64,
                "uniqueItems": True,
            },
        },
        ("object_name", "subelements"),
    )
    return parameters_schema(
        {
            "source_body": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "splitters": {
                "type": "array",
                "items": splitter,
                "minItems": 1,
                "maxItems": 32,
                "uniqueItems": True,
            },
            "retained_region_index": {
                "type": "integer",
                "minimum": 0,
                "maximum": 255,
            },
        },
        ("source_body", "splitters", "retained_region_index"),
    )
def model_boolean_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="model.boolean",
        description="Create one exact Boolean result.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="section",
                description="Section two ordered current shapes.",
                action_ids=frozenset({"Part_Section"}),
                surface_ids=frozenset({"model"}),
                exact_target_type="TwoOrderedExactCurrentShapes",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _section_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="combine",
                description="Combine explicit Design Bodies.",
                action_ids=frozenset({"PartDesign_Combine"}),
                surface_ids=frozenset({"model"}),
                exact_target_type="ResultBodyAndOrderedToolBodies",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _combine_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="split",
                description=(
                    "Split one exact source Body with ordered whole shapes or exact "
                    "face, shell, and solid subelements, then choose the zero-based "
                    "X/Y/Z-sorted region which retains the source Body identity."
                ),
                action_ids=frozenset({"PartDesign_Split"}),
                surface_ids=frozenset({"model"}),
                exact_target_type="SourceBodySplittersAndRetainedRegion",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _split_definition()},
                    ("label", "definition"),
                ),
            ),
        ),
    )


def register_model_boolean_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_boolean_capability_definition())
