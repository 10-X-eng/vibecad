# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for running one current FEM solver."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _OBJECT_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_RUN_SOLVER = "analyze.run_solver"


def analyze_run_solver_capability_definition() -> NativeCapabilityDefinition:
    description = "Run one current FEM solver and import its results."
    return NativeCapabilityDefinition(
        name=ANALYZE_RUN_SOLVER,
        description=description,
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="run",
                description=description,
                action_ids=frozenset({"VibeCAD_AnalyzeRunCurrentSolver"}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="CurrentNamedFemSolver",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "solver_name": _OBJECT_NAME,
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 86400,
                            "default": 3600,
                        },
                    },
                    "required": ["solver_name"],
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def register_analyze_run_solver_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_run_solver_capability_definition())
