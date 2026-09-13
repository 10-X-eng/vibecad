# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry bindings for focused CAM inspection."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureFocusedInspectSchema import (
    MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES,
)
from VibeCADNativeManufactureInspectRuntime import NativeManufactureInspectRuntime


def _inspect(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeManufactureInspectRuntime):
        raise TypeError("A focused CAM inspection requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused CAM inspection requires argument data.")
    return runtime.inspect(arguments, ticket=getattr(call, "ticket", None))


def register_manufacture_focused_inspect_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES.values():
        registry.register_implementation(NativeCapabilityImplementation(name, _inspect))


def manufacture_focused_inspect_runtime_bindings(
    runtime: NativeManufactureInspectRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureInspectRuntime):
        raise TypeError("runtime must be a NativeManufactureInspectRuntime")
    return {
        name: runtime
        for name in MANUFACTURE_FOCUSED_INSPECT_CAPABILITIES.values()
    }


__all__ = [
    "manufacture_focused_inspect_runtime_bindings",
    "register_manufacture_focused_inspect_capability_implementations",
]
