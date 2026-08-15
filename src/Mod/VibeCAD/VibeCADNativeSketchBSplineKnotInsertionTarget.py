# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for one B-spline knot insertion."""

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


LABEL = "Insert Knot"
MAX_PARAMETER = 1_000_000_000.0
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_index",
        "parameter",
    }
)


@dataclass(frozen=True, slots=True)
class SketchBSplineKnotInsertionSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_index: int
    parameter: float


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


def _parameter(value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeSketchError(f"{LABEL} parameter must be a number.")
    result = float(value)
    if not math.isfinite(result) or not -MAX_PARAMETER <= result <= MAX_PARAMETER:
        raise NativeSketchError(
            f"{LABEL} parameter must be finite and within {MAX_PARAMETER:g}."
        )
    return result


def prepare_sketch_bspline_knot_insertion(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBSplineKnotInsertionSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"An {LABEL} definition has incorrect fields.")
    return SketchBSplineKnotInsertionSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        _geometry_index(value["geometry_index"]),
        _parameter(value["parameter"]),
    )
