# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for one-step B-spline degree elevation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    MAX_SKETCH_ELEMENTS,
    prepare_active_sketch_target,
)


LABEL = "Increase B-Spline Degree"
MAX_DEGREE_TARGETS = 256
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_indices",
    }
)


@dataclass(frozen=True, slots=True)
class SketchBSplineDegreeSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_indices: tuple[int, ...]


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _geometry_indices(value: Any) -> tuple[int, ...]:
    if (
        not isinstance(value, (list, tuple))
        or not 1 <= len(value) <= MAX_DEGREE_TARGETS
        or any(
            type(index) is not int or not 0 <= index < MAX_SKETCH_ELEMENTS
            for index in value
        )
        or len(set(value)) != len(value)
    ):
        raise NativeSketchError(
            f"{LABEL} requires a bounded ordered list of unique current internal B-spline indices."
        )
    return tuple(value)


def prepare_sketch_bspline_degree(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBSplineDegreeSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"An {LABEL} definition has incorrect fields.")
    return SketchBSplineDegreeSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        _geometry_indices(value["geometry_indices"]),
    )
