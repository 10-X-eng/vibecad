# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact Manufacture reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureInspectRuntime import NativeManufactureInspectRuntime
from VibeCADNativeManufactureInspectSchema import (
    MANUFACTURE_INSPECT_CAPABILITY_NAME,
)


def _inspect(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeManufactureInspectRuntime):
        raise TypeError("A Manufacture inspection call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Manufacture inspection call requires argument data.")
    return runtime.inspect(arguments, ticket=getattr(call, "ticket", None))


def register_manufacture_inspect_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MANUFACTURE_INSPECT_CAPABILITY_NAME, _inspect)
    )


def manufacture_inspect_runtime_bindings(
    runtime: NativeManufactureInspectRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureInspectRuntime):
        raise TypeError("runtime must be a NativeManufactureInspectRuntime")
    return {MANUFACTURE_INSPECT_CAPABILITY_NAME: runtime}
