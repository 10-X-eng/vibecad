# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact feasibility and append proofs shared by Native Sketch constraints."""

from __future__ import annotations

from dataclasses import dataclass
import json
import math
from typing import Any, Mapping

from VibeCADNativeSketchConstraintTargets import (
    PreparedSketchConstraintTarget,
    current_sketch_constraint_records,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchLimits import MAX_SKETCH_BATCH_CONSTRAINTS
from VibeCADNativeSketchState import serialize_sketch_constraint


_GEOMETRY_METADATA = frozenset(
    {
        "index",
        "type_id",
        "kind",
        "construction",
        "blocked",
        "geometry_id",
        "internal_type",
        "layer_id",
        "tag",
    }
)
_SOLVER_ISSUE_ATTRIBUTES = (
    "ConflictingConstraints",
    "RedundantConstraints",
    "PartiallyRedundantConstraints",
    "MalformedConstraints",
)
_FEASIBILITY_INDEX_FIELDS = (
    "conflicting_constraint_indices",
    "redundant_constraint_indices",
    "partially_redundant_constraint_indices",
    "malformed_constraint_indices",
)


@dataclass(frozen=True, slots=True)
class ExactConstraintExpectation:
    constraint_type: str
    references: tuple[Mapping[str, Any], ...]
    driving: bool
    value: float | None
    tolerance: float
    allowed_values: tuple[float, ...] = ()


class NativeSketchConstraintFeasibilityError(NativeSketchError):
    """A complete solver diagnosis rejected the proposed exact constraint."""

    def __init__(self, label: str, reasons: tuple[str, ...], action: str) -> None:
        self.reasons = reasons
        self.action = action
        reason = ", ".join(reasons) if reasons else "unsolved"
        verb = "added" if action == "append" else "changed"
        super().__init__(f"{label} would be {reason}; no constraint was {verb}.")


def sketch_solver_issues(sketch: Any, label: str) -> tuple[tuple[int, ...], ...]:
    result = []
    for attribute in _SOLVER_ISSUE_ATTRIBUTES:
        try:
            result.append(tuple(sorted(int(value) for value in getattr(sketch, attribute))))
        except Exception as exc:
            raise NativeSketchError(f"{label} solver diagnostics are unavailable.") from exc
    return tuple(result)


def make_dimensional_constraint(
    arguments: tuple[Any, ...],
    *,
    driving: bool,
) -> Any:
    if type(driving) is not bool:
        raise TypeError("driving must be a boolean")
    import Sketcher

    try:
        return Sketcher.Constraint(*arguments, True, driving)
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact dimensional-constraint definition."
        ) from exc


def _integer(value: Any, field: str) -> int:
    if type(value) is not int:
        raise NativeSketchError(
            f"Sketcher returned an invalid {field} feasibility value."
        )
    return value


def _indices(value: Any, field: str) -> tuple[int, ...]:
    if not isinstance(value, (list, tuple)):
        raise NativeSketchError(
            f"Sketcher returned invalid {field} feasibility indices."
        )
    result = tuple(_integer(item, field) for item in value)
    if len(result) > 1_000_001 or len(set(result)) != len(result):
        raise NativeSketchError(
            f"Sketcher returned invalid {field} feasibility indices."
        )
    return tuple(sorted(result))


def _verify_feasibility_result(
    result: Any,
    *,
    expected_index: int,
    expected_count: int,
    label: str,
    action: str,
) -> None:
    if not isinstance(result, Mapping):
        raise NativeSketchError(
            f"{label} solver feasibility returned an invalid result."
        )
    required = {
        "accepted",
        "degrees_of_freedom",
        "solver_status",
        "first_proposed_constraint_index",
        "proposed_constraint_count",
        *_FEASIBILITY_INDEX_FIELDS,
    }
    if not required.issubset(result) or type(result["accepted"]) is not bool:
        raise NativeSketchError(
            f"{label} solver feasibility returned incomplete diagnostics."
        )
    degrees = _integer(result["degrees_of_freedom"], "degrees-of-freedom")
    status = _integer(result["solver_status"], "solver-status")
    first = _integer(
        result["first_proposed_constraint_index"],
        "first-proposed-index",
    )
    count = _integer(result["proposed_constraint_count"], "proposed-count")
    issues = {
        field: _indices(result[field], field) for field in _FEASIBILITY_INDEX_FIELDS
    }
    if first != expected_index or count != expected_count:
        raise NativeSketchError(
            f"{label} solver feasibility did not analyze the exact {action}."
        )
    if not result["accepted"]:
        reasons = [
            name
            for field, name in (
                ("conflicting_constraint_indices", "conflicting"),
                ("redundant_constraint_indices", "redundant"),
                (
                    "partially_redundant_constraint_indices",
                    "partially redundant",
                ),
                ("malformed_constraint_indices", "malformed"),
            )
            if issues[field]
        ]
        raise NativeSketchConstraintFeasibilityError(
            label,
            tuple(reasons),
            action,
        )
    if degrees < 0 or status != 0 or any(issues.values()):
        raise NativeSketchError(
            f"{label} solver feasibility returned inconsistent acceptance."
        )


def diagnose_exact_constraints(
    sketch: Any,
    constraints: tuple[Any, ...],
    *,
    expected_index: int,
    label: str,
) -> None:
    if not isinstance(constraints, tuple) or not (
        1 <= len(constraints) <= MAX_SKETCH_BATCH_CONSTRAINTS
    ):
        raise TypeError(
            "constraints must contain one to "
            f"{MAX_SKETCH_BATCH_CONSTRAINTS} constraints"
        )
    diagnose = getattr(sketch, "diagnoseAdditionalConstraints", None)
    if not callable(diagnose):
        raise NativeSketchError(f"{label} solver feasibility is unavailable.")
    try:
        result = diagnose(
            constraints[0] if len(constraints) == 1 else list(constraints)
        )
    except Exception as exc:
        raise NativeSketchError(f"{label} solver feasibility check failed.") from exc
    _verify_feasibility_result(
        result,
        expected_index=expected_index,
        expected_count=len(constraints),
        label=label,
        action="append",
    )


def diagnose_exact_block_constraints(
    sketch: Any,
    constraints: tuple[Any, ...],
    *,
    expected_index: int,
    label: str,
) -> None:
    """Diagnose Block against copied geometry carrying the proposed blocked state."""

    if not isinstance(constraints, tuple) or not (
        1 <= len(constraints) <= MAX_SKETCH_BATCH_CONSTRAINTS
    ):
        raise TypeError(
            "constraints must contain one to "
            f"{MAX_SKETCH_BATCH_CONSTRAINTS} constraints"
        )
    diagnose = getattr(sketch, "diagnoseBlockConstraints", None)
    if not callable(diagnose):
        raise NativeSketchError(f"{label} Block feasibility is unavailable.")
    try:
        result = diagnose(
            constraints[0] if len(constraints) == 1 else list(constraints)
        )
    except Exception as exc:
        raise NativeSketchError(f"{label} Block feasibility check failed.") from exc
    _verify_feasibility_result(
        result,
        expected_index=expected_index,
        expected_count=len(constraints),
        label=label,
        action="append",
    )


def diagnose_exact_constraint_replacement(
    sketch: Any,
    constraint: Any,
    *,
    replaced_constraint_index: int,
    expected_index: int,
    label: str,
) -> None:
    diagnose = getattr(sketch, "diagnoseConstraintReplacement", None)
    if not callable(diagnose):
        raise NativeSketchError(
            f"{label} replacement feasibility is unavailable."
        )
    try:
        result = diagnose(replaced_constraint_index, constraint)
    except Exception as exc:
        raise NativeSketchError(
            f"{label} replacement feasibility check failed."
        ) from exc
    _verify_feasibility_result(
        result,
        expected_index=expected_index,
        expected_count=1,
        label=label,
        action="replacement",
    )


def diagnose_exact_constraint(
    sketch: Any,
    constraint: Any,
    *,
    expected_index: int,
    label: str,
) -> None:
    diagnose_exact_constraints(
        sketch,
        (constraint,),
        expected_index=expected_index,
        label=label,
    )


def add_exact_constraint(
    sketch: Any,
    constraint: Any,
    *,
    expected_index: int,
    label: str,
) -> int:
    try:
        index = int(sketch.addConstraint(constraint))
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the {label}.") from exc
    if index != expected_index:
        raise NativeSketchError(
            f"Sketcher returned an unexpected {label} constraint index."
        )
    return index


def add_exact_constraints(
    sketch: Any,
    constraints: tuple[Any, ...],
    *,
    expected_index: int,
    label: str,
) -> tuple[int, ...]:
    if not isinstance(constraints, tuple) or not (
        1 <= len(constraints) <= MAX_SKETCH_BATCH_CONSTRAINTS
    ):
        raise TypeError(
            "constraints must contain one to "
            f"{MAX_SKETCH_BATCH_CONSTRAINTS} constraints"
        )
    try:
        raw_indices = sketch.addConstraint(list(constraints))
        indices = tuple(int(value) for value in raw_indices)
    except Exception as exc:
        raise NativeSketchError(f"Sketcher rejected the {label}.") from exc
    expected = tuple(range(expected_index, expected_index + len(constraints)))
    if indices != expected:
        raise NativeSketchError(
            f"Sketcher returned unexpected {label} constraint indices."
        )
    return indices


def _metadata_records(records: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    result = []
    for encoded in records:
        record = json.loads(encoded)
        result.append({key: record[key] for key in _GEOMETRY_METADATA if key in record})
    return tuple(result)


def _stable_existing_constraints(
    before: tuple[str, ...],
    after: tuple[str, ...],
) -> bool:
    if len(before) != len(after):
        return False
    for before_encoded, after_encoded in zip(before, after, strict=True):
        expected = json.loads(before_encoded)
        observed = json.loads(after_encoded)
        if not bool(expected.get("driving", False)):
            expected.pop("value", None)
            observed.pop("value", None)
        if expected != observed:
            return False
    return True


def _verify_solver_issues_unchanged(
    sketch: Any,
    before_issues: tuple[tuple[int, ...], ...],
    label: str,
) -> None:
    current_issues = sketch_solver_issues(sketch, label)
    for before, after in zip(before_issues, current_issues, strict=True):
        if set(after) - set(before):
            raise NativeSketchError(
                f"{label} introduced a solver conflict or redundancy."
            )


def _verify_constraint_expectation(
    sketch: Any,
    index: int,
    expectation: ExactConstraintExpectation,
    label: str,
) -> dict[str, Any]:
    constraint = serialize_sketch_constraint(sketch, index)
    observed_value = float(constraint.get("value", float("nan")))
    if expectation.allowed_values:
        value_matches = "value" in constraint and any(
            math.isclose(
                observed_value,
                allowed,
                rel_tol=1.0e-9,
                abs_tol=expectation.tolerance,
            )
            for allowed in expectation.allowed_values
        )
    else:
        value_matches = (
            "value" not in constraint
            if expectation.value is None
            else math.isclose(
                observed_value,
                expectation.value,
                rel_tol=1.0e-9,
                abs_tol=expectation.tolerance,
            )
        )
    if (
        constraint.get("type") != expectation.constraint_type
        or constraint.get("references")
        != [dict(reference) for reference in expectation.references]
        or bool(constraint.get("driving")) is not expectation.driving
        or not bool(constraint.get("active"))
        or bool(constraint.get("virtual"))
        or not value_matches
    ):
        raise NativeSketchError(f"{label} differs from its exact definition.")
    return constraint


def verify_exact_constraint_appends(
    sketch: Any,
    target: PreparedSketchConstraintTarget,
    *,
    constraint_indices: tuple[int, ...],
    solver_issues: tuple[tuple[int, ...], ...],
    expectations: tuple[ExactConstraintExpectation, ...],
    label: str,
    expected_geometry_records: tuple[str, ...] | None = None,
) -> tuple[dict[str, Any], ...]:
    if (
        not isinstance(constraint_indices, tuple)
        or not isinstance(expectations, tuple)
        or not expectations
        or len(constraint_indices) != len(expectations)
    ):
        raise TypeError("constraint indices and expectations must be equal tuples")
    geometry, constraints, external = current_sketch_constraint_records(
        sketch,
        target.spec,
    )
    geometry_count = target.spec.target.expected_geometry_count
    constraint_count = target.spec.target.expected_constraint_count
    if len(geometry) != geometry_count:
        raise NativeSketchError(f"{label} changed geometry topology.")
    expected_geometry = (
        target.geometry_records
        if expected_geometry_records is None
        else expected_geometry_records
    )
    if len(expected_geometry) != geometry_count:
        raise TypeError("expected geometry records must match the target geometry count")
    if _metadata_records(geometry) != _metadata_records(expected_geometry):
        raise NativeSketchError(f"{label} changed geometry metadata.")
    if external != target.external_geometry_records:
        raise NativeSketchError(f"{label} changed external geometry.")
    expected_indices = tuple(
        range(constraint_count, constraint_count + len(expectations))
    )
    if (
        constraint_indices != expected_indices
        or len(constraints) != constraint_count + len(expectations)
        or not _stable_existing_constraints(
            target.constraint_records,
            constraints[:constraint_count],
        )
    ):
        raise NativeSketchError(f"{label} changed constraints beyond its exact append.")
    _verify_solver_issues_unchanged(sketch, solver_issues, label)
    result = []
    for index, expectation in zip(
        constraint_indices,
        expectations,
        strict=True,
    ):
        result.append(
            _verify_constraint_expectation(sketch, index, expectation, label)
        )
    return tuple(result)


def verify_exact_constraint_replacement(
    sketch: Any,
    target: PreparedSketchConstraintTarget,
    *,
    replaced_constraint_index: int,
    replacement_constraint_index: int,
    solver_issues: tuple[tuple[int, ...], ...],
    expectation: ExactConstraintExpectation,
    label: str,
) -> dict[str, Any]:
    geometry, constraints, external = current_sketch_constraint_records(
        sketch, target.spec
    )
    constraint_count = target.spec.target.expected_constraint_count
    if len(geometry) != target.spec.target.expected_geometry_count:
        raise NativeSketchError(f"{label} changed geometry topology.")
    if _metadata_records(geometry) != _metadata_records(target.geometry_records):
        raise NativeSketchError(f"{label} changed geometry metadata.")
    if external != target.external_geometry_records:
        raise NativeSketchError(f"{label} changed external geometry.")
    if (
        constraint_count <= 0
        or replaced_constraint_index < 0
        or replaced_constraint_index >= constraint_count
        or replacement_constraint_index != constraint_count - 1
        or len(constraints) != constraint_count
    ):
        raise NativeSketchError(f"{label} did not perform the exact replacement.")
    expected_remaining = []
    for old_index, encoded in enumerate(target.constraint_records):
        if old_index == replaced_constraint_index:
            continue
        record = json.loads(encoded)
        record["index"] = len(expected_remaining)
        expected_remaining.append(
            json.dumps(
                record,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    if not _stable_existing_constraints(
        tuple(expected_remaining), constraints[: constraint_count - 1]
    ):
        raise NativeSketchError(
            f"{label} changed constraints beyond its exact replacement."
        )
    _verify_solver_issues_unchanged(sketch, solver_issues, label)
    return _verify_constraint_expectation(
        sketch,
        replacement_constraint_index,
        expectation,
        label,
    )


def verify_exact_constraint_append(
    sketch: Any,
    target: PreparedSketchConstraintTarget,
    *,
    constraint_index: int,
    solver_issues: tuple[tuple[int, ...], ...],
    constraint_type: str,
    references: list[dict[str, Any]],
    driving: bool,
    value: float,
    tolerance: float,
    label: str,
) -> dict[str, Any]:
    return verify_exact_constraint_appends(
        sketch,
        target,
        constraint_indices=(constraint_index,),
        solver_issues=solver_issues,
        expectations=(
            ExactConstraintExpectation(
                constraint_type,
                tuple(references),
                driving,
                value,
                tolerance,
            ),
        ),
        label=label,
    )[0]
