# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact horizontal and vertical Distance constraints for an open Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchConstraintAppend import (
    add_exact_constraint,
    diagnose_exact_constraint,
    make_dimensional_constraint,
    sketch_solver_issues,
    verify_exact_constraint_append,
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


_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "expected_external_geometry_count",
        "selection",
        "dimension",
        "driving",
    }
)
_DIMENSION_FIELDS = frozenset({"value", "unit"})
_LINE_TYPES = frozenset({"Part::GeomLineSegment"})
_TOLERANCE = 1.0e-7
_MAX_ABSOLUTE_DIMENSION = 1_000_000.0


@dataclass(frozen=True, slots=True)
class SketchAxisDistanceDefinition:
    axis: str
    title: str
    constraint_type: str
    operation: str
    equal_coordinate_constraint: str

    def __post_init__(self) -> None:
        identity = (
            self.axis,
            self.constraint_type,
            self.operation,
            self.equal_coordinate_constraint,
        )
        if identity not in {
            ("x", "DistanceX", "constrain_distance_x", "Vertical"),
            ("y", "DistanceY", "constrain_distance_y", "Horizontal"),
        }:
            raise ValueError("Invalid Sketch axis-distance definition.")

    @property
    def coordinate_index(self) -> int:
        return 0 if self.axis == "x" else 1

    @property
    def label(self) -> str:
        return f"Sketch {self.title}"


HORIZONTAL_DISTANCE = SketchAxisDistanceDefinition(
    axis="x",
    title="Horizontal Distance",
    constraint_type="DistanceX",
    operation="constrain_distance_x",
    equal_coordinate_constraint="Vertical",
)
VERTICAL_DISTANCE = SketchAxisDistanceDefinition(
    axis="y",
    title="Vertical Distance",
    constraint_type="DistanceY",
    operation="constrain_distance_y",
    equal_coordinate_constraint="Horizontal",
)


@dataclass(frozen=True, slots=True)
class SketchAxisDistanceSpec:
    definition: SketchAxisDistanceDefinition
    target: SketchConstraintTargetSpec
    dimension_mm: float
    driving: bool


@dataclass(frozen=True, slots=True)
class ResolvedAxisDistance:
    target_form: str
    references: tuple[SketchConstraintElement, ...]
    measured_value: float


@dataclass(frozen=True, slots=True)
class PreparedSketchAxisDistance:
    target: PreparedSketchConstraintTarget
    spec: SketchAxisDistanceSpec
    resolved: ResolvedAxisDistance
    solver_issues: tuple[tuple[int, ...], ...]


def _dimension(value: Any, definition: SketchAxisDistanceDefinition) -> float:
    if not isinstance(value, Mapping) or set(value) != _DIMENSION_FIELDS:
        raise NativeSketchError(f"{definition.label} dimension has incorrect fields.")
    if value["unit"] != "mm":
        raise NativeSketchError(f"{definition.label} requires unit mm.")
    raw = value["value"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise NativeSketchError(f"{definition.label} value must be a number.")
    result = float(raw)
    if not math.isfinite(result) or abs(result) > _MAX_ABSOLUTE_DIMENSION:
        raise NativeSketchError(
            f"{definition.label} value must be finite and from "
            f"-{_MAX_ABSOLUTE_DIMENSION} to {_MAX_ABSOLUTE_DIMENSION} mm."
        )
    return result


def prepare_sketch_axis_distance(
    document_uid: str,
    value: Mapping[str, Any],
    definition: SketchAxisDistanceDefinition,
) -> SketchAxisDistanceSpec:
    if not isinstance(definition, SketchAxisDistanceDefinition):
        raise TypeError("definition must be a SketchAxisDistanceDefinition")
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(
            f"A {definition.label} definition has incorrect fields."
        )
    driving = value["driving"]
    if type(driving) is not bool:
        raise NativeSketchError(f"{definition.label} driving must be a boolean.")
    return SketchAxisDistanceSpec(
        definition,
        prepare_sketch_constraint_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
            expected_external_geometry_count=value[
                "expected_external_geometry_count"
            ],
            selection=value["selection"],
        ),
        _dimension(value["dimension"], definition),
        driving,
    )


def _point(
    sketch: Any,
    element: SketchConstraintElement,
    definition: SketchAxisDistanceDefinition,
) -> tuple[float, float]:
    getter = getattr(sketch, "getPoint", None)
    if not callable(getter):
        raise NativeSketchError(f"{definition.label} point lookup is unavailable.")
    try:
        point = getter(element.geometry_index, element.position_code)
        coordinates = (float(point.x), float(point.y))
    except Exception as exc:
        raise NativeSketchError(f"{definition.label} exact point is unavailable.") from exc
    if not all(math.isfinite(coordinate) for coordinate in coordinates):
        raise NativeSketchError(f"{definition.label} point is not finite.")
    return coordinates


def _normalized_points(
    sketch: Any,
    first: SketchConstraintElement,
    second: SketchConstraintElement,
    definition: SketchAxisDistanceDefinition,
) -> ResolvedAxisDistance:
    first_coordinate = _point(sketch, first, definition)[definition.coordinate_index]
    second_coordinate = _point(sketch, second, definition)[definition.coordinate_index]
    measured = second_coordinate - first_coordinate
    if abs(measured) <= _TOLERANCE:
        coordinate = definition.axis.upper()
        raise NativeSketchError(
            f"{definition.label} points have the same {coordinate} coordinate; use "
            f"the {definition.equal_coordinate_constraint} geometric constraint instead."
        )
    references = (first, second)
    if measured < 0.0:
        references = (second, first)
        measured = -measured
    return ResolvedAxisDistance("point_to_point", references, measured)


def _resolve_axis_distance(
    sketch: Any,
    selection: tuple[SketchConstraintElement, ...],
    definition: SketchAxisDistanceDefinition,
) -> ResolvedAxisDistance:
    if len(selection) == 2:
        if any(element.position == "whole" for element in selection):
            raise NativeSketchError(
                f"{definition.label} two-element selection must contain two exact points."
            )
        return _normalized_points(
            sketch,
            selection[0],
            selection[1],
            definition,
        )

    element = selection[0]
    if element.position == "whole":
        if element.geometry_index in {-1, -2}:
            raise NativeSketchError(
                f"{definition.label} cannot constrain an axis as a line."
            )
        geometry = sketch_constraint_geometry(sketch, element.geometry_index)
        if str(getattr(geometry, "TypeId", "") or "") not in _LINE_TYPES:
            raise NativeSketchError(
                f"{definition.label} whole-geometry selection requires one line segment."
            )
        return _normalized_points(
            sketch,
            SketchConstraintElement(element.geometry_index, "start"),
            SketchConstraintElement(element.geometry_index, "end"),
            definition,
        )
    if element.geometry_index == -1 and element.position == "start":
        raise NativeSketchError(f"{definition.label} cannot constrain the origin to itself.")
    coordinate = _point(sketch, element, definition)[definition.coordinate_index]
    return ResolvedAxisDistance("point_coordinate", (element,), coordinate)


def _constraint_arguments(prepared: PreparedSketchAxisDistance) -> tuple[Any, ...]:
    references = prepared.resolved.references
    value = prepared.spec.dimension_mm
    constraint_type = prepared.spec.definition.constraint_type
    if prepared.resolved.target_form == "point_coordinate":
        point = references[0]
        return (constraint_type, point.geometry_index, point.position_code, value)
    first, second = references
    return (
        constraint_type,
        first.geometry_index,
        first.position_code,
        second.geometry_index,
        second.position_code,
        value,
    )


def _expected_references(
    resolved: ResolvedAxisDistance,
) -> list[dict[str, Any]]:
    return [
        {
            "slot": slot,
            "geometry_index": element.geometry_index,
            "position": element.position_code,
        }
        for slot, element in enumerate(resolved.references, start=1)
    ]


def preflight_sketch_axis_distance(
    context: NativeRuntimeContext,
    spec: SketchAxisDistanceSpec,
) -> PreparedSketchAxisDistance:
    if not isinstance(spec, SketchAxisDistanceSpec):
        raise TypeError("spec must be a SketchAxisDistanceSpec")
    definition = spec.definition
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = _resolve_axis_distance(sketch, spec.target.selection, definition)
    if resolved.target_form == "point_to_point" and spec.dimension_mm <= _TOLERANCE:
        raise NativeSketchError(
            f"Sketch point-to-point {definition.title} must be greater than zero."
        )
    if not spec.driving and not math.isclose(
        resolved.measured_value,
        spec.dimension_mm,
        rel_tol=1.0e-9,
        abs_tol=_TOLERANCE,
    ):
        raise NativeSketchError(
            f"Sketch reference {definition.title} measurement changed; read the "
            "current Sketch and retry."
        )
    solver_issues = sketch_solver_issues(sketch, definition.label)
    prepared = PreparedSketchAxisDistance(target, spec, resolved, solver_issues)
    constraint = make_dimensional_constraint(
        _constraint_arguments(prepared),
        driving=spec.driving,
    )
    diagnose_exact_constraint(
        sketch,
        constraint,
        expected_index=spec.target.target.expected_constraint_count,
        label=definition.label,
    )
    geometry, constraints, external = current_sketch_constraint_records(
        sketch,
        spec.target,
    )
    if (
        geometry != target.geometry_records
        or constraints != target.constraint_records
        or external != target.external_geometry_records
        or sketch_solver_issues(sketch, definition.label) != solver_issues
    ):
        raise NativeSketchError(
            f"{definition.label} feasibility check changed the active Sketch."
        )
    return prepared


def create_sketch_axis_distance(
    document: Any,
    prepared: PreparedSketchAxisDistance,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchAxisDistance):
        raise TypeError("prepared must be a PreparedSketchAxisDistance")
    definition = prepared.spec.definition
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage=f"after {definition.title} preflight",
    )
    constraint = make_dimensional_constraint(
        _constraint_arguments(prepared),
        driving=prepared.spec.driving,
    )
    index = add_exact_constraint(
        sketch,
        constraint,
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label=definition.title,
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_axis_distance(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSketchAxisDistance = draft.value["prepared"]
    definition = prepared.spec.definition
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    constraint = verify_exact_constraint_append(
        sketch,
        prepared.target,
        constraint_index=int(draft.value["constraint_index"]),
        solver_issues=prepared.solver_issues,
        constraint_type=definition.constraint_type,
        references=_expected_references(prepared.resolved),
        driving=prepared.spec.driving,
        value=prepared.spec.dimension_mm,
        tolerance=_TOLERANCE,
        label=definition.label,
    )
    measured_after = _resolve_axis_distance(
        sketch,
        prepared.spec.target.selection,
        definition,
    )
    if (
        measured_after.target_form != prepared.resolved.target_form
        or not math.isclose(
            measured_after.measured_value,
            prepared.spec.dimension_mm,
            rel_tol=1.0e-9,
            abs_tol=_TOLERANCE,
        )
    ):
        raise NativeSketchError(
            f"{definition.label} solver result does not satisfy its exact value."
        )
    return sketch_geometry_result(
        sketch,
        {
            "operation": definition.operation,
            "target_form": prepared.resolved.target_form,
            "constraint": constraint,
            "measured_before": {
                "value": prepared.resolved.measured_value,
                "unit": "mm",
            },
            "measured_after": {
                "value": measured_after.measured_value,
                "unit": "mm",
            },
        },
    )
