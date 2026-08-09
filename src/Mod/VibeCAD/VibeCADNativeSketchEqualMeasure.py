# SPDX-License-Identifier: LGPL-2.1-or-later

"""Geometric postconditions for ordered Native Sketch Equal chains."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from VibeCADNativeSketchConstraintTargets import (
    SketchConstraintElement,
    sketch_constraint_geometry,
)
from VibeCADNativeSketchEqualTarget import (
    BSplineWeightLink,
    LABEL,
    ResolvedSketchEqual,
)
from VibeCADNativeSketchErrors import NativeSketchError


_TOLERANCE = 1.0e-7


@dataclass(frozen=True, slots=True)
class EqualPairMeasurement:
    first_geometry_index: int
    second_geometry_index: int
    errors: tuple[tuple[str, float], ...]

    @property
    def maximum_error(self) -> float:
        return max(value for _name, value in self.errors)

    def record(self) -> dict[str, Any]:
        return {
            "first_geometry_index": self.first_geometry_index,
            "second_geometry_index": self.second_geometry_index,
            "errors": {name: value for name, value in self.errors},
        }


@dataclass(frozen=True, slots=True)
class EqualMeasurement:
    family: str
    unit: str
    pairs: tuple[EqualPairMeasurement, ...]

    @property
    def maximum_error(self) -> float:
        return max(pair.maximum_error for pair in self.pairs)

    def satisfied(self) -> bool:
        return self.maximum_error <= _TOLERANCE

    def record(self) -> dict[str, Any]:
        return {
            "maximum_error": self.maximum_error,
            "unit": self.unit,
            "pairs": [pair.record() for pair in self.pairs],
        }


def _finite_positive(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeSketchError(f"{LABEL} {label} is unavailable.") from exc
    if not math.isfinite(result) or result <= _TOLERANCE:
        raise NativeSketchError(f"{LABEL} {label} must be finite and positive.")
    return result


def _line_length(sketch: Any, element: SketchConstraintElement) -> float:
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError(f"{LABEL} point lookup is unavailable.")
    try:
        start = getter(element.geometry_index, 1)
        end = getter(element.geometry_index, 2)
        delta_x = float(end.x) - float(start.x)
        delta_y = float(end.y) - float(start.y)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} line endpoints are unavailable.") from exc
    if not math.isfinite(delta_x) or not math.isfinite(delta_y):
        raise NativeSketchError(f"{LABEL} line endpoints are not finite.")
    return _finite_positive(math.hypot(delta_x, delta_y), "line length")


def _attribute(geometry: Any, attribute: str, label: str) -> float:
    try:
        value = getattr(geometry, attribute)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} {label} is unavailable.") from exc
    return _finite_positive(value, label)


def _weight(
    sketch: Any,
    element: SketchConstraintElement,
    link: BSplineWeightLink | None,
) -> float:
    if link is None:
        raise NativeSketchError(f"{LABEL} B-spline weight link is unavailable.")
    handle = sketch_constraint_geometry(sketch, element.geometry_index)
    radius = _attribute(handle, "Radius", "control-point weight radius")
    spline = sketch_constraint_geometry(sketch, link.spline_index)
    try:
        weights = tuple(float(value) for value in spline.getWeights())
        weight = weights[link.pole_index]
    except Exception as exc:
        raise NativeSketchError(
            f"{LABEL} B-spline pole weight is unavailable."
        ) from exc
    weight = _finite_positive(weight, "B-spline pole weight")
    if not math.isclose(radius, weight, rel_tol=1.0e-9, abs_tol=_TOLERANCE):
        raise NativeSketchError(
            f"{LABEL} control-point handle and spline weight disagree."
        )
    return weight


def _quantities(
    sketch: Any,
    element: SketchConstraintElement,
    family: str,
    link: BSplineWeightLink | None,
) -> tuple[tuple[str, float], ...]:
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    if family == "line_length":
        return (("length", _line_length(sketch, element)),)
    if family == "circular_radius":
        return (("radius", _attribute(geometry, "Radius", "radius")),)
    if family in {"elliptic_radii", "hyperbolic_radii"}:
        return (
            ("major_radius", _attribute(geometry, "MajorRadius", "major radius")),
            ("minor_radius", _attribute(geometry, "MinorRadius", "minor radius")),
        )
    if family == "parabolic_focal_length":
        return (
            (
                "focal_length",
                _attribute(geometry, "Focal", "focal length"),
            ),
        )
    if family == "b_spline_weight":
        return (("weight", _weight(sketch, element, link)),)
    raise NativeSketchError(f"{LABEL} family is unsupported.")


def measure_sketch_equal(
    sketch: Any,
    resolved: ResolvedSketchEqual,
) -> EqualMeasurement:
    if not isinstance(resolved, ResolvedSketchEqual):
        raise TypeError("resolved must be a ResolvedSketchEqual")
    quantities = tuple(
        _quantities(sketch, element, resolved.family, link)
        for element, link in zip(
            resolved.references,
            resolved.weight_links,
            strict=True,
        )
    )
    pairs = []
    for plan, first_values, second_values in zip(
        resolved.plans,
        quantities[:-1],
        quantities[1:],
        strict=True,
    ):
        first_element, second_element = plan.references
        if tuple(name for name, _value in first_values) != tuple(
            name for name, _value in second_values
        ):
            raise NativeSketchError(f"{LABEL} measurement families changed.")
        pairs.append(
            EqualPairMeasurement(
                first_element.geometry_index,
                second_element.geometry_index,
                tuple(
                    (first_name, abs(second_value - first_value))
                    for (first_name, first_value), (_second_name, second_value) in zip(
                        first_values,
                        second_values,
                        strict=True,
                    )
                ),
            )
        )
    return EqualMeasurement(
        resolved.family,
        "unitless" if resolved.family == "b_spline_weight" else "mm",
        tuple(pairs),
    )
