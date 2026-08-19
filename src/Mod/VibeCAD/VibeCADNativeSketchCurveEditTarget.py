# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact corner-or-curve-pair targets for Sketch curve edits."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    prepare_active_sketch_target,
)


MAX_EXTERNAL_SKETCH_GEOMETRY = 1_000_000
_BASE_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "target",
        "preserve_corner",
    }
)
_CORNER_FIELDS = frozenset({"form", "geometry_index", "position"})
_PAIR_FIELDS = frozenset({"form", "curves"})
_CURVE_FIELDS = frozenset({"geometry_index", "reference_point_mm"})
_POINT_FIELDS = frozenset({"x", "y"})
_POSITION_CODES = {"start": 1, "end": 2}


@dataclass(frozen=True, slots=True)
class SketchCurveEditCorner:
    geometry_index: int
    position: str

    @property
    def position_code(self) -> int:
        return _POSITION_CODES[self.position]


@dataclass(frozen=True, slots=True)
class SketchCurveEditCurve:
    geometry_index: int
    reference_point_mm: tuple[float, float]


@dataclass(frozen=True, slots=True)
class SketchCurveEditPair:
    curves: tuple[SketchCurveEditCurve, SketchCurveEditCurve]


@dataclass(frozen=True, slots=True)
class SketchCurveEditSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    selection: SketchCurveEditCorner | SketchCurveEditPair
    requested_size_mm: float
    preserve_corner: bool

    @property
    def form(self) -> str:
        return (
            "corner"
            if isinstance(self.selection, SketchCurveEditCorner)
            else "curve_pair"
        )


def _geometry_index(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value < 1_000_000:
        raise NativeSketchError(
            f"A {label} geometry_index must be an integer from 0 to 999999."
        )
    return value


def _point(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != _POINT_FIELDS:
        raise NativeSketchError(f"A {label} reference point has incorrect fields.")
    result = []
    for axis in ("x", "y"):
        raw = value[axis]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise NativeSketchError(f"A {label} reference point must be numeric.")
        coordinate = float(raw)
        if not math.isfinite(coordinate) or abs(coordinate) > 1_000_000.0:
            raise NativeSketchError(
                f"A {label} reference point must be within 1000000 mm."
            )
        result.append(coordinate)
    return result[0], result[1]


def _curve(value: Any, label: str) -> SketchCurveEditCurve:
    if not isinstance(value, Mapping) or set(value) != _CURVE_FIELDS:
        raise NativeSketchError(f"A {label} curve target has incorrect fields.")
    return SketchCurveEditCurve(
        _geometry_index(value["geometry_index"], label),
        _point(value["reference_point_mm"], label),
    )


def _selection(
    value: Any,
    label: str,
) -> SketchCurveEditCorner | SketchCurveEditPair:
    if not isinstance(value, Mapping):
        raise NativeSketchError(f"A {label} target must be an object.")
    form = value.get("form")
    if form == "corner":
        if set(value) != _CORNER_FIELDS:
            raise NativeSketchError(f"A corner {label} target has incorrect fields.")
        position = value["position"]
        if not isinstance(position, str) or position not in _POSITION_CODES:
            raise NativeSketchError(f"A corner {label} position must be start or end.")
        return SketchCurveEditCorner(
            _geometry_index(value["geometry_index"], label),
            position,
        )
    if form == "curve_pair":
        if set(value) != _PAIR_FIELDS:
            raise NativeSketchError(
                f"A curve-pair {label} target has incorrect fields."
            )
        raw_curves = value["curves"]
        if not isinstance(raw_curves, list) or len(raw_curves) != 2:
            raise NativeSketchError(
                f"A curve-pair {label} target requires exactly two curves."
            )
        first, second = (_curve(item, label) for item in raw_curves)
        if first.geometry_index == second.geometry_index:
            raise NativeSketchError(f"The two {label} curves must be distinct.")
        return SketchCurveEditPair((first, second))
    raise NativeSketchError(f"A {label} target form must be corner or curve_pair.")


def _external_count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= MAX_EXTERNAL_SKETCH_GEOMETRY:
        raise NativeSketchError(
            "Sketch expected_external_geometry_count must be an integer from 0 to "
            f"{MAX_EXTERNAL_SKETCH_GEOMETRY}."
        )
    return value


def _positive_size(value: Any, label: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise NativeSketchError(f"{label} must be a positive millimeter value.")
    result = float(value)
    if not math.isfinite(result) or not 0.0 < result <= 1_000_000.0:
        raise NativeSketchError(f"{label} must be greater than 0 and at most 1000000 mm.")
    return result


def prepare_sketch_curve_edit_target(
    document_uid: str,
    value: Mapping[str, Any],
    *,
    label: str,
    size_field: str,
) -> SketchCurveEditSpec:
    if not isinstance(label, str) or not label:
        raise TypeError("label must be a nonempty string")
    if not isinstance(size_field, str) or not size_field:
        raise TypeError("size_field must be a nonempty string")
    if not isinstance(value, Mapping) or set(value) != {*_BASE_FIELDS, size_field}:
        raise NativeSketchError(f"A {label} definition has incorrect fields.")
    preserve = value["preserve_corner"]
    if type(preserve) is not bool:
        raise NativeSketchError(f"{label} preserve_corner must be a boolean.")
    return SketchCurveEditSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _external_count(value["expected_external_geometry_count"]),
        _selection(value["target"], label),
        _positive_size(value[size_field], f"{label} {size_field}"),
        preserve,
    )
