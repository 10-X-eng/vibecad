# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed exact target and replacement forms for Native Sketch Tangent."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeSketchConstraintTargets import (
    SketchConstraintElement,
    SketchConstraintTargetSpec,
    prepare_sketch_constraint_target,
    sketch_constraint_geometry,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchState import serialize_sketch_constraint


LABEL = "Sketch Tangent"
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
_VIA_POINT_FIELDS = frozenset(
    {"form", "first_curve", "second_curve", "point"}
)
_REPLACE_ENDPOINT_CURVE_FIELDS = frozenset(
    {"form", "constraint_index", "endpoint", "curve"}
)
_REPLACE_ENDPOINT_ENDPOINT_FIELDS = frozenset(
    {"form", "constraint_index", "first_endpoint", "second_endpoint"}
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
class SketchTangentSpec:
    target: SketchConstraintTargetSpec
    target_form: str
    replacement_index: int | None


@dataclass(frozen=True, slots=True)
class TangentConstraintPlan:
    constructor: tuple[Any, ...]
    references: tuple[SketchConstraintElement, ...]
    support: bool
    orientation_value: bool


@dataclass(frozen=True, slots=True)
class ResolvedSketchTangent:
    target_form: str
    semantic_form: str
    references: tuple[SketchConstraintElement, ...]
    plans: tuple[TangentConstraintPlan, ...]
    replacement_index: int | None
    replaced_constraint: Mapping[str, Any] | None


def prepare_sketch_tangent_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchTangentSpec:
    if not isinstance(value, Mapping) or set(value) != FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    raw = value["target"]
    if not isinstance(raw, Mapping):
        raise NativeSketchError(f"{LABEL} target must be an object.")
    form = raw.get("form")
    replacement_index = None
    if form == "curve_curve" and set(raw) == _CURVE_CURVE_FIELDS:
        selection = [raw["first_curve"], raw["second_curve"]]
    elif form == "endpoint_curve" and set(raw) == _ENDPOINT_CURVE_FIELDS:
        selection = [raw["endpoint"], raw["curve"]]
    elif form == "endpoint_endpoint" and set(raw) == _ENDPOINT_ENDPOINT_FIELDS:
        selection = [raw["first_endpoint"], raw["second_endpoint"]]
    elif form == "curves_via_point" and set(raw) == _VIA_POINT_FIELDS:
        selection = [raw["first_curve"], raw["second_curve"], raw["point"]]
    elif (
        form == "replace_with_endpoint_curve"
        and set(raw) == _REPLACE_ENDPOINT_CURVE_FIELDS
    ):
        selection = [raw["endpoint"], raw["curve"]]
        replacement_index = raw["constraint_index"]
    elif (
        form == "replace_with_endpoint_endpoint"
        and set(raw) == _REPLACE_ENDPOINT_ENDPOINT_FIELDS
    ):
        selection = [raw["first_endpoint"], raw["second_endpoint"]]
        replacement_index = raw["constraint_index"]
    else:
        raise NativeSketchError(
            f"{LABEL} target must be one exact curve_curve, endpoint_curve, "
            "endpoint_endpoint, curves_via_point, or explicit replacement form."
        )
    if replacement_index is not None and type(replacement_index) is not int:
        raise NativeSketchError(f"{LABEL} replacement index must be an integer.")
    return SketchTangentSpec(
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
        replacement_index,
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


def _require_endpoint(
    sketch: Any,
    element: SketchConstraintElement,
    role: str,
) -> None:
    if element.position not in {"start", "end"}:
        raise NativeSketchError(f"{LABEL} {role} must be one exact curve endpoint.")
    geometry_type = _geometry_type(sketch, element)
    if element.geometry_index in {-1, -2} or geometry_type == _POINT_TYPE:
        raise NativeSketchError(
            f"{LABEL} {role} must be a curve endpoint, not a root or standalone point."
        )


def _require_point(
    sketch: Any,
    element: SketchConstraintElement,
    role: str,
) -> None:
    if element.position not in {"start", "end", "center"}:
        raise NativeSketchError(f"{LABEL} {role} must be one exact point.")
    _geometry_type(sketch, element)
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError(f"{LABEL} point lookup is unavailable.")
    try:
        point = getter(element.geometry_index, element.position_code)
        coordinates = float(point.x), float(point.y)
    except Exception as exc:
        raise NativeSketchError(
            f"{LABEL} {role} does not name an available point."
        ) from exc
    if not all(math.isfinite(value) for value in coordinates):
        raise NativeSketchError(f"{LABEL} {role} is not finite.")


def _editable(sketch: Any, index: int) -> bool:
    if index < 0:
        return False
    try:
        return not bool(getattr(sketch.GeometryFacadeList[index], "Blocked"))
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} editability is unavailable.") from exc


def _require_editable(
    sketch: Any,
    references: tuple[SketchConstraintElement, ...],
) -> None:
    if not any(_editable(sketch, item.geometry_index) for item in references):
        raise NativeSketchError(
            f"{LABEL} requires at least one editable internal target."
        )


def _constraints(sketch: Any) -> tuple[Any, ...]:
    try:
        return tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc


def _has_point_on_object(
    sketch: Any,
    point: SketchConstraintElement,
    curve: SketchConstraintElement,
) -> bool:
    return any(
        str(getattr(constraint, "Type", "") or "") == "PointOnObject"
        and int(getattr(constraint, "First", -2000)) == point.geometry_index
        and int(getattr(constraint, "FirstPos", 0)) == point.position_code
        and int(getattr(constraint, "Second", -2000)) == curve.geometry_index
        for constraint in _constraints(sketch)
    )


def _support_plan(
    sketch: Any,
    point: SketchConstraintElement,
    curve: SketchConstraintElement,
) -> TangentConstraintPlan | None:
    if (
        point.geometry_index == curve.geometry_index
        or _has_point_on_object(sketch, point, curve)
        or _geometry_type(sketch, curve) == _BSPLINE_TYPE
    ):
        return None
    return TangentConstraintPlan(
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
    if str(getattr(constraint, "Type", "") or "") != "Tangent":
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
    if not first_pos and not second_pos and third_pos:
        return (
            "curves_via_point",
            frozenset((first, second)),
            (third, third_pos),
        )
    return None


def _resolved_key(
    semantic_form: str,
    references: tuple[SketchConstraintElement, ...],
) -> tuple[Any, ...]:
    encoded = tuple(
        (element.geometry_index, element.position_code) for element in references
    )
    if semantic_form == "curve_curve":
        return semantic_form, frozenset(item[0] for item in encoded)
    if semantic_form == "endpoint_curve":
        return semantic_form, encoded[0], encoded[1][0]
    if semantic_form == "endpoint_endpoint":
        return semantic_form, frozenset(encoded)
    return semantic_form, frozenset(item[0] for item in encoded[:2]), encoded[2]


def _refuse_existing_tangent(
    sketch: Any,
    semantic_form: str,
    references: tuple[SketchConstraintElement, ...],
    replacement_index: int | None,
) -> None:
    expected = _resolved_key(semantic_form, references)
    for index, constraint in enumerate(_constraints(sketch)):
        if index != replacement_index and _constraint_key(constraint) == expected:
            raise NativeSketchError(
                f"{LABEL} targets already have that Tangent constraint."
            )


def _support_replacement_message(
    sketch: Any,
    semantic_form: str,
    references: tuple[SketchConstraintElement, ...],
) -> None:
    if semantic_form == "curves_via_point":
        return
    first_index = references[0].geometry_index
    second_index = references[1].geometry_index
    selected = frozenset((first_index, second_index))
    for index, constraint in enumerate(_constraints(sketch)):
        kind = str(getattr(constraint, "Type", "") or "")
        first = int(getattr(constraint, "First", -2000))
        second = int(getattr(constraint, "Second", -2000))
        first_pos = int(getattr(constraint, "FirstPos", 0))
        second_pos = int(getattr(constraint, "SecondPos", 0))
        if (
            semantic_form in {"curve_curve", "endpoint_endpoint"}
            and kind == "Coincident"
            and frozenset((first, second)) == selected
            and (
                semantic_form == "curve_curve"
                or frozenset(
                    (
                        (first, first_pos),
                        (second, second_pos),
                    )
                )
                == frozenset(
                    (
                        (
                            references[0].geometry_index,
                            references[0].position_code,
                        ),
                        (
                            references[1].geometry_index,
                            references[1].position_code,
                        ),
                    )
                )
            )
        ):
            raise NativeSketchError(
                f"{LABEL} would replace Coincident constraint {index}; use "
                "replace_with_endpoint_endpoint and name that exact index."
            )
        if (
            semantic_form in {"curve_curve", "endpoint_curve"}
            and kind == "PointOnObject"
            and {first, second} == set(selected)
            and (
                semantic_form == "curve_curve"
                or (
                    first == references[0].geometry_index
                    and first_pos == references[0].position_code
                    and second == references[1].geometry_index
                )
            )
        ):
            raise NativeSketchError(
                f"{LABEL} would replace PointOnObject constraint {index}; use "
                "replace_with_endpoint_curve and name that exact index."
            )
        if (
            semantic_form in {"endpoint_curve", "endpoint_endpoint"}
            and kind == "Tangent"
            and not first_pos
            and not second_pos
            and frozenset((first, second)) == selected
        ):
            replacement_form = (
                "replace_with_endpoint_curve"
                if semantic_form == "endpoint_curve"
                else "replace_with_endpoint_endpoint"
            )
            raise NativeSketchError(
                f"{LABEL} would replace whole-curve Tangent constraint {index}; "
                f"use {replacement_form} and name that exact index."
            )


def _replacement_record(
    sketch: Any,
    spec: SketchTangentSpec,
    semantic_form: str,
    references: tuple[SketchConstraintElement, ...],
) -> Mapping[str, Any]:
    index = spec.replacement_index
    count = spec.target.target.expected_constraint_count
    if index is None or index < 0 or index >= count:
        raise NativeSketchError(
            f"{LABEL} replacement index is outside the expected constraints."
        )
    record = serialize_sketch_constraint(sketch, index)
    if (
        not bool(record.get("driving"))
        or not bool(record.get("active"))
        or bool(record.get("virtual"))
    ):
        raise NativeSketchError(
            f"{LABEL} can replace only one active driving non-virtual constraint."
        )
    kind = str(record.get("type", ""))
    constraint = _constraints(sketch)[index]
    if kind == "Tangent":
        valid = _constraint_key(constraint) == (
            "curve_curve",
            frozenset(item.geometry_index for item in references),
        )
    elif semantic_form == "endpoint_endpoint" and kind == "Coincident":
        expected = frozenset(
            (item.geometry_index, item.position_code) for item in references
        )
        observed = frozenset(
            (
                (
                    int(getattr(constraint, "First", -2000)),
                    int(getattr(constraint, "FirstPos", 0)),
                ),
                (
                    int(getattr(constraint, "Second", -2000)),
                    int(getattr(constraint, "SecondPos", 0)),
                ),
            )
        )
        valid = observed == expected
    elif semantic_form == "endpoint_curve" and kind == "PointOnObject":
        valid = (
            int(getattr(constraint, "First", -2000))
            == references[0].geometry_index
            and int(getattr(constraint, "FirstPos", 0))
            == references[0].position_code
            and int(getattr(constraint, "Second", -2000))
            == references[1].geometry_index
        )
    else:
        valid = False
    if not valid:
        expected_types = (
            "Coincident or whole-curve Tangent"
            if semantic_form == "endpoint_endpoint"
            else "PointOnObject or whole-curve Tangent"
        )
        raise NativeSketchError(
            f"{LABEL} replacement index does not name the exact {expected_types} "
            "constraint for those targets."
        )
    return record


def _main_plan(
    semantic_form: str,
    references: tuple[SketchConstraintElement, ...],
) -> TangentConstraintPlan:
    encoded = tuple(
        (element.geometry_index, element.position_code) for element in references
    )
    if semantic_form == "curve_curve":
        constructor = "Tangent", encoded[0][0], encoded[1][0]
        orientation = False
    elif semantic_form == "endpoint_curve":
        constructor = "Tangent", encoded[0][0], encoded[0][1], encoded[1][0]
        orientation = True
    elif semantic_form == "endpoint_endpoint":
        constructor = (
            "Tangent",
            encoded[0][0],
            encoded[0][1],
            encoded[1][0],
            encoded[1][1],
        )
        orientation = True
    else:
        constructor = (
            "TangentViaPoint",
            encoded[0][0],
            encoded[1][0],
            encoded[2][0],
            encoded[2][1],
        )
        orientation = True
    return TangentConstraintPlan(constructor, references, False, orientation)


def resolve_sketch_tangent(
    sketch: Any,
    spec: SketchTangentSpec,
) -> ResolvedSketchTangent:
    references = spec.target.selection
    form = spec.target_form
    replacement = form.startswith("replace_with_")
    semantic_form = form.removeprefix("replace_with_") if replacement else form
    if semantic_form == "curve_curve":
        first_type = _require_whole_curve(sketch, references[0], "first curve")
        second_type = _require_whole_curve(sketch, references[1], "second curve")
        if references[0].geometry_index == references[1].geometry_index:
            raise NativeSketchError(f"{LABEL} requires two distinct curves.")
        if _BSPLINE_TYPE in {first_type, second_type}:
            raise NativeSketchError(
                f"{LABEL} whole-curve B-splines require an explicit endpoint or "
                "curves_via_point form."
            )
        if first_type in _IMPLICIT_POINT_TYPES or second_type in _IMPLICIT_POINT_TYPES:
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
    elif semantic_form == "endpoint_curve":
        _require_endpoint(sketch, references[0], "endpoint")
        _require_whole_curve(sketch, references[1], "curve")
        if references[0].geometry_index == references[1].geometry_index:
            raise NativeSketchError(f"{LABEL} cannot target one curve against itself.")
        _require_editable(sketch, references)
        support = ()
    elif semantic_form == "endpoint_endpoint":
        _require_endpoint(sketch, references[0], "first endpoint")
        _require_endpoint(sketch, references[1], "second endpoint")
        if references[0].geometry_index == references[1].geometry_index:
            raise NativeSketchError(
                f"{LABEL} endpoint_endpoint requires two distinct curves."
            )
        _require_editable(sketch, references)
        support = ()
    else:
        _require_whole_curve(sketch, references[0], "first curve")
        _require_whole_curve(sketch, references[1], "second curve")
        _require_point(sketch, references[2], "point")
        if references[0].geometry_index == references[1].geometry_index:
            raise NativeSketchError(f"{LABEL} requires two distinct curves.")
        _require_editable(sketch, references[:2])
        support = tuple(
            plan
            for curve in references[:2]
            for plan in (_support_plan(sketch, references[2], curve),)
            if plan is not None
        )
    _refuse_existing_tangent(
        sketch, semantic_form, references, spec.replacement_index
    )
    replaced_record = None
    if replacement:
        replaced_record = _replacement_record(
            sketch, spec, semantic_form, references
        )
    else:
        _support_replacement_message(sketch, semantic_form, references)
    return ResolvedSketchTangent(
        form,
        semantic_form,
        references,
        (*support, _main_plan(semantic_form, references)),
        spec.replacement_index,
        replaced_record,
    )


def tangent_via_point_is_on_curves(
    sketch: Any,
    resolved: ResolvedSketchTangent,
) -> bool:
    if resolved.semantic_form != "curves_via_point":
        return True
    point = resolved.references[2]
    getter = getattr(sketch, "getPoint", None)
    query = getattr(sketch, "isPointOnCurve", None)
    if not callable(getter) or not callable(query):
        raise NativeSketchError(f"{LABEL} point-on-curve queries are unavailable.")
    try:
        value = getter(point.geometry_index, point.position_code)
        return all(
            bool(query(curve.geometry_index, float(value.x), float(value.y)))
            for curve in resolved.references[:2]
        )
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} point-on-curve query failed.") from exc


def make_tangent_constraints(
    resolved: ResolvedSketchTangent,
) -> tuple[Any, ...]:
    import Sketcher

    try:
        return tuple(Sketcher.Constraint(*plan.constructor) for plan in resolved.plans)
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact Tangent definition."
        ) from exc
