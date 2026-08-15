# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp provider contract for retained background CAM simulation."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME = "manufacture.simulation_result"
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


_EXACT_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)


def manufacture_simulation_result_capability_definition() -> (
    NativeCapabilityDefinition
):
    return NativeCapabilityDefinition(
        name=MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
        description=(
            "Create one durable, undoable material-removal Mesh from an exact CAM "
            "Job and ordered active operations. Native cutting and tessellation run "
            "in the background without blocking the UI."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="native",
                description=(
                    "Simulate an explicit ordered subset of one exact Job using the "
                    "human CAM simulator's quality scale, then retain one source-preserving "
                    "CutMaterial result at the current History marker."
                ),
                action_ids=frozenset({"CAM_Simulator"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type=(
                    "ExactCamJobOrderedActiveOperationsAndNativeQuality"
                ),
                transaction_behavior="background",
                background_required=True,
                parameters=_closed(
                    {
                        "job": _EXACT_TARGET,
                        "operations": {
                            "type": "array",
                            "items": _EXACT_TARGET,
                            "minItems": 1,
                            "maxItems": 64,
                            "uniqueItems": True,
                            "description": (
                                "Distinct exact operations in their current Job order; "
                                "every operation must be active, generated, and usable at "
                                "the current History position."
                            ),
                        },
                        "quality": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 10,
                            "description": (
                                "Retained simulation quality from 1 (low) through 10 "
                                "(high), matching the human simulator control."
                            ),
                        },
                    },
                    ("job", "operations", "quality"),
                ),
            ),
        ),
    )


def register_manufacture_simulation_result_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_simulation_result_capability_definition())
