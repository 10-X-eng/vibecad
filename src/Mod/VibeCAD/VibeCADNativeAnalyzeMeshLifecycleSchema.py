# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contracts for the Gmsh mesh lifecycle."""

from __future__ import annotations

from VibeCADNativeAnalyzeMeshSchema import (
    _LABEL,
    _OBJECT_NAME,
    _SIZE,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_GMSH_MESH = "analyze.gmsh_mesh"
ANALYZE_SOLID_MESH = "analyze.solid_mesh"
ANALYZE_FLOW_MESH = "analyze.flow_mesh"
ANALYZE_EDIT_GMSH_MESH = "analyze.edit_gmsh_mesh"
ANALYZE_GENERATE_GMSH = "analyze.generate_gmsh"
_CFD_MAXIMUM_SIZE = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1.0e12,
    "description": (
        "Maximum element size in millimetres; choose a positive value from the "
        "domain dimensions."
    ),
}
_ANALYSIS_NAME = {**_OBJECT_NAME, "description": "Analysis object name."}
_SOURCE_NAME = {**_OBJECT_NAME, "description": "Geometry object name."}
_ELEMENT_ORDER = {
    "type": "string",
    "enum": ["second", "first"],
    "default": "second",
    "description": "Second-order is quadratic for bending; first-order is linear.",
}


def _mesh_create_definition(
    name: str,
    description: str,
    action_id: str,
    *,
    flow: bool,
) -> NativeCapabilityDefinition:
    properties = {
        "analysis_name": _ANALYSIS_NAME,
        "source_name": _SOURCE_NAME,
        "maximum_size_mm": _CFD_MAXIMUM_SIZE,
        "minimum_size_mm": {**_SIZE, "default": 0.0},
        "label": {**_LABEL, "default": "Gmsh mesh"},
    }
    if not flow:
        properties["element_order"] = _ELEMENT_ORDER
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
                exact_target_type="ExactFemAnalysisAndActiveShape",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": properties,
                    "required": [
                        "analysis_name",
                        "source_name",
                        "maximum_size_mm",
                    ],
                    "additionalProperties": False,
                },
                provider_supplemental=True,
            ),
        ),
    )


def _definition(
    name: str,
    description: str,
    action_id: str,
    exact_target_type: str,
    parameters: dict,
    *,
    background_required: bool,
    operation: str | None = None,
) -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=name,
        description=description,
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation=operation or (
                    "create" if name == ANALYZE_GMSH_MESH else "generate"
                ),
                description=description,
                action_ids=frozenset({action_id}),
                surface_ids=frozenset({"analyze"}),
                exact_target_type=exact_target_type,
                transaction_behavior=(
                    "background" if background_required else "document"
                ),
                background_required=background_required,
                parameters=parameters,
                provider_supplemental=True,
            ),
        ),
    )


def analyze_mesh_lifecycle_capability_definitions() -> tuple[NativeCapabilityDefinition, ...]:
    return (
        _mesh_create_definition(
            ANALYZE_SOLID_MESH,
            "Create a Gmsh volume mesh for solid analysis.",
            "VibeCAD_AnalyzeCreateSolidMeshFocused",
            flow=False,
        ),
        _mesh_create_definition(
            ANALYZE_FLOW_MESH,
            "Create a first-order Gmsh volume mesh for OpenFOAM.",
            "VibeCAD_AnalyzeCreateFlowMeshFocused",
            flow=True,
        ),
        NativeCapabilityDefinition(
            name=ANALYZE_GMSH_MESH,
            description="Create or edit a first-order Gmsh volume mesh.",
            primary_classification="mutation",
            variants=(
                NativeCapabilityVariant(
                    operation="create",
                    description="Create a first-order Gmsh volume mesh for one fluid domain.",
                    action_ids=frozenset({"VibeCAD_AnalyzeCreateGmshMesh"}),
                    surface_ids=frozenset({"analyze"}),
                    exact_target_type="ExactFemAnalysisAndActiveShape",
                    transaction_behavior="document",
                    background_required=False,
                    parameters={
                        "type": "object",
                        "properties": {
                            "analysis_name": _ANALYSIS_NAME,
                            "source_name": _SOURCE_NAME,
                            "maximum_size_mm": _CFD_MAXIMUM_SIZE,
                            "minimum_size_mm": {**_SIZE, "default": 0.0},
                            "element_order": {
                                "type": "string",
                                "const": "first",
                                "default": "first",
                            },
                            "label": {**_LABEL, "default": "Gmsh mesh"},
                        },
                        "required": [
                            "analysis_name",
                            "source_name",
                            "maximum_size_mm",
                        ],
                        "additionalProperties": False,
                    },
                    provider_supplemental=True,
                ),
                NativeCapabilityVariant(
                    operation="update",
                    description="Edit one current Gmsh volume mesh.",
                    action_ids=frozenset({"FEM_MeshGmshFromShape"}),
                    surface_ids=frozenset({"analyze"}),
                    exact_target_type="CurrentNamedFemGmshDefinition",
                    transaction_behavior="document",
                    background_required=False,
                    parameters={
                        "type": "object",
                        "properties": {
                            "mesh_name": _OBJECT_NAME,
                            "maximum_size_mm": _CFD_MAXIMUM_SIZE,
                            "minimum_size_mm": _SIZE,
                            "label": _LABEL,
                        },
                        "required": ["mesh_name"],
                        "minProperties": 3,
                        "additionalProperties": False,
                    },
                    provider_supplemental=True,
                ),
            ),
        ),
        _definition(
            ANALYZE_EDIT_GMSH_MESH,
            "Edit one current Gmsh mesh definition.",
            "VibeCAD_AnalyzeUpdateGmshMeshFocused",
            "CurrentNamedFemGmshDefinition",
            {
                "type": "object",
                "properties": {
                    "mesh_name": {
                        **_OBJECT_NAME,
                        "description": "Gmsh mesh_name.",
                    },
                    "maximum_size_mm": _CFD_MAXIMUM_SIZE,
                    "minimum_size_mm": _SIZE,
                    "element_order": _ELEMENT_ORDER,
                },
                "required": ["mesh_name"],
                "minProperties": 2,
                "additionalProperties": False,
            },
            background_required=False,
            operation="edit",
        ),
        _definition(
            ANALYZE_GENERATE_GMSH,
            "Generate one exact Gmsh mesh definition.",
            "VibeCAD_AnalyzeGenerateCurrentGmshMesh",
            "ExactFemGmshDefinition",
            {
                "type": "object",
                "properties": {
                    "mesh_name": _OBJECT_NAME,
                    "timeout_seconds": {
                        "type": "integer",
                        "minimum": 1,
                        "maximum": 86400,
                        "default": 300,
                    },
                },
                "required": ["mesh_name"],
                "additionalProperties": False,
            },
            background_required=True,
        ),
    )


def register_analyze_mesh_lifecycle_capability_definitions(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    for definition in analyze_mesh_lifecycle_capability_definitions():
        registry.register_definition(definition)
