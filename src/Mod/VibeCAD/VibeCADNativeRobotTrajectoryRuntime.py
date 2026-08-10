# SPDX-License-Identifier: LGPL-2.1-or-later

"""Document-bound runtime for exact Robot trajectory operations."""

from __future__ import annotations

from functools import partial
from typing import Any, Mapping

from VibeCADNativeArguments import strict_variant_arguments
from VibeCADNativeImmediate import run_immediate_mutation
from VibeCADNativeRobotTrajectory import (
    NativeRobotTrajectoryError,
    append_waypoint,
    create_trajectory,
    preflight_position_waypoint,
    preflight_robot_waypoint,
    preflight_trajectory_create,
    prepare_position_waypoint_spec,
    prepare_robot_waypoint_spec,
    prepare_trajectory_create_spec,
    verify_appended_waypoint,
    verify_created_trajectory,
)
from VibeCADNativeRobotTrajectoryFeatures import (
    mutate_trajectory_feature,
    preflight_trajectory_feature,
    trajectory_feature_is_noop,
    verify_trajectory_feature,
    verify_trajectory_feature_noop,
)
from VibeCADNativeRobotTrajectoryFeatureSpecs import (
    prepare_compound_trajectory_spec,
    prepare_dress_up_trajectory_spec,
    prepare_edge_trajectory_spec,
)
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket, NativeRevisionConflict


_FEATURE_ARGUMENTS = {
    "edge2_trac": frozenset(
        {
            "mode",
            "target",
            "source",
            "edges",
            "segmentation_mm",
            "use_rotation",
            "expected_trajectory_setup_state_sha256",
            "expected_target_state_sha256",
            "expected_source_state_sha256",
        }
    ),
    "trajectory_dress_up": frozenset(
        {
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
        }
    ),
    "trajectory_compound": frozenset(
        {
            "mode",
            "target",
            "sources",
            "expected_trajectory_setup_state_sha256",
            "expected_target_state_sha256",
        }
    ),
}
_FEATURE_PREPARERS = {
    "edge2_trac": prepare_edge_trajectory_spec,
    "trajectory_dress_up": prepare_dress_up_trajectory_spec,
    "trajectory_compound": prepare_compound_trajectory_spec,
}
_FEATURE_TITLES = {
    "edge2_trac": "Robot Edge Trajectory",
    "trajectory_dress_up": "Robot Trajectory Modifier",
    "trajectory_compound": "Robot Trajectory Sequence",
}


def _require_current_ticket(
    context: NativeRuntimeContext,
    ticket: NativeCallTicket,
) -> None:
    if not isinstance(ticket, NativeCallTicket):
        raise TypeError("ticket must be a NativeCallTicket")
    current = context.state.current_revision(context.document_uid)
    if current != ticket.expected_revision:
        raise NativeRevisionConflict(ticket.expected_revision, current)


class NativeRobotTrajectoryRuntime:
    """Mutate trajectories only on an exact frozen Robot-capable surface."""

    def __init__(self, context: NativeRuntimeContext) -> None:
        if not isinstance(context, NativeRuntimeContext):
            raise TypeError("context must be a NativeRuntimeContext")
        self._context = context

    def mutate_trajectory(
        self,
        arguments: Mapping[str, Any],
        *,
        ticket: NativeCallTicket,
    ) -> dict[str, Any]:
        operation, values = strict_variant_arguments(
            arguments,
            {
                "create_trajectory": frozenset(
                    {
                        "label",
                        "expected_state_sha256",
                        "expected_trajectory_count",
                    }
                ),
                "insert_robot_waypoint": frozenset(
                    {
                        "trajectory",
                        "robot",
                        "expected_trajectory_setup_state_sha256",
                        "expected_trajectory_state_sha256",
                        "expected_robot_setup_state_sha256",
                        "expected_robot_state_sha256",
                        "expected_defaults_state_sha256",
                    }
                ),
                "insert_position_waypoint": frozenset(
                    {
                        "trajectory",
                        "position_mm",
                        "expected_trajectory_setup_state_sha256",
                        "expected_trajectory_state_sha256",
                        "expected_defaults_state_sha256",
                    }
                ),
                **_FEATURE_ARGUMENTS,
            },
        )
        self._context.guard()
        if operation in _FEATURE_ARGUMENTS:
            _require_current_ticket(self._context, ticket)
            spec = _FEATURE_PREPARERS[operation](
                self._context.document_uid,
                values,
            )
            prepared = preflight_trajectory_feature(
                self._context.document,
                operation,
                spec,
            )
            if trajectory_feature_is_noop(prepared):
                return verify_trajectory_feature_noop(
                    self._context.document,
                    prepared,
                )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name=(
                    f"{spec.target.mode.title()} Native {_FEATURE_TITLES[operation]}"
                ),
                mutate=partial(mutate_trajectory_feature, prepared=prepared),
                verify=verify_trajectory_feature,
            )
        if operation == "create_trajectory":
            prepared = preflight_trajectory_create(
                self._context.document,
                prepare_trajectory_create_spec(values),
            )
            return run_immediate_mutation(
                self._context,
                ticket=ticket,
                transaction_name="Create Native Robot Trajectory",
                mutate=partial(create_trajectory, prepared=prepared),
                verify=verify_created_trajectory,
            )
        if operation == "insert_robot_waypoint":
            prepared = preflight_robot_waypoint(
                self._context.document,
                prepare_robot_waypoint_spec(self._context.document_uid, values),
            )
        elif operation == "insert_position_waypoint":
            prepared = preflight_position_waypoint(
                self._context.document,
                prepare_position_waypoint_spec(self._context.document_uid, values),
            )
        else:
            raise NativeRobotTrajectoryError(
                "The requested Robot trajectory operation is not implemented."
            )
        return run_immediate_mutation(
            self._context,
            ticket=ticket,
            transaction_name="Insert Native Robot Waypoint",
            mutate=partial(append_waypoint, prepared=prepared),
            verify=verify_appended_waypoint,
        )
