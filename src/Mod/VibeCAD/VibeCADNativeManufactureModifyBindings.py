# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for deterministic CAM modifications."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureModifyRuntime import NativeManufactureModifyRuntime
from VibeCADNativeManufactureModifySchema import (
    MANUFACTURE_MODIFY_CAPABILITY_NAME,
)


def _modify(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureModifyRuntime):
        raise TypeError("A CAM modification call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM modification call requires argument data.")
    return runtime.modify(arguments, ticket=ticket)


def register_manufacture_modify_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MANUFACTURE_MODIFY_CAPABILITY_NAME, _modify)
    )


def manufacture_modify_runtime_bindings(
    runtime: NativeManufactureModifyRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureModifyRuntime):
        raise TypeError("runtime must be a NativeManufactureModifyRuntime")
    return {MANUFACTURE_MODIFY_CAPABILITY_NAME: runtime}
