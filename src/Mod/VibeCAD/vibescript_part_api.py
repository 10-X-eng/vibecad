# SPDX-License-Identifier: LGPL-2.1-or-later

"""Production provider-facing API for the Part VibeScript domain.

All distances are millimetres and all angles are degrees.  Methods validate
their declarative contract before returning a :class:`DomainValue`, so source
errors identify the exact operation and parameter before OCC execution begins.
"""

from __future__ import annotations

from collections.abc import Mapping
import math
from typing import Any, Iterable, Sequence

from vibescript_domain_api import DomainValue


_TOPOLOGY_TYPES = frozenset({"edge", "wire", "face", "shell", "solid", "compound"})
_PUBLISHABLE_TYPES = frozenset({"wire", "face", "shell", "solid", "compound"})
_JOIN_TYPES = frozenset({"arc", "tangent", "intersection"})
_TRANSITION_TYPES = frozenset({"transformed", "right_corner", "round_corner"})
_SUBSHAPE_TYPES = frozenset({"edge", "wire", "face", "shell", "solid"})
_HELIX_REPRESENTATIONS = frozenset({"standard", "segmented"})
_PROJECTION_MODES = frozenset({"parallel", "perspective"})


def _error(operation: str, parameter: str, message: str, value: Any = None) -> ValueError:
    received = "" if value is None else f" Received {value!r}."
    return ValueError(f"api.{operation}: invalid {parameter}: {message}.{received}")


def _number(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: float | None = None,
    strict: bool = False,
) -> float:
    if isinstance(value, bool):
        raise _error(operation, parameter, "expected a finite number", value)
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise _error(operation, parameter, "expected a finite number", value) from exc
    if not math.isfinite(result):
        raise _error(operation, parameter, "expected a finite number", value)
    if minimum is not None and (result <= minimum if strict else result < minimum):
        relation = "greater than" if strict else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
    return result


def _nonzero_number(operation: str, parameter: str, value: Any) -> float:
    result = _number(operation, parameter, value)
    if abs(result) <= 1.0e-12:
        raise _error(operation, parameter, "magnitude must be greater than zero", value)
    return result


def _integer(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: int | None = None,
    maximum: int | None = None,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise _error(operation, parameter, "expected an integer", value)
    if minimum is not None and value < minimum:
        raise _error(operation, parameter, f"must be at least {minimum}", value)
    if maximum is not None and value > maximum:
        raise _error(operation, parameter, f"must not exceed {maximum}", value)
    return value


def _vector(
    operation: str,
    parameter: str,
    value: Any,
    *,
    nonzero: bool = False,
) -> list[float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "expected [x, y, z]", value)
    result = [_number(operation, f"{parameter}[{index}]", item) for index, item in enumerate(value)]
    if nonzero and math.sqrt(sum(item * item for item in result)) <= 1.0e-12:
        raise _error(operation, parameter, "vector magnitude must be non-zero", value)
    return result


def _oriented_x_direction(
    operation: str,
    direction: Sequence[float],
    value: Any,
) -> list[float] | None:
    """Validate an optional explicit local +X direction for an oriented primitive."""

    if value is None:
        return None
    x_direction = _vector(operation, "x_direction", value, nonzero=True)
    cross = [
        direction[1] * x_direction[2] - direction[2] * x_direction[1],
        direction[2] * x_direction[0] - direction[0] * x_direction[2],
        direction[0] * x_direction[1] - direction[1] * x_direction[0],
    ]
    if math.sqrt(sum(item * item for item in cross)) <= 1.0e-12:
        raise _error(
            operation,
            "x_direction",
            "must not be parallel to direction",
            value,
        )
    return x_direction


def _label(operation: str, value: Any) -> str:
    result = str(value or "").strip()
    if len(result) > 120:
        raise _error(operation, "label", "must contain at most 120 characters", value)
    return result


def _shape(
    operation: str,
    parameter: str,
    value: Any,
    *,
    allowed: Iterable[str] = _TOPOLOGY_TYPES,
) -> DomainValue:
    allowed_types = frozenset(allowed)
    if not isinstance(value, DomainValue) or value.domain != "part":
        raise _error(
            operation,
            parameter,
            "expected a value returned by this Part api",
            type(value).__name__,
        )
    if value.output_type not in allowed_types:
        raise _error(
            operation,
            parameter,
            f"expected topology type {sorted(allowed_types)}",
            value.output_type,
        )
    return value


def _shapes(
    operation: str,
    parameter: str,
    values: Any,
    *,
    minimum: int = 1,
    allowed: Iterable[str] = _TOPOLOGY_TYPES,
) -> list[DomainValue]:
    if isinstance(values, DomainValue):
        sequence = [values]
    elif isinstance(values, (list, tuple)):
        sequence = list(values)
    else:
        raise _error(
            operation, parameter, "expected a Part value or an array of Part values", values
        )
    if len(sequence) < minimum:
        raise _error(operation, parameter, f"requires at least {minimum} value(s)", values)
    return [
        _shape(operation, f"{parameter}[{index}]", value, allowed=allowed)
        for index, value in enumerate(sequence)
    ]


def _result_type(operation: str, value: Any) -> str:
    result = str(value or "").strip().lower()
    if result not in _PUBLISHABLE_TYPES:
        raise _error(
            operation, "output_type", f"must be one of {sorted(_PUBLISHABLE_TYPES)}", value
        )
    return result


def _topology_type(operation: str, value: Any) -> str:
    result = str(value or "").strip().lower()
    if result not in _TOPOLOGY_TYPES:
        raise _error(
            operation,
            "output_type",
            f"must be one of {sorted(_TOPOLOGY_TYPES)}",
            value,
        )
    return result


def _document_reference(operation: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {"document_uid", "object_name"}:
        raise _error(
            operation,
            "reference",
            "expected a validated input reference with document_uid and object_name",
            value,
        )
    result = {
        "document_uid": str(value.get("document_uid") or "").strip(),
        "object_name": str(value.get("object_name") or "").strip(),
    }
    if not all(result.values()):
        raise _error(operation, "reference", "document_uid and object_name must be non-empty")
    return result


def _inferred_result_type(
    operation: str,
    declared: Any,
    inferred: str,
    *,
    exact: bool = False,
) -> str:
    if declared is None:
        return inferred
    clean = _result_type(operation, declared)
    if exact and clean != inferred:
        raise _error(
            operation,
            "output_type",
            f"must be {inferred!r} for the supplied topology and options",
            declared,
        )
    return clean


def _indices(operation: str, parameter: str, value: Any) -> str | list[int]:
    if value == "all":
        return "all"
    if not isinstance(value, (list, tuple)) or not value:
        raise _error(
            operation, parameter, "expected 'all' or a non-empty array of 1-based indices", value
        )
    result: list[int] = []
    for index, item in enumerate(value):
        if isinstance(item, bool) or not isinstance(item, int) or item < 1:
            raise _error(
                operation, f"{parameter}[{index}]", "expected a positive 1-based integer", item
            )
        if item in result:
            raise _error(operation, parameter, "contains duplicate indices", value)
        result.append(item)
    return result


def _weights(
    operation: str,
    value: Any,
    *,
    pole_count: int,
    required: bool = False,
) -> list[float]:
    if value in (None, ()) and not required:
        return []
    if not isinstance(value, (list, tuple)) or len(value) != pole_count:
        raise _error(
            operation,
            "weights",
            f"expected exactly {pole_count} positive weights",
            value,
        )
    return [
        _number(
            operation,
            f"weights[{index}]",
            weight,
            minimum=0.0,
            strict=True,
        )
        for index, weight in enumerate(value)
    ]


class PartDomainAPI:
    """Explicit, immutable construction API injected into Part VibeScript source."""

    __slots__ = ()

    domain = "part"

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared = tuple(dict.fromkeys(str(item) for item in exports))
        missing = [name for name in declared if not callable(getattr(self, name, None))]
        if missing:
            raise RuntimeError(f"Part runtime is missing declared exports: {', '.join(missing)}.")
        undeclared = [name for name in self.exported_names if name not in declared]
        if undeclared:
            raise RuntimeError(
                f"Part pack does not declare runtime exports: {', '.join(undeclared)}."
            )
        if frozenset(str(item) for item in output_types) != _PUBLISHABLE_TYPES:
            raise RuntimeError(
                "Part pack output types do not match the production runtime contract."
            )

    @staticmethod
    def _value(
        operation: str,
        output_type: str,
        *arguments: Any,
        label: str = "",
        **properties: Any,
    ) -> DomainValue:
        clean_label = _label(operation, label)
        if clean_label:
            properties["label"] = clean_label
        return DomainValue(
            domain="part",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def from_object(
        self,
        reference: Mapping[str, str],
        *,
        output_type: str,
        label: str = "",
    ) -> DomainValue:
        """Snapshot a referenced live object's exact Shape for isolated Part operations."""

        operation = "from_object"
        return self._value(
            operation,
            _topology_type(operation, output_type),
            _document_reference(operation, reference),
            label=label,
        )

    def box(
        self,
        length: float,
        width: float,
        height: float,
        *,
        origin: Sequence[float] = (0.0, 0.0, 0.0),
        direction: Sequence[float] = (0.0, 0.0, 1.0),
        x_direction: Sequence[float] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Create a box in local +X/+Y/+Z; direction sets +Z and x_direction sets +X."""

        operation = "box"
        clean_direction = _vector(
            operation,
            "direction",
            direction,
            nonzero=True,
        )
        clean_x_direction = _oriented_x_direction(
            operation,
            clean_direction,
            x_direction,
        )
        return self._value(
            operation,
            "solid",
            _number(operation, "length", length, minimum=0.0, strict=True),
            _number(operation, "width", width, minimum=0.0, strict=True),
            _number(operation, "height", height, minimum=0.0, strict=True),
            origin=_vector(operation, "origin", origin),
            direction=clean_direction,
            **(
                {}
                if clean_x_direction is None
                else {"x_direction": clean_x_direction}
            ),
            label=label,
        )

    def wedge(
        self,
        length: float,
        width: float,
        height: float,
        *,
        ridge_x: float = 0.0,
        origin: Sequence[float] = (0.0, 0.0, 0.0),
        direction: Sequence[float] = (0.0, 0.0, 1.0),
        x_direction: Sequence[float] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Create a wedge in local +X/+Y/+Z; direction sets +Z and x_direction sets +X."""

        operation = "wedge"
        clean_length = _number(operation, "length", length, minimum=0.0, strict=True)
        clean_ridge = _number(operation, "ridge_x", ridge_x, minimum=0.0)
        if clean_ridge > clean_length:
            raise _error(operation, "ridge_x", "must not exceed length", ridge_x)
        clean_direction = _vector(
            operation,
            "direction",
            direction,
            nonzero=True,
        )
        clean_x_direction = _oriented_x_direction(
            operation,
            clean_direction,
            x_direction,
        )
        return self._value(
            operation,
            "solid",
            clean_length,
            _number(operation, "width", width, minimum=0.0, strict=True),
            _number(operation, "height", height, minimum=0.0, strict=True),
            ridge_x=clean_ridge,
            origin=_vector(operation, "origin", origin),
            direction=clean_direction,
            **(
                {}
                if clean_x_direction is None
                else {"x_direction": clean_x_direction}
            ),
            label=label,
        )

    def plane(
        self,
        length: float,
        width: float,
        *,
        origin: Sequence[float] = (0.0, 0.0, 0.0),
        normal: Sequence[float] = (0.0, 0.0, 1.0),
        x_direction: Sequence[float] = (1.0, 0.0, 0.0),
        label: str = "",
    ) -> DomainValue:
        """Create an oriented rectangular planar face."""

        operation = "plane"
        clean_normal = _vector(operation, "normal", normal, nonzero=True)
        clean_x = _vector(operation, "x_direction", x_direction, nonzero=True)
        cross = [
            clean_normal[1] * clean_x[2] - clean_normal[2] * clean_x[1],
            clean_normal[2] * clean_x[0] - clean_normal[0] * clean_x[2],
            clean_normal[0] * clean_x[1] - clean_normal[1] * clean_x[0],
        ]
        if math.sqrt(sum(value * value for value in cross)) <= 1.0e-12:
            raise _error(operation, "x_direction", "must not be parallel to normal", x_direction)
        return self._value(
            operation,
            "face",
            _number(operation, "length", length, minimum=0.0, strict=True),
            _number(operation, "width", width, minimum=0.0, strict=True),
            origin=_vector(operation, "origin", origin),
            normal=clean_normal,
            x_direction=clean_x,
            label=label,
        )

    def prism(
        self,
        sides: int,
        circumradius: float,
        height: float,
        *,
        center: Sequence[float] = (0.0, 0.0, 0.0),
        direction: Sequence[float] = (0.0, 0.0, 1.0),
        rotation_degrees: float = 0.0,
        label: str = "",
    ) -> DomainValue:
        """Create a regular 3-64 sided prism from its base circumradius."""

        operation = "prism"
        return self._value(
            operation,
            "solid",
            _integer(operation, "sides", sides, minimum=3, maximum=64),
            _number(operation, "circumradius", circumradius, minimum=0.0, strict=True),
            _number(operation, "height", height, minimum=0.0, strict=True),
            center=_vector(operation, "center", center),
            direction=_vector(operation, "direction", direction, nonzero=True),
            rotation_degrees=_number(operation, "rotation_degrees", rotation_degrees),
            label=label,
        )

    def cylinder(
        self,
        radius: float,
        height: float,
        *,
        origin: Sequence[float] = (0.0, 0.0, 0.0),
        direction: Sequence[float] = (0.0, 0.0, 1.0),
        angle: float = 360.0,
        label: str = "",
    ) -> DomainValue:
        """Create a full or partial cylinder along an explicit axis."""

        operation = "cylinder"
        clean_angle = _number(operation, "angle", angle, minimum=0.0, strict=True)
        if clean_angle > 360.0:
            raise _error(operation, "angle", "must not exceed 360 degrees", angle)
        return self._value(
            operation,
            "solid",
            _number(operation, "radius", radius, minimum=0.0, strict=True),
            _number(operation, "height", height, minimum=0.0, strict=True),
            origin=_vector(operation, "origin", origin),
            direction=_vector(operation, "direction", direction, nonzero=True),
            angle=clean_angle,
            label=label,
        )

    def cone(
        self,
        radius1: float,
        radius2: float,
        height: float,
        *,
        origin: Sequence[float] = (0.0, 0.0, 0.0),
        direction: Sequence[float] = (0.0, 0.0, 1.0),
        angle: float = 360.0,
        label: str = "",
    ) -> DomainValue:
        """Create a cone or frustum; at least one end radius must be positive."""

        operation = "cone"
        first = _number(operation, "radius1", radius1, minimum=0.0)
        second = _number(operation, "radius2", radius2, minimum=0.0)
        if first == 0.0 and second == 0.0:
            raise _error(operation, "radius1/radius2", "at least one radius must be positive")
        clean_angle = _number(operation, "angle", angle, minimum=0.0, strict=True)
        if clean_angle > 360.0:
            raise _error(operation, "angle", "must not exceed 360 degrees", angle)
        return self._value(
            operation,
            "solid",
            first,
            second,
            _number(operation, "height", height, minimum=0.0, strict=True),
            origin=_vector(operation, "origin", origin),
            direction=_vector(operation, "direction", direction, nonzero=True),
            angle=clean_angle,
            label=label,
        )

    def sphere(
        self,
        radius: float,
        *,
        center: Sequence[float] = (0.0, 0.0, 0.0),
        direction: Sequence[float] = (0.0, 0.0, 1.0),
        latitude1: float = -90.0,
        latitude2: float = 90.0,
        longitude: float = 360.0,
        label: str = "",
    ) -> DomainValue:
        """Create a sphere or angular spherical segment with explicit limits."""

        operation = "sphere"
        low = _number(operation, "latitude1", latitude1)
        high = _number(operation, "latitude2", latitude2)
        sweep = _number(operation, "longitude", longitude, minimum=0.0, strict=True)
        if not -90.0 <= low < high <= 90.0:
            raise _error(
                operation, "latitude1/latitude2", "must satisfy -90 <= latitude1 < latitude2 <= 90"
            )
        if sweep > 360.0:
            raise _error(operation, "longitude", "must not exceed 360 degrees", longitude)
        return self._value(
            operation,
            "solid",
            _number(operation, "radius", radius, minimum=0.0, strict=True),
            center=_vector(operation, "center", center),
            direction=_vector(operation, "direction", direction, nonzero=True),
            latitude1=low,
            latitude2=high,
            longitude=sweep,
            label=label,
        )

    def torus(
        self,
        major_radius: float,
        minor_radius: float,
        *,
        center: Sequence[float] = (0.0, 0.0, 0.0),
        direction: Sequence[float] = (0.0, 0.0, 1.0),
        angle1: float = -180.0,
        angle2: float = 180.0,
        sweep: float = 360.0,
        label: str = "",
    ) -> DomainValue:
        """Create a torus or toroidal segment; major radius must exceed minor radius."""

        operation = "torus"
        major = _number(operation, "major_radius", major_radius, minimum=0.0, strict=True)
        minor = _number(operation, "minor_radius", minor_radius, minimum=0.0, strict=True)
        if major <= minor:
            raise _error(
                operation, "major_radius", "must be greater than minor_radius", major_radius
            )
        first = _number(operation, "angle1", angle1)
        second = _number(operation, "angle2", angle2)
        clean_sweep = _number(operation, "sweep", sweep, minimum=0.0, strict=True)
        if first >= second:
            raise _error(operation, "angle1/angle2", "angle1 must be less than angle2")
        if clean_sweep > 360.0:
            raise _error(operation, "sweep", "must not exceed 360 degrees", sweep)
        return self._value(
            operation,
            "solid",
            major,
            minor,
            center=_vector(operation, "center", center),
            direction=_vector(operation, "direction", direction, nonzero=True),
            angle1=first,
            angle2=second,
            sweep=clean_sweep,
            label=label,
        )

    def line(self, start: Sequence[float], end: Sequence[float]) -> DomainValue:
        """Create one straight edge between two distinct 3D points."""

        operation = "line"
        first = _vector(operation, "start", start)
        second = _vector(operation, "end", end)
        if math.dist(first, second) <= 1.0e-9:
            raise _error(operation, "end", "must differ from start", end)
        return self._value(operation, "edge", first, second)

    def arc(
        self,
        start: Sequence[float],
        point: Sequence[float],
        end: Sequence[float],
    ) -> DomainValue:
        """Create a circular arc through three non-coincident points."""

        operation = "arc"
        first = _vector(operation, "start", start)
        middle = _vector(operation, "point", point)
        last = _vector(operation, "end", end)
        first_leg = [middle[index] - first[index] for index in range(3)]
        second_leg = [last[index] - first[index] for index in range(3)]
        cross = [
            first_leg[1] * second_leg[2] - first_leg[2] * second_leg[1],
            first_leg[2] * second_leg[0] - first_leg[0] * second_leg[2],
            first_leg[0] * second_leg[1] - first_leg[1] * second_leg[0],
        ]
        if math.sqrt(sum(value * value for value in cross)) <= 1.0e-9:
            raise _error(
                operation,
                "start/point/end",
                "three distinct, non-collinear points are required",
            )
        return self._value(operation, "edge", first, middle, last)

    def circle(
        self,
        radius: float,
        *,
        center: Sequence[float] = (0.0, 0.0, 0.0),
        normal: Sequence[float] = (0.0, 0.0, 1.0),
        start_angle: float = 0.0,
        end_angle: float = 360.0,
    ) -> DomainValue:
        """Create a circular edge or arc in a plane defined by its normal."""

        operation = "circle"
        start = _number(operation, "start_angle", start_angle)
        end = _number(operation, "end_angle", end_angle)
        if end <= start or end - start > 360.0:
            raise _error(
                operation,
                "start_angle/end_angle",
                "must define a positive sweep no greater than 360 degrees",
            )
        return self._value(
            operation,
            "edge",
            _number(operation, "radius", radius, minimum=0.0, strict=True),
            center=_vector(operation, "center", center),
            normal=_vector(operation, "normal", normal, nonzero=True),
            start_angle=start,
            end_angle=end,
        )

    def ellipse(
        self,
        major_radius: float,
        minor_radius: float,
        *,
        center: Sequence[float] = (0.0, 0.0, 0.0),
        normal: Sequence[float] = (0.0, 0.0, 1.0),
    ) -> DomainValue:
        """Create a closed elliptical edge with major_radius >= minor_radius."""

        operation = "ellipse"
        major = _number(operation, "major_radius", major_radius, minimum=0.0, strict=True)
        minor = _number(operation, "minor_radius", minor_radius, minimum=0.0, strict=True)
        if major < minor:
            raise _error(operation, "major_radius", "must be at least minor_radius", major_radius)
        return self._value(
            operation,
            "edge",
            major,
            minor,
            center=_vector(operation, "center", center),
            normal=_vector(operation, "normal", normal, nonzero=True),
        )

    def bezier(
        self,
        poles: Sequence[Sequence[float]],
        *,
        weights: Sequence[float] = (),
    ) -> DomainValue:
        """Create a polynomial or exactly weighted rational Bezier edge."""

        operation = "bezier"
        if not isinstance(poles, (list, tuple)) or not 2 <= len(poles) <= 25:
            raise _error(operation, "poles", "expected 2-25 control points", poles)
        clean_poles = [
            _vector(operation, f"poles[{index}]", point)
            for index, point in enumerate(poles)
        ]
        return self._value(
            operation,
            "edge",
            clean_poles,
            weights=_weights(operation, weights, pole_count=len(clean_poles)),
        )

    def bspline(
        self,
        points: Sequence[Sequence[float]],
        *,
        periodic: bool = False,
        tolerance: float = 1.0e-7,
    ) -> DomainValue:
        """Interpolate a B-spline edge through at least three points."""

        operation = "bspline"
        if not isinstance(points, (list, tuple)) or len(points) < 3:
            raise _error(
                operation, "points", "expected at least three interpolation points", points
            )
        return self._value(
            operation,
            "edge",
            [_vector(operation, f"points[{index}]", point) for index, point in enumerate(points)],
            periodic=bool(periodic),
            tolerance=_number(operation, "tolerance", tolerance, minimum=0.0, strict=True),
        )

    def nurbs_curve(
        self,
        poles: Sequence[Sequence[float]],
        degree: int,
        knots: Sequence[float],
        multiplicities: Sequence[int],
        *,
        weights: Sequence[float] = (),
        periodic: bool = False,
    ) -> DomainValue:
        """Create an exact polynomial or rational B-spline from its NURBS data."""

        operation = "nurbs_curve"
        if not isinstance(poles, (list, tuple)) or not 2 <= len(poles) <= 512:
            raise _error(operation, "poles", "expected 2-512 control poles", poles)
        clean_degree = _integer(operation, "degree", degree, minimum=1, maximum=25)
        if clean_degree >= len(poles):
            raise _error(operation, "degree", "must be smaller than the pole count", degree)
        if not isinstance(knots, (list, tuple)) or not 2 <= len(knots) <= 1024:
            raise _error(operation, "knots", "expected 2-1024 knot values", knots)
        clean_knots = [
            _number(operation, f"knots[{index}]", value)
            for index, value in enumerate(knots)
        ]
        if any(right <= left for left, right in zip(clean_knots, clean_knots[1:])):
            raise _error(operation, "knots", "values must be strictly increasing", knots)
        if not isinstance(multiplicities, (list, tuple)) or len(multiplicities) != len(
            clean_knots
        ):
            raise _error(
                operation,
                "multiplicities",
                f"expected exactly {len(clean_knots)} integer values",
                multiplicities,
            )
        clean_multiplicities = [
            _integer(
                operation,
                f"multiplicities[{index}]",
                value,
                minimum=1,
                maximum=clean_degree + 1,
            )
            for index, value in enumerate(multiplicities)
        ]
        if any(value > clean_degree for value in clean_multiplicities[1:-1]):
            raise _error(
                operation,
                "multiplicities",
                "interior knot multiplicities must not exceed degree",
                multiplicities,
            )
        if periodic:
            if clean_multiplicities[0] != clean_multiplicities[-1]:
                raise _error(
                    operation,
                    "multiplicities",
                    "a periodic curve requires equal first and last multiplicities",
                    multiplicities,
                )
            relation = sum(clean_multiplicities[:-1])
            expected_relation = len(poles)
        else:
            relation = sum(clean_multiplicities)
            expected_relation = len(poles) + clean_degree + 1
        if relation != expected_relation:
            raise _error(
                operation,
                "multiplicities",
                f"sum relation is {relation}, but this curve requires {expected_relation}",
                multiplicities,
            )
        clean_poles = [
            _vector(operation, f"poles[{index}]", point)
            for index, point in enumerate(poles)
        ]
        return self._value(
            operation,
            "edge",
            clean_poles,
            clean_degree,
            clean_knots,
            clean_multiplicities,
            weights=_weights(operation, weights, pole_count=len(clean_poles)),
            periodic=bool(periodic),
        )

    def helix(
        self,
        pitch: float,
        height: float,
        radius: float,
        *,
        angle: float = 0.0,
        left_handed: bool = False,
        vertical_height: bool = False,
        representation: str = "standard",
    ) -> DomainValue:
        """Create a standard or long-path segmented cylindrical/conical helix."""

        operation = "helix"
        clean_representation = str(representation or "").strip().lower()
        if clean_representation not in _HELIX_REPRESENTATIONS:
            raise _error(
                operation,
                "representation",
                f"must be one of {sorted(_HELIX_REPRESENTATIONS)}",
                representation,
            )
        if clean_representation == "segmented" and vertical_height:
            raise _error(
                operation,
                "vertical_height",
                "is available only for the standard representation",
                vertical_height,
            )
        return self._value(
            operation,
            "wire",
            _number(operation, "pitch", pitch, minimum=0.0, strict=True),
            _number(operation, "height", height, minimum=0.0, strict=True),
            _number(operation, "radius", radius, minimum=0.0, strict=True),
            angle=_number(operation, "angle", angle),
            left_handed=bool(left_handed),
            vertical_height=bool(vertical_height),
            representation=clean_representation,
        )

    def wire(
        self,
        items: Sequence[Any],
        *,
        closed: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Create a wire from ordered Part edges or from ordered 3D points."""

        operation = "wire"
        if not isinstance(items, (list, tuple)) or len(items) < 1:
            raise _error(
                operation, "items", "expected one or more edges or at least two points", items
            )
        if all(isinstance(item, DomainValue) for item in items):
            values: list[Any] = _shapes(operation, "items", items, allowed={"edge", "wire"})
        else:
            if len(items) < 2:
                raise _error(operation, "items", "a point wire requires at least two points", items)
            values = [
                _vector(operation, f"items[{index}]", point) for index, point in enumerate(items)
            ]
        return self._value(operation, "wire", values, closed=bool(closed), label=label)

    def face(
        self,
        outer: DomainValue,
        *,
        holes: Sequence[DomainValue] = (),
        label: str = "",
    ) -> DomainValue:
        """Create a planar face from one closed outer wire and optional closed hole wires."""

        operation = "face"
        clean_holes = _shapes(operation, "holes", holes, minimum=0, allowed={"wire"})
        return self._value(
            operation,
            "face",
            _shape(operation, "outer", outer, allowed={"wire"}),
            holes=clean_holes,
            label=label,
        )

    def shell(self, faces: Sequence[DomainValue], *, label: str = "") -> DomainValue:
        """Sew an ordered set of faces into a shell."""

        operation = "shell"
        return self._value(
            operation,
            "shell",
            _shapes(operation, "faces", faces, allowed={"face"}),
            label=label,
        )

    def solid(self, shell: DomainValue, *, label: str = "") -> DomainValue:
        """Create a solid from one closed shell."""

        operation = "solid"
        return self._value(
            operation,
            "solid",
            _shape(operation, "shell", shell, allowed={"shell"}),
            label=label,
        )

    def compound(self, shapes: Sequence[DomainValue], *, label: str = "") -> DomainValue:
        """Publish heterogeneous Part topology as one compound without fusing it."""

        operation = "compound"
        return self._value(
            operation,
            "compound",
            _shapes(operation, "shapes", shapes),
            label=label,
        )

    def subshape(
        self,
        shape: DomainValue,
        kind: str,
        index: int,
        *,
        label: str = "",
    ) -> DomainValue:
        """Extract one 1-based edge, wire, face, shell, or solid from a Part value."""

        operation = "subshape"
        clean_kind = str(kind or "").strip().lower()
        if clean_kind not in _SUBSHAPE_TYPES:
            raise _error(operation, "kind", f"must be one of {sorted(_SUBSHAPE_TYPES)}", kind)
        return self._value(
            operation,
            clean_kind,
            _shape(operation, "shape", shape),
            clean_kind,
            _integer(operation, "index", index, minimum=1),
            label=label,
        )

    def extrude(
        self,
        shape: DomainValue,
        vector: Sequence[float],
        *,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Extrude an edge to a face, a wire to a shell, or a face to a solid."""

        operation = "extrude"
        clean_shape = _shape(operation, "shape", shape, allowed={"edge", "wire", "face"})
        inferred = {"edge": "face", "wire": "shell", "face": "solid"}[clean_shape.output_type]
        return self._value(
            operation,
            _inferred_result_type(operation, output_type, inferred, exact=True),
            clean_shape,
            _vector(operation, "vector", vector, nonzero=True),
            label=label,
        )

    def revolve(
        self,
        shape: DomainValue,
        axis_origin: Sequence[float],
        axis_direction: Sequence[float],
        *,
        angle: float = 360.0,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Revolve an edge to a face, a wire to a shell, or a face to a solid."""

        operation = "revolve"
        clean_angle = _number(operation, "angle", angle, minimum=0.0, strict=True)
        if clean_angle > 360.0:
            raise _error(operation, "angle", "must not exceed 360 degrees", angle)
        clean_shape = _shape(operation, "shape", shape, allowed={"edge", "wire", "face"})
        inferred = {"edge": "face", "wire": "shell", "face": "solid"}[clean_shape.output_type]
        return self._value(
            operation,
            _inferred_result_type(operation, output_type, inferred, exact=True),
            clean_shape,
            _vector(operation, "axis_origin", axis_origin),
            _vector(operation, "axis_direction", axis_direction, nonzero=True),
            angle=clean_angle,
            label=label,
        )

    def loft(
        self,
        sections: Sequence[DomainValue],
        *,
        solid: bool = False,
        ruled: bool = False,
        closed: bool = False,
        max_degree: int = 5,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Loft through at least two wire sections; optionally create a solid."""

        operation = "loft"
        inferred = "solid" if bool(solid) else "shell"
        clean_type = _inferred_result_type(operation, output_type, inferred, exact=True)
        return self._value(
            operation,
            clean_type,
            _shapes(operation, "sections", sections, minimum=2, allowed={"wire"}),
            solid=bool(solid),
            ruled=bool(ruled),
            closed=bool(closed),
            max_degree=_integer(operation, "max_degree", max_degree, minimum=1, maximum=25),
            label=label,
        )

    def sweep(
        self,
        profile: DomainValue | Sequence[DomainValue],
        path: DomainValue,
        *,
        solid: bool = False,
        frenet: bool = False,
        transition: str = "transformed",
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Sweep one or more ordered wire profiles along one wire path."""

        operation = "sweep"
        if isinstance(profile, DomainValue):
            clean_profile: DomainValue | list[DomainValue] = _shape(
                operation, "profile", profile, allowed={"wire"}
            )
        else:
            clean_profile = _shapes(
                operation,
                "profile",
                profile,
                allowed={"wire"},
            )
            if len(clean_profile) > 64:
                raise _error(
                    operation,
                    "profile",
                    "must contain at most 64 ordered wire profiles",
                    len(clean_profile),
                )
        clean_transition = str(transition or "").strip().lower()
        if clean_transition not in _TRANSITION_TYPES:
            raise _error(
                operation, "transition", f"must be one of {sorted(_TRANSITION_TYPES)}", transition
            )
        inferred = "solid" if bool(solid) else "shell"
        clean_type = _inferred_result_type(operation, output_type, inferred, exact=True)
        return self._value(
            operation,
            clean_type,
            clean_profile,
            _shape(operation, "path", path, allowed={"wire"}),
            solid=bool(solid),
            frenet=bool(frenet),
            transition=clean_transition,
            label=label,
        )

    def ruled_surface(
        self,
        first: DomainValue,
        second: DomainValue,
        *,
        label: str = "",
    ) -> DomainValue:
        """Create a ruled face between edges or a ruled shell between wires."""

        operation = "ruled_surface"
        clean_first = _shape(operation, "first", first, allowed={"edge", "wire"})
        clean_second = _shape(operation, "second", second, allowed={"edge", "wire"})
        if clean_first.output_type != clean_second.output_type:
            raise _error(
                operation,
                "first/second",
                "both sections must have the same topology type",
            )
        output_type = "face" if clean_first.output_type == "edge" else "shell"
        return self._value(operation, output_type, clean_first, clean_second, label=label)

    def filled_surface(
        self,
        boundaries: Sequence[DomainValue],
        *,
        label: str = "",
    ) -> DomainValue:
        """Fill ordered edges/wires or the boundary of a face with an OCC surface."""

        operation = "filled_surface"
        return self._value(
            operation,
            "face",
            _shapes(
                operation,
                "boundaries",
                boundaries,
                minimum=1,
                allowed={"edge", "wire", "face"},
            ),
            label=label,
        )

    def fuse(
        self,
        shapes: Sequence[DomainValue],
        *,
        tolerance: float = 0.0,
        refine: bool = True,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Boolean-union shapes in one OCC operation with optional fuzzy tolerance."""

        operation = "fuse"
        clean_shapes = _shapes(operation, "shapes", shapes, minimum=2)
        inferred = clean_shapes[0].output_type
        if inferred == "edge" or any(shape.output_type != inferred for shape in clean_shapes[1:]):
            if output_type is None:
                raise _error(
                    operation,
                    "output_type",
                    "is required when input topology types differ or are non-publishable edges",
                )
            inferred = "compound"
        return self._value(
            operation,
            _inferred_result_type(operation, output_type, inferred),
            clean_shapes,
            tolerance=_number(operation, "tolerance", tolerance, minimum=0.0),
            refine=bool(refine),
            label=label,
        )

    def cut(
        self,
        base: DomainValue,
        tools: Sequence[DomainValue] | DomainValue,
        *,
        tolerance: float = 0.0,
        refine: bool = True,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Subtract tools in one OCC operation with optional fuzzy tolerance."""

        operation = "cut"
        clean_base = _shape(operation, "base", base)
        if clean_base.output_type == "edge" and output_type is None:
            raise _error(operation, "output_type", "is required when the base is an edge")
        inferred = clean_base.output_type if clean_base.output_type != "edge" else "compound"
        return self._value(
            operation,
            _inferred_result_type(operation, output_type, inferred),
            clean_base,
            _shapes(operation, "tools", tools),
            tolerance=_number(operation, "tolerance", tolerance, minimum=0.0),
            refine=bool(refine),
            label=label,
        )

    def common(
        self,
        shapes: Sequence[DomainValue],
        *,
        tolerance: float = 0.0,
        refine: bool = True,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Boolean-intersect shapes in one OCC operation with optional fuzzy tolerance."""

        operation = "common"
        clean_shapes = _shapes(operation, "shapes", shapes, minimum=2)
        inferred = clean_shapes[0].output_type
        if inferred == "edge" or any(shape.output_type != inferred for shape in clean_shapes[1:]):
            if output_type is None:
                raise _error(
                    operation,
                    "output_type",
                    "is required when input topology types differ or are non-publishable edges",
                )
            inferred = "compound"
        return self._value(
            operation,
            _inferred_result_type(operation, output_type, inferred),
            clean_shapes,
            tolerance=_number(operation, "tolerance", tolerance, minimum=0.0),
            refine=bool(refine),
            label=label,
        )

    def section(
        self,
        left: DomainValue,
        right: DomainValue,
        *,
        tolerance: float = 0.0,
        label: str = "",
    ) -> DomainValue:
        """Compute intersection edges between two shapes as a compound."""

        operation = "section"
        return self._value(
            operation,
            "compound",
            _shape(operation, "left", left),
            _shape(operation, "right", right),
            tolerance=_number(operation, "tolerance", tolerance, minimum=0.0),
            label=label,
        )

    def general_fuse(
        self,
        shapes: Sequence[DomainValue],
        *,
        tolerance: float = 0.0,
        label: str = "",
    ) -> DomainValue:
        """Fragment all inputs at intersections and return every touching piece."""

        operation = "general_fuse"
        return self._value(
            operation,
            "compound",
            _shapes(operation, "shapes", shapes, minimum=2),
            tolerance=_number(operation, "tolerance", tolerance, minimum=0.0),
            label=label,
        )

    def slice(
        self,
        shape: DomainValue,
        normal: Sequence[float],
        offsets: Sequence[float],
        *,
        label: str = "",
    ) -> DomainValue:
        """Intersect a shape with planes normal to a direction at signed offsets."""

        operation = "slice"
        if not isinstance(offsets, (list, tuple)) or not offsets:
            raise _error(operation, "offsets", "expected one or more signed distances", offsets)
        clean_offsets = [
            _number(operation, f"offsets[{index}]", value) for index, value in enumerate(offsets)
        ]
        if len(set(clean_offsets)) != len(clean_offsets):
            raise _error(operation, "offsets", "must not contain duplicates", offsets)
        return self._value(
            operation,
            "compound",
            _shape(operation, "shape", shape),
            _vector(operation, "normal", normal, nonzero=True),
            clean_offsets,
            label=label,
        )

    def defeature(
        self,
        shape: DomainValue,
        faces: Sequence[int],
        *,
        label: str = "",
    ) -> DomainValue:
        """Heal a solid after removing selected 1-based feature faces."""

        operation = "defeature"
        clean_shape = _shape(operation, "shape", shape, allowed={"solid"})
        return self._value(
            operation,
            "solid",
            clean_shape,
            _indices(operation, "faces", faces),
            label=label,
        )

    def to_nurbs(self, shape: DomainValue, *, label: str = "") -> DomainValue:
        """Convert analytic curves and surfaces to an exact NURBS representation."""

        operation = "to_nurbs"
        clean_shape = _shape(operation, "shape", shape)
        return self._value(operation, clean_shape.output_type, clean_shape, label=label)

    def reverse(self, shape: DomainValue, *, label: str = "") -> DomainValue:
        """Return a copy with its OCC topology orientation reversed."""

        operation = "reverse"
        clean_shape = _shape(operation, "shape", shape)
        return self._value(operation, clean_shape.output_type, clean_shape, label=label)

    def fillet(
        self,
        shape: DomainValue,
        radius: float,
        *,
        edges: str | Sequence[int] = "all",
        label: str = "",
    ) -> DomainValue:
        """Round selected 1-based edges of a solid; use edges='all' deliberately."""

        operation = "fillet"
        clean_shape = _shape(operation, "shape", shape, allowed={"solid", "shell"})
        return self._value(
            operation,
            clean_shape.output_type,
            clean_shape,
            _number(operation, "radius", radius, minimum=0.0, strict=True),
            edges=_indices(operation, "edges", edges),
            label=label,
        )

    def sew(
        self,
        shapes: Sequence[DomainValue],
        *,
        output_type: str = "shell",
        label: str = "",
    ) -> DomainValue:
        """Sew touching faces/shells; a closed sewn shell may be promoted to a solid."""

        operation = "sew"
        clean_type = _result_type(operation, output_type)
        if clean_type not in {"shell", "solid", "compound"}:
            raise _error(
                operation,
                "output_type",
                "must be 'shell', 'solid', or 'compound'",
                output_type,
            )
        return self._value(
            operation,
            clean_type,
            _shapes(
                operation,
                "shapes",
                shapes,
                allowed={"face", "shell", "compound"},
            ),
            label=label,
        )

    def repair(
        self,
        shape: DomainValue,
        *,
        working_tolerance: float = 1.0e-7,
        minimum_tolerance: float = 1.0e-7,
        maximum_tolerance: float = 1.0e-3,
        label: str = "",
    ) -> DomainValue:
        """Run ShapeFix on a copy using explicit bounded tolerance limits."""

        operation = "repair"
        minimum = _number(
            operation,
            "minimum_tolerance",
            minimum_tolerance,
            minimum=0.0,
            strict=True,
        )
        working = _number(
            operation,
            "working_tolerance",
            working_tolerance,
            minimum=0.0,
            strict=True,
        )
        maximum = _number(
            operation,
            "maximum_tolerance",
            maximum_tolerance,
            minimum=0.0,
            strict=True,
        )
        if not minimum <= working <= maximum:
            raise _error(
                operation,
                "minimum_tolerance/working_tolerance/maximum_tolerance",
                "must satisfy minimum <= working <= maximum",
            )
        clean_shape = _shape(operation, "shape", shape)
        return self._value(
            operation,
            clean_shape.output_type,
            clean_shape,
            working_tolerance=working,
            minimum_tolerance=minimum,
            maximum_tolerance=maximum,
            label=label,
        )

    def chamfer(
        self,
        shape: DomainValue,
        distance: float,
        *,
        edges: str | Sequence[int] = "all",
        label: str = "",
    ) -> DomainValue:
        """Chamfer selected 1-based edges of a solid; use edges='all' deliberately."""

        operation = "chamfer"
        clean_shape = _shape(operation, "shape", shape, allowed={"solid", "shell"})
        return self._value(
            operation,
            clean_shape.output_type,
            clean_shape,
            _number(operation, "distance", distance, minimum=0.0, strict=True),
            edges=_indices(operation, "edges", edges),
            label=label,
        )

    def offset(
        self,
        shape: DomainValue,
        distance: float,
        *,
        tolerance: float = 1.0e-7,
        join: str = "arc",
        fill: bool = False,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Create a 3D skin offset of a face, shell, or solid."""

        operation = "offset"
        clean_join = str(join or "").strip().lower()
        if clean_join not in _JOIN_TYPES:
            raise _error(operation, "join", f"must be one of {sorted(_JOIN_TYPES)}", join)
        clean_shape = _shape(operation, "shape", shape, allowed={"face", "shell", "solid"})
        if bool(fill) and clean_shape.output_type != "shell":
            raise _error(operation, "fill", "is supported only when offsetting a shell", fill)
        if clean_shape.output_type == "shell" and bool(fill):
            inferred = "solid"
        elif clean_shape.output_type == "face":
            inferred = "shell"
        else:
            inferred = clean_shape.output_type
        return self._value(
            operation,
            _inferred_result_type(operation, output_type, inferred),
            clean_shape,
            _nonzero_number(operation, "distance", distance),
            tolerance=_number(operation, "tolerance", tolerance, minimum=0.0, strict=True),
            join=clean_join,
            fill=bool(fill),
            label=label,
        )

    def offset2d(
        self,
        shape: DomainValue,
        distance: float,
        *,
        join: str = "arc",
        fill: bool = False,
        open_result: bool = False,
        intersection: bool = False,
        output_type: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Planar-offset an edge, wire, or face; optionally fill the swept region."""

        operation = "offset2d"
        clean_join = str(join or "").strip().lower()
        if clean_join not in _JOIN_TYPES:
            raise _error(operation, "join", f"must be one of {sorted(_JOIN_TYPES)}", join)
        clean_shape = _shape(operation, "shape", shape, allowed={"edge", "wire", "face"})
        inferred = "face" if bool(fill) else "wire"
        return self._value(
            operation,
            _inferred_result_type(operation, output_type, inferred, exact=True),
            clean_shape,
            _nonzero_number(operation, "distance", distance),
            join=clean_join,
            fill=bool(fill),
            open_result=bool(open_result),
            intersection=bool(intersection),
            label=label,
        )

    def thicken(
        self,
        shape: DomainValue,
        faces: Sequence[int],
        thickness: float,
        *,
        tolerance: float = 1.0e-7,
        join: str = "arc",
        label: str = "",
    ) -> DomainValue:
        """Remove selected 1-based faces and thicken the remaining shell into a solid."""

        operation = "thicken"
        clean_join = str(join or "").strip().lower()
        if clean_join not in _JOIN_TYPES:
            raise _error(operation, "join", f"must be one of {sorted(_JOIN_TYPES)}", join)
        return self._value(
            operation,
            "solid",
            _shape(operation, "shape", shape, allowed={"solid", "shell"}),
            _indices(operation, "faces", faces),
            _nonzero_number(operation, "thickness", thickness),
            tolerance=_number(operation, "tolerance", tolerance, minimum=0.0, strict=True),
            join=clean_join,
            label=label,
        )

    def transform(
        self,
        shape: DomainValue,
        *,
        translation: Sequence[float] = (0.0, 0.0, 0.0),
        rotation_axis: Sequence[float] = (0.0, 0.0, 1.0),
        rotation_degrees: float = 0.0,
        scale: float | Sequence[float] = 1.0,
        pivot: Sequence[float] = (0.0, 0.0, 0.0),
        label: str = "",
    ) -> DomainValue:
        """Copy, scale and rotate about pivot, then translate a shape."""

        operation = "transform"
        if isinstance(scale, (list, tuple)):
            clean_scale = _vector(operation, "scale", scale)
            if any(value <= 0.0 for value in clean_scale):
                raise _error(operation, "scale", "all scale factors must be positive", scale)
        else:
            factor = _number(operation, "scale", scale, minimum=0.0, strict=True)
            clean_scale = [factor, factor, factor]
        clean_shape = _shape(operation, "shape", shape)
        return self._value(
            operation,
            clean_shape.output_type,
            clean_shape,
            translation=_vector(operation, "translation", translation),
            rotation_axis=_vector(operation, "rotation_axis", rotation_axis, nonzero=True),
            rotation_degrees=_number(operation, "rotation_degrees", rotation_degrees),
            scale=clean_scale,
            pivot=_vector(operation, "pivot", pivot),
            label=label,
        )

    def mirror(
        self,
        shape: DomainValue,
        plane_origin: Sequence[float],
        plane_normal: Sequence[float],
        *,
        label: str = "",
    ) -> DomainValue:
        """Copy and mirror a shape across an explicit plane."""

        operation = "mirror"
        clean_shape = _shape(operation, "shape", shape)
        return self._value(
            operation,
            clean_shape.output_type,
            clean_shape,
            _vector(operation, "plane_origin", plane_origin),
            _vector(operation, "plane_normal", plane_normal, nonzero=True),
            label=label,
        )

    def project(
        self,
        target: DomainValue,
        profile: DomainValue,
        vector: Sequence[float],
        *,
        mode: str = "parallel",
        output_type: str = "wire",
        label: str = "",
    ) -> DomainValue:
        """Project a profile onto a target by direction or from a viewpoint."""

        operation = "project"
        clean_mode = str(mode or "").strip().lower()
        if clean_mode not in _PROJECTION_MODES:
            raise _error(
                operation,
                "mode",
                f"must be one of {sorted(_PROJECTION_MODES)}",
                mode,
            )
        clean_type = _result_type(operation, output_type)
        if clean_type not in {"wire", "compound"}:
            raise _error(
                operation,
                "output_type",
                "must be 'wire' or 'compound'",
                output_type,
            )
        return self._value(
            operation,
            clean_type,
            _shape(operation, "target", target, allowed={"face", "shell", "solid", "compound"}),
            _shape(operation, "profile", profile, allowed={"edge", "wire"}),
            _vector(
                operation,
                "vector",
                vector,
                nonzero=clean_mode == "parallel",
            ),
            mode=clean_mode,
            label=label,
        )

    def refine(self, shape: DomainValue, *, label: str = "") -> DomainValue:
        """Copy a shape and remove redundant splitter edges and faces."""

        operation = "refine"
        clean_shape = _shape(operation, "shape", shape)
        return self._value(
            operation,
            clean_shape.output_type,
            clean_shape,
            label=label,
        )

    @property
    def exported_names(self) -> tuple[str, ...]:
        return (
            "from_object",
            "box",
            "wedge",
            "plane",
            "prism",
            "cylinder",
            "cone",
            "sphere",
            "torus",
            "line",
            "arc",
            "circle",
            "ellipse",
            "bezier",
            "bspline",
            "nurbs_curve",
            "helix",
            "wire",
            "face",
            "shell",
            "solid",
            "compound",
            "subshape",
            "extrude",
            "revolve",
            "loft",
            "sweep",
            "ruled_surface",
            "filled_surface",
            "fuse",
            "cut",
            "common",
            "section",
            "general_fuse",
            "slice",
            "defeature",
            "to_nurbs",
            "reverse",
            "sew",
            "repair",
            "fillet",
            "chamfer",
            "offset",
            "offset2d",
            "thicken",
            "transform",
            "mirror",
            "project",
            "refine",
        )
