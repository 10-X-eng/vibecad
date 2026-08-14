# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for durable FEM mesh definitions."""

from __future__ import annotations

from VibeCADNativeAnalyzeModelSchema import (
    _ANALYSIS_TARGET,
    _LABEL,
    _OBJECT_NAME,
    _STATE_SHA256,
)
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_MESH_CAPABILITY_NAME = "analyze.mesh"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_gmsh": "ExactFemGmshDefinitionAndActiveShape",
    "update_netgen": "ExactFemNetgenDefinitionAndActiveShape",
}
_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_SOURCE = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
    "description": "One exact active shape from the current Analyze snapshot.",
}
_SIZE = {
    "type": "number",
    "minimum": 0.0,
    "maximum": 1.0e12,
    "description": "Millimetres; zero lets the mesher choose automatically.",
}
_GMSH_SETTINGS = {
    "type": "object",
    "properties": {
        "maximum_size_mm": _SIZE,
        "minimum_size_mm": _SIZE,
        "element_dimension": {
            "type": "string",
            "enum": ["from_shape", "1d", "2d", "3d"],
        },
        "element_order": {"type": "string", "enum": ["first", "second"]},
    },
    "required": [
        "maximum_size_mm",
        "minimum_size_mm",
        "element_dimension",
        "element_order",
    ],
    "additionalProperties": False,
}
_USER_FINENESS = {
    "type": "object",
    "properties": {
        "growth_rate": {"type": "number", "minimum": 0.0, "maximum": 1.0e12},
        "curvature_safety": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0e12,
        },
        "segments_per_edge": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1.0e12,
        },
    },
    "required": ["growth_rate", "curvature_safety", "segments_per_edge"],
    "additionalProperties": False,
}
_NETGEN_BASE = {
    "maximum_size_mm": _SIZE,
    "minimum_size_mm": _SIZE,
    "second_order": {"type": "boolean"},
}
_NETGEN_SETTINGS = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                **_NETGEN_BASE,
                "fineness": {
                    "type": "string",
                    "enum": ["very_coarse", "coarse", "moderate", "fine", "very_fine"],
                },
            },
            "required": [
                "maximum_size_mm",
                "minimum_size_mm",
                "fineness",
                "second_order",
            ],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                **_NETGEN_BASE,
                "fineness": {"type": "string", "const": "user_defined"},
                "user_fineness": _USER_FINENESS,
            },
            "required": [
                "maximum_size_mm",
                "minimum_size_mm",
                "fineness",
                "second_order",
                "user_fineness",
            ],
            "additionalProperties": False,
        },
    ]
}


def _closed(properties: dict, required: tuple[str, ...], *, minimum: int = 0) -> dict:
    result = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
    if minimum:
        result["minProperties"] = minimum
    return result


def _create(settings: dict) -> dict:
    return _closed(
        {
            "analysis": _ANALYSIS_TARGET,
            "source": _SOURCE,
            "label": _LABEL,
            "settings": settings,
        },
        ("analysis", "source", "label", "settings"),
    )


def _update(settings: dict) -> dict:
    return _closed(
        {"target": _TARGET, "label": _LABEL, "source": _SOURCE, "settings": settings},
        ("target",),
        minimum=3,
    )


def _generate() -> dict:
    return _closed(
        {
            "target": _TARGET,
            "timeout_seconds": {
                "type": "integer",
                "minimum": 1,
                "maximum": 86400,
                "description": "Hard backend timeout; cancellation remains available through native.job.",
            },
        },
        ("target", "timeout_seconds"),
    )


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type=_UPDATE_EXACT_TARGET_BY_OPERATION.get(
            operation,
            "ExactFemAnalysisMeshDefinitionAndActiveShape",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def _generation_variant(
    operation: str,
    description: str,
    action_id: str,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type="ExactFemMeshDefinitionRefinementGraphAndBackendArtifact",
        transaction_behavior="background",
        background_required=True,
        parameters=_generate(),
    )


def analyze_mesh_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=ANALYZE_MESH_CAPABILITY_NAME,
        description=(
            "Create, edit, or asynchronously generate one durable solver mesh definition "
            "using the exact settings available in the human Gmsh and Netgen task panels."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "create_gmsh",
                "Create a Gmsh definition for one exact active shape; does not run Gmsh.",
                "FEM_MeshGmshFromShape",
                _create(_GMSH_SETTINGS),
            ),
            _variant(
                "create_netgen",
                "Create a Netgen definition for one exact active face or solid; does not mesh it.",
                "FEM_MeshNetgenFromShape",
                _create(_NETGEN_SETTINGS),
            ),
            _variant(
                "update_gmsh",
                "Edit one exact Gmsh definition and clear generated data only when invalidated.",
                "VibeCAD_AnalyzeUpdateGmshMesh",
                _update(_GMSH_SETTINGS),
            ),
            _variant(
                "update_netgen",
                "Edit one exact Netgen definition and clear generated data only when invalidated.",
                "VibeCAD_AnalyzeUpdateNetgenMesh",
                _update(_NETGEN_SETTINGS),
            ),
            _generation_variant(
                "generate_gmsh",
                "Run Gmsh off-thread from one frozen definition and commit only an exact verified artifact.",
                "VibeCAD_AnalyzeGenerateGmshMesh",
            ),
            _generation_variant(
                "generate_netgen",
                "Run Netgen off-thread from one frozen definition and commit only an exact verified artifact.",
                "VibeCAD_AnalyzeGenerateNetgenMesh",
            ),
        ),
    )


def register_analyze_mesh_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_mesh_capability_definition())
