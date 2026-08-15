# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact point and open-curve targets for Native Sketch Symmetric."""

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


LABEL = "Sketch Symmetric"
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "target",
    }
)
_FORM_FIELDS = {
    "points_about_line": frozenset(
        {"form", "first_point", "second_point", "symmetry_line"}
    ),
    "points_about_point": frozenset(
        {"form", "first_point", "second_point", "symmetry_point"}
    ),
    "curve_about_line": frozenset({"form", "curve", "symmetry_line"}),
    "curve_about_point": frozenset({"form", "curve", "symmetry_point"}),
}
_LINE_TYPE = "Part::GeomLineSegment"
_BSPLINE_TYPE = "Part::GeomBSplineCurve"
_OPEN_CURVE_TYPES = frozenset(
    {
        _LINE_TYPE,
        "Part::GeomArcOfCircle",
        "Part::GeomArcOfEllipse",
        "Part::GeomArcOfHyperbola",
        "Part::GeomArcOfParabola",
        _BSPLINE_TYPE,
    }
)


@dataclass(frozen=True, slots=True)
class SketchSymmetricSpec:
    target: SketchConstraintTargetSpec
    target_form: str


@dataclass(frozen=True, slots=True)
class ResolvedSketchSymmetric:
    target_form: str
    references: tuple[
        SketchConstraintElement,
        SketchConstraintElement,
        SketchConstraintElement,
    ]
    reference_kind: str


def _target_selection(target: Any) -> tuple[str, list[Mapping[str, Any]]]:
    if not isinstance(target, Mapping):
        raise NativeSketchError(f"{LABEL} target must be an object.")
    form = target.get("form")
    if not isinstance(form, str) or form not in _FORM_FIELDS:
        raise NativeSketchError(f"{LABEL} target form is unsupported.")
    if set(target) != _FORM_FIELDS[form]:
        raise NativeSketchError(f"{LABEL} {form} target has incorrect fields.")
    if form == "curve_about_line" and target["curve"] == target["symmetry_line"]:
        raise NativeSketchError(f"{LABEL} curve cannot be its own symmetry line.")
    if form.startswith("points_"):
        selection = [target["first_point"], target["second_point"]]
    else:
        selection = [target["curve"]]
    selection.append(
        target["symmetry_line"] if form.endswith("_line") else target["symmetry_point"]
    )
    return form, selection


def prepare_sketch_symmetric_target(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchSymmetricSpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    form, selection = _target_selection(value["target"])
    return SketchSymmetricSpec(
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value["expected_external_geometry_count"],
            selection=selection,
        ),
        form,
    )


def _geometry_type(sketch: Any, element: SketchConstraintElement) -> str:
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    return str(getattr(geometry, "TypeId", "") or "")


def _require_point(
    sketch: Any,
    element: SketchConstraintElement,
    role: str,
) -> None:
    if element.position == "whole" or element.geometry_index == -2:
        raise NativeSketchError(f"{LABEL} {role} must be one exact point.")
    if element.geometry_index == -1 and element.position != "start":
        raise NativeSketchError(f"{LABEL} root point must use start position.")
    try:
        sketch.getPoint(element.geometry_index, element.position_code)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} {role} is unavailable.") from exc


def _require_line(
    sketch: Any,
    element: SketchConstraintElement,
) -> None:
    if element.position != "whole" or _geometry_type(sketch, element) != _LINE_TYPE:
        raise NativeSketchError(
            f"{LABEL} symmetry_line must be one whole straight line or Sketch axis."
        )


def _require_open_curve(
    sketch: Any,
    element: SketchConstraintElement,
) -> None:
    if element.position != "whole" or element.geometry_index in {-1, -2}:
        raise NativeSketchError(f"{LABEL} curve must be one whole non-axis open curve.")
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    type_id = str(getattr(geometry, "TypeId", "") or "")
    if type_id not in _OPEN_CURVE_TYPES:
        raise NativeSketchError(
            f"{LABEL} curve must be a line, open conic arc, or non-periodic B-spline."
        )
    if type_id == _BSPLINE_TYPE:
        periodic = getattr(geometry, "isPeriodic", None)
        try:
            if not callable(periodic) or bool(periodic()):
                raise NativeSketchError(f"{LABEL} curve cannot be a periodic B-spline.")
        except NativeSketchError:
            raise
        except Exception as exc:
            raise NativeSketchError(
                f"{LABEL} B-spline periodic state is unavailable."
            ) from exc
    for position in (1, 2):
        try:
            sketch.getPoint(element.geometry_index, position)
        except Exception as exc:
            raise NativeSketchError(
                f"{LABEL} curve does not expose two exact endpoints."
            ) from exc


def _blocked_geometry_indices(sketch: Any) -> frozenset[int]:
    try:
        constraints = tuple(sketch.Constraints)
        facades = tuple(sketch.GeometryFacadeList)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} geometry status is unavailable.") from exc
    blocked = {
        int(getattr(constraint, "First", -2000))
        for constraint in constraints
        if str(getattr(constraint, "Type", "") or "") == "Block"
    }
    blocked.update(
        index
        for index, facade in enumerate(facades)
        if bool(getattr(facade, "Blocked", False))
    )
    return frozenset(blocked)


def _refuse_all_fixed(
    sketch: Any,
    references: tuple[SketchConstraintElement, ...],
) -> None:
    blocked = _blocked_geometry_indices(sketch)
    if all(
        element.geometry_index < 0 or element.geometry_index in blocked
        for element in references
    ):
        raise NativeSketchError(f"{LABEL} targets are all fixed or external.")


def _constraint_key(constraint: Any) -> tuple[Any, ...] | None:
    if str(getattr(constraint, "Type", "") or "") != "Symmetric":
        return None
    first = (
        int(getattr(constraint, "First", -2000)),
        int(getattr(constraint, "FirstPos", 0)),
    )
    second = (
        int(getattr(constraint, "Second", -2000)),
        int(getattr(constraint, "SecondPos", 0)),
    )
    third = int(getattr(constraint, "Third", -2000))
    third_pos = int(getattr(constraint, "ThirdPos", 0))
    if not first[1] or not second[1] or third <= -2000:
        return None
    reference = ("point", third, third_pos) if third_pos else ("line", third)
    return frozenset((first, second)), reference


def _resolved_key(
    references: tuple[
        SketchConstraintElement,
        SketchConstraintElement,
        SketchConstraintElement,
    ],
    reference_kind: str,
) -> tuple[Any, ...]:
    first, second, reference = references
    encoded_reference = (
        ("line", reference.geometry_index)
        if reference_kind == "line"
        else ("point", reference.geometry_index, reference.position_code)
    )
    return (
        frozenset(
            (
                (first.geometry_index, first.position_code),
                (second.geometry_index, second.position_code),
            )
        ),
        encoded_reference,
    )


def _refuse_existing(
    sketch: Any,
    resolved: ResolvedSketchSymmetric,
) -> None:
    expected = _resolved_key(resolved.references, resolved.reference_kind)
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraints are unavailable.") from exc
    if any(_constraint_key(constraint) == expected for constraint in constraints):
        raise NativeSketchError(
            f"{LABEL} targets already have that Symmetric constraint."
        )


def resolve_sketch_symmetric(
    sketch: Any,
    spec: SketchSymmetricSpec,
) -> ResolvedSketchSymmetric:
    if not isinstance(spec, SketchSymmetricSpec):
        raise TypeError("spec must be a SketchSymmetricSpec")
    selection = spec.target.selection
    reference_kind = "line" if spec.target_form.endswith("_line") else "point"
    if spec.target_form.startswith("points_"):
        first, second, reference = selection
        _require_point(sketch, first, "first_point")
        _require_point(sketch, second, "second_point")
    else:
        curve, reference = selection
        _require_open_curve(sketch, curve)
        first = SketchConstraintElement(curve.geometry_index, "start")
        second = SketchConstraintElement(curve.geometry_index, "end")
    if reference_kind == "line":
        _require_line(sketch, reference)
    else:
        _require_point(sketch, reference, "symmetry_point")
    if spec.target_form == "curve_about_line" and (
        first.geometry_index == reference.geometry_index
    ):
        raise NativeSketchError(f"{LABEL} curve cannot be its own symmetry line.")
    if spec.target_form == "curve_about_point" and (
        first.geometry_index == reference.geometry_index
        and reference.position in {"start", "end"}
    ):
        raise NativeSketchError(
            f"{LABEL} curve cannot use one of its own endpoints as symmetry_point."
        )
    if (
        spec.target_form == "points_about_line"
        and len({first.geometry_index, second.geometry_index, reference.geometry_index})
        == 1
    ):
        raise NativeSketchError(
            f"{LABEL} cannot mirror one line's endpoints about that same line."
        )
    references = (first, second, reference)
    _refuse_all_fixed(sketch, references)
    resolved = ResolvedSketchSymmetric(
        spec.target_form,
        references,
        reference_kind,
    )
    _refuse_existing(sketch, resolved)
    return resolved


def make_symmetric_constraint(resolved: ResolvedSketchSymmetric) -> Any:
    if not isinstance(resolved, ResolvedSketchSymmetric):
        raise TypeError("resolved must be a ResolvedSketchSymmetric")
    import Sketcher

    first, second, reference = resolved.references
    arguments = (
        "Symmetric",
        first.geometry_index,
        first.position_code,
        second.geometry_index,
        second.position_code,
        reference.geometry_index,
    )
    if resolved.reference_kind == "point":
        arguments += (reference.position_code,)
    try:
        return Sketcher.Constraint(*arguments)
    except Exception as exc:
        raise NativeSketchError(
            f"{LABEL} exact constraint definition was rejected."
        ) from exc
