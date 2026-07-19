# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production Draft VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_SHAPE_OUTPUT_TYPES = frozenset(
    {"wire", "circle", "rectangle", "bspline", "array"}
)
_MAX_POINTS = 4096
_MAX_WIRE_SUBDIVISIONS = 4096
_MAX_WIRE_SEGMENTS = 100_000
_MAX_ARRAY_ELEMENTS = 100_000
_MAX_TEXT_LINES = 128
_MAX_TEXT_CHARS = 16_384


class DraftAPIError(ValueError):
    """A source error carrying an exact repair target for the operating model."""

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
    value: Any = None,
) -> DraftAPIError:
    suffix = "" if value is None else f"; received {value!r}"
    return DraftAPIError(
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
    result = float(value)
    if not math.isfinite(result):
        raise _error(operation, parameter, "must be finite", value)
    if minimum is not None and (
        result <= minimum if strict_minimum else result < minimum
    ):
        qualifier = "greater than" if strict_minimum else "at least"
        raise _error(operation, parameter, f"must be {qualifier} {minimum:g}", value)
    if maximum is not None and result > maximum:
        raise _error(operation, parameter, f"must be at most {maximum:g}", value)
    return result


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


def _vector(operation: str, parameter: str, value: Any) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "must be [x, y, z]", value)
    return tuple(
        _number(operation, f"{parameter}[{index}]", item)
        for index, item in enumerate(value)
    )


def _placement(operation: str, value: Any) -> dict[str, tuple[float, ...]]:
    if value is None:
        return {
            "position": (0.0, 0.0, 0.0),
            "rotation": (0.0, 0.0, 0.0, 1.0),
        }
    if not isinstance(value, Mapping) or set(value) not in (
        {"position", "rotation"},
        {"position", "axis", "angle_degrees"},
    ):
        raise _error(
            operation,
            "placement",
            "must contain position and quaternion rotation, or position, axis, and angle_degrees",
            value,
        )
    position = _vector(operation, "placement.position", value.get("position"))
    if "rotation" in value:
        raw_rotation = value.get("rotation")
        if not isinstance(raw_rotation, (list, tuple)) or len(raw_rotation) != 4:
            raise _error(
                operation,
                "placement.rotation",
                "must be quaternion [x, y, z, w]",
                raw_rotation,
            )
        rotation = tuple(
            _number(operation, f"placement.rotation[{index}]", item)
            for index, item in enumerate(raw_rotation)
        )
        magnitude = math.sqrt(sum(item * item for item in rotation))
        if magnitude <= 1.0e-12:
            raise _error(operation, "placement.rotation", "quaternion must be non-zero")
        normalized = tuple(item / magnitude for item in rotation)
    else:
        axis = _vector(operation, "placement.axis", value.get("axis"))
        magnitude = math.sqrt(sum(item * item for item in axis))
        if magnitude <= 1.0e-12:
            raise _error(operation, "placement.axis", "must be non-zero")
        half_angle = math.radians(
            _number(operation, "placement.angle_degrees", value.get("angle_degrees"))
        ) / 2.0
        scale = math.sin(half_angle) / magnitude
        normalized = (
            axis[0] * scale,
            axis[1] * scale,
            axis[2] * scale,
            math.cos(half_angle),
        )
    return {
        "position": position,
        "rotation": normalized,
    }


def _label(operation: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise _error(operation, "label", "must be a string of at most 256 characters")
    return value


def _points(
    operation: str,
    value: Any,
    *,
    minimum: int,
) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(value, (list, tuple)) or not minimum <= len(value) <= _MAX_POINTS:
        raise _error(
            operation,
            "points",
            f"must contain {minimum}-{_MAX_POINTS} points",
        )
    result = tuple(
        _vector(operation, f"points[{index}]", point)
        for index, point in enumerate(value)
    )
    for index in range(1, len(result)):
        if result[index] == result[index - 1]:
            raise _error(
                operation,
                f"points[{index}]",
                "must differ from the preceding point",
                result[index],
            )
    if len(result) > 2 and result[0] == result[-1]:
        raise _error(
            operation,
            "points[-1]",
            "must not repeat the first point; use closed=True",
        )
    return result


def _wire_corner_limit(
    points: tuple[tuple[float, float, float], ...],
    *,
    closed: bool,
) -> tuple[int, float]:
    """Return the treatable turn count and a conservative global corner limit."""

    point_count = len(points)
    segment_count = point_count if closed else point_count - 1
    segment_lengths = []
    for index in range(segment_count):
        start = points[index]
        end = points[(index + 1) % point_count]
        segment_lengths.append(
            math.sqrt(sum((end[axis] - start[axis]) ** 2 for axis in range(3)))
        )
    tangent_multipliers = [0.0] * point_count
    vertices = range(point_count) if closed else range(1, point_count - 1)
    turn_count = 0
    for index in vertices:
        previous = points[(index - 1) % point_count]
        current = points[index]
        following = points[(index + 1) % point_count]
        before = tuple(previous[axis] - current[axis] for axis in range(3))
        after = tuple(following[axis] - current[axis] for axis in range(3))
        before_length = math.sqrt(sum(value * value for value in before))
        after_length = math.sqrt(sum(value * value for value in after))
        cosine = sum(left * right for left, right in zip(before, after)) / (
            before_length * after_length
        )
        angle = math.acos(max(-1.0, min(1.0, cosine)))
        if angle <= 1.0e-10 or abs(math.pi - angle) <= 1.0e-10:
            continue
        tangent_multipliers[index] = 1.0 / math.tan(angle / 2.0)
        turn_count += 1
    limits = []
    for index, length in enumerate(segment_lengths):
        start_multiplier = tangent_multipliers[index]
        end_multiplier = tangent_multipliers[(index + 1) % point_count]
        consumed_per_unit = start_multiplier + end_multiplier
        if consumed_per_unit > 1.0e-12:
            limits.append(length / consumed_per_unit)
    return turn_count, min(limits, default=0.0)


def _reference(operation: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _error(
            operation,
            "source",
            "must be a Draft graph value or stable document reference",
            value,
        )
    document_uid = str(value.get("document_uid") or "").strip()
    object_name = str(value.get("object_name") or "").strip()
    if not document_uid or not _NAME.fullmatch(object_name):
        raise _error(
            operation,
            "source",
            "reference must contain non-empty document_uid and stable object_name",
        )
    return {"document_uid": document_uid, "object_name": object_name}


class DraftDomainAPI:
    """Explicit parametric-object API injected into Draft source."""

    __slots__ = ("_next_graph_id",)

    domain = "draft"
    exported_names = (
        "wire",
        "circle",
        "rectangle",
        "bspline",
        "array",
        "text",
    )

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared = tuple(dict.fromkeys(str(item) for item in exports))
        if declared != self.exported_names:
            raise RuntimeError(
                "Draft pack exports do not match the production runtime contract: "
                f"expected {self.exported_names!r}, received {declared!r}."
            )
        expected_types = (
            "wire",
            "circle",
            "rectangle",
            "bspline",
            "array",
            "text",
        )
        if tuple(dict.fromkeys(str(item) for item in output_types)) != expected_types:
            raise RuntimeError(
                "Draft pack must publish exactly wire, circle, rectangle, bspline, array, and text."
            )
        object.__setattr__(self, "_next_graph_id", 1)

    def _graph_id(self) -> str:
        value = int(self._next_graph_id)
        object.__setattr__(self, "_next_graph_id", value + 1)
        return f"d{value}"

    def _value(
        self,
        operation: str,
        output_type: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain=self.domain,
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def wire(
        self,
        points: Sequence[Sequence[float]],
        *,
        closed: bool = False,
        make_face: bool = False,
        fillet_radius: float = 0.0,
        chamfer_size: float = 0.0,
        subdivisions: int = 0,
        placement: Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Create one editable native Draft wire or polyline."""

        clean_points = _points("wire", points, minimum=2)
        if not isinstance(closed, bool) or not isinstance(make_face, bool):
            raise _error("wire", "closed/make_face", "must be booleans")
        if make_face and (not closed or len(clean_points) < 3):
            raise _error(
                "wire",
                "make_face",
                "requires closed=True and at least three points",
            )
        clean_fillet = _number(
            "wire", "fillet_radius", fillet_radius, minimum=0.0
        )
        clean_chamfer = _number(
            "wire", "chamfer_size", chamfer_size, minimum=0.0
        )
        clean_subdivisions = _integer(
            "wire",
            "subdivisions",
            subdivisions,
            minimum=0,
            maximum=_MAX_WIRE_SUBDIVISIONS,
        )
        if clean_fillet > 0.0 and clean_chamfer > 0.0:
            raise _error(
                "wire",
                "fillet_radius/chamfer_size",
                "must not both be non-zero; choose one corner treatment",
            )
        if clean_subdivisions > 0 and (clean_fillet > 0.0 or clean_chamfer > 0.0):
            raise _error(
                "wire",
                "subdivisions",
                "must be 0 when fillet_radius or chamfer_size is non-zero",
            )
        corner_amount = max(clean_fillet, clean_chamfer)
        if corner_amount > 0.0:
            turn_count, corner_limit = _wire_corner_limit(
                clean_points,
                closed=closed,
            )
            parameter = "fillet_radius" if clean_fillet > 0.0 else "chamfer_size"
            if turn_count == 0:
                raise _error(
                    "wire",
                    parameter,
                    "requires at least one non-collinear corner",
                )
            if corner_amount >= corner_limit:
                raise _error(
                    "wire",
                    parameter,
                    (
                        "must be less than "
                        f"{corner_limit:g} for the adjacent segment lengths and angles"
                    ),
                    corner_amount,
                )
        base_segments = len(clean_points) if closed else len(clean_points) - 1
        generated_segments = base_segments * (clean_subdivisions + 1)
        if generated_segments > _MAX_WIRE_SEGMENTS:
            raise _error(
                "wire",
                "subdivisions",
                f"would generate {generated_segments} segments; maximum is {_MAX_WIRE_SEGMENTS}",
            )
        return self._value(
            "wire",
            "wire",
            clean_points,
            closed=closed,
            make_face=make_face,
            fillet_radius=clean_fillet,
            chamfer_size=clean_chamfer,
            subdivisions=clean_subdivisions,
            placement=_placement("wire", placement),
            label=_label("wire", label),
            graph_id=self._graph_id(),
        )

    def circle(
        self,
        radius: float,
        *,
        start_angle: float = 0.0,
        end_angle: float = 360.0,
        make_face: bool = False,
        placement: Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Create one editable native Draft circle or circular arc."""

        clean_radius = _number("circle", "radius", radius, minimum=0.0, strict_minimum=True)
        clean_start = _number(
            "circle", "start_angle", start_angle, minimum=-360.0, maximum=360.0
        )
        clean_end = _number(
            "circle", "end_angle", end_angle, minimum=-360.0, maximum=360.0
        )
        if not isinstance(make_face, bool):
            raise _error("circle", "make_face", "must be boolean")
        full_circle = math.isclose(
            (clean_end - clean_start) % 360.0,
            0.0,
            rel_tol=0.0,
            abs_tol=1.0e-12,
        )
        if make_face and not full_circle:
            raise _error("circle", "make_face", "requires a full 360-degree circle")
        return self._value(
            "circle",
            "circle",
            clean_radius,
            start_angle=clean_start,
            end_angle=clean_end,
            make_face=make_face,
            placement=_placement("circle", placement),
            label=_label("circle", label),
            graph_id=self._graph_id(),
        )

    def rectangle(
        self,
        length: float,
        height: float,
        *,
        make_face: bool = False,
        fillet_radius: float = 0.0,
        chamfer_size: float = 0.0,
        placement: Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Create one editable native Draft rectangle."""

        clean_length = _number(
            "rectangle", "length", length, minimum=0.0, strict_minimum=True
        )
        clean_height = _number(
            "rectangle", "height", height, minimum=0.0, strict_minimum=True
        )
        if not isinstance(make_face, bool):
            raise _error("rectangle", "make_face", "must be boolean")
        clean_fillet = _number(
            "rectangle", "fillet_radius", fillet_radius, minimum=0.0
        )
        clean_chamfer = _number(
            "rectangle", "chamfer_size", chamfer_size, minimum=0.0
        )
        if clean_fillet > 0.0 and clean_chamfer > 0.0:
            raise _error(
                "rectangle",
                "fillet_radius/chamfer_size",
                "must not both be non-zero; choose one corner treatment",
            )
        half_short_side = min(clean_length, clean_height) / 2.0
        for parameter, amount in (
            ("fillet_radius", clean_fillet),
            ("chamfer_size", clean_chamfer),
        ):
            if amount >= half_short_side and amount > 0.0:
                raise _error(
                    "rectangle",
                    parameter,
                    f"must be less than half the shorter side ({half_short_side:g})",
                    amount,
                )
        return self._value(
            "rectangle",
            "rectangle",
            clean_length,
            clean_height,
            make_face=make_face,
            fillet_radius=clean_fillet,
            chamfer_size=clean_chamfer,
            placement=_placement("rectangle", placement),
            label=_label("rectangle", label),
            graph_id=self._graph_id(),
        )

    def bspline(
        self,
        points: Sequence[Sequence[float]],
        *,
        closed: bool = False,
        make_face: bool = False,
        parameterization: float = 1.0,
        placement: Mapping[str, Any] | None = None,
        label: str = "",
    ) -> DomainValue:
        """Create one editable interpolated native Draft B-spline."""

        clean_points = _points("bspline", points, minimum=3)
        if not isinstance(closed, bool) or not isinstance(make_face, bool):
            raise _error("bspline", "closed/make_face", "must be booleans")
        if make_face and not closed:
            raise _error("bspline", "make_face", "requires closed=True")
        clean_parameterization = _number(
            "bspline",
            "parameterization",
            parameterization,
            minimum=0.0,
            maximum=1.0,
        )
        return self._value(
            "bspline",
            "bspline",
            clean_points,
            closed=closed,
            make_face=make_face,
            parameterization=clean_parameterization,
            placement=_placement("bspline", placement),
            label=_label("bspline", label),
            graph_id=self._graph_id(),
        )

    def array(
        self,
        source: DomainValue | Mapping[str, Any],
        *,
        kind: str = "orthogonal",
        interval_x: Sequence[float] = (10.0, 0.0, 0.0),
        interval_y: Sequence[float] = (0.0, 10.0, 0.0),
        interval_z: Sequence[float] = (0.0, 0.0, 10.0),
        interval_axis: Sequence[float] = (0.0, 0.0, 0.0),
        count_x: int = 2,
        count_y: int = 2,
        count_z: int = 1,
        count: int = 6,
        total_angle_degrees: float = 360.0,
        center: Sequence[float] = (0.0, 0.0, 0.0),
        axis: Sequence[float] = (0.0, 0.0, 1.0),
        radial_distance: float = 100.0,
        tangential_distance: float = 50.0,
        number_circles: int = 3,
        symmetry: int = 1,
        use_link: bool = True,
        fuse: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Create one editable native orthogonal, polar, or circular Draft array."""

        if isinstance(source, DomainValue):
            if source.domain != self.domain or source.output_type not in _SHAPE_OUTPUT_TYPES:
                raise _error(
                    "array",
                    "source",
                    "must be a shape-producing value from this Draft API",
                )
            clean_source: Any = source
        else:
            clean_source = _reference("array", source)
        clean_kind = str(kind or "").strip().lower()
        if clean_kind not in {"orthogonal", "polar", "circular"}:
            raise _error(
                "array",
                "kind",
                "must be 'orthogonal', 'polar', or 'circular'",
                kind,
            )
        if not isinstance(use_link, bool) or not isinstance(fuse, bool):
            raise _error("array", "use_link/fuse", "must be booleans")
        if use_link and fuse:
            raise _error("array", "fuse", "cannot be true when use_link=True")
        clean_count_x = _integer(
            "array", "count_x", count_x, minimum=1, maximum=_MAX_ARRAY_ELEMENTS
        )
        clean_count_y = _integer(
            "array", "count_y", count_y, minimum=1, maximum=_MAX_ARRAY_ELEMENTS
        )
        clean_count_z = _integer(
            "array", "count_z", count_z, minimum=1, maximum=_MAX_ARRAY_ELEMENTS
        )
        clean_count = _integer(
            "array", "count", count, minimum=2, maximum=_MAX_ARRAY_ELEMENTS
        )
        clean_number_circles = _integer(
            "array",
            "number_circles",
            number_circles,
            minimum=2,
            maximum=_MAX_ARRAY_ELEMENTS,
        )
        clean_symmetry = _integer(
            "array",
            "symmetry",
            symmetry,
            minimum=1,
            maximum=_MAX_ARRAY_ELEMENTS,
        )
        orthogonal_count = clean_count_x * clean_count_y * clean_count_z
        if clean_kind == "orthogonal" and not 2 <= orthogonal_count <= _MAX_ARRAY_ELEMENTS:
            raise _error(
                "array",
                "count_x/count_y/count_z",
                f"product must be 2-{_MAX_ARRAY_ELEMENTS}",
                orthogonal_count,
            )
        clean_axis = _vector("array", "axis", axis)
        magnitude = math.sqrt(sum(value * value for value in clean_axis))
        if magnitude <= 1.0e-12:
            raise _error("array", "axis", "must be non-zero")
        clean_angle = _number(
            "array",
            "total_angle_degrees",
            total_angle_degrees,
            minimum=-360.0,
            maximum=360.0,
        )
        if clean_kind == "polar" and abs(clean_angle) <= 1.0e-12:
            raise _error("array", "total_angle_degrees", "must be non-zero for polar arrays")
        clean_radial_distance = _number(
            "array",
            "radial_distance",
            radial_distance,
            minimum=0.0,
            strict_minimum=True,
        )
        clean_tangential_distance = _number(
            "array",
            "tangential_distance",
            tangential_distance,
            minimum=0.0,
            strict_minimum=True,
        )
        if clean_kind == "circular":
            circular_count = 1
            for ring in range(1, clean_number_circles):
                raw_count = math.floor(
                    2.0
                    * math.pi
                    * ring
                    * clean_radial_distance
                    / clean_tangential_distance
                )
                circular_count += math.floor(raw_count / clean_symmetry) * clean_symmetry
            if not 2 <= circular_count <= _MAX_ARRAY_ELEMENTS:
                raise _error(
                    "array",
                    "radial_distance/tangential_distance/number_circles/symmetry",
                    f"produce {circular_count} elements; required range is 2-{_MAX_ARRAY_ELEMENTS}",
                )
        return self._value(
            "array",
            "array",
            clean_source,
            kind=clean_kind,
            interval_x=_vector("array", "interval_x", interval_x),
            interval_y=_vector("array", "interval_y", interval_y),
            interval_z=_vector("array", "interval_z", interval_z),
            interval_axis=_vector("array", "interval_axis", interval_axis),
            count_x=clean_count_x,
            count_y=clean_count_y,
            count_z=clean_count_z,
            count=clean_count,
            total_angle_degrees=clean_angle,
            center=_vector("array", "center", center),
            axis=tuple(value / magnitude for value in clean_axis),
            radial_distance=clean_radial_distance,
            tangential_distance=clean_tangential_distance,
            number_circles=clean_number_circles,
            symmetry=clean_symmetry,
            use_link=use_link,
            fuse=fuse,
            label=_label("array", label),
            graph_id=self._graph_id(),
        )

    def text(
        self,
        lines: str | Sequence[str],
        *,
        placement: Mapping[str, Any] | None = None,
        screen: bool = False,
        height: float = 2.0,
        line_spacing: float = 1.0,
        label: str = "",
    ) -> DomainValue:
        """Create one native Draft text object with bounded display settings."""

        if isinstance(lines, str):
            raw_lines = (lines,)
        elif isinstance(lines, (list, tuple)):
            raw_lines = tuple(lines)
        else:
            raise _error("text", "lines", "must be a string or array of strings", lines)
        if not 1 <= len(raw_lines) <= _MAX_TEXT_LINES:
            raise _error(
                "text",
                "lines",
                f"must contain 1-{_MAX_TEXT_LINES} strings",
            )
        if any(not isinstance(value, str) for value in raw_lines):
            raise _error("text", "lines", "must contain only strings")
        clean_lines = tuple(raw_lines)
        if any(not value for value in clean_lines):
            raise _error("text", "lines", "must not contain empty strings")
        if sum(len(value) for value in clean_lines) > _MAX_TEXT_CHARS:
            raise _error(
                "text",
                "lines",
                f"may contain at most {_MAX_TEXT_CHARS} total characters",
            )
        if not isinstance(screen, bool):
            raise _error("text", "screen", "must be boolean")
        return self._value(
            "text",
            "text",
            clean_lines,
            placement=_placement("text", placement),
            screen=screen,
            height=_number("text", "height", height, minimum=0.0, strict_minimum=True),
            line_spacing=_number(
                "text",
                "line_spacing",
                line_spacing,
                minimum=0.0,
                strict_minimum=True,
            ),
            label=_label("text", label),
            graph_id=self._graph_id(),
        )
