# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for a CFD material and OpenFOAM solver."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import _LABEL, _OBJECT_NAME
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_FLUID_MATERIAL = "analyze.fluid_material"
ANALYZE_OPENFOAM_SOLVER = "analyze.openfoam_solver"
_POSITIVE = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e30}
_ANALYSIS_NAME = {**_OBJECT_NAME, "description": "Analysis object name."}
_SOURCE_NAME = {**_OBJECT_NAME, "description": "Geometry object name."}


def _definition(
    name: str,
    description: str,
    action_id: str,
    parameters: dict,
) -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=name,
        description=description,
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description=description,
                action_ids=frozenset({action_id}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type="CurrentNamedFemAnalysis",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters,
                provider_supplemental=True,
            ),
        ),
    )


def analyze_cfd_lifecycle_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    fluid_values = {
        "name": {"type": "string", "minLength": 1, "maxLength": 160},
        "density_kg_m3": _POSITIVE,
        "kinematic_viscosity_m2_s": _POSITIVE,
        "label": {**_LABEL, "default": "Fluid material"},
    }
    return (
        NativeCapabilityDefinition(
            name=ANALYZE_FLUID_MATERIAL,
            description="Create or edit fluid properties for one solid domain.",
            primary_classification="mutation",
            variants=(
                NativeCapabilityVariant(
                    operation="create",
                    description="Create fluid properties for one solid domain.",
                    action_ids=frozenset({"VibeCAD_AnalyzeCreateFluidMaterial"}),
                    surface_ids=frozenset({"analyze"}),
                    exact_target_type="CurrentNamedFemAnalysis",
                    transaction_behavior="document",
                    background_required=False,
                    parameters={
                        "type": "object",
                        "properties": {
                            "analysis_name": _ANALYSIS_NAME,
                            "source_name": _SOURCE_NAME,
                            **fluid_values,
                        },
                        "required": [
                            "analysis_name",
                            "source_name",
                            "name",
                            "density_kg_m3",
                            "kinematic_viscosity_m2_s",
                        ],
                        "additionalProperties": False,
                    },
                    provider_supplemental=True,
                ),
                NativeCapabilityVariant(
                    operation="update",
                    description="Edit one current fluid material.",
                    action_ids=frozenset({"FEM_MaterialEditor"}),
                    surface_ids=frozenset({"analyze"}),
                    exact_target_type="CurrentNamedFluidMaterial",
                    transaction_behavior="document",
                    background_required=False,
                    parameters={
                        "type": "object",
                        "properties": {
                            "material_name": _OBJECT_NAME,
                            **fluid_values,
                        },
                        "required": ["material_name"],
                        "minProperties": 3,
                        "additionalProperties": False,
                    },
                    provider_supplemental=True,
                ),
            ),
        ),
        _definition(
            ANALYZE_OPENFOAM_SOLVER,
            "Add an OpenFOAM solver to one fluid study.",
            "VibeCAD_AnalyzeCreateOpenFOAMSolver",
            {
                "type": "object",
                "properties": {
                    "analysis_name": _ANALYSIS_NAME,
                    "momentum_model": {
                        "type": "string",
                        "enum": ["laminar", "k_omega_sst"],
                        "default": "laminar",
                    },
                    "label": {**_LABEL, "default": "OpenFOAM"},
                },
                "required": ["analysis_name"],
                "additionalProperties": False,
            },
        ),
    )


def register_analyze_cfd_lifecycle_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in analyze_cfd_lifecycle_capability_definitions():
        registry.register_definition(definition)
