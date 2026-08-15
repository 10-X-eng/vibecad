# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact retained Mesh conversions."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_CONVERT_CAPABILITY_NAME = "mesh.convert"
_OBJECT_NAME = {
    "type": "string",
    "minLength": 1,
    "maxLength": 128,
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
}
_OBJECT_REF = {
    "type": "object",
    "properties": {"object_name": _OBJECT_NAME},
    "required": ["object_name"],
    "additionalProperties": False,
}
_LABEL = {"type": "string", "minLength": 1, "maxLength": 160}
_STATE_SHA256 = {
    "type": "string",
    "minLength": 64,
    "maxLength": 64,
    "pattern": r"^[0-9a-f]{64}$",
}
_VECTOR = {
    "type": "array",
    "items": {"type": "number", "minimum": -1_000_000.0, "maximum": 1_000_000.0},
    "minItems": 3,
    "maxItems": 3,
}
_DIRECTION = {
    "type": "array",
    "items": {"type": "number", "minimum": -1.0, "maximum": 1.0},
    "minItems": 3,
    "maxItems": 3,
}
_ANCHOR = {
    "type": "object",
    "properties": {"origin_mm": _VECTOR, "direction": _DIRECTION},
    "required": ["origin_mm", "direction"],
    "additionalProperties": False,
}


def mesh_convert_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_CONVERT_CAPABILITY_NAME,
        description=(
            "Create editable linked Mesh/shape conversions or a retained curve "
            "projected from ordered rays onto one exact current-History Mesh."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="shape_to_mesh",
                description=(
                    "Tessellate one exact shape or selected faces into an editable "
                    "linked Mesh using explicit standard deflection settings."
                ),
                action_ids=frozenset({"Mesh_FromPartShape"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryShapeOrFaces",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": _OBJECT_REF,
                        "subelements": {
                            "type": "array",
                            "items": {
                                "type": "string",
                                "pattern": r"^Face[1-9][0-9]*$",
                                "maxLength": 32,
                            },
                            "maxItems": 256,
                            "uniqueItems": True,
                        },
                        "label": _LABEL,
                        "linear_deflection_mm": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": 1_000_000.0,
                        },
                        "angular_deflection_degrees": {
                            "type": "number",
                            "exclusiveMinimum": 0.0,
                            "maximum": 180.0,
                        },
                        "relative": {"type": "boolean"},
                        "segments": {"type": "boolean"},
                    },
                    "required": [
                        "source",
                        "subelements",
                        "label",
                        "linear_deflection_mm",
                        "angular_deflection_degrees",
                        "relative",
                        "segments",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="mesh_to_shape",
                description=(
                    "Create one editable OCC shape linked to an unchanged exact "
                    "current-History Mesh with explicit sewing and tolerance."
                ),
                action_ids=frozenset({"MeshPart_ShapeFromMesh"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryMesh",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": _OBJECT_REF,
                        "expected_state_sha256": _STATE_SHA256,
                        "label": _LABEL,
                        "tolerance_mm": {
                            "type": "number",
                            "minimum": 0.000001,
                            "maximum": 10.0,
                        },
                        "sew_adjacent_faces": {"type": "boolean"},
                    },
                    "required": [
                        "source",
                        "expected_state_sha256",
                        "label",
                        "tolerance_mm",
                        "sew_adjacent_faces",
                    ],
                    "additionalProperties": False,
                },
            ),
            NativeCapabilityVariant(
                operation="curve_on_mesh",
                description=(
                    "Project 2 to 64 ordered rays onto one exact Mesh and retain "
                    "stable facet/barycentric anchors in an editable curve feature."
                ),
                action_ids=frozenset({"MeshPart_CurveOnMesh"}),
                surface_ids=frozenset({"mesh"}),
                exact_target_type="CurrentHistoryMeshAndOrderedPickRays",
                transaction_behavior="document",
                background_required=False,
                parameters={
                    "type": "object",
                    "properties": {
                        "source": _OBJECT_REF,
                        "expected_state_sha256": _STATE_SHA256,
                        "anchors": {
                            "type": "array",
                            "items": _ANCHOR,
                            "minItems": 2,
                            "maxItems": 64,
                        },
                        "label": _LABEL,
                        "closed": {"type": "boolean"},
                        "approximate": {"type": "boolean"},
                        "maximum_degree": {"type": "integer", "minimum": 1, "maximum": 8},
                        "continuity": {"type": "string", "enum": ["C0", "C1", "C2", "C3"]},
                        "tolerance_mm": {"type": "number", "minimum": 0.001, "maximum": 10.0},
                        "split_angle_degrees": {"type": "number", "minimum": 5.0, "maximum": 180.0},
                    },
                    "required": [
                        "source",
                        "expected_state_sha256",
                        "anchors",
                        "label",
                        "closed",
                        "approximate",
                        "maximum_degree",
                        "continuity",
                        "tolerance_mm",
                        "split_angle_degrees",
                    ],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_mesh_convert_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_convert_capability_definition())
