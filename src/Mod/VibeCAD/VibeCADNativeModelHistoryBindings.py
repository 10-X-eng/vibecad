# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for exact Model History lifecycle control."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelHistoryRuntime import NativeModelHistoryRuntime


HISTORY_CAPABILITY_NAME = "model.history"
RECOMPUTE_CAPABILITY_NAME = "model.recompute"
MODEL_HISTORY_CAPABILITY_NAMES = (
    HISTORY_CAPABILITY_NAME,
    RECOMPUTE_CAPABILITY_NAME,
)


def _history(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelHistoryRuntime):
        raise TypeError("A Model History call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model History call requires argument data.")
    return runtime.control_history(
        arguments,
        ticket=getattr(call, "ticket", None),
    )


def _recompute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelHistoryRuntime):
        raise TypeError("A Model recompute call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model recompute call requires argument data.")
    return runtime.recompute_model(arguments)


def register_model_history_capability_implementations(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(HISTORY_CAPABILITY_NAME, _history)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(RECOMPUTE_CAPABILITY_NAME, _recompute)
    )


def model_history_runtime_bindings(
    runtime: NativeModelHistoryRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelHistoryRuntime):
        raise TypeError("runtime must be a NativeModelHistoryRuntime")
    return {
        HISTORY_CAPABILITY_NAME: runtime,
        RECOMPUTE_CAPABILITY_NAME: runtime,
    }
