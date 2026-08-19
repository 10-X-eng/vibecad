# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native contract for cancellable detached FEM solver execution."""

from __future__ import annotations

from VibeCADNativeAnalyzeSolverSchema import SOLVER_TARGET
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME = "analyze.solver_execution"


def analyze_solver_execution_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_SOLVER_EXECUTION_CAPABILITY_NAME,
        description="Run one frozen FEM solver input and publish verified results.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="run",
                description="Run one exact supported FEM solver as a cancellable job.",
                action_ids=frozenset({"FEM_SolverRun"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="ExactFemSolverAnalysisHistoryAndInputArtifacts",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "target": SOLVER_TARGET,
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 86400,
                        },
                    },
                    "required": ["target", "timeout_seconds"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_analyze_solver_execution_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_solver_execution_capability_definition())
