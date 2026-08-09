# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact lifecycle for Radius and Diameter Sketch constraints."""

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


_BASE_FIELDS = frozenset(
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
_EXPECTED_FIELD = "expected_constraint"
_DIMENSION_FIELDS = frozenset({"value", "unit"})
_EXPECTED_CONSTRAINTS = frozenset({"radius", "diameter"})
_CIRCLE_TYPE = "Part::GeomCircle"
_ARC_TYPE = "Part::GeomArcOfCircle"
_TOLERANCE = 1.0e-7
_MAX_DIMENSION = 1_000_000.0


@dataclass(frozen=True, slots=True)
class SketchCircularSizeMode:
    label: str
    operation: str
    constraint_type: str | None
    require_expected_constraint: bool


COMBINED_RADIUS_DIAMETER_MODE = SketchCircularSizeMode(
    label="Sketch Radius/Diameter",
    operation="constrain_radius_diameter",
    constraint_type=None,
    require_expected_constraint=True,
)
RADIUS_MODE = SketchCircularSizeMode(
    label="Sketch Radius",
    operation="constrain_radius",
    constraint_type="Radius",
    require_expected_constraint=False,
)
DIAMETER_MODE = SketchCircularSizeMode(
    label="Sketch Diameter",
    operation="constrain_diameter",
    constraint_type="Diameter",
    require_expected_constraint=False,
)


@dataclass(frozen=True, slots=True)
class SketchCircularSizeSpec:
    target: SketchConstraintTargetSpec
    mode: SketchCircularSizeMode
    expected_constraint: str | None
    dimension_mm: float
    driving: bool


@dataclass(frozen=True, slots=True)
class ResolvedSketchCircularSize:
    element: SketchConstraintElement
    constraint_name: str
    constraint_type: str
    target_form: str
    measured_value: float


@dataclass(frozen=True, slots=True)
class PreparedSketchCircularSize:
    target: PreparedSketchConstraintTarget
    spec: SketchCircularSizeSpec
    resolved: ResolvedSketchCircularSize
    solver_issues: tuple[tuple[int, ...], ...]


def _require_mode(mode: Any) -> SketchCircularSizeMode:
    if not isinstance(mode, SketchCircularSizeMode):
        raise TypeError("mode must be a SketchCircularSizeMode")
    return mode


def _dimension(value: Any, mode: SketchCircularSizeMode) -> float:
    if not isinstance(value, Mapping) or set(value) != _DIMENSION_FIELDS:
        raise NativeSketchError(f"{mode.label} dimension has incorrect fields.")
    if value["unit"] != "mm":
        raise NativeSketchError(f"{mode.label} requires unit mm.")
    raw = value["value"]
    if isinstance(raw, bool) or not isinstance(raw, (int, float)):
        raise NativeSketchError(f"{mode.label} value must be a number.")
    result = float(raw)
    if not math.isfinite(result) or not _TOLERANCE <= result <= _MAX_DIMENSION:
        raise NativeSketchError(
            f"{mode.label} value must be from {_TOLERANCE} to "
            f"{_MAX_DIMENSION} mm."
        )
    return result


def prepare_sketch_circular_size(
    document_uid: str,
    value: Mapping[str, Any],
    *,
    mode: SketchCircularSizeMode,
) -> SketchCircularSizeSpec:
    mode = _require_mode(mode)
    fields = _BASE_FIELDS | (
        {_EXPECTED_FIELD} if mode.require_expected_constraint else set()
    )
    if not isinstance(value, Mapping) or set(value) != fields:
        raise NativeSketchError(f"A {mode.label} definition has incorrect fields.")
    selection = value["selection"]
    if not isinstance(selection, list) or len(selection) != 1:
        raise NativeSketchError(
            f"{mode.label} requires one exact whole circle or circular arc; use "
            "Equal explicitly for a multi-curve size group."
        )
    expected: str | None = None
    if mode.require_expected_constraint:
        expected_value = value[_EXPECTED_FIELD]
        if (
            not isinstance(expected_value, str)
            or expected_value not in _EXPECTED_CONSTRAINTS
        ):
            raise NativeSketchError(
                f"{mode.label} expected_constraint must be radius or diameter."
            )
        expected = expected_value
    driving = value["driving"]
    if type(driving) is not bool:
        raise NativeSketchError(f"{mode.label} driving must be a boolean.")
    return SketchCircularSizeSpec(
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
        mode,
        expected,
        _dimension(value["dimension"], mode),
        driving,
    )


def _radius(
    sketch: Any,
    element: SketchConstraintElement,
    mode: SketchCircularSizeMode,
) -> float:
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    try:
        radius = float(geometry.Radius)
    except Exception as exc:
        raise NativeSketchError(f"{mode.label} radius is unavailable.") from exc
    if not math.isfinite(radius) or radius <= _TOLERANCE:
        raise NativeSketchError(f"{mode.label} radius is invalid.")
    return radius


def _resolved_constraint_type(
    mode: SketchCircularSizeMode,
    geometry_type: str,
) -> str:
    if mode.constraint_type is not None:
        return mode.constraint_type
    return "Diameter" if geometry_type == _CIRCLE_TYPE else "Radius"


def _resolve_circular_size(
    sketch: Any,
    spec: SketchCircularSizeSpec,
) -> ResolvedSketchCircularSize:
    element = spec.target.selection[0]
    if element.position != "whole" or element.geometry_index in {-1, -2}:
        raise NativeSketchError(
            f"{spec.mode.label} target must be one exact whole circle or circular arc."
        )
    geometry = sketch_constraint_geometry(sketch, element.geometry_index)
    geometry_type = str(getattr(geometry, "TypeId", "") or "")
    if geometry_type not in {_CIRCLE_TYPE, _ARC_TYPE}:
        raise NativeSketchError(
            f"{spec.mode.label} does not support whole "
            f"{geometry_type or 'geometry'}."
        )
    radius = _radius(sketch, element, spec.mode)
    constraint_type = _resolved_constraint_type(spec.mode, geometry_type)
    constraint_name = constraint_type.lower()
    geometry_name = "circle" if geometry_type == _CIRCLE_TYPE else "circular_arc"
    measured = radius * (2.0 if constraint_type == "Diameter" else 1.0)
    if (
        spec.mode.require_expected_constraint
        and spec.expected_constraint != constraint_name
    ):
        raise NativeSketchError(
            f"{spec.mode.label} resolves this exact target to {constraint_name}, not "
            f"{spec.expected_constraint}; read the current Sketch and retry."
        )
    return ResolvedSketchCircularSize(
        element,
        constraint_name,
        constraint_type,
        f"{geometry_name}_{constraint_name}",
        measured,
    )


def _constraint_arguments(
    prepared: PreparedSketchCircularSize,
) -> tuple[Any, ...]:
    return (
        prepared.resolved.constraint_type,
        prepared.resolved.element.geometry_index,
        prepared.spec.dimension_mm,
    )


def preflight_sketch_circular_size(
    context: NativeRuntimeContext,
    spec: SketchCircularSizeSpec,
) -> PreparedSketchCircularSize:
    if not isinstance(spec, SketchCircularSizeSpec):
        raise TypeError("spec must be a SketchCircularSizeSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = _resolve_circular_size(sketch, spec)
    if not spec.driving and not math.isclose(
        resolved.measured_value,
        spec.dimension_mm,
        rel_tol=1.0e-9,
        abs_tol=_TOLERANCE,
    ):
        raise NativeSketchError(
            f"{spec.mode.label} reference measurement changed; read the current "
            "Sketch and retry."
        )
    solver_issues = sketch_solver_issues(sketch, spec.mode.label)
    prepared = PreparedSketchCircularSize(target, spec, resolved, solver_issues)
    constraint = make_dimensional_constraint(
        _constraint_arguments(prepared),
        driving=spec.driving,
    )
    diagnose_exact_constraint(
        sketch,
        constraint,
        expected_index=spec.target.target.expected_constraint_count,
        label=spec.mode.label,
    )
    geometry, constraints, external = current_sketch_constraint_records(
        sketch,
        spec.target,
    )
    if (
        geometry != target.geometry_records
        or constraints != target.constraint_records
        or external != target.external_geometry_records
        or sketch_solver_issues(sketch, spec.mode.label) != solver_issues
    ):
        raise NativeSketchError(
            f"{spec.mode.label} feasibility check changed the active Sketch."
        )
    return prepared


def create_sketch_circular_size(
    document: Any,
    prepared: PreparedSketchCircularSize,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchCircularSize):
        raise TypeError("prepared must be a PreparedSketchCircularSize")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage=f"after {prepared.spec.mode.label.removeprefix('Sketch ')} preflight",
    )
    constraint = make_dimensional_constraint(
        _constraint_arguments(prepared),
        driving=prepared.spec.driving,
    )
    label = prepared.spec.mode.label.removeprefix("Sketch ")
    index = add_exact_constraint(
        sketch,
        constraint,
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label=label,
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_index": index},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def verify_sketch_circular_size(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchCircularSize):
        raise TypeError("draft must contain a PreparedSketchCircularSize")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    constraint = verify_exact_constraint_append(
        sketch,
        prepared.target,
        constraint_index=int(draft.value["constraint_index"]),
        solver_issues=prepared.solver_issues,
        constraint_type=prepared.resolved.constraint_type,
        references=[
            {
                "slot": 1,
                "geometry_index": prepared.resolved.element.geometry_index,
            }
        ],
        driving=prepared.spec.driving,
        value=prepared.spec.dimension_mm,
        tolerance=_TOLERANCE,
        label=prepared.spec.mode.label,
    )
    measured_after = _resolve_circular_size(sketch, prepared.spec)
    if (
        measured_after.target_form != prepared.resolved.target_form
        or measured_after.constraint_type != prepared.resolved.constraint_type
        or not math.isclose(
            measured_after.measured_value,
            prepared.spec.dimension_mm,
            rel_tol=1.0e-9,
            abs_tol=_TOLERANCE,
        )
    ):
        raise NativeSketchError(
            f"{prepared.spec.mode.label} solver result does not satisfy its exact value."
        )
    return sketch_geometry_result(
        sketch,
        {
            "operation": prepared.spec.mode.operation,
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
