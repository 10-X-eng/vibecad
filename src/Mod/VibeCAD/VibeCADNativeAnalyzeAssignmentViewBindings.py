# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for FEM assignment presentation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeAssignmentViewRuntime import (
    NativeAnalyzeAssignmentViewRuntime,
)
from VibeCADNativeAnalyzeAssignmentViewSchema import (
    ANALYZE_ASSIGNMENT_VIEW_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeAssignmentViewRuntime):
        raise TypeError("An assignment-view call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An assignment-view call requires argument data.")
    return runtime.execute(arguments)


def register_analyze_assignment_view_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ANALYZE_ASSIGNMENT_VIEW_CAPABILITY_NAME,
            _execute,
        )
    )


def analyze_assignment_view_runtime_bindings(
    runtime: NativeAnalyzeAssignmentViewRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeAssignmentViewRuntime):
        raise TypeError("runtime must be a NativeAnalyzeAssignmentViewRuntime")
    return {ANALYZE_ASSIGNMENT_VIEW_CAPABILITY_NAME: runtime}
