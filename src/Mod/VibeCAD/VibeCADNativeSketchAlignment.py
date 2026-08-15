# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact Horizontal and Vertical alignment lifecycle for an open Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import (
    ExactConstraintExpectation,
    add_exact_constraint,
    diagnose_exact_constraint,
    sketch_solver_issues,
    verify_exact_constraint_appends,
)
from VibeCADNativeSketchConstraintTargets import (
    PreparedSketchConstraintTarget,
    SketchConstraintElement,
    SketchConstraintTargetSpec,
    current_sketch_constraint_records,
    preflight_sketch_constraint_target,
    prepare_sketch_constraint_target,
    require_unchanged_sketch_constraint_target,
    sketch_constraint_geometry,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


_BASE_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
    }
)
_INFERENCES = frozenset({"horizontal", "vertical"})
_LINE_TYPE = "Part::GeomLineSegment"
_LINEAR_TOLERANCE = 1.0e-7
_DIAGONAL_TOLERANCE = 1.0e-10


@dataclass(frozen=True, slots=True)
class SketchAlignmentDefinition:
    title: str
    operation: str
    inference: str | None

    def __post_init__(self) -> None:
        identity = self.title, self.operation, self.inference
        if identity not in {
            (
                "automatic Horizontal/Vertical",
                "constrain_horizontal_vertical",
                None,
            ),
            ("Horizontal", "constrain_horizontal", "horizontal"),
            ("Vertical", "constrain_vertical", "vertical"),
        }:
            raise ValueError("Invalid Sketch alignment definition.")

    @property
    def label(self) -> str:
        return f"Sketch {self.title}"

    @property
    def automatic(self) -> bool:
        return self.inference is None


AUTOMATIC_ALIGNMENT = SketchAlignmentDefinition(
    "automatic Horizontal/Vertical",
    "constrain_horizontal_vertical",
    None,
)
HORIZONTAL_ALIGNMENT = SketchAlignmentDefinition(
    "Horizontal",
    "constrain_horizontal",
    "horizontal",
)
VERTICAL_ALIGNMENT = SketchAlignmentDefinition(
    "Vertical",
    "constrain_vertical",
    "vertical",
)


@dataclass(frozen=True, slots=True)
class SketchAlignmentSpec:
    definition: SketchAlignmentDefinition
    target: SketchConstraintTargetSpec
    expected_inference: str | None


@dataclass(frozen=True, slots=True)
class ResolvedSketchAlignment:
    target_form: str
    inference: str
    references: tuple[SketchConstraintElement, ...]
    delta_before_mm: tuple[float, float]


@dataclass(frozen=True, slots=True)
class PreparedSketchAlignment:
    target: PreparedSketchConstraintTarget
    spec: SketchAlignmentSpec
    resolved: ResolvedSketchAlignment
    solver_issues: tuple[tuple[int, ...], ...]


def prepare_sketch_alignment(
    document_uid: str,
    value: Mapping[str, Any],
    definition: SketchAlignmentDefinition,
) -> SketchAlignmentSpec:
    if not isinstance(definition, SketchAlignmentDefinition):
        raise TypeError("definition must be a SketchAlignmentDefinition")
    fields = _BASE_FIELDS | ({"expected_inference"} if definition.automatic else set())
    if not isinstance(value, Mapping) or set(value) != fields:
        raise NativeSketchError(f"A {definition.label} definition has incorrect fields.")
    expected_inference = None
    if definition.automatic:
        expected_inference = value["expected_inference"]
        if (
            not isinstance(expected_inference, str)
            or expected_inference not in _INFERENCES
        ):
            raise NativeSketchError(
                f"{definition.label} expected_inference must be horizontal or vertical."
            )
    selection = value["selection"]
    if not isinstance(selection, list) or len(selection) not in {1, 2}:
        raise NativeSketchError(
            f"{definition.label} selection must contain one whole line or two exact "
            "points."
        )
    return SketchAlignmentSpec(
        definition,
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
        expected_inference,
    )


def _point(
    sketch: Any,
    element: SketchConstraintElement,
    *,
    role: str,
    definition: SketchAlignmentDefinition,
) -> tuple[float, float]:
    if element.position == "whole":
        raise NativeSketchError(
            f"{definition.label} {role} must be one exact point."
        )
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError(f"{definition.label} point lookup is unavailable.")
    try:
        value = getter(element.geometry_index, element.position_code)
        x = float(value.x)
        y = float(value.y)
    except Exception as exc:
        raise NativeSketchError(
            f"{definition.label} {role} is unavailable."
        ) from exc
    if not math.isfinite(x) or not math.isfinite(y):
        raise NativeSketchError(f"{definition.label} {role} is not finite.")
    return x, y


def _delta(
    first: tuple[float, float],
    second: tuple[float, float],
) -> tuple[float, float]:
    return second[0] - first[0], second[1] - first[1]


def _require_nonzero(
    delta: tuple[float, float],
    definition: SketchAlignmentDefinition,
) -> float:
    length = math.hypot(*delta)
    if length <= _LINEAR_TOLERANCE:
        raise NativeSketchError(
            f"{definition.label} cannot constrain coincident points or a zero-length "
            "line."
        )
    return length


def _infer(
    delta: tuple[float, float],
    definition: SketchAlignmentDefinition,
) -> str:
    length = _require_nonzero(delta, definition)
    delta_x, delta_y = delta
    diagonal_distance = abs(abs(delta_x) - abs(delta_y)) / length
    if diagonal_distance <= _DIAGONAL_TOLERANCE:
        raise NativeSketchError(
            f"{definition.label} target is diagonally ambiguous; use explicit "
            "Horizontal or Vertical."
        )
    return "horizontal" if abs(delta_x) > abs(delta_y) else "vertical"


def _line_points(
    sketch: Any,
    element: SketchConstraintElement,
    definition: SketchAlignmentDefinition,
) -> tuple[tuple[float, float], tuple[float, float]]:
    return (
        _point(
            sketch,
            SketchConstraintElement(element.geometry_index, "start"),
            role="line start",
            definition=definition,
        ),
        _point(
            sketch,
            SketchConstraintElement(element.geometry_index, "end"),
            role="line end",
            definition=definition,
        ),
    )


def _refuse_existing_line_constraint(
    sketch: Any,
    geometry_index: int,
    definition: SketchAlignmentDefinition,
) -> None:
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(
            f"{definition.label} constraints are unavailable."
        ) from exc
    for constraint in constraints:
        if (
            str(getattr(constraint, "Type", "") or "")
            in {"Horizontal", "Vertical", "Block"}
            and int(getattr(constraint, "First", -2000)) == geometry_index
            and int(getattr(constraint, "FirstPos", 0)) == 0
        ):
            raise NativeSketchError(
                f"{definition.label} line already has a Horizontal, Vertical, or "
                "Block constraint."
            )


def _resolve_alignment(
    sketch: Any,
    spec: SketchAlignmentSpec,
) -> ResolvedSketchAlignment:
    definition = spec.definition
    selection = spec.target.selection
    if len(selection) == 1:
        line = selection[0]
        if line.position != "whole":
            raise NativeSketchError(
                f"{definition.label} line form requires one whole line."
            )
        if line.geometry_index < 0:
            raise NativeSketchError(
                f"{definition.label} cannot constrain a fixed axis or edge."
            )
        geometry = sketch_constraint_geometry(sketch, line.geometry_index)
        if str(getattr(geometry, "TypeId", "") or "") != _LINE_TYPE:
            raise NativeSketchError(
                f"{definition.label} line form requires a straight line."
            )
        _refuse_existing_line_constraint(sketch, line.geometry_index, definition)
        references = (line,)
        delta = _delta(*_line_points(sketch, line, definition))
        target_form = "line"
    elif len(selection) == 2:
        first, second = selection
        first_point = _point(
            sketch,
            first,
            role="first point",
            definition=definition,
        )
        second_point = _point(
            sketch,
            second,
            role="second point",
            definition=definition,
        )
        references = (first, second)
        delta = _delta(first_point, second_point)
        target_form = "point_pair"
    else:
        raise NativeSketchError(
            f"{definition.label} requires one whole line or two exact points."
        )
    if definition.automatic:
        inference = _infer(delta, definition)
        if inference != spec.expected_inference:
            raise NativeSketchError(
                f"{definition.label} inferred {inference}, not the expected "
                f"{spec.expected_inference}; read the current Sketch and retry."
            )
    else:
        _require_nonzero(delta, definition)
        assert definition.inference is not None
        inference = definition.inference
    return ResolvedSketchAlignment(target_form, inference, references, delta)


def _constraint_arguments(resolved: ResolvedSketchAlignment) -> tuple[Any, ...]:
    constraint_type = resolved.inference.capitalize()
    if resolved.target_form == "line":
        return constraint_type, resolved.references[0].geometry_index
    first, second = resolved.references
    return (
        constraint_type,
        first.geometry_index,
        first.position_code,
        second.geometry_index,
        second.position_code,
    )


def _constraint(
    resolved: ResolvedSketchAlignment,
    definition: SketchAlignmentDefinition,
) -> Any:
    import Sketcher

    try:
        return Sketcher.Constraint(*_constraint_arguments(resolved))
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected the exact {definition.title} definition."
        ) from exc


def preflight_sketch_alignment(
    context: NativeRuntimeContext,
    spec: SketchAlignmentSpec,
) -> PreparedSketchAlignment:
    if not isinstance(spec, SketchAlignmentSpec):
        raise TypeError("spec must be a SketchAlignmentSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = _resolve_alignment(sketch, spec)
    solver_issues = sketch_solver_issues(sketch, spec.definition.label)
    diagnose_exact_constraint(
        sketch,
        _constraint(resolved, spec.definition),
        expected_index=spec.target.target.expected_constraint_count,
        label=spec.definition.label,
    )
    geometry, constraints, external = current_sketch_constraint_records(
        sketch,
        spec.target,
    )
    if (
        geometry != target.geometry_records
        or constraints != target.constraint_records
        or external != target.external_geometry_records
        or sketch_solver_issues(sketch, spec.definition.label) != solver_issues
    ):
        raise NativeSketchError(
            f"{spec.definition.label} feasibility check changed the active Sketch."
        )
    return PreparedSketchAlignment(target, spec, resolved, solver_issues)


def create_sketch_alignment(
    document: Any,
    prepared: PreparedSketchAlignment,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchAlignment):
        raise TypeError("prepared must be a PreparedSketchAlignment")
    definition = prepared.spec.definition
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage=f"after {definition.title} preflight",
    )
    index = add_exact_constraint(
        sketch,
        _constraint(prepared.resolved, definition),
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label=f"{definition.title} constraint",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _expected_references(
    resolved: ResolvedSketchAlignment,
) -> tuple[Mapping[str, Any], ...]:
    result = []
    for slot, element in enumerate(resolved.references, start=1):
        reference: dict[str, Any] = {
            "slot": slot,
            "geometry_index": element.geometry_index,
        }
        if element.position_code:
            reference["position"] = element.position_code
        result.append(reference)
    return tuple(result)


def _current_delta(
    sketch: Any,
    prepared: PreparedSketchAlignment,
) -> tuple[float, float]:
    resolved = prepared.resolved
    definition = prepared.spec.definition
    if resolved.target_form == "line":
        return _delta(*_line_points(sketch, resolved.references[0], definition))
    return _delta(
        _point(
            sketch,
            resolved.references[0],
            role="first point",
            definition=definition,
        ),
        _point(
            sketch,
            resolved.references[1],
            role="second point",
            definition=definition,
        ),
    )


def _measurement(delta: tuple[float, float]) -> dict[str, Any]:
    return {"delta_x": delta[0], "delta_y": delta[1], "unit": "mm"}


def verify_sketch_alignment(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchAlignment):
        raise TypeError("draft must contain a PreparedSketchAlignment")
    definition = prepared.spec.definition
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    constraint = verify_exact_constraint_appends(
        sketch,
        prepared.target,
        constraint_indices=(int(draft.value["constraint_index"]),),
        solver_issues=prepared.solver_issues,
        expectations=(
            ExactConstraintExpectation(
                prepared.resolved.inference.capitalize(),
                _expected_references(prepared.resolved),
                True,
                None,
                0.0,
            ),
        ),
        label=definition.label,
    )[0]
    delta_after = _current_delta(sketch, prepared)
    residual = (
        abs(delta_after[1])
        if prepared.resolved.inference == "horizontal"
        else abs(delta_after[0])
    )
    if residual > _LINEAR_TOLERANCE:
        raise NativeSketchError(
            f"{definition.label} solver result does not satisfy its exact alignment."
        )
    details: dict[str, Any] = {
        "operation": definition.operation,
        "target_form": prepared.resolved.target_form,
        "constraint": constraint,
        "measured_before": _measurement(prepared.resolved.delta_before_mm),
        "measured_after": _measurement(delta_after),
    }
    if definition.automatic:
        details["inference"] = prepared.resolved.inference
    else:
        details["alignment"] = prepared.resolved.inference
    return sketch_geometry_result(sketch, details)
