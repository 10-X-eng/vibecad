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
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeState import NativeCallTicket


class NativeRobotTrajectoryRuntime:
    """Mutate trajectories only on the exact frozen Assemble surface."""

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
            },
        )
        self._context.guard()
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
