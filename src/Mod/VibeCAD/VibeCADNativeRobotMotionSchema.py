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


def _home_parameters() -> dict[str, object]:
    return parameters_schema(
        {
            "robot": object_reference_schema(),
        },
        ("robot",),
    )


def robot_motion_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ROBOT_MOTION_CAPABILITY_NAME,
        description="Set Robot home positions and sample trajectory motion.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="set_home_pos",
                description="Save the Robot's current six-axis position as home.",
                action_ids=frozenset({"Robot_SetHomePos"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentRobot",
                transaction_behavior="document",
                background_required=False,
                parameters=_home_parameters(),
            ),
            NativeCapabilityVariant(
                operation="restore_home_pos",
                description="Move the Robot to its saved home position.",
                action_ids=frozenset({"Robot_RestoreHomePos"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentRobotWithSixAxisHome",
                transaction_behavior="document",
                background_required=False,
                parameters=_home_parameters(),
            ),
            NativeCapabilityVariant(
                operation="simulate",
                description="Sample the Robot and trajectory at ordered times.",
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
                    },
                    (
                        "robot",
                        "trajectory",
                        "sample_times_s",
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
