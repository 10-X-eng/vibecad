# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for human-authorized Mesh and point-cloud export."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_EXPORT_CAPABILITY_NAME = "mesh.export"
_OBJECT_REF = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "minLength": 1,
            "maxLength": 128,
            "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
        }
    },
    "required": ["object_name"],
    "additionalProperties": False,
}
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}
_MESH_TARGET = {
    "type": "object",
    "properties": {
        **_OBJECT_REF["properties"],
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_NONEMPTY_COUNT = {"type": "integer", "minimum": 1, "maximum": 2_147_483_647}


def mesh_export_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_EXPORT_CAPABILITY_NAME,
        description="Export one exact current Mesh or point cloud to a human-authorized file.",
        primary_classification="export",
        variants=(
            NativeCapabilityVariant(
                operation="export_mesh",
                description=(
                    "Ask the human for a destination, then export a detached copy "
                    "of the unchanged exact Mesh without blocking the UI thread."
                ),
                action_ids=frozenset({"Mesh_Export"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryMeshAndAuthorizedOutputPath",
                transaction_behavior="background_output",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "target": _MESH_TARGET,
                        "format": {
                            "type": "string",
                            "enum": [
                                "binary_stl",
                                "ascii_stl",
                                "binary_mesh",
                                "obj",
                                "off",
                                "ply",
                                "nastran",
                                "3mf",
                            ],
                        },
                    },
                    "required": [
                        "target",
                        "format",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="export_point_cloud",
                description=(
                    "Ask the human for a destination, then export a detached copy of "
                    "the unchanged exact point cloud with its structure and attributes."
                ),
                action_ids=frozenset({"Points_Export"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryPointCloudAndAuthorizedOutputPath",
                transaction_behavior="background_output",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "target": _OBJECT_REF,
                        "expected_state_sha256": _STATE_SHA256,
                        "expected_point_count": _NONEMPTY_COUNT,
                        "format": {"type": "string", "enum": ["asc", "pcd", "ply"]},
                    },
                    "required": [
                        "target",
                        "expected_state_sha256",
                        "expected_point_count",
                        "format",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_mesh_export_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_export_capability_definition())
