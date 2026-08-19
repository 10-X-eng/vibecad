# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transactional deletion from the one human-opened Sketch."""

from __future__ import annotations

from dataclasses import dataclass
import json
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchBSplineHelperState import remap_constraint
from VibeCADNativeSketchConstraintAppend import sketch_solver_issues
from VibeCADNativeSketchConstraintToggleState import (
    SketchExpressionRecord,
    sketch_expression_records,
)
from VibeCADNativeSketchCurvePointDiagnostic import aligned_internal_geometry
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchExactState import canonical_sketch_records
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchInternalAlignmentState import constraint_geometry
from VibeCADNativeSketchMutationState import (
    collection_index_map,
    expected_expression_records,
)
from VibeCADNativeSketchState import (
    iter_sketch_constraint_records,
    iter_sketch_external_geometry_records,
    iter_sketch_geometry_records,
)
from VibeCADNativeSketchTargets import (
    ActiveSketchTargetSpec,
    PreparedActiveSketchTarget,
    preflight_active_sketch,
    prepare_active_sketch_target,
    require_prepared_active_sketch,
)
from VibeCADNativeTargets import object_identity


LABEL = "Sketch Delete Geometry"
MAX_DELETE_GEOMETRY = 64
_FIELDS = frozenset(
    {
        "sketch",
        "expected_geometry_count",
        "expected_constraint_count",
        "geometry_ids",
    }
)


@dataclass(frozen=True, slots=True)
class SketchDeleteGeometrySpec:
    target: ActiveSketchTargetSpec
    geometry_ids: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class PreparedSketchDeleteGeometry:
    target: PreparedActiveSketchTarget
    spec: SketchDeleteGeometrySpec
    deletion_indices: tuple[int, ...]
    expected_deleted_indices: frozenset[int]
    geometry_records: tuple[str, ...]
    constraint_records: tuple[str, ...]
    constraint_tags: tuple[str, ...]
    external_geometry_records: tuple[str, ...]
    expression_records: tuple[SketchExpressionRecord, ...]
    solver_issues: tuple[tuple[int, ...], ...]


def _geometry_ids(value: Any) -> tuple[int, ...]:
    if (
        not isinstance(value, list)
        or not 1 <= len(value) <= MAX_DELETE_GEOMETRY
        or any(type(item) is not int or not 0 <= item <= 999_999 for item in value)
        or len(set(value)) != len(value)
    ):
        raise NativeSketchError(
            f"{LABEL} geometry_ids must contain one through "
            f"{MAX_DELETE_GEOMETRY} unique nonnegative IDs."
        )
    return tuple(sorted(value))


def prepare_sketch_delete_geometry(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchDeleteGeometrySpec:
    if not isinstance(value, Mapping) or set(value) != _FIELDS:
        raise NativeSketchError(f"A {LABEL} definition has incorrect fields.")
    return SketchDeleteGeometrySpec(
        prepare_active_sketch_target(
            document_uid,
            sketch=value["sketch"],
            expected_geometry_count=value["expected_geometry_count"],
            expected_constraint_count=value["expected_constraint_count"],
        ),
        _geometry_ids(value["geometry_ids"]),
    )


def _geometry_id_map(records: tuple[str, ...]) -> dict[int, int]:
    result: dict[int, int] = {}
    for index, record in enumerate(records):
        geometry_id = json.loads(record).get("geometry_id")
        if type(geometry_id) is not int or geometry_id < 0 or geometry_id in result:
            raise NativeSketchError(f"{LABEL} stable geometry identity is unavailable.")
        result[geometry_id] = index
    return result


def _records(
    sketch: Any,
    spec: SketchDeleteGeometrySpec,
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, ...]]:
    return (
        canonical_sketch_records(
            iter_sketch_geometry_records(
                sketch,
                spec.target.expected_geometry_count,
            )
        ),
        canonical_sketch_records(
            iter_sketch_constraint_records(
                sketch,
                spec.target.expected_constraint_count,
            )
        ),
        canonical_sketch_records(iter_sketch_external_geometry_records(sketch)),
    )


def _group_roles(sketch: Any) -> tuple[dict[int, tuple[int, ...]], dict[int, int]]:
    handles: dict[int, tuple[int, ...]] = {}
    members: dict[int, int] = {}
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} group state is unavailable.") from exc
    for constraint in constraints:
        if str(getattr(constraint, "Type", "") or "") not in {"Group", "Text"}:
            continue
        raw = getattr(constraint, "Elements", None)
        if not isinstance(raw, (list, tuple)) or not raw:
            raise NativeSketchError(f"{LABEL} found malformed grouped geometry.")
        try:
            elements = tuple((int(item[0]), int(item[1])) for item in raw)
        except (IndexError, TypeError, ValueError) as exc:
            raise NativeSketchError(f"{LABEL} found malformed grouped geometry.") from exc
        if any(index < 0 or position != 0 for index, position in elements):
            raise NativeSketchError(f"{LABEL} found malformed grouped geometry.")
        handle = elements[0][0]
        if handle in handles:
            raise NativeSketchError(f"{LABEL} found a reused group handle.")
        handles[handle] = tuple(index for index, _position in elements[1:])
        for member in handles[handle]:
            if member in members:
                raise NativeSketchError(f"{LABEL} found multiply grouped geometry.")
            members[member] = handle
    return handles, members


def _constraint_tags(sketch: Any, expected_count: int) -> tuple[str, ...]:
    try:
        constraints = tuple(sketch.Constraints)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} constraint identity is unavailable.") from exc
    tags = tuple(str(getattr(constraint, "Tag", "") or "") for constraint in constraints)
    if (
        len(tags) != expected_count
        or any(not tag or len(tag) > 128 for tag in tags)
        or len(set(tags)) != len(tags)
    ):
        raise NativeSketchError(f"{LABEL} constraint identity is unavailable.")
    return tags


def preflight_sketch_delete_geometry(
    context: NativeRuntimeContext,
    spec: SketchDeleteGeometrySpec,
) -> PreparedSketchDeleteGeometry:
    if not isinstance(spec, SketchDeleteGeometrySpec):
        raise TypeError("spec must be a SketchDeleteGeometrySpec")
    target = preflight_active_sketch(context, spec.target)
    geometry, constraints, external = _records(target.sketch, spec)
    geometry_ids = _geometry_id_map(geometry)
    missing = [item for item in spec.geometry_ids if item not in geometry_ids]
    if missing:
        raise NativeSketchError(
            f"{LABEL} geometry_ids {missing} do not exist; available geometry_ids "
            f"are {sorted(geometry_ids)} (geometry_count {len(geometry)})."
        )

    handles, members = _group_roles(target.sketch)
    selected = {geometry_ids[item] for item in spec.geometry_ids}
    ids_by_index = {index: geometry_id for geometry_id, index in geometry_ids.items()}
    grouped_members = selected & set(members)
    if grouped_members:
        member = min(grouped_members)
        raise NativeSketchError(
            f"Sketch geometry_id {ids_by_index[member]} belongs to group handle "
            f"geometry_id {ids_by_index[members[member]]}; delete that handle instead."
        )
    for index in selected:
        if "internal_type" in json.loads(geometry[index]):
            raise NativeSketchError(
                f"Sketch geometry_id {ids_by_index[index]} is internal helper geometry; "
                "delete its owning curve instead."
            )

    deletion = set(selected)
    for handle in selected & set(handles):
        deletion.update(handles[handle])
    expected_deleted = set(deletion)
    for index in tuple(deletion):
        expected_deleted.update(aligned_internal_geometry(index, geometry, constraints))

    solver = sketch_solver_issues(target.sketch, LABEL)
    if any(solver):
        raise NativeSketchError(f"{LABEL} requires a Sketch without solver issues.")
    constraint_tags = _constraint_tags(
        target.sketch,
        spec.target.expected_constraint_count,
    )
    expressions = sketch_expression_records(target.sketch, constraints, label=LABEL)
    return PreparedSketchDeleteGeometry(
        target,
        spec,
        tuple(sorted(deletion)),
        frozenset(expected_deleted),
        geometry,
        constraints,
        constraint_tags,
        external,
        expressions,
        solver,
    )


def _require_unchanged(
    document: Any,
    prepared: PreparedSketchDeleteGeometry,
) -> Any:
    sketch = require_prepared_active_sketch(document, prepared.target)
    if _records(sketch, prepared.spec) != (
        prepared.geometry_records,
        prepared.constraint_records,
        prepared.external_geometry_records,
    ) or _constraint_tags(
        sketch,
        prepared.spec.target.expected_constraint_count,
    ) != prepared.constraint_tags:
        raise NativeSketchError(f"The active Sketch changed after {LABEL} preflight.")
    if (
        sketch_expression_records(
            sketch,
            prepared.constraint_records,
            label=LABEL,
        )
        != prepared.expression_records
        or sketch_solver_issues(sketch, LABEL) != prepared.solver_issues
    ):
        raise NativeSketchError(f"The active Sketch changed after {LABEL} preflight.")
    return sketch


def create_sketch_delete_geometry(
    document: Any,
    prepared: PreparedSketchDeleteGeometry,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchDeleteGeometry):
        raise TypeError("prepared must be a PreparedSketchDeleteGeometry")
    sketch = _require_unchanged(document, prepared)
    try:
        receipt = sketch.delGeometries(list(prepared.deletion_indices), True)
    except Exception as exc:
        raise NativeSketchError(
            "Sketcher rejected the exact Delete Geometry operation."
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "receipt": receipt},
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _decode(records: tuple[str, ...]) -> tuple[dict[str, Any], ...]:
    return tuple(json.loads(record) for record in records)


def verify_sketch_delete_geometry(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    receipt = draft.value.get("receipt") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedSketchDeleteGeometry):
        raise TypeError("draft must contain exact Delete Geometry state")
    sketch = require_prepared_active_sketch(document, prepared.target)
    try:
        valid = bool(sketch.isValid())
        geometry_count = int(sketch.GeometryCount)
        constraint_count = int(sketch.ConstraintCount)
    except Exception as exc:
        raise NativeSketchError(f"{LABEL} final state is unavailable.") from exc
    if not valid or geometry_count < 0 or constraint_count < 0:
        raise NativeSketchError(f"{LABEL} left the active Sketch invalid.")

    geometry = canonical_sketch_records(iter_sketch_geometry_records(sketch))
    constraints = canonical_sketch_records(iter_sketch_constraint_records(sketch))
    external = canonical_sketch_records(iter_sketch_external_geometry_records(sketch))
    geometry_map, deleted_geometry, created_geometry = collection_index_map(
        receipt,
        "geometry",
        len(prepared.geometry_records),
        geometry_count,
        label=LABEL,
    )
    constraint_map, deleted_constraints, created_constraints = collection_index_map(
        receipt,
        "constraints",
        len(prepared.constraint_records),
        constraint_count,
        label=LABEL,
    )
    if set(deleted_geometry) != prepared.expected_deleted_indices or created_geometry:
        raise NativeSketchError(f"{LABEL} changed the wrong geometry identities.")
    if created_constraints:
        raise NativeSketchError(f"{LABEL} created unexpected constraints.")

    before_geometry = _decode(prepared.geometry_records)
    after_geometry = _decode(geometry)
    for old_index, new_index in geometry_map.items():
        expected = dict(before_geometry[old_index])
        expected["index"] = new_index
        if expected != after_geometry[new_index]:
            raise NativeSketchError(f"{LABEL} changed surviving geometry.")
    for old_index, tag in deleted_geometry.items():
        if tag != str(before_geometry[old_index].get("tag", "") or ""):
            raise NativeSketchError(f"{LABEL} reported the wrong deleted geometry.")

    before_constraints = _decode(prepared.constraint_records)
    after_constraints = _decode(constraints)
    actual_constraint_tags = _constraint_tags(sketch, constraint_count)
    deleted_set = set(deleted_geometry)
    for old_index, record in enumerate(before_constraints):
        involved = constraint_geometry(record)
        if old_index in deleted_constraints:
            if not involved & deleted_set:
                raise NativeSketchError(f"{LABEL} deleted an unrelated constraint.")
            if deleted_constraints[old_index] != prepared.constraint_tags[old_index]:
                raise NativeSketchError(f"{LABEL} reported the wrong deleted constraint.")
            continue
        new_index = constraint_map.get(old_index)
        if new_index is None:
            raise NativeSketchError(f"{LABEL} lost a constraint identity.")
        if actual_constraint_tags[new_index] != prepared.constraint_tags[old_index]:
            raise NativeSketchError(f"{LABEL} changed a surviving constraint identity.")
        if not involved & deleted_set:
            expected = remap_constraint(record, new_index, geometry_map)
            if expected != after_constraints[new_index]:
                raise NativeSketchError(f"{LABEL} changed an unrelated constraint.")

    if external != prepared.external_geometry_records:
        raise NativeSketchError(f"{LABEL} changed external geometry.")
    if sketch_solver_issues(sketch, LABEL) != prepared.solver_issues:
        raise NativeSketchError(f"{LABEL} left the active Sketch with solver issues.")
    if sketch_expression_records(sketch, constraints, label=LABEL) != (
        expected_expression_records(prepared.expression_records, constraint_map)
    ):
        raise NativeSketchError(f"{LABEL} changed unrelated expressions.")

    return sketch_geometry_result(
        sketch,
        {
            "operation": "delete_geometry",
            "requested_geometry_ids": list(prepared.spec.geometry_ids),
            "deleted_geometry_count": len(deleted_geometry),
            "deleted_constraint_count": len(deleted_constraints),
        },
    )
