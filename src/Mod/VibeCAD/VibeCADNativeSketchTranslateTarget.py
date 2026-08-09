# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Sketch Translate / rectangular-array command."""

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


LABEL = "Sketch Translate"
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_indices",
        "first_translation_mm",
        "copy_count",
        "second_translation_mm",
        "row_count",
        "constraint_mode",
    }
)
_VECTOR_FIELDS = frozenset({"x", "y"})
_CONSTRAINT_MODES = frozenset({"copy", "equalize_dimensions"})


@dataclass(frozen=True, slots=True)
class SketchTranslateSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_indices: tuple[int, ...]
    first_translation_mm: tuple[float, float]
    copy_count: int
    second_translation_mm: tuple[float, float]
    row_count: int
    constraint_mode: str
    equalize_dimensional_constraints: bool


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _vector(value: Any, field: str) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != _VECTOR_FIELDS:
        raise NativeSketchError(f"{LABEL} {field} has incorrect fields.")
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
                f"{LABEL} {field} requires finite millimetre coordinates."
            )
        result.append(float(coordinate))
    return result[0], result[1]


def _nonzero(vector: tuple[float, float]) -> bool:
    return math.hypot(*vector) > 1.0e-7


def prepare_sketch_translate(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchTranslateSpec:
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
    first = _vector(value["first_translation_mm"], "first translation")
    second = _vector(value["second_translation_mm"], "second translation")
    if not _nonzero(first):
        raise NativeSketchError(f"{LABEL} first translation must be nonzero.")
    copy_count = value["copy_count"]
    row_count = value["row_count"]
    if type(copy_count) is not int or not 0 <= copy_count <= 9_999:
        raise NativeSketchError(f"{LABEL} copy count must be from 0 through 9999.")
    if type(row_count) is not int or not 1 <= row_count <= 9_999:
        raise NativeSketchError(f"{LABEL} row count must be from 1 through 9999.")
    if row_count == 1 and _nonzero(second):
        raise NativeSketchError(f"{LABEL} second translation must be zero for one row.")
    if row_count > 1 and not _nonzero(second):
        raise NativeSketchError(
            f"{LABEL} second translation must be nonzero for multiple rows."
        )
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
    created_instances = copies_to_make * row_count + row_count - 1
    if created_instances * len(raw_indices) > MAX_TRANSFORM_INSTANCES:
        raise NativeSketchError(
            f"{LABEL} would create too many geometry instances in one operation."
        )
    return SketchTranslateSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        tuple(raw_indices),
        first,
        copy_count,
        second,
        row_count,
        mode,
        mode == "equalize_dimensions",
    )
