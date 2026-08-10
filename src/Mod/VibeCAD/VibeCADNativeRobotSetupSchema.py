# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for Robot setup on the Assemble ribbon."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    object_reference_schema,
    parameters_schema,
    placement_schema,
)
from VibeCADNativeRobotDefaultsState import MAX_ROBOT_MOTION_VALUE


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
            "Create and configure exact Robot objects and waypoint defaults; "
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
            NativeCapabilityVariant(
                operation="add_tool_shape",
                description=(
                    "Attach one exact Part feature or VRML object to one exact "
                    "Robot without changing either object identity."
                ),
                action_ids=frozenset({"Robot_AddToolShape"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentRobotAndToolShape",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "robot": object_reference_schema(),
                        "tool_shape": object_reference_schema(),
                        "expected_setup_state_sha256": _STATE_SHA256,
                        "expected_robot_state_sha256": _STATE_SHA256,
                        "expected_tool_shape_state_sha256": _STATE_SHA256,
                    },
                    (
                        "robot",
                        "tool_shape",
                        "expected_setup_state_sha256",
                        "expected_robot_state_sha256",
                        "expected_tool_shape_state_sha256",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_default_orientation",
                description=(
                    "Set the exact orientation and displacement used by later "
                    "waypoint creation in this application session."
                ),
                action_ids=frozenset({"Robot_SetDefaultOrientation"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="RobotWaypointSessionOrientation",
                transaction_behavior="session",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "expected_defaults_state_sha256": _STATE_SHA256,
                        "placement": placement_schema(),
                    },
                    ("expected_defaults_state_sha256", "placement"),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_default_values",
                description=(
                    "Set exact speed, acceleration, and continuity defaults for "
                    "later waypoint creation in this application session."
                ),
                action_ids=frozenset({"Robot_SetDefaultValues"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="RobotWaypointSessionMotionDefaults",
                transaction_behavior="session",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "expected_defaults_state_sha256": _STATE_SHA256,
                        "speed_mm_per_s": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": MAX_ROBOT_MOTION_VALUE,
                        },
                        "continuous": {"type": "boolean"},
                        "acceleration_mm_per_s2": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": MAX_ROBOT_MOTION_VALUE,
                        },
                    },
                    (
                        "expected_defaults_state_sha256",
                        "speed_mm_per_s",
                        "continuous",
                        "acceleration_mm_per_s2",
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
