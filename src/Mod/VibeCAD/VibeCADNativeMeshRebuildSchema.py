# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for the Mesh ribbon's reconstruction operations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_REBUILD_CAPABILITY_NAME = "mesh.rebuild"
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
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
_POINT_COUNT = {"type": "integer", "minimum": 1, "maximum": 2_147_483_647}
_POINT_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "expected_point_count": _POINT_COUNT,
    },
    "required": ["object_name", "expected_state_sha256", "expected_point_count"],
    "additionalProperties": False,
}
_STRUCTURED_TARGET = {
    "type": "object",
    "properties": {**_POINT_TARGET["properties"], "result_label": _LABEL},
    "required": [*_POINT_TARGET["required"], "result_label"],
    "additionalProperties": False,
}


def mesh_rebuild_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_REBUILD_CAPABILITY_NAME,
        description=(
            "Reconstruct one exact point cloud or triangulate exact structured point grids "
            "off the UI thread, then retain linked Mesh results in History."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="poisson_reconstruction",
                description="Reconstruct one exact point cloud with PCL Poisson settings.",
                action_ids=frozenset({"Reen_PoissonReconstruction"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryPointCloud",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "target": _POINT_TARGET,
                        "result_label": _LABEL,
                        "octree_depth": {"type": "integer", "minimum": 4, "maximum": 10},
                        "solver_divide": {"type": "integer", "minimum": 1, "maximum": 20},
                        "samples_per_node": {
                            "type": "number",
                            "minimum": 1.0,
                            "maximum": 50.0,
                        },
                        "normal_neighbors": {
                            "type": "integer",
                            "minimum": 3,
                            "maximum": 128,
                        },
                    },
                    "required": [
                        "target",
                        "result_label",
                        "octree_depth",
                        "solver_divide",
                        "samples_per_node",
                        "normal_neighbors",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="view_triangulation",
                description=(
                    "Triangulate 1 to 16 exact complete point grids, preserving holes "
                    "represented by non-finite grid cells and retaining one Mesh per source."
                ),
                action_ids=frozenset({"Reen_ViewTriangulation"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryStructuredPointClouds",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "structured_clouds": {
                            "type": "array",
                            "items": _STRUCTURED_TARGET,
                            "minItems": 1,
                            "maxItems": 16,
                        }
                    },
                    "required": ["structured_clouds"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_mesh_rebuild_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_rebuild_capability_definition())
