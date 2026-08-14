# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for the complete Mesh-ribbon Points group."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_POINTS_CAPABILITY_NAME = "mesh.points"
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
_GEOMETRY_SOURCE = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "label": _LABEL,
    },
    "required": ["object_name", "expected_state_sha256", "label"],
    "additionalProperties": False,
}
_VERTEX = {
    "type": "object",
    "properties": {
        "x_mm": {"type": "number", "minimum": -1.0e12, "maximum": 1.0e12},
        "y_mm": {"type": "number", "minimum": -1.0e12, "maximum": 1.0e12},
        "z_mm": {"type": "number", "minimum": -1.0e12, "maximum": 1.0e12},
    },
    "required": ["x_mm", "y_mm", "z_mm"],
    "additionalProperties": False,
}
_CUT_RESULT = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "enum": ["keep_inside", "keep_outside"]},
                "result_label": _LABEL,
            },
            "required": ["mode", "result_label"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "split"},
                "inside_result_label": _LABEL,
                "outside_result_label": _LABEL,
            },
            "required": ["mode", "inside_result_label", "outside_result_label"],
            "additionalProperties": False,
        },
    ]
}


def mesh_points_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_POINTS_CAPABILITY_NAME,
        description=(
            "Import, sample, structure, merge, or cut exact point clouds. Large data "
            "is processed detached in background; retained results commit once to History."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="import_point_cloud",
                description=(
                    "Ask the human for one supported point-cloud file, then import it "
                    "without exposing a filesystem path or recentering its coordinates."
                ),
                action_ids=frozenset({"Points_Import"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="HumanAuthorizedPointCloudInput",
                transaction_behavior="background",
                background_required=True,
                parameters={"type": "object", "properties": {}, "additionalProperties": False},
            ),
            NativeCapabilityVariant(
                operation="convert_to_points",
                description=(
                    "Sample 1 to 16 exact current-History geometry objects at the stated "
                    "maximum spacing, producing one linked point cloud per source."
                ),
                action_ids=frozenset({"Points_Convert"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryGeometrySources",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "geometry_sources": {
                            "type": "array",
                            "minItems": 1,
                            "maxItems": 16,
                            "items": _GEOMETRY_SOURCE,
                        },
                        "maximum_distance_mm": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": 1.0e6,
                        },
                    },
                    "required": ["geometry_sources", "maximum_distance_mm"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="structure",
                description=(
                    "Arrange one exact point cloud on an inferred X/Y grid. The explicit "
                    "coordinate tolerance controls grouping and ambiguous cells are rejected."
                ),
                action_ids=frozenset({"Points_Structure"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryPointCloud",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "target": _POINT_TARGET,
                        "result_label": _LABEL,
                        "coordinate_tolerance_mm": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": 1.0e6,
                        },
                    },
                    "required": ["target", "result_label", "coordinate_tolerance_mm"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="merge",
                description=(
                    "Merge 2 to 16 exact point clouds in document coordinates. Complete "
                    "aligned attributes are retained and incomplete attribute sets are reported."
                ),
                action_ids=frozenset({"Points_Merge"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryPointClouds",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "point_clouds": {
                            "type": "array",
                            "minItems": 2,
                            "maxItems": 16,
                            "items": _POINT_TARGET,
                        },
                        "result_label": _LABEL,
                    },
                    "required": ["point_clouds", "result_label"],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="polygon_cut",
                description=(
                    "Keep points inside or outside an explicit coplanar model-space polygon "
                    "prism, or retain both complementary results. Camera state is never used."
                ),
                action_ids=frozenset({"Points_PolyCut"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryPointCloudAndModelPolygon",
                transaction_behavior="background",
                background_required=True,
                parameters={
                    "type": "object",
                    "properties": {
                        "target": _POINT_TARGET,
                        "polygon": {
                            "type": "array",
                            "minItems": 3,
                            "maxItems": 256,
                            "items": _VERTEX,
                        },
                        "result": _CUT_RESULT,
                    },
                    "required": ["target", "polygon", "result"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_mesh_points_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_points_capability_definition())
