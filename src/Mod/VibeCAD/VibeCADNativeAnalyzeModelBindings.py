# SPDX-License-Identifier: LGPL-2.1-or-later

"""Registry binding for Native FEM analysis and material mutations."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeModelRuntime import NativeAnalyzeModelRuntime
from VibeCADNativeAnalyzeCurrentTargets import current_analysis_target
from VibeCADNativeAnalyzeModelSchema import (
    ANALYZE_CONFIGURE_STUDY,
    ANALYZE_CREATE_STUDY,
    ANALYZE_MODEL_CAPABILITY_NAME,
)
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


def _focused_arguments(call: Any, operation: str) -> tuple[NativeAnalyzeModelRuntime, dict]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeModelRuntime):
        raise TypeError("A focused study call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A focused study call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != operation:
        raise ValueError(f"A focused study call requires the {operation} operation.")
    return runtime, values


def _create_study(call: Any) -> Mapping[str, Any]:
    runtime, values = _focused_arguments(call, "create")
    return runtime.execute(
        {
            "operation": "create_analysis",
            "label": values.pop("label"),
            "default_solver_policy": "none",
            "study": {
                "physics": values.pop("physics"),
                "regime": values.pop("regime"),
            },
        },
        ticket=getattr(call, "ticket", None),
    )


def _configure_study(call: Any) -> Mapping[str, Any]:
    runtime, values = _focused_arguments(call, "configure")
    target = current_analysis_target(runtime, values.pop("analysis_name"))
    return runtime.execute(
        {
            "operation": "update_study",
            "target": target,
            "study": {
                "physics": values.pop("physics"),
                "regime": values.pop("regime"),
            },
        },
        ticket=getattr(call, "ticket", None),
    )


def register_analyze_model_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_MODEL_CAPABILITY_NAME, _execute)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_CREATE_STUDY, _create_study)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_CONFIGURE_STUDY, _configure_study)
    )


def analyze_model_runtime_bindings(runtime: NativeAnalyzeModelRuntime) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeModelRuntime):
        raise TypeError("runtime must be a NativeAnalyzeModelRuntime")
    return {
        ANALYZE_MODEL_CAPABILITY_NAME: runtime,
        ANALYZE_CREATE_STUDY: runtime,
        ANALYZE_CONFIGURE_STUDY: runtime,
    }
