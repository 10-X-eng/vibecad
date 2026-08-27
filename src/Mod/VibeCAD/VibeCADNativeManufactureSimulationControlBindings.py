# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native CAM simulation task control."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureSimulationControlSchema import (
    MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME,
)
from VibeCADNativeManufactureSimulationRuntime import (
    NativeManufactureSimulationRuntime,
)


def _close(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeManufactureSimulationRuntime):
        raise TypeError("CAM simulation close requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("CAM simulation close requires argument data.")
    return runtime.close(arguments, ticket=getattr(call, "ticket", None))


def register_manufacture_simulation_control_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME,
            _close,
        )
    )


def manufacture_simulation_control_runtime_bindings(
    runtime: NativeManufactureSimulationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureSimulationRuntime):
        raise TypeError("runtime must be a NativeManufactureSimulationRuntime")
    return {MANUFACTURE_SIMULATION_CONTROL_CAPABILITY_NAME: runtime}


__all__ = [
    "manufacture_simulation_control_runtime_bindings",
    "register_manufacture_simulation_control_capability_implementation",
]
