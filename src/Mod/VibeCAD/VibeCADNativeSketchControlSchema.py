# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for leaving the exact active Sketch."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import OBJECT_NAME_SCHEMA, parameters_schema


def _leave_parameters() -> dict:
    return parameters_schema(
        {
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
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
        ),
    )


def sketch_control_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="sketch.control",
        description="Finish the exact human-opened Sketch edit session.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="leave",
                description=(
                    "Finish the exact active Sketch and require a new AI turn "
                    "for the resulting ribbon surface."
                ),
                action_ids=frozenset({"Sketcher_LeaveSketch"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchEditSession",
                transaction_behavior="edit_control",
                background_required=False,
                parameters=_leave_parameters(),
            ),
        ),
    )


def register_sketch_control_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(sketch_control_capability_definition())
