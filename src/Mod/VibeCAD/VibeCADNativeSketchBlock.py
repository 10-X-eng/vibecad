# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic whole-edge Block constraints for the human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBlockTarget import (
    LABEL,
    ResolvedSketchBlock,
    SketchBlockSpec,
    make_block_constraints,
    prepare_sketch_block_target,
    resolve_sketch_block,
)
from VibeCADNativeSketchConstraintAppend import (
    ExactConstraintExpectation,
    add_exact_constraints,
    diagnose_exact_block_constraints,
    sketch_solver_issues,
    verify_exact_constraint_appends,
)
from VibeCADNativeSketchConstraintTargets import (
    PreparedSketchConstraintTarget,
    current_sketch_constraint_records,
    preflight_sketch_constraint_target,
    require_unchanged_sketch_constraint_target,
)
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


@dataclass(frozen=True, slots=True)
class PreparedSketchBlock:
    target: PreparedSketchConstraintTarget
    spec: SketchBlockSpec
    resolved: ResolvedSketchBlock
    solver_issues: tuple[tuple[int, ...], ...]


def prepare_sketch_block(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchBlockSpec:
    return prepare_sketch_block_target(document_uid, value)


def preflight_sketch_block(
    context: NativeRuntimeContext,
    spec: SketchBlockSpec,
) -> PreparedSketchBlock:
    if not isinstance(spec, SketchBlockSpec):
        raise TypeError("spec must be a SketchBlockSpec")
    target = preflight_sketch_constraint_target(context, spec.target)
    sketch = target.target.sketch
    resolved = resolve_sketch_block(sketch, spec)
    solver_issues = sketch_solver_issues(sketch, LABEL)
    diagnose_exact_block_constraints(
        sketch,
        make_block_constraints(resolved),
        expected_index=spec.target.target.expected_constraint_count,
        label=LABEL,
    )
    geometry, constraints, external = current_sketch_constraint_records(
        sketch,
        spec.target,
    )
    if (
        geometry != target.geometry_records
        or constraints != target.constraint_records
        or external != target.external_geometry_records
        or sketch_solver_issues(sketch, LABEL) != solver_issues
    ):
        raise NativeSketchError(f"{LABEL} feasibility check changed the active Sketch.")
    return PreparedSketchBlock(target, spec, resolved, solver_issues)


def create_sketch_block(
    document: Any,
    prepared: PreparedSketchBlock,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchBlock):
        raise TypeError("prepared must be a PreparedSketchBlock")
    sketch = require_unchanged_sketch_constraint_target(
        document,
        prepared.target,
        stage="after Block preflight",
    )
    indices = add_exact_constraints(
        sketch,
        make_block_constraints(prepared.resolved),
        expected_index=prepared.spec.target.target.expected_constraint_count,
        label="Block constraints",
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "constraint_indices": indices},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _expected_geometry_records(prepared: PreparedSketchBlock) -> tuple[str, ...]:
    selected = {
        element.geometry_index for element in prepared.resolved.references
    }
    result = []
    for encoded in prepared.target.geometry_records:
        record = json.loads(encoded)
        if int(record["index"]) in selected:
            record["blocked"] = True
        result.append(
            json.dumps(
                record,
                ensure_ascii=True,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    return tuple(result)


def _expectations(
    resolved: ResolvedSketchBlock,
) -> tuple[ExactConstraintExpectation, ...]:
    return tuple(
        ExactConstraintExpectation(
            "Block",
            ({"slot": 1, "geometry_index": element.geometry_index},),
            True,
            None,
            0.0,
        )
        for element in resolved.references
    )


def _frozen_geometry(
    records: tuple[str, ...],
    resolved: ResolvedSketchBlock,
) -> list[dict[str, Any]]:
    selected = {
        element.geometry_index for element in resolved.references
    }
    keys = (
        "index",
        "geometry_id",
        "type_id",
        "kind",
        "internal_type",
        "construction",
        "blocked",
    )
    return [
        {key: record[key] for key in keys if key in record}
        for encoded in records
        for record in (json.loads(encoded),)
        if int(record["index"]) in selected
    ]


def verify_sketch_block(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchBlock):
        raise TypeError("draft must contain a PreparedSketchBlock")
    raw_indices = draft.value.get("constraint_indices")
    if not isinstance(raw_indices, tuple):
        raise TypeError("draft must contain exact Block constraint indices")
    sketch = require_prepared_active_sketch(document, prepared.target.target)
    expected_geometry = _expected_geometry_records(prepared)
    constraints = verify_exact_constraint_appends(
        sketch,
        prepared.target,
        constraint_indices=tuple(int(value) for value in raw_indices),
        solver_issues=prepared.solver_issues,
        expectations=_expectations(prepared.resolved),
        label=LABEL,
        expected_geometry_records=expected_geometry,
    )
    geometry, _constraint_records, _external = current_sketch_constraint_records(
        sketch,
        prepared.spec.target,
    )
    if geometry != expected_geometry:
        raise NativeSketchError(
            f"{LABEL} changed geometry beyond setting the exact blocked flags."
        )
    return sketch_geometry_result(
        sketch,
        {
            "operation": "constrain_block",
            "constraints": list(constraints),
            "frozen_geometry": _frozen_geometry(geometry, prepared.resolved),
        },
    )
