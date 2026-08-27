# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider contract for exact retained Mesh modification operations."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


MESH_MODIFY_CAPABILITY_NAME = "mesh.modify"
MESH_REPAIR_CAPABILITY_NAME = "mesh.repair"
MESH_FILL_HOLES_CAPABILITY_NAME = "mesh.fill_holes"
MESH_SMOOTH_CAPABILITY_NAME = "mesh.smooth"
MESH_REMESH_CAPABILITY_NAME = "mesh.remesh"
MESH_DECIMATE_CAPABILITY_NAME = "mesh.decimate"
MESH_SCALE_CAPABILITY_NAME = "mesh.scale"
MESH_MODIFY_CAPABILITY_NAMES = (
    MESH_MODIFY_CAPABILITY_NAME,
    MESH_REPAIR_CAPABILITY_NAME,
    MESH_FILL_HOLES_CAPABILITY_NAME,
    MESH_SMOOTH_CAPABILITY_NAME,
    MESH_REMESH_CAPABILITY_NAME,
    MESH_DECIMATE_CAPABILITY_NAME,
    MESH_SCALE_CAPABILITY_NAME,
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
_INDEX = {"type": "integer", "minimum": 0, "maximum": 2_147_483_647}
_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "label": _LABEL,
    },
    "required": ["object_name", "expected_state_sha256", "label"],
    "additionalProperties": False,
}
_TARGETS = {
    "type": "array",
    "items": _TARGET,
    "minItems": 1,
    "maxItems": 32,
}
_ONE_TARGET = {
    "type": "array",
    "items": _TARGET,
    "minItems": 1,
    "maxItems": 1,
}
_POINT_SELECTION = {
    "oneOf": [
        {
            "type": "object",
            "properties": {"kind": {"type": "string", "const": "all"}},
            "required": ["kind"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "point_indices"},
                "point_indices": {
                    "type": "array",
                    "items": _INDEX,
                    "minItems": 1,
                    "maxItems": 256,
                    "uniqueItems": True,
                },
            },
            "required": ["kind", "point_indices"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "kind": {"type": "string", "const": "point_ranges"},
                "ranges": {
                    "type": "array",
                    "description": "Inclusive nonoverlapping zero-based point ranges.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "first_index": _INDEX,
                            "last_index": _INDEX,
                        },
                        "required": ["first_index", "last_index"],
                        "additionalProperties": False,
                    },
                    "minItems": 1,
                    "maxItems": 256,
                },
            },
            "required": ["kind", "ranges"],
            "additionalProperties": False,
        },
    ]
}
_SMOOTH_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
        "label": _LABEL,
        "selection": _POINT_SELECTION,
    },
    "required": ["object_name", "expected_state_sha256", "label"],
    "additionalProperties": False,
}
_SMOOTH_TARGETS = {
    "type": "array",
    "items": _SMOOTH_TARGET,
    "minItems": 1,
    "maxItems": 32,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_COMPONENT_SELECTION = {
    "oneOf": [
        _closed(
            {
                "kind": {"type": "string", "const": "maximum_facets"},
                "maximum_facets": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2_147_483_647,
                },
            },
            ("kind", "maximum_facets"),
        ),
        _closed(
            {
                "kind": {"type": "string", "const": "component_ids"},
                "component_ids": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "minLength": 77,
                        "maxLength": 77,
                        "pattern": r"^component-v1:[0-9a-f]{64}$",
                    },
                    "minItems": 1,
                    "maxItems": 256,
                    "uniqueItems": True,
                },
            },
            ("kind", "component_ids"),
        ),
    ]
}
_SMOOTH_SETTINGS = {
    "oneOf": [
        _closed(
            {
                "method": {"type": "string", "const": "taubin"},
                "iterations": {"type": "integer", "minimum": 1, "maximum": 10_000},
                "lambda": {"type": "number", "minimum": -10.0, "maximum": 10.0},
                "mu": {"type": "number", "minimum": -10.0, "maximum": 10.0},
            },
            ("method", "iterations", "lambda", "mu"),
        ),
        _closed(
            {
                "method": {"type": "string", "const": "laplace"},
                "iterations": {"type": "integer", "minimum": 1, "maximum": 10_000},
                "lambda": {"type": "number", "minimum": -10.0, "maximum": 10.0},
            },
            ("method", "iterations", "lambda"),
        ),
        _closed(
            {
                "method": {"type": "string", "const": "median"},
                "iterations": {"type": "integer", "minimum": 1, "maximum": 10_000},
            },
            ("method", "iterations"),
        ),
    ]
}
_DECIMATE_SETTINGS = {
    "oneOf": [
        _closed(
            {
                "mode": {"type": "string", "const": "target_facets"},
                "target_facet_count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": 2_147_483_647,
                },
            },
            ("mode", "target_facet_count"),
        ),
        _closed(
            {
                "mode": {"type": "string", "const": "percentage"},
                "reduction_percent": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "exclusiveMaximum": 100.0,
                },
                "tolerance_mm": {
                    "type": "number",
                    "minimum": 0.0,
                    "maximum": 1_000_000.0,
                },
            },
            ("mode", "reduction_percent", "tolerance_mm"),
        ),
    ]
}

_SMOOTH_FOCUSED_PARAMETERS = _closed(
    {
        "targets": _SMOOTH_TARGETS,
        "method": {
            "type": "string",
            "enum": ["taubin", "laplace", "median"],
        },
        "iterations": {"type": "integer", "minimum": 1, "maximum": 10_000},
        "lambda": {"type": "number", "minimum": -10.0, "maximum": 10.0},
        "mu": {"type": "number", "minimum": -10.0, "maximum": 10.0},
    },
    ("targets", "method", "iterations"),
)
_DECIMATE_FOCUSED_PARAMETERS = _closed(
    {
        "targets": _TARGETS,
        "mode": {
            "type": "string",
            "enum": ["target_facets", "percentage"],
        },
        "target_facet_count": {
            "type": "integer",
            "minimum": 1,
            "maximum": 2_147_483_647,
        },
        "reduction_percent": {
            "type": "number",
            "exclusiveMinimum": 0.0,
            "exclusiveMaximum": 100.0,
        },
        "tolerance_mm": {
            "type": "number",
            "minimum": 0.0,
            "maximum": 1_000_000.0,
        },
    },
    ("targets", "mode"),
)
_REPAIR_FOCUSED_PARAMETERS = _closed(
    {
        "targets": _TARGETS,
        "defects": {
            "type": "array",
            "items": {
                "type": "string",
                "enum": [
                    "non_uniform_orientation",
                    "duplicated_facets",
                    "duplicated_points",
                    "non_manifold_edges",
                    "non_manifold_points",
                    "facet_indices_out_of_range",
                    "point_indices_out_of_range",
                    "corrupted_facets",
                    "invalid_neighbourhood",
                    "degenerated_facets",
                    "self_intersections",
                    "surface_folds",
                    "boundary_folds",
                ],
            },
            "minItems": 1,
            "maxItems": 13,
            "uniqueItems": True,
        },
        "max_iterations": {
            "type": "integer",
            "minimum": 1,
            "maximum": 100,
            "default": 1,
        },
    },
    ("targets", "defects"),
)


def _variant(
    operation: str,
    description: str,
    action_id: str,
    parameters: dict,
    *,
    background: bool = True,
) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"mesh"}),
        exact_target_type="ExactCurrentHistoryMeshState",
        transaction_behavior="background" if background else "document",
        background_required=background,
        parameters=parameters,
    )


def mesh_modify_capability_definition() -> NativeCapabilityDefinition:
    targets_only = lambda: _closed({"targets": _TARGETS}, ("targets",))
    return NativeCapabilityDefinition(
        name=MESH_MODIFY_CAPABILITY_NAME,
        description=(
            "Create retained source-linked Mesh repairs and edits against exact "
            "current-History state. Every index and component identity is zero-based."
        ),
        primary_classification="mutation",
        variants=(
            _variant(
                "harmonize_normals",
                "Make inconsistent facet winding coherent on 1 to 32 exact Meshes.",
                "Mesh_HarmonizeNormals",
                targets_only(),
            ),
            _variant(
                "flip_normals",
                "Reverse every facet normal on 1 to 32 exact Meshes.",
                "Mesh_FlipNormals",
                targets_only(),
            ),
            _variant(
                "fill_holes",
                "Fill every boundary having at most the explicit number of edges.",
                "Mesh_FillupHoles",
                _closed(
                    {
                        "targets": _TARGETS,
                        "maximum_boundary_edges": {
                            "type": "integer",
                            "minimum": 3,
                            "maximum": 10_000,
                            "default": 3,
                        },
                    },
                    ("targets",),
                ),
            ),
            _variant(
                "fill_boundary",
                "Fill the hole adjacent to one exact zero-based seed facet.",
                "Mesh_FillInteractiveHole",
                _closed(
                    {
                        "targets": _ONE_TARGET,
                        "seed_facet_index": _INDEX,
                        "refinement_level": {
                            "type": "integer",
                            "minimum": 0,
                            "maximum": 10,
                        },
                    },
                    ("targets", "seed_facet_index", "refinement_level"),
                ),
            ),
            _variant(
                "add_triangle",
                "Add one facet using three distinct exact zero-based source point indices.",
                "Mesh_AddFacet",
                _closed(
                    {
                        "targets": _ONE_TARGET,
                        "point_indices": {
                            "type": "array",
                            "items": _INDEX,
                            "minItems": 3,
                            "maxItems": 3,
                            "uniqueItems": True,
                        },
                    },
                    ("targets", "point_indices"),
                ),
            ),
            _variant(
                "remove_components",
                "Remove exact connected components by size or IDs returned by Mesh inspection.",
                "Mesh_RemoveComponents",
                _closed(
                    {"targets": _ONE_TARGET, "selection": _COMPONENT_SELECTION},
                    ("targets", "selection"),
                ),
            ),
            _variant(
                "smooth",
                "Smooth complete Meshes or explicit zero-based point subsets.",
                "Mesh_Smoothing",
                _closed(
                    {"targets": _SMOOTH_TARGETS, "settings": _SMOOTH_SETTINGS},
                    ("targets", "settings"),
                ),
            ),
            _variant(
                "gmsh_remesh",
                "Remesh one exact Mesh with the configured Gmsh surface mesher.",
                "Mesh_RemeshGmsh",
                _closed(
                    {
                        "targets": _ONE_TARGET,
                        "algorithm": {
                            "type": "string",
                            "enum": [
                                "automatic",
                                "adaptive",
                                "delaunay",
                                "frontal",
                                "bamg",
                                "frontal_quad",
                                "parallelograms",
                                "quasi_structured_quad",
                            ],
                        },
                        "minimum_element_size_mm": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1_000_000.0,
                        },
                        "maximum_element_size_mm": {
                            "type": "number",
                            "minimum": 0.0,
                            "maximum": 1_000_000.0,
                        },
                        "surface_angle_degrees": {
                            "type": "number",
                            "minimum": 20.0,
                            "maximum": 120.0,
                        },
                        "timeout_seconds": {
                            "type": "integer",
                            "minimum": 1,
                            "maximum": 86_400,
                            "default": 300,
                        },
                    },
                    (
                        "targets",
                        "algorithm",
                        "minimum_element_size_mm",
                        "maximum_element_size_mm",
                        "surface_angle_degrees",
                    ),
                ),
                background=True,
            ),
            _variant(
                "decimate",
                "Reduce 1 to 32 exact Meshes by target count or percentage.",
                "Mesh_Decimating",
                _closed(
                    {"targets": _TARGETS, "settings": _DECIMATE_SETTINGS},
                    ("targets", "settings"),
                ),
            ),
            _variant(
                "scale",
                "Uniformly scale Mesh-local coordinates by one finite positive factor.",
                "Mesh_Scale",
                _closed(
                    {
                        "targets": _TARGETS,
                "factor": {
                    "type": "number",
                    "exclusiveMinimum": 0.0,
                    "maximum": 1.0e100,
                },
                    },
                    ("targets", "factor"),
                ),
            ),
        ),
    )


def register_mesh_modify_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    modify = mesh_modify_capability_definition()
    registry.register_definition(modify)
    variants = {variant.operation: variant for variant in modify.variants}
    registry.register_definition(
        NativeCapabilityDefinition(
            name=MESH_REPAIR_CAPABILITY_NAME,
            description="Repair exact nonzero defect names returned by Mesh inspection.",
            primary_classification="mutation",
            variants=(
                NativeCapabilityVariant(
                    operation="repair",
                    description="Repair selected inspected defects on 1 to 32 exact Meshes.",
                    action_ids=frozenset({"Mesh_HarmonizeNormals"}),
                    surface_ids=frozenset({"mesh"}),
                    exact_target_type="ExactCurrentHistoryMeshState",
                    transaction_behavior="background",
                    background_required=True,
                    parameters=_REPAIR_FOCUSED_PARAMETERS,
                ),
            ),
        )
    )
    fill_holes = variants["fill_holes"]
    registry.register_definition(
        NativeCapabilityDefinition(
            name=MESH_FILL_HOLES_CAPABILITY_NAME,
            description="Fill Mesh boundary loops up to an explicit edge count.",
            primary_classification="mutation",
            variants=(
                NativeCapabilityVariant(
                    operation=fill_holes.operation,
                    description=fill_holes.description,
                    action_ids=fill_holes.action_ids,
                    surface_ids=fill_holes.surface_ids,
                    exact_target_type=fill_holes.exact_target_type,
                    transaction_behavior=fill_holes.transaction_behavior,
                    background_required=fill_holes.background_required,
                    parameters=_closed(
                        {
                            "targets": _TARGETS,
                            "maximum_boundary_edges": {
                                "type": "integer",
                                "minimum": 3,
                                "maximum": 10_000,
                            },
                        },
                        ("targets", "maximum_boundary_edges"),
                    ),
                ),
            ),
        )
    )
    for name, description, operation, focused_parameters in (
        (
            MESH_SMOOTH_CAPABILITY_NAME,
            "Smooth exact Mesh vertices with Taubin, Laplace, or median filtering.",
            "smooth",
            _SMOOTH_FOCUSED_PARAMETERS,
        ),
        (
            MESH_REMESH_CAPABILITY_NAME,
            "Rebuild one exact Mesh surface with the configured Gmsh mesher.",
            "gmsh_remesh",
            None,
        ),
        (
            MESH_DECIMATE_CAPABILITY_NAME,
            "Reduce exact Mesh facet counts by target count or percentage.",
            "decimate",
            _DECIMATE_FOCUSED_PARAMETERS,
        ),
        (
            MESH_SCALE_CAPABILITY_NAME,
            "Scale exact Mesh coordinates uniformly about the local origin.",
            "scale",
            None,
        ),
    ):
        variant = variants[operation]
        if focused_parameters is not None:
            variant = NativeCapabilityVariant(
                operation=variant.operation,
                description=variant.description,
                action_ids=variant.action_ids,
                surface_ids=variant.surface_ids,
                exact_target_type=variant.exact_target_type,
                transaction_behavior=variant.transaction_behavior,
                background_required=variant.background_required,
                parameters=focused_parameters,
                provider_supplemental=variant.provider_supplemental,
            )
        registry.register_definition(
            NativeCapabilityDefinition(
                name=name,
                description=description,
                primary_classification="mutation",
                variants=(variant,),
            )
        )
