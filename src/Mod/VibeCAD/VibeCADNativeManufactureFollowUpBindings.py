# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for related CAM setup creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeManufactureFollowUpRuntime import (
    NativeManufactureFollowUpRuntime,
)
from VibeCADNativeManufactureFollowUpSchema import (
    MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME,
)


def _create(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeManufactureFollowUpRuntime):
        raise TypeError("A follow-up CAM call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A follow-up CAM call requires argument data.")
    return runtime.create(arguments, ticket)


def register_manufacture_follow_up_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME,
            _create,
        )
    )


def manufacture_follow_up_runtime_bindings(
    runtime: NativeManufactureFollowUpRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeManufactureFollowUpRuntime):
        raise TypeError("runtime must be a NativeManufactureFollowUpRuntime")
    return {MANUFACTURE_FOLLOW_UP_CAPABILITY_NAME: runtime}
