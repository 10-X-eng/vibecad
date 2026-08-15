# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for bounded exact Mesh Analyze reads."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_INSPECT_CAPABILITY_NAME = "mesh.inspect"
_OBJECT_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}
_EXACT_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_INDICES = {
    "type": "array",
    "items": {"type": "integer", "minimum": 0},
    "minItems": 1,
    "maxItems": 32,
    "uniqueItems": True,
}


def _parameters(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def mesh_inspect_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_INSPECT_CAPABILITY_NAME,
        description=(
            "Read exact current-History Mesh quality, facets, retained curvature "
            "samples, watertightness, or bounds without changing the document, "
            "selection, camera, ribbon, or structural revision."
        ),
        primary_classification="read",
        variants=(
            NativeCapabilityVariant(
                operation="evaluation",
                description=(
                    "Run the complete native defect evaluation on a detached copy "
                    "in the background and return bounded counts and samples."
                ),
                action_ids=frozenset({"Mesh_Evaluation"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="ExactCurrentHistoryMeshState",
                transaction_behavior="none",
                background_required=True,
                parameters=_parameters(
                    {
                        "target": _EXACT_TARGET,
                        "degeneration_mode": {
                            "type": "string",
                            "enum": ["strict", "mesh_tolerance"],
                        },
                    },
                    ("target", "degeneration_mode"),
                ),
            ),
            NativeCapabilityVariant(
                operation="evaluate_facet",
                description=(
                    "Read document-space vertices, topology, normal, area, aspect "
                    "ratio, and roundness for 1 to 32 exact facet indices."
                ),
                action_ids=frozenset({"Mesh_EvaluateFacet"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="ExactCurrentHistoryMeshFacetIndices",
                transaction_behavior="none",
                background_required=False,
                parameters=_parameters(
                    {"target": _EXACT_TARGET, "facet_indices": _INDICES},
                    ("target", "facet_indices"),
                ),
            ),
            NativeCapabilityVariant(
                operation="curvature_info",
                description=(
                    "Read principal, mean, Gaussian, and absolute curvature plus "
                    "directions for 1 to 32 vertices of one retained curvature plot."
                ),
                action_ids=frozenset({"Mesh_CurvatureInfo"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="ExactCurrentHistoryMeshCurvatureVertexIndices",
                transaction_behavior="none",
                background_required=False,
                parameters=_parameters(
                    {"curvature": _EXACT_TARGET, "vertex_indices": _INDICES},
                    ("curvature", "vertex_indices"),
                ),
            ),
            NativeCapabilityVariant(
                operation="evaluate_solid",
                description=(
                    "Read whether one exact Mesh is a solid and watertight, including "
                    "its exact open-edge count."
                ),
                action_ids=frozenset({"Mesh_EvaluateSolid"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="ExactCurrentHistoryMeshState",
                transaction_behavior="none",
                background_required=False,
                parameters=_parameters({"target": _EXACT_TARGET}, ("target",)),
            ),
            NativeCapabilityVariant(
                operation="bounding_box",
                description=(
                    "Read the exact document-space minimum, maximum, and size of one Mesh."
                ),
                action_ids=frozenset({"Mesh_BoundingBox"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="ExactCurrentHistoryMeshState",
                transaction_behavior="none",
                background_required=False,
                parameters=_parameters({"target": _EXACT_TARGET}, ("target",)),
            ),
        ),
    )


def register_mesh_inspect_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_inspect_capability_definition())
