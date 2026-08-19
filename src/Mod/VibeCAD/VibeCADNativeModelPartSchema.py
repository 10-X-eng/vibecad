# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for standalone Part operations on Model."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    NONNEGATIVE_MM_SCHEMA,
    OBJECT_NAME_SCHEMA,
    POSITIVE_MM_SCHEMA,
    SIGNED_MM_SCHEMA,
    parameters_schema,
    placement_schema,
)


_MODEL_SURFACE = frozenset({"model"})
_ARC_ANGLE = {"type": "number", "minimum": 0.0, "maximum": 360.0}
_TAPER_ANGLE = {"type": "number", "minimum": -89.9, "maximum": 89.9}
_ROTATIONS = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 1_000.0,
}
_POINT_PROPERTIES = {
    "x_mm": SIGNED_MM_SCHEMA,
    "y_mm": SIGNED_MM_SCHEMA,
    "z_mm": SIGNED_MM_SCHEMA,
}
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
_CURVE_NAME = {
    "type": "string",
    "maxLength": 64,
    "pattern": r"^(?:Edge|Wire)[1-9][0-9]*$",
}
_CROSS_SECTION_ELEMENT_NAME = {
    "type": "string",
    "maxLength": 64,
    "pattern": r"^(?:Vertex|Edge|Wire|Face|Shell|Solid|CompSolid|Compound)[1-9][0-9]*$",
}


def _primitive_definition() -> dict[str, Any]:
    return parameters_schema(
        {
            "kind": {
                "type": "string",
                "enum": [
                    "plane",
                    "helix",
                    "spiral",
                    "circle",
                    "ellipse",
                    "point",
                    "line",
                    "regular_polygon",
                ],
            },
            "length_mm": POSITIVE_MM_SCHEMA,
            "width_mm": POSITIVE_MM_SCHEMA,
            "pitch_mm": POSITIVE_MM_SCHEMA,
            "height_mm": POSITIVE_MM_SCHEMA,
            "radius_mm": NONNEGATIVE_MM_SCHEMA,
            "taper_degrees": _TAPER_ANGLE,
            "handedness": {"type": "string", "enum": ["right", "left"]},
            "growth_mm": NONNEGATIVE_MM_SCHEMA,
            "rotations": _ROTATIONS,
            "start_degrees": _ARC_ANGLE,
            "end_degrees": _ARC_ANGLE,
            "major_radius_mm": POSITIVE_MM_SCHEMA,
            "minor_radius_mm": POSITIVE_MM_SCHEMA,
            **_POINT_PROPERTIES,
            **{f"start_{name}": schema for name, schema in _POINT_PROPERTIES.items()},
            **{f"end_{name}": schema for name, schema in _POINT_PROPERTIES.items()},
            "sides": {"type": "integer", "minimum": 3, "maximum": 1_000},
            "circumradius_mm": POSITIVE_MM_SCHEMA,
        },
        ("kind",),
    )


def _builder_definition() -> dict[str, Any]:
    input_group = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "subelements": {
                "type": "array",
                "items": _ELEMENT_NAME,
                "minItems": 1,
                "maxItems": 64,
                "uniqueItems": True,
            },
        },
        ("object_name", "subelements"),
    )
    return parameters_schema(
        {
            "kind": {
                "type": "string",
                "enum": [
                    "edge_from_vertices",
                    "wire_from_edges",
                    "face_from_vertices",
                    "face_from_edges",
                    "shell_from_faces",
                    "solid_from_shell",
                ],
            },
            "inputs": {
                "type": "array",
                "items": input_group,
                "minItems": 1,
                "maxItems": 32,
                "uniqueItems": True,
            },
            "source": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "planar": {"type": "boolean"},
            "refine": {"type": "boolean"},
            "all_faces": {"type": "boolean"},
        },
        ("kind",),
    )


def _make_face_definition() -> dict[str, Any]:
    source = parameters_schema(
        {"object_name": OBJECT_NAME_SCHEMA},
        ("object_name",),
    )
    return parameters_schema(
        {
            "sources": {
                "type": "array",
                "items": source,
                "minItems": 1,
                "maxItems": 32,
                "uniqueItems": True,
            }
        },
        ("sources",),
    )


def _ruled_surface_definition() -> dict[str, Any]:
    curve = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "subelement": _CURVE_NAME,
        },
        ("object_name",),
    )
    return parameters_schema(
        {
            "curves": {
                "type": "array",
                "items": curve,
                "minItems": 2,
                "maxItems": 2,
                "uniqueItems": True,
            }
        },
        ("curves",),
    )


def _cross_sections_definition() -> dict[str, Any]:
    source = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "subelements": {
                "type": "array",
                "items": _CROSS_SECTION_ELEMENT_NAME,
                "minItems": 1,
                "maxItems": 64,
                "uniqueItems": True,
            },
        },
        ("object_name",),
    )
    distribution = parameters_schema(
        {
            "kind": {"type": "string", "enum": ["single", "series"]},
            "position_mm": SIGNED_MM_SCHEMA,
            "count": {"type": "integer", "minimum": 1, "maximum": 10_000},
            "distance_mm": NONNEGATIVE_MM_SCHEMA,
            "both_sides": {"type": "boolean"},
        },
        ("kind",),
    )
    return parameters_schema(
        {
            "sources": {
                "type": "array",
                "items": source,
                "minItems": 1,
                "maxItems": 32,
                "uniqueItems": True,
            },
            "plane": {"type": "string", "enum": ["xy", "xz", "yz"]},
            "distribution": distribution,
        },
        ("sources", "plane", "distribution"),
    )


def _offset_definition(*, two_dimensional: bool = False) -> dict[str, Any]:
    fields = {
        "source": parameters_schema(
            {"object_name": OBJECT_NAME_SCHEMA},
            ("object_name",),
        ),
        "value_mm": SIGNED_MM_SCHEMA,
        "mode": {
            "type": "string",
            "enum": ["skin", "pipe"] if two_dimensional else ["skin", "pipe", "recto_verso"],
        },
        "join": {
            "type": "string",
            "enum": ["arc", "tangent", "intersection"],
        },
        "intersection": {"type": "boolean"},
    }
    if not two_dimensional:
        fields["self_intersection"] = {"type": "boolean"}
    fields["fill"] = {"type": "boolean"}
    return parameters_schema(fields, tuple(fields))


def _projection_definition() -> dict[str, Any]:
    target = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "subelement": {
                "type": "string",
                "maxLength": 64,
                "pattern": r"^Face[1-9][0-9]*$",
            },
        },
        ("object_name", "subelement"),
    )
    source = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "subelement": {
                "type": "string",
                "maxLength": 64,
                "pattern": r"^(?:Edge|Wire|Face)[1-9][0-9]*$",
            },
        },
        ("object_name", "subelement"),
    )
    fields = {
        "target": target,
        "sources": {
            "type": "array",
            "items": source,
            "minItems": 1,
            "maxItems": 64,
            "uniqueItems": True,
        },
        "mode": {"type": "string", "enum": ["all", "faces", "edges"]},
        "height_mm": {"type": "number", "minimum": 0.0, "maximum": 999.0},
        "offset_mm": {"type": "number", "minimum": -999.0, "maximum": 999.0},
        "direction_xyz": {
            "type": "array",
            "items": {"type": "number", "minimum": -1.0, "maximum": 1.0},
            "minItems": 3,
            "maxItems": 3,
        },
    }
    return parameters_schema(fields, tuple(fields))


def _compound_definition() -> dict[str, Any]:
    return parameters_schema(
        {
            "sources": {
                "type": "array",
                "items": parameters_schema(
                    {"object_name": OBJECT_NAME_SCHEMA},
                    ("object_name",),
                ),
                "minItems": 1,
                "maxItems": 64,
                "uniqueItems": True,
            }
        },
        ("sources",),
    )


def _compound_filter_definition() -> dict[str, Any]:
    source = parameters_schema(
        {"object_name": OBJECT_NAME_SCHEMA},
        ("object_name",),
    )
    integer = {"type": "integer", "minimum": -1_000_000, "maximum": 1_000_000}
    selector = {
        "oneOf": [
            integer,
            {
                "type": "array",
                "items": {"oneOf": [integer, {"type": "null"}]},
                "minItems": 2,
                "maxItems": 3,
            },
        ]
    }
    schema = parameters_schema(
        {
            "source": source,
            "mode": {
                "type": "string",
                "enum": [
                    "bypass",
                    "specific_items",
                    "collision",
                    "volume",
                    "area",
                    "length",
                    "distance",
                ],
            },
            "stencil": {"oneOf": [source, {"type": "null"}]},
            "selectors": {
                "type": "array",
                "items": selector,
                "minItems": 1,
                "maxItems": 256,
            },
            "window_percent": {
                "type": "array",
                "items": {
                    "type": "number",
                    "minimum": -1_000_000.0,
                    "maximum": 1_000_000.0,
                },
                "minItems": 2,
                "maxItems": 2,
            },
            "maximum": {
                "oneOf": [
                    {
                        "type": "number",
                        "exclusiveMinimum": 0.0,
                        "maximum": 1.0e18,
                    },
                    {"type": "null"},
                ]
            },
            "invert": {"type": "boolean"},
        },
        ("source", "mode"),
    )
    schema["description"] = (
        "specific_items: selectors,invert; collision: stencil,invert; "
        "windows: stencil,window_percent,maximum,invert."
    )
    return schema


def _defeature_definition() -> dict[str, Any]:
    source = parameters_schema(
        {
            "object_name": OBJECT_NAME_SCHEMA,
            "faces": {
                "type": "array",
                "items": _FACE_NAME,
                "minItems": 1,
                "maxItems": 64,
                "uniqueItems": True,
            },
        },
        ("object_name", "faces"),
    )
    return parameters_schema(
        {
            "sources": {
                "type": "array",
                "items": source,
                "minItems": 1,
                "maxItems": 32,
                "uniqueItems": True,
            }
        },
        ("sources",),
    )


def model_part_capability_definition() -> NativeCapabilityDefinition:
    properties = {
        "label": LABEL_SCHEMA,
        "placement": placement_schema(),
        "definition": _primitive_definition(),
    }
    return NativeCapabilityDefinition(
        name="model.part",
        description="Create Part geometry.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="primitive",
                description="Create a Part primitive.",
                action_ids=frozenset({"Part_Primitives"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="NewPartPrimitive",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(properties, tuple(properties)),
            ),
            NativeCapabilityVariant(
                operation="builder",
                description="Build an Edge, Wire, Face, Shell, or Solid from exact shapes.",
                action_ids=frozenset({"Part_Builder"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactShapeInputs",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _builder_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="make_face",
                description="Create a parametric face from exact closed wires.",
                action_ids=frozenset({"Part_MakeFace"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentClosedWireSources",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _make_face_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="ruled_surface",
                description="Create a ruled surface between two exact edges or wires.",
                action_ids=frozenset({"Part_RuledSurface"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="TwoExactCurrentEdgesOrWires",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "definition": _ruled_surface_definition(),
                    },
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="cross_sections",
                description=(
                    "Create source-preserving planar cross-sections from exact "
                    "whole shapes or selected subelements."
                ),
                action_ids=frozenset({"Part_CrossSections"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentShapesAndPlaneSeries",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "definition": _cross_sections_definition(),
                    },
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="offset_3d",
                description=(
                    "Create one retained 3D offset from an exact current whole shape."
                ),
                action_ids=frozenset({"Part_Offset"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentWholeShape",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _offset_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="offset_2d",
                description=(
                    "Create one retained planar 2D offset from an exact current whole shape."
                ),
                action_ids=frozenset({"Part_Offset2D"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentWholePlanarShape",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "definition": _offset_definition(two_dimensional=True),
                    },
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="project_surface",
                description=(
                    "Project exact edges, wires, or faces onto one exact target face."
                ),
                action_ids=frozenset({"Part_ProjectionOnSurface"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentProjectionGeometry",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _projection_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="compound",
                description="Create one retained compound from ordered exact whole shapes.",
                action_ids=frozenset({"Part_Compound"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentWholeShapes",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _compound_definition()},
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="compound_filter",
                description="Filter direct child shapes from one exact Compound or CompSolid.",
                action_ids=frozenset({"Part_CompoundFilter"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentCompoundAndStencil?",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {
                        "label": LABEL_SCHEMA,
                        "definition": _compound_filter_definition(),
                    },
                    ("label", "definition"),
                ),
            ),
            NativeCapabilityVariant(
                operation="defeature",
                description=(
                    "Remove exact selected faces from ordered current shapes and "
                    "heal one standalone replacement result per source."
                ),
                action_ids=frozenset({"Part_Defeaturing"}),
                surface_ids=_MODEL_SURFACE,
                exact_target_type="ExactCurrentShapesAndFaces",
                transaction_behavior="document",
                background_required=False,
                parameters=parameters_schema(
                    {"label": LABEL_SCHEMA, "definition": _defeature_definition()},
                    ("label", "definition"),
                ),
            ),
        ),
    )


def register_model_part_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(model_part_capability_definition())
