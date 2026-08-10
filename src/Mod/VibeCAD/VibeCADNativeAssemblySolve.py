# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Native execution and placement proof for the human Assembly solver."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from VibeCADNativeAssemblyComponents import assembly_components
from VibeCADNativeAssemblyGrounding import active_grounded_joints
from VibeCADNativeAssemblyJointConnectors import (
    placement_is_same,
    placement_summary,
)
from VibeCADNativeAssemblyJointGraph import (
    active_regular_joints,
    object_is_valid,
    require_joint_group,
    solver_diagnostics,
    timeline_active,
)
from VibeCADNativeAssemblySolveState import (
    AssemblySolverState,
    capture_assembly_solver_state,
)
from VibeCADNativeAssemblyState import read_active_assembly, same_assembly
from VibeCADNativeMutation import NativeMutationDraft, NativeMutationError
from VibeCADNativeTargets import (
    NativeObjectRef,
    object_identity,
    object_reference,
    read_current_selection,
    resolve_object,
)


NATIVE_ASSEMBLY_SOLVE_FAILED = "NATIVE_ASSEMBLY_SOLVE_FAILED"
MAX_REPORTED_PLACEMENT_CHANGES = 32


class NativeAssemblySolveError(NativeMutationError):
    """The exact active Assembly could not be solved and verified safely."""

    def __init__(self, message: str) -> None:
        super().__init__(NATIVE_ASSEMBLY_SOLVE_FAILED, message)


@dataclass(frozen=True, slots=True)
class AssemblySolveSpec:
    assembly_ref: NativeObjectRef
    expected_solver_state_sha256: str
    expected_component_count: int
    expected_grounded_count: int
    expected_joint_count: int


@dataclass(frozen=True, slots=True)
class PreparedAssemblySolve:
    assembly: Any
    joint_group: Any
    components: tuple[Any, ...]
    grounded_joints: tuple[Any, ...]
    regular_joints: tuple[Any, ...]
    solver_state: AssemblySolverState
    active_before: Any
    selection_before: dict[str, Any]


def _exact_active_assembly(
    document: Any,
    spec: AssemblySolveSpec,
    active_reader: Callable[[Any], Any | None],
) -> Any:
    assembly = resolve_object(
        document,
        spec.assembly_ref,
        expected_types=("Assembly::AssemblyObject",),
    )
    active = active_reader(document)
    if not same_assembly(assembly, active):
        raise NativeAssemblySolveError(
            "The human-active Assembly changed; read current Assemble state and retry."
        )
    if not timeline_active(assembly):
        raise NativeAssemblySolveError(
            "The human-active Assembly is outside the current document history."
        )
    return assembly


def preflight_assembly_solve(
    document: Any,
    spec: AssemblySolveSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> PreparedAssemblySolve:
    """Resolve one exact current solver graph without changing the document."""

    if not isinstance(spec, AssemblySolveSpec):
        raise TypeError("spec must be an AssemblySolveSpec")
    assembly = _exact_active_assembly(document, spec, active_reader)
    joint_group = require_joint_group(assembly)
    components = assembly_components(assembly)
    grounded = active_grounded_joints(joint_group)
    regular = active_regular_joints(joint_group)
    if len(components) != spec.expected_component_count:
        raise NativeAssemblySolveError(
            "The active Assembly component count changed; read current Assemble state and retry."
        )
    if len(grounded) != spec.expected_grounded_count:
        raise NativeAssemblySolveError(
            "The active Assembly grounded count changed; read current Assemble state and retry."
        )
    if len(regular) != spec.expected_joint_count:
        raise NativeAssemblySolveError(
            "The active Assembly joint count changed; read current Assemble state and retry."
        )
    solver_state = capture_assembly_solver_state(assembly)
    if solver_state.state_sha256 != spec.expected_solver_state_sha256:
        raise NativeAssemblySolveError(
            "The active Assembly placement state changed; read current Assemble state and retry."
        )
    return PreparedAssemblySolve(
        assembly=assembly,
        joint_group=joint_group,
        components=components,
        grounded_joints=grounded,
        regular_joints=regular,
        solver_state=solver_state,
        active_before=assembly,
        selection_before=selection_reader(document),
    )


def _record_by_name(state: AssemblySolverState) -> dict[str, Any]:
    return {str(record.obj.Name): record for record in state.records}


def _unique_identities(objects: tuple[Any, ...]) -> tuple[Any, ...]:
    result = []
    seen: set[tuple[str, str, str]] = set()
    for obj in objects:
        identity = object_identity(obj)
        key = (identity.document_uid, identity.object_name, identity.type_id)
        if key not in seen:
            seen.add(key)
            result.append(identity)
    return tuple(result)


def _solver_summary(diagnostics: dict[str, Any]) -> dict[str, Any]:
    joints = list(diagnostics.get("joints", ()) or ())
    maximum_residual = max(
        (float(item.get("maximum_absolute_residual", 0.0)) for item in joints),
        default=0.0,
    )
    return {
        "solver_status": diagnostics.get("solver_status"),
        "remaining_degrees_of_freedom": diagnostics.get(
            "remaining_degrees_of_freedom",
            0,
        ),
        "has_conflicts": bool(diagnostics.get("has_conflicts", False)),
        "has_redundancies": bool(diagnostics.get("has_redundancies", False)),
        "has_partial_redundancies": bool(
            diagnostics.get("has_partial_redundancies", False)
        ),
        "has_malformed_constraints": bool(
            diagnostics.get("has_malformed_constraints", False)
        ),
        "diagnostic_joint_counts": {
            "conflicting": len(diagnostics.get("conflicting_joints", ()) or ()),
            "redundant": len(diagnostics.get("redundant_joints", ()) or ()),
            "partially_redundant": len(
                diagnostics.get("partially_redundant_joints", ()) or ()
            ),
            "malformed": len(diagnostics.get("malformed_joints", ()) or ()),
        },
        "maximum_absolute_residual": maximum_residual,
        "residual_tolerance": float(diagnostics.get("residual_tolerance", 1.0e-6)),
    }


def _solve_failure_message(code: int, diagnostics: dict[str, Any]) -> str:
    message = str(diagnostics.get("solver_message") or "").strip()
    if not message:
        message = f"native solver returned {code}"
    return f"Assembly solve failed: {message}"


def apply_assembly_solve(
    document: Any,
    spec: AssemblySolveSpec,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> NativeMutationDraft:
    """Run the same native solver lifecycle as the human Solve command."""

    prepared = preflight_assembly_solve(
        document,
        spec,
        active_reader=active_reader,
        selection_reader=selection_reader,
    )
    before_objects = tuple(document.Objects)
    before_names = {str(obj.Name) for obj in before_objects}
    solve = getattr(prepared.assembly, "solve", None)
    if not callable(solve):
        raise NativeAssemblySolveError(
            "The human-active Assembly does not expose the native solver."
        )
    try:
        solver_code = int(solve(False))
    except Exception as exc:
        raise NativeAssemblySolveError(
            "The native Assembly solver failed before producing a result."
        ) from exc
    try:
        recompute_result = document.recompute()
    except Exception as exc:
        raise NativeAssemblySolveError(
            "The solved Assembly document failed to recompute."
        ) from exc
    if recompute_result is False:
        raise NativeAssemblySolveError(
            "The solved Assembly document failed to recompute."
        )
    try:
        diagnostics = solver_diagnostics(prepared.assembly)
    except Exception as exc:
        raise NativeAssemblySolveError(
            f"The native Assembly solver returned {solver_code}, but its "
            "diagnostics could not be read."
        ) from exc
    if solver_code != 0 or not object_is_valid(prepared.assembly):
        raise NativeAssemblySolveError(_solve_failure_message(solver_code, diagnostics))
    try:
        after_state = capture_assembly_solver_state(prepared.assembly)
    except Exception as exc:
        raise NativeAssemblySolveError(
            "The solved Assembly placement state could not be verified."
        ) from exc
    after_objects = tuple(document.Objects)
    after_names = {str(obj.Name) for obj in after_objects}
    deleted_names = before_names - after_names
    if deleted_names:
        raise NativeAssemblySolveError(
            "The native Assembly solver removed objects from the exact document graph."
        )
    created = tuple(obj for obj in after_objects if str(obj.Name) not in before_names)
    before_by_name = _record_by_name(prepared.solver_state)
    after_by_name = _record_by_name(after_state)
    if set(before_by_name) != set(after_by_name):
        raise NativeAssemblySolveError(
            "The native Assembly solver changed the exact placement-object graph."
        )
    changed = tuple(
        after_by_name[name].obj
        for name, before in before_by_name.items()
        if not placement_is_same(before.placement, after_by_name[name].placement)
        or before.placement_locks != after_by_name[name].placement_locks
    )
    graph_changed = (prepared.assembly, prepared.joint_group) if created else ()
    return NativeMutationDraft(
        value={
            "spec": spec,
            "prepared": prepared,
            "before_objects": before_objects,
            "before_state": prepared.solver_state,
            "after_state": after_state,
            "created_objects": created,
            "diagnostics": diagnostics,
            "solver_code": solver_code,
        },
        recompute_targets=(),
        created=tuple(object_identity(obj) for obj in created),
        changed=_unique_identities((*changed, *graph_changed)),
    )


def _same_record_objects(
    expected: AssemblySolverState,
    current: AssemblySolverState,
) -> bool:
    expected_records = _record_by_name(expected)
    current_records = _record_by_name(current)
    if set(expected_records) != set(current_records):
        return False
    for name, before in expected_records.items():
        after = current_records[name]
        if (
            int(before.obj.ID) != int(after.obj.ID)
            or str(before.obj.TypeId) != str(after.obj.TypeId)
            or before.obj is not after.obj
            or not placement_is_same(before.placement, after.placement)
            or before.placement_locks != after.placement_locks
        ):
            return False
    return True


def _verify_created_grounding(
    created: tuple[Any, ...],
    joint_group: Any,
    before_state: AssemblySolverState,
) -> list[dict[str, Any]]:
    before = _record_by_name(before_state)
    result = []
    seen_targets: set[str] = set()
    for joint in created:
        component = getattr(joint, "ObjectToGround", None)
        component_name = str(getattr(component, "Name", "") or "")
        record = before.get(component_name)
        if (
            str(getattr(joint, "TypeId", "") or "") != "App::FeaturePython"
            or joint not in list(getattr(joint_group, "Group", ()) or ())
            or not timeline_active(joint)
            or str(getattr(joint, "VibeCADTimelineRole", "") or "") != "operation"
            or record is None
            or component is not record.obj
            or not any(read_only for _name, read_only in record.placement_locks)
            or component_name in seen_targets
        ):
            raise NativeAssemblySolveError(
                "The native Assembly solver created an unexpected document object."
            )
        seen_targets.add(component_name)
        result.append(
            {
                "joint": object_reference(joint),
                "component": object_reference(component),
            }
        )
    return result


def verify_assembly_solve(
    document: Any,
    draft: NativeMutationDraft,
    *,
    active_reader: Callable[[Any], Any | None] = read_active_assembly,
    selection_reader: Callable[[Any], dict[str, Any]] = read_current_selection,
) -> dict[str, Any]:
    """Prove graph identity, exact placements, grounding, and solver health."""

    value = draft.value
    spec = value["spec"]
    prepared = value["prepared"]
    assembly = prepared.assembly
    joint_group = prepared.joint_group
    if (
        document.getObject(str(assembly.Name)) is not assembly
        or document.getObject(str(joint_group.Name)) is not joint_group
        or not same_assembly(prepared.active_before, active_reader(document))
        or not timeline_active(assembly)
        or not timeline_active(joint_group)
        or not object_is_valid(assembly)
    ):
        raise NativeAssemblySolveError(
            "Assembly solve changed the exact active Assembly identity or validity."
        )
    before_objects = tuple(value["before_objects"])
    created = tuple(value["created_objects"])
    expected_objects = (*before_objects, *created)
    if tuple(document.Objects) != expected_objects:
        raise NativeAssemblySolveError(
            "Assembly solve changed objects outside the exact native solver graph."
        )
    try:
        current_state = capture_assembly_solver_state(assembly)
    except Exception as exc:
        raise NativeAssemblySolveError(
            "The Assembly placement state could not be read before commit."
        ) from exc
    after_state = value["after_state"]
    if not _same_record_objects(after_state, current_state):
        raise NativeAssemblySolveError(
            "An Assembly placement changed after native solver verification."
        )
    if len(assembly_components(assembly)) != spec.expected_component_count:
        raise NativeAssemblySolveError(
            "Assembly solve changed the active component count."
        )
    regular = active_regular_joints(joint_group)
    if regular != prepared.regular_joints or len(regular) != spec.expected_joint_count:
        raise NativeAssemblySolveError(
            "Assembly solve changed the exact active regular-joint graph."
        )
    grounded = active_grounded_joints(joint_group)
    if grounded != (*prepared.grounded_joints, *created):
        raise NativeAssemblySolveError(
            "Assembly solve changed the exact active grounding graph."
        )
    repairs = _verify_created_grounding(created, joint_group, value["before_state"])
    before_records = _record_by_name(value["before_state"])
    after_records = _record_by_name(after_state)
    grounded_targets = {
        str(getattr(joint.ObjectToGround, "Name", "") or "")
        for joint in grounded
        if getattr(joint, "ObjectToGround", None) is not None
    }
    if any(
        name in before_records
        and not placement_is_same(
            before_records[name].placement,
            after_records[name].placement,
        )
        for name in grounded_targets
    ):
        raise NativeAssemblySolveError(
            "The native solver moved a grounded Assembly component."
        )
    if selection_reader(document) != prepared.selection_before:
        raise NativeAssemblySolveError(
            "Assembly solve changed the human's exact selection."
        )
    try:
        diagnostics = solver_diagnostics(assembly)
    except Exception as exc:
        raise NativeAssemblySolveError(
            "The Assembly solver diagnostics could not be read before commit."
        ) from exc
    if diagnostics.get("solver_status") != 0 or not object_is_valid(assembly):
        raise NativeAssemblySolveError(
            "The Assembly solver result became invalid before commit."
        )
    placement_changes = []
    lock_changes = []
    for name, before in before_records.items():
        after = after_records[name]
        if not placement_is_same(before.placement, after.placement):
            if len(placement_changes) < MAX_REPORTED_PLACEMENT_CHANGES:
                placement_changes.append(
                    {
                        "object": object_reference(after.obj),
                        "before": placement_summary(before.placement),
                        "after": placement_summary(after.placement),
                    }
                )
        if before.placement_locks != after.placement_locks:
            lock_changes.append(
                {
                    "object": object_reference(after.obj),
                    "placement_locks": dict(after.placement_locks),
                }
            )
    moved_count = sum(
        not placement_is_same(before.placement, after_records[name].placement)
        for name, before in before_records.items()
    )
    result = {
        "assembly": object_reference(assembly),
        "component_count": len(assembly_components(assembly)),
        "grounded_count": len(grounded),
        "joint_count": len(regular),
        "placement_object_count": len(after_state.records),
        "placement_state_before_sha256": value["before_state"].state_sha256,
        "placement_state_after_sha256": after_state.state_sha256,
        "moved_object_count": moved_count,
        "placement_changes": placement_changes,
        "placement_lock_changes": lock_changes,
        "grounding_repairs": repairs,
        "grounded_placements_unchanged": True,
        "solver": _solver_summary(diagnostics),
        "active_assembly_unchanged": True,
        "selection_unchanged": True,
    }
    if moved_count > len(placement_changes):
        result["placement_changes_truncated"] = True
    return result
