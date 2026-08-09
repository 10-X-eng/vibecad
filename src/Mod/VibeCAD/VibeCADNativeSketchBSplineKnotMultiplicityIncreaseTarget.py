# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for one-step B-spline knot multiplicity increase."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    MAX_SKETCH_ELEMENTS,
    prepare_active_sketch_target,
)


LABEL = "Increase Knot Multiplicity"
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_index",
        "knot_index",
    }
)


@dataclass(frozen=True, slots=True)
class SketchBSplineKnotMultiplicityIncreaseSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_index: int
    knot_index: int


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _index(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value < MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(f"{LABEL} requires one current zero-based {field}.")
    return value


def prepare_sketch_bspline_knot_multiplicity_increase(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBSplineKnotMultiplicityIncreaseSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"An {LABEL} definition has incorrect fields.")
    return SketchBSplineKnotMultiplicityIncreaseSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        _index(value["geometry_index"], "internal B-spline geometry index"),
        _index(value["knot_index"], "B-spline knot index"),
    )
