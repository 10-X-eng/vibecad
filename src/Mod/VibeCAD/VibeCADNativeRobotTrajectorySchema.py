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
    placement_schema,
    vector_schema,
)
from VibeCADNativeRobotTrajectoryFeatureSpecs import (
    MAX_DRESS_UP_MOTION_VALUE,
    MAX_EDGE_SEGMENTATION_MM,
    MIN_EDGE_SEGMENTATION_MM,
)
from VibeCADNativeRobotTrajectory import MAX_WAYPOINT_COORDINATE_MM
from VibeCADNativeRobotTrajectoryState import (
    MAX_TRAJECTORIES,
    MAX_TRAJECTORY_SOURCES,
)


ROBOT_TRAJECTORY_CAPABILITY_NAME = "robot.trajectory"
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}
_NULLABLE_OBJECT_REFERENCE = {
    "oneOf": [object_reference_schema(), {"type": "null"}],
}
_NULLABLE_STATE_SHA256 = {
    "oneOf": [_STATE_SHA256, {"type": "null"}],
}
_FEATURE_MODE = {"type": "string", "enum": ["create", "edit"]}
_EDGE_NAMES = {
    "type": "array",
    "items": {
        "type": "string",
        "pattern": r"^Edge[1-9][0-9]*$",
        "maxLength": 32,
    },
    "minItems": 1,
    "maxItems": MAX_TRAJECTORY_SOURCES,
    "uniqueItems": True,
}


def robot_trajectory_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ROBOT_TRAJECTORY_CAPABILITY_NAME,
        description=(
            "Create and edit exact Robot trajectories, waypoints, edge routes, "
            "dress-ups, and ordered trajectory sequences."
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
                description="Append one LIN waypoint at an explicit world-space point.",
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
            NativeCapabilityVariant(
                operation="edge2_trac",
                description=(
                    "Create or edit one edge-derived trajectory from exact Part "
                    "edges and a frozen source shape."
                ),
                action_ids=frozenset({"Robot_Edge2Trac"}),
                surface_ids=frozenset({"assemble", "manufacture"}),
                exact_target_type="ExactPartEdgesAndOptionalEdgeTrajectory",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "mode": _FEATURE_MODE,
                        "target": _NULLABLE_OBJECT_REFERENCE,
                        "source": object_reference_schema(),
                        "edges": _EDGE_NAMES,
                        "segmentation_mm": {
                            "type": "number",
                            "minimum": MIN_EDGE_SEGMENTATION_MM,
                            "maximum": MAX_EDGE_SEGMENTATION_MM,
                        },
                        "use_rotation": {"type": "boolean"},
                        "expected_trajectory_setup_state_sha256": _STATE_SHA256,
                        "expected_target_state_sha256": _NULLABLE_STATE_SHA256,
                        "expected_source_state_sha256": _STATE_SHA256,
                    },
                    (
                        "mode",
                        "target",
                        "source",
                        "edges",
                        "segmentation_mm",
                        "use_rotation",
                        "expected_trajectory_setup_state_sha256",
                        "expected_target_state_sha256",
                        "expected_source_state_sha256",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="trajectory_dress_up",
                description=(
                    "Create or edit one exact trajectory modifier with explicit "
                    "motion, continuity, and placement semantics."
                ),
                action_ids=frozenset({"Robot_TrajectoryDressUp"}),
                surface_ids=frozenset({"assemble", "manufacture"}),
                exact_target_type="ExactSourceAndOptionalTrajectoryDressUp",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "mode": _FEATURE_MODE,
                        "target": _NULLABLE_OBJECT_REFERENCE,
                        "source": object_reference_schema(),
                        "use_speed": {"type": "boolean"},
                        "speed_mm_per_s": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": MAX_DRESS_UP_MOTION_VALUE,
                        },
                        "use_acceleration": {"type": "boolean"},
                        "acceleration_mm_per_s2": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": MAX_DRESS_UP_MOTION_VALUE,
                        },
                        "continuity_mode": {
                            "type": "string",
                            "enum": [
                                "unchanged",
                                "continuous",
                                "discontinuous",
                            ],
                        },
                        "placement": placement_schema(),
                        "placement_mode": {
                            "type": "string",
                            "enum": [
                                "unchanged",
                                "replace_orientation",
                                "translate",
                                "rotate",
                                "transform",
                            ],
                        },
                        "expected_trajectory_setup_state_sha256": _STATE_SHA256,
                        "expected_target_state_sha256": _NULLABLE_STATE_SHA256,
                        "expected_source_state_sha256": _STATE_SHA256,
                    },
                    (
                        "mode",
                        "target",
                        "source",
                        "use_speed",
                        "speed_mm_per_s",
                        "use_acceleration",
                        "acceleration_mm_per_s2",
                        "continuity_mode",
                        "placement",
                        "placement_mode",
                        "expected_trajectory_setup_state_sha256",
                        "expected_target_state_sha256",
                        "expected_source_state_sha256",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="trajectory_compound",
                description=(
                    "Create or edit one exact ordered sequence of unique, frozen "
                    "source trajectories."
                ),
                action_ids=frozenset({"Robot_TrajectoryCompound"}),
                surface_ids=frozenset({"assemble", "manufacture"}),
                exact_target_type="ExactOrderedSourcesAndOptionalTrajectoryCompound",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "mode": _FEATURE_MODE,
                        "target": _NULLABLE_OBJECT_REFERENCE,
                        "sources": {
                            "type": "array",
                            "items": parameters_schema(
                                {
                                    "trajectory": object_reference_schema(),
                                    "expected_state_sha256": _STATE_SHA256,
                                },
                                ("trajectory", "expected_state_sha256"),
                            ),
                            "minItems": 1,
                            "maxItems": MAX_TRAJECTORY_SOURCES,
                            "uniqueItems": True,
                        },
                        "expected_trajectory_setup_state_sha256": _STATE_SHA256,
                        "expected_target_state_sha256": _NULLABLE_STATE_SHA256,
                    },
                    (
                        "mode",
                        "target",
                        "sources",
                        "expected_trajectory_setup_state_sha256",
                        "expected_target_state_sha256",
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
