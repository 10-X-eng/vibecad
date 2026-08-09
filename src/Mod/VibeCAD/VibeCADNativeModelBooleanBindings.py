# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for standalone Model boolean operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelBooleanRuntime import NativeModelBooleanRuntime


MODEL_BOOLEAN_CAPABILITY_NAME = "model.boolean"


def _boolean(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelBooleanRuntime):
        raise TypeError("A Model Boolean call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model Boolean call requires argument data.")
    return runtime.mutate_boolean(arguments, ticket=getattr(call, "ticket", None))


def register_model_boolean_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MODEL_BOOLEAN_CAPABILITY_NAME, _boolean)
    )


def model_boolean_runtime_bindings(
    runtime: NativeModelBooleanRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelBooleanRuntime):
        raise TypeError("runtime must be a NativeModelBooleanRuntime")
    return {MODEL_BOOLEAN_CAPABILITY_NAME: runtime}
