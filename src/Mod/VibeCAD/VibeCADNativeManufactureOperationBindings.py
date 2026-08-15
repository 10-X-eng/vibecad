# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact CAM machining operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureOperationRuntime import (
    NativeManufactureOperationRuntime,
)
from VibeCADNativeManufactureOperationSchema import (
    MANUFACTURE_OPERATION_CAPABILITY_NAME,
)


def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureOperationRuntime):
        raise TypeError("A CAM operation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM operation call requires argument data.")
    return runtime.mutate_operation(arguments, ticket=ticket)


def register_manufacture_operation_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_OPERATION_CAPABILITY_NAME,
            _mutate,
        )
    )


def manufacture_operation_runtime_bindings(
    runtime: NativeManufactureOperationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureOperationRuntime):
        raise TypeError("runtime must be a NativeManufactureOperationRuntime")
    return {MANUFACTURE_OPERATION_CAPABILITY_NAME: runtime}
