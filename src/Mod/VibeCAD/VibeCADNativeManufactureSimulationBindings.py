# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact GL CAM simulation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureSimulationRuntime import (
    NativeManufactureSimulationRuntime,
)
from VibeCADNativeManufactureSimulationSchema import (
    MANUFACTURE_SIMULATION_CAPABILITY_NAME,
)


def _simulate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureSimulationRuntime):
        raise TypeError("A Manufacture simulation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Manufacture simulation call requires argument data.")
    return runtime.simulate(arguments, ticket)


def register_manufacture_simulation_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_SIMULATION_CAPABILITY_NAME,
            _simulate,
        )
    )


def manufacture_simulation_runtime_bindings(
    runtime: NativeManufactureSimulationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureSimulationRuntime):
        raise TypeError("runtime must be a NativeManufactureSimulationRuntime")
    return {MANUFACTURE_SIMULATION_CAPABILITY_NAME: runtime}
