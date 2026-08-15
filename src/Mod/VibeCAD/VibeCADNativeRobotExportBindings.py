# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Native Robot program output."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeRobotExportRuntime import NativeRobotExportRuntime
from VibeCADNativeRobotExportSchema import ROBOT_EXPORT_CAPABILITY_NAME


def _export(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeRobotExportRuntime):
        raise TypeError("A Robot export call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Robot export call requires argument data.")
    return runtime.export(arguments, ticket=ticket)


def register_robot_export_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ROBOT_EXPORT_CAPABILITY_NAME, _export)
    )


def robot_export_runtime_bindings(
    runtime: NativeRobotExportRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeRobotExportRuntime):
        raise TypeError("runtime must be a NativeRobotExportRuntime")
    return {ROBOT_EXPORT_CAPABILITY_NAME: runtime}
