# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Robot trajectories."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeRobotTrajectoryRuntime import NativeRobotTrajectoryRuntime
from VibeCADNativeRobotTrajectorySchema import ROBOT_TRAJECTORY_CAPABILITY_NAME


def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeRobotTrajectoryRuntime):
        raise TypeError("A Robot trajectory call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Robot trajectory call requires argument data.")
    return runtime.mutate_trajectory(arguments, ticket=ticket)


def register_robot_trajectory_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ROBOT_TRAJECTORY_CAPABILITY_NAME, _mutate)
    )


def robot_trajectory_runtime_bindings(
    runtime: NativeRobotTrajectoryRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeRobotTrajectoryRuntime):
        raise TypeError("runtime must be a NativeRobotTrajectoryRuntime")
    return {ROBOT_TRAJECTORY_CAPABILITY_NAME: runtime}
