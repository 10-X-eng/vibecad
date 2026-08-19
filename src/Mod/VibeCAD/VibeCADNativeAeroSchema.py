# SPDX-License-Identifier: LGPL-2.1-or-later

"""Native provider contracts for Aero. Families follow primary class."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)

AERO_SOLVE_CAPABILITY_NAME = "aero.solve"
AERO_EXPORT_CAPABILITY_NAME = "aero.export"
AERO_INSPECT_CAPABILITY_NAME = "aero.inspect"
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
        exact_target_type=None,
        transaction_behavior=transaction_behavior,
        background_required=False,
        parameters=_EMPTY,
    )


def aero_solve_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=AERO_SOLVE_CAPABILITY_NAME,
        description=(
            "Solve or repair VibeCAD Aero with wrapper stamps. Analyze does not "
            "move CAD. Numbers are not airworthy."
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
                "report",
                "Write markdown/spreadsheet from the last AeroReport.",
                "VibeCADAero_Report",
                transaction_behavior="document",
            ),
            _variant(
                "propose_repairs",
                "Preview bounded pitch-stability CAD changes.",
                "VibeCADAero_ProposeRepairs",
                transaction_behavior="document",
            ),
            _variant(
                "apply_repairs",
                "Apply the repair preview if Native revision and config match.",
                "VibeCADAero_ApplyRepairs",
                transaction_behavior="document",
            ),
        ),
    )


def aero_export_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=AERO_EXPORT_CAPABILITY_NAME,
        description="Export the last AeroReport as a JSBSim plant. Does not re-solve.",
        primary_classification="export",
        variants=(
            _variant(
                "export_jsbsim",
                "Write JSBSim XML from the last AeroReport.",
                "VibeCADAero_ExportJSBSim",
                transaction_behavior="output",
            ),
        ),
    )


def aero_inspect_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=AERO_INSPECT_CAPABILITY_NAME,
        description=(
            "Read the Aero flight card: mass, loading, hover margin, tail volume. "
            "Not airworthy."
        ),
        primary_classification="read",
        variants=(
            _variant(
                "flight_card",
                "Compute mass, loading, hover margin, tail volume, endurance.",
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
    registry.register_definition(aero_export_capability_definition())
    registry.register_definition(aero_inspect_capability_definition())
