# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit immutable API for production Surface VibeScript programs."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
import math
import re
from typing import Any

from vibescript_domain_api import DomainValue


_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")
_TOPOLOGY_TYPES = frozenset({"vertex", "edge", "wire", "face", "shell", "solid"})
_CURVE_TYPES = frozenset({"edge", "wire"})
_SURFACE_TYPES = frozenset(
    {"face", "shell", "solid", "surface", "fill", "blend", "extension", "loft"}
)
_PUBLISHABLE_TYPES = (
    "surface",
    "face",
    "shell",
    "fill",
    "blend",
    "extension",
    "loft",
    "solid",
)
_CONTINUITIES = frozenset({"C0", "G1", "G2"})
_MAX_CURVE_POINTS = 4096
_MAX_GRID_AXIS = 128
_MAX_BOUNDARIES = 256
_MAX_CONSTRAINTS = 256


class SurfaceAPIError(ValueError):
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
    value: Any = None,
) -> SurfaceAPIError:
    suffix = "" if value is None else f"; received {value!r}"
    return SurfaceAPIError(
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
        relation = "greater than" if strict_minimum else "at least"
        raise _error(operation, parameter, f"must be {relation} {minimum:g}", value)
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


def _boolean(operation: str, parameter: str, value: Any) -> bool:
    if not isinstance(value, bool):
        raise _error(operation, parameter, "must be a boolean", value)
    return value


def _vector(operation: str, parameter: str, value: Any) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "must be [x, y, z]", value)
    return tuple(
        _number(operation, f"{parameter}[{index}]", item)
        for index, item in enumerate(value)
    )


def _nonzero_vector(
    operation: str, parameter: str, value: Any
) -> tuple[float, float, float]:
    result = _vector(operation, parameter, value)
    magnitude = math.sqrt(sum(item * item for item in result))
    if magnitude <= 1.0e-12:
        raise _error(operation, parameter, "must be non-zero", value)
    return tuple(item / magnitude for item in result)


def _label(operation: str, value: Any) -> str:
    if not isinstance(value, str) or len(value) > 256:
        raise _error(operation, "label", "must be a string of at most 256 characters")
    return value


def _points(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: int,
    maximum: int = _MAX_CURVE_POINTS,
) -> tuple[tuple[float, float, float], ...]:
    if not isinstance(value, (list, tuple)) or not minimum <= len(value) <= maximum:
        raise _error(
            operation,
            parameter,
            f"must contain {minimum}-{maximum} points",
        )
    result = tuple(
        _vector(operation, f"{parameter}[{index}]", point)
        for index, point in enumerate(value)
    )
    for index in range(1, len(result)):
        if result[index] == result[index - 1]:
            raise _error(
                operation,
                f"{parameter}[{index}]",
                "must differ from the preceding point",
                result[index],
            )
    return result


def _shape(
    operation: str,
    parameter: str,
    value: Any,
    *,
    allowed: Iterable[str],
) -> DomainValue:
    allowed_types = frozenset(allowed)
    if not isinstance(value, DomainValue) or value.domain != "surface":
        raise _error(
            operation,
            parameter,
            "must be a value returned by this Surface api",
            type(value).__name__,
        )
    if value.output_type not in allowed_types:
        raise _error(
            operation,
            parameter,
            f"must have type {sorted(allowed_types)}",
            value.output_type,
        )
    return value


def _shapes(
    operation: str,
    parameter: str,
    values: Any,
    *,
    allowed: Iterable[str],
    minimum: int,
    maximum: int,
) -> tuple[DomainValue, ...]:
    if not isinstance(values, (list, tuple)) or not minimum <= len(values) <= maximum:
        raise _error(
            operation,
            parameter,
            f"must contain {minimum}-{maximum} Surface api values",
        )
    return tuple(
        _shape(operation, f"{parameter}[{index}]", value, allowed=allowed)
        for index, value in enumerate(values)
    )


def _reference(operation: str, value: Any) -> dict[str, str]:
    if not isinstance(value, Mapping) or set(value) != {
        "document_uid",
        "object_name",
    }:
        raise _error(
            operation,
            "reference",
            "must contain exactly document_uid and object_name",
            value,
        )
    document_uid = str(value.get("document_uid") or "").strip()
    object_name = str(value.get("object_name") or "").strip()
    if not document_uid or not _NAME.fullmatch(object_name):
        raise _error(
            operation,
            "reference",
            "must identify one stable document object",
            value,
        )
    return {"document_uid": document_uid, "object_name": object_name}


def _continuity(operation: str, parameter: str, value: Any) -> str:
    clean = str(value or "").strip().upper()
    if clean not in _CONTINUITIES:
        raise _error(operation, parameter, "must be 'C0', 'G1', or 'G2'", value)
    return clean


class SurfaceDomainAPI:
    """Explicit surface-construction API injected into Surface source."""

    __slots__ = ()

    domain = "surface"
    exported_names = (
        "line",
        "circle",
        "bezier",
        "bspline",
        "wire",
        "from_object",
        "face",
        "surface",
        "boundary",
        "curve_constraint",
        "face_constraint",
        "point_constraint",
        "fill",
        "blend",
        "extend",
        "loft",
        "thicken",
        "shell",
    )

    def __init__(self, exports: Iterable[str], output_types: Iterable[str]) -> None:
        declared_exports = tuple(dict.fromkeys(str(item) for item in exports))
        if declared_exports != self.exported_names:
            raise RuntimeError(
                "Surface pack exports do not match the production runtime contract: "
                f"expected {self.exported_names!r}, received {declared_exports!r}."
            )
        declared_types = tuple(dict.fromkeys(str(item) for item in output_types))
        if declared_types != _PUBLISHABLE_TYPES:
            raise RuntimeError(
                "Surface pack output types do not match the production runtime contract: "
                f"expected {_PUBLISHABLE_TYPES!r}, received {declared_types!r}."
            )

    @staticmethod
    def _value(
        operation: str,
        output_type: str,
        *arguments: Any,
        **properties: Any,
    ) -> DomainValue:
        return DomainValue(
            domain="surface",
            operation=operation,
            output_type=output_type,
            arguments=tuple(arguments),
            properties=properties,
        )

    def line(
        self,
        start: Sequence[float],
        end: Sequence[float],
        *,
        label: str = "",
    ) -> DomainValue:
        """Create one straight boundary edge between two distinct 3D points."""

        clean_start = _vector("line", "start", start)
        clean_end = _vector("line", "end", end)
        if clean_start == clean_end:
            raise _error("line", "end", "must differ from start", end)
        return self._value(
            "line", "edge", clean_start, clean_end, label=_label("line", label)
        )

    def circle(
        self,
        center: Sequence[float],
        radius: float,
        *,
        normal: Sequence[float] = (0.0, 0.0, 1.0),
        start_angle: float = 0.0,
        end_angle: float = 360.0,
        label: str = "",
    ) -> DomainValue:
        """Create one circular boundary edge or arc in an arbitrary plane."""

        start = _number("circle", "start_angle", start_angle, minimum=-360.0, maximum=360.0)
        end = _number("circle", "end_angle", end_angle, minimum=-360.0, maximum=360.0)
        if math.isclose(start, end, rel_tol=0.0, abs_tol=1.0e-12):
            end = start + 360.0
        if not 0.0 < end - start <= 360.0:
            raise _error(
                "circle",
                "end_angle",
                "must be greater than start_angle by at most 360 degrees",
                end_angle,
            )
        return self._value(
            "circle",
            "edge",
            _vector("circle", "center", center),
            _number("circle", "radius", radius, minimum=0.0, strict_minimum=True),
            normal=_nonzero_vector("circle", "normal", normal),
            start_angle=start,
            end_angle=end,
            label=_label("circle", label),
        )

    def bezier(
        self,
        poles: Sequence[Sequence[float]],
        *,
        label: str = "",
    ) -> DomainValue:
        """Create one exact Bezier boundary edge from 2-64 control poles."""

        return self._value(
            "bezier",
            "edge",
            _points("bezier", "poles", poles, minimum=2, maximum=64),
            label=_label("bezier", label),
        )

    def bspline(
        self,
        points: Sequence[Sequence[float]],
        *,
        periodic: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Create one interpolating B-spline boundary edge through 3-4096 points."""

        return self._value(
            "bspline",
            "edge",
            _points("bspline", "points", points, minimum=3),
            periodic=_boolean("bspline", "periodic", periodic),
            label=_label("bspline", label),
        )

    def wire(
        self,
        items: Sequence[Any],
        *,
        closed: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Join ordered edges/wires or ordered 3D points into one boundary wire."""

        if not isinstance(items, (list, tuple)) or not 1 <= len(items) <= _MAX_CURVE_POINTS:
            raise _error("wire", "items", f"must contain 1-{_MAX_CURVE_POINTS} values")
        if all(isinstance(item, DomainValue) for item in items):
            clean_items: Any = _shapes(
                "wire",
                "items",
                items,
                allowed=_CURVE_TYPES,
                minimum=1,
                maximum=_MAX_CURVE_POINTS,
            )
            mode = "curves"
        else:
            clean_items = _points("wire", "items", items, minimum=2)
            mode = "points"
        return self._value(
            "wire",
            "wire",
            clean_items,
            mode=mode,
            closed=_boolean("wire", "closed", closed),
            label=_label("wire", label),
        )

    def from_object(
        self,
        reference: Mapping[str, Any],
        output_type: str,
        *,
        subelement: str | None = None,
        interface: str | None = None,
        label: str = "",
    ) -> DomainValue:
        """Use a revision-bound whole Shape, exact subelement, or semantic interface."""

        clean_type = str(output_type or "").strip().lower()
        if clean_type not in _TOPOLOGY_TYPES:
            raise _error(
                "from_object",
                "output_type",
                f"must be one of {sorted(_TOPOLOGY_TYPES)}",
                output_type,
            )
        if subelement is not None and interface is not None:
            raise _error(
                "from_object", "subelement/interface", "are mutually exclusive"
            )
        if subelement is not None:
            element = str(subelement or "").strip()
            prefixes = {
                "vertex": "Vertex",
                "edge": "Edge",
                "wire": "Wire",
                "face": "Face",
                "shell": "Shell",
                "solid": "Solid",
            }
            if not re.fullmatch(rf"{prefixes[clean_type]}[1-9][0-9]*", element):
                raise _error(
                    "from_object",
                    "subelement",
                    f"must be a 1-based {prefixes[clean_type]}N name",
                    subelement,
                )
            selection = {"type": "exact_subelement", "subelement": element}
        elif interface is not None:
            name = str(interface or "").strip()
            if not _NAME.fullmatch(name):
                raise _error(
                    "from_object",
                    "interface",
                    "must be a stable published interface name",
                    interface,
                )
            selection = {"type": "published_interface", "interface_name": name}
        else:
            selection = {"type": "whole_shape"}
        return self._value(
            "from_object",
            clean_type,
            _reference("from_object", reference),
            selection=selection,
            label=_label("from_object", label),
        )

    def face(
        self,
        outer: DomainValue,
        *,
        holes: Sequence[DomainValue] = (),
        label: str = "",
    ) -> DomainValue:
        """Create one exact planar face from a closed outer wire and optional holes."""

        return self._value(
            "face",
            "face",
            _shape("face", "outer", outer, allowed={"wire"}),
            holes=_shapes(
                "face",
                "holes",
                holes,
                allowed={"wire"},
                minimum=0,
                maximum=256,
            ),
            label=_label("face", label),
        )

    def surface(
        self,
        points: Sequence[Sequence[Sequence[float]]],
        *,
        mode: str = "interpolate",
        degree_min: int = 3,
        degree_max: int = 5,
        continuity: str = "C2",
        tolerance: float = 1.0e-3,
        parametrization: str = "chord_length",
        smoothing: Sequence[float] = (1.0, 1.0, 1.0),
        label: str = "",
    ) -> DomainValue:
        """Interpolate or approximate one bounded B-spline surface through a point grid."""

        operation = "surface"
        if not isinstance(points, (list, tuple)) or not 2 <= len(points) <= _MAX_GRID_AXIS:
            raise _error(operation, "points", f"must contain 2-{_MAX_GRID_AXIS} rows")
        column_count = len(points[0]) if isinstance(points[0], (list, tuple)) else 0
        if not 2 <= column_count <= _MAX_GRID_AXIS:
            raise _error(
                operation, "points[0]", f"must contain 2-{_MAX_GRID_AXIS} columns"
            )
        clean_rows = []
        for row_index, row in enumerate(points):
            if not isinstance(row, (list, tuple)) or len(row) != column_count:
                raise _error(
                    operation,
                    f"points[{row_index}]",
                    f"must contain exactly {column_count} points",
                )
            clean_rows.append(
                tuple(
                    _vector(operation, f"points[{row_index}][{column_index}]", point)
                    for column_index, point in enumerate(row)
                )
            )
        clean_mode = str(mode or "").strip().lower()
        if clean_mode not in {"interpolate", "approximate"}:
            raise _error(operation, "mode", "must be 'interpolate' or 'approximate'", mode)
        clean_min = _integer(operation, "degree_min", degree_min, minimum=1, maximum=25)
        clean_max = _integer(operation, "degree_max", degree_max, minimum=1, maximum=25)
        if clean_min > clean_max:
            raise _error(operation, "degree_min", "must not exceed degree_max", degree_min)
        continuity_name = str(continuity or "").strip().upper()
        continuity_name = {"G1": "C1", "G2": "C2"}.get(
            continuity_name, continuity_name
        )
        if continuity_name not in {"C0", "C1", "C2"}:
            raise _error(
                operation,
                "continuity",
                "must be 'C0', 'C1', or 'C2'",
                continuity,
            )
        clean_parametrization = str(parametrization or "").strip().lower()
        if clean_parametrization not in {"uniform", "centripetal", "chord_length"}:
            raise _error(
                operation,
                "parametrization",
                "must be 'uniform', 'centripetal', or 'chord_length'",
                parametrization,
            )
        if not isinstance(smoothing, (list, tuple)) or len(smoothing) != 3:
            raise _error(operation, "smoothing", "must be [length, curvature, torsion]")
        clean_smoothing = tuple(
            _number(operation, f"smoothing[{index}]", value, minimum=0.0)
            for index, value in enumerate(smoothing)
        )
        return self._value(
            operation,
            "surface",
            tuple(clean_rows),
            mode=clean_mode,
            degree_min=clean_min,
            degree_max=clean_max,
            continuity=continuity_name,
            tolerance=_number(
                operation, "tolerance", tolerance, minimum=0.0, strict_minimum=True
            ),
            parametrization=clean_parametrization,
            smoothing=clean_smoothing,
            label=_label(operation, label),
        )

    def boundary(
        self,
        curve: DomainValue,
        *,
        continuity: str = "C0",
        support_face: DomainValue | None = None,
    ) -> DomainValue:
        """Declare one ordered filling boundary with optional G1/G2 support face."""

        support = (
            None
            if support_face is None
            else _shape("boundary", "support_face", support_face, allowed={"face"})
        )
        clean_continuity = _continuity("boundary", "continuity", continuity)
        if support is None and clean_continuity != "C0":
            raise _error(
                "boundary", "continuity", "G1/G2 requires support_face", continuity
            )
        return self._value(
            "boundary",
            "boundary_constraint",
            _shape("boundary", "curve", curve, allowed=_CURVE_TYPES),
            continuity=clean_continuity,
            support_face=support,
        )

    def curve_constraint(
        self,
        curve: DomainValue,
        *,
        continuity: str = "C0",
        support_face: DomainValue | None = None,
    ) -> DomainValue:
        """Declare one non-boundary curve constraint for a variational filling."""

        support = (
            None
            if support_face is None
            else _shape(
                "curve_constraint", "support_face", support_face, allowed={"face"}
            )
        )
        clean_continuity = _continuity(
            "curve_constraint", "continuity", continuity
        )
        if support is None and clean_continuity != "C0":
            raise _error(
                "curve_constraint", "continuity", "G1/G2 requires support_face", continuity
            )
        return self._value(
            "curve_constraint",
            "curve_constraint",
            _shape("curve_constraint", "curve", curve, allowed=_CURVE_TYPES),
            continuity=clean_continuity,
            support_face=support,
        )

    def face_constraint(
        self,
        face: DomainValue,
        *,
        continuity: str = "C0",
    ) -> DomainValue:
        """Declare one free face constraint for a variational filling."""

        return self._value(
            "face_constraint",
            "face_constraint",
            _shape("face_constraint", "face", face, allowed={"face"}),
            continuity=_continuity("face_constraint", "continuity", continuity),
        )

    def point_constraint(
        self,
        point: DomainValue | Sequence[float],
    ) -> DomainValue:
        """Declare one literal 3D point or referenced vertex filling constraint."""

        clean: Any
        if isinstance(point, DomainValue):
            clean = _shape("point_constraint", "point", point, allowed={"vertex"})
        else:
            clean = _vector("point_constraint", "point", point)
        return self._value("point_constraint", "point_constraint", clean)

    def fill(
        self,
        boundaries: Sequence[DomainValue],
        *,
        curve_constraints: Sequence[DomainValue] = (),
        face_constraints: Sequence[DomainValue] = (),
        point_constraints: Sequence[DomainValue] = (),
        initial_face: DomainValue | None = None,
        degree: int = 3,
        points_on_curve: int = 15,
        iterations: int = 2,
        anisotropy: bool = False,
        tolerance_2d: float = 1.0e-5,
        tolerance_3d: float = 1.0e-4,
        angular_tolerance: float = 0.01,
        curvature_tolerance: float = 0.1,
        maximum_degree: int = 8,
        maximum_segments: int = 9,
        label: str = "",
    ) -> DomainValue:
        """Create one native variational filling with complete bounded solver controls."""

        operation = "fill"
        clean_boundaries = _shapes(
            operation,
            "boundaries",
            boundaries,
            allowed={"boundary_constraint"},
            minimum=1,
            maximum=_MAX_BOUNDARIES,
        )
        clean_curves = _shapes(
            operation,
            "curve_constraints",
            curve_constraints,
            allowed={"curve_constraint"},
            minimum=0,
            maximum=_MAX_CONSTRAINTS,
        )
        clean_faces = _shapes(
            operation,
            "face_constraints",
            face_constraints,
            allowed={"face_constraint"},
            minimum=0,
            maximum=_MAX_CONSTRAINTS,
        )
        clean_points = _shapes(
            operation,
            "point_constraints",
            point_constraints,
            allowed={"point_constraint"},
            minimum=0,
            maximum=_MAX_CONSTRAINTS,
        )
        clean_degree = _integer(operation, "degree", degree, minimum=2, maximum=25)
        clean_max_degree = _integer(
            operation, "maximum_degree", maximum_degree, minimum=2, maximum=25
        )
        if clean_degree > clean_max_degree:
            raise _error(operation, "degree", "must not exceed maximum_degree", degree)
        return self._value(
            operation,
            "fill",
            clean_boundaries,
            curve_constraints=clean_curves,
            face_constraints=clean_faces,
            point_constraints=clean_points,
            initial_face=(
                None
                if initial_face is None
                else _shape(operation, "initial_face", initial_face, allowed={"face"})
            ),
            degree=clean_degree,
            points_on_curve=_integer(
                operation, "points_on_curve", points_on_curve, minimum=2, maximum=1000
            ),
            iterations=_integer(operation, "iterations", iterations, minimum=1, maximum=1000),
            anisotropy=_boolean(operation, "anisotropy", anisotropy),
            tolerance_2d=_number(
                operation, "tolerance_2d", tolerance_2d, minimum=0.0, strict_minimum=True
            ),
            tolerance_3d=_number(
                operation, "tolerance_3d", tolerance_3d, minimum=0.0, strict_minimum=True
            ),
            angular_tolerance=_number(
                operation,
                "angular_tolerance",
                angular_tolerance,
                minimum=0.0,
                strict_minimum=True,
            ),
            curvature_tolerance=_number(
                operation,
                "curvature_tolerance",
                curvature_tolerance,
                minimum=0.0,
                strict_minimum=True,
            ),
            maximum_degree=clean_max_degree,
            maximum_segments=_integer(
                operation, "maximum_segments", maximum_segments, minimum=1, maximum=10000
            ),
            label=_label(operation, label),
        )

    def blend(
        self,
        boundaries: Sequence[DomainValue],
        *,
        style: str = "curved",
        reversed: Sequence[bool] = (),
        label: str = "",
    ) -> DomainValue:
        """Blend two to four consecutive boundary curves with a native fill style."""

        operation = "blend"
        clean_boundaries = _shapes(
            operation,
            "boundaries",
            boundaries,
            allowed=_CURVE_TYPES,
            minimum=2,
            maximum=4,
        )
        clean_style = str(style or "").strip().lower()
        if clean_style not in {"stretched", "coons", "curved"}:
            raise _error(
                operation,
                "style",
                "must be 'stretched', 'coons', or 'curved'",
                style,
            )
        if reversed:
            if not isinstance(reversed, (list, tuple)) or len(reversed) != len(clean_boundaries):
                raise _error(
                    operation,
                    "reversed",
                    "must contain one boolean per boundary",
                    reversed,
                )
            clean_reversed = tuple(
                _boolean(operation, f"reversed[{index}]", value)
                for index, value in enumerate(reversed)
            )
        else:
            clean_reversed = tuple(False for _item in clean_boundaries)
        return self._value(
            operation,
            "blend",
            clean_boundaries,
            style=clean_style,
            reversed=clean_reversed,
            label=_label(operation, label),
        )

    def extend(
        self,
        face: DomainValue,
        *,
        u_negative: float = 0.05,
        u_positive: float = 0.05,
        v_negative: float = 0.05,
        v_positive: float = 0.05,
        tolerance: float = 0.1,
        samples_u: int = 32,
        samples_v: int = 32,
        label: str = "",
    ) -> DomainValue:
        """Extend one face in all four parametric directions using native sampling."""

        operation = "extend"
        return self._value(
            operation,
            "extension",
            _shape(operation, "face", face, allowed={"face", "surface", "fill", "blend", "extension"}),
            u_negative=_number(operation, "u_negative", u_negative, minimum=-0.5, maximum=10.0),
            u_positive=_number(operation, "u_positive", u_positive, minimum=-0.5, maximum=10.0),
            v_negative=_number(operation, "v_negative", v_negative, minimum=-0.5, maximum=10.0),
            v_positive=_number(operation, "v_positive", v_positive, minimum=-0.5, maximum=10.0),
            tolerance=_number(
                operation, "tolerance", tolerance, minimum=0.0, maximum=10.0, strict_minimum=True
            ),
            samples_u=_integer(operation, "samples_u", samples_u, minimum=2, maximum=512),
            samples_v=_integer(operation, "samples_v", samples_v, minimum=2, maximum=512),
            label=_label(operation, label),
        )

    def loft(
        self,
        sections: Sequence[DomainValue],
        *,
        solid: bool = False,
        ruled: bool = False,
        closed: bool = False,
        max_degree: int = 5,
        label: str = "",
    ) -> DomainValue:
        """Loft through ordered edge/wire sections as a face, shell, or solid."""

        operation = "loft"
        clean_solid = _boolean(operation, "solid", solid)
        return self._value(
            operation,
            "solid" if clean_solid else "loft",
            _shapes(
                operation,
                "sections",
                sections,
                allowed=_CURVE_TYPES,
                minimum=2,
                maximum=256,
            ),
            solid=clean_solid,
            ruled=_boolean(operation, "ruled", ruled),
            closed=_boolean(operation, "closed", closed),
            max_degree=_integer(operation, "max_degree", max_degree, minimum=1, maximum=25),
            label=_label(operation, label),
        )

    def thicken(
        self,
        shape: DomainValue,
        thickness: float,
        *,
        remove_faces: Sequence[int] = (),
        tolerance: float = 1.0e-7,
        join: str = "arc",
        label: str = "",
    ) -> DomainValue:
        """Thicken a face/shell or hollow a solid into one validated solid."""

        operation = "thicken"
        if not isinstance(remove_faces, (list, tuple)) or len(remove_faces) > 256:
            raise _error(operation, "remove_faces", "must contain at most 256 indices")
        clean_indices = []
        seen = set()
        for index, value in enumerate(remove_faces):
            clean = _integer(
                operation, f"remove_faces[{index}]", value, minimum=1, maximum=1_000_000
            )
            if clean in seen:
                raise _error(operation, "remove_faces", "must not contain duplicates", value)
            seen.add(clean)
            clean_indices.append(clean)
        clean_join = str(join or "").strip().lower()
        if clean_join not in {"arc", "tangent", "intersection"}:
            raise _error(
                operation,
                "join",
                "must be 'arc', 'tangent', or 'intersection'",
                join,
            )
        clean_thickness = _number(operation, "thickness", thickness)
        if abs(clean_thickness) <= 1.0e-12:
            raise _error(operation, "thickness", "must be non-zero", thickness)
        return self._value(
            operation,
            "solid",
            _shape(operation, "shape", shape, allowed=_SURFACE_TYPES),
            clean_thickness,
            remove_faces=tuple(clean_indices),
            tolerance=_number(
                operation, "tolerance", tolerance, minimum=0.0, strict_minimum=True
            ),
            join=clean_join,
            label=_label(operation, label),
        )

    def shell(
        self,
        faces: Sequence[DomainValue],
        *,
        make_solid: bool = False,
        tolerance: float = 1.0e-7,
        cut_free_edges: bool = False,
        nonmanifold: bool = False,
        label: str = "",
    ) -> DomainValue:
        """Sew touching faces/shells and optionally promote one closed shell to a solid."""

        operation = "shell"
        clean_solid = _boolean(operation, "make_solid", make_solid)
        return self._value(
            operation,
            "solid" if clean_solid else "shell",
            _shapes(
                operation,
                "faces",
                faces,
                allowed={"face", "shell", "surface", "fill", "blend", "extension", "loft"},
                minimum=1,
                maximum=1024,
            ),
            make_solid=clean_solid,
            tolerance=_number(
                operation, "tolerance", tolerance, minimum=0.0, strict_minimum=True
            ),
            cut_free_edges=_boolean(operation, "cut_free_edges", cut_free_edges),
            nonmanifold=_boolean(operation, "nonmanifold", nonmanifold),
            label=_label(operation, label),
        )
