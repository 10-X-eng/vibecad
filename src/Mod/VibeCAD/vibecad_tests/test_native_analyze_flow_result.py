# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
from copy import deepcopy
from types import SimpleNamespace

from VibeCADNativeAnalyzeFlowResultSchema import (
    ANALYZE_COMPARE_FLOW,
    ANALYZE_FLOW_PERFORMANCE,
    analyze_flow_result_capability_definitions,
)
from VibeCADNativeAnalyzeResultState import openfoam_flow_summary_state
from VibeCADNativeAnalyzeSnapshot import _compact_result


def _summary() -> dict:
    return {
        "format_version": 1,
        "pressure_unit": "Pa",
        "velocity_unit": "m/s",
        "density_kg_m3": 1.2,
        "kinematic_viscosity_m2_s": 1.5e-5,
        "turbulence_model": "kOmegaSST",
        "converged": True,
        "pressure_range_pa": [0.0, 12.0],
        "velocity_magnitude_range_m_s": [0.0, 2.0],
        "maximum_velocity_m_s": 2.0,
        "boundaries": [
            {
                "name": "inlet",
                "kind": "inlet_velocity",
                "area_m2": 1.0,
                "geometric_area_m2": 1.0,
                "pressure_area_average_pa": 12.0,
                "velocity_area_average_m_s": [2.0, 0.0, 0.0],
                "outward_volumetric_flow_rate_m3_s": -2.0,
                "outward_mass_flow_rate_kg_s": -2.4,
                "condition": {
                    "kind": "inlet_velocity",
                    "velocity_m_s": 2.0,
                    "turbulence": {
                        "kind": "intensity_length_scale",
                        "intensity_ratio": 0.05,
                        "length_scale_m": 0.01,
                    },
                },
            },
            {
                "name": "outlet",
                "kind": "outlet_static_pressure",
                "area_m2": 1.0,
                "geometric_area_m2": 1.0,
                "pressure_area_average_pa": 0.0,
                "velocity_area_average_m_s": [2.0, 0.0, 0.0],
                "outward_volumetric_flow_rate_m3_s": 2.0,
                "outward_mass_flow_rate_kg_s": 2.4,
                "condition": {
                    "kind": "outlet_static_pressure",
                    "pressure_pa": 0.0,
                    "turbulence": {"kind": "none"},
                },
            },
        ],
    }


def test_flow_performance_has_one_explicit_boundary_contract() -> None:
    definitions = {
        definition.name: definition
        for definition in analyze_flow_result_capability_definitions()
    }

    variant = definitions[ANALYZE_FLOW_PERFORMANCE].variants[0]

    assert variant.operation == "measure"
    assert set(variant.parameters["properties"]) == {
        "result_name",
        "upstream_boundary",
        "downstream_boundary",
        "flow_boundary",
    }
    assert set(variant.parameters["required"]) == set(
        variant.parameters["properties"]
    )

    comparison = definitions[ANALYZE_COMPARE_FLOW].variants[0]
    assert comparison.operation == "compare"
    assert set(comparison.parameters["properties"]) == {"baseline", "candidate"}
    assert set(comparison.parameters["properties"]["baseline"]["properties"]) == {
        "result_name",
        "upstream_boundary",
        "downstream_boundary",
        "flow_boundary",
    }


def test_flow_summary_state_retains_exact_performance_inputs() -> None:
    class Result:
        PropertiesList = ("VibeCADOpenFOAMSummary",)
        VibeCADOpenFOAMSummary = json.dumps(_summary())

    state = openfoam_flow_summary_state(Result())

    assert state["density_kg_m3"] == 1.2
    assert state["boundaries"][0]["geometric_area_m2"] == 1.0
    assert state["boundaries"][0]["outward_volumetric_flow_rate_m3_s"] == -2.0
    assert state["boundaries"][1]["outward_mass_flow_rate_kg_s"] == 2.4
    assert state["boundaries"][0]["condition"]["velocity_m_s"] == 2.0
    assert state["converged"] is True
    assert state["turbulence_model"] == "kOmegaSST"


def test_flow_comparison_requires_matching_converged_operating_conditions() -> None:
    from femsolver.openfoam.results import openfoam_flow_comparison

    baseline = _summary()
    candidate = deepcopy(baseline)
    candidate["boundaries"][0]["pressure_area_average_pa"] = 10.0
    candidate["boundaries"][0]["outward_volumetric_flow_rate_m3_s"] = -2.2
    candidate["boundaries"][1]["outward_volumetric_flow_rate_m3_s"] = 2.2
    passage = {
        "upstream_boundary": "inlet",
        "downstream_boundary": "outlet",
        "flow_boundary": "outlet",
    }

    comparison = openfoam_flow_comparison(
        baseline,
        candidate,
        baseline_passage=passage,
        candidate_passage=passage,
    )

    assert comparison["baseline"]["static_pressure_drop_pa"] == 12.0
    assert comparison["candidate"]["static_pressure_drop_pa"] == 10.0
    assert comparison["changes"]["static_pressure_drop_pa"]["value"] == -2.0
    assert comparison["changes"]["volumetric_flow_rate_m3_s"]["value"] == 0.2

    candidate["boundaries"][0]["condition"]["velocity_m_s"] = 3.0
    try:
        openfoam_flow_comparison(
            baseline,
            candidate,
            baseline_passage=passage,
            candidate_passage=passage,
        )
    except RuntimeError as exc:
        assert "operating conditions differ" in str(exc)
    else:
        raise AssertionError("different operating conditions were compared")


def test_flow_comparison_binding_uses_both_explicit_result_names(monkeypatch) -> None:
    import VibeCADNativeAnalyzeFlowResultBindings as bindings
    from VibeCADNativeAnalyzeInspectRuntime import NativeAnalyzeInspectRuntime

    baseline = _summary()
    candidate = deepcopy(baseline)
    candidate["boundaries"][0]["pressure_area_average_pa"] = 10.0
    states = {
        "BaselineFlow": {
            "object_name": "BaselineFlow",
            "flow": baseline,
        },
        "CandidateFlow": {
            "object_name": "CandidateFlow",
            "flow": candidate,
        },
    }
    monkeypatch.setattr(
        bindings,
        "current_state",
        lambda _runtime, name, _reader: (object(), states[name]),
    )
    runtime = object.__new__(NativeAnalyzeInspectRuntime)
    passage = {
        "upstream_boundary": "inlet",
        "downstream_boundary": "outlet",
        "flow_boundary": "outlet",
    }

    result = bindings._compare(
        SimpleNamespace(
            runtime=runtime,
            arguments={
                "operation": "compare",
                "baseline": {"result_name": "BaselineFlow", **passage},
                "candidate": {"result_name": "CandidateFlow", **passage},
            },
        )
    )

    assert result["baseline_result_name"] == "BaselineFlow"
    assert result["candidate_result_name"] == "CandidateFlow"
    assert result["changes"]["static_pressure_drop_pa"]["value"] == -2.0


def test_solved_result_context_publishes_exact_flow_boundary_names() -> None:
    compact = _compact_result(
        {
            "object_name": "FlowResult",
            "result_kind": "pipeline",
            "state_sha256": "state",
            "flow_boundaries": [
                {"name": "inlet", "kind": "inlet_velocity"},
                {"name": "outlet", "kind": "outlet_static_pressure"},
            ],
            "flow_boundaries_truncated": False,
        }
    )

    assert compact["flow_boundaries"] == [
        {"name": "inlet", "kind": "inlet_velocity"},
        {"name": "outlet", "kind": "outlet_static_pressure"},
    ]
