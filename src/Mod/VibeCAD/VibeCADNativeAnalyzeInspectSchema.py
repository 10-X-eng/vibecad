# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded read contract for FEM analyses, materials, elements, and cards."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeAnalyzeModelSchema import _ANALYSIS_TARGET, _MATERIAL_TARGET
from VibeCADNativeAnalyzeGeometrySchema import _ELEMENT_TARGET
from VibeCADNativeAnalyzeElectromagneticSchema import _CONSTRAINT_TARGET
from VibeCADNativeAnalyzeFluidSchema import _TARGET as _FLUID_TARGET
from VibeCADNativeAnalyzeGeometricalSchema import _TARGET as _GEOMETRICAL_TARGET
from VibeCADNativeAnalyzeSupportSchema import _TARGET as _SUPPORT_TARGET
from VibeCADNativeAnalyzeConnectionSchema import _TARGET as _CONNECTION_TARGET
from VibeCADNativeAnalyzeLoadSchema import _TARGET as _LOAD_TARGET
from VibeCADNativeAnalyzeThermalSchema import _TARGET as _THERMAL_TARGET
from VibeCADNativeAnalyzeMeshSchema import _TARGET as _MESH_TARGET
from VibeCADNativeAnalyzeMeshRefinementSchema import _TARGET as _REFINEMENT_TARGET
from VibeCADNativeAnalyzeMeshOutputSchema import FEM_MESH_OBJECT_TARGET
from VibeCADNativeAnalyzeSolverSchema import SOLVER_TARGET
from VibeCADNativeAnalyzeEquationSchema import EQUATION_TARGET
from VibeCADNativeAnalyzeResultState import RESULT_TARGET


ANALYZE_INSPECT_CAPABILITY_NAME = "analyze.inspect"

_EXACT_TARGET_BY_OPERATION = {
    "analysis": "ExactFemAnalysisState",
    "material": "ExactFemMaterialState",
    "material_catalog": "BoundedMaterialCatalogQuery",
    "element_definition": "ExactFemElementDefinitionState",
    "electromagnetic_constraint": "ExactFemElectromagneticConstraintState",
    "fluid_constraint": "ExactFemFluidConstraintState",
    "geometrical_feature": "ExactFemGeometricalFeatureState",
    "support_condition": "ExactFemSupportConditionState",
    "connection": "ExactFemConnectionState",
    "load": "ExactFemMechanicalLoadState",
    "thermal_condition": "ExactFemThermalConditionState",
    "fem_mesh_definition": "ExactFemMeshDefinitionState",
    "mesh_refinement": "ExactFemMeshRefinementState",
    "fem_mesh_elements": "ExactActiveFemMeshContentAndHistory",
    "solver": "ExactFemSolverState",
    "equation": "ExactElmerEquationState",
    "result": "ExactFemResultOrPostState",
}


def _variant(
    operation: str, description: str, action_id: str, parameters: dict
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type=_EXACT_TARGET_BY_OPERATION.get(
            operation,
            "ExactFemAnalysisMaterialOrCatalogQuery",
        ),
        transaction_behavior="none",
        background_required=False,
        parameters=parameters,
    )


def analyze_inspect_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_INSPECT_CAPABILITY_NAME,
        description=(
            "Read one exact FEM analysis, material, element definition, electromagnetic or "
            "fluid constraint, geometrical feature, solver, equation, result or post object, "
            "or a bounded FEM element page; or search the installed material catalog."
        ),
        primary_classification="read",
        variants=(
            _variant(
                "analysis",
                "Read exact membership and readiness counts for one current FEM analysis.",
                "VibeCAD_AnalyzeReadAnalysis",
                {
                    "type": "object",
                    "properties": {"target": _ANALYSIS_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "material",
                "Read normalized physical values and exact references for one FEM material.",
                "VibeCAD_AnalyzeReadMaterial",
                {
                    "type": "object",
                    "properties": {"target": _MATERIAL_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "material_catalog",
                "Search installed material cards by words and category; returns at most 25 exact UUIDs.",
                "VibeCAD_AnalyzeSearchMaterialCatalog",
                {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "maxLength": 160},
                        "category": {
                            "type": "string",
                            "enum": ["any", "solid", "fluid"],
                        },
                        "limit": {"type": "integer", "minimum": 1, "maximum": 25},
                    },
                    "required": ["query", "category", "limit"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "element_definition",
                "Read one normalized beam, shell, or 1D fluid definition and its exact assignments.",
                "VibeCAD_AnalyzeReadElementDefinition",
                {
                    "type": "object",
                    "properties": {"target": _ELEMENT_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "electromagnetic_constraint",
                "Read one normalized electromagnetic constraint and its exact assignments.",
                "VibeCAD_AnalyzeReadElectromagneticConstraint",
                {
                    "type": "object",
                    "properties": {"target": _CONSTRAINT_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "fluid_constraint",
                "Read one normalized initial or boundary fluid constraint and its exact assignments.",
                "VibeCAD_AnalyzeReadFluidConstraint",
                {
                    "type": "object",
                    "properties": {"target": _FLUID_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "geometrical_feature",
                "Read one normalized plane rotation, section print, or local coordinate system.",
                "VibeCAD_AnalyzeReadGeometricalFeature",
                {
                    "type": "object",
                    "properties": {"target": _GEOMETRICAL_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "support_condition",
                "Read one normalized fixed, rigid-body, displacement, or spring support condition.",
                "VibeCAD_AnalyzeReadSupportCondition",
                {
                    "type": "object",
                    "properties": {"target": _SUPPORT_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "connection",
                "Read one normalized contact or tie connection with exact slave/master roles.",
                "VibeCAD_AnalyzeReadConnection",
                {
                    "type": "object",
                    "properties": {"target": _CONNECTION_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "load",
                "Read one normalized force, pressure, centrifugal, or gravity load.",
                "VibeCAD_AnalyzeReadLoad",
                {
                    "type": "object",
                    "properties": {"target": _LOAD_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "thermal_condition",
                "Read one normalized initial, surface, nodal, or body thermal condition.",
                "VibeCAD_AnalyzeReadThermalCondition",
                {
                    "type": "object",
                    "properties": {"target": _THERMAL_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "fem_mesh_definition",
                "Read one exact Gmsh or Netgen definition, settings, source, and generated topology.",
                "VibeCAD_AnalyzeReadMeshDefinition",
                {
                    "type": "object",
                    "properties": {"target": _MESH_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "mesh_refinement",
                "Read one exact mesh refinement, typed values, geometry, and owning mesh.",
                "VibeCAD_AnalyzeReadMeshRefinement",
                {
                    "type": "object",
                    "properties": {"target": _REFINEMENT_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "fem_mesh_elements",
                "Read one bounded page of FEM element IDs, connectivity, centroids, and bounds.",
                "VibeCAD_AnalyzeReadFemMeshElements",
                {
                    "type": "object",
                    "properties": {
                        "target": FEM_MESH_OBJECT_TARGET,
                        "element_kind": {
                            "type": "string",
                            "enum": ["primary", "volume", "face", "edge", "zero_d", "ball"],
                        },
                        "offset": {"type": "integer", "minimum": 0},
                        "page_size": {"type": "integer", "minimum": 1, "maximum": 64},
                    },
                    "required": ["target", "element_kind", "offset", "page_size"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "solver",
                "Read one exact FEM solver, its backend implementation, owner, and settings.",
                "VibeCAD_AnalyzeReadSolver",
                {
                    "type": "object",
                    "properties": {"target": SOLVER_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "equation",
                "Read one exact Elmer equation, its owning solver, priority, and settings.",
                "VibeCAD_AnalyzeReadEquation",
                {
                    "type": "object",
                    "properties": {"target": EQUATION_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "result",
                (
                    "Read one exact FEM result or post-processing object with bounded "
                    "field ranges, ownership, frames, settings, and presentation state."
                ),
                "VibeCAD_AnalyzeReadResult",
                {
                    "type": "object",
                    "properties": {"target": RESULT_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "linearized_stress",
                (
                    "Read membrane, membrane-plus-bending, total, and peak-residual "
                    "stress summaries from one exact stress line without returning arrays."
                ),
                "FEM_PostFilterLinearizedStresses",
                {
                    "type": "object",
                    "properties": {"target": RESULT_TARGET},
                    "required": ["target"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_analyze_inspect_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_inspect_capability_definition())
