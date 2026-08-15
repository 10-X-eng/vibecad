# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strong provider contract for primary FEM mesh refinements."""

from __future__ import annotations

from VibeCADNativeAnalyzeMeshSchema import _TARGET as _MESH_TARGET
from VibeCADNativeAnalyzeModelSchema import _LABEL, _OBJECT_NAME, _STATE_SHA256
from VibeCADNativeAnalyzeMeshRefinementValues import MODES
from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)


ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME = "analyze.mesh_refinement"

_UPDATE_EXACT_TARGET_BY_OPERATION = {
    "update_region": "ExactFemMeshRegionAndGeometry",
    "update_group": "ExactFemMeshGroupAndGeometry",
    "update_distance": "ExactFemMeshDistanceAndGeometry",
    "update_boundary_layer": "ExactFemMeshBoundaryLayerAndEdges",
    "update_shape": "ExactFemMeshShapeRefinement",
}
_TARGET = {
    "type": "object",
    "properties": {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _STATE_SHA256,
    },
    "required": ["object_name", "expected_state_sha256"],
    "additionalProperties": False,
}
_POSITIVE = {"type": "number", "exclusiveMinimum": 0.0, "maximum": 1.0e12}
_NONNEGATIVE = {"type": "number", "minimum": 0.0, "maximum": 1.0e12}
_VECTOR = {
    "type": "object",
    "properties": {axis: {"type": "number", "minimum": -1.0e12, "maximum": 1.0e12} for axis in ("x", "y", "z")},
    "required": ["x", "y", "z"],
    "additionalProperties": False,
}


def _closed(properties: dict, required: tuple[str, ...], *, minimum: int = 0) -> dict:
    result = {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }
    if minimum:
        result["minProperties"] = minimum
    return result


def _references(kinds: tuple[str, ...]) -> dict:
    pattern = "|".join(kinds)
    return {
        "type": "array",
        "items": _closed(
            {
                "object_name": _OBJECT_NAME,
                "expected_state_sha256": _STATE_SHA256,
                "subelements": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "pattern": rf"^(?:{pattern})[1-9][0-9]*$",
                        "maxLength": 32,
                    },
                    "minItems": 1,
                    "maxItems": 256,
                    "uniqueItems": True,
                },
            },
            ("object_name", "expected_state_sha256", "subelements"),
        ),
        "minItems": 1,
        "maxItems": 64,
    }


_DEFINITIONS = {
    "region": _closed({"element_size_mm": _POSITIVE}, ("element_size_mm",)),
    "group": _closed(
        {"export_identifier": {"type": "string", "enum": ["object_name", "label"]}},
        ("export_identifier",),
    ),
    "distance": _closed(
        {
            "distance_minimum_mm": _POSITIVE,
            "distance_maximum_mm": _POSITIVE,
            "size_minimum_mm": _POSITIVE,
            "size_maximum_mm": _POSITIVE,
            "linear_interpolation": {"type": "boolean"},
            "sampling": {"type": "integer", "minimum": 1, "maximum": 1000},
        },
        (
            "distance_minimum_mm",
            "distance_maximum_mm",
            "size_minimum_mm",
            "size_maximum_mm",
            "linear_interpolation",
            "sampling",
        ),
    ),
    "boundary_layer": _closed(
        {
            "minimum_thickness_mm": _POSITIVE,
            "number_of_layers": {"type": "integer", "minimum": 1, "maximum": 100000},
            "growth_rate": _POSITIVE,
        },
        ("minimum_thickness_mm", "number_of_layers", "growth_rate"),
    ),
    "shape": _closed(
        {
            "shape": {
                "oneOf": [
                    _closed(
                        {
                            "kind": {"type": "string", "const": "box"},
                            "center_mm": _VECTOR,
                            "length_mm": _POSITIVE,
                            "width_mm": _POSITIVE,
                            "height_mm": _POSITIVE,
                        },
                        ("kind", "center_mm", "length_mm", "width_mm", "height_mm"),
                    ),
                    _closed(
                        {
                            "kind": {"type": "string", "const": "sphere"},
                            "center_mm": _VECTOR,
                            "radius_mm": _POSITIVE,
                        },
                        ("kind", "center_mm", "radius_mm"),
                    ),
                    _closed(
                        {
                            "kind": {"type": "string", "const": "cylinder"},
                            "center_mm": _VECTOR,
                            "axis": _VECTOR,
                            "radius_mm": _POSITIVE,
                        },
                        ("kind", "center_mm", "axis", "radius_mm"),
                    ),
                ]
            },
            "size_inside_mm": _POSITIVE,
            "size_outside_mm": _POSITIVE,
            "transition_thickness_mm": _NONNEGATIVE,
        },
        ("shape", "size_inside_mm", "size_outside_mm", "transition_thickness_mm"),
    ),
}
_KINDS = {
    "region": ("Solid", "Face", "Edge", "Vertex"),
    "group": ("Solid", "Face", "Edge", "Vertex"),
    "distance": ("Face", "Edge", "Vertex"),
    "boundary_layer": ("Edge",),
}
_ACTIONS = {
    "region": "FEM_MeshRegion",
    "group": "FEM_MeshGroup",
    "distance": "FEM_MeshDistance",
    "boundary_layer": "FEM_MeshBoundaryLayer",
    "shape": "FEM_MeshShape",
}


def _create(mode: str) -> dict:
    fields = {"mesh": _MESH_TARGET, "label": _LABEL, "definition": _DEFINITIONS[mode]}
    if mode != "shape":
        fields["references"] = _references(_KINDS[mode])
    return _closed(fields, tuple(fields))


def _update(mode: str) -> dict:
    fields = {"target": _TARGET, "label": _LABEL, "definition": _DEFINITIONS[mode]}
    if mode != "shape":
        fields["references"] = _references(_KINDS[mode])
    return _closed(fields, ("target",), minimum=3)


def _variant(operation: str, description: str, action_id: str, parameters: dict) -> NativeCapabilityVariant:
    return NativeCapabilityVariant(
        operation=operation,
        description=description,
        action_ids=frozenset({action_id}),
        surface_ids=frozenset({"analyze"}),
        exact_target_type=_UPDATE_EXACT_TARGET_BY_OPERATION.get(
            operation,
            "ExactFemMeshDefinitionRefinementAndGeometry",
        ),
        transaction_behavior="document",
        background_required=False,
        parameters=parameters,
    )


def analyze_mesh_refinement_capability_definition() -> NativeCapabilityDefinition:
    names = {
        "region": "local element-size region",
        "group": "named export group",
        "distance": "distance-based size field",
        "boundary_layer": "2D boundary layer",
        "shape": "box, sphere, or cylinder size field",
    }
    variants = []
    for mode in MODES:
        variants.append(
            _variant(
                f"create_{mode}",
                f"Create one typed {names[mode]} owned by an exact mesh definition.",
                _ACTIONS[mode],
                _create(mode),
            )
        )
        variants.append(
            _variant(
                f"update_{mode}",
                f"Edit one exact {names[mode]} in place and invalidate stale mesh data.",
                "VibeCAD_AnalyzeUpdateMesh" + "".join(part.title() for part in mode.split("_")),
                _update(mode),
            )
        )
    return NativeCapabilityDefinition(
        name=ANALYZE_MESH_REFINEMENT_CAPABILITY_NAME,
        description=(
            "Create or edit exact, typed FEM mesh refinement resources using the same "
            "parameters and geometry kinds as the human editors."
        ),
        primary_classification="mutation",
        variants=tuple(variants),
    )


def register_analyze_mesh_refinement_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(analyze_mesh_refinement_capability_definition())
