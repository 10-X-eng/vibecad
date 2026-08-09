# SPDX-License-Identifier: LGPL-2.1-or-later

"""One exact transaction for bounded Native Sketch geometry and constraints."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBatchPlan import (
    SketchBatchConstraintSpec,
    SketchBatchGeometrySpec,
    SketchBatchPointRef,
    SketchBatchSpec,
)
from VibeCADNativeSketchConstraintAppend import (
    ExactConstraintExpectation,
    add_exact_constraints,
    diagnose_exact_constraints,
    make_dimensional_constraint,
    sketch_solver_issues,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchInsertion import (
    PreparedSketchInsertion,
    preflight_sketch_insertion,
    require_unchanged_sketch_insertion,
    verify_sketch_append,
)
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_geometry_records,
    serialize_sketch_constraint,
    serialize_sketch_diagnostics,
    serialize_sketch_geometry,
)
from VibeCADNativeTargets import object_identity, object_reference


_POSITION_CODES = {"point": 1, "start": 1, "end": 2, "center": 3, "origin": 1}
_HOST_GEOMETRY = {
    "point": ("Part::GeomPoint", "point"),
    "line": ("Part::GeomLineSegment", "line"),
    "circle": ("Part::GeomCircle", "circle"),
    "arc": ("Part::GeomArcOfCircle", "circular_arc"),
}
_HOST_CONSTRAINT = {
    "coincident": "Coincident",
    "horizontal": "Horizontal",
    "vertical": "Vertical",
    "parallel": "Parallel",
    "perpendicular": "Perpendicular",
    "equal": "Equal",
    "distance_x": "DistanceX",
    "distance_y": "DistanceY",
    "distance": "Distance",
    "radius": "Radius",
    "diameter": "Diameter",
    "angle": "Angle",
}
_DIMENSIONAL_KINDS = frozenset(
    {"distance_x", "distance_y", "distance", "radius", "diameter", "angle"}
)
_LINEAR_TOLERANCE = 1.0e-7


@dataclass(frozen=True, slots=True)
class PreparedSketchBatch:
    insertion: PreparedSketchInsertion
    spec: SketchBatchSpec
    solver_issues: tuple[tuple[int, ...], ...]


@dataclass(frozen=True, slots=True)
class _CreatedConstraint:
    local_ref: str
    constraint: Any
    expectation: ExactConstraintExpectation


def preflight_sketch_batch(
    context: NativeRuntimeContext,
    spec: SketchBatchSpec,
) -> PreparedSketchBatch:
    if not isinstance(spec, SketchBatchSpec):
        raise TypeError("spec must be a SketchBatchSpec")
    insertion = preflight_sketch_insertion(context, spec.target)
    return PreparedSketchBatch(
        insertion,
        spec,
        sketch_solver_issues(insertion.target.sketch, "Sketch batch"),
    )


def _geometry_value(spec: SketchBatchGeometrySpec) -> Any:
    import FreeCAD as App
    import Part

    if spec.kind == "point":
        return Part.Point(App.Vector(*spec.first_mm, 0.0))
    if spec.kind == "line":
        return Part.LineSegment(
            App.Vector(*spec.first_mm, 0.0),
            App.Vector(*spec.second_mm, 0.0),
        )
    if spec.kind == "circle":
        return Part.Circle(
            App.Vector(*spec.first_mm, 0.0),
            App.Vector(0.0, 0.0, 1.0),
            spec.radius_mm,
        )
    if spec.kind == "arc":
        circle = Part.Circle(
            App.Vector(*spec.first_mm, 0.0),
            App.Vector(0.0, 0.0, 1.0),
            spec.radius_mm,
        )
        first = math.radians(float(spec.start_angle_degrees))
        last = first + math.radians(float(spec.sweep_angle_degrees))
        return Part.ArcOfCircle(circle, first, last)
    raise NativeSketchError(f"Sketch batch geometry kind {spec.kind!r} is unavailable.")


def _point_target(
    reference: SketchBatchPointRef,
    indices: Mapping[str, int],
) -> tuple[int, int]:
    if reference.is_origin:
        return -1, 1
    return indices[str(reference.geometry_ref)], _POSITION_CODES[reference.position]


def _references(*targets: tuple[int, int]) -> tuple[Mapping[str, Any], ...]:
    result = []
    for slot, (geometry_index, position) in enumerate(targets, start=1):
        reference: dict[str, Any] = {
            "slot": slot,
            "geometry_index": geometry_index,
        }
        if position:
            reference["position"] = position
        result.append(reference)
    return tuple(result)


def _curve_references(indices: tuple[int, ...]) -> tuple[Mapping[str, Any], ...]:
    return _references(*((index, 0) for index in indices))


def _created_constraint(
    spec: SketchBatchConstraintSpec,
    indices: Mapping[str, int],
) -> _CreatedConstraint:
    import Sketcher

    kind = spec.kind
    host_type = _HOST_CONSTRAINT[kind]
    point_targets = tuple(_point_target(point, indices) for point in spec.points)
    curve_indices = tuple(indices[local_ref] for local_ref in spec.geometry_refs)
    value = spec.value
    if kind == "coincident":
        first, second = point_targets
        arguments = (host_type, first[0], first[1], second[0], second[1])
        references = _references(first, second)
    elif kind in {"horizontal", "vertical"}:
        arguments = (host_type, curve_indices[0])
        references = _curve_references(curve_indices)
    elif kind in {"parallel", "perpendicular", "equal"}:
        arguments = (host_type, *curve_indices)
        references = _curve_references(curve_indices)
    elif kind in {"distance_x", "distance_y", "distance"}:
        first, second = point_targets
        arguments = (
            host_type,
            first[0],
            first[1],
            second[0],
            second[1],
            value,
        )
        references = _references(first, second)
    elif kind in {"radius", "diameter"}:
        arguments = (host_type, curve_indices[0], value)
        references = _curve_references(curve_indices)
    elif kind == "angle":
        arguments = (
            host_type,
            curve_indices[0],
            0,
            curve_indices[1],
            0,
            math.radians(float(value)),
        )
        references = _curve_references(curve_indices)
        value = math.radians(float(value))
    else:
        raise NativeSketchError(f"Sketch batch constraint kind {kind!r} is unavailable.")
    try:
        constraint = (
            make_dimensional_constraint(arguments, driving=True)
            if kind in _DIMENSIONAL_KINDS
            else Sketcher.Constraint(*arguments)
        )
    except NativeSketchError:
        raise
    except Exception as exc:
        raise NativeSketchError(
            f"Sketcher rejected batch constraint {spec.local_ref!r}."
        ) from exc
    expectation = ExactConstraintExpectation(
        host_type,
        references,
        True,
        float(value) if value is not None else None,
        1.0e-10 if kind == "angle" else _LINEAR_TOLERANCE,
    )
    return _CreatedConstraint(spec.local_ref, constraint, expectation)


def _current_records(sketch: Any) -> tuple[tuple[str, ...], tuple[str, ...]]:
    return (
        canonical_sketch_records(iter_sketch_geometry_records(sketch)),
        canonical_sketch_records(iter_sketch_constraint_records(sketch)),
    )


def create_sketch_batch(
    document: Any,
    prepared: PreparedSketchBatch,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchBatch):
        raise TypeError("prepared must be a PreparedSketchBatch")
    sketch = require_unchanged_sketch_insertion(
        document,
        prepared.insertion,
        stage="after batch preflight",
    )
    first_geometry = prepared.spec.target.expected_geometry_count
    geometry_indices = []
    by_ref: dict[str, int] = {}
    for offset, spec in enumerate(prepared.spec.geometry):
        expected = first_geometry + offset
        try:
            index = int(sketch.addGeometry(_geometry_value(spec), spec.construction))
        except Exception as exc:
            raise NativeSketchError(
                f"Sketcher rejected batch geometry {spec.local_ref!r}."
            ) from exc
        if index != expected:
            raise NativeSketchError(
                f"Sketcher returned an unexpected index for geometry {spec.local_ref!r}."
            )
        geometry_indices.append(index)
        by_ref[spec.local_ref] = index

    created_constraints = tuple(
        _created_constraint(spec, by_ref) for spec in prepared.spec.constraints
    )
    before_diagnosis = _current_records(sketch)
    issues_before_diagnosis = sketch_solver_issues(sketch, "Sketch batch")
    if issues_before_diagnosis != prepared.solver_issues:
        raise NativeSketchError("Sketch batch geometry changed existing solver issues.")
    diagnose_exact_constraints(
        sketch,
        tuple(item.constraint for item in created_constraints),
        expected_index=prepared.spec.target.expected_constraint_count,
        label="Sketch batch",
    )
    if (
        _current_records(sketch) != before_diagnosis
        or sketch_solver_issues(sketch, "Sketch batch") != issues_before_diagnosis
    ):
        raise NativeSketchError("Sketch batch feasibility changed the active Sketch.")
    constraint_indices = add_exact_constraints(
        sketch,
        tuple(item.constraint for item in created_constraints),
        expected_index=prepared.spec.target.expected_constraint_count,
        label="Sketch batch constraints",
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "geometry_indices": tuple(geometry_indices),
            "constraint_indices": constraint_indices,
            "constraint_expectations": tuple(
                item.expectation for item in created_constraints
            ),
        },
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _is_degenerate(record: Mapping[str, Any]) -> bool:
    kind = record.get("kind")
    if kind == "line":
        start = record.get("start_mm")
        end = record.get("end_mm")
        if not (
            isinstance(start, list)
            and isinstance(end, list)
            and len(start) >= 2
            and len(end) >= 2
        ):
            return True
        return math.hypot(float(end[0]) - float(start[0]), float(end[1]) - float(start[1])) <= 1.0e-9
    if kind in {"circle", "circular_arc"}:
        radius = record.get("radius_mm")
        if type(radius) not in {int, float} or float(radius) <= 1.0e-9:
            return True
    if kind == "circular_arc":
        first = record.get("first_parameter")
        last = record.get("last_parameter")
        return not (
            type(first) in {int, float}
            and type(last) in {int, float}
            and float(last) - float(first) > 1.0e-12
        )
    return kind not in {"point", "line", "circle", "circular_arc"}


def _verify_geometry(
    spec: SketchBatchGeometrySpec,
    record: Mapping[str, Any],
    expected_index: int,
) -> None:
    expected_type, expected_kind = _HOST_GEOMETRY[spec.kind]
    if (
        record.get("index") != expected_index
        or record.get("type_id") != expected_type
        or record.get("kind") != expected_kind
        or bool(record.get("construction")) is not spec.construction
        or bool(record.get("blocked"))
    ):
        raise NativeSketchError(
            f"Sketch batch geometry {spec.local_ref!r} differs from its definition."
        )


def _constraint_matches(
    record: Mapping[str, Any],
    expectation: ExactConstraintExpectation,
    expected_index: int,
) -> bool:
    if (
        record.get("index") != expected_index
        or record.get("type") != expectation.constraint_type
        or record.get("references")
        != [dict(reference) for reference in expectation.references]
        or bool(record.get("driving")) is not expectation.driving
        or not bool(record.get("active"))
        or bool(record.get("virtual"))
    ):
        return False
    if expectation.value is None:
        return "value" not in record
    value = record.get("value")
    return bool(
        type(value) in {int, float}
        and math.isclose(
            float(value),
            expectation.value,
            rel_tol=1.0e-9,
            abs_tol=expectation.tolerance,
        )
    )


def _concise_diagnostics(sketch: Any) -> dict[str, Any]:
    diagnostics = serialize_sketch_diagnostics(sketch)
    profile = diagnostics["profile"]
    solver = diagnostics["solver"]
    return {
        "profile": {
            name: profile.get(name)
            for name in (
                "wire_count",
                "closed_wire_count",
                "open_wire_count",
                "face_count",
                "closed_profile",
            )
        },
        "solver": {
            name: solver.get(name)
            for name in (
                "degrees_of_freedom",
                "fully_constrained",
                "conflicting_constraints",
                "redundant_constraints",
                "partially_redundant_constraints",
                "malformed_constraints",
                "valid",
            )
        },
    }


def verify_sketch_batch(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    if not isinstance(prepared, PreparedSketchBatch):
        raise TypeError("draft must contain a PreparedSketchBatch")
    geometry_indices = tuple(value.get("geometry_indices", ()))
    constraint_indices = tuple(value.get("constraint_indices", ()))
    expectations = tuple(value.get("constraint_expectations", ()))
    if (
        len(geometry_indices) != len(prepared.spec.geometry)
        or len(constraint_indices) != len(prepared.spec.constraints)
        or len(expectations) != len(prepared.spec.constraints)
    ):
        raise NativeSketchError("Sketch batch did not retain its exact result mapping.")
    sketch = verify_sketch_append(
        document,
        prepared.insertion,
        geometry_added=len(geometry_indices),
        constraints_added=len(constraint_indices),
    )
    geometry_records = tuple(
        serialize_sketch_geometry(sketch, index) for index in geometry_indices
    )
    degenerate = []
    geometry_refs = []
    for spec, index, record in zip(
        prepared.spec.geometry,
        geometry_indices,
        geometry_records,
        strict=True,
    ):
        _verify_geometry(spec, record, index)
        if _is_degenerate(record):
            degenerate.append(spec.local_ref)
        reference = {
            "local_ref": spec.local_ref,
            "geometry_index": index,
            "kind": spec.kind,
            "construction": spec.construction,
        }
        if "geometry_id" in record:
            reference["geometry_id"] = record["geometry_id"]
        geometry_refs.append(reference)
    if degenerate:
        raise NativeSketchError(
            f"Sketch batch produced degenerate geometry refs: {degenerate}."
        )

    constraint_refs = []
    for spec, index, expectation in zip(
        prepared.spec.constraints,
        constraint_indices,
        expectations,
        strict=True,
    ):
        record = serialize_sketch_constraint(sketch, index)
        if not _constraint_matches(record, expectation, index):
            raise NativeSketchError(
                f"Sketch batch constraint {spec.local_ref!r} differs from its definition."
            )
        constraint_refs.append(
            {
                "local_ref": spec.local_ref,
                "constraint_index": index,
                "type": expectation.constraint_type,
            }
        )
    current_issues = sketch_solver_issues(sketch, "Sketch batch")
    for before, after in zip(prepared.solver_issues, current_issues, strict=True):
        if set(after) - set(before):
            raise NativeSketchError(
                "Sketch batch introduced a conflict, redundancy, or malformed constraint."
            )
    try:
        valid = bool(sketch.isValid())
        geometry_count = int(sketch.GeometryCount)
        constraint_count = int(sketch.ConstraintCount)
    except Exception as exc:
        raise NativeSketchError("Sketch batch postcondition is unavailable.") from exc
    if not valid:
        raise NativeSketchError("Sketch batch left the active Sketch invalid.")
    return {
        "sketch": object_reference(sketch),
        "geometry_refs": geometry_refs,
        "constraint_refs": constraint_refs,
        "geometry_count": geometry_count,
        "constraint_count": constraint_count,
        "degenerate_geometry_refs": [],
        **_concise_diagnostics(sketch),
    }
