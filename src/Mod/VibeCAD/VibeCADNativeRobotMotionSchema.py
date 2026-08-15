# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact Robot home and simulation operations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import object_reference_schema, parameters_schema


ROBOT_MOTION_CAPABILITY_NAME = "robot.motion"
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}


def _home_parameters() -> dict[str, object]:
    return parameters_schema(
        {
            "robot": object_reference_schema(),
            "expected_setup_state_sha256": _STATE_SHA256,
            "expected_robot_state_sha256": _STATE_SHA256,
        },
        (
            "robot",
            "expected_setup_state_sha256",
            "expected_robot_state_sha256",
        ),
    )


def robot_motion_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ROBOT_MOTION_CAPABILITY_NAME,
        description=(
            "Capture or restore one exact Robot home position, or evaluate one "
            "exact Robot and trajectory at bounded explicit simulation times."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="set_home_pos",
                description=(
                    "Capture the exact Robot's current six joint values as its "
                    "durable home position."
                ),
                action_ids=frozenset({"Robot_SetHomePos"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentRobot",
                transaction_behavior="document",
                background_required=False,
                parameters=_home_parameters(),
            ),
            NativeCapabilityVariant(
                operation="restore_home_pos",
                description=(
                    "Move the exact Robot's six joints to its durable home position."
                ),
                action_ids=frozenset({"Robot_RestoreHomePos"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentRobotWithSixAxisHome",
                transaction_behavior="document",
                background_required=False,
                parameters=_home_parameters(),
            ),
            NativeCapabilityVariant(
                operation="simulate",
                description=(
                    "Evaluate the shipped Robot trajectory simulation at explicit "
                    "ordered times without changing the document or opening a dialog."
                ),
                action_ids=frozenset({"Robot_Simulate"}),
                surface_ids=frozenset({"assemble", "manufacture"}),
                exact_target_type="ActiveDocumentRobotAndTrajectory",
                transaction_behavior="session",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "robot": object_reference_schema(),
                        "trajectory": object_reference_schema(),
                        "sample_times_s": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 64,
                            "items": {
                                "type": "number",
                                "minimum": 0.0,
                                "maximum": 1.0e12,
                            },
                        },
                        "expected_setup_state_sha256": _STATE_SHA256,
                        "expected_robot_state_sha256": _STATE_SHA256,
                        "expected_trajectory_setup_state_sha256": _STATE_SHA256,
                        "expected_trajectory_state_sha256": _STATE_SHA256,
                    },
                    (
                        "robot",
                        "trajectory",
                        "sample_times_s",
                        "expected_setup_state_sha256",
                        "expected_robot_state_sha256",
                        "expected_trajectory_setup_state_sha256",
                        "expected_trajectory_state_sha256",
                    ),
                ),
            ),
        ),
    )


def register_robot_motion_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(robot_motion_capability_definition())
