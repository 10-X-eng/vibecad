# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for optional CAMotics presentation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureCamoticsRuntime import (
    NativeManufactureCamoticsRuntime,
)
from VibeCADNativeManufactureCamoticsSchema import (
    MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureCamoticsRuntime):
        raise TypeError("A CAMotics call requires its exact document runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAMotics call requires argument data.")
    return runtime.execute(arguments, ticket)


def register_manufacture_camotics_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_CAMOTICS_CAPABILITY_NAME,
            _execute,
        )
    )


def manufacture_camotics_runtime_bindings(
    runtime: NativeManufactureCamoticsRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureCamoticsRuntime):
        raise TypeError("runtime must be a NativeManufactureCamoticsRuntime")
    return {MANUFACTURE_CAMOTICS_CAPABILITY_NAME: runtime}
