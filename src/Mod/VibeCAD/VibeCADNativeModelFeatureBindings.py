# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for the split Model feature family."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelFeatureRuntime import NativeModelFeatureRuntime


MODEL_FEATURE_CAPABILITY_NAMES = ("model.feature",)


def _feature(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelFeatureRuntime):
        raise TypeError("A Model feature call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model feature call requires argument data.")
    return runtime.mutate_feature(arguments, ticket=getattr(call, "ticket", None))


def register_model_feature_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation("model.feature", _feature)
    )


def model_feature_runtime_bindings(
    runtime: NativeModelFeatureRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelFeatureRuntime):
        raise TypeError("runtime must be a NativeModelFeatureRuntime")
    return {"model.feature": runtime}
