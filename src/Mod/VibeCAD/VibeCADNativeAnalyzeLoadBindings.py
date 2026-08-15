# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native FEM mechanical loads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeLoadRuntime import NativeAnalyzeLoadRuntime
from VibeCADNativeAnalyzeLoadSchema import ANALYZE_LOAD_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation, NativeCapabilityRegistry


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeLoadRuntime):
        raise TypeError("An Analyze load call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze load call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_load_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_LOAD_CAPABILITY_NAME, _execute)
    )


def analyze_load_runtime_bindings(runtime: NativeAnalyzeLoadRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeLoadRuntime):
        raise TypeError("runtime must be a NativeAnalyzeLoadRuntime")
    return {ANALYZE_LOAD_CAPABILITY_NAME: runtime}
