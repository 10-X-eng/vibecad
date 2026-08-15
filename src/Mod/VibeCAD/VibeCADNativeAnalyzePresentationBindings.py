# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for legacy FEM result presentation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzePresentationRuntime import NativeAnalyzePresentationRuntime
from VibeCADNativeAnalyzePresentationSchema import (
    ANALYZE_PRESENTATION_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzePresentationRuntime):
        raise TypeError("An Analyze presentation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze presentation call requires argument data.")
    return runtime.execute(arguments)


def register_analyze_presentation_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ANALYZE_PRESENTATION_CAPABILITY_NAME,
            _execute,
        )
    )


def analyze_presentation_runtime_bindings(
    runtime: NativeAnalyzePresentationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzePresentationRuntime):
        raise TypeError("runtime must be a NativeAnalyzePresentationRuntime")
    return {ANALYZE_PRESENTATION_CAPABILITY_NAME: runtime}
