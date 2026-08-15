# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Sketch Rotate / polar-transform command."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    MAX_SKETCH_ELEMENTS,
    prepare_active_sketch_target,
)
from VibeCADNativeSketchTransformTarget import MAX_TRANSFORM_INSTANCES


LABEL = "Sketch Rotate"
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_indices",
        "center_mm",
        "total_angle",
        "copy_count",
        "constraint_mode",
    }
)
_VECTOR_FIELDS = frozenset({"x", "y"})
_ANGLE_FIELDS = frozenset({"value", "unit"})
_CONSTRAINT_MODES = frozenset({"copy", "equalize_dimensions"})


@dataclass(frozen=True, slots=True)
class SketchRotateSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_indices: tuple[int, ...]
    center_mm: tuple[float, float]
    total_angle_degrees: float
    total_angle_radians: float
    copy_count: int
    constraint_mode: str
    equalize_dimensional_constraints: bool


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _center(value: Any) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != _VECTOR_FIELDS:
        raise NativeSketchError(f"{LABEL} center has incorrect fields.")
    result = []
    for axis in ("x", "y"):
        coordinate = value[axis]
        if (
            isinstance(coordinate, bool)
            or not isinstance(coordinate, (int, float))
            or not math.isfinite(float(coordinate))
            or abs(float(coordinate)) > 1_000_000_000.0
        ):
            raise NativeSketchError(
                f"{LABEL} center requires finite millimetre coordinates."
            )
        result.append(float(coordinate))
    return result[0], result[1]


def _angle(value: Any) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != _ANGLE_FIELDS:
        raise NativeSketchError(f"{LABEL} total angle has incorrect fields.")
    if value["unit"] != "deg":
        raise NativeSketchError(f"{LABEL} total angle requires unit deg.")
    raw = value["value"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise NativeSketchError(f"{LABEL} total angle must be a number.")
    degrees = float(raw)
    if (
        not math.isfinite(degrees)
        or abs(degrees) <= 1.0e-7
        or abs(degrees) >= 360.0 - 1.0e-7
    ):
        raise NativeSketchError(
            f"{LABEL} total angle must be nonzero and strictly between -360 and 360 degrees."
        )
    return degrees, math.radians(degrees)


def prepare_sketch_rotate(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchRotateSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    raw_indices = value["geometry_indices"]
    if (
        not isinstance(raw_indices, (list, tuple))
        or not 1 <= len(raw_indices) <= MAX_TRANSFORM_INSTANCES
        or any(
            type(index) is not int
            or not -MAX_SKETCH_ELEMENTS <= index < MAX_SKETCH_ELEMENTS
            for index in raw_indices
        )
        or len(set(raw_indices)) != len(raw_indices)
    ):
        raise NativeSketchError(
            f"{LABEL} requires a bounded ordered list of unique geometry indices."
        )
    copy_count = value["copy_count"]
    if type(copy_count) is not int or not 0 <= copy_count <= 9_999:
        raise NativeSketchError(f"{LABEL} copy count must be from 0 through 9999.")
    mode = value["constraint_mode"]
    if mode not in _CONSTRAINT_MODES:
        raise NativeSketchError(
            f"{LABEL} constraint mode must be 'copy' or 'equalize_dimensions'."
        )
    if copy_count == 0 and mode != "copy":
        raise NativeSketchError(
            f"{LABEL} move mode cannot request ignored Equal constraints."
        )
    copies_to_make = 1 if copy_count == 0 else copy_count
    if copies_to_make * len(raw_indices) > MAX_TRANSFORM_INSTANCES:
        raise NativeSketchError(
            f"{LABEL} would create too many geometry instances in one operation."
        )
    degrees, radians = _angle(value["total_angle"])
    return SketchRotateSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        tuple(raw_indices),
        _center(value["center_mm"]),
        degrees,
        radians,
        copy_count,
        mode,
        mode == "equalize_dimensions",
    )
