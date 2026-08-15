# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for read-only Sketch relationship queries."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema
from VibeCADNativeSketchConstraintSchemaCommon import element_schema


MAX_SKETCH_INSPECT_SELECTION = 32
MAX_SKETCH_INSPECT_CONSTRAINT_SELECTION = 32


def _common_sketch_parameters() -> dict:
    return {
        "sketch": parameters_schema(
            {"object_name": OBJECT_NAME_SCHEMA},
            ("object_name",),
        ),
        "expected_geometry_count": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1_000_000,
        },
        "expected_constraint_count": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1_000_000,
        },
        "expected_external_geometry_count": {
            "type": "integer",
            "minimum": 0,
            "maximum": 1_000_000,
        },
    }


def _select_constraints_parameters() -> dict:
    properties = _common_sketch_parameters()
    properties["selection"] = {
        "type": "array",
        "items": element_schema(),
        "minItems": 1,
        "maxItems": MAX_SKETCH_INSPECT_SELECTION,
        "uniqueItems": True,
    }
    return parameters_schema(
        properties,
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "selection",
        ),
    )


def _select_elements_parameters() -> dict:
    properties = _common_sketch_parameters()
    properties["constraints"] = {
        "type": "array",
        "items": parameters_schema(
            {
                "constraint_index": {
                    "type": "integer",
                    "minimum": 0,
                    "maximum": 999_999,
                },
                "expected_type": {
                    "type": "string",
                    "minLength": 1,
                    "maxLength": 96,
                },
                "expected_name": {
                    "type": "string",
                    "maxLength": 128,
                },
            },
            ("constraint_index", "expected_type", "expected_name"),
        ),
        "minItems": 1,
        "maxItems": MAX_SKETCH_INSPECT_CONSTRAINT_SELECTION,
        "uniqueItems": True,
    }
    return parameters_schema(
        properties,
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "expected_external_geometry_count",
            "constraints",
        ),
    )


def sketch_inspect_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="sketch.inspect",
        description="Read exact relationships in the human-opened Sketch without changing selection.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="select_constraints",
                description="Read constraints associated with exact Sketch elements or points.",
                action_ids=frozenset({"Sketcher_SelectConstraints"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactElementSelection",
                transaction_behavior="none",
                background_required=False,
                parameters=_select_constraints_parameters(),
            ),
            NativeCapabilityVariant(
                operation="select_elements",
                description="Read exact Sketch elements associated with selected constraints.",
                action_ids=frozenset(
                    {"Sketcher_SelectElementsAssociatedWithConstraints"}
                ),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchExactConstraintSelection",
                transaction_behavior="none",
                background_required=False,
                parameters=_select_elements_parameters(),
            ),
        ),
    )


def register_sketch_inspect_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(sketch_inspect_capability_definition())
