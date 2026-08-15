# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for exact FEM post functions."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzePostFunctionRuntime import NativeAnalyzePostFunctionRuntime
from VibeCADNativeAnalyzePostFunctionSchema import (
    ANALYZE_POST_FUNCTION_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    ticket = getattr(call, "ticket", None)
    if not isinstance(runtime, NativeAnalyzePostFunctionRuntime):
        raise TypeError("An Analyze post-function call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze post-function call requires argument data.")
    return runtime.execute(arguments, ticket=ticket)


def register_analyze_post_function_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_POST_FUNCTION_CAPABILITY_NAME, _execute)
    )


def analyze_post_function_runtime_bindings(
    runtime: NativeAnalyzePostFunctionRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzePostFunctionRuntime):
        raise TypeError("runtime must be a NativeAnalyzePostFunctionRuntime")
    return {ANALYZE_POST_FUNCTION_CAPABILITY_NAME: runtime}
