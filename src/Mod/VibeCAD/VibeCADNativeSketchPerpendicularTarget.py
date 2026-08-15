# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target forms for Native Sketch Perpendicular."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeSketchConstraintTargets import (
    SketchConstraintElement,
    SketchConstraintTargetSpec,
    prepare_sketch_constraint_target,
    sketch_constraint_geometry,
)
from VibeCADNativeSketchErrors import NativeSketchError


LABEL = "Sketch Perpendicular"
FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "target",
    }
)
_CURVE_CURVE_FIELDS = frozenset({"form", "first_curve", "second_curve"})
_ENDPOINT_CURVE_FIELDS = frozenset({"form", "endpoint", "curve"})
_ENDPOINT_ENDPOINT_FIELDS = frozenset(
    {"form", "first_endpoint", "second_endpoint"}
)
_POINT_PAIR_LINE_FIELDS = frozenset(
    {"form", "first_point", "second_point", "line"}
)
_VIA_POINT_FIELDS = frozenset(
    {"form", "first_curve", "second_curve", "point"}
)
_LINE_TYPE = "Part::GeomLineSegment"
_POINT_TYPE = "Part::GeomPoint"
_BSPLINE_TYPE = "Part::GeomBSplineCurve"
_SIMPLE_CURVE_TYPES = frozenset(
    {_LINE_TYPE, "Part::GeomCircle", "Part::GeomArcOfCircle"}
)
_IMPLICIT_POINT_TYPES = frozenset(
    {
        "Part::GeomEllipse",
        "Part::GeomArcOfEllipse",
        "Part::GeomHyperbola",
        "Part::GeomArcOfHyperbola",
        "Part::GeomParabola",
        "Part::GeomArcOfParabola",
    }
)


@dataclass(frozen=True, slots=True)
class SketchPerpendicularSpec:
    target: SketchConstraintTargetSpec
    target_form: str


@dataclass(frozen=True, slots=True)
class PerpendicularConstraintPlan:
    constructor: tuple[Any, ...]
    references: tuple[SketchConstraintElement, ...]
    support: bool
    orientation_value: bool


@dataclass(frozen=True, slots=True)
class ResolvedSketchPerpendicular:
    target_form: str
    references: tuple[SketchConstraintElement, ...]
    plans: tuple[PerpendicularConstraintPlan, ...]


def prepare_sketch_perpendicular_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchPerpendicularSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    raw = value["target"]
    if not isinstance(raw, Mapping):
        raise NativeSketchError(f"{LABEL} target must be an object.")
    form = raw.get("form")
    if form == "curve_curve" and set(raw) == _CURVE_CURVE_FIELDS:
        selection = [raw["first_curve"], raw["second_curve"]]
    elif form == "endpoint_curve" and set(raw) == _ENDPOINT_CURVE_FIELDS:
        selection = [raw["endpoint"], raw["curve"]]
    elif form == "endpoint_endpoint" and set(raw) == _ENDPOINT_ENDPOINT_FIELDS:
        selection = [raw["first_endpoint"], raw["second_endpoint"]]
    elif form == "point_pair_line" and set(raw) == _POINT_PAIR_LINE_FIELDS:
        selection = [raw["first_point"], raw["second_point"], raw["line"]]
    elif form == "curves_via_point" and set(raw) == _VIA_POINT_FIELDS:
        selection = [raw["first_curve"], raw["second_curve"], raw["point"]]
    else:
        raise NativeSketchError(
            f"{LABEL} target must be one exact curve_curve, endpoint_curve, "
            "endpoint_endpoint, point_pair_line, or curves_via_point form."
        )
    return SketchPerpendicularSpec(
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value[
                "expected_external_geometry_count"
            ],
            selection=selection,
        ),
        str(form),
    )


def _geometry_type(sketch: Any, element: SketchConstraintElement) -> str:
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    return str(getattr(geometry, "TypeId", "") or "")


def _require_whole_curve(
    sketch: Any,
    element: SketchConstraintElement,
    role: str,
) -> str:
    if element.position != "whole":
        raise NativeSketchError(f"{LABEL} {role} must be one exact whole curve.")
    geometry_type = _geometry_type(sketch, element)
    if geometry_type == _POINT_TYPE or not geometry_type.startswith("Part::Geom"):
        raise NativeSketchError(
            f"{LABEL} {role} does not name supported curve geometry."
        )
    return geometry_type


def _require_exact_point(
    sketch: Any,
    element: SketchConstraintElement,
    role: str,
    *,
    endpoint: bool,
) -> None:
    allowed = {"start", "end"} if endpoint else {"start", "end", "center"}
    if element.position not in allowed:
        kind = "curve endpoint" if endpoint else "point"
        raise NativeSketchError(f"{LABEL} {role} must be one exact {kind}.")
    geometry_type = _geometry_type(sketch, element)
    if endpoint and (element.geometry_index in {-1, -2} or geometry_type == _POINT_TYPE):
        raise NativeSketchError(
            f"{LABEL} {role} must be a connected curve endpoint, not a root or "
            "standalone point."
        )


def _editable(sketch: Any, index: int) -> bool:
    if index < 0:
        return False
    try:
        facade = sketch.GeometryFacadeList[index]
        return not bool(getattr(facade, "Blocked"))
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} editability is unavailable.") from exc


def _require_editable(sketch: Any, references: tuple[SketchConstraintElement, ...]) -> None:
    if not any(_editable(sketch, element.geometry_index) for element in references):
        raise NativeSketchError(
            f"{LABEL} requires at least one editable internal target."
        )


def _point_on_curve(
    sketch: Any,
    point: SketchConstraintElement,
    curve: SketchConstraintElement,
) -> bool:
    getter = getattr(sketch, "getPoint", None)
    query = getattr(sketch, "isPointOnCurve", None)
    if not callable(getter) or not callable(query):
        raise NativeSketchError(f"{LABEL} point-on-curve queries are unavailable.")
    try:
        value = getter(point.geometry_index, point.position_code)
        return bool(query(curve.geometry_index, float(value.x), float(value.y)))
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} point-on-curve query failed.") from exc


def _has_point_on_object(
    sketch: Any,
    point: SketchConstraintElement,
    curve: SketchConstraintElement,
) -> bool:
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc
    return any(
        str(getattr(constraint, "Type", "") or "") == "PointOnObject"
        and int(getattr(constraint, "First", -2000)) == point.geometry_index
        and int(getattr(constraint, "FirstPos", 0)) == point.position_code
        and int(getattr(constraint, "Second", -2000)) == curve.geometry_index
        for constraint in constraints
    )


def _support_plan(
    sketch: Any,
    point: SketchConstraintElement,
    curve: SketchConstraintElement,
) -> PerpendicularConstraintPlan | None:
    if (
        point.geometry_index == curve.geometry_index
        or _has_point_on_object(sketch, point, curve)
        or _geometry_type(sketch, curve) == _BSPLINE_TYPE
    ):
        return None
    return PerpendicularConstraintPlan(
        (
            "PointOnObject",
            point.geometry_index,
            point.position_code,
            curve.geometry_index,
        ),
        (point, curve),
        True,
        False,
    )


def _constraint_key(constraint: Any) -> tuple[Any, ...] | None:
    if str(getattr(constraint, "Type", "") or "") != "Perpendicular":
        return None
    first = int(getattr(constraint, "First", -2000))
    first_pos = int(getattr(constraint, "FirstPos", 0))
    second = int(getattr(constraint, "Second", -2000))
    second_pos = int(getattr(constraint, "SecondPos", 0))
    third = int(getattr(constraint, "Third", -2000))
    third_pos = int(getattr(constraint, "ThirdPos", 0))
    if third <= -2000:
        if not first_pos and not second_pos:
            return "curve_curve", frozenset((first, second))
        if first_pos and not second_pos:
            return "endpoint_curve", (first, first_pos), second
        if first_pos and second_pos:
            return "endpoint_endpoint", frozenset(
                ((first, first_pos), (second, second_pos))
            )
        return None
    if first_pos and second_pos:
        return (
            "point_pair_line",
            frozenset(((first, first_pos), (second, second_pos))),
            third,
        )
    if not first_pos and not second_pos and third_pos:
        return (
            "curves_via_point",
            frozenset((first, second)),
            (third, third_pos),
        )
    return None


def _resolved_key(
    form: str,
    references: tuple[SketchConstraintElement, ...],
) -> tuple[Any, ...]:
    encoded = tuple(
        (element.geometry_index, element.position_code) for element in references
    )
    if form == "curve_curve":
        return form, frozenset(item[0] for item in encoded)
    if form == "endpoint_curve":
        return form, encoded[0], encoded[1][0]
    if form == "endpoint_endpoint":
        return form, frozenset(encoded)
    if form == "point_pair_line":
        return "curve_curve", frozenset((encoded[0][0], encoded[2][0]))
    return form, frozenset(item[0] for item in encoded[:2]), encoded[2]


def _refuse_existing(
    sketch: Any,
    form: str,
    references: tuple[SketchConstraintElement, ...],
) -> None:
    expected = {_resolved_key(form, references)}
    if form == "point_pair_line":
        encoded = tuple(
            (element.geometry_index, element.position_code)
            for element in references
        )
        expected.add(
            (
                form,
                frozenset(encoded[:2]),
                encoded[2][0],
            )
        )
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc
    if any(_constraint_key(constraint) in expected for constraint in constraints):
        raise NativeSketchError(
            f"{LABEL} targets already have that Perpendicular constraint."
        )


def _main_plan(
    form: str,
    references: tuple[SketchConstraintElement, ...],
) -> PerpendicularConstraintPlan:
    encoded = tuple(
        (element.geometry_index, element.position_code) for element in references
    )
    if form == "curve_curve":
        constructor = "Perpendicular", encoded[0][0], encoded[1][0]
        orientation = False
    elif form == "endpoint_curve":
        constructor = "Perpendicular", encoded[0][0], encoded[0][1], encoded[1][0]
        orientation = True
    elif form == "endpoint_endpoint":
        constructor = (
            "Perpendicular",
            encoded[0][0],
            encoded[0][1],
            encoded[1][0],
            encoded[1][1],
        )
        orientation = True
    elif form == "point_pair_line":
        line_reference = SketchConstraintElement(encoded[0][0], "whole")
        return PerpendicularConstraintPlan(
            ("Perpendicular", encoded[0][0], encoded[2][0]),
            (line_reference, references[2]),
            False,
            False,
        )
    else:
        constructor = (
            "PerpendicularViaPoint",
            encoded[0][0],
            encoded[1][0],
            encoded[2][0],
            encoded[2][1],
        )
        orientation = True
    return PerpendicularConstraintPlan(constructor, references, False, orientation)


def resolve_sketch_perpendicular(
    sketch: Any,
    spec: SketchPerpendicularSpec,
) -> ResolvedSketchPerpendicular:
    references = spec.target.selection
    form = spec.target_form
    if form == "curve_curve":
        first_type = _require_whole_curve(sketch, references[0], "first curve")
        second_type = _require_whole_curve(sketch, references[1], "second curve")
        if references[0].geometry_index == references[1].geometry_index:
            raise NativeSketchError(f"{LABEL} requires two distinct curves.")
        if _LINE_TYPE not in {first_type, second_type}:
            raise NativeSketchError(
                f"{LABEL} curve_curve requires at least one straight line."
            )
        other_type = second_type if first_type == _LINE_TYPE else first_type
        if other_type in _IMPLICIT_POINT_TYPES:
            raise NativeSketchError(
                f"{LABEL} will not infer construction geometry for that conic; "
                "create and select an explicit point with curves_via_point."
            )
        if first_type not in _SIMPLE_CURVE_TYPES or second_type not in _SIMPLE_CURVE_TYPES:
            raise NativeSketchError(
                f"{LABEL} curve_curve supports lines, circles, and circular arcs; "
                "use an explicit point form for other curves."
            )
        _require_editable(sketch, references)
        support = ()
    elif form == "endpoint_curve":
        _require_exact_point(sketch, references[0], "endpoint", endpoint=True)
        _require_whole_curve(sketch, references[1], "curve")
        if references[0].geometry_index == references[1].geometry_index:
            raise NativeSketchError(f"{LABEL} cannot target one curve against itself.")
        _require_editable(sketch, references)
        support = ()
    elif form == "endpoint_endpoint":
        _require_exact_point(sketch, references[0], "first endpoint", endpoint=True)
        _require_exact_point(sketch, references[1], "second endpoint", endpoint=True)
        if references[0].geometry_index == references[1].geometry_index:
            raise NativeSketchError(
                f"{LABEL} endpoint_endpoint requires two distinct curves."
            )
        _require_editable(sketch, references)
        support = ()
    elif form == "point_pair_line":
        _require_exact_point(sketch, references[0], "first point", endpoint=True)
        _require_exact_point(sketch, references[1], "second point", endpoint=True)
        if (
            references[0].geometry_index != references[1].geometry_index
            or {references[0].position, references[1].position} != {"start", "end"}
            or _geometry_type(sketch, references[0]) != _LINE_TYPE
        ):
            raise NativeSketchError(
                f"{LABEL} point_pair_line requires the start and end of one "
                "explicit straight line; arbitrary point pairs use an unsafe host "
                "constructor. Create the line first."
            )
        line_type = _require_whole_curve(sketch, references[2], "line")
        if line_type != _LINE_TYPE:
            raise NativeSketchError(f"{LABEL} point_pair_line requires a straight line.")
        if references[0].geometry_index == references[2].geometry_index:
            raise NativeSketchError(f"{LABEL} cannot target one line against itself.")
        _require_editable(sketch, (references[0], references[2]))
        support = ()
    else:
        _require_whole_curve(sketch, references[0], "first curve")
        _require_whole_curve(sketch, references[1], "second curve")
        _require_exact_point(sketch, references[2], "point", endpoint=False)
        if references[0].geometry_index == references[1].geometry_index:
            raise NativeSketchError(f"{LABEL} requires two distinct curves.")
        _require_editable(sketch, references[:2])
        support = tuple(
            plan
            for curve in references[:2]
            for plan in (_support_plan(sketch, references[2], curve),)
            if plan is not None
        )
    _refuse_existing(sketch, form, references)
    return ResolvedSketchPerpendicular(
        form,
        references,
        (*support, _main_plan(form, references)),
    )


def perpendicular_via_point_is_on_curves(
    sketch: Any,
    resolved: ResolvedSketchPerpendicular,
) -> bool:
    if resolved.target_form != "curves_via_point":
        return True
    point = resolved.references[2]
    return all(
        _point_on_curve(sketch, point, curve) for curve in resolved.references[:2]
    )


def make_perpendicular_constraints(
    resolved: ResolvedSketchPerpendicular,
) -> tuple[Any, ...]:
    import Sketcher

    try:
        return tuple(Sketcher.Constraint(*plan.constructor) for plan in resolved.plans)
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact Perpendicular definition."
        ) from exc
