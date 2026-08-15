# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact CAM Area operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureAreaRuntime import NativeManufactureAreaRuntime
from VibeCADNativeManufactureAreaSchema import MANUFACTURE_AREA_CAPABILITY_NAME


def _area(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureAreaRuntime):
        raise TypeError("A CAM Area call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM Area call requires argument data.")
    return runtime.area(arguments, ticket=ticket)


def register_manufacture_area_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MANUFACTURE_AREA_CAPABILITY_NAME, _area)
    )


def manufacture_area_runtime_bindings(
    runtime: NativeManufactureAreaRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureAreaRuntime):
        raise TypeError("runtime must be a NativeManufactureAreaRuntime")
    return {MANUFACTURE_AREA_CAPABILITY_NAME: runtime}
