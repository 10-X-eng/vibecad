# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry bindings for focused CAM post output scope."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureFocusedPostSchema import (
    MANUFACTURE_FOCUSED_POST_CAPABILITIES,
)
from VibeCADNativeManufacturePostRuntime import NativeManufacturePostRuntime


def _post(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeManufacturePostRuntime):
        raise TypeError("A focused CAM post request requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused CAM post request requires argument data.")
    return runtime.execute(arguments, getattr(call, "ticket", None))


def register_manufacture_focused_post_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for name in dict.fromkeys(MANUFACTURE_FOCUSED_POST_CAPABILITIES.values()):
        registry.register_implementation(NativeCapabilityImplementation(name, _post))


def manufacture_focused_post_runtime_bindings(
    runtime: NativeManufacturePostRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufacturePostRuntime):
        raise TypeError("runtime must be a NativeManufacturePostRuntime")
    return {
        name: runtime
        for name in dict.fromkeys(MANUFACTURE_FOCUSED_POST_CAPABILITIES.values())
    }


__all__ = [
    "manufacture_focused_post_runtime_bindings",
    "register_manufacture_focused_post_capability_implementations",
]
