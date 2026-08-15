# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for one-step B-spline degree reduction."""

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


LABEL = "Decrease B-Spline Degree"
MAX_DEVIATION_MM = 1_000_000.0
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_index",
        "maximum_deviation_mm",
    }
)


@dataclass(frozen=True, slots=True)
class SketchBSplineDegreeDecreaseSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_index: int
    maximum_deviation_mm: float


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _geometry_index(value: Any) -> int:
    if type(value) is not int or not 0 <= value < MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} requires one current internal B-spline geometry index."
        )
    return value


def _deviation(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeSketchError(f"{LABEL} maximum_deviation_mm must be a number.")
    result = float(value)
    if not math.isfinite(result) or not 0.0 <= result <= MAX_DEVIATION_MM:
        raise NativeSketchError(
            f"{LABEL} maximum_deviation_mm must be from 0 to {MAX_DEVIATION_MM}."
        )
    return result


def prepare_sketch_bspline_degree_decrease(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBSplineDegreeDecreaseSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    return SketchBSplineDegreeDecreaseSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        _geometry_index(value["geometry_index"]),
        _deviation(value["maximum_deviation_mm"]),
    )
