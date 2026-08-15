# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native FEM mechanical support conditions."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeSupportRuntime import NativeAnalyzeSupportRuntime
from VibeCADNativeAnalyzeSupportSchema import ANALYZE_SUPPORT_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeSupportRuntime):
        raise TypeError("An Analyze support call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze support call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_support_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_SUPPORT_CAPABILITY_NAME, _execute)
    )


def analyze_support_runtime_bindings(
    runtime: NativeAnalyzeSupportRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeSupportRuntime):
        raise TypeError("runtime must be a NativeAnalyzeSupportRuntime")
    return {ANALYZE_SUPPORT_CAPABILITY_NAME: runtime}

