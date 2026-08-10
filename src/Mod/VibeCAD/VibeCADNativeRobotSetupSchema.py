# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for Robot setup on the Assemble ribbon."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import LABEL_SCHEMA, parameters_schema


ROBOT_SETUP_CAPABILITY_NAME = "robot.setup"
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}


def robot_setup_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ROBOT_SETUP_CAPABILITY_NAME,
        description=(
            "Create and configure exact Robot objects in the active document; "
            "definition-file paths remain under human control."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description=(
                    "Create one durable six-axis Robot from VRML and kinematic "
                    "CSV files selected in host-owned human dialogs."
                ),
                action_ids=frozenset({"Robot_Create"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type=(
                    "ActiveDocumentAndHumanAuthorizedRobotDefinitionFiles"
                ),
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "expected_state_sha256": _STATE_SHA256,
                        "expected_robot_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 128,
                        },
                    },
                    (
                        "label",
                        "expected_state_sha256",
                        "expected_robot_count",
                    ),
                ),
            ),
        ),
    )


def register_robot_setup_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(robot_setup_capability_definition())
