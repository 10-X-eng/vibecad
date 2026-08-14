# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for the Model ribbon's Surface operations."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    OBJECT_NAME_SCHEMA,
    parameters_schema,
)


_MODEL_SURFACE = frozenset({"model"})
_ELEMENT_NAME = {
    "type": "string",
    "maxLength": 64,
    "pattern": r"^(?:Vertex|Edge|Face)[1-9][0-9]*$",
}
_FACE_NAME = {
    "type": "string",
    "maxLength": 64,
    "pattern": r"^Face[1-9][0-9]*$",
}
_EDGE_NAME = {
    "type": "string",
    "maxLength": 64,
    "pattern": r"^Edge[1-9][0-9]*$",
}
_POSITIVE_TOLERANCE = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1_000.0,
}


def _filling_definition() -> dict[str, Any]:
    constraint = parameters_schema(
        {
            "kind": {
                "type": "string",
                "enum": ["boundary_edge", "curve_edge", "face", "point"],
            },
            "object_name": OBJECT_NAME_SCHEMA,
            "subelement": _ELEMENT_NAME,
            "support_face": _FACE_NAME,
            "continuity": {"type": "string", "enum": ["C0", "G1", "G2"]},
        },
        ("kind", "object_name", "subelement"),
    )
    initial_face = parameters_schema(
        {"object_name": OBJECT_NAME_SCHEMA, "face": _FACE_NAME},
        ("object_name", "face"),
    )
    return parameters_schema(
        {
            "constraints": {
                "type": "array",
                "items": constraint,
                "minItems": 1,
                "maxItems": 256,
                "uniqueItems": True,
            },
            "initial_face": initial_face,
            "degree": {"type": "integer", "minimum": 2, "maximum": 25},
            "points_on_curve": {
                "type": "integer",
                "minimum": 2,
                "maximum": 1_000,
            },
            "iterations": {"type": "integer", "minimum": 1, "maximum": 1_000},
            "anisotropy": {"type": "boolean"},
            "tolerance_2d": {
                **_POSITIVE_TOLERANCE,
                "description": "Dimensionless 2D parametric filling tolerance.",
            },
            "tolerance_3d": {
                **_POSITIVE_TOLERANCE,
                "description": "Model-space filling tolerance in millimetres.",
            },
            "angular_tolerance": {
                "type": "number",
                "exclusiveMinimum": 0.0,
                "maximum": 3.141592653589793,
                "description": "G1 angular tolerance in radians.",
            },
            "curvature_tolerance": {
                **_POSITIVE_TOLERANCE,
                "description": "G2 curvature tolerance in inverse millimetres.",
            },
            "maximum_degree": {
                "type": "integer",
                "minimum": 2,
                "maximum": 25,
            },
            "maximum_segments": {
                "type": "integer",
                "minimum": 1,
                "maximum": 10_000,
            },
        },
        ("constraints",),
    )


def _geometric_fill_definition() -> dict[str, Any]:
    boundary = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "edge": _EDGE_NAME,
            "reversed": {"type": "boolean"},
        },
        ("object_name", "edge"),
    )
    return parameters_schema(
        {
            "boundaries": {
                "type": "array",
                "items": boundary,
                "minItems": 2,
                "maxItems": 4,
                "uniqueItems": True,
            },
            "style": {
                "type": "string",
                "enum": ["stretched", "coons", "curved"],
            },
        },
        ("boundaries",),
    )


def _sections_definition() -> dict[str, Any]:
    section = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "edge": _EDGE_NAME,
        },
        ("object_name", "edge"),
    )
    return parameters_schema(
        {
            "sections": {
                "type": "array",
                "items": section,
                "minItems": 2,
                "maxItems": 256,
                "uniqueItems": True,
            }
        },
        ("sections",),
    )


def _extend_definition() -> dict[str, Any]:
    extension = {"type": "number", "minimum": -0.5, "maximum": 10.0}
    samples = {"type": "integer", "minimum": 2, "maximum": 512}
    return parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "face": _FACE_NAME,
            "u_negative": extension,
            "u_positive": extension,
            "u_symmetric": {"type": "boolean"},
            "v_negative": extension,
            "v_positive": extension,
            "v_symmetric": {"type": "boolean"},
            "tolerance": {
                "type": "number",
                "minimum": 0.0,
                "maximum": 10.0,
                "description": "Model-space extension tolerance in millimetres.",
            },
            "samples_u": samples,
            "samples_v": samples,
        },
        ("object_name", "face"),
    )


def _curve_on_mesh_definition() -> dict[str, Any]:
    vector = {
        "type": "array",
        "items": {"type": "number"},
        "minItems": 3,
        "maxItems": 3,
    }
    anchor = parameters_schema(
        {"origin_mm": vector, "direction": vector},
        ("origin_mm", "direction"),
    )
    return parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "anchors": {
                "type": "array",
                "items": anchor,
                "minItems": 2,
                "maxItems": 64,
            },
            "closed": {"type": "boolean"},
            "approximate": {"type": "boolean"},
            "maximum_degree": {"type": "integer", "minimum": 1, "maximum": 8},
            "continuity": {"type": "string", "enum": ["C0", "C1", "C2", "C3"]},
            "tolerance": {
                "type": "number",
                "minimum": 0.001,
                "maximum": 10.0,
                "description": "Curve approximation tolerance in millimetres.",
            },
            "split_angle_degrees": {
                "type": "number",
                "minimum": 5.0,
                "maximum": 180.0,
            },
        },
        ("object_name", "anchors"),
    )


def _blend_curve_definition() -> dict[str, Any]:
    endpoint = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "edge": _EDGE_NAME,
            "parameter": {"type": "number", "minimum": 0.0, "maximum": 1.0},
            "continuity": {
                "type": "string",
                "enum": ["C0", "G1", "G2", "G3", "G4"],
            },
            "size": {"type": "number", "minimum": -100.0, "maximum": 100.0},
        },
        ("object_name", "edge"),
    )
    return parameters_schema(
        {"start": endpoint, "end": endpoint},
        ("start", "end"),
    )


def model_surface_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="model.surface",
        description="Create retained surface geometry.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="filling",
                description=(
                    "Create one variational filling from exact ordered boundary, "
                    "curve, face, and point constraints."
                ),
                action_ids=frozenset({"Surface_Filling"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="OrderedExactCurrentFillingConstraints",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _filling_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="geom_fill_surface",
                description=(
                    "Fill two to four exact boundary edges with stretched, "
                    "Coons, or curved interpolation."
                ),
                action_ids=frozenset({"Surface_GeomFillSurface"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="OrderedExactCurrentBoundaryEdges",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "definition": _geometric_fill_definition(),
                    },
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="sections",
                description="Create one surface through ordered exact section edges.",
                action_ids=frozenset({"Surface_Sections"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="OrderedExactCurrentSectionEdges",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _sections_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="extend_face",
                description="Extend one exact face along its local U and V parameters.",
                action_ids=frozenset({"Surface_ExtendFace"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentFace",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _extend_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="curve_on_mesh",
                description="Create a retained curve from ordered pick rays on one exact mesh.",
                action_ids=frozenset({"Surface_CurveOnMesh"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentMeshAndOrderedPickRays",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _curve_on_mesh_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="blend_curve",
                description="Blend two exact edge points with endpoint continuity.",
                action_ids=frozenset({"Surface_BlendCurve"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="TwoExactCurrentEdgePoints",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _blend_curve_definition()},
                    ("label", "definition"),
                ),
            ),
        ),
    )


def register_model_surface_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_surface_capability_definition())
