# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp Native contract for FEM result graph operations."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _ANALYSIS_TARGET
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_RESULTS_CAPABILITY_NAME = "analyze.results"


def analyze_results_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_RESULTS_CAPABILITY_NAME,
        description=(
            "Operate on one exact FEM analysis result graph without exposing native arrays."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="purge",
                description=(
                    "Atomically purge every solver result and post-processing object from "
                    "one exact analysis while retaining its model, meshes, and solvers."
                ),
                action_ids=frozenset({"FEM_ResultsPurge"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactFemAnalysisResultGraphAndHistory",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "analysis": _ANALYSIS_TARGET,
                        "expected_result_graph_sha256": {
                            "type": "string",
                            "minLength": 64,
                            "maxLength": 64,
                            "pattern": r"^[0-9a-f]{64}$",
                        },
                        "expected_result_object_count": {
                            "type": "integer",
                            "minimum": 1,
                        },
                    },
                    "required": [
                        "analysis",
                        "expected_result_graph_sha256",
                        "expected_result_object_count",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_analyze_results_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_results_capability_definition())
