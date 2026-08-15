# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime bindings for Design Hole capabilities."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelHoleRuntime import NativeModelHoleRuntime


MODEL_HOLE_CAPABILITY_NAME = "model.hole"


def _runtime(call: Any) -> NativeModelHoleRuntime:
    value = getattr(call, "runtime", None)
    if not isinstance(value, NativeModelHoleRuntime):
        raise TypeError("A Model Hole call requires its exact runtime.")
    return value


def _arguments(call: Any) -> Mapping[str, Any]:
    value = getattr(call, "arguments", None)
    if not isinstance(value, Mapping):
        raise TypeError("A Model Hole call requires argument data.")
    return value


def _hole(call: Any) -> Mapping[str, Any]:
    return _runtime(call).mutate_hole(
        _arguments(call),
        ticket=getattr(call, "ticket", None),
    )


def register_model_hole_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation("model.hole", _hole)
    )


def model_hole_runtime_bindings(runtime: NativeModelHoleRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelHoleRuntime):
        raise TypeError("runtime must be a NativeModelHoleRuntime")
    return {MODEL_HOLE_CAPABILITY_NAME: runtime}
