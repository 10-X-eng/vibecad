# SPDX-License-Identifier: LGPL-2.1-or-later

"""Independent representation and shape proofs for B-spline degree elevation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from typing import Any

from VibeCADNativeSketchBSplineDegreeTarget import LABEL
from VibeCADNativeSketchErrors import NativeSketchError


MAX_CURVE_VALUES = 4_096
_SAMPLE_FRACTIONS = (0.0, 0.0625, 0.125, 0.25, 0.5, 0.75, 0.875, 0.9375, 1.0)


@dataclass(frozen=True, slots=True)
class FrozenCurveProof:
    digest: str
    samples: tuple[tuple[float, float, float], ...]
    control_positions: tuple[tuple[float, float, float], ...]
    knot_positions: tuple[tuple[float, float, float], ...]


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise NativeSketchError(f"{LABEL} found invalid B-spline values.") from exc
    if not math.isfinite(result):
        raise NativeSketchError(f"{LABEL} found non-finite B-spline values.")
    return 0.0 if abs(result) < 1.0e-14 else round(result, 12)


def _vector(value: Any) -> tuple[float, float, float]:
    return tuple(
        _number(getattr(value, lower, getattr(value, upper, None)))
        for lower, upper in (("x", "X"), ("y", "Y"), ("z", "Z"))
    )


def _values(geometry: Any, method: str) -> list[Any]:
    callback = getattr(geometry, method, None)
    if not callable(callback):
        raise NativeSketchError(f"{LABEL} cannot inspect B-spline {method} values.")
    try:
        values = list(callback() or [])
    except Exception as exc:
        raise NativeSketchError(
            f"{LABEL} cannot inspect B-spline {method} values."
        ) from exc
    if len(values) > MAX_CURVE_VALUES:
        raise NativeSketchError(f"{LABEL} B-spline state is too large to verify.")
    return values


def curve_proof(geometry: Any) -> FrozenCurveProof:
    poles = [_vector(value) for value in _values(geometry, "getPoles")]
    weights = [_number(value) for value in _values(geometry, "getWeights")]
    knots = [_number(value) for value in _values(geometry, "getKnots")]
    multiplicities = [int(value) for value in _values(geometry, "getMultiplicities")]
    degree = int(getattr(geometry, "Degree"))
    pole_count = int(getattr(geometry, "NbPoles"))
    knot_count = int(getattr(geometry, "NbKnots"))
    if (
        degree < 1
        or len(poles) != pole_count
        or len(weights) != pole_count
        or len(knots) != knot_count
        or len(multiplicities) != knot_count
    ):
        raise NativeSketchError(f"{LABEL} found incomplete B-spline structure.")
    first = _number(getattr(geometry, "FirstParameter"))
    last = _number(getattr(geometry, "LastParameter"))
    if not first < last:
        raise NativeSketchError(f"{LABEL} found an invalid B-spline parameter range.")
    value_at = getattr(geometry, "value", None)
    if not callable(value_at):
        raise NativeSketchError(f"{LABEL} cannot evaluate the B-spline shape.")
    try:
        samples = tuple(
            _vector(value_at(first + (last - first) * fraction))
            for fraction in _SAMPLE_FRACTIONS
        )
        knot_positions = tuple(_vector(value_at(knot)) for knot in knots)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} cannot evaluate the B-spline shape.") from exc
    payload = (
        degree,
        poles,
        weights,
        knots,
        multiplicities,
        bool(geometry.isRational()),
        bool(geometry.isPeriodic()),
        bool(geometry.isClosed()),
        first,
        last,
    )
    digest = hashlib.sha256(
        json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode()
    ).hexdigest()
    return FrozenCurveProof(digest, samples, tuple(poles), knot_positions)


def same_shape_samples(first: FrozenCurveProof, second: FrozenCurveProof) -> bool:
    return all(
        math.dist(actual, expected) <= 1.0e-8
        for actual, expected in zip(first.samples, second.samples, strict=True)
    )
