# SPDX-License-Identifier: LGPL-2.1-or-later

"""Sharp Native contract for creating equations on an exact Elmer solver."""

from __future__ import annotations

from VibeCADNativeAnalyzeSolverSchema import SOLVER_TARGET
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_EQUATION_CAPABILITY_NAME = "analyze.equation"
EQUATION_TARGET = SOLVER_TARGET
_ACTIONS = {
    "elasticity": "FEM_EquationElasticity",
    "deformation": "FEM_EquationDeformation",
    "electrostatic": "FEM_EquationElectrostatic",
    "electric_force": "FEM_EquationElectricforce",
    "magnetodynamic": "FEM_EquationMagnetodynamic",
    "magnetodynamic_2d": "FEM_EquationMagnetodynamic2D",
    "static_current": "FEM_EquationStaticCurrent",
    "flow": "FEM_EquationFlow",
    "flux": "FEM_EquationFlux",
    "heat": "FEM_EquationHeat",
}


def _variant(kind: str, action_id: str) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=f"create_{kind}",
        description=(
            f"Create one {kind.replace('_', ' ')} equation with the stock Elmer defaults "
            "on an exact Elmer solver."
        ),
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type="ExactElmerSolverAndHistory",
        transaction_behavior="document",
        background_required=False,
        parameters={
            "type": "object",
            "properties": {
                "solver": SOLVER_TARGET,
                "label": {"type": "string", "minLength": 1, "maxLength": 160},
            },
            "required": ["solver", "label"],
            "additionalProperties": False,
        },
    )


def analyze_equation_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_EQUATION_CAPABILITY_NAME,
        description=(
            "Create one explicitly selected stock Elmer equation as an owned resource of "
            "one exact Elmer solver."
        ),
        primary_classification="mutation",
        variants=tuple(_variant(kind, action_id) for kind, action_id in _ACTIONS.items()),
    )


def register_analyze_equation_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_equation_capability_definition())
