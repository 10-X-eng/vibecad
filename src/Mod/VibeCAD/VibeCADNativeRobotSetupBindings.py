# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Robot setup."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeRobotSetupRuntime import NativeRobotSetupRuntime
from VibeCADNativeRobotSetupSchema import ROBOT_SETUP_CAPABILITY_NAME


def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeRobotSetupRuntime):
        raise TypeError("A Robot setup call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Robot setup call requires argument data.")
    return runtime.mutate_setup(arguments, ticket=ticket)


def register_robot_setup_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ROBOT_SETUP_CAPABILITY_NAME, _mutate)
    )


def robot_setup_runtime_bindings(
    runtime: NativeRobotSetupRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeRobotSetupRuntime):
        raise TypeError("runtime must be a NativeRobotSetupRuntime")
    return {ROBOT_SETUP_CAPABILITY_NAME: runtime}
