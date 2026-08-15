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
        description=(
            "Freeze one exact FEM solver input, run its external backend with bounded "
            "progress and cancellation, then publish verified results only if the "
            "document is still exact."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="run",
                description=(
                    "Run one exact supported FEM solver without blocking the UI; use "
                    "native.job for status or cancellation."
                ),
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
