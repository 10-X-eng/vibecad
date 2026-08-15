# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Robot motion."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeRobotMotionRuntime import NativeRobotMotionRuntime
from VibeCADNativeRobotMotionSchema import ROBOT_MOTION_CAPABILITY_NAME


def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeRobotMotionRuntime):
        raise TypeError("A Robot motion call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Robot motion call requires argument data.")
    return runtime.mutate_motion(arguments, ticket=ticket)


def register_robot_motion_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ROBOT_MOTION_CAPABILITY_NAME, _mutate)
    )


def robot_motion_runtime_bindings(
    runtime: NativeRobotMotionRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeRobotMotionRuntime):
        raise TypeError("runtime must be a NativeRobotMotionRuntime")
    return {ROBOT_MOTION_CAPABILITY_NAME: runtime}
