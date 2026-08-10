# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for Robot trajectories on the Assemble ribbon."""

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
    vector_schema,
)
from VibeCADNativeRobotTrajectory import MAX_WAYPOINT_COORDINATE_MM
from VibeCADNativeRobotTrajectoryState import MAX_TRAJECTORIES


ROBOT_TRAJECTORY_CAPABILITY_NAME = "robot.trajectory"
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}


def robot_trajectory_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ROBOT_TRAJECTORY_CAPABILITY_NAME,
        description=(
            "Create exact Robot trajectories and append LIN waypoints from an "
            "exact Robot pose or explicit world point."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_trajectory",
                description="Create one durable empty Robot trajectory.",
                action_ids=frozenset({"Robot_CreateTrajectory"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentTrajectoryState",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "expected_state_sha256": _STATE_SHA256,
                        "expected_trajectory_count": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": MAX_TRAJECTORIES,
                        },
                    },
                    (
                        "label",
                        "expected_state_sha256",
                        "expected_trajectory_count",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="insert_robot_waypoint",
                description=(
                    "Append one LIN waypoint at the exact TCP multiplied by Tool "
                    "pose of one exact active-document Robot."
                ),
                action_ids=frozenset({"Robot_InsertWaypoint"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentRobotAndTrajectory",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "trajectory": object_reference_schema(),
                        "robot": object_reference_schema(),
                        "expected_trajectory_setup_state_sha256": _STATE_SHA256,
                        "expected_trajectory_state_sha256": _STATE_SHA256,
                        "expected_robot_setup_state_sha256": _STATE_SHA256,
                        "expected_robot_state_sha256": _STATE_SHA256,
                        "expected_defaults_state_sha256": _STATE_SHA256,
                    },
                    (
                        "trajectory",
                        "robot",
                        "expected_trajectory_setup_state_sha256",
                        "expected_trajectory_state_sha256",
                        "expected_robot_setup_state_sha256",
                        "expected_robot_state_sha256",
                        "expected_defaults_state_sha256",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="insert_position_waypoint",
                description=(
                    "Append one LIN waypoint at an explicit world-space point "
                    "using the frozen orientation, displacement, and motion "
                    "defaults; this does not control mouse preselection."
                ),
                action_ids=frozenset({"Robot_InsertWaypointPreselect"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentTrajectoryAndWorldPoint",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "trajectory": object_reference_schema(),
                        "position_mm": vector_schema(
                            minimum=-MAX_WAYPOINT_COORDINATE_MM,
                            maximum=MAX_WAYPOINT_COORDINATE_MM,
                        ),
                        "expected_trajectory_setup_state_sha256": _STATE_SHA256,
                        "expected_trajectory_state_sha256": _STATE_SHA256,
                        "expected_defaults_state_sha256": _STATE_SHA256,
                    },
                    (
                        "trajectory",
                        "position_mm",
                        "expected_trajectory_setup_state_sha256",
                        "expected_trajectory_state_sha256",
                        "expected_defaults_state_sha256",
                    ),
                ),
            ),
        ),
    )


def register_robot_trajectory_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(robot_trajectory_capability_definition())
