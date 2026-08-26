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


def robot_setup_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ROBOT_SETUP_CAPABILITY_NAME,
        description="Create and configure a Robot.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description="Create a six-axis Robot from selected visual and kinematic files.",
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
                    },
                    ("label",),
                ),
            ),
            NativeCapabilityVariant(
                operation="add_tool_shape",
                description="Attach a Part or VRML object as a Robot tool shape.",
                action_ids=frozenset({"Robot_AddToolShape"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentRobotAndToolShape",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "robot": object_reference_schema(),
                        "tool_shape": object_reference_schema(),
                    },
                    ("robot", "tool_shape"),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_default_orientation",
                description="Set the orientation and displacement for new waypoints.",
                action_ids=frozenset({"Robot_SetDefaultOrientation"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="RobotWaypointSessionOrientation",
                transaction_behavior="session",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "placement": placement_schema(),
                    },
                    ("placement",),
                ),
            ),
            NativeCapabilityVariant(
                operation="set_default_values",
                description="Set speed, acceleration, and continuity for new waypoints.",
                action_ids=frozenset({"Robot_SetDefaultValues"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="RobotWaypointSessionMotionDefaults",
                transaction_behavior="session",
                background_required=False,
                parameters=parameters_schema(
                    {
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
