# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target for the human Sketch Offset command."""

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


LABEL = "Sketch Offset"
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_reference_count",
        "expected_external_geometry_count",
        "geometry_indices",
        "offset_distance",
        "join_type",
        "source_mode",
    }
)
_DISTANCE_FIELDS = frozenset({"value", "unit"})
JOIN_TYPES = frozenset({"arc", "intersection"})
SOURCE_MODES = frozenset({"keep", "delete", "constrain"})
JOIN_VALUES = {"arc": 0, "intersection": 2}
SOURCE_VALUES = {"keep": 0, "delete": 1, "constrain": 2}
_MIN_OFFSET_DISTANCE_MM = 1.0e-7
_MAX_OFFSET_DISTANCE_MM = 1_000_000_000.0


@dataclass(frozen=True, slots=True)
class SketchOffsetSpec:
    target: ActiveSketchTargetSpec
    expected_external_reference_count: int
    expected_external_geometry_count: int
    geometry_indices: tuple[int, ...]
    offset_length_mm: float
    join_type: str
    source_mode: str

    @property
    def join_value(self) -> int:
        return JOIN_VALUES[self.join_type]

    @property
    def source_value(self) -> int:
        return SOURCE_VALUES[self.source_mode]


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_SKETCH_ELEMENTS:
        raise NativeSketchError(
            f"{LABEL} {field} must be an integer from 0 to {MAX_SKETCH_ELEMENTS}."
        )
    return value


def _distance(value: Any) -> float:
    if not isinstance(value, Mapping) or set(value) != _DISTANCE_FIELDS:
        raise NativeSketchError(f"{LABEL} offset distance has incorrect fields.")
    if value["unit"] != "mm":
        raise NativeSketchError(f"{LABEL} offset distance unit must be mm.")
    number = value["value"]
    if isinstance(number, bool) or not isinstance(number, (int, float)):
        raise NativeSketchError(f"{LABEL} offset distance must be a number.")
    distance = float(number)
    if (
        not math.isfinite(distance)
        or abs(distance) <= _MIN_OFFSET_DISTANCE_MM
        or abs(distance) > _MAX_OFFSET_DISTANCE_MM
    ):
        raise NativeSketchError(
            f"{LABEL} requires a finite signed distance whose magnitude is greater "
            f"than {_MIN_OFFSET_DISTANCE_MM:g} mm and no greater than "
            f"{_MAX_OFFSET_DISTANCE_MM:g} mm."
        )
    return distance


def _choice(value: Any, choices: frozenset[str], field: str) -> str:
    if not isinstance(value, str) or value not in choices:
        allowed = ", ".join(sorted(choices))
        raise NativeSketchError(f"{LABEL} {field} must be one of: {allowed}.")
    return value


def prepare_sketch_offset(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchOffsetSpec:
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
    return SketchOffsetSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _count(value["expected_external_reference_count"], "external reference count"),
        _count(value["expected_external_geometry_count"], "external geometry count"),
        tuple(raw_indices),
        _distance(value["offset_distance"]),
        _choice(value["join_type"], JOIN_TYPES, "join type"),
        _choice(value["source_mode"], SOURCE_MODES, "source mode"),
    )
