# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed ordered targets for Native Sketch Equal constraints."""

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


LABEL = "Sketch Equal"
MAX_EQUAL_TARGETS = 17
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
    }
)
_BSPLINE_CONTROL_POINT = "BSplineControlPoint"
_LINE = "Part::GeomLineSegment"
_CIRCULAR = frozenset({"Part::GeomCircle", "Part::GeomArcOfCircle"})
_ELLIPTIC = frozenset({"Part::GeomEllipse", "Part::GeomArcOfEllipse"})
_HYPERBOLIC = "Part::GeomArcOfHyperbola"
_PARABOLIC = "Part::GeomArcOfParabola"
_BSPLINE = "Part::GeomBSplineCurve"


@dataclass(frozen=True, slots=True)
class SketchEqualSpec:
    target: SketchConstraintTargetSpec


@dataclass(frozen=True, slots=True)
class BSplineWeightLink:
    spline_index: int
    pole_index: int


@dataclass(frozen=True, slots=True)
class EqualConstraintPlan:
    references: tuple[SketchConstraintElement, SketchConstraintElement]


@dataclass(frozen=True, slots=True)
class ResolvedSketchEqual:
    references: tuple[SketchConstraintElement, ...]
    family: str
    plans: tuple[EqualConstraintPlan, ...]
    weight_links: tuple[BSplineWeightLink | None, ...]


def prepare_sketch_equal_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchEqualSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    selection = value["selection"]
    if not isinstance(selection, list) or not 2 <= len(selection) <= MAX_EQUAL_TARGETS:
        raise NativeSketchError(
            f"{LABEL} selection must contain two through {MAX_EQUAL_TARGETS} edges."
        )
    return SketchEqualSpec(
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value["expected_external_geometry_count"],
            selection=selection,
            maximum_selection=MAX_EQUAL_TARGETS,
            allowed_internal_types=frozenset({_BSPLINE_CONTROL_POINT}),
        )
    )


def _internal_type(sketch: Any, index: int) -> str:
    if index < 0:
        return ""
    try:
        facade = sketch.GeometryFacadeList[index]
        return str(getattr(facade, "InternalType", "") or "")
    except Exception as exc:
        raise NativeSketchError(
            f"{LABEL} geometry {index} facade is unavailable."
        ) from exc


def _family(sketch: Any, element: SketchConstraintElement) -> str:
    if element.position != "whole":
        raise NativeSketchError(f"{LABEL} targets must be exact whole edges.")
    if element.geometry_index in {-1, -2}:
        raise NativeSketchError(f"{LABEL} cannot target Sketch axes.")
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    type_id = str(getattr(geometry, "TypeId", "") or "")
    internal_type = _internal_type(sketch, element.geometry_index)
    if internal_type == _BSPLINE_CONTROL_POINT:
        if type_id != "Part::GeomCircle":
            raise NativeSketchError(
                f"{LABEL} B-spline control-point weights must be circle handles."
            )
        return "b_spline_weight"
    if type_id == _BSPLINE:
        raise NativeSketchError(
            f"{LABEL} does not support whole B-spline curves; target their "
            "control-point weight handles instead."
        )
    if type_id == _LINE:
        return "line_length"
    if type_id in _CIRCULAR:
        return "circular_radius"
    if type_id in _ELLIPTIC:
        return "elliptic_radii"
    if type_id == _HYPERBOLIC:
        return "hyperbolic_radii"
    if type_id == _PARABOLIC:
        return "parabolic_focal_length"
    raise NativeSketchError(
        f"{LABEL} targets must be supported lines, conics, or B-spline "
        "control-point weight handles."
    )


def _blocked_geometry_indices(sketch: Any) -> frozenset[int]:
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc
    result = {
        int(getattr(constraint, "First", -2000))
        for constraint in constraints
        if str(getattr(constraint, "Type", "") or "") == "Block"
    }
    try:
        result.update(
            index
            for index, facade in enumerate(sketch.GeometryFacadeList)
            if bool(getattr(facade, "Blocked", False))
        )
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} geometry status is unavailable.") from exc
    return frozenset(result)


def _refuse_multiple_fixed(
    sketch: Any,
    references: tuple[SketchConstraintElement, ...],
) -> None:
    blocked = _blocked_geometry_indices(sketch)
    fixed_count = sum(
        element.geometry_index < 0 or element.geometry_index in blocked
        for element in references
    )
    if fixed_count > 1:
        raise NativeSketchError(
            f"{LABEL} permits at most one fixed or external target."
        )


def _weight_link(sketch: Any, geometry_index: int) -> BSplineWeightLink:
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc
    links = []
    for constraint in constraints:
        if (
            str(getattr(constraint, "Type", "") or "") != "InternalAlignment"
            or int(getattr(constraint, "First", -2000)) != geometry_index
            or int(getattr(constraint, "FirstPos", 0)) != 3
        ):
            continue
        spline_index = int(getattr(constraint, "Second", -2000))
        try:
            spline = sketch_constraint_geometry(sketch, spline_index)
        except NativeSketchError:
            continue
        if str(getattr(spline, "TypeId", "") or "") != _BSPLINE:
            continue
        raw_pole = getattr(
            constraint,
            "InternalAlignmentIndex",
            getattr(constraint, "SecondPos", -1),
        )
        try:
            pole_index = int(raw_pole)
            weights = tuple(float(value) for value in spline.getWeights())
        except Exception as exc:
            raise NativeSketchError(
                f"{LABEL} B-spline pole weight is unavailable."
            ) from exc
        if not 0 <= pole_index < len(weights) or not math.isfinite(weights[pole_index]):
            raise NativeSketchError(f"{LABEL} B-spline pole link is malformed.")
        links.append(BSplineWeightLink(spline_index, pole_index))
    if len(links) != 1:
        raise NativeSketchError(
            f"{LABEL} B-spline weight handle must have one exact spline owner."
        )
    return links[0]


def _refuse_existing_equal(
    sketch: Any,
    plans: tuple[EqualConstraintPlan, ...],
) -> None:
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc
    adjacency: dict[int, set[int]] = {}
    direct = set()
    for constraint in constraints:
        if str(getattr(constraint, "Type", "") or "") != "Equal":
            continue
        first = int(getattr(constraint, "First", -2000))
        second = int(getattr(constraint, "Second", -2000))
        direct.add(frozenset({first, second}))
        adjacency.setdefault(first, set()).add(second)
        adjacency.setdefault(second, set()).add(first)

    def connected(first: int, second: int) -> bool:
        pending = [first]
        visited = set()
        while pending:
            current = pending.pop()
            if current == second:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend(adjacency.get(current, set()) - visited)
        return False

    for plan in plans:
        pair = frozenset(element.geometry_index for element in plan.references)
        first, second = plan.references
        if pair in direct:
            raise NativeSketchError(
                f"{LABEL} targets {first.geometry_index} and "
                f"{second.geometry_index} already have an Equal constraint."
            )
        if connected(first.geometry_index, second.geometry_index):
            raise NativeSketchError(
                f"{LABEL} targets {first.geometry_index} and "
                f"{second.geometry_index} are already connected by Equal constraints."
            )


def resolve_sketch_equal(sketch: Any, spec: SketchEqualSpec) -> ResolvedSketchEqual:
    if not isinstance(spec, SketchEqualSpec):
        raise TypeError("spec must be a SketchEqualSpec")
    references = spec.target.selection
    families = tuple(_family(sketch, element) for element in references)
    if len(set(families)) != 1:
        raise NativeSketchError(
            f"{LABEL} targets must all belong to the same compatible family."
        )
    _refuse_multiple_fixed(sketch, references)
    plans = tuple(
        EqualConstraintPlan((first, second))
        for first, second in zip(references, references[1:], strict=False)
    )
    _refuse_existing_equal(sketch, plans)
    weight_links = tuple(
        _weight_link(sketch, element.geometry_index)
        if families[0] == "b_spline_weight"
        else None
        for element in references
    )
    return ResolvedSketchEqual(references, families[0], plans, weight_links)


def make_equal_constraints(resolved: ResolvedSketchEqual) -> tuple[Any, ...]:
    if not isinstance(resolved, ResolvedSketchEqual):
        raise TypeError("resolved must be a ResolvedSketchEqual")
    import Sketcher

    result = []
    for plan in resolved.plans:
        first, second = plan.references
        try:
            result.append(
                Sketcher.Constraint(
                    "Equal",
                    first.geometry_index,
                    second.geometry_index,
                )
            )
        except Exception as exc:
            raise NativeSketchError(
                "Sketcher rejected the exact Equal definition."
            ) from exc
    return tuple(result)
