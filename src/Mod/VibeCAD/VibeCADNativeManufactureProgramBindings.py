# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact CAM program-control operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureProgramRuntime import NativeManufactureProgramRuntime
from VibeCADNativeManufactureProgramSchema import (
    MANUFACTURE_PROGRAM_CAPABILITY_NAME,
)


def _program(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureProgramRuntime):
        raise TypeError("A CAM program call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM program call requires argument data.")
    return runtime.program(arguments, ticket=ticket)


def register_manufacture_program_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MANUFACTURE_PROGRAM_CAPABILITY_NAME, _program)
    )


def manufacture_program_runtime_bindings(
    runtime: NativeManufactureProgramRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureProgramRuntime):
        raise TypeError("runtime must be a NativeManufactureProgramRuntime")
    return {MANUFACTURE_PROGRAM_CAPABILITY_NAME: runtime}
