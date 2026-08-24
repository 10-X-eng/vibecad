# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for completed CFD results."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _OBJECT_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_FLOW_RESULT = "analyze.flow_results"
ANALYZE_SHOW_FLOW = "analyze.show_flow"
ANALYZE_FLOW_PERFORMANCE = "analyze.flow_performance"
ANALYZE_COMPARE_FLOW = "analyze.compare_flow"

_BOUNDARY_NAME = {
    "type": "string",
    "description": "Exact result patch name from flow_boundaries.",
    "minLength": 1,
    "maxLength": 256,
    "pattern": "^[A-Za-z_][A-Za-z0-9_]*$",
}


def analyze_flow_result_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_FLOW_RESULT,
        description="Read pressure and velocity results from one OpenFOAM run.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="read",
                description="Read field ranges and area-averaged boundary values.",
                action_ids=frozenset({"VibeCAD_AnalyzeReadOpenFOAMFlow"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="CurrentNamedOpenFOAMResult",
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {"result_name": _OBJECT_NAME},
                    "required": ["result_name"],
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def analyze_show_flow_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_SHOW_FLOW,
        description="Show pressure or velocity from one OpenFOAM result.",
        primary_classification="view",
        variants=(
            NativeCapabilityVariant(
                operation="show",
                description="Color the result by pressure or velocity magnitude.",
                action_ids=frozenset({"VibeCAD_AnalyzeShowOpenFOAMFlow"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="CurrentNamedOpenFOAMResultPresentation",
                transaction_behavior="presentation",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "result_name": _OBJECT_NAME,
                        "field": {
                            "type": "string",
                            "enum": [
                                "pressure",
                                "velocity",
                                "turbulent_kinetic_energy",
                                "specific_dissipation_rate",
                                "turbulent_kinematic_viscosity",
                            ],
                        },
                    },
                    "required": ["result_name", "field"],
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def analyze_flow_performance_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_FLOW_PERFORMANCE,
        description="Measure GFA, EFA, flow, pressure drop, Cd, and continuity.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="measure",
                description="Measure one passage using explicit result boundaries.",
                action_ids=frozenset({"VibeCAD_AnalyzeMeasureOpenFOAMFlow"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="CurrentNamedOpenFOAMResultAndBoundaries",
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "result_name": _OBJECT_NAME,
                        "upstream_boundary": _BOUNDARY_NAME,
                        "downstream_boundary": _BOUNDARY_NAME,
                        "flow_boundary": _BOUNDARY_NAME,
                    },
                    "required": [
                        "result_name",
                        "upstream_boundary",
                        "downstream_boundary",
                        "flow_boundary",
                    ],
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def analyze_compare_flow_capability_definition() -> NativeCapabilityDefinition:
    passage = {
        "type": "object",
        "properties": {
            "result_name": _OBJECT_NAME,
            "upstream_boundary": _BOUNDARY_NAME,
            "downstream_boundary": _BOUNDARY_NAME,
            "flow_boundary": _BOUNDARY_NAME,
        },
        "required": [
            "result_name",
            "upstream_boundary",
            "downstream_boundary",
            "flow_boundary",
        ],
        "additionalProperties": False,
    }
    return NativeCapabilityDefinition(
        name=ANALYZE_COMPARE_FLOW,
        description="Compare two converged OpenFOAM passages at matching conditions.",
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="compare",
                description="Compare explicit baseline and candidate passages.",
                action_ids=frozenset({"VibeCAD_AnalyzeCompareOpenFOAMFlow"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="TwoCurrentNamedOpenFOAMResultsAndBoundaries",
                transaction_behavior="none",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "baseline": passage,
                        "candidate": passage,
                    },
                    "required": ["baseline", "candidate"],
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def analyze_flow_result_capability_definitions() -> tuple[
    NativeCapabilityDefinition, ...
]:
    return (
        analyze_flow_result_capability_definition(),
        analyze_show_flow_capability_definition(),
        analyze_flow_performance_capability_definition(),
        analyze_compare_flow_capability_definition(),
    )


def register_analyze_flow_result_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in analyze_flow_result_capability_definitions():
        registry.register_definition(definition)
