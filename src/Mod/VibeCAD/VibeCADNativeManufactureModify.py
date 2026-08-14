# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, deterministic state changes for existing CAM operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import (
    job_state,
    operation_active_state,
    operation_reference_state,
    resolve_job_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


MAX_ACTIVE_TARGETS = 64
_TARGET_FIELDS = frozenset({"object_name", "expected_active", "active"})


@dataclass(frozen=True, slots=True)
class OperationActiveSpec:
    job: Mapping[str, Any]
    targets: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class _TimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class _PreparedActiveTarget:
    selected: Any
    base: Any
    expected_active: bool
    desired_active: bool
    selected_before: Mapping[str, Any]
    base_before: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class PreparedOperationActive:
    job: Any
    job_before: Mapping[str, Any]
    targets: tuple[_PreparedActiveTarget, ...]
    operation_group_before: tuple[Any, ...]
    operation_states_before: tuple[Mapping[str, Any], ...]
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Any
    timeline_before: _TimelineState


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _timeline_state(document: Any) -> _TimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != (
        "App::DocumentTimeline"
    ):
        _error(
            "Changing CAM operation state requires a valid document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    try:
        operations = tuple(timeline.Operations or ())
        visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
        suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
        position = int(timeline.Position)
    except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError) as exc:
        raise NativeManufactureError(
            "The document History cannot be read safely.",
            error_code="NATIVE_MANUFACTURE_HISTORY_INVALID",
        ) from exc
    if len(operations) != len(visibility) or len(operations) != len(suppression):
        _error(
            "The document History has inconsistent state arrays.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return _TimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _operation_group(job: Any) -> tuple[Any, ...]:
    operations = getattr(job, "Operations", None)
    group = tuple(getattr(operations, "Group", ()) or ())
    if len(group) > 100_000:
        _error(
            "The CAM Job operation group exceeds the supported safety bound.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    names = tuple(str(getattr(operation, "Name", "") or "") for operation in group)
    if any(not name for name in names) or len(set(names)) != len(names):
        _error(
            "The CAM Job operation group has no unique stable names.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    return group


def _base_operation(operation: Any) -> Any:
    try:
        import Path.Dressup.Utils as PathDressup

        return PathDressup.baseOp(operation)
    except Exception as exc:
        raise NativeManufactureError(
            f"CAM operation {operation.Name!r} has an unreadable dress-up chain.",
            error_code="NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        ) from exc


def _require_usable_operation(document: Any, job: Any, operation: Any) -> Any:
    try:
        import Path.Main.Job as PathJob
        import PathScripts.PathUtils as PathUtils
        from Path.CommandBoundary import is_timeline_input_usable

        base = _base_operation(operation)
        parent = PathUtils.findParentJob(base)
        operation_usable = is_timeline_input_usable(operation, document)
        base_usable = is_timeline_input_usable(base, document)
        usable = operation_usable and base_usable
        is_path = callable(getattr(operation, "isDerivedFrom", None)) and (
            operation.isDerivedFrom("Path::Feature")
        )
        valid_job = isinstance(getattr(job, "Proxy", None), PathJob.ObjectJob)
    except Exception as exc:
        raise NativeManufactureError(
            f"CAM operation {operation.Name!r} could not be validated.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if (
        not valid_job
        or not is_path
        or not usable
        or base is None
        or getattr(base, "Document", None) is not document
        or document.getObject(str(getattr(base, "Name", "") or "")) is not base
        or "Active" not in tuple(getattr(base, "PropertiesList", ()) or ())
        or parent is not job
    ):
        _error(
            f"CAM operation {operation.Name!r} is not an active-state target of the exact Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "object_name": str(operation.Name),
                "operation_is_path": bool(is_path),
                "operation_is_usable": bool(operation_usable),
                "base_object_name": str(getattr(base, "Name", "") or ""),
                "base_is_live": bool(
                    base is not None
                    and getattr(base, "Document", None) is document
                    and document.getObject(str(getattr(base, "Name", "") or ""))
                    is base
                ),
                "base_has_active_state": bool(
                    base is not None
                    and "Active"
                    in tuple(getattr(base, "PropertiesList", ()) or ())
                ),
                "base_is_usable": bool(base_usable),
                "base_belongs_to_job": bool(parent is job),
            },
        )
    if type(base.Active) is not bool:
        _error(
            f"CAM operation {operation.Name!r} exposes an invalid Active property.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return base


def prepare_operation_active_spec(
    job: Mapping[str, Any],
    targets: Any,
) -> OperationActiveSpec:
    if not isinstance(job, Mapping):
        _error("job must be one exact CAM Job target.")
    if not isinstance(targets, list) or not 1 <= len(targets) <= MAX_ACTIVE_TARGETS:
        _error(
            f"targets must contain one through {MAX_ACTIVE_TARGETS} exact CAM operations."
        )
    prepared: list[Mapping[str, Any]] = []
    names: set[str] = set()
    for index, value in enumerate(targets):
        if not isinstance(value, Mapping) or set(value) != _TARGET_FIELDS:
            _error(
                f"targets[{index}] must contain object_name, expected_active, and active."
            )
        name = str(value.get("object_name") or "").strip()
        expected = value.get("expected_active")
        desired = value.get("active")
        if (
            not name
            or len(name) > 128
            or not (name[0].isalpha() or name[0] == "_")
            or any(not (character.isalnum() or character == "_") for character in name)
        ):
            _error(f"targets[{index}].object_name is not a stable object name.")
        if type(expected) is not bool or type(desired) is not bool:
            _error(
                f"targets[{index}] expected_active and active must be booleans."
            )
        if name in names:
            _error("Each CAM operation may appear only once in targets.")
        names.add(name)
        prepared.append(
            {
                "object_name": name,
                "expected_active": expected,
                "active": desired,
            }
        )
    return OperationActiveSpec(job=dict(job), targets=tuple(prepared))


def preflight_operation_active(
    document: Any,
    spec: OperationActiveSpec,
) -> PreparedOperationActive:
    if not isinstance(spec, OperationActiveSpec):
        raise TypeError("spec must be an OperationActiveSpec")
    job, before = resolve_job_target(document, spec.job)
    group = _operation_group(job)
    by_name = {str(operation.Name): operation for operation in group}
    group_bases = {str(operation.Name): _base_operation(operation) for operation in group}
    requested_names = {str(value["object_name"]) for value in spec.targets}
    prepared: list[_PreparedActiveTarget] = []
    desired_by_base: dict[int, bool] = {}
    for value in spec.targets:
        name = str(value["object_name"])
        operation = by_name.get(name)
        if operation is None:
            _error(
                f"CAM operation {name!r} is not in the exact Job operation group.",
                "NATIVE_MANUFACTURE_TARGET_STALE",
                repair={"available_operation_names": sorted(by_name)[:MAX_ACTIVE_TARGETS]},
            )
        base = _require_usable_operation(document, job, operation)
        selected_before = operation_reference_state(operation)
        base_before = operation_reference_state(base)
        expected = bool(value["expected_active"])
        desired = bool(value["active"])
        current = operation_active_state(operation)
        if current is not expected or bool(base.Active) is not expected:
            _error(
                f"CAM operation {name!r} active state changed after turn start.",
                "NATIVE_MANUFACTURE_STATE_STALE",
                repair={"object_name": name, "current_active": current},
            )
        if expected is desired:
            _error(
                f"CAM operation {name!r} is already in the requested state.",
                "NATIVE_MANUFACTURE_NO_CHANGE",
                repair={"object_name": name, "active": expected},
            )
        prior_desired = desired_by_base.get(id(base))
        if prior_desired is not None and prior_desired is not desired:
            _error(
                "Targets that resolve to the same underlying operation must request the same state."
            )
        desired_by_base[id(base)] = desired
        prepared.append(
            _PreparedActiveTarget(
                selected=operation,
                base=base,
                expected_active=expected,
                desired_active=desired,
                selected_before=selected_before,
                base_before=base_before,
            )
        )
    for target in prepared:
        aliases = {
            name for name, base in group_bases.items() if base is target.base
        }
        if not aliases.issubset(requested_names):
            _error(
                f"CAM operation {target.selected.Name!r} shares its Active state with other Job entries.",
                "NATIVE_MANUFACTURE_TARGET_AMBIGUOUS",
                repair={"required_operation_names": sorted(aliases)},
            )
    return PreparedOperationActive(
        job=job,
        job_before=before,
        targets=tuple(prepared),
        operation_group_before=group,
        operation_states_before=tuple(
            operation_reference_state(operation) for operation in group
        ),
        objects_before=tuple(document.Objects),
        visibility_before=tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in tuple(document.Objects)
            if getattr(obj, "ViewObject", None) is not None
        ),
        selection_before=read_current_selection(document),
        timeline_before=_timeline_state(document),
    )


def _assert_preflight_current(
    document: Any,
    prepared: PreparedOperationActive,
) -> None:
    if (
        tuple(document.Objects) != prepared.objects_before
        or _operation_group(prepared.job) != prepared.operation_group_before
        or _timeline_state(document) != prepared.timeline_before
        or read_current_selection(document) != prepared.selection_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
        or job_state(prepared.job).get("state_sha256")
        != prepared.job_before.get("state_sha256")
    ):
        _error(
            "The exact CAM Job, History, selection, or visibility changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    for target in prepared.targets:
        if (
            document.getObject(str(target.selected.Name)) is not target.selected
            or document.getObject(str(target.base.Name)) is not target.base
            or operation_reference_state(target.selected) != target.selected_before
            or operation_reference_state(target.base) != target.base_before
        ):
            _error(
                f"CAM operation {target.selected.Name!r} changed after preflight.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )


def set_operation_active(
    document: Any,
    *,
    prepared: PreparedOperationActive,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedOperationActive):
        raise TypeError("prepared must be a PreparedOperationActive")
    _assert_preflight_current(document, prepared)
    unique_bases: list[Any] = []
    seen: set[int] = set()
    for target in prepared.targets:
        if id(target.base) in seen:
            continue
        seen.add(id(target.base))
        try:
            target.base.Active = target.desired_active
        except Exception as exc:
            raise NativeManufactureError(
                f"CAM rejected the requested Active state for {target.selected.Name!r}.",
                error_code="NATIVE_MANUFACTURE_OPERATION_MODIFY_FAILED",
            ) from exc
        unique_bases.append(target.base)
    recompute_targets = tuple(
        dict.fromkeys(
            [
                *unique_bases,
                *(target.selected for target in prepared.targets),
                prepared.job,
            ]
        )
    )
    changed = tuple(object_identity(obj) for obj in recompute_targets)
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=recompute_targets,
        changed=changed,
    )


def _job_invariants(state: Mapping[str, Any]) -> dict[str, Any]:
    counts = dict(state.get("counts", {}))
    counts.pop("active_operations", None)
    return {
        "object_name": state.get("object_name"),
        "type_id": state.get("type_id"),
        "settings_sha256": state.get("settings_sha256"),
        "models": state.get("models"),
        "tools": state.get("tools"),
        "machine": state.get("machine"),
        "stock": state.get("stock"),
        "postprocessor": state.get("postprocessor"),
        "counts": counts,
    }


def verify_operation_active(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedOperationActive):
        raise TypeError("draft must contain exact prepared CAM Active state")
    if (
        tuple(document.Objects) != prepared.objects_before
        or _operation_group(prepared.job) != prepared.operation_group_before
        or _timeline_state(document) != prepared.timeline_before
        or read_current_selection(document) != prepared.selection_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        _error(
            "Changing CAM operation state altered the document graph, History, selection, or visibility.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    after_by_name = {
        str(operation.Name): operation_reference_state(operation)
        for operation in prepared.operation_group_before
    }
    before_by_name = {
        str(operation.Name): state
        for operation, state in zip(
            prepared.operation_group_before,
            prepared.operation_states_before,
            strict=True,
        )
    }
    target_names = {str(target.selected.Name) for target in prepared.targets}
    for name, before in before_by_name.items():
        if name not in target_names and after_by_name[name] != before:
            _error(
                f"Changing CAM operation state modified unrelated operation {name!r}.",
                "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            )
    results = []
    for target in prepared.targets:
        selected_after = after_by_name[str(target.selected.Name)]
        base_after = operation_reference_state(target.base)
        if (
            selected_after.get("active") is not target.desired_active
            or base_after.get("active") is not target.desired_active
            or selected_after.get("configuration_sha256")
            != target.selected_before.get("configuration_sha256")
            or base_after.get("configuration_sha256")
            != target.base_before.get("configuration_sha256")
            or not bool(target.selected.isValid())
            or not bool(target.base.isValid())
        ):
            _error(
                f"CAM operation {target.selected.Name!r} did not reach the exact requested state.",
                "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
                repair={
                    "object_name": str(target.selected.Name),
                    "requested_active": target.desired_active,
                    "current_active": selected_after.get("active"),
                },
            )
        result = {
            "object_name": str(target.selected.Name),
            "label": str(getattr(target.selected, "Label", "") or "")[:160],
            "previous_active": target.expected_active,
            "active": target.desired_active,
            "command_count": selected_after.get("command_count"),
        }
        if target.base is not target.selected:
            result["underlying_operation_name"] = str(target.base.Name)
        results.append(result)
    after = job_state(prepared.job)
    expected_active_count = sum(
        1 for operation in prepared.operation_group_before if operation_active_state(operation)
    )
    if (
        _job_invariants(after) != _job_invariants(prepared.job_before)
        or int(after["counts"]["active_operations"]) != expected_active_count
    ):
        _error(
            "Changing CAM operation state modified unrelated Job resources.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "set_active",
        "operations": results,
        "job": {
            "object_name": str(prepared.job.Name),
            "state_sha256": after["state_sha256"],
            "operation_count": int(after["counts"]["operations"]),
            "active_operation_count": int(after["counts"]["active_operations"]),
        },
    }
