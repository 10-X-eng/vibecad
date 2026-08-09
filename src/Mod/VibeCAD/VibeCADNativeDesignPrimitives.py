# SPDX-License-Identifier: LGPL-2.1-or-later

"""Validation and native property mapping for Design primitive geometry."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeDesignResults import (
    DesignResultSpec,
    create_design_operation,
)
from VibeCADNativeModelErrors import NativeModelError
from VibeCADNativeMutation import NativeMutationDraft


_BASE_FIELDS = frozenset({"label", "placement", "result"})
_GEOMETRY_FIELDS = {
    "design_box": ("length_mm", "width_mm", "height_mm"),
    "design_cylinder": ("radius_mm", "height_mm", "sweep_degrees"),
    "design_sphere": (
        "radius_mm",
        "latitude_start_degrees",
        "latitude_end_degrees",
        "sweep_degrees",
    ),
    "design_cone": ("radius1_mm", "radius2_mm", "height_mm", "sweep_degrees"),
    "design_ellipsoid": (
        "radius_x_mm",
        "radius_y_mm",
        "radius_z_mm",
        "latitude_start_degrees",
        "latitude_end_degrees",
        "sweep_degrees",
    ),
    "design_torus": (
        "major_radius_mm",
        "minor_radius_mm",
        "section_start_degrees",
        "section_end_degrees",
        "sweep_degrees",
    ),
    "design_prism": ("sides", "circumradius_mm", "height_mm"),
    "design_wedge": (
        "xmin_mm",
        "ymin_mm",
        "zmin_mm",
        "x2min_mm",
        "z2min_mm",
        "xmax_mm",
        "ymax_mm",
        "zmax_mm",
        "x2max_mm",
        "z2max_mm",
    ),
    "design_tube": ("outer_radius_mm", "inner_radius_mm", "height_mm"),
}
_TYPE_INFO = {
    "design_box": ("PartDesign::DesignBox", "Box"),
    "design_cylinder": ("PartDesign::DesignCylinder", "Cylinder"),
    "design_sphere": ("PartDesign::DesignSphere", "Sphere"),
    "design_cone": ("PartDesign::DesignCone", "Cone"),
    "design_ellipsoid": ("PartDesign::DesignEllipsoid", "Ellipsoid"),
    "design_torus": ("PartDesign::DesignTorus", "Torus"),
    "design_prism": ("PartDesign::DesignPrism", "Prism"),
    "design_wedge": ("PartDesign::DesignWedge", "Wedge"),
    "design_tube": ("PartDesign::DesignTube", "Tube"),
}


def primitive_argument_fields() -> dict[str, frozenset[str]]:
    return {
        operation: _BASE_FIELDS | frozenset(fields)
        for operation, fields in _GEOMETRY_FIELDS.items()
    }


def _finite(parameters: dict[str, float | int]) -> dict[str, float | int]:
    if not all(math.isfinite(float(value)) for value in parameters.values()):
        raise NativeModelError("Design primitive dimensions must be finite.")
    return parameters


def primitive_native_parameters(
    operation: str,
    values: Mapping[str, Any],
) -> dict[str, float | int]:
    if operation == "design_box":
        return _finite({
            "Length": float(values["length_mm"]),
            "Width": float(values["width_mm"]),
            "Height": float(values["height_mm"]),
        })
    if operation == "design_cylinder":
        return _finite({
            "Radius": float(values["radius_mm"]),
            "Height": float(values["height_mm"]),
            "Angle": float(values["sweep_degrees"]),
        })
    if operation == "design_sphere":
        start = float(values["latitude_start_degrees"])
        end = float(values["latitude_end_degrees"])
        if start >= end:
            raise NativeModelError("Sphere latitude start must be below latitude end.")
        return _finite({
            "Radius": float(values["radius_mm"]),
            "Angle1": start,
            "Angle2": end,
            "Angle3": float(values["sweep_degrees"]),
        })
    if operation == "design_cone":
        first = float(values["radius1_mm"])
        second = float(values["radius2_mm"])
        if first <= 0.0 and second <= 0.0:
            raise NativeModelError("A cone requires at least one positive radius.")
        return _finite({
            "Radius1": first,
            "Radius2": second,
            "Height": float(values["height_mm"]),
            "Angle": float(values["sweep_degrees"]),
        })
    if operation == "design_ellipsoid":
        start = float(values["latitude_start_degrees"])
        end = float(values["latitude_end_degrees"])
        if start >= end:
            raise NativeModelError("Ellipsoid latitude start must be below latitude end.")
        return _finite({
            "Radius1": float(values["radius_z_mm"]),
            "Radius2": float(values["radius_x_mm"]),
            "Radius3": float(values["radius_y_mm"]),
            "Angle1": start,
            "Angle2": end,
            "Angle3": float(values["sweep_degrees"]),
        })
    if operation == "design_torus":
        major = float(values["major_radius_mm"])
        minor = float(values["minor_radius_mm"])
        start = float(values["section_start_degrees"])
        end = float(values["section_end_degrees"])
        if minor >= major:
            raise NativeModelError("Torus minor radius must be smaller than major radius.")
        if start >= end:
            raise NativeModelError("Torus section start must be below section end.")
        return _finite({
            "Radius1": major,
            "Radius2": minor,
            "Angle1": start,
            "Angle2": end,
            "Angle3": float(values["sweep_degrees"]),
        })
    if operation == "design_prism":
        return _finite({
            "Polygon": int(values["sides"]),
            "Circumradius": float(values["circumradius_mm"]),
            "Height": float(values["height_mm"]),
        })
    if operation == "design_wedge":
        native = {
            name.removesuffix("_mm"): float(values[name])
            for name in _GEOMETRY_FIELDS[operation]
        }
        native = {name[0].upper() + name[1:]: value for name, value in native.items()}
        if (
            native["Xmax"] <= native["Xmin"]
            or native["Ymax"] <= native["Ymin"]
            or native["Zmax"] <= native["Zmin"]
        ):
            raise NativeModelError("Wedge maximum bounds must exceed minimum bounds.")
        return _finite(native)
    if operation == "design_tube":
        outer = float(values["outer_radius_mm"])
        inner = float(values["inner_radius_mm"])
        if inner >= outer:
            raise NativeModelError("Tube inner radius must be smaller than outer radius.")
        return _finite({
            "OuterRadius": outer,
            "InnerRadius": inner,
            "Height": float(values["height_mm"]),
        })
    raise NativeModelError("That Design primitive operation is unavailable.")


def create_design_primitive(
    document: Any,
    *,
    operation: str,
    label: str,
    native_parameters: Mapping[str, float | int],
    placement: Any,
    result_spec: DesignResultSpec,
) -> NativeMutationDraft:
    type_id, base_name = _TYPE_INFO[operation]

    def configure(feature: Any) -> Mapping[str, Any]:
        for property_name, property_value in native_parameters.items():
            setattr(feature, property_name, property_value)
        feature.Placement = placement
        return {
            "native_parameters": dict(native_parameters),
            "placement": placement,
        }

    def verify(feature: Any, expected: Mapping[str, Any]) -> Mapping[str, Any]:
        if feature.Placement != expected["placement"]:
            raise NativeModelError("The Design primitive placement changed before commit.")
        for property_name, property_value in expected["native_parameters"].items():
            actual = getattr(feature, property_name)
            number = float(getattr(actual, "Value", actual))
            if abs(number - float(property_value)) > 1.0e-8:
                raise NativeModelError("A Design primitive parameter changed before commit.")
        return {}

    return create_design_operation(
        document,
        type_id=type_id,
        base_name=base_name,
        label=label,
        result_spec=result_spec,
        configure=configure,
        verify_feature=verify,
    )
