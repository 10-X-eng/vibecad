# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime bindings for Model dress-up operations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelDressupRuntime import NativeModelDressupRuntime


MODEL_DRESSUP_CAPABILITY_NAME = "model.dressup"


def _runtime(call: Any) -> NativeModelDressupRuntime:
    value = getattr(call, "runtime", None)
    if not isinstance(value, NativeModelDressupRuntime):
        raise TypeError("A Model dress-up call requires its exact runtime.")
    return value


def _arguments(call: Any) -> Mapping[str, Any]:
    value = getattr(call, "arguments", None)
    if not isinstance(value, Mapping):
        raise TypeError("A Model dress-up call requires argument data.")
    return value


def _dressup(call: Any) -> Mapping[str, Any]:
    return _runtime(call).mutate_dressup(
        _arguments(call),
        ticket=getattr(call, "ticket", None),
    )


def register_model_dressup_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MODEL_DRESSUP_CAPABILITY_NAME, _dressup)
    )


def model_dressup_runtime_bindings(runtime: NativeModelDressupRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelDressupRuntime):
        raise TypeError("runtime must be a NativeModelDressupRuntime")
    return {MODEL_DRESSUP_CAPABILITY_NAME: runtime}
