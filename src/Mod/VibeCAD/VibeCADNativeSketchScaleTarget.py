# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Sketch Scale command."""

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


LABEL = "Sketch Scale"
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_indices",
        "center_mm",
        "scale_factor",
        "keep_originals",
    }
)
_VECTOR_FIELDS = frozenset({"x", "y"})
_MAX_SCALE_FACTOR = 1_000_000.0
_MIN_SCALE_FACTOR = 1.0e-7


@dataclass(frozen=True, slots=True)
class SketchScaleSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_indices: tuple[int, ...]
    center_mm: tuple[float, float]
    scale_factor: float
    keep_originals: bool
    allow_origin_constraints: bool = False


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


def _factor(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeSketchError(f"{LABEL} factor must be a number.")
    factor = float(value)
    if (
        not math.isfinite(factor)
        or factor <= _MIN_SCALE_FACTOR
        or factor > _MAX_SCALE_FACTOR
    ):
        raise NativeSketchError(
            f"{LABEL} factor must be greater than {_MIN_SCALE_FACTOR:g} "
            f"and no greater than {_MAX_SCALE_FACTOR:g}."
        )
    return factor


def prepare_sketch_scale(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchScaleSpec:
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
    keep_originals = value["keep_originals"]
    if type(keep_originals) is not bool:
        raise NativeSketchError(f"{LABEL} keep originals must be true or false.")
    return SketchScaleSpec(
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
        _factor(value["scale_factor"]),
        keep_originals,
    )
