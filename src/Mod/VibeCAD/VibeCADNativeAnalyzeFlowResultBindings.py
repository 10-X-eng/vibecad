# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bind focused CFD result reads to exact Analyze state."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeAnalyzeCurrentTargets import current_state
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeAnalyzeFlowPresentation import present_flow_result
from VibeCADNativeAnalyzeFlowResultSchema import (
    ANALYZE_COMPARE_FLOW,
    ANALYZE_FLOW_PERFORMANCE,
    ANALYZE_FLOW_RESULT,
    ANALYZE_SHOW_FLOW,
)
from VibeCADNativeAnalyzeInspectRuntime import NativeAnalyzeInspectRuntime
from VibeCADNativeAnalyzePresentationRuntime import NativeAnalyzePresentationRuntime
from VibeCADNativeAnalyzeResultState import result_state
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityImplementation,
    NativeCapabilityRegistry,
)


def _read(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("A flow-result call requires its exact inspection runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A flow-result call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "read":
        raise ValueError("A flow-result call requires the read operation.")
    _result, state = current_state(
        runtime,
        values.pop("result_name"),
        result_state,
    )
    flow = state.get("flow")
    if not isinstance(flow, Mapping):
        raise NativeAnalyzeError(
            "The named result does not contain completed OpenFOAM flow fields.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    boundary_fields = (
        "name",
        "kind",
        "area_m2",
        "pressure_area_average_pa",
        "velocity_area_average_m_s",
    )
    response = {
        "result_name": state["object_name"],
        **{
            name: flow[name]
            for name in (
                "pressure_unit",
                "velocity_unit",
                "pressure_range_pa",
                "velocity_magnitude_range_m_s",
                "maximum_velocity_m_s",
            )
        },
        "boundaries": [
            {name: boundary[name] for name in boundary_fields}
            for boundary in flow["boundaries"]
        ],
    }
    for name in ("converged", "turbulence_model"):
        if name in flow:
            response[name] = flow[name]
    for name in (
        "static_pressure_drop_pa",
        "pressure_drop_from",
        "pressure_drop_to",
    ):
        if name in flow:
            response[name] = flow[name]
    return response


def _performance(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("A flow-performance call requires its exact inspection runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A flow-performance call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "measure":
        raise ValueError("A flow-performance call requires the measure operation.")
    _result, state = current_state(
        runtime,
        values.pop("result_name"),
        result_state,
    )
    flow = state.get("flow")
    if not isinstance(flow, Mapping):
        raise NativeAnalyzeError(
            "The named result does not contain completed OpenFOAM flow fields.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    from femsolver.openfoam.results import openfoam_flow_performance

    try:
        performance = openfoam_flow_performance(flow, **values)
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise NativeAnalyzeError(str(exc)) from exc
    return {"result_name": state["object_name"], **performance}


def _comparison_passage(
    runtime: NativeAnalyzeInspectRuntime,
    value: Any,
) -> tuple[str, Mapping[str, Any], dict[str, str]]:
    if not isinstance(value, Mapping):
        raise TypeError("Each flow comparison passage must be one object.")
    passage = dict(value)
    _result, state = current_state(
        runtime,
        passage.pop("result_name"),
        result_state,
    )
    flow = state.get("flow")
    if not isinstance(flow, Mapping):
        raise NativeAnalyzeError(
            "The named result does not contain completed OpenFOAM flow fields.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    return str(state["object_name"]), flow, passage


def _compare(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("A flow-comparison call requires its exact inspection runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A flow-comparison call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "compare":
        raise ValueError("A flow-comparison call requires the compare operation.")
    baseline_name, baseline, baseline_passage = _comparison_passage(
        runtime, values.pop("baseline")
    )
    candidate_name, candidate, candidate_passage = _comparison_passage(
        runtime, values.pop("candidate")
    )
    from femsolver.openfoam.results import openfoam_flow_comparison

    try:
        comparison = openfoam_flow_comparison(
            baseline,
            candidate,
            baseline_passage=baseline_passage,
            candidate_passage=candidate_passage,
        )
    except (KeyError, TypeError, ValueError, RuntimeError) as exc:
        raise NativeAnalyzeError(str(exc)) from exc
    return {
        "baseline_result_name": baseline_name,
        "candidate_result_name": candidate_name,
        **comparison,
    }


def _show(call: Any) -> Mapping[str, Any]:
    runtime = getattr(call, "runtime", None)
    arguments = getattr(call, "arguments", None)
    if not isinstance(runtime, NativeAnalyzePresentationRuntime):
        raise TypeError("A flow-presentation call requires its exact runtime.")
    if not isinstance(arguments, Mapping):
        raise TypeError("A flow-presentation call requires argument data.")
    values = dict(arguments)
    if values.pop("operation", None) != "show":
        raise ValueError("A flow-presentation call requires the show operation.")
    context = getattr(runtime, "_context", None)
    if context is None:
        raise TypeError("A flow-presentation call requires its document context.")
    result, state = current_state(
        runtime,
        values.pop("result_name"),
        result_state,
    )
    if not isinstance(state.get("flow"), Mapping):
        raise NativeAnalyzeError(
            "The named result does not contain completed OpenFOAM flow fields.",
            error_code="NATIVE_ANALYZE_TARGET_TYPE_INVALID",
        )
    response = present_flow_result(result, values.pop("field"), visible=True)
    context.guard()
    return response


def register_analyze_flow_result_capability_implementation(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_FLOW_RESULT, _read)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_SHOW_FLOW, _show)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_FLOW_PERFORMANCE, _performance)
    )
    registry.register_implementation(
        NativeCapabilityImplementation(ANALYZE_COMPARE_FLOW, _compare)
    )


def analyze_flow_result_runtime_bindings(
    runtime: NativeAnalyzeInspectRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzeInspectRuntime):
        raise TypeError("runtime must be a NativeAnalyzeInspectRuntime")
    return {
        ANALYZE_FLOW_RESULT: runtime,
        ANALYZE_FLOW_PERFORMANCE: runtime,
        ANALYZE_COMPARE_FLOW: runtime,
    }


def analyze_flow_presentation_runtime_bindings(
    runtime: NativeAnalyzePresentationRuntime,
) -> dict[str, Any]:
    if not isinstance(runtime, NativeAnalyzePresentationRuntime):
        raise TypeError("runtime must be a NativeAnalyzePresentationRuntime")
    return {ANALYZE_SHOW_FLOW: runtime}
