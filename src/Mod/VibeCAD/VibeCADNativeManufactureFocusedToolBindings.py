# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry bindings for focused CAM tool mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureFocusedToolSchema import (
    MANUFACTURE_FOCUSED_TOOL_CAPABILITIES,
)
from VibeCADNativeManufactureToolRuntime import NativeManufactureToolRuntime


def _lower_focused_tool_arguments(arguments: Mapping[str, Any]) -> dict[str, Any]:
    lowered = dict(arguments)
    if lowered.get("operation") != "update_controller":
        return lowered
    controller = dict(lowered["controller"])
    controller["tool_number"] = {
        "kind": "explicit",
        "value": controller["tool_number"],
    }
    lowered["controller"] = controller
    return lowered


def _mutate(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeManufactureToolRuntime):
        raise TypeError("A focused CAM tool call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused CAM tool call requires argument data.")
    return runtime.mutate(
        _lower_focused_tool_arguments(arguments),
        ticket=getattr(call, "ticket", None),
    )


def register_manufacture_focused_tool_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in MANUFACTURE_FOCUSED_TOOL_CAPABILITIES.values():
        registry.register_implementation(NativeCapabilityImplementation(name, _mutate))


def manufacture_focused_tool_runtime_bindings(
    runtime: NativeManufactureToolRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureToolRuntime):
        raise TypeError("runtime must be a NativeManufactureToolRuntime")
    return {name: runtime for name in MANUFACTURE_FOCUSED_TOOL_CAPABILITIES.values()}


__all__ = [
    "manufacture_focused_tool_runtime_bindings",
    "register_manufacture_focused_tool_capability_implementations",
]
