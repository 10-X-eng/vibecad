# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact state and semantic checks for removing Sketch axes alignment."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchDiagnosticState import (
    ISSUE_FIELDS,
    require_healthy_external_records,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchAxisAlignmentTarget import LABEL, SketchAxisAlignmentSpec
from VibeCADNativeSketchTransformState import (
    FrozenSketchTransformState,
    SketchTransformPlan,
    SketchTransformSnapshot,
    frozen_transform_state,
    parse_transform_diagnostic,
    require_pure_transform_diagnostic,
    require_transform_snapshot_unchanged,
    verify_transform_state,
)
from VibeCADNativeSketchMutationState import geometry_records_without_tags
from VibeCADNativeSketchTargets import preflight_active_sketch


_COUNT_FIELDS = (
    "removed_horizontal_constraints",
    "removed_vertical_constraints",
    "created_parallel_constraints",
    "removed_axis_symmetry_constraints",
    "removed_point_on_axis_constraints",
    "converted_distance_constraints",
)
_FIELDS = frozenset(
    {
        "accepted",
        "degrees_of_freedom",
        "solver_status",
        *ISSUE_FIELDS,
        "geometry_count",
        "constraint_count",
        "geometry",
        "geometry_metadata",
        "constraints",
        "external_reference_count",
        "external_references",
        "external_geometry_count",
        "external_geometry",
        "external_geometry_metadata",
        "input_geometry_indices",
        *_COUNT_FIELDS,
        "geometry_tags",
        "constraint_tags",
        "expressions",
        "mutation_receipt",
    }
)

FrozenAxisAlignmentState = FrozenSketchTransformState
SketchAxisAlignmentSnapshot = SketchTransformSnapshot


@dataclass(frozen=True, slots=True)
class SketchAxisAlignmentPlan:
    transform: SketchTransformPlan
    counts: tuple[tuple[str, int], ...]

    def count(self, field: str) -> int:
        try:
            return dict(self.counts)[field]
        except KeyError as exc:
            raise NativeSketchError(f"{LABEL} count is unavailable: {field}.") from exc


def capture_axis_alignment_snapshot(
    context: NativeRuntimeContext,
    spec: SketchAxisAlignmentSpec,
) -> SketchAxisAlignmentSnapshot:
    if not isinstance(spec, SketchAxisAlignmentSpec):
        raise TypeError("spec must be a SketchAxisAlignmentSpec")
    target = preflight_active_sketch(context, spec.target)
    state = frozen_transform_state(
        target.sketch,
        spec.target.expected_geometry_count,
        spec.target.expected_constraint_count,
        label=LABEL,
    )
    if (
        len(state.external_reference_records) != spec.expected_external_reference_count
        or len(state.external_geometry_records) != spec.expected_external_geometry_count
    ):
        raise NativeSketchError(f"{LABEL} external state changed; read it and retry.")
    if any(state.solver_issues):
        raise NativeSketchError(f"{LABEL} requires a Sketch without solver issues.")
    require_healthy_external_records(state.external_geometry_records, label=LABEL)
    return SketchTransformSnapshot(target, spec, state, LABEL)


def require_axis_alignment_snapshot_unchanged(
    document: Any,
    snapshot: SketchAxisAlignmentSnapshot,
) -> Any:
    return require_transform_snapshot_unchanged(document, snapshot)


def require_pure_axis_alignment_diagnostic(
    snapshot: SketchAxisAlignmentSnapshot,
) -> None:
    require_pure_transform_diagnostic(snapshot)


def _record(encoded: str) -> dict[str, Any]:
    value = json.loads(encoded)
    if not isinstance(value, dict):
        raise NativeSketchError(f"{LABEL} found invalid constraint state.")
    return value


def _references(record: Mapping[str, Any]) -> dict[int, tuple[int, int]]:
    result = {}
    values = record.get("references", [])
    if not isinstance(values, list):
        return result
    for value in values:
        if not isinstance(value, Mapping):
            continue
        slot = value.get("slot")
        geometry = value.get("geometry_index")
        position = value.get("position", 0)
        if type(slot) is int and type(geometry) is int and type(position) is int:
            result[slot] = (geometry, position)
    return result


def _involves_selection(record: Mapping[str, Any], selected: set[int]) -> bool:
    return any(
        geometry in selected for geometry, _position in _references(record).values()
    )


def _classification(
    record: Mapping[str, Any],
    selected: set[int],
) -> str:
    if not _involves_selection(record, selected):
        return "unchanged"
    constraint_type = record.get("type")
    references = _references(record)
    if constraint_type in {"Horizontal", "Vertical"} and all(
        position == 0 for _geometry, position in references.values()
    ):
        return str(constraint_type).lower()
    if constraint_type == "Symmetric" and references.get(3) in {(-1, 0), (-2, 0)}:
        return "axis_symmetry"
    if constraint_type == "PointOnObject" and references.get(2) in {(-1, 0), (-2, 0)}:
        return "point_on_axis"
    if constraint_type in {"DistanceX", "DistanceY"}:
        return "distance"
    return "unchanged"


def _without_index(record: Mapping[str, Any]) -> dict[str, Any]:
    result = dict(record)
    result.pop("index", None)
    return result


def _expected_constraints(
    snapshot: SketchAxisAlignmentSnapshot,
) -> tuple[tuple[dict[str, Any], ...], dict[str, int]]:
    selected = set(snapshot.spec.geometry_indices)
    anchors: dict[str, int] = {}
    result = []
    counts = {field: 0 for field in _COUNT_FIELDS}
    for encoded in snapshot.state.constraint_records:
        record = _record(encoded)
        classification = _classification(record, selected)
        if classification in {"horizontal", "vertical"}:
            count_field = f"removed_{classification}_constraints"
            counts[count_field] += 1
            geometry = _references(record).get(1, (-2000, 0))[0]
            if classification not in anchors:
                anchors[classification] = geometry
                continue
            result.append(
                {
                    "type": "Parallel",
                    "driving": True,
                    "active": True,
                    "virtual": False,
                    "references": [
                        {"slot": 1, "geometry_index": anchors[classification]},
                        {"slot": 2, "geometry_index": geometry},
                    ],
                }
            )
            counts["created_parallel_constraints"] += 1
            continue
        if classification == "axis_symmetry":
            counts["removed_axis_symmetry_constraints"] += 1
            continue
        if classification == "point_on_axis":
            counts["removed_point_on_axis_constraints"] += 1
            continue
        current = _without_index(record)
        if classification == "distance":
            current["type"] = "Distance"
            counts["converted_distance_constraints"] += 1
        result.append(current)
    for index, record in enumerate(result):
        record["index"] = index
    return tuple(result), counts


def _count(value: Any, field: str) -> int:
    if type(value) is not int or not 0 <= value <= 1_000_000:
        raise NativeSketchError(f"{LABEL} feasibility returned invalid {field}.")
    return value


def parse_axis_alignment_diagnostic(
    result: Any,
    snapshot: SketchAxisAlignmentSnapshot,
) -> SketchAxisAlignmentPlan:
    if not isinstance(result, Mapping) or set(result) != _FIELDS:
        raise NativeSketchError(f"{LABEL} feasibility returned incomplete diagnostics.")
    indices = (
        tuple(result["input_geometry_indices"])
        if isinstance(result["input_geometry_indices"], (list, tuple))
        else ()
    )
    if indices != snapshot.spec.geometry_indices:
        raise NativeSketchError(f"{LABEL} feasibility analyzed a different operation.")
    counts = {field: _count(result[field], field) for field in _COUNT_FIELDS}
    expected_records, expected_counts = _expected_constraints(snapshot)
    if counts != expected_counts or not any(counts.values()):
        raise NativeSketchError(
            f"{LABEL} feasibility returned the wrong constraint rewrite."
        )
    plan = parse_transform_diagnostic(result, snapshot)
    if tuple(_record(value) for value in plan.constraint_records) != expected_records:
        raise NativeSketchError(
            f"{LABEL} feasibility returned the wrong constraint rewrite."
        )
    if (
        plan.geometry_records
        != geometry_records_without_tags(snapshot.state.geometry_records)
        or plan.external_reference_records != snapshot.state.external_reference_records
        or plan.external_geometry_records != snapshot.state.external_geometry_records
        or plan.identity.geometry.created_indices
        or plan.identity.geometry.deleted_indices
    ):
        raise NativeSketchError(f"{LABEL} feasibility changed unrelated Sketch state.")
    deleted_count = (
        counts["removed_horizontal_constraints"]
        + counts["removed_vertical_constraints"]
        + counts["removed_axis_symmetry_constraints"]
        + counts["removed_point_on_axis_constraints"]
    )
    if (
        len(plan.identity.constraints.deleted_indices) != deleted_count
        or len(plan.identity.constraints.created_indices)
        != counts["created_parallel_constraints"]
    ):
        raise NativeSketchError(
            f"{LABEL} feasibility returned the wrong mutation identity."
        )
    return SketchAxisAlignmentPlan(
        plan,
        tuple((field, counts[field]) for field in _COUNT_FIELDS),
    )


def verify_axis_alignment_state(
    document: Any,
    snapshot: SketchAxisAlignmentSnapshot,
    plan: SketchAxisAlignmentPlan,
    receipt: Any,
) -> tuple[Any, tuple[int, ...], tuple[int, ...], tuple[int, ...], tuple[int, ...]]:
    return verify_transform_state(document, snapshot, plan.transform, receipt)
