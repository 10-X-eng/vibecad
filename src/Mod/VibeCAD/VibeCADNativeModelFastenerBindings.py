# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry and runtime binding for Model-ribbon standard fasteners."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)
from VibeCADNativeModelFastenerRuntime import NativeModelFastenerRuntime


MODEL_FASTENER_CAPABILITY_NAME = "model.fastener"


def _fastener(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeModelFastenerRuntime):
        raise TypeError("A Model fastener call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A Model fastener call requires argument data.")
    return runtime.mutate_fastener(
        arguments,
        ticket=getattr(call, "ticket", None),
    )


def register_model_fastener_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(MODEL_FASTENER_CAPABILITY_NAME, _fastener)
    )


def model_fastener_runtime_bindings(
    runtime: NativeModelFastenerRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeModelFastenerRuntime):
        raise TypeError("runtime must be a NativeModelFastenerRuntime")
    return {MODEL_FASTENER_CAPABILITY_NAME: runtime}
