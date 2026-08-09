# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded provider variants for Design-native primitive operations."""

from __future__ import annotations

from typing import Any

from VibeCADNativeCapabilityRegistry import NativeCapabilityVariant
from VibeCADNativeDesignSchema import (
    LABEL_SCHEMA,
    NONNEGATIVE_MM_SCHEMA,
    POSITIVE_MM_SCHEMA,
    SIGNED_MM_SCHEMA,
    design_result_schema,
    parameters_schema,
    placement_schema,
)


MODEL_SURFACE = frozenset({"model"})
_FULL_ANGLE = {
    "type": "number",
    "exclusiveMinimum": 0.0,
    "maximum": 360.0,
}
_LATITUDE = {
    "type": "number",
    "minimum": -90.0,
    "maximum": 90.0,
}
_TORUS_LATITUDE = {
    "type": "number",
    "minimum": -180.0,
    "maximum": 180.0,
}


def _primitive_definition() -> dict[str, Any]:
    properties = {
        "kind": {
            "type": "string",
            "enum": [
                "box",
                "cylinder",
                "sphere",
                "cone",
                "ellipsoid",
                "torus",
                "prism",
                "wedge",
                "tube",
            ],
        },
        "length_mm": POSITIVE_MM_SCHEMA,
        "width_mm": POSITIVE_MM_SCHEMA,
        "height_mm": POSITIVE_MM_SCHEMA,
        "radius_mm": POSITIVE_MM_SCHEMA,
        "sweep_degrees": _FULL_ANGLE,
        "latitude_start_degrees": _LATITUDE,
        "latitude_end_degrees": _LATITUDE,
        "radius1_mm": NONNEGATIVE_MM_SCHEMA,
        "radius2_mm": NONNEGATIVE_MM_SCHEMA,
        "radius_x_mm": POSITIVE_MM_SCHEMA,
        "radius_y_mm": POSITIVE_MM_SCHEMA,
        "radius_z_mm": POSITIVE_MM_SCHEMA,
        "major_radius_mm": POSITIVE_MM_SCHEMA,
        "minor_radius_mm": POSITIVE_MM_SCHEMA,
        "section_start_degrees": _TORUS_LATITUDE,
        "section_end_degrees": _TORUS_LATITUDE,
        "sides": {"type": "integer", "minimum": 3, "maximum": 128},
        "circumradius_mm": POSITIVE_MM_SCHEMA,
        "xmin_mm": SIGNED_MM_SCHEMA,
        "ymin_mm": SIGNED_MM_SCHEMA,
        "zmin_mm": SIGNED_MM_SCHEMA,
        "x2min_mm": SIGNED_MM_SCHEMA,
        "z2min_mm": SIGNED_MM_SCHEMA,
        "xmax_mm": SIGNED_MM_SCHEMA,
        "ymax_mm": SIGNED_MM_SCHEMA,
        "zmax_mm": SIGNED_MM_SCHEMA,
        "x2max_mm": SIGNED_MM_SCHEMA,
        "z2max_mm": SIGNED_MM_SCHEMA,
        "outer_radius_mm": POSITIVE_MM_SCHEMA,
        "inner_radius_mm": NONNEGATIVE_MM_SCHEMA,
    }
    schema = parameters_schema(properties, ("kind",))
    schema["description"] = (
        "Kind fields: box length/width/height; cylinder radius/height/sweep; "
        "sphere radius/latitudes/sweep; cone radius1/radius2/height/sweep; "
        "ellipsoid radius_x/radius_y/radius_z/latitudes/sweep; torus major/minor "
        "radius/sections/sweep; prism sides/circumradius/height; wedge all min/max "
        "coordinates; tube outer/inner radius/height. Field names include units."
    )
    return schema


def model_primitive_variants() -> tuple[NativeCapabilityVariant, ...]:
    properties = {
        "label": LABEL_SCHEMA,
        "placement": placement_schema(),
        "result": design_result_schema(),
        "definition": _primitive_definition(),
    }
    return (
        NativeCapabilityVariant(
            operation="primitive",
            description="Create or apply one typed placed Design primitive.",
            action_ids=frozenset(
                {
                    "PartDesign::DesignBox",
                    "PartDesign::DesignCylinder",
                    "PartDesign::DesignSphere",
                    "PartDesign::DesignCone",
                    "PartDesign::DesignEllipsoid",
                    "PartDesign::DesignTorus",
                    "PartDesign::DesignPrism",
                    "PartDesign::DesignWedge",
                    "PartDesign::DesignTube",
                }
            ),
            surface_ids=MODEL_SURFACE,
            exact_target_type="DesignResult",
            transaction_behavior="document",
            background_required=False,
            parameters=parameters_schema(properties, tuple(properties)),
        ),
    )
