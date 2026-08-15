# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared closed target for one exact curve and one exact sketch point."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    prepare_active_sketch_target,
)


_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "target",
    }
)
_TARGET_FIELDS = frozenset({"geometry_index", "reference_point_mm"})
_POINT_FIELDS = frozenset({"x", "y"})


@dataclass(frozen=True, slots=True)
class SketchCurvePointSelection:
    geometry_index: int
    reference_point_mm: tuple[float, float]


@dataclass(frozen=True, slots=True)
class SketchCurvePointSpec:
    target: ActiveSketchTargetSpec
    expected_external_geometry_count: int
    selection: SketchCurvePointSelection


def _bounded_index(value: Any, label: str) -> int:
    if type(value) is not int or not 0 <= value < 1_000_000:
        raise NativeSketchError(
            f"{label} geometry_index must be an integer from 0 to 999999."
        )
    return value


def _bounded_point(value: Any, label: str) -> tuple[float, float]:
    if not isinstance(value, Mapping) or set(value) != _POINT_FIELDS:
        raise NativeSketchError(f"{label} reference_point_mm has incorrect fields.")
    result = []
    for axis in ("x", "y"):
        raw = value[axis]
        if isinstance(raw, bool) or not isinstance(raw, (int, float)):
            raise NativeSketchError(f"{label} reference point must be numeric.")
        coordinate = float(raw)
        if not math.isfinite(coordinate) or abs(coordinate) > 1_000_000.0:
            raise NativeSketchError(
                f"{label} reference point must be within 1000000 mm."
            )
        result.append(coordinate)
    return result[0], result[1]


def _external_count(value: Any) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise NativeSketchError(
            "Sketch expected_external_geometry_count must be an integer from 0 to "
            "1000000."
        )
    return value


def prepare_sketch_curve_point_target(
    document_uid: str,
    value: Mapping[str, Any],
    *,
    label: str,
) -> SketchCurvePointSpec:
    if not isinstance(label, str) or not label:
        raise TypeError("label must be a nonempty string")
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {label} definition has incorrect fields.")
    raw_target = value["target"]
    if not isinstance(raw_target, Mapping) or set(raw_target) != _TARGET_FIELDS:
        raise NativeSketchError(f"A {label} target has incorrect fields.")
    return SketchCurvePointSpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _external_count(value["expected_external_geometry_count"]),
        SketchCurvePointSelection(
            _bounded_index(raw_target["geometry_index"], label),
            _bounded_point(raw_target["reference_point_mm"], label),
        ),
    )
