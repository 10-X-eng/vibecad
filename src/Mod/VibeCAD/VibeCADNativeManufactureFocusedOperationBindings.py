# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry bindings for focused common CAM operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureFocusedOperationSchema import (
    MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES,
)
from VibeCADNativeManufactureOperationRuntime import NativeManufactureOperationRuntime


def _lower_focused_operation_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    values = dict(arguments)
    adaptive_fields = {
        "operation",
        "label",
        "job",
        "tool_controller",
        "geometry",
        "coolant",
    }
    if (
        values.get("operation") == "adaptive"
        and {"job", "tool_controller", "geometry"}.issubset(values)
        and set(values).issubset(adaptive_fields)
    ):
        values["operation"] = "adaptive_defaults"
    return values


def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeManufactureOperationRuntime):
        raise TypeError("A focused CAM operation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused CAM operation call requires argument data.")
    return runtime.mutate_operation(
        _lower_focused_operation_arguments(arguments),
        ticket=getattr(call, "ticket", None),
    )


def register_manufacture_focused_operation_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES.values():
        registry.register_implementation(NativeCapabilityImplementation(name, _mutate))


def manufacture_focused_operation_runtime_bindings(
    runtime: NativeManufactureOperationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureOperationRuntime):
        raise TypeError("runtime must be a NativeManufactureOperationRuntime")
    return {
        name: runtime
        for name in MANUFACTURE_FOCUSED_OPERATION_CAPABILITIES.values()
    }


__all__ = [
    "manufacture_focused_operation_runtime_bindings",
    "register_manufacture_focused_operation_capability_implementations",
]
