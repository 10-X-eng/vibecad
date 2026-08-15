# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for the complete Mesh-ribbon Approximate group."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_APPROXIMATE_CAPABILITY_NAME = "mesh.approximate"
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
_EXACT_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_LABELED_TARGET = {
    "type": "object",
    "properties": {**_EXACT_TARGET["properties"], "result_label": _LABEL},
    "required": [*_EXACT_TARGET["required"], "result_label"],
    "additionalProperties": False,
}
_POINT_TARGET = {
    "type": "object",
    "properties": {
        **_EXACT_TARGET["properties"],
        "expected_point_count": {
            "type": "integer",
            "minimum": 2,
            "maximum": 2_147_483_647,
        },
    },
    "required": [*_EXACT_TARGET["required"], "expected_point_count"],
    "additionalProperties": False,
}
_VECTOR = {
    "type": "object",
    "properties": {
        "x": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "y": {"type": "number", "minimum": -1.0, "maximum": 1.0},
        "z": {"type": "number", "minimum": -1.0, "maximum": 1.0},
    },
    "required": ["x", "y", "z"],
    "additionalProperties": False,
}
_UV_DIRECTIONS = {
    "oneOf": [
        {
            "type": "object",
            "properties": {"mode": {"type": "string", "const": "automatic"}},
            "required": ["mode"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "explicit"},
                "u_direction": _VECTOR,
                "v_direction": _VECTOR,
            },
            "required": ["mode", "u_direction", "v_direction"],
            "additionalProperties": False,
        },
    ]
}
_SURFACE_SMOOTHING = {
    "type": "object",
    "properties": {
        "enabled": {"type": "boolean"},
        "total_weight": {"type": "number", "minimum": 0.0, "maximum": 1000.0},
        "gradient_weight": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "bending_weight": {"type": "number", "minimum": 0.0, "maximum": 1.0},
        "curvature_weight": {"type": "number", "minimum": 0.0, "maximum": 1.0},
    },
    "required": [
        "enabled",
        "total_weight",
        "gradient_weight",
        "bending_weight",
        "curvature_weight",
    ],
    "additionalProperties": False,
}
_CURVE_FIT = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "approximation"},
                "minimum_degree": {"type": "integer", "minimum": 1, "maximum": 11},
                "maximum_degree": {"type": "integer", "minimum": 2, "maximum": 11},
                "continuity": {
                    "type": "string",
                    "enum": ["C0", "G1", "C1", "G2", "C2", "C3", "CN"],
                },
                "closed": {"type": "boolean"},
                "parametrization": {
                    "type": "string",
                    "enum": ["automatic", "chord_length", "centripetal", "uniform"],
                },
                "tolerance_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 1000.0,
                },
            },
            "required": [
                "mode",
                "minimum_degree",
                "maximum_degree",
                "continuity",
                "closed",
                "parametrization",
                "tolerance_mm",
            ],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "mode": {"type": "string", "const": "smoothing"},
                "maximum_degree": {"type": "integer", "minimum": 2, "maximum": 11},
                "continuity": {
                    "type": "string",
                    "enum": ["C0", "G1", "C1", "G2", "C2", "C3", "CN"],
                },
                "closed": {"type": "boolean"},
                "curve_length_weight": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "curvature_weight": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "torsion_weight": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "tolerance_mm": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 1000.0,
                },
            },
            "required": [
                "mode",
                "maximum_degree",
                "continuity",
                "closed",
                "curve_length_weight",
                "curvature_weight",
                "torsion_weight",
                "tolerance_mm",
            ],
            "additionalProperties": False,
        },
    ]
}


def _variant(operation: str, description: str, action_id: str, parameters: dict) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"mesh"}),
        exact_target_type="ExactCurrentHistoryGeometry",
        transaction_behavior="background",
        background_required=True,
        parameters=parameters,
    )


def _multi_target(field: str) -> dict:
    return {
        "type": "object",
        "properties": {
            field: {
                "type": "array",
                "items": _LABELED_TARGET,
                "minItems": 1,
                "maxItems": 16,
            }
        },
        "required": [field],
        "additionalProperties": False,
    }


def mesh_approximate_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_APPROXIMATE_CAPABILITY_NAME,
        description=(
            "Fit exact current-History point or Mesh geometry off the UI thread, then "
            "retain linked parametric or spline results with quantitative fit data."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "approx_plane",
                "Fit 1 to 16 exact point-bearing geometry sources to bounded parametric planes.",
                "Reen_ApproxPlane",
                _multi_target("geometry_sources"),
            ),
            _variant(
                "approx_cylinder",
                "Fit 1 to 16 exact non-empty Meshes to bounded parametric cylinders.",
                "Reen_ApproxCylinder",
                _multi_target("cylinder_meshes"),
            ),
            _variant(
                "approx_sphere",
                "Fit 1 to 16 exact non-empty Meshes to parametric spheres.",
                "Reen_ApproxSphere",
                _multi_target("sphere_meshes"),
            ),
            _variant(
                "approx_polynomial",
                "Fit 1 to 16 exact Meshes to quadratic Bezier surface patches.",
                "Reen_ApproxPolynomial",
                _multi_target("polynomial_meshes"),
            ),
            _variant(
                "approx_surface",
                "Fit one exact point cloud or Mesh to a bounded B-spline surface.",
                "Reen_ApproxSurface",
                {
                    "type": "object",
                    "properties": {
                        "surface_source": _EXACT_TARGET,
                        "result_label": _LABEL,
                        "u_degree": {"type": "integer", "minimum": 1, "maximum": 11},
                        "v_degree": {"type": "integer", "minimum": 1, "maximum": 11},
                        "u_control_points": {"type": "integer", "minimum": 2, "maximum": 100},
                        "v_control_points": {"type": "integer", "minimum": 2, "maximum": 100},
                        "iterations": {"type": "integer", "minimum": -1, "maximum": 100},
                        "patch_size_factor": {"type": "number", "minimum": 1.0, "maximum": 2.0},
                        "parameter_correction": {"type": "boolean"},
                        "smoothing": _SURFACE_SMOOTHING,
                        "uv_directions": _UV_DIRECTIONS,
                    },
                    "required": [
                        "surface_source",
                        "result_label",
                        "u_degree",
                        "v_degree",
                        "u_control_points",
                        "v_control_points",
                        "iterations",
                        "patch_size_factor",
                        "parameter_correction",
                        "smoothing",
                        "uv_directions",
                    ],
                    "additionalProperties": False,
                },
            ),
            _variant(
                "approx_curve",
                "Fit one ordered exact point cloud to a bounded open or closed B-spline curve.",
                "Reen_ApproxCurve",
                {
                    "type": "object",
                    "properties": {
                        "curve_source": _POINT_TARGET,
                        "result_label": _LABEL,
                        "fit": _CURVE_FIT,
                    },
                    "required": ["curve_source", "result_label", "fit"],
                    "additionalProperties": False,
                },
            ),
        ),
    )


def register_mesh_approximate_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(mesh_approximate_capability_definition())
