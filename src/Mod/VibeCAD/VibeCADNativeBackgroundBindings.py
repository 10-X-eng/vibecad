# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for shared Native background-job control."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeBackgroundRuntime import NativeBackgroundRuntime
from VibeCADNativeBackgroundSchema import NATIVE_BACKGROUND_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _control(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeBackgroundRuntime):
        raise TypeError("A Native job call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Native job call requires argument data.")
    return runtime.control(arguments)


def register_native_background_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(NATIVE_BACKGROUND_CAPABILITY_NAME, _control)
    )


def native_background_runtime_bindings(
    runtime: NativeBackgroundRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeBackgroundRuntime):
        raise TypeError("runtime must be a NativeBackgroundRuntime")
    return {NATIVE_BACKGROUND_CAPABILITY_NAME: runtime}
