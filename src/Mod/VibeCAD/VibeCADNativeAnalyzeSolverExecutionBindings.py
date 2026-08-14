# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for detached Native FEM solver execution."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeSolverExecutionRuntime import (
    NativeAnalyzeSolverExecutionRuntime,
)
from VibeCADNativeAnalyzeSolverExecutionSchema import (
    ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _execute(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeSolverExecutionRuntime):
        raise TypeError("An Analyze solver-execution call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("An Analyze solver-execution call requires argument data.")
    return runtime.execute(arguments, ticket=getattr(call, "ticket", None))


def register_analyze_solver_execution_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(
            ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME,
            _execute,
        )
    )


def analyze_solver_execution_runtime_bindings(
    runtime: NativeAnalyzeSolverExecutionRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeSolverExecutionRuntime):
        raise TypeError("runtime must be a NativeAnalyzeSolverExecutionRuntime")
    return {ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME: runtime}
