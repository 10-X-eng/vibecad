# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for exact bounded Native FEM reads."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeInspectRuntime import NativeAnalyzeInspectRuntime
from VibeCADNativeAnalyzeInspectSchema import ANALYZE_INSPECT_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _inspect(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("An Analyze inspection call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze inspection call requires argument data.")
    return runtime.inspect(arguments)


def register_analyze_inspect_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_INSPECT_CAPABILITY_NAME, _inspect)
    )


def analyze_inspect_runtime_bindings(
    runtime: NativeAnalyzeInspectRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("runtime must be a NativeAnalyzeInspectRuntime")
    return {ANALYZE_INSPECT_CAPABILITY_NAME: runtime}

