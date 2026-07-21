# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production BIM VibeScript programs.

The API describes one native Arch hierarchy in level-local coordinates.  It
does not expose document factories or native FreeCAD objects: every value is a
bounded immutable graph node that is independently rebuilt and validated in an
isolated ``FreeCADCmd`` worker.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
from typing import Any

from vibescript_domain_api import DomainValue


_EXPORTS = ("site", "building", "level", "wall", "slab", "structure", "opening")
_OUTPUT_TYPES = _EXPORTS
_MAX_LABEL_CHARS = 256
_MAX_ADDRESS_CHARS = 1024
_MAX_LOCATION_CHARS = 256
_MAX_WALL_POINTS = 512
_MAX_SLAB_POINTS = 4096
_MAX_COORDINATE = 10_000_000.0
_MAX_LENGTH = 10_000_000.0
_EPSILON = 1.0e-9
_ALIGNMENTS = ("left", "right", "center")
_STRUCTURE_ROLES = ("column", "beam", "member")
_MISSING = object()


class BIMAPIError(ValueError):
    """A source error carrying one exact repair target for the operating model."""

    def __init__(
        self,
        message: str,
        *,
        operation: str,
        parameter: str,
        reason: str,
    ) -> None:
        self.details = {
            "stage": "source_validation",
            "operation": operation,
            "parameter": parameter,
            "reason": reason,
            "correction": (
                f"Correct api.{operation} parameter {parameter!r}: it {reason}. "
                "Change only the failing source expression, then retry against the "
                "failed working revision."
            ),
        }
        super().__init__(message)


def _error(
    operation: str,
    parameter: str,
    reason: str,
    value: Any = _MISSING,
) -> BIMAPIError:
    suffix = "" if value is _MISSING else f"; received {value!r}"
    return BIMAPIError(
        f"api.{operation}: {parameter} {reason}{suffix}.",
        operation=operation,
        parameter=parameter,
        reason=reason,
    )


def _number(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: float | None = None,
    maximum: float | None = None,
    strict_minimum: bool = False,
) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise _error(operation, parameter, "must be a finite number", value)
    clean = float(value)
    if not math.isfinite(clean):
        raise _error(operation, parameter, "must be finite", value)
    if minimum is not None and (
        clean <= minimum if strict_minimum else clean < minimum
    ):
        relation = "greater than" if strict_minimum else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
    if maximum is not None and clean > maximum:
        raise _error(operation, parameter, f"must be at most {maximum:g}", value)
    return clean


def _integer(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> int:
    if isinstance(value, bool) or type(value) is not int:
        raise _error(operation, parameter, "must be an integer", value)
    if not minimum <= value <= maximum:
        raise _error(
            operation,
            parameter,
            f"must be in the inclusive range {minimum}-{maximum}",
            value,
        )
    return value


def _text(
    operation: str,
    parameter: str,
    value: Any,
    *,
    maximum: int,
) -> str:
    if not isinstance(value, str) or len(value) > maximum:
        raise _error(
            operation,
            parameter,
            f"must be a string of at most {maximum} characters",
            value,
        )
    if "\0" in value:
        raise _error(operation, parameter, "cannot contain a null character")
    return value


def _label(operation: str, value: Any) -> str:
    return _text(operation, "label", value, maximum=_MAX_LABEL_CHARS)


def _parent(
    operation: str,
    parameter: str,
    value: Any,
    output_type: str,
) -> DomainValue:
    if not isinstance(value, DomainValue) or value.domain != "bim":
        raise _error(
            operation,
            parameter,
            "must be a value returned by this BIM api",
            type(value).__name__,
        )
    if value.output_type != output_type or value.operation != output_type:
        raise _error(
            operation,
            parameter,
            f"must be a BIM {output_type} value",
            value.output_type,
        )
    return value


def _point2(operation: str, parameter: str, value: Any) -> tuple[float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise _error(operation, parameter, "must be [x, y]", value)
    return (
        _number(
            operation,
            f"{parameter}[0]",
            value[0],
            minimum=-_MAX_COORDINATE,
            maximum=_MAX_COORDINATE,
        ),
        _number(
            operation,
            f"{parameter}[1]",
            value[1],
            minimum=-_MAX_COORDINATE,
            maximum=_MAX_COORDINATE,
        ),
    )


def _points2(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: int,
    maximum: int,
) -> tuple[tuple[float, float], ...]:
    if not isinstance(value, (list, tuple)) or not minimum <= len(value) <= maximum:
        raise _error(
            operation,
            parameter,
            f"must contain {minimum}-{maximum} [x, y] points",
            value,
        )
    result = tuple(
        _point2(operation, f"{parameter}[{index}]", point)
        for index, point in enumerate(value)
    )
    for index in range(1, len(result)):
        if _distance(result[index - 1], result[index]) <= _EPSILON:
            raise _error(
                operation,
                f"{parameter}[{index}]",
                "must differ from the preceding point",
                result[index],
            )
    if _distance(result[0], result[-1]) <= _EPSILON:
        raise _error(
            operation,
            f"{parameter}[-1]",
            "must not repeat the first point; use the operation's closed profile",
            result[-1],
        )
    return result


def _distance(left: Sequence[float], right: Sequence[float]) -> float:
    return math.hypot(float(right[0]) - float(left[0]), float(right[1]) - float(left[1]))


def _orientation(
    first: Sequence[float],
    second: Sequence[float],
    third: Sequence[float],
) -> float:
    return (
        (float(second[0]) - float(first[0]))
        * (float(third[1]) - float(first[1]))
        - (float(second[1]) - float(first[1]))
        * (float(third[0]) - float(first[0]))
    )


def _between(first: float, second: float, value: float) -> bool:
    return min(first, second) - _EPSILON <= value <= max(first, second) + _EPSILON


def _segments_intersect(
    a: Sequence[float],
    b: Sequence[float],
    c: Sequence[float],
    d: Sequence[float],
) -> bool:
    ab_c = _orientation(a, b, c)
    ab_d = _orientation(a, b, d)
    cd_a = _orientation(c, d, a)
    cd_b = _orientation(c, d, b)
    if ((ab_c > _EPSILON and ab_d < -_EPSILON) or (ab_c < -_EPSILON and ab_d > _EPSILON)) and (
        (cd_a > _EPSILON and cd_b < -_EPSILON)
        or (cd_a < -_EPSILON and cd_b > _EPSILON)
    ):
        return True
    for value, first, second, other in (
        (ab_c, a, b, c),
        (ab_d, a, b, d),
        (cd_a, c, d, a),
        (cd_b, c, d, b),
    ):
        if abs(value) <= _EPSILON and _between(first[0], second[0], other[0]) and _between(
            first[1], second[1], other[1]
        ):
            return True
    return False


def _reject_self_intersections(
    operation: str,
    parameter: str,
    points: Sequence[Sequence[float]],
    *,
    closed: bool,
) -> None:
    segment_count = len(points) if closed else len(points) - 1
    segments = [
        (points[index], points[(index + 1) % len(points)])
        for index in range(segment_count)
    ]
    for first_index, (a, b) in enumerate(segments):
        for second_index in range(first_index + 1, len(segments)):
            if second_index == first_index + 1:
                continue
            if closed and first_index == 0 and second_index == len(segments) - 1:
                continue
            c, d = segments[second_index]
            if _segments_intersect(a, b, c, d):
                raise _error(
                    operation,
                    parameter,
                    f"segments {first_index} and {second_index} intersect",
                )


def _polygon_area(points: Sequence[Sequence[float]]) -> float:
    return 0.5 * sum(
        float(points[index][0]) * float(points[(index + 1) % len(points)][1])
        - float(points[(index + 1) % len(points)][0]) * float(points[index][1])
        for index in range(len(points))
    )


def _placement(operation: str, value: Any) -> dict[str, tuple[float, ...]]:
    if value is None:
        return {
            "position": (0.0, 0.0, 0.0),
            "rotation": (0.0, 0.0, 0.0, 1.0),
        }
    if not isinstance(value, Mapping) or set(value) != {"position", "rotation"}:
        raise _error(
            operation,
            "placement",
            "must contain exactly position and quaternion rotation",
            value,
        )
    position = value.get("position")
    rotation = value.get("rotation")
    if not isinstance(position, (list, tuple)) or len(position) != 3:
        raise _error(operation, "placement.position", "must be [x, y, z]", position)
    clean_position = tuple(
        _number(
            operation,
            f"placement.position[{index}]",
            item,
            minimum=-_MAX_COORDINATE,
            maximum=_MAX_COORDINATE,
        )
        for index, item in enumerate(position)
    )
    if not isinstance(rotation, (list, tuple)) or len(rotation) != 4:
        raise _error(
            operation,
            "placement.rotation",
            "must be quaternion [x, y, z, w]",
            rotation,
        )
    clean_rotation = tuple(
        _number(operation, f"placement.rotation[{index}]", item)
        for index, item in enumerate(rotation)
    )
    magnitude = math.sqrt(sum(item * item for item in clean_rotation))
    if magnitude <= _EPSILON:
        raise _error(operation, "placement.rotation", "quaternion must be non-zero")
    return {
        "position": clean_position,
        "rotation": tuple(item / magnitude for item in clean_rotation),
    }


class BIMDomainAPI:
    """Immutable native-Arch hierarchy API injected into BIM source."""

    __slots__ = ("_next_graph_id",)

    domain = "bim"
    exported_names = _EXPORTS

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        if tuple(dict.fromkeys(str(item) for item in exports)) != _EXPORTS:
            raise RuntimeError(
                f"BIM pack exports must be exactly {_EXPORTS!r}."
            )
        if tuple(dict.fromkeys(str(item) for item in output_types)) != _OUTPUT_TYPES:
            raise RuntimeError(
                f"BIM pack output types must be exactly {_OUTPUT_TYPES!r}."
            )
        object.__setattr__(self, "_next_graph_id", 1)

    def _graph_id(self) -> str:
        value = int(self._next_graph_id)
        object.__setattr__(self, "_next_graph_id", value + 1)
        return f"bim{value}"

    def _value(
        self,
        operation: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain=self.domain,
            operation=operation,
            output_type=operation,
            arguments=tuple(arguments),
            properties=properties,
        )

    def site(
        self,
        *,
        address: str = "",
        postal_code: str = "",
        city: str = "",
        region: str = "",
        country: str = "",
        latitude: float = 0.0,
        longitude: float = 0.0,
        elevation: float = 0.0,
        label: str = "",
    ) -> DomainValue:
        """Create one spatial root with native Arch Site geolocation metadata."""

        return self._value(
            "site",
            address=_text("site", "address", address, maximum=_MAX_ADDRESS_CHARS),
            postal_code=_text(
                "site", "postal_code", postal_code, maximum=_MAX_LOCATION_CHARS
            ),
            city=_text("site", "city", city, maximum=_MAX_LOCATION_CHARS),
            region=_text("site", "region", region, maximum=_MAX_LOCATION_CHARS),
            country=_text("site", "country", country, maximum=_MAX_LOCATION_CHARS),
            latitude=_number("site", "latitude", latitude, minimum=-90.0, maximum=90.0),
            longitude=_number(
                "site", "longitude", longitude, minimum=-180.0, maximum=180.0
            ),
            elevation=_number(
                "site",
                "elevation",
                elevation,
                minimum=-_MAX_COORDINATE,
                maximum=_MAX_COORDINATE,
            ),
            label=_label("site", label),
            graph_id=self._graph_id(),
        )

    def building(self, site: DomainValue, *, label: str = "") -> DomainValue:
        """Create one native Building container under exactly one returned Site."""

        return self._value(
            "building",
            _parent("building", "site", site, "site"),
            label=_label("building", label),
            graph_id=self._graph_id(),
        )

    def level(
        self,
        building: DomainValue,
        elevation: float,
        *,
        height: float = 3000.0,
        label: str = "",
    ) -> DomainValue:
        """Create one native Building Storey that owns level-local child geometry."""

        return self._value(
            "level",
            _parent("level", "building", building, "building"),
            elevation=_number(
                "level",
                "elevation",
                elevation,
                minimum=-_MAX_COORDINATE,
                maximum=_MAX_COORDINATE,
            ),
            height=_number(
                "level", "height", height, minimum=0.0, maximum=_MAX_LENGTH, strict_minimum=True
            ),
            label=_label("level", label),
            graph_id=self._graph_id(),
        )

    def wall(
        self,
        level: DomainValue,
        points: Sequence[Sequence[float]],
        *,
        closed: bool = False,
        width: float = 200.0,
        height: float = 2800.0,
        alignment: str = "center",
        offset: float = 0.0,
        label: str = "",
    ) -> DomainValue:
        """Create one open or closed native Wall from a level-local polyline baseline."""

        parent = _parent("wall", "level", level, "level")
        clean_points = _points2(
            "wall", "points", points, minimum=2, maximum=_MAX_WALL_POINTS
        )
        if not isinstance(closed, bool):
            raise _error("wall", "closed", "must be boolean", closed)
        if closed and len(clean_points) < 3:
            raise _error("wall", "closed", "requires at least three points")
        _reject_self_intersections("wall", "points", clean_points, closed=closed)
        clean_alignment = str(alignment or "").strip().lower()
        if clean_alignment not in _ALIGNMENTS:
            raise _error(
                "wall", "alignment", f"must be one of {list(_ALIGNMENTS)!r}", alignment
            )
        return self._value(
            "wall",
            parent,
            clean_points,
            closed=closed,
            width=_number(
                "wall", "width", width, minimum=0.0, maximum=_MAX_LENGTH, strict_minimum=True
            ),
            height=_number(
                "wall", "height", height, minimum=0.0, maximum=_MAX_LENGTH, strict_minimum=True
            ),
            alignment=clean_alignment,
            offset=_number(
                "wall",
                "offset",
                offset,
                minimum=-_MAX_LENGTH,
                maximum=_MAX_LENGTH,
            ),
            label=_label("wall", label),
            graph_id=self._graph_id(),
        )

    def slab(
        self,
        level: DomainValue,
        boundary: Sequence[Sequence[float]],
        *,
        thickness: float = 200.0,
        top_offset: float = 0.0,
        label: str = "",
    ) -> DomainValue:
        """Create one downward-extruded native Slab from a level-local polygon."""

        parent = _parent("slab", "level", level, "level")
        clean_boundary = _points2(
            "slab", "boundary", boundary, minimum=3, maximum=_MAX_SLAB_POINTS
        )
        _reject_self_intersections("slab", "boundary", clean_boundary, closed=True)
        if abs(_polygon_area(clean_boundary)) <= _EPSILON:
            raise _error("slab", "boundary", "must enclose a non-zero planar area")
        return self._value(
            "slab",
            parent,
            clean_boundary,
            thickness=_number(
                "slab",
                "thickness",
                thickness,
                minimum=0.0,
                maximum=_MAX_LENGTH,
                strict_minimum=True,
            ),
            top_offset=_number(
                "slab",
                "top_offset",
                top_offset,
                minimum=-_MAX_LENGTH,
                maximum=_MAX_LENGTH,
            ),
            label=_label("slab", label),
            graph_id=self._graph_id(),
        )

    def structure(
        self,
        level: DomainValue,
        length: float,
        width: float,
        height: float,
        *,
        placement: Mapping[str, Any] | None = None,
        role: str = "column",
        label: str = "",
    ) -> DomainValue:
        """Create one rectangular Column, Beam, or Member using role and placement."""

        clean_role = str(role or "").strip().lower()
        if clean_role not in _STRUCTURE_ROLES:
            raise _error(
                "structure", "role", f"must be one of {list(_STRUCTURE_ROLES)!r}", role
            )
        dimensions = tuple(
            _number(
                "structure",
                name,
                value,
                minimum=0.0,
                maximum=_MAX_LENGTH,
                strict_minimum=True,
            )
            for name, value in (("length", length), ("width", width), ("height", height))
        )
        return self._value(
            "structure",
            _parent("structure", "level", level, "level"),
            *dimensions,
            placement=_placement("structure", placement),
            role=clean_role,
            label=_label("structure", label),
            graph_id=self._graph_id(),
        )

    def opening(
        self,
        host: DomainValue,
        width: float,
        height: float,
        *,
        segment: int = 0,
        offset: float = 0.0,
        sill: float = 0.0,
        hole_depth: float = 0.0,
        label: str = "",
    ) -> DomainValue:
        """Cut one hosted void in a returned Wall segment; no door/window fill is created."""

        return self._value(
            "opening",
            _parent("opening", "host", host, "wall"),
            width=_number(
                "opening",
                "width",
                width,
                minimum=0.0,
                maximum=_MAX_LENGTH,
                strict_minimum=True,
            ),
            height=_number(
                "opening",
                "height",
                height,
                minimum=0.0,
                maximum=_MAX_LENGTH,
                strict_minimum=True,
            ),
            segment=_integer("opening", "segment", segment, minimum=0, maximum=_MAX_WALL_POINTS),
            offset=_number(
                "opening", "offset", offset, minimum=0.0, maximum=_MAX_LENGTH
            ),
            sill=_number(
                "opening", "sill", sill, minimum=0.0, maximum=_MAX_LENGTH
            ),
            hole_depth=_number(
                "opening", "hole_depth", hole_depth, minimum=0.0, maximum=_MAX_LENGTH
            ),
            label=_label("opening", label),
            graph_id=self._graph_id(),
        )
