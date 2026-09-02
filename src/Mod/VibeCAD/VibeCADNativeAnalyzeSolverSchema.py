# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp Native contract for adding FEM solvers to an exact analysis."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _ANALYSIS_TARGET, _OBJECT_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_SOLVER_CAPABILITY_NAME = "analyze.solver"
SOLVER_TARGET = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
        },
        "expected_state_sha256": {
            "type": "string",
            "minLength": 64,
            "maxLength": 64,
            "pattern": r"^[0-9a-f]{64}$",
        },
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}


def _variant(
    operation: str,
    backend: str,
    action_id: str,
    purpose: str,
) -> NativeCapabilityVariant:
    properties = {
        "analysis": {
            "anyOf": [
                {**_OBJECT_NAME, "description": "Study analysis_name."},
                _ANALYSIS_TARGET,
            ],
            "description": "Study analysis_name or exact target.",
        },
        "label": {"type": "string", "minLength": 1, "maxLength": 160},
    }
    if operation == "create_openfoam":
        properties["momentum_model"] = {
            "type": "string",
            "enum": ["laminar", "k_omega_sst"],
            "default": "laminar",
        }
    return NativeCapabilityVariant(
        operation=operation,
        description=f"Add one {backend} solver for {purpose}.",
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type="ExactFemAnalysisAndHistory",
        transaction_behavior="document",
        background_required=False,
        parameters={
            "type": "object",
            "properties": properties,
            "required": ["analysis", "label"],
            "additionalProperties": False,
        },
    )


def analyze_solver_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_SOLVER_CAPABILITY_NAME,
        description=(
            "Add one selected FEM solver to a study."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "create_calculix",
                "CalculiX",
                "FEM_SolverCalculiX",
                "structural mechanics or heat transfer",
            ),
            _variant(
                "create_elmer",
                "Elmer",
                "FEM_SolverElmer",
                "equation-based multiphysics",
            ),
            _variant(
                "create_openfoam",
                "OpenFOAM",
                "FEM_SolverOpenFOAM",
                "computational fluid dynamics",
            ),
            _variant(
                "create_mystran",
                "Mystran",
                "FEM_SolverMystran",
                "linear structural mechanics",
            ),
            _variant(
                "create_z88",
                "Z88",
                "FEM_SolverZ88",
                "structural mechanics",
            ),
        ),
    )


def register_analyze_solver_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_solver_capability_definition())
