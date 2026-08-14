# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact in-place start-point editing for one current CAM operation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import exact_fields, finite_number
from VibeCADNativeManufactureState import (
    job_state,
    operation_reference_state,
    operation_state,
    persistent_configuration_state,
    resolve_job_target,
    resolve_operation_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


_POINT_FIELDS = frozenset({"x_mm", "y_mm"})
_START_POINT_PROPERTIES = frozenset(
    {"StartPoint", "UseStartPoint", "ClearanceHeight"}
)
_MUTATED_PROPERTIES = ("StartPoint", "UseStartPoint")


@dataclass(frozen=True, slots=True)
class StartPointSpec:
    job: Mapping[str, Any]
    target: Mapping[str, Any]
    point_mm: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class StartPointTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class PreparedStartPoint:
    job: Any
    job_before: Mapping[str, Any]
    target: Any
    target_before: Mapping[str, Any]
    target_configuration_before: Mapping[str, Any]
    point_before: tuple[float, float, float]
    use_before: bool
    clearance_height_mm: float
    point_mm: tuple[float, float, float]
    operation_group_before: tuple[Any, ...]
    operation_states_before: tuple[Mapping[str, Any], ...]
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Mapping[str, Any]
    timeline_before: StartPointTimelineState


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _timeline_state(document: Any) -> StartPointTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != (
        "App::DocumentTimeline"
    ):
        _error(
            "Setting a CAM start point requires valid document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(timeline.Operations or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    position = int(timeline.Position)
    if (
        len(operations) != len(visibility)
        or len(operations) != len(suppression)
        or not 0 <= position <= len(operations)
    ):
        _error(
            "Document History has inconsistent CAM start-point state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return StartPointTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _operation_group(job: Any) -> tuple[Any, ...]:
    group = tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
    names = tuple(str(getattr(operation, "Name", "") or "") for operation in group)
    if any(not name for name in names) or len(names) != len(set(names)):
        _error(
            "The exact CAM Job has an invalid operation group.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    return group


def _point_tuple(value: Any) -> tuple[float, float, float]:
    return (
        finite_number(getattr(value, "x", None), "CAM start point x"),
        finite_number(getattr(value, "y", None), "CAM start point y"),
        finite_number(getattr(value, "z", None), "CAM start point z"),
    )


def _job_invariants(state: Mapping[str, Any]) -> dict[str, Any]:
    return {
        name: state.get(name)
        for name in (
            "object_name",
            "type_id",
            "settings_sha256",
            "models",
            "tools",
            "machine",
            "stock",
            "postprocessor",
            "counts",
        )
    }


def preflight_start_point(
    document: Any,
    spec: StartPointSpec,
) -> PreparedStartPoint:
    if not isinstance(spec, StartPointSpec):
        raise TypeError("spec must be a StartPointSpec")
    if _transaction_open(document):
        _error(
            "Finish or cancel the open task before setting a CAM start point.",
            "NATIVE_TRANSACTION_ACTIVE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        _error(
            "Wait for document recompute before setting a CAM start point.",
            "NATIVE_MANUFACTURE_RECOMPUTE_ACTIVE",
        )
    job, job_before = resolve_job_target(document, spec.job)
    target, target_before = resolve_operation_target(document, spec.target)
    operation_group = _operation_group(job)
    if target not in operation_group:
        _error(
            f"CAM operation {target.Name!r} is not in the exact Job operation group.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "available_operation_names": [
                    str(operation.Name) for operation in operation_group[:64]
                ]
            },
        )
    properties = set(
        str(value) for value in getattr(target, "PropertiesList", ()) or ()
    )
    if not _START_POINT_PROPERTIES <= properties:
        _error(
            f"CAM operation {target.Name!r} does not support start-point editing.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={"required_properties": sorted(_START_POINT_PROPERTIES)},
        )
    if (
        str(target.getTypeIdOfProperty("StartPoint"))
        != "App::PropertyVectorDistance"
        or str(target.getTypeIdOfProperty("UseStartPoint")) != "App::PropertyBool"
        or str(target.getTypeIdOfProperty("ClearanceHeight"))
        not in {"App::PropertyDistance", "App::PropertyLength"}
    ):
        _error(
            f"CAM operation {target.Name!r} has an invalid start-point property contract.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    point = exact_fields(spec.point_mm, _POINT_FIELDS, "CAM start point")
    x_mm = finite_number(point["x_mm"], "CAM start point x")
    y_mm = finite_number(point["y_mm"], "CAM start point y")
    clearance = finite_number(
        getattr(target.ClearanceHeight, "Value", None),
        "CAM clearance height",
    )
    desired = (x_mm, y_mm, clearance)
    before = _point_tuple(target.StartPoint)
    if bool(target.UseStartPoint) and before == desired:
        _error(
            "The CAM operation already uses that exact start point.",
            "NATIVE_MANUFACTURE_NO_CHANGE",
            repair={"object_name": str(target.Name), "start_point_mm": list(before)},
        )
    return PreparedStartPoint(
        job=job,
        job_before=job_before,
        target=target,
        target_before=target_before,
        target_configuration_before=persistent_configuration_state(
            target,
            excluded_names=_MUTATED_PROPERTIES,
        ),
        point_before=before,
        use_before=bool(target.UseStartPoint),
        clearance_height_mm=clearance,
        point_mm=desired,
        operation_group_before=operation_group,
        operation_states_before=tuple(
            operation_reference_state(operation) for operation in operation_group
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


def _preflight_is_current(document: Any, prepared: PreparedStartPoint) -> bool:
    try:
        return bool(
            tuple(document.Objects) == prepared.objects_before
            and document.getObject(str(prepared.target.Name)) is prepared.target
            and _operation_group(prepared.job) == prepared.operation_group_before
            and _timeline_state(document) == prepared.timeline_before
            and read_current_selection(document) == prepared.selection_before
            and all(
                bool(obj.ViewObject.Visibility) is visible
                for obj, visible in prepared.visibility_before
            )
            and job_state(prepared.job).get("state_sha256")
            == prepared.job_before.get("state_sha256")
            and operation_state(prepared.target) == prepared.target_before
        )
    except Exception:
        return False


def set_start_point(
    document: Any,
    *,
    prepared: PreparedStartPoint,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedStartPoint):
        raise TypeError("prepared must be a PreparedStartPoint")
    if not _preflight_is_current(document, prepared):
        _error(
            "The exact CAM Job, operation, History, selection, or visibility changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    import FreeCAD as App

    prepared.target.StartPoint = App.Vector(*prepared.point_mm)
    prepared.target.UseStartPoint = True
    return NativeMutationDraft(
        value={"prepared": prepared},
        recompute_targets=(prepared.target, prepared.job),
        changed=(object_identity(prepared.target), object_identity(prepared.job)),
    )


def verify_start_point(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("prepared") if isinstance(draft.value, dict) else None
    if not isinstance(prepared, PreparedStartPoint):
        raise TypeError("draft must contain one PreparedStartPoint")
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
            "Setting the CAM start point changed the document graph, History, selection, or visibility.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    after_states = tuple(
        operation_reference_state(operation)
        for operation in prepared.operation_group_before
    )
    for operation, before, after in zip(
        prepared.operation_group_before,
        prepared.operation_states_before,
        after_states,
        strict=True,
    ):
        if operation is not prepared.target and after != before:
            _error(
                f"Setting the CAM start point modified unrelated operation {operation.Name!r}.",
                "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            )
    target_after = operation_state(prepared.target)
    actual_point = _point_tuple(prepared.target.StartPoint)
    configuration_after = persistent_configuration_state(
        prepared.target,
        excluded_names=_MUTATED_PROPERTIES,
    )
    changed_configuration_names = sorted(
        name
        for name in set(prepared.target_configuration_before) | set(configuration_after)
        if prepared.target_configuration_before.get(name)
        != configuration_after.get(name)
    )
    failures = tuple(
        name
        for name, valid in (
            ("identity", document.getObject(str(prepared.target.Name)) is prepared.target),
            ("validity", bool(prepared.target.isValid())),
            ("enabled", bool(prepared.target.UseStartPoint)),
            ("point", actual_point == prepared.point_mm),
            (
                "clearance_height",
                finite_number(
                    getattr(prepared.target.ClearanceHeight, "Value", None),
                    "CAM clearance height",
                )
                == prepared.clearance_height_mm,
            ),
            (
                "configuration",
                not changed_configuration_names,
            ),
        )
        if not valid
    )
    if failures:
        _error(
            "The CAM start point failed exact checks: " + ", ".join(failures) + ".",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={
                "failed_invariants": list(failures),
                "changed_configuration_properties": changed_configuration_names,
            },
        )
    job_after = job_state(prepared.job)
    if _job_invariants(job_after) != _job_invariants(prepared.job_before):
        _error(
            "Setting the CAM start point modified unrelated Job resources.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "set_start_point",
        "target": {
            "object_name": str(prepared.target.Name),
            "label": str(prepared.target.Label)[:160],
            "state_sha256": target_after["state_sha256"],
        },
        "previous": {
            "enabled": prepared.use_before,
            "point_mm": list(prepared.point_before),
        },
        "start_point": {
            "enabled": True,
            "point_mm": list(actual_point),
            "clearance_height_mm": prepared.clearance_height_mm,
        },
        "path": {
            "command_count": target_after.get("command_count"),
            "path_sha256": target_after.get("path_sha256"),
        },
        "job": {
            "object_name": str(prepared.job.Name),
            "state_sha256": job_after["state_sha256"],
        },
    }
