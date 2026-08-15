# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for standalone Model Part operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelPartRuntime import NativeModelPartRuntime


MODEL_PART_CAPABILITY_NAME = "model.part"


def _part(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelPartRuntime):
        raise TypeError("A Model Part call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model Part call requires argument data.")
    return runtime.mutate_part(arguments, ticket=getattr(call, "ticket", None))


def register_model_part_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MODEL_PART_CAPABILITY_NAME, _part)
    )


def model_part_runtime_bindings(runtime: NativeModelPartRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelPartRuntime):
        raise TypeError("runtime must be a NativeModelPartRuntime")
    return {MODEL_PART_CAPABILITY_NAME: runtime}
