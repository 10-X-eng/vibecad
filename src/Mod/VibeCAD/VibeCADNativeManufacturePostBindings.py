# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact CAM postprocessing."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufacturePostRuntime import NativeManufacturePostRuntime
from VibeCADNativeManufacturePostSchema import MANUFACTURE_POST_CAPABILITY_NAME


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufacturePostRuntime):
        raise TypeError("A CAM post call requires its exact document runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A CAM post call requires argument data.")
    return runtime.execute(arguments, ticket)


def register_manufacture_post_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MANUFACTURE_POST_CAPABILITY_NAME, _execute)
    )


def manufacture_post_runtime_bindings(
    runtime: NativeManufacturePostRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufacturePostRuntime):
        raise TypeError("runtime must be a NativeManufacturePostRuntime")
    return {MANUFACTURE_POST_CAPABILITY_NAME: runtime}
