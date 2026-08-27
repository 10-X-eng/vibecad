# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for retained Mesh merge, segmentation, and boundaries."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_SEGMENT_CAPABILITY_NAME = "mesh.segment"
MESH_COMBINE_CAPABILITY_NAME = "mesh.combine"
MESH_SEPARATE_CAPABILITY_NAME = "mesh.separate"
MESH_SEGMENT_CAPABILITY_NAMES = (
    MESH_SEGMENT_CAPABILITY_NAME,
    MESH_COMBINE_CAPABILITY_NAME,
    MESH_SEPARATE_CAPABILITY_NAME,
)
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
_NUMBER = {"type": "number", "minimum": -1.0e100, "maximum": 1.0e100}
_NONNEGATIVE = {"type": "number", "minimum": 0.0, "maximum": 1.0e100}
_CURVATURE_TOLERANCE_PER_MM = {
    **_NONNEGATIVE,
    "description": "Absolute curvature tolerance in inverse millimetres.",
}
_POSITIVE = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e100}
_POSITIVE_INT = {"type": "integer", "minimum": 1, "maximum": 2_147_483_647}
_VECTOR = {
    "type": "object",
    "properties": {"x": _NUMBER, "y": _NUMBER, "z": _NUMBER},
    "required": ["x", "y", "z"],
    "additionalProperties": False,
}
_POINT = {
    "type": "object",
    "properties": {"x_mm": _NUMBER, "y_mm": _NUMBER, "z_mm": _NUMBER},
    "required": ["x_mm", "y_mm", "z_mm"],
    "additionalProperties": False,
}
_EXACT_MESH = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_LABELED_MESH = {
    "type": "object",
    "properties": {**_EXACT_MESH["properties"], "label": _LABEL},
    "required": ["object_name", "expected_state_sha256", "label"],
    "additionalProperties": False,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


def _surface(kind: str, properties: dict, required: tuple[str, ...]) -> dict:
    return _closed(
        {"kind": {"type": "string", "const": kind}, **properties},
        ("kind", *required),
    )


_CURVATURE_SURFACE = {
    "oneOf": [
        _surface(
            "plane",
            {
                "minimum_facets": _POSITIVE_INT,
                "curvature_tolerance": _CURVATURE_TOLERANCE_PER_MM,
            },
            ("minimum_facets", "curvature_tolerance"),
        ),
        _surface(
            "cylinder",
            {
                "minimum_facets": _POSITIVE_INT,
                "curvature_per_mm": _NONNEGATIVE,
                "flat_curvature_tolerance": _CURVATURE_TOLERANCE_PER_MM,
                "curved_curvature_tolerance": _CURVATURE_TOLERANCE_PER_MM,
            },
            (
                "minimum_facets",
                "curvature_per_mm",
                "flat_curvature_tolerance",
                "curved_curvature_tolerance",
            ),
        ),
        _surface(
            "sphere",
            {
                "minimum_facets": _POSITIVE_INT,
                "curvature_per_mm": _NONNEGATIVE,
                "curvature_tolerance": _CURVATURE_TOLERANCE_PER_MM,
            },
            ("minimum_facets", "curvature_per_mm", "curvature_tolerance"),
        ),
        _surface(
            "freeform",
            {
                "minimum_facets": _POSITIVE_INT,
                "maximum_curvature_per_mm": _NUMBER,
                "minimum_curvature_per_mm": _NUMBER,
                "maximum_curvature_tolerance": _CURVATURE_TOLERANCE_PER_MM,
                "minimum_curvature_tolerance": _CURVATURE_TOLERANCE_PER_MM,
            },
            (
                "minimum_facets",
                "maximum_curvature_per_mm",
                "minimum_curvature_per_mm",
                "maximum_curvature_tolerance",
                "minimum_curvature_tolerance",
            ),
        ),
    ]
}
_PLANE_INITIAL = _closed({"point_mm": _POINT, "normal": _VECTOR}, ("point_mm", "normal"))
_CYLINDER_INITIAL = _closed(
    {"base_mm": _POINT, "axis": _VECTOR, "radius_mm": _POSITIVE},
    ("base_mm", "axis", "radius_mm"),
)
_SPHERE_INITIAL = _closed(
    {"center_mm": _POINT, "radius_mm": _POSITIVE},
    ("center_mm", "radius_mm"),
)
_BEST_FIT_SURFACE = {
    "oneOf": [
        _surface(
            "plane",
            {
                "minimum_facets": _POSITIVE_INT,
                "distance_tolerance_mm": _NONNEGATIVE,
                "initial": _PLANE_INITIAL,
            },
            ("minimum_facets", "distance_tolerance_mm"),
        ),
        _surface(
            "cylinder",
            {
                "minimum_facets": _POSITIVE_INT,
                "distance_tolerance_mm": _NONNEGATIVE,
                "initial": _CYLINDER_INITIAL,
            },
            ("minimum_facets", "distance_tolerance_mm"),
        ),
        _surface(
            "sphere",
            {
                "minimum_facets": _POSITIVE_INT,
                "distance_tolerance_mm": _NONNEGATIVE,
                "initial": _SPHERE_INITIAL,
            },
            ("minimum_facets", "distance_tolerance_mm"),
        ),
    ]
}
_FACET_SELECTION = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "facet_indices"},
                "facet_indices": {
                    "type": "array",
                    "items": {"type": "integer", "minimum": 0},
                    "minItems": 1,
                    "maxItems": 256,
                    "uniqueItems": True,
                },
            },
            ("kind", "facet_indices"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "facet_ranges"},
                "ranges": {
                    "type": "array",
                    "items": _closed(
                        {
                            "first_index": {"type": "integer", "minimum": 0},
                            "last_index": {"type": "integer", "minimum": 0},
                        },
                        ("first_index", "last_index"),
                    ),
                    "minItems": 1,
                    "maxItems": 256,
                },
            },
            ("kind", "ranges"),
        ),
    ]
}
_MANUAL_RESULT = {
    "oneOf": [
        _closed(
            {
                "mode": {"type": "string", "const": "extract"},
                "segment_label": _LABEL,
            },
            ("mode", "segment_label"),
        ),
        _closed(
            {
                "mode": {"type": "string", "const": "split"},
                "segment_label": _LABEL,
                "remainder_label": _LABEL,
            },
            ("mode", "segment_label", "remainder_label"),
        ),
    ]
}


def _variant(operation: str, description: str, action_id: str, parameters: dict) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"mesh"}),
        exact_target_type="ExactCurrentHistoryMeshOrFacetSelection",
        transaction_behavior="background",
        background_required=True,
        parameters=parameters,
    )


def mesh_segment_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MESH_SEGMENT_CAPABILITY_NAME,
        description=(
            "Merge exact Meshes, split connected components, detect durable surface "
            "segments, extract exact facets, or create linked boundary geometry."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "merge",
                "Merge 2 to 32 exact current-History Meshes into one retained Mesh.",
                "Mesh_Merge",
                _closed(
                    {
                        "sources": {
                            "type": "array",
                            "items": _EXACT_MESH,
                            "minItems": 2,
                            "maxItems": 32,
                        },
                        "result_label": _LABEL,
                    },
                    ("sources", "result_label"),
                ),
            ),
            _variant(
                "split_components",
                "Split one exact Mesh into its edge-connected components.",
                "Mesh_SplitComponents",
                _closed(
                    {"target": _EXACT_MESH, "result_label_prefix": _LABEL},
                    ("target", "result_label_prefix"),
                ),
            ),
            _variant(
                "mesh_segmentation",
                "Detect Plane, Cylinder, Sphere, or Freeform regions by curvature.",
                "Mesh_Segmentation",
                _closed(
                    {
                        "target": _EXACT_MESH,
                        "surfaces": {
                            "type": "array",
                            "items": _CURVATURE_SURFACE,
                            "minItems": 1,
                            "maxItems": 4,
                        },
                        "smoothing_steps": {"type": "integer", "minimum": 0, "maximum": 10_000},
                        "result_label_prefix": _LABEL,
                    },
                    ("target", "surfaces", "smoothing_steps", "result_label_prefix"),
                ),
            ),
            _variant(
                "segmentation_best_fit",
                "Detect Plane, Cylinder, or Sphere regions by geometric distance.",
                "Mesh_SegmentationBestFit",
                _closed(
                    {
                        "target": _EXACT_MESH,
                        "surfaces": {
                            "type": "array",
                            "items": _BEST_FIT_SURFACE,
                            "minItems": 1,
                            "maxItems": 3,
                        },
                        "result_label_prefix": _LABEL,
                    },
                    ("target", "surfaces", "result_label_prefix"),
                ),
            ),
            _variant(
                "reverse_segmentation",
                "Detect fitted planar regions and optionally create linked boundary faces.",
                "Reen_Segmentation",
                _closed(
                    {
                        "target": _EXACT_MESH,
                        "minimum_facets": _POSITIVE_INT,
                        "curvature_tolerance": _CURVATURE_TOLERANCE_PER_MM,
                        "distance_tolerance_mm": _NONNEGATIVE,
                        "smoothing_steps": {"type": "integer", "minimum": 0, "maximum": 10_000},
                        "include_unused_facets": {"type": "boolean"},
                        "create_boundary_faces": {"type": "boolean"},
                        "result_label_prefix": _LABEL,
                    },
                    (
                        "target",
                        "minimum_facets",
                        "curvature_tolerance",
                        "distance_tolerance_mm",
                        "smoothing_steps",
                        "include_unused_facets",
                        "create_boundary_faces",
                        "result_label_prefix",
                    ),
                ),
            ),
            _variant(
                "segmentation_manual",
                "Extract or split an exact, explicitly indexed facet selection.",
                "Reen_SegmentationManual",
                _closed(
                    {"target": _EXACT_MESH, "selection": _FACET_SELECTION, "result": _MANUAL_RESULT},
                    ("target", "selection", "result"),
                ),
            ),
            _variant(
                "segmentation_from_components",
                "Split connected components from 1 to 32 exact Meshes in one operation.",
                "Reen_SegmentationFromComponents",
                _closed(
                    {
                        "targets": {
                            "type": "array",
                            "items": _EXACT_MESH,
                            "minItems": 1,
                            "maxItems": 32,
                        },
                        "result_label_prefix": _LABEL,
                    },
                    ("targets", "result_label_prefix"),
                ),
            ),
            _variant(
                "mesh_boundary",
                "Create one recomputable boundary wire or face for each exact Mesh.",
                "Reen_MeshBoundary",
                _closed(
                    {
                        "targets": {
                            "type": "array",
                            "items": _LABELED_MESH,
                            "minItems": 1,
                            "maxItems": 32,
                        },
                        "make_faces_when_closed": {"type": "boolean"},
                    },
                    ("targets", "make_faces_when_closed"),
                ),
            ),
        ),
    )


def register_mesh_segment_capability_definition(registry: NativeCapabilityRegistry) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    segment = mesh_segment_capability_definition()
    registry.register_definition(segment)
    variants = {variant.operation: variant for variant in segment.variants}
    for name, description, operation, parameters in (
        (
            MESH_COMBINE_CAPABILITY_NAME,
            "Combine two or more exact Meshes into one retained Mesh.",
            "merge",
            _closed(
                {
                    "sources": {
                        "type": "array",
                        "items": _EXACT_MESH,
                        "minItems": 2,
                        "maxItems": 32,
                    },
                    "result_label": _LABEL,
                },
                ("sources",),
            ),
        ),
        (
            MESH_SEPARATE_CAPABILITY_NAME,
            "Separate one exact Mesh into its connected components.",
            "split_components",
            _closed(
                {"target": _EXACT_MESH, "result_label_prefix": _LABEL},
                ("target",),
            ),
        ),
    ):
        variant = variants[operation]
        registry.register_definition(
            NativeCapabilityDefinition(
                name=name,
                description=description,
                primary_classification="mutation",
                variants=(
                    NativeCapabilityVariant(
                        operation=variant.operation,
                        description=variant.description,
                        action_ids=variant.action_ids,
                        surface_ids=variant.surface_ids,
                        exact_target_type=variant.exact_target_type,
                        transaction_behavior=variant.transaction_behavior,
                        background_required=variant.background_required,
                        parameters=parameters,
                        provider_supplemental=variant.provider_supplemental,
                    ),
                ),
            )
        )
