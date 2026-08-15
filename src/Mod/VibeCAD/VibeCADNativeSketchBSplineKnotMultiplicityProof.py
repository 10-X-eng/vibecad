# SPDX-License-Identifier: LGPL-2.1-or-later

"""Independent representation and sampled-shape proof for knot multiplicity changes."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from VibeCADNativeSketchErrors import NativeSketchError


MAX_CURVE_VALUES = 4_096
SAMPLE_COUNT = 129
MAX_SHAPE_DEVIATION_MM = 1.0e-3


@dataclass(frozen=True, slots=True)
class KnotMultiplicityCurveProof:
    digest: str
    samples: tuple[tuple[float, float, float], ...]
    control_positions: tuple[tuple[float, float, float], ...]
    knot_positions: tuple[tuple[float, float, float], ...]
    degree: int
    knots: tuple[float, ...]
    multiplicities: tuple[int, ...]
    first_parameter: float
    last_parameter: float
    rational: bool
    periodic: bool
    closed: bool


def _number(value: Any, *, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeSketchError(f"{label} found invalid B-spline values.") from exc
    if not math.isfinite(result):
        raise NativeSketchError(f"{label} found non-finite B-spline values.")
    return 0.0 if abs(result) < 1.0e-14 else round(result, 12)


def _vector(value: Any, *, label: str) -> tuple[float, float, float]:
    return tuple(
        _number(getattr(value, lower, getattr(value, upper, None)), label=label)
        for lower, upper in (("x", "X"), ("y", "Y"), ("z", "Z"))
    )


def _values(geometry: Any, method: str, *, label: str) -> list[Any]:
    callback = getattr(geometry, method, None)
    if not callable(callback):
        raise NativeSketchError(f"{label} cannot inspect B-spline {method} values.")
    try:
        values = list(callback() or [])
    except Exception as exc:
        raise NativeSketchError(
            f"{label} cannot inspect B-spline {method} values."
        ) from exc
    if len(values) > MAX_CURVE_VALUES:
        raise NativeSketchError(f"{label} B-spline state is too large to verify.")
    return values


def knot_multiplicity_curve_proof(
    geometry: Any, *, label: str
) -> KnotMultiplicityCurveProof:
    poles = tuple(
        _vector(value, label=label)
        for value in _values(geometry, "getPoles", label=label)
    )
    weights = tuple(
        _number(value, label=label)
        for value in _values(geometry, "getWeights", label=label)
    )
    knots = tuple(
        _number(value, label=label)
        for value in _values(geometry, "getKnots", label=label)
    )
    multiplicities = tuple(
        int(value) for value in _values(geometry, "getMultiplicities", label=label)
    )
    degree = int(getattr(geometry, "Degree"))
    if (
        degree < 1
        or len(poles) != int(getattr(geometry, "NbPoles"))
        or len(weights) != len(poles)
        or len(knots) != int(getattr(geometry, "NbKnots"))
        or len(multiplicities) != len(knots)
        or any(value < 1 for value in multiplicities)
    ):
        raise NativeSketchError(f"{label} found incomplete B-spline structure.")
    first = _number(getattr(geometry, "FirstParameter"), label=label)
    last = _number(getattr(geometry, "LastParameter"), label=label)
    value_at = getattr(geometry, "value", None)
    if not first < last or not callable(value_at):
        raise NativeSketchError(f"{label} cannot evaluate the B-spline shape.")
    try:
        samples = tuple(
            _vector(
                value_at(first + (last - first) * index / (SAMPLE_COUNT - 1)),
                label=label,
            )
            for index in range(SAMPLE_COUNT)
        )
        knot_positions = tuple(_vector(value_at(knot), label=label) for knot in knots)
    except Exception as exc:
        raise NativeSketchError(f"{label} cannot evaluate the B-spline shape.") from exc
    rational = bool(geometry.isRational())
    periodic = bool(geometry.isPeriodic())
    closed = bool(geometry.isClosed())
    payload = (
        degree,
        poles,
        weights,
        knots,
        multiplicities,
        rational,
        periodic,
        closed,
        first,
        last,
    )
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    return KnotMultiplicityCurveProof(
        digest,
        samples,
        poles,
        knot_positions,
        degree,
        knots,
        multiplicities,
        first,
        last,
        rational,
        periodic,
        closed,
    )


def maximum_sampled_displacement_mm(
    first: KnotMultiplicityCurveProof,
    second: KnotMultiplicityCurveProof,
    *,
    label: str,
) -> float:
    if len(first.samples) != SAMPLE_COUNT or len(second.samples) != SAMPLE_COUNT:
        raise NativeSketchError(f"{label} has incomplete sampled shape proof.")
    return max(
        math.dist(left, right) for left, right in zip(first.samples, second.samples)
    )


def _point_segment_distance(point, start, end) -> float:
    direction = tuple(end[index] - start[index] for index in range(3))
    length_squared = sum(value * value for value in direction)
    if length_squared <= 1.0e-28:
        return math.dist(point, start)
    fraction = (
        sum((point[index] - start[index]) * direction[index] for index in range(3))
        / length_squared
    )
    fraction = min(1.0, max(0.0, fraction))
    closest = tuple(start[index] + fraction * direction[index] for index in range(3))
    return math.dist(point, closest)


def _directed_deviation(points, polyline) -> float:
    segments = tuple(zip(polyline, polyline[1:]))
    return max(
        min(_point_segment_distance(point, start, end) for start, end in segments)
        for point in points
    )


def maximum_sampled_deviation_mm(
    first: KnotMultiplicityCurveProof,
    second: KnotMultiplicityCurveProof,
    *,
    label: str,
) -> float:
    if len(first.samples) != SAMPLE_COUNT or len(second.samples) != SAMPLE_COUNT:
        raise NativeSketchError(f"{label} has incomplete sampled shape proof.")
    return max(
        _directed_deviation(first.samples, second.samples),
        _directed_deviation(second.samples, first.samples),
    )
