# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider contract for one atomic client-referenced Sketch batch."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeDesignSchema import (
    OBJECT_NAME_SCHEMA,
    SIGNED_MM_SCHEMA,
    parameters_schema,
)
from VibeCADNativeSketchBatchPlan import (
    BATCH_LOCAL_REF_PATTERN,
    MAX_BATCH_CONSTRAINTS,
    MAX_BATCH_GEOMETRY,
)


_LOCAL_REF_SCHEMA = {
    "type": "string",
    "maxLength": 32,
    "pattern": BATCH_LOCAL_REF_PATTERN,
}
_POSITIVE_MM_SCHEMA = {
    "type": "number",
    "exclusiveMinimum": 1.0e-9,
    "maximum": 1_000_000.0,
}


def _point_2d_schema() -> dict:
    return parameters_schema(
        {"x": SIGNED_MM_SCHEMA, "y": SIGNED_MM_SCHEMA},
        ("x", "y"),
    )


def _geometry_item_schema() -> dict:
    return parameters_schema(
        {
            "ref": _LOCAL_REF_SCHEMA,
            "kind": {
                "type": "string",
                "enum": ["point", "line", "circle", "arc"],
                "description": (
                    "Fields: point=position_mm; line=start_mm,end_mm; "
                    "circle=center_mm,radius_mm; arc=center_mm,radius_mm,"
                    "start_angle_degrees,end_angle_degrees (counterclockwise)."
                ),
            },
            "construction": {"type": "boolean"},
            "position_mm": _point_2d_schema(),
            "start_mm": _point_2d_schema(),
            "end_mm": _point_2d_schema(),
            "center_mm": _point_2d_schema(),
            "radius_mm": _POSITIVE_MM_SCHEMA,
            "start_angle_degrees": {
                "type": "number",
                "minimum": -360.0,
                "maximum": 360.0,
            },
            "end_angle_degrees": {
                "type": "number",
                "minimum": -360.0,
                "maximum": 360.0,
            },
        },
        ("ref", "kind", "construction"),
    )


def _point_ref_schema() -> dict:
    return {
        "description": "Sketch origin {origin:true} or request-local geometry endpoint.",
        "oneOf": [
            parameters_schema(
                {
                    "origin": {"type": "boolean", "const": True},
                    "position": {
                        "type": "string",
                        "const": "point",
                        "description": "Canonical origin point normalization.",
                    },
                },
                ("origin",),
            ),
            parameters_schema(
                {
                    "geometry_ref": _LOCAL_REF_SCHEMA,
                    "position": {
                        "type": "string",
                        "enum": ["point", "start", "end", "center"],
                    },
                },
                ("geometry_ref", "position"),
            ),
        ]
    }


def _constraint_item_schema() -> dict:
    return parameters_schema(
        {
            "ref": _LOCAL_REF_SCHEMA,
            "kind": {
                "type": "string",
                "enum": [
                    "coincident",
                    "horizontal",
                    "vertical",
                    "parallel",
                    "perpendicular",
                    "equal",
                    "distance_x",
                    "distance_y",
                    "distance",
                    "radius",
                    "diameter",
                    "angle",
                ],
                "description": (
                    "Fields: coincident=first,second; horizontal|vertical="
                    "geometry_ref; parallel|perpendicular|equal="
                    "first_geometry_ref,second_geometry_ref; distance_x|"
                    "distance_y|distance=first,second,value_mm; radius|diameter="
                    "geometry_ref,value_mm; angle=first_geometry_ref,"
                    "second_geometry_ref,value_degrees."
                ),
            },
            "first": _point_ref_schema(),
            "second": _point_ref_schema(),
            "geometry_ref": _LOCAL_REF_SCHEMA,
            "first_geometry_ref": _LOCAL_REF_SCHEMA,
            "second_geometry_ref": _LOCAL_REF_SCHEMA,
            "value_mm": SIGNED_MM_SCHEMA,
            "value_degrees": {
                "type": "number",
                "minimum": -180.0,
                "maximum": 360.0,
            },
        },
        ("ref", "kind"),
    )


def _batch_parameters() -> dict:
    return parameters_schema(
        {
            "sketch": parameters_schema(
                {"object_name": OBJECT_NAME_SCHEMA},
                ("object_name",),
            ),
            "expected_geometry_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "expected_constraint_count": {
                "type": "integer",
                "minimum": 0,
                "maximum": 1_000_000,
            },
            "geometry": {
                "type": "array",
                "items": _geometry_item_schema(),
                "minItems": 1,
                "maxItems": MAX_BATCH_GEOMETRY,
            },
            "constraints": {
                "type": "array",
                "items": _constraint_item_schema(),
                "minItems": 1,
                "maxItems": MAX_BATCH_CONSTRAINTS,
                "description": (
                    f"Create 1 through {MAX_BATCH_CONSTRAINTS} constraints in this "
                    "same atomic request. Constraint refs are request-local."
                ),
            },
        },
        (
            "sketch",
            "expected_geometry_count",
            "expected_constraint_count",
            "geometry",
            "constraints",
        ),
    )


def sketch_batch_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name="sketch.batch",
        description=(
            "Create bounded Sketch geometry and constraints atomically with "
            "client-local references."
        ),
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create",
                description=(
                    f"Create 1-{MAX_BATCH_GEOMETRY} primitive elements and "
                    f"1-{MAX_BATCH_CONSTRAINTS} feasible constraints in one exact "
                    "transaction. Local refs exist only inside this request."
                ),
                action_ids=frozenset({"VibeCAD_NativeSketchBatch"}),
                surface_ids=frozenset({"sketch.edit"}),
                exact_target_type="ActiveSketchAndExpectedStateCounts",
                transaction_behavior="document",
                background_required=False,
                parameters=_batch_parameters(),
            ),
        ),
    )


def register_sketch_batch_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_shared_definition(sketch_batch_capability_definition())
