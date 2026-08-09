# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for retained Part Join operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelJoinRuntime import NativeModelJoinRuntime


MODEL_JOIN_CAPABILITY_NAME = "model.join"


def _join(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelJoinRuntime):
        raise TypeError("A Model Join call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model Join call requires argument data.")
    return runtime.mutate_join(arguments, ticket=getattr(call, "ticket", None))


def register_model_join_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MODEL_JOIN_CAPABILITY_NAME, _join)
    )


def model_join_runtime_bindings(runtime: NativeModelJoinRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelJoinRuntime):
        raise TypeError("runtime must be a NativeModelJoinRuntime")
    return {MODEL_JOIN_CAPABILITY_NAME: runtime}
