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
from VibeCADNativeRobotTrajectoryState import MAX_TRAJECTORY_SOURCES


ROBOT_TRAJECTORY_CAPABILITY_NAME = "robot.trajectory"
ROBOT_EDGE_PATH_CAPABILITY_NAME = "robot.edge_path"
ROBOT_PATH_MOTION_CAPABILITY_NAME = "robot.set_path_motion"
ROBOT_PATH_SEQUENCE_CAPABILITY_NAME = "robot.path_sequence"
_NULLABLE_OBJECT_REFERENCE = {
    "oneOf": [{"type": "null"}, object_reference_schema()],
    "description": (
        "Create has no existing feature: null. Edit updates this existing feature."
    ),
}
_FEATURE_MODE = {
    "type": "string",
    "enum": ["create", "edit"],
    "description": (
        "Trajectory feature operations: create makes a new feature; edit updates target."
    ),
}
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
_IDENTITY_PLACEMENT = {
    "origin_mm": {"x": 0.0, "y": 0.0, "z": 0.0},
    "rotation": {
        "axis": {"x": 0.0, "y": 0.0, "z": 1.0},
        "angle_degrees": 0.0,
    },
}


def _focused_feature_variants(
    *,
    create_operation: str,
    edit_operation: str,
    action_id: str,
    surfaces: frozenset[str],
    exact_target_type: str,
    target_description: str,
    create_description: str,
    edit_description: str,
    properties: dict[str, dict],
    create_required: tuple[str, ...],
    edit_required: tuple[str, ...],
) -> tuple[NativeCapabilityVariant, NativeCapabilityVariant]:
    return (
        NativeCapabilityVariant(
            operation=create_operation,
            description=create_description,
            action_ids=frozenset({action_id}),
            surface_ids=surfaces,
            exact_target_type=exact_target_type,
            transaction_behavior="document",
            background_required=False,
            parameters=parameters_schema(properties, create_required),
        ),
        NativeCapabilityVariant(
            operation=edit_operation,
            description=edit_description,
            action_ids=frozenset({action_id}),
            surface_ids=surfaces,
            exact_target_type=exact_target_type,
            transaction_behavior="document",
            background_required=False,
            parameters=parameters_schema(
                {
                    "target": {
                        **object_reference_schema(),
                        "description": target_description,
                    },
                    **properties,
                },
                ("target", *edit_required),
            ),
            provider_supplemental=True,
        ),
    )


def robot_edge_path_capability_definition() -> NativeCapabilityDefinition:
    properties = {
        "source": {
            **object_reference_schema(),
            "description": "Part feature containing the path edges.",
        },
        "edges": {
            **_EDGE_NAMES,
            "description": "Ordered connected edge names.",
        },
        "segmentation_mm": {
            "type": "number",
            "minimum": MIN_EDGE_SEGMENTATION_MM,
            "maximum": MAX_EDGE_SEGMENTATION_MM,
            "default": 0.5,
            "description": "Maximum curve deviation in mm; default 0.5.",
        },
        "use_rotation": {
            "type": "boolean",
            "default": False,
            "description": "Use edge orientation; default false.",
        },
    }
    return NativeCapabilityDefinition(
        name=ROBOT_EDGE_PATH_CAPABILITY_NAME,
        description="Create or edit Robot paths from Part edges.",
        primary_classification="mutation",
        variants=_focused_feature_variants(
            create_operation="create_path",
            edit_operation="edit_path",
            action_id="Robot_Edge2Trac",
            surfaces=frozenset({"assemble", "manufacture"}),
            exact_target_type="ExactPartEdgesAndOptionalEdgeTrajectory",
            target_description="Existing edge path to edit.",
            create_description="Create an edge-derived Robot path.",
            edit_description="Edit an existing edge-derived Robot path.",
            properties=properties,
            create_required=("source", "edges"),
            edit_required=(
                "source",
                "edges",
                "segmentation_mm",
                "use_rotation",
            ),
        ),
    )


def robot_path_motion_capability_definition() -> NativeCapabilityDefinition:
    properties = {
        "path": {
            **object_reference_schema(),
            "description": (
                "Base Robot path to modify, or existing motion modifier to update."
            ),
        },
        "speed_limit_mm_per_s": {
            "type": "number",
            "minimum": 0.0,
            "maximum": MAX_DRESS_UP_MOTION_VALUE,
            "description": "Speed limit in mm/s.",
        },
        "remove_speed_limit": {
            "type": "boolean",
            "enum": [True],
            "description": "Remove the speed limit.",
        },
        "acceleration_limit_mm_per_s2": {
            "type": "number",
            "minimum": 0.0,
            "maximum": MAX_DRESS_UP_MOTION_VALUE,
            "description": "Acceleration limit in mm/s2.",
        },
        "remove_acceleration_limit": {
            "type": "boolean",
            "enum": [True],
            "description": "Remove the acceleration limit.",
        },
        "continuity_mode": {
            "type": "string",
            "enum": ["unchanged", "continuous", "discontinuous"],
            "default": "unchanged",
        },
        "placement": {
            **placement_schema(),
            "default": _IDENTITY_PLACEMENT,
        },
        "placement_mode": {
            "type": "string",
            "enum": [
                "unchanged",
                "replace_orientation",
                "translate",
                "rotate",
                "transform",
            ],
            "default": "unchanged",
        },
    }
    return NativeCapabilityDefinition(
        name=ROBOT_PATH_MOTION_CAPABILITY_NAME,
        description=(
            "Set speed, acceleration, continuity, or placement on a Robot path."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="set_motion",
                description="Apply motion settings to a Robot path.",
                action_ids=frozenset({"Robot_TrajectoryDressUp"}),
                surface_ids=frozenset({"assemble", "manufacture"}),
                exact_target_type="ExactRobotPathOrTrajectoryDressUp",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(properties, ("path",)),
            ),
        ),
    )


def robot_path_sequence_capability_definition() -> NativeCapabilityDefinition:
    properties = {
        "sources": {
            "type": "array",
            "items": parameters_schema(
                {"trajectory": object_reference_schema()},
                ("trajectory",),
            ),
            "minItems": 1,
            "maxItems": MAX_TRAJECTORY_SOURCES,
            "uniqueItems": True,
            "description": "Robot paths in execution order.",
        },
    }
    return NativeCapabilityDefinition(
        name=ROBOT_PATH_SEQUENCE_CAPABILITY_NAME,
        description="Create or edit ordered Robot path sequences.",
        primary_classification="mutation",
        variants=_focused_feature_variants(
            create_operation="create_sequence",
            edit_operation="edit_sequence",
            action_id="Robot_TrajectoryCompound",
            surfaces=frozenset({"assemble", "manufacture"}),
            exact_target_type="ExactOrderedSourcesAndOptionalTrajectoryCompound",
            target_description="Existing path sequence to edit.",
            create_description="Create an ordered Robot path sequence.",
            edit_description="Edit an ordered Robot path sequence.",
            properties=properties,
            create_required=("sources",),
            edit_required=("sources",),
        ),
    )


def robot_trajectory_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ROBOT_TRAJECTORY_CAPABILITY_NAME,
        description="Create trajectories and add Robot or position waypoints.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_trajectory",
                description="Create an empty Robot trajectory named by label.",
                action_ids=frozenset({"Robot_CreateTrajectory"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentTrajectoryState",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": {
                            **LABEL_SCHEMA,
                            "description": "Trajectory name.",
                        },
                    },
                    ("label",),
                ),
            ),
            NativeCapabilityVariant(
                operation="insert_robot_waypoint",
                description="Append a LIN waypoint at the Robot TCP and tool pose.",
                action_ids=frozenset({"Robot_InsertWaypoint"}),
                surface_ids=frozenset({"assemble"}),
                exact_target_type="ActiveDocumentRobotAndTrajectory",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "trajectory": object_reference_schema(),
                        "robot": object_reference_schema(),
                    },
                    ("trajectory", "robot"),
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
                    },
                    ("trajectory", "position_mm"),
                ),
            ),
            NativeCapabilityVariant(
                operation="edge2_trac",
                description="Create or edit a trajectory along Part edges.",
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
                    },
                    (
                        "mode",
                        "target",
                        "source",
                        "edges",
                        "segmentation_mm",
                        "use_rotation",
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="trajectory_dress_up",
                description="Create or edit a trajectory motion and placement modifier.",
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
                    ),
                ),
            ),
            NativeCapabilityVariant(
                operation="trajectory_compound",
                description="Create or edit an ordered trajectory sequence.",
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
                                },
                                ("trajectory",),
                            ),
                            "minItems": 1,
                            "maxItems": MAX_TRAJECTORY_SOURCES,
                            "uniqueItems": True,
                        },
                    },
                    (
                        "mode",
                        "target",
                        "sources",
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


def register_robot_path_feature_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(robot_edge_path_capability_definition())
    registry.register_definition(robot_path_motion_capability_definition())
    registry.register_definition(robot_path_sequence_capability_definition())
