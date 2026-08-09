# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact geometric and mass-property reads for Native mode."""

from __future__ import annotations

import math
from typing import Any

from VibeCADNativeTargets import (
    NativeElementRef,
    NativeObjectRef,
    resolve_element,
    resolve_object,
)


MAX_MASS_OBJECTS = 16
DEFAULT_DENSITY_KG_PER_MM3 = 1.0e-6


class NativeMeasureError(RuntimeError):
    def failure(self) -> dict[str, str]:
        return {"error_code": "NATIVE_MEASURE_FAILED", "message": str(self)}


def _vector(value: Any) -> list[float]:
    try:
        return [float(value.x), float(value.y), float(value.z)]
    except Exception as exc:
        raise NativeMeasureError("A measured vector is unavailable.") from exc


def measure_distance(
    document: Any,
    first: NativeElementRef,
    second: NativeElementRef,
) -> dict[str, Any]:
    _first_object, first_shape = resolve_element(document, first)
    _second_object, second_shape = resolve_element(document, second)
    try:
        distance, point_pairs, _info = first_shape.distToShape(second_shape)
        closest = point_pairs[0] if point_pairs else None
    except Exception as exc:
        raise NativeMeasureError("The exact elements cannot be measured for distance.") from exc
    result: dict[str, Any] = {
        "distance_mm": float(distance),
        "first": first.summary(),
        "second": second.summary(),
    }
    if closest and len(closest) == 2:
        result["closest_points_mm"] = [_vector(closest[0]), _vector(closest[1])]
    return result


def _direction(element: Any) -> list[float]:
    shape_type = str(getattr(element, "ShapeType", "") or "")
    try:
        if shape_type == "Edge":
            parameter = 0.5 * (
                float(element.FirstParameter) + float(element.LastParameter)
            )
            return _vector(element.tangentAt(parameter))
        if shape_type == "Face":
            u_min, u_max, v_min, v_max = element.ParameterRange
            return _vector(
                element.normalAt(
                    0.5 * (float(u_min) + float(u_max)),
                    0.5 * (float(v_min) + float(v_max)),
                )
            )
    except Exception as exc:
        raise NativeMeasureError(
            "An angle target has no stable tangent or normal direction."
        ) from exc
    raise NativeMeasureError("Angle measurement requires exact edges or faces.")


def measure_angle(
    document: Any,
    first: NativeElementRef,
    second: NativeElementRef,
) -> dict[str, Any]:
    _first_object, first_shape = resolve_element(document, first)
    _second_object, second_shape = resolve_element(document, second)
    first_vector = _direction(first_shape)
    second_vector = _direction(second_shape)
    first_length = math.sqrt(sum(value * value for value in first_vector))
    second_length = math.sqrt(sum(value * value for value in second_vector))
    if first_length <= 1.0e-12 or second_length <= 1.0e-12:
        raise NativeMeasureError("An angle target has a zero-length direction.")
    cosine = sum(
        first_vector[index] * second_vector[index] for index in range(3)
    ) / (first_length * second_length)
    angle = math.degrees(math.acos(max(-1.0, min(1.0, cosine))))
    return {
        "angle_degrees": angle,
        "first": first.summary(),
        "second": second.summary(),
    }


def measure_radius(document: Any, target: NativeElementRef) -> dict[str, Any]:
    _obj, element = resolve_element(document, target)
    candidates = [
        getattr(getattr(element, "Curve", None), "Radius", None),
        getattr(getattr(element, "Surface", None), "Radius", None),
        getattr(element, "Radius", None),
    ]
    radius = None
    for value in candidates:
        try:
            candidate = float(value)
        except (TypeError, ValueError):
            continue
        if math.isfinite(candidate) and candidate > 0.0:
            radius = candidate
            break
    if radius is None:
        raise NativeMeasureError("The exact element has no circular radius.")
    return {"radius_mm": radius, "target": target.summary()}


def _shape_material(obj: Any) -> Any | None:
    visited = set()
    candidate = obj
    while candidate is not None and id(candidate) not in visited:
        visited.add(id(candidate))
        material = getattr(candidate, "ShapeMaterial", None)
        if material is not None:
            return material
        linked = getattr(candidate, "getLinkedObject", None)
        candidate = linked(False) if callable(linked) else None
    return None


def _density(obj: Any) -> tuple[float, str]:
    material = _shape_material(obj)
    if material is None:
        return DEFAULT_DENSITY_KG_PER_MM3, "default_1000_kg_m3"
    material_name = str(getattr(material, "Name", "") or "").strip()
    try:
        if not material_name:
            material_name = str(material.getName()).strip()
    except Exception:
        pass
    if material_name == "Default":
        return DEFAULT_DENSITY_KG_PER_MM3, "default_1000_kg_m3"
    try:
        has_density = material.hasPhysicalProperty("Density")
    except Exception:
        has_density = False
    if not has_density:
        return DEFAULT_DENSITY_KG_PER_MM3, "default_1000_kg_m3"
    try:
        getter = getattr(material, "getPhysicalQuantity", None)
        quantity = (
            getter("Density")
            if callable(getter)
            else material.getPhysicalValue("Density")
        )
        density = float(quantity.getValueAs("kg/mm^3"))
    except Exception as exc:
        raise NativeMeasureError("The object's material Density is unreadable.") from exc
    if not math.isfinite(density) or density <= 0.0:
        raise NativeMeasureError("The object's material Density must be positive.")
    return density, "shape_material"


def mass_properties(
    document: Any,
    targets: tuple[NativeObjectRef, ...],
) -> dict[str, Any]:
    if not targets or len(targets) > MAX_MASS_OBJECTS:
        raise NativeMeasureError("Mass properties require 1 to 16 exact objects.")
    objects = [resolve_object(document, target) for target in targets]
    if len({obj.Name for obj in objects}) != len(objects):
        raise NativeMeasureError("Mass-property targets must be unique.")
    total_volume = 0.0
    total_area = 0.0
    total_mass = 0.0
    weighted_mass_center = [0.0, 0.0, 0.0]
    weighted_volume_center = [0.0, 0.0, 0.0]
    items = []
    for obj, target in zip(objects, targets):
        shape = getattr(obj, "Shape", None)
        is_null = getattr(shape, "isNull", None)
        if shape is None or (callable(is_null) and bool(is_null())):
            raise NativeMeasureError("A mass-property target has no usable shape.")
        volume = abs(float(getattr(shape, "Volume", 0.0) or 0.0))
        area = abs(float(getattr(shape, "Area", 0.0) or 0.0))
        density, density_source = _density(obj)
        mass = volume * density
        center = _vector(shape.CenterOfMass)
        total_volume += volume
        total_area += area
        total_mass += mass
        for index in range(3):
            weighted_mass_center[index] += center[index] * mass
            weighted_volume_center[index] += center[index] * volume
        items.append(
            {
                "target": target.summary(),
                "volume_mm3": volume,
                "area_mm2": area,
                "mass_kg": mass,
                "density_kg_m3": density * 1.0e9,
                "density_source": density_source,
            }
        )
    return {
        "volume_mm3": total_volume,
        "area_mm2": total_area,
        "mass_kg": total_mass,
        "center_of_mass_mm": [
            value / total_mass if total_mass > 0.0 else 0.0
            for value in weighted_mass_center
        ],
        "center_of_volume_mm": [
            value / total_volume if total_volume > 0.0 else 0.0
            for value in weighted_volume_center
        ],
        "objects": items,
    }
