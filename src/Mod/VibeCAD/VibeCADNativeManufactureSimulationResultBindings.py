# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for retained CAM simulation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureSimulationResultRuntime import (
    NativeManufactureSimulationResultRuntime,
)
from VibeCADNativeManufactureSimulationResultSchema import (
    MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
)


def _simulate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureSimulationResultRuntime):
        raise TypeError("A retained CAM simulation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A retained CAM simulation call requires argument data.")
    return runtime.simulate(arguments, ticket)


def register_manufacture_simulation_result_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME,
            _simulate,
        )
    )


def manufacture_simulation_result_runtime_bindings(
    runtime: NativeManufactureSimulationResultRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureSimulationResultRuntime):
        raise TypeError("runtime must be a NativeManufactureSimulationResultRuntime")
    return {MANUFACTURE_SIMULATION_RESULT_CAPABILITY_NAME: runtime}
