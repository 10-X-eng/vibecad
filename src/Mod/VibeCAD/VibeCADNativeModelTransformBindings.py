# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Model transformation operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelTransformRuntime import NativeModelTransformRuntime


MODEL_TRANSFORM_CAPABILITY_NAME = "model.transform"


def _transform(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelTransformRuntime):
        raise TypeError("A Model transform call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model transform call requires argument data.")
    return runtime.mutate_transform(
        arguments,
        ticket=getattr(call, "ticket", None),
    )


def register_model_transform_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MODEL_TRANSFORM_CAPABILITY_NAME, _transform)
    )


def model_transform_runtime_bindings(
    runtime: NativeModelTransformRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelTransformRuntime):
        raise TypeError("runtime must be a NativeModelTransformRuntime")
    return {MODEL_TRANSFORM_CAPABILITY_NAME: runtime}
