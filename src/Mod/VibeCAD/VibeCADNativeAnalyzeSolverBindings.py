# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for exact Native FEM solver creation."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeCurrentTargets import current_analysis_target
from VibeCADNativeAnalyzeSolverRuntime import NativeAnalyzeSolverRuntime
from VibeCADNativeAnalyzeSolverSchema import ANALYZE_SOLVER_CAPABILITY_NAME
from VibeCADNativeCapabilityRegistry import NativeCapabilityImplementation, NativeCapabilityRegistry


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeSolverRuntime):
        raise TypeError("An Analyze solver call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze solver call requires argument data.")
    values = dict(arguments)
    analysis = values.get("analysis")
    analysis_name = analysis if isinstance(analysis, str) else None
    if analysis_name is not None:
        values["analysis"] = current_analysis_target(runtime, analysis_name)
    result = dict(
        runtime.execute(values, ticket=getattr(call, "ticket", None))
    )
    if analysis_name is not None:
        result["analysis_name"] = str(analysis_name)
    return result


def register_analyze_solver_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_SOLVER_CAPABILITY_NAME, _execute)
    )


def analyze_solver_runtime_bindings(
    runtime: NativeAnalyzeSolverRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeSolverRuntime):
        raise TypeError("runtime must be a NativeAnalyzeSolverRuntime")
    return {ANALYZE_SOLVER_CAPABILITY_NAME: runtime}
