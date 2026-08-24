# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind focused solver execution to the shared Analyze runtime."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeCurrentTargets import current_target
from VibeCADNativeAnalyzeRunSchema import ANALYZE_RUN_SOLVER
from VibeCADNativeAnalyzeSolverExecutionRuntime import (
    NativeAnalyzeSolverExecutionRuntime,
)
from VibeCADNativeAnalyzeSolverState import solver_state
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _run(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeSolverExecutionRuntime):
        raise TypeError("A focused solver run requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused solver run requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "run":
        raise ValueError("A focused solver run requires the run operation.")
    target = current_target(
        runtime,
        values.pop("solver_name"),
        solver_state,
    )
    values["target"] = target
    values.setdefault("timeout_seconds", 3600)
    result = dict(
        runtime.execute(
            {"operation": "run", **values},
            ticket=getattr(call, "ticket", None),
        )
    )
    result["solver_name"] = str(target["object_name"])
    return result


def register_analyze_run_solver_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_RUN_SOLVER, _run)
    )


def analyze_run_solver_runtime_bindings(
    runtime: NativeAnalyzeSolverExecutionRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeSolverExecutionRuntime):
        raise TypeError("runtime must be a NativeAnalyzeSolverExecutionRuntime")
    return {ANALYZE_RUN_SOLVER: runtime}
