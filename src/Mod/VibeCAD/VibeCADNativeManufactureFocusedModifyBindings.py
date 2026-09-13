# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry bindings for focused CAM operation edits and dress-ups."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureFocusedModifySchema import (
    MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES,
)
from VibeCADNativeManufactureModifyRuntime import NativeManufactureModifyRuntime


def _modify(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeManufactureModifyRuntime):
        raise TypeError("A focused CAM edit requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused CAM edit requires argument data.")
    return runtime.modify(arguments, ticket=getattr(call, "ticket", None))


def register_manufacture_focused_modify_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in dict.fromkeys(MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES.values()):
        registry.register_implementation(NativeCapabilityImplementation(name, _modify))


def manufacture_focused_modify_runtime_bindings(
    runtime: NativeManufactureModifyRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureModifyRuntime):
        raise TypeError("runtime must be a NativeManufactureModifyRuntime")
    return {
        name: runtime
        for name in dict.fromkeys(MANUFACTURE_FOCUSED_MODIFY_CAPABILITIES.values())
    }


__all__ = [
    "manufacture_focused_modify_runtime_bindings",
    "register_manufacture_focused_modify_capability_implementations",
]
