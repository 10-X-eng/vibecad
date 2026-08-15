# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native FEM analysis and material mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeModelRuntime import NativeAnalyzeModelRuntime
from VibeCADNativeAnalyzeModelSchema import ANALYZE_MODEL_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeModelRuntime):
        raise TypeError("An Analyze model call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze model call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_model_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_MODEL_CAPABILITY_NAME, _execute)
    )


def analyze_model_runtime_bindings(runtime: NativeAnalyzeModelRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeModelRuntime):
        raise TypeError("runtime must be a NativeAnalyzeModelRuntime")
    return {ANALYZE_MODEL_CAPABILITY_NAME: runtime}

