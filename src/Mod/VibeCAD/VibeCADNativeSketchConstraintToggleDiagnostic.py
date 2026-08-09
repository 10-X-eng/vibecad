# SPDX-License-Identifier: LGPL-2.1-or-later

"""Strict host-diagnostic parsing for exact constraint-state batches."""

from __future__ import annotations

from typing import Any, Mapping

from VibeCADNativeSketchErrors import NativeSketchError


_BASE_DIAGNOSTIC_FIELDS = frozenset(
    {
        "accepted",
        "degrees_of_freedom",
        "solver_status",
        "conflicting_constraint_indices",
        "redundant_constraint_indices",
        "partially_redundant_constraint_indices",
        "malformed_constraint_indices",
        "constraint_indices",
    }
)
_ISSUE_FIELDS = (
    "conflicting_constraint_indices",
    "redundant_constraint_indices",
    "partially_redundant_constraint_indices",
    "malformed_constraint_indices",
)


def _integer(value: Any, field: str, label: str) -> int:
    if type(value) is not int:
        raise NativeSketchError(
            f"{label} feasibility returned an invalid {field} value."
        )
    return value


def _exact_sequence(value: Any, field: str, label: str) -> tuple[Any, ...]:
    if not isinstance(value, (list, tuple)) or len(value) > 1_000_000:
        raise NativeSketchError(f"{label} feasibility returned invalid {field} values.")
    return tuple(value)


def diagnose_constraint_state_changes(
    sketch: Any,
    targets: tuple[Any, ...],
    *,
    method_name: str,
    state_result_field: str,
    target_state_field: str,
    label: str,
) -> int:
    diagnose = getattr(sketch, method_name, None)
    if not callable(diagnose):
        raise NativeSketchError(f"{label} solver feasibility is unavailable.")
    changes = tuple(
        (target.constraint_index, bool(getattr(target, target_state_field)))
        for target in targets
    )
    try:
        result = diagnose(list(changes))
    except Exception as exc:
        raise NativeSketchError(f"{label} solver feasibility check failed.") from exc
    expected_fields = _BASE_DIAGNOSTIC_FIELDS | {state_result_field}
    if not isinstance(result, Mapping) or set(result) != expected_fields:
        raise NativeSketchError(
            f"{label} solver feasibility returned incomplete diagnostics."
        )
    if type(result["accepted"]) is not bool:
        raise NativeSketchError(
            f"{label} solver feasibility returned invalid acceptance."
        )
    degrees = _integer(result["degrees_of_freedom"], "degrees-of-freedom", label)
    status = _integer(result["solver_status"], "solver-status", label)
    indices = _exact_sequence(result["constraint_indices"], "constraint-index", label)
    states = _exact_sequence(result[state_result_field], "constraint-state", label)
    if (
        any(type(value) is not int for value in indices)
        or any(type(value) is not bool for value in states)
        or indices != tuple(index for index, _state in changes)
        or states != tuple(state for _index, state in changes)
    ):
        raise NativeSketchError(
            f"{label} solver feasibility did not analyze the exact requested states."
        )
    issues = []
    for field in _ISSUE_FIELDS:
        values = _exact_sequence(result[field], field, label)
        if any(type(value) is not int or value < 0 for value in values) or len(
            set(values)
        ) != len(values):
            raise NativeSketchError(
                f"{label} solver feasibility returned invalid {field} values."
            )
        issues.append(values)
    if not result["accepted"]:
        raise NativeSketchError(
            f"{label} would introduce a solver issue; no state was changed."
        )
    if degrees < 0 or status != 0 or any(issues):
        raise NativeSketchError(
            f"{label} solver feasibility returned inconsistent acceptance."
        )
    return degrees
