# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for retained native Mesh curvature plots."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_CURVATURE_CAPABILITY_NAME = "mesh.curvature"
_TARGET = {
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
        "label": {"type": "string", "minLength": 1, "maxLength": 160},
    },
    "required": ["object_name", "expected_state_sha256", "label"],
    "additionalProperties": False,
}


def mesh_curvature_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_CURVATURE_CAPABILITY_NAME,
        description=(
            "Create retained source-linked per-vertex curvature plots for exact "
            "current-History Meshes in one atomic History operation."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="vertex_curvature",
                description=(
                    "Calculate one recomputable native Mesh::Curvature result per target."
                ),
                action_ids=frozenset({"Mesh_VertexCurvature"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="ExactCurrentHistoryMeshStatesWithResultLabels",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "targets": {
                            "type": "array",
                            "items": _TARGET,
                            "minItems": 1,
                            "maxItems": 16,
                        }
                    },
                    "required": ["targets"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_mesh_curvature_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_curvature_capability_definition())
