# SPDX-License-Identifier: LGPL-2.1-or-later

"""Atomic Native implementation of Sketch internal-geometry toggling."""

from __future__ import annotations

import json
from typing import Any, Mapping

from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInsertion import sketch_geometry_result
from VibeCADNativeSketchInternalAlignmentState import (
    PreparedSketchInternalAlignment,
    capture_final_state,
    constraint_geometry,
    current_geometry_index,
    decoded_records,
    expected_final_expressions,
    final_bindings,
    identity_mapping,
    preflight_sketch_internal_alignment,
    require_internal_alignment_unchanged,
)
from VibeCADNativeSketchInternalAlignmentTarget import (
    LABEL,
    OPERATION,
    SketchInternalAlignmentSpec,
    prepare_sketch_internal_alignment,
)
from VibeCADNativeSketchTargets import require_prepared_active_sketch
from VibeCADNativeTargets import object_identity


def _validate_exposure_receipt(
    receipt: Any,
    *,
    source_index: int,
    before_count: int,
    after_count: int,
    missing_keys: set[tuple[str, int]],
) -> None:
    expected_fields = {
        "source_geometry_index",
        "geometry_count_before",
        "geometry_count_after",
        "created_count",
        "created",
    }
    if not isinstance(receipt, Mapping) or set(receipt) != expected_fields:
        raise NativeSketchError(f"{LABEL} returned an invalid exposure receipt.")
    created = receipt["created"]
    if (
        receipt["source_geometry_index"] != source_index
        or receipt["geometry_count_before"] != before_count
        or receipt["geometry_count_after"] != after_count
        or type(receipt["created_count"]) is not int
        or not isinstance(created, list)
        or len(created) != after_count - before_count
    ):
        raise NativeSketchError(f"{LABEL} returned inconsistent exposure counts.")
    reported_roles = []
    for offset, item in enumerate(created):
        if (
            not isinstance(item, Mapping)
            or set(item) != {"geometry_index", "geometry_id", "role"}
            or item["geometry_index"] != before_count + offset
            or type(item["geometry_id"]) is not int
            or not isinstance(item["role"], str)
        ):
            raise NativeSketchError(f"{LABEL} returned invalid exposed geometry.")
        reported_roles.append(item["role"])
    expected_roles = sorted(role for role, _index in missing_keys)
    if sorted(reported_roles) != expected_roles:
        raise NativeSketchError(f"{LABEL} exposed unexpected helper roles.")


def create_sketch_internal_alignment(
    document: Any,
    prepared: PreparedSketchInternalAlignment,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedSketchInternalAlignment):
        raise TypeError("prepared must be exact internal-alignment state")
    sketch = require_internal_alignment_unchanged(document, prepared)
    for plan in sorted(
        prepared.plans,
        key=lambda item: item.requested_index,
        reverse=True,
    ):
        source_index = current_geometry_index(sketch, plan.root_tag)
        if plan.action == "expose_missing":
            before_count = int(sketch.GeometryCount)
            before_keys = {item.key for item in plan.before_helpers}
            try:
                receipt = sketch.exposeInternalGeometry(source_index)
            except Exception as exc:
                raise NativeSketchError(f"Sketcher rejected {LABEL} exposure.") from exc
            after_count = int(sketch.GeometryCount)
            _validate_exposure_receipt(
                receipt,
                source_index=source_index,
                before_count=before_count,
                after_count=after_count,
                missing_keys=set(plan.complete_keys) - before_keys,
            )
        elif plan.action == "hide_unused":
            try:
                sketch.deleteUnusedInternalGeometry(source_index)
            except Exception as exc:
                raise NativeSketchError(f"Sketcher rejected {LABEL} cleanup.") from exc
        else:
            raise NativeSketchError(f"{LABEL} prepared an invalid action.")
    return NativeMutationDraft(
        value=prepared,
        recompute_targets=(sketch,),
        changed=(object_identity(sketch),),
    )


def _record_without_index(record: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in record.items() if key != "index"}


def _remap_constraint(
    record: Mapping[str, Any],
    new_index: int,
    geometry_mapping: Mapping[int, int],
) -> dict[str, Any]:
    result = json.loads(json.dumps(record))
    result["index"] = new_index
    for field in ("references", "elements"):
        for reference in result.get(field, []):
            geometry = reference.get("geometry_index")
            if type(geometry) is int and geometry >= 0:
                mapped = geometry_mapping.get(geometry)
                if mapped is None:
                    raise NativeSketchError(
                        f"{LABEL} retained a constraint on removed geometry."
                    )
                reference["geometry_index"] = mapped
    return result


def _state_name(current: int, complete: int) -> str:
    if current == 0:
        return "hidden"
    if current == complete:
        return "exposed"
    return "partial"


def _verify_geometry_identity(
    prepared: PreparedSketchInternalAlignment,
    final_state: Any,
    final_by_root: Mapping[str, tuple[int, tuple[Any, ...]]],
) -> tuple[dict[int, int], set[int], set[int]]:
    mapping, deleted, created = identity_mapping(
        prepared.state.geometry_tags,
        final_state.geometry_tags,
    )
    expected_deleted = set()
    expected_created = set()
    for plan in prepared.plans:
        before_by_key = {item.key: item for item in plan.before_helpers}
        _root_index, after_helpers = final_by_root[plan.root_tag]
        after_by_key = {item.key: item for item in after_helpers}
        expected_deleted.update(
            item.geometry_index
            for key, item in before_by_key.items()
            if key not in after_by_key
        )
        expected_created.update(
            item.geometry_index
            for key, item in after_by_key.items()
            if key not in before_by_key
        )
    if deleted != expected_deleted or created != expected_created:
        raise NativeSketchError(f"{LABEL} changed the wrong geometry identities.")
    before_records = decoded_records(prepared.state.geometry_records, "geometry")
    after_records = decoded_records(final_state.geometry_records, "geometry")
    for old, new in mapping.items():
        if _record_without_index(before_records[old]) != _record_without_index(
            after_records[new]
        ):
            raise NativeSketchError(f"{LABEL} changed surviving geometry.")
    return mapping, deleted, created


def _verify_constraint_identity(
    prepared: PreparedSketchInternalAlignment,
    final_state: Any,
    final_by_root: Mapping[str, tuple[int, tuple[Any, ...]]],
    geometry_mapping: Mapping[int, int],
    deleted_geometry: set[int],
) -> tuple[dict[int, int], set[int], set[int]]:
    mapping, deleted, created = identity_mapping(
        prepared.state.constraint_tags,
        final_state.constraint_tags,
    )
    before = decoded_records(prepared.state.constraint_records, "constraint")
    after = decoded_records(final_state.constraint_records, "constraint")
    for index in deleted:
        if not constraint_geometry(before[index]) & deleted_geometry:
            raise NativeSketchError(f"{LABEL} removed an unrelated constraint.")
    for old, new in mapping.items():
        if _remap_constraint(before[old], new, geometry_mapping) != after[new]:
            raise NativeSketchError(f"{LABEL} changed a surviving constraint.")

    target_helpers = set()
    target_roots = set()
    created_alignments = set()
    for plan in prepared.plans:
        before_keys = {item.key for item in plan.before_helpers}
        root_index, helpers = final_by_root[plan.root_tag]
        target_roots.add(root_index)
        target_helpers.update(item.geometry_index for item in helpers)
        created_alignments.update(
            item.constraint_index for item in helpers if item.key not in before_keys
        )
    actual_created_alignments = {
        index for index in created if after[index].get("type") == "InternalAlignment"
    }
    if actual_created_alignments != created_alignments:
        raise NativeSketchError(f"{LABEL} created the wrong helper alignments.")
    for index in created - actual_created_alignments:
        record = after[index]
        involved = constraint_geometry(record)
        if (
            record.get("type") not in {"Weight", "Equal"}
            or not involved
            or not involved <= target_helpers
            or record.get("name")
        ):
            raise NativeSketchError(f"{LABEL} created an unrelated constraint.")
    if (
        expected_final_expressions(prepared.state, mapping)
        != final_state.expression_records
    ):
        raise NativeSketchError(f"{LABEL} changed Sketch expressions.")
    return mapping, deleted, created


def verify_sketch_internal_alignment(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value
    if not isinstance(prepared, PreparedSketchInternalAlignment):
        raise TypeError("draft must contain exact internal-alignment state")
    sketch = require_prepared_active_sketch(document, prepared.target)
    final_state = capture_final_state(sketch)
    if (
        final_state.external_reference_records
        != prepared.state.external_reference_records
        or final_state.external_geometry_records
        != prepared.state.external_geometry_records
        or final_state.configuration_token != prepared.state.configuration_token
        or any(final_state.solver_issues)
    ):
        raise NativeSketchError(f"{LABEL} changed unrelated or invalid Sketch state.")

    final_by_root = {}
    changed_targets = []
    for plan in prepared.plans:
        root_index = current_geometry_index(sketch, plan.root_tag)
        helpers = final_bindings(
            sketch,
            final_state,
            root_index,
            plan.complete_keys,
        )
        final_keys = tuple(item.key for item in helpers)
        if set(final_keys) != set(plan.final_keys):
            raise NativeSketchError(f"{LABEL} produced the wrong final helper state.")
        final_by_root[plan.root_tag] = root_index, helpers
        changed_targets.append(
            {
                "geometry_index": root_index,
                "geometry_kind": plan.geometry_kind,
                "action": plan.action,
                "previous_internal_geometry_count": len(plan.before_helpers),
                "internal_geometry_count": len(helpers),
                "complete_internal_geometry_count": len(plan.complete_keys),
                "state": _state_name(len(helpers), len(plan.complete_keys)),
            }
        )

    geometry_mapping, deleted_geometry, created_geometry = _verify_geometry_identity(
        prepared,
        final_state,
        final_by_root,
    )
    _constraint_mapping, deleted_constraints, created_constraints = (
        _verify_constraint_identity(
            prepared,
            final_state,
            final_by_root,
            geometry_mapping,
            deleted_geometry,
        )
    )
    return sketch_geometry_result(
        sketch,
        {
            "operation": OPERATION,
            "changed_targets": changed_targets,
            "created_geometry_count": len(created_geometry),
            "removed_geometry_count": len(deleted_geometry),
            "created_constraint_count": len(created_constraints),
            "removed_constraint_count": len(deleted_constraints),
        },
    )


def prepare_internal_alignment(
    document_uid: str,
    value: Mapping[str, Any],
) -> SketchInternalAlignmentSpec:
    return prepare_sketch_internal_alignment(document_uid, value)


def preflight_internal_alignment(
    context: NativeRuntimeContext,
    spec: SketchInternalAlignmentSpec,
) -> PreparedSketchInternalAlignment:
    return preflight_sketch_internal_alignment(context, spec)
