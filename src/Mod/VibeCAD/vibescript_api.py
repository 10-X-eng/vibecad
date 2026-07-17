# SPDX-License-Identifier: LGPL-2.1-or-later

"""VibeScript authoring library for source-backed native PartDesign geometry.

This module is injected into the scope of VibeScript model sources. It wraps
the raw FreeCAD document API with three layers that remove its sharp edges:

- ``SketchBuilder``: geometry plus constraints as declarative specs. Polyline
  chains are auto-coincident and dimensions carry names, so scripts never
  spell raw ``Sketcher.Constraint("Coincident", 0, 2, 1, 1)`` index tuples.
- Edge selectors: ``edges(shape)`` filters edges by geometric predicates
  (direction, length, position) immediately after the creating feature, so
  scripts never hardcode ``"Edge7"`` style topological names.
- ``Params``: validated named parameters that can be bound to an
  ``App::VarSet`` inside the current execution document and referenced through
  its expression engine. VibeCAD separately persists the accepted source and
  parameter values for deterministic regeneration.

All FreeCAD imports are deferred into methods that touch the current execution
document; every pure-logic piece is importable and testable without FreeCAD.
"""

from __future__ import annotations

import keyword
import math
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass
from numbers import Real
from typing import Any, Callable, Union

__all__ = [
    "AppliedSketch",
    "ArcSpec",
    "CircleSpec",
    "ConstraintSpec",
    "EdgeFacts",
    "EdgeQuery",
    "FeatureError",
    "GeoArg",
    "GeometryRef",
    "LineSpec",
    "ParameterError",
    "Params",
    "PointRef",
    "RectangleRef",
    "SelectorError",
    "SketchBuilder",
    "SketchBuilderError",
    "SketchValidationError",
    "VibeScriptError",
    "assert_fully_constrained",
    "edge_facts_from_shape",
    "edges",
    "fillet",
    "groove",
    "is_parallel",
    "is_perpendicular",
    "loft",
    "mirror",
    "new_body",
    "new_sketch",
    "pad",
    "pocket",
    "polar_pattern",
    "resolve_constraint_args",
    "revolve",
]


class VibeScriptError(Exception):
    """Base error for all VibeScript authoring failures."""


class ParameterError(VibeScriptError):
    """Invalid parameter name or value."""


class SketchBuilderError(VibeScriptError):
    """Invalid geometry or constraint request on a SketchBuilder."""


class SketchValidationError(VibeScriptError):
    """A sketch failed solve or is not fully constrained."""


class SelectorError(VibeScriptError):
    """An edge/face selector matched the wrong number of elements."""


class FeatureError(VibeScriptError):
    """Invalid arguments for a PartDesign feature helper."""


# --------------------------------------------------------------------------
# Numeric / point validation helpers (pure)
# --------------------------------------------------------------------------


def _finite(name: str, value: Any) -> float:
    if isinstance(value, bool) or not isinstance(value, Real):
        raise ParameterError(f"{name} must be a number, got {value!r}.")
    result = float(value)
    if not math.isfinite(result):
        raise ParameterError(f"{name} must be finite, got {value!r}.")
    return result


def _positive(name: str, value: Any) -> float:
    result = _finite(name, value)
    if result <= 0.0:
        raise ParameterError(f"{name} must be positive, got {result:g}.")
    return result


def _point2(name: str, value: Any) -> tuple[float, float]:
    if not isinstance(value, (tuple, list)) or len(value) != 2:
        raise SketchBuilderError(f"{name} must be a 2-tuple of numbers, got {value!r}.")
    return (_finite(f"{name}[0]", value[0]), _finite(f"{name}[1]", value[1]))


def _vector3(value: Any) -> tuple[float, float, float] | None:
    """Best-effort conversion of a FreeCAD Vector-like object to a tuple."""
    if value is None:
        return None
    if isinstance(value, (tuple, list)) and len(value) == 3:
        return (float(value[0]), float(value[1]), float(value[2]))
    x = getattr(value, "x", None)
    y = getattr(value, "y", None)
    z = getattr(value, "z", None)
    if x is None or y is None or z is None:
        return None
    return (float(x), float(y), float(z))


# --------------------------------------------------------------------------
# Parameters
# --------------------------------------------------------------------------


class Params(Mapping[str, float]):
    """Validated named model parameters.

    Behaves as a read-only mapping with attribute access. ``bind`` creates or
    updates an ``App::VarSet`` in the current execution document so features in
    that run can reference named expressions. VibeCAD persists the accepted
    numeric values separately and regenerates the source when they change.
    """

    def __init__(self, *, _binding_name: str = "Params", **values: Any) -> None:
        parsed: dict[str, float] = {}
        for name, value in values.items():
            self._check_name(name)
            parsed[name] = _finite(f"parameter {name!r}", value)
        object.__setattr__(self, "_values", parsed)
        object.__setattr__(self, "_binding_name", str(_binding_name or "Params"))

    @staticmethod
    def _check_name(name: str) -> None:
        if not name.isidentifier() or keyword.iskeyword(name):
            raise ParameterError(
                f"Parameter name {name!r} must be a valid Python identifier."
            )
        if name.startswith("_"):
            raise ParameterError(
                f"Parameter name {name!r} must not start with an underscore."
            )

    def __getitem__(self, name: str) -> float:
        try:
            return self._values[name]
        except KeyError:
            raise ParameterError(
                f"Unknown parameter {name!r}; defined: {sorted(self._values)}."
            ) from None

    def __getattr__(self, name: str) -> float:
        if name.startswith("_"):
            raise AttributeError(name)
        return self[name]

    def __setattr__(self, name: str, value: Any) -> None:
        raise ParameterError(
            "Params is immutable; construct a new Params instead of "
            f"assigning {name!r}."
        )

    def __iter__(self) -> Iterator[str]:
        return iter(self._values)

    def __len__(self) -> int:
        return len(self._values)

    def __repr__(self) -> str:
        inner = ", ".join(f"{k}={v:g}" for k, v in self._values.items())
        return f"Params({inner})"

    def expression(self, name: str, *, object_name: str | None = None) -> str:
        """Expression-engine reference for a bound parameter."""
        self[name]  # validate the parameter exists
        return f"{object_name or self._binding_name}.{name}"

    def bind(self, document: Any, *, object_name: str | None = None) -> Any:
        """Bind parameters to an ``App::VarSet`` in the execution document.

        Creates the VarSet if missing, adds one ``App::PropertyFloat`` per
        parameter, and returns the VarSet object.
        """
        resolved_name = str(object_name or self._binding_name)
        varset = document.getObject(resolved_name)
        if varset is None:
            varset = document.addObject("App::VarSet", resolved_name)
        for name, value in self._values.items():
            if not hasattr(varset, name):
                varset.addProperty("App::PropertyFloat", name, "Parameters")
            setattr(varset, name, value)
        return varset


# --------------------------------------------------------------------------
# Constraint specs (pure)
# --------------------------------------------------------------------------

START = 1
END = 2
CENTER = 3


@dataclass(frozen=True)
class PointRef:
    """A point on a builder-local geometry: (geometry index, vertex pos)."""

    geometry: int
    point: int


@dataclass(frozen=True)
class GeometryRef:
    """Handle for one geometry element created on a SketchBuilder."""

    index: int

    def start(self) -> PointRef:
        return PointRef(self.index, START)

    def end(self) -> PointRef:
        return PointRef(self.index, END)

    def center(self) -> PointRef:
        return PointRef(self.index, CENTER)


@dataclass(frozen=True)
class GeoArg:
    """Marks a constraint argument as a builder-local geometry index."""

    index: int


ConstraintArg = Union[GeoArg, int, float]


@dataclass(frozen=True)
class ConstraintSpec:
    """One Sketcher constraint, geometry indices kept symbolic until apply."""

    kind: str
    args: tuple[ConstraintArg, ...]
    name: str | None = None


def resolve_constraint_args(
    spec: ConstraintSpec, offset: int
) -> tuple[int | float, ...]:
    """Resolve builder-local geometry indices against a sketch offset."""
    resolved: list[int | float] = []
    for arg in spec.args:
        if isinstance(arg, GeoArg):
            resolved.append(arg.index + offset)
        else:
            resolved.append(arg)
    return tuple(resolved)


@dataclass(frozen=True)
class LineSpec:
    start: tuple[float, float]
    end: tuple[float, float]


@dataclass(frozen=True)
class CircleSpec:
    center: tuple[float, float]
    radius: float


@dataclass(frozen=True)
class ArcSpec:
    """Circular arc, counter-clockwise from ``start_deg`` to ``end_deg``."""

    center: tuple[float, float]
    radius: float
    start_deg: float
    end_deg: float


GeometrySpec = Union[LineSpec, CircleSpec, ArcSpec]


@dataclass(frozen=True)
class RectangleRef:
    """Handles for the four lines of a builder rectangle."""

    bottom: GeometryRef
    right: GeometryRef
    top: GeometryRef
    left: GeometryRef

    @property
    def lines(self) -> tuple[GeometryRef, ...]:
        return (self.bottom, self.right, self.top, self.left)


@dataclass
class AppliedSketch:
    """Result of ``SketchBuilder.apply``: where the specs landed."""

    sketch: Any
    geometry_offset: int
    geometry_count: int
    constraint_offset: int
    constraint_count: int

    def geometry_index(self, ref: GeometryRef | int) -> int:
        local = ref.index if isinstance(ref, GeometryRef) else int(ref)
        return self.geometry_offset + local


class SketchBuilder:
    """Declarative sketch authoring: geometry plus constraints as specs.

    All construction is pure; ``apply`` is the only method that touches a
    live Sketcher object. Polylines and rectangles chain their segments with
    automatic coincident constraints so scripts never write raw index tuples.
    """

    def __init__(self) -> None:
        self._geometry: list[GeometrySpec] = []
        self._constraints: list[ConstraintSpec] = []

    # -- introspection (pure) ---------------------------------------------

    @property
    def geometry(self) -> tuple[GeometrySpec, ...]:
        return tuple(self._geometry)

    @property
    def constraints(self) -> tuple[ConstraintSpec, ...]:
        return tuple(self._constraints)

    # -- geometry ----------------------------------------------------------

    def line(self, start: Any, end: Any) -> GeometryRef:
        p0 = _point2("start", start)
        p1 = _point2("end", end)
        if p0 == p1:
            raise SketchBuilderError(f"Line start and end must differ, both were {p0}.")
        self._geometry.append(LineSpec(p0, p1))
        return GeometryRef(len(self._geometry) - 1)

    def polyline(
        self, points: Iterable[Any], *, closed: bool = False
    ) -> list[GeometryRef]:
        """Chain of lines with automatic coincident constraints."""
        parsed = [_point2(f"points[{i}]", p) for i, p in enumerate(points)]
        if len(parsed) < 2:
            raise SketchBuilderError(
                f"polyline needs at least 2 points, got {len(parsed)}."
            )
        if closed and len(parsed) < 3:
            raise SketchBuilderError(
                f"A closed polyline needs at least 3 points, got {len(parsed)}."
            )
        pairs = list(zip(parsed, parsed[1:]))
        if closed:
            pairs.append((parsed[-1], parsed[0]))
        refs = [self.line(a, b) for a, b in pairs]
        for previous, current in zip(refs, refs[1:]):
            self.coincident(previous.end(), current.start())
        if closed:
            self.coincident(refs[-1].end(), refs[0].start())
        return refs

    def rectangle(
        self,
        width: Any,
        height: Any,
        *,
        center: Any = (0.0, 0.0),
        width_name: str | None = None,
        height_name: str | None = None,
    ) -> RectangleRef:
        """Fully constrained axis-aligned rectangle.

        Sixteen degrees of freedom are consumed by construction: four
        coincident corners, two horizontal and two vertical lines, an
        anchored corner, and the two named dimensions.
        """
        w = _positive("width", width)
        h = _positive("height", height)
        cx, cy = _point2("center", center)
        x0, x1 = cx - w / 2.0, cx + w / 2.0
        y0, y1 = cy - h / 2.0, cy + h / 2.0
        bottom, right, top, left = self.polyline(
            [(x0, y0), (x1, y0), (x1, y1), (x0, y1)], closed=True
        )
        self.horizontal(bottom)
        self.horizontal(top)
        self.vertical(right)
        self.vertical(left)
        self.distance_x(bottom.start(), x0)
        self.distance_y(bottom.start(), y0)
        self.distance(bottom, w, name=width_name)
        self.distance(right, h, name=height_name)
        return RectangleRef(bottom=bottom, right=right, top=top, left=left)

    def circle(
        self,
        center: Any,
        radius: Any,
        *,
        radius_name: str | None = None,
    ) -> GeometryRef:
        """Fully constrained circle: radius plus fixed center."""
        c = _point2("center", center)
        r = _positive("radius", radius)
        self._geometry.append(CircleSpec(c, r))
        ref = GeometryRef(len(self._geometry) - 1)
        self.radius(ref, r, name=radius_name)
        self.distance_x(ref.center(), c[0])
        self.distance_y(ref.center(), c[1])
        return ref

    def arc(
        self,
        center: Any,
        radius: Any,
        start_deg: Any,
        end_deg: Any,
    ) -> GeometryRef:
        """Circular arc, counter-clockwise from ``start_deg`` to ``end_deg``.

        The arc is fixed in place with a ``Block`` constraint, so it consumes
        no degrees of freedom. Use it for computed geometry (blade profiles,
        cam curves) where the coordinates already encode the design intent.
        """
        c = _point2("center", center)
        r = _positive("radius", radius)
        start = _finite("start_deg", start_deg)
        end = _finite("end_deg", end_deg)
        sweep = (end - start) % 360.0
        if sweep < 1e-9 or sweep > 360.0 - 1e-9:
            raise SketchBuilderError(
                f"arc sweep must be strictly between 0 and 360 degrees; "
                f"start {start:g} and end {end:g} describe an empty or full "
                "circle (use circle() for full circles)."
            )
        self._geometry.append(ArcSpec(c, r, start, end))
        ref = GeometryRef(len(self._geometry) - 1)
        self.block(ref)
        return ref

    # -- constraints ---------------------------------------------------------

    def _geometry_arg(self, ref: GeometryRef | int, method: str) -> GeoArg:
        index = ref.index if isinstance(ref, GeometryRef) else int(ref)
        if not 0 <= index < len(self._geometry):
            raise SketchBuilderError(
                f"{method}: geometry index {index} does not exist on this "
                f"builder (have {len(self._geometry)} elements)."
            )
        return GeoArg(index)

    def _add(
        self,
        kind: str,
        args: tuple[ConstraintArg, ...],
        name: str | None = None,
    ) -> ConstraintSpec:
        if name is not None and (not name or not isinstance(name, str)):
            raise SketchBuilderError(
                f"Constraint name must be a non-empty string, got {name!r}."
            )
        spec = ConstraintSpec(kind=kind, args=args, name=name)
        self._constraints.append(spec)
        return spec

    def coincident(self, a: PointRef, b: PointRef) -> ConstraintSpec:
        return self._add(
            "Coincident",
            (
                self._geometry_arg(a.geometry, "coincident"),
                a.point,
                self._geometry_arg(b.geometry, "coincident"),
                b.point,
            ),
        )

    def horizontal(self, ref: GeometryRef | int) -> ConstraintSpec:
        return self._add("Horizontal", (self._geometry_arg(ref, "horizontal"),))

    def vertical(self, ref: GeometryRef | int) -> ConstraintSpec:
        return self._add("Vertical", (self._geometry_arg(ref, "vertical"),))

    def distance(
        self,
        ref: GeometryRef | int,
        value: Any,
        *,
        name: str | None = None,
    ) -> ConstraintSpec:
        return self._add(
            "Distance",
            (
                self._geometry_arg(ref, "distance"),
                _positive("distance value", value),
            ),
            name,
        )

    def distance_between(
        self,
        a: PointRef,
        b: PointRef,
        value: Any,
        *,
        name: str | None = None,
    ) -> ConstraintSpec:
        return self._add(
            "Distance",
            (
                self._geometry_arg(a.geometry, "distance_between"),
                a.point,
                self._geometry_arg(b.geometry, "distance_between"),
                b.point,
                _positive("distance value", value),
            ),
            name,
        )

    def distance_x(
        self,
        point: PointRef,
        value: Any,
        *,
        name: str | None = None,
    ) -> ConstraintSpec:
        return self._add(
            "DistanceX",
            (
                self._geometry_arg(point.geometry, "distance_x"),
                point.point,
                _finite("distance_x value", value),
            ),
            name,
        )

    def distance_y(
        self,
        point: PointRef,
        value: Any,
        *,
        name: str | None = None,
    ) -> ConstraintSpec:
        return self._add(
            "DistanceY",
            (
                self._geometry_arg(point.geometry, "distance_y"),
                point.point,
                _finite("distance_y value", value),
            ),
            name,
        )

    def radius(
        self,
        ref: GeometryRef | int,
        value: Any,
        *,
        name: str | None = None,
    ) -> ConstraintSpec:
        return self._add(
            "Radius",
            (
                self._geometry_arg(ref, "radius"),
                _positive("radius value", value),
            ),
            name,
        )

    def equal(self, a: GeometryRef | int, b: GeometryRef | int) -> ConstraintSpec:
        return self._add(
            "Equal",
            (self._geometry_arg(a, "equal"), self._geometry_arg(b, "equal")),
        )

    def block(self, ref: GeometryRef | int) -> ConstraintSpec:
        """Fix one geometry element exactly where its coordinates put it."""
        return self._add("Block", (self._geometry_arg(ref, "block"),))

    def fix_all(self) -> list[ConstraintSpec]:
        """Block every geometry element that is not already blocked.

        This is the fixed-sketch idiom: when a script computes exact
        coordinates (interpolated profiles, generated point tables), blocking
        the geometry makes the sketch fully constrained without inventing a
        dimension per point.
        """
        blocked = {
            spec.args[0].index
            for spec in self._constraints
            if spec.kind == "Block" and isinstance(spec.args[0], GeoArg)
        }
        return [
            self.block(index)
            for index in range(len(self._geometry))
            if index not in blocked
        ]

    def fix_point(
        self,
        point: PointRef,
        x: Any,
        y: Any,
        *,
        x_name: str | None = None,
        y_name: str | None = None,
    ) -> tuple[ConstraintSpec, ConstraintSpec]:
        return (
            self.distance_x(point, x, name=x_name),
            self.distance_y(point, y, name=y_name),
        )

    # -- application (needs FreeCAD) ----------------------------------------

    def apply(
        self,
        sketch: Any,
        *,
        construction: bool = False,
        fixed: bool = False,
    ) -> AppliedSketch:
        """Add all geometry and constraints to a live Sketcher object.

        With ``fixed=True`` every geometry element that is not already
        blocked receives a ``Block`` constraint first (see ``fix_all``), so
        computed-geometry sketches arrive fully constrained.
        """
        import FreeCAD as App
        import Part
        import Sketcher

        if not self._geometry:
            raise SketchBuilderError(
                "apply: builder has no geometry; add lines/circles first."
            )
        if fixed:
            self.fix_all()
        offset = int(
            getattr(sketch, "GeometryCount", len(getattr(sketch, "Geometry", [])))
        )
        geometry = []
        for spec in self._geometry:
            if isinstance(spec, LineSpec):
                geometry.append(
                    Part.LineSegment(
                        App.Vector(spec.start[0], spec.start[1], 0.0),
                        App.Vector(spec.end[0], spec.end[1], 0.0),
                    )
                )
            elif isinstance(spec, ArcSpec):
                geometry.append(
                    Part.ArcOfCircle(
                        Part.Circle(
                            App.Vector(spec.center[0], spec.center[1], 0.0),
                            App.Vector(0.0, 0.0, 1.0),
                            spec.radius,
                        ),
                        math.radians(spec.start_deg),
                        math.radians(spec.end_deg),
                    )
                )
            else:
                geometry.append(
                    Part.Circle(
                        App.Vector(spec.center[0], spec.center[1], 0.0),
                        App.Vector(0.0, 0.0, 1.0),
                        spec.radius,
                    )
                )
        constraint_offset = len(getattr(sketch, "Constraints", []))
        sketch.addGeometry(geometry, bool(construction))
        sketch.addConstraint(
            [
                Sketcher.Constraint(spec.kind, *resolve_constraint_args(spec, offset))
                for spec in self._constraints
            ]
        )
        for local_index, spec in enumerate(self._constraints):
            if spec.name is not None:
                sketch.renameConstraint(constraint_offset + local_index, spec.name)
        return AppliedSketch(
            sketch=sketch,
            geometry_offset=offset,
            geometry_count=len(self._geometry),
            constraint_offset=constraint_offset,
            constraint_count=len(self._constraints),
        )


# --------------------------------------------------------------------------
# Edge selectors (pure predicates over EdgeFacts)
# --------------------------------------------------------------------------

_AXES: dict[str, tuple[float, float, float]] = {
    "X": (1.0, 0.0, 0.0),
    "Y": (0.0, 1.0, 0.0),
    "Z": (0.0, 0.0, 1.0),
}


def _unit(vector: Any, context: str) -> tuple[float, float, float]:
    if isinstance(vector, str):
        try:
            return _AXES[vector.upper()]
        except KeyError:
            raise SelectorError(
                f"{context}: unknown axis {vector!r}; use 'X', 'Y', 'Z' or a 3-tuple."
            ) from None
    parsed = _vector3(vector)
    if parsed is None:
        raise SelectorError(
            f"{context}: expected an axis name or 3-vector, got {vector!r}."
        )
    norm = math.sqrt(sum(component**2 for component in parsed))
    if norm < 1e-12:
        raise SelectorError(f"{context}: zero-length direction {vector!r}.")
    return (parsed[0] / norm, parsed[1] / norm, parsed[2] / norm)


def is_parallel(direction: Any, axis: Any, *, tolerance: float = 1e-6) -> bool:
    """True when two directions are parallel or anti-parallel."""
    a = _unit(direction, "is_parallel")
    b = _unit(axis, "is_parallel")
    dot = abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])
    return dot >= 1.0 - tolerance


def is_perpendicular(direction: Any, axis: Any, *, tolerance: float = 1e-6) -> bool:
    """True when two directions are perpendicular."""
    a = _unit(direction, "is_perpendicular")
    b = _unit(axis, "is_perpendicular")
    dot = abs(a[0] * b[0] + a[1] * b[1] + a[2] * b[2])
    return dot <= tolerance


@dataclass(frozen=True)
class EdgeFacts:
    """Geometric facts about one edge, extracted right after a recompute."""

    index: int  # 1-based, matches FreeCAD's "Edge<N>" naming
    curve: str  # curve class name, e.g. "Line", "Circle"
    length: float
    midpoint: tuple[float, float, float]
    direction: tuple[float, float, float] | None = None

    @property
    def name(self) -> str:
        return f"Edge{self.index}"


def edge_facts_from_shape(source: Any) -> tuple[EdgeFacts, ...]:
    """Extract ``EdgeFacts`` from a shape or a feature exposing ``.Shape``."""
    shape = source
    if not hasattr(shape, "Edges") and hasattr(shape, "Shape"):
        shape = shape.Shape
    raw_edges = getattr(shape, "Edges", None)
    if raw_edges is None:
        raise SelectorError(
            f"edges: {source!r} has no Edges; pass a shape or a feature."
        )
    facts: list[EdgeFacts] = []
    for number, edge in enumerate(raw_edges, start=1):
        curve_obj = getattr(edge, "Curve", None)
        curve = type(curve_obj).__name__ if curve_obj is not None else "Unknown"
        direction = _vector3(
            getattr(curve_obj, "Direction", None) or getattr(curve_obj, "Axis", None)
        )
        midpoint = _vector3(getattr(edge, "CenterOfMass", None)) or (
            0.0,
            0.0,
            0.0,
        )
        facts.append(
            EdgeFacts(
                index=number,
                curve=curve,
                length=float(getattr(edge, "Length", 0.0)),
                midpoint=midpoint,
                direction=direction,
            )
        )
    return tuple(facts)


class EdgeQuery:
    """Immutable, chainable filter over a set of ``EdgeFacts``."""

    def __init__(self, facts: Iterable[EdgeFacts]) -> None:
        self._facts = tuple(facts)

    # -- core ----------------------------------------------------------------

    @property
    def facts(self) -> tuple[EdgeFacts, ...]:
        return self._facts

    def __len__(self) -> int:
        return len(self._facts)

    def __iter__(self) -> Iterator[EdgeFacts]:
        return iter(self._facts)

    def where(self, predicate: Callable[[EdgeFacts], bool]) -> "EdgeQuery":
        return EdgeQuery(fact for fact in self._facts if predicate(fact))

    # -- filters ---------------------------------------------------------------

    def lines(self) -> "EdgeQuery":
        return self.where(lambda f: f.curve in ("Line", "LineSegment"))

    def circles(self) -> "EdgeQuery":
        return self.where(lambda f: f.curve in ("Circle", "ArcOfCircle"))

    def parallel_to(self, axis: Any, *, tolerance: float = 1e-6) -> "EdgeQuery":
        unit = _unit(axis, "parallel_to")
        return self.where(
            lambda f: (
                f.direction is not None
                and is_parallel(f.direction, unit, tolerance=tolerance)
            )
        )

    def perpendicular_to(self, axis: Any, *, tolerance: float = 1e-6) -> "EdgeQuery":
        unit = _unit(axis, "perpendicular_to")
        return self.where(
            lambda f: (
                f.direction is not None
                and is_perpendicular(f.direction, unit, tolerance=tolerance)
            )
        )

    def of_length(self, value: Any, *, tolerance: float = 1e-6) -> "EdgeQuery":
        target = _finite("of_length value", value)
        return self.where(lambda f: abs(f.length - target) <= tolerance)

    def at(
        self,
        *,
        x: Any = None,
        y: Any = None,
        z: Any = None,
        tolerance: float = 1e-6,
    ) -> "EdgeQuery":
        """Edges whose midpoint matches the given coordinates."""
        checks: list[tuple[int, float]] = []
        for axis_index, value, label in ((0, x, "x"), (1, y, "y"), (2, z, "z")):
            if value is not None:
                checks.append((axis_index, _finite(f"at {label}", value)))
        if not checks:
            raise SelectorError("at: provide at least one of x, y, z.")
        return self.where(
            lambda f: all(abs(f.midpoint[i] - v) <= tolerance for i, v in checks)
        )

    def _extreme(
        self, axis_index: int, *, largest: bool, tolerance: float
    ) -> "EdgeQuery":
        if not self._facts:
            return self
        values = [fact.midpoint[axis_index] for fact in self._facts]
        pivot = max(values) if largest else min(values)
        return self.where(lambda f: abs(f.midpoint[axis_index] - pivot) <= tolerance)

    def top(self, *, tolerance: float = 1e-6) -> "EdgeQuery":
        """Edges whose midpoint has the maximum Z among the current set."""
        return self._extreme(2, largest=True, tolerance=tolerance)

    def bottom(self, *, tolerance: float = 1e-6) -> "EdgeQuery":
        """Edges whose midpoint has the minimum Z among the current set."""
        return self._extreme(2, largest=False, tolerance=tolerance)

    def longest(self, *, tolerance: float = 1e-9) -> "EdgeQuery":
        if not self._facts:
            return self
        pivot = max(fact.length for fact in self._facts)
        return self.where(lambda f: abs(f.length - pivot) <= tolerance)

    def shortest(self, *, tolerance: float = 1e-9) -> "EdgeQuery":
        if not self._facts:
            return self
        pivot = min(fact.length for fact in self._facts)
        return self.where(lambda f: abs(f.length - pivot) <= tolerance)

    # -- terminals ---------------------------------------------------------

    def names(self) -> list[str]:
        return [fact.name for fact in self._facts]

    def indices(self) -> list[int]:
        return [fact.index for fact in self._facts]

    def one(self) -> EdgeFacts:
        if len(self._facts) != 1:
            raise SelectorError(
                f"Expected exactly one edge, matched {len(self._facts)}: "
                f"{self.names()}."
            )
        return self._facts[0]

    def require(self, count: int) -> "EdgeQuery":
        if len(self._facts) != count:
            raise SelectorError(
                f"Expected exactly {count} edges, matched "
                f"{len(self._facts)}: {self.names()}."
            )
        return self


def edges(source: Any) -> EdgeQuery:
    """Entry point: build an ``EdgeQuery`` from a shape, feature, or facts."""
    if isinstance(source, EdgeQuery):
        return source
    if isinstance(source, (list, tuple)) and all(
        isinstance(item, EdgeFacts) for item in source
    ):
        return EdgeQuery(source)
    return EdgeQuery(edge_facts_from_shape(source))


# --------------------------------------------------------------------------
# Feature helpers (need FreeCAD document objects)
# --------------------------------------------------------------------------


def _axis_reference(profile: Any, axis: Any, context: str) -> tuple[Any, list[str]]:
    """Normalize an axis argument into a ``(object, [subname])`` reference.

    Accepts ``"V"``/``"H"`` (the profile sketch's vertical/horizontal axis),
    ``"N"`` (the sketch normal, for patterns), or an explicit
    ``(object, subname_or_list)`` tuple.
    """
    if isinstance(axis, str):
        key = axis.upper()
        if key in ("V", "H", "N"):
            return (profile, [f"{key}_Axis"])
        raise FeatureError(
            f"{context}: unknown axis {axis!r}; use 'V', 'H', 'N' or an "
            "(object, subname) tuple."
        )
    if isinstance(axis, tuple) and len(axis) == 2:
        target, subnames = axis
        if isinstance(subnames, str):
            subnames = [subnames]
        if not isinstance(subnames, (list, tuple)) or not all(
            isinstance(item, str) for item in subnames
        ):
            raise FeatureError(
                f"{context}: axis subnames must be a string or list of "
                f"strings, got {axis[1]!r}."
            )
        return (target, list(subnames))
    raise FeatureError(
        f"{context}: axis must be 'V', 'H', 'N' or an (object, subname) "
        f"tuple, got {axis!r}."
    )


def _feature_list(features: Any, context: str) -> list[Any]:
    """Normalize one feature or an iterable of features into a list."""
    if isinstance(features, (list, tuple)):
        items = list(features)
    else:
        items = [features]
    if not items or any(item is None for item in items):
        raise FeatureError(f"{context}: pass one feature or a non-empty list.")
    return items


def _sweep_angle(context: str, value: Any) -> float:
    angle = _finite(f"{context} angle", value)
    if not 0.0 < angle <= 360.0:
        raise FeatureError(
            f"{context} angle must be in (0, 360] degrees, got {angle:g}."
        )
    return angle


def new_body(document: Any, name: str = "Body", *, label: str | None = None) -> Any:
    """Create a ``PartDesign::Body`` in ``document``."""
    body = document.addObject("PartDesign::Body", name)
    if label is not None:
        body.Label = str(label)
    return body


def new_sketch(
    body: Any,
    plane: Any = "XY",
    *,
    name: str = "Sketch",
    z_offset: Any = 0.0,
) -> Any:
    """Create a sketch on ``body``, attached to a base plane or a face.

    ``plane`` accepts ``"XY"``/``"XZ"``/``"YZ"`` (the body's origin planes),
    an ``(object, subname)`` tuple for face attachment, or an existing plane
    object. ``z_offset`` shifts the sketch along its normal, which is how
    loft sections are stacked.
    """
    offset = _finite("new_sketch z_offset", z_offset)
    support: tuple[Any, list[str]]
    if isinstance(plane, str):
        key = plane.upper()
        if key not in ("XY", "XZ", "YZ"):
            raise FeatureError(
                f"new_sketch: unknown plane {plane!r}; use 'XY', 'XZ', 'YZ', "
                "an (object, subname) tuple, or a plane object."
            )
        role = f"{key}_Plane"
        origin = getattr(body, "Origin", None)
        origin_features = getattr(origin, "OriginFeatures", None) or []
        plane_object = next(
            (
                item
                for item in origin_features
                if str(getattr(item, "Role", "")) == role
            ),
            None,
        )
        if plane_object is None:
            raise FeatureError(
                f"new_sketch: body {getattr(body, 'Name', body)!r} has no "
                f"origin plane with role {role!r}."
            )
        support = (plane_object, [""])
    elif isinstance(plane, tuple) and len(plane) == 2:
        target, subname = plane
        support = (target, [str(subname)])
    else:
        support = (plane, [""])
    sketch = body.newObject("Sketcher::SketchObject", name)
    if hasattr(sketch, "AttachmentSupport"):
        sketch.AttachmentSupport = [support]
    else:
        sketch.Support = [support]
    sketch.MapMode = "FlatFace"
    if offset != 0.0:
        import FreeCAD as App

        sketch.AttachmentOffset = App.Placement(
            App.Vector(0.0, 0.0, offset), App.Rotation()
        )
    return sketch


def pad(
    body: Any,
    profile: Any,
    length: Any,
    *,
    name: str = "Pad",
    reverse: bool = False,
    midplane: bool = False,
    refine: bool = True,
) -> Any:
    """Create a ``PartDesign::Pad`` from a closed profile sketch."""
    feature = body.newObject("PartDesign::Pad", name)
    feature.Profile = profile
    feature.Length = _positive("pad length", length)
    feature.Reversed = bool(reverse)
    feature.SideType = "Symmetric" if midplane else "One side"
    feature.Refine = bool(refine)
    return feature


def pocket(
    body: Any,
    profile: Any,
    length: Any = None,
    *,
    name: str = "Pocket",
    through_all: bool = False,
    reverse: bool = False,
    midplane: bool = False,
    refine: bool = True,
) -> Any:
    """Create a ``PartDesign::Pocket`` from a closed profile sketch.

    Pass a positive ``length`` for a blind pocket, or ``through_all=True``
    (and no length) to cut through the whole solid regardless of its depth.
    """
    if through_all:
        if length is not None:
            raise FeatureError(
                "pocket: through_all=True cuts the full depth; do not pass "
                "a length as well."
            )
    elif length is None:
        raise FeatureError(
            "pocket: pass a positive length, or through_all=True to cut "
            "through the whole solid."
        )
    feature = body.newObject("PartDesign::Pocket", name)
    feature.Profile = profile
    if through_all:
        feature.Type = "ThroughAll"
    else:
        feature.Length = _positive("pocket length", length)
    feature.Reversed = bool(reverse)
    feature.SideType = "Symmetric" if midplane else "One side"
    feature.Refine = bool(refine)
    return feature


def revolve(
    body: Any,
    profile: Any,
    angle: Any = 360.0,
    *,
    axis: Any = "V",
    name: str = "Revolution",
    reverse: bool = False,
    midplane: bool = False,
    refine: bool = True,
) -> Any:
    """Create a ``PartDesign::Revolution`` by sweeping ``profile`` around an axis.

    ``axis`` defaults to the profile sketch's vertical axis; pass ``"H"`` for
    its horizontal axis or an ``(object, subname)`` tuple for anything else.
    The profile must not cross the axis.
    """
    feature = body.newObject("PartDesign::Revolution", name)
    feature.Profile = profile
    feature.ReferenceAxis = _axis_reference(profile, axis, "revolve")
    feature.Angle = _sweep_angle("revolve", angle)
    feature.Reversed = bool(reverse)
    feature.Midplane = bool(midplane)
    feature.Refine = bool(refine)
    return feature


def groove(
    body: Any,
    profile: Any,
    angle: Any = 360.0,
    *,
    axis: Any = "V",
    name: str = "Groove",
    reverse: bool = False,
    midplane: bool = False,
    refine: bool = True,
) -> Any:
    """Create a ``PartDesign::Groove``: the subtractive form of ``revolve``."""
    feature = body.newObject("PartDesign::Groove", name)
    feature.Profile = profile
    feature.ReferenceAxis = _axis_reference(profile, axis, "groove")
    feature.Angle = _sweep_angle("groove", angle)
    feature.Reversed = bool(reverse)
    feature.Midplane = bool(midplane)
    feature.Refine = bool(refine)
    return feature


def loft(
    body: Any,
    sections: Iterable[Any],
    *,
    name: str = "Loft",
    ruled: bool = False,
    closed: bool = False,
    subtractive: bool = False,
    refine: bool = True,
) -> Any:
    """Create a ``PartDesign::AdditiveLoft`` (or subtractive) through sections.

    ``sections`` is an ordered iterable of at least two profile sketches;
    the first becomes the loft ``Profile`` and the rest its ``Sections``.
    """
    profiles = list(sections)
    if len(profiles) < 2:
        raise FeatureError(
            f"loft: needs at least 2 section sketches, got {len(profiles)}."
        )
    if subtractive:
        type_id = "PartDesign::SubtractiveLoft"
    else:
        type_id = "PartDesign::AdditiveLoft"
    feature = body.newObject(type_id, name)
    feature.Profile = profiles[0]
    feature.Sections = profiles[1:]
    feature.Ruled = bool(ruled)
    feature.Closed = bool(closed)
    feature.Refine = bool(refine)
    return feature


def polar_pattern(
    body: Any,
    features: Any,
    count: Any,
    *,
    axis: Any = "N",
    angle: Any = 360.0,
    name: str = "PolarPattern",
) -> Any:
    """Create a ``PartDesign::PolarPattern`` of one or more features.

    ``count`` is the total number of occurrences including the original.
    ``axis`` defaults to the normal axis of the first feature's profile
    sketch; pass an ``(object, subname)`` tuple to pattern around anything
    else. This is the correct way to repeat a feature N times around a hub:
    the pattern participates in the body's feature tree, unlike manually
    copied sketches.
    """
    originals = _feature_list(features, "polar_pattern")
    occurrences = int(_finite("polar_pattern count", count))
    if occurrences < 2:
        raise FeatureError(
            f"polar_pattern: count must be at least 2, got {occurrences}."
        )
    axis_target = originals[0]
    profile = getattr(axis_target, "Profile", None)
    if profile is not None:
        axis_target = profile[0] if isinstance(profile, tuple) else profile
    feature = body.newObject("PartDesign::PolarPattern", name)
    feature.Originals = originals
    feature.Axis = _axis_reference(axis_target, axis, "polar_pattern")
    feature.Angle = _sweep_angle("polar_pattern", angle)
    feature.Occurrences = occurrences
    return feature


def mirror(
    body: Any,
    features: Any,
    plane: Any,
    *,
    name: str = "Mirrored",
) -> Any:
    """Create a ``PartDesign::Mirrored`` of one or more features.

    ``plane`` is ``"V"``/``"H"`` (the first feature's profile-sketch axes,
    mirroring across the sketch's vertical/horizontal axis) or an
    ``(object, subname)`` tuple naming any datum plane or face.
    """
    originals = _feature_list(features, "mirror")
    plane_target = originals[0]
    profile = getattr(plane_target, "Profile", None)
    if profile is not None:
        plane_target = profile[0] if isinstance(profile, tuple) else profile
    feature = body.newObject("PartDesign::Mirrored", name)
    feature.Originals = originals
    feature.MirrorPlane = _axis_reference(plane_target, plane, "mirror")
    return feature


def fillet(
    body: Any,
    base_feature: Any,
    edge_names: Iterable[str] | EdgeQuery,
    radius: Any,
    *,
    name: str = "Fillet",
) -> Any:
    """Create a ``PartDesign::Fillet`` on selected edges of ``base_feature``.

    ``edge_names`` accepts an ``EdgeQuery`` (resolved via ``names()``) or an
    iterable of ``"Edge<N>"`` strings.
    """
    if isinstance(edge_names, EdgeQuery):
        names = edge_names.names()
    else:
        names = [str(item) for item in edge_names]
    if not names:
        raise FeatureError("fillet: no edges selected; the selector matched nothing.")
    feature = body.newObject("PartDesign::Fillet", name)
    feature.Base = (base_feature, names)
    feature.Radius = _positive("fillet radius", radius)
    return feature


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def assert_fully_constrained(sketch: Any) -> None:
    """Raise ``SketchValidationError`` unless the sketch solves cleanly and
    has zero remaining degrees of freedom."""
    label = getattr(sketch, "Name", None) or repr(sketch)
    solve = getattr(sketch, "solve", None)
    if callable(solve):
        status = int(solve())
        if status != 0:
            raise SketchValidationError(
                f"Sketch {label!r} failed to solve (status {status}); fix "
                "conflicting or malformed constraints."
            )
    if not bool(getattr(sketch, "FullyConstrained", False)):
        raise SketchValidationError(
            f"Sketch {label!r} is not fully constrained; add the missing "
            "dimensions or positional constraints."
        )
