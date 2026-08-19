# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native provider contract for the Aero ribbon (aero.solve)."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)

AERO_SOLVE_CAPABILITY_NAME = "aero.solve"
AERO_SURFACES = frozenset({"aero", "model"})

_EMPTY = {
    "type": "object",
    "properties": {},
    "required": [],
    "additionalProperties": False,
}


def _variant(
    operation: str,
    description: str,
    action_id: str,
    *,
    transaction_behavior: str,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=AERO_SURFACES,
        exact_target_type="AeroDocument",
        transaction_behavior=transaction_behavior,
        background_required=False,
        parameters=_EMPTY,
    )


def aero_solve_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=AERO_SOLVE_CAPABILITY_NAME,
        description=(
            "Solve, report, export, or repair VibeCAD Aero with wrapper stamps. "
            "Analyze does not move CAD. Numbers are not airworthy."
        ),
        primary_classification="mutation",
        preserve_operation_branches=True,
        variants=(
            _variant(
                "analyze",
                "Solve section+3D+hover and write AeroReport. Does not repair CAD.",
                "VibeCADAero_Analyze",
                transaction_behavior="document",
            ),
            _variant(
                "section",
                "NeuralFoil 2D section only.",
                "VibeCADAero_Section",
                transaction_behavior="document",
            ),
            _variant(
                "vlm",
                "AeroSandbox VLM + AeroBuildup only.",
                "VibeCADAero_VLM",
                transaction_behavior="document",
            ),
            _variant(
                "export_jsbsim",
                "Export JSBSim from the last AeroReport. Does not re-solve.",
                "VibeCADAero_ExportJSBSim",
                transaction_behavior="output",
            ),
            _variant(
                "report",
                "Write markdown/spreadsheet from the last AeroReport. Does not re-solve.",
                "VibeCADAero_Report",
                transaction_behavior="document",
            ),
            _variant(
                "propose_repairs",
                "Preview bounded pitch-stability CAD changes. Does not apply them.",
                "VibeCADAero_ProposeRepairs",
                transaction_behavior="document",
            ),
            _variant(
                "apply_repairs",
                "Apply the current repair preview if the document revision matches.",
                "VibeCADAero_ApplyRepairs",
                transaction_behavior="document",
            ),
            _variant(
                "flight_card",
                "Compute mass, loading, hover margin, tail volume, endurance. Not airworthy.",
                "VibeCADAero_FlightCard",
                transaction_behavior="none",
            ),
        ),
    )


def register_aero_solve_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(aero_solve_capability_definition())
