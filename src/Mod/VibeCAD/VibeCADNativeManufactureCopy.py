# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact Native copy of complete CAM operation History closures."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import (
    copy_configuration_sha256,
    copy_configuration_state,
    job_state,
    operation_reference_state,
    persistent_resource_state,
    resolve_job_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


MAX_COPY_JOBS = 8
MAX_COPY_OPERATIONS = 64
MAX_COPY_CLOSURE_OBJECTS = 256
_JOB_COPY_FIELDS = frozenset({"job", "operation_names"})


@dataclass(frozen=True, slots=True)
class CopyJobSpec:
    target: Mapping[str, Any]
    operation_names: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class OperationCopySpec:
    jobs: tuple[CopyJobSpec, ...]


@dataclass(frozen=True, slots=True)
class _PreparedCopyJob:
    job: Any
    before: Mapping[str, Any]
    group_before: tuple[Any, ...]
    operation_states_before: tuple[Mapping[str, Any], ...]


@dataclass(frozen=True, slots=True)
class PreparedOperationCopy:
    spec: OperationCopySpec
    jobs: tuple[_PreparedCopyJob, ...]
    plan: Any
    source_states_before: tuple[Mapping[str, Any], ...]
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Any


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _copy_domain():
    import Path.OperationCopy as PathOperationCopy

    return PathOperationCopy


def _stable_object_name(value: Any, path: str) -> str:
    name = str(value or "").strip()
    if (
        not name
        or len(name) > 128
        or not (name[0].isalpha() or name[0] == "_")
        or any(not (character.isalnum() or character == "_") for character in name)
    ):
        _error(f"{path} must be one stable document object name.")
    return name


def prepare_operation_copy_spec(jobs: Any) -> OperationCopySpec:
    if not isinstance(jobs, list) or not 1 <= len(jobs) <= MAX_COPY_JOBS:
        _error(f"jobs must contain one through {MAX_COPY_JOBS} exact CAM Jobs.")
    prepared: list[CopyJobSpec] = []
    job_names: set[str] = set()
    operation_names: set[str] = set()
    operation_count = 0
    for job_index, value in enumerate(jobs):
        if not isinstance(value, Mapping) or set(value) != _JOB_COPY_FIELDS:
            _error(
                f"jobs[{job_index}] must contain job and operation_names only."
            )
        target = value.get("job")
        if not isinstance(target, Mapping) or set(target) != {
            "object_name",
            "expected_state_sha256",
        }:
            _error(
                f"jobs[{job_index}].job must contain object_name and expected_state_sha256."
            )
        job_name = _stable_object_name(
            target.get("object_name"), f"jobs[{job_index}].job.object_name"
        )
        expected_hash = str(target.get("expected_state_sha256") or "").strip()
        if (
            len(expected_hash) != 64
            or any(character not in "0123456789abcdef" for character in expected_hash)
        ):
            _error(
                f"jobs[{job_index}].job.expected_state_sha256 must be a lowercase SHA-256 hash."
            )
        if job_name in job_names:
            _error("Each exact CAM Job may appear only once in jobs.")
        job_names.add(job_name)

        names = value.get("operation_names")
        if (
            not isinstance(names, list)
            or not names
            or len(names) > MAX_COPY_OPERATIONS
        ):
            _error(
                f"jobs[{job_index}].operation_names must contain one through "
                f"{MAX_COPY_OPERATIONS} operation names."
            )
        exact_names = tuple(
            _stable_object_name(name, f"jobs[{job_index}].operation_names[{index}]")
            for index, name in enumerate(names)
        )
        if len(set(exact_names)) != len(exact_names):
            _error("A CAM operation may appear only once in operation_names.")
        if operation_names.intersection(exact_names):
            _error("A CAM operation may be copied only once per call.")
        operation_names.update(exact_names)
        operation_count += len(exact_names)
        prepared.append(
            CopyJobSpec(
                target={
                    "object_name": job_name,
                    "expected_state_sha256": expected_hash,
                },
                operation_names=exact_names,
            )
        )
    if operation_count > MAX_COPY_OPERATIONS:
        _error(
            f"One copy call may target at most {MAX_COPY_OPERATIONS} operations across all Jobs."
        )
    return OperationCopySpec(jobs=tuple(prepared))


def _operation_group(job: Any) -> tuple[Any, ...]:
    group = tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
    names = tuple(str(getattr(value, "Name", "") or "") for value in group)
    if any(not name for name in names) or len(set(names)) != len(names):
        _error(
            f"CAM Job {job.Name!r} has no exact operation-group identity.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    return group


def _source_states(plan: Any) -> tuple[Mapping[str, Any], ...]:
    try:
        return tuple(persistent_resource_state(value) for value in plan.source_closure)
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The complete CAM copy source graph could not be fingerprinted.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        ) from exc


def preflight_operation_copy(
    document: Any,
    spec: OperationCopySpec,
) -> PreparedOperationCopy:
    if not isinstance(spec, OperationCopySpec):
        raise TypeError("spec must be an OperationCopySpec")
    prepared_jobs: list[_PreparedCopyJob] = []
    selection: list[tuple[Any, Any]] = []
    for value in spec.jobs:
        job, before = resolve_job_target(document, value.target)
        group = _operation_group(job)
        by_name = {str(operation.Name): operation for operation in group}
        missing = [name for name in value.operation_names if name not in by_name]
        if missing:
            _error(
                f"CAM operation {missing[0]!r} is not in exact Job {job.Name!r}.",
                "NATIVE_MANUFACTURE_TARGET_STALE",
                repair={
                    "job_object_name": str(job.Name),
                    "available_operation_names": sorted(by_name)[:MAX_COPY_OPERATIONS],
                },
            )
        selection.extend((by_name[name], job) for name in value.operation_names)
        prepared_jobs.append(
            _PreparedCopyJob(
                job=job,
                before=before,
                group_before=group,
                operation_states_before=tuple(
                    persistent_resource_state(operation) for operation in group
                ),
            )
        )
    try:
        plan = _copy_domain().planOperations(document, selection)
    except Exception as exc:
        raise NativeManufactureError(
            str(exc) or "The selected CAM operations cannot be copied.",
            error_code="NATIVE_MANUFACTURE_OPERATION_COPY_INVALID",
        ) from exc
    if len(plan.source_closure) > MAX_COPY_CLOSURE_OBJECTS:
        _error(
            f"The complete CAM copy closure contains more than "
            f"{MAX_COPY_CLOSURE_OBJECTS} objects.",
            "NATIVE_MANUFACTURE_OPERATION_COPY_TOO_LARGE",
            repair={"closure_object_count": len(plan.source_closure)},
        )
    return PreparedOperationCopy(
        spec=spec,
        jobs=tuple(prepared_jobs),
        plan=plan,
        source_states_before=_source_states(plan),
        objects_before=tuple(document.Objects),
        visibility_before=tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in tuple(document.Objects)
            if getattr(obj, "ViewObject", None) is not None
        ),
        selection_before=read_current_selection(document),
    )


def _assert_preflight_current(document: Any, prepared: PreparedOperationCopy) -> None:
    if (
        tuple(document.Objects) != prepared.objects_before
        or read_current_selection(document) != prepared.selection_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        _error(
            "The CAM document graph, human selection, or visibility changed after copy preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    for value in prepared.jobs:
        if (
            document.getObject(str(value.job.Name)) is not value.job
            or _operation_group(value.job) != value.group_before
            or job_state(value.job).get("state_sha256")
            != value.before.get("state_sha256")
        ):
            _error(
                f"CAM Job {value.job.Name!r} changed after copy preflight.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
    try:
        _copy_domain().assertPlanCurrent(prepared.plan)
    except Exception as exc:
        raise NativeManufactureError(
            str(exc) or "The CAM copy source graph changed after preflight.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        ) from exc
    if _source_states(prepared.plan) != prepared.source_states_before:
        _error(
            "The complete CAM copy source graph changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def copy_operations(
    document: Any,
    *,
    prepared: PreparedOperationCopy,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedOperationCopy):
        raise TypeError("prepared must be a PreparedOperationCopy")
    _assert_preflight_current(document, prepared)
    try:
        result = _copy_domain().copyOperations(document, prepared.plan)
    except Exception as exc:
        raise NativeManufactureError(
            str(exc) or "The complete CAM operation graph could not be copied.",
            error_code="NATIVE_MANUFACTURE_OPERATION_COPY_FAILED",
        ) from exc
    recompute_targets = tuple(
        dict.fromkeys(
            [
                *result.created,
                *(value.job.Operations for value in prepared.jobs),
                *(
                    operation
                    for value in prepared.jobs
                    for operation in tuple(value.job.Operations.Group or ())
                ),
                *(value.job for value in prepared.jobs),
            ]
        )
    )
    return NativeMutationDraft(
        value={"prepared": prepared, "result": result},
        recompute_targets=recompute_targets,
        created=tuple(object_identity(value.copied) for value in result.outputs),
        changed=tuple(object_identity(value.job) for value in prepared.jobs),
    )


def _timeline_after(document: Any) -> tuple[Any, tuple[Any, ...], tuple[bool, ...], tuple[bool, ...], int]:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None:
        _error(
            "The CAM operation copy lost document History.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    operations = tuple(timeline.Operations or ())
    visibility = tuple(bool(value) for value in timeline.VisibilityAtEnd)
    suppression = tuple(bool(value) for value in timeline.SuppressionAtEnd)
    try:
        position = int(timeline.Position)
    except (AttributeError, TypeError, ValueError) as exc:
        raise NativeManufactureError(
            "The CAM operation copy produced an unreadable History marker.",
            error_code="NATIVE_MANUFACTURE_HISTORY_INVALID",
        ) from exc
    if len(operations) != len(visibility) or len(operations) != len(suppression):
        _error(
            "The CAM operation copy produced malformed History state arrays.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return timeline, operations, visibility, suppression, position


def _verify_timeline(
    document: Any,
    prepared: PreparedOperationCopy,
    result: Any,
) -> None:
    plan = prepared.plan
    timeline, operations, visibility, suppression, position = _timeline_after(document)
    expected_operations = (
        *plan.timeline_operations[: plan.timeline_position],
        *result.adoption_order,
        *plan.timeline_operations[plan.timeline_position :],
    )
    if (
        timeline is not plan.timeline
        or operations != expected_operations
        or position != plan.timeline_position + len(result.adoption_order)
    ):
        _error(
            "The copied CAM graph was not inserted as one exact block at the History marker.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    insertion_count = len(result.adoption_order)
    for old_index in range(len(plan.timeline_operations)):
        new_index = old_index if old_index < plan.timeline_position else old_index + insertion_count
        if (
            visibility[new_index] is not plan.timeline_visibility[old_index]
            or suppression[new_index] is not plan.timeline_suppression[old_index]
        ):
            _error(
                "Operation copy changed existing History visibility or suppression.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
            )
    inserted_suppression = suppression[
        plan.timeline_position : plan.timeline_position + insertion_count
    ]
    if any(inserted_suppression):
        _error(
            "A copied CAM History object was unexpectedly suppressed.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )


def _job_invariants(state: Mapping[str, Any]) -> dict[str, Any]:
    counts = dict(state.get("counts", {}))
    counts.pop("operations", None)
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


def _verify_copy_graph(
    document: Any,
    result: Any,
) -> None:
    import Path.Base.Util as PathUtil
    import PathScripts.PathUtils as PathUtils

    outputs = result.copied_outputs
    if len(outputs) == 1:
        owner = outputs[0]
        if result.timeline_operation is not owner:
            _error(
                "A single CAM copy did not become its own History operation.",
                "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
            )
        resources = tuple(value for value in result.copied_source_order if value is not owner)
    else:
        owner = result.timeline_operation
        resources = result.copied_source_order
        if (
            "CAMOutputs" not in tuple(getattr(owner, "PropertiesList", ()) or ())
            or tuple(owner.CAMOutputs or ()) != outputs
            or str(getattr(owner, "CAMOperationKind", "") or "")
            != "Copy CAM operations"
            or bool(getattr(owner.ViewObject, "ShowInTree", True))
        ):
            _error(
                "The multi-operation copy lost its exact History controller.",
                "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
            )
    if str(getattr(owner, "VibeCADTimelineRole", "") or "") != "operation":
        _error(
            "The copied CAM graph has no exact History operation owner.",
            "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
        )
    for resource in resources:
        if (
            str(getattr(resource, "VibeCADTimelineRole", "") or "") != "resource"
            or getattr(resource, "VibeCADTimelineOwner", None) is not owner
        ):
            _error(
                f"Copied CAM resource {resource.Name!r} lost its exact History owner.",
                "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
            )
    for value in result.outputs:
        if (
            PathUtils.findParentJob(value.copied) is not value.job
            or PathUtil.timelineParentJob(value.copied) is not value.job
            or value.copied not in tuple(value.job.Operations.Group or ())
        ):
            _error(
                f"Copied operation {value.copied.Name!r} lost exact Job ownership.",
                "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
            )
    for copied in result.copied_source_order:
        if "VibeCADTimelineReplacedInputs" in tuple(
            getattr(copied, "PropertiesList", ()) or ()
        ):
            _error(
                f"Copied CAM object {copied.Name!r} retained replacement semantics.",
                "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
            )


def _toolpaths_copy_equivalent(
    source: Any,
    copied: Any,
    result: Any,
) -> bool:
    """Compare exact commands, allowing only graph-authored label comments to differ."""

    try:
        source_commands = tuple(source.Path.Commands)
        copied_commands = tuple(copied.Path.Commands)
        if len(source_commands) != len(copied_commands):
            return False
        label_comment_pairs = {
            (
                f"({str(getattr(source_value, 'Label', '') or '')})",
                f"({str(getattr(copied_value, 'Label', '') or '')})",
            )
            for source_value, copied_value in result.source_copy_pairs
        }
        for source_command, copied_command in zip(
            source_commands, copied_commands, strict=True
        ):
            source_gcode = str(source_command.toGCode())
            copied_gcode = str(copied_command.toGCode())
            if source_gcode == copied_gcode:
                continue
            if (source_gcode, copied_gcode) not in label_comment_pairs:
                return False
        return True
    except Exception as exc:
        raise NativeManufactureError(
            "A copied CAM toolpath could not be compared with its source.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        ) from exc


def verify_operation_copy(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value if isinstance(draft.value, dict) else {}
    prepared = value.get("prepared")
    result = value.get("result")
    copy_domain = _copy_domain()
    if not isinstance(prepared, PreparedOperationCopy) or not isinstance(
        result, copy_domain.OperationCopyResult
    ):
        raise TypeError("draft must contain one exact prepared CAM copy result")
    created = tuple(obj for obj in document.Objects if obj not in prepared.objects_before)
    if created != result.created or tuple(document.Objects) != (*prepared.objects_before, *created):
        _error(
            "Operation copy created objects outside its exact semantic closure.",
            "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
        )
    if (
        read_current_selection(document) != prepared.selection_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        _error(
            "Operation copy changed the human selection or existing visibility.",
            "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
        )
    if _source_states(prepared.plan) != prepared.source_states_before:
        _error(
            "Operation copy changed its source CAM graph.",
            "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
        )
    if any(
        document.getObject(str(obj.Name)) is not obj or not bool(obj.isValid())
        for obj in result.created
    ):
        _error(
            "The copied CAM graph contains an invalid object.",
            "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
        )
    _verify_timeline(document, prepared, result)
    _verify_copy_graph(document, result)

    source_state_by_id = {
        id(source): state
        for source, state in zip(
            prepared.plan.source_closure,
            prepared.source_states_before,
            strict=True,
        )
    }
    source_canonical_names = {
        str(source.Name): f"copy_graph_{index}"
        for index, (source, _copied) in enumerate(result.source_copy_pairs)
    }
    copied_canonical_names = {
        str(copied.Name): f"copy_graph_{index}"
        for index, (_source, copied) in enumerate(result.source_copy_pairs)
    }
    for source, copied in result.source_copy_pairs:
        source_state = source_state_by_id[id(source)]
        copied_state = persistent_resource_state(copied)
        compared_fields = (
            "type_id",
            "command_count",
            "shape_sha256",
            "placement",
        )
        mismatched = [
            field
            for field in compared_fields
            if copied_state.get(field) != source_state.get(field)
        ]
        source_path_hash = source_state.get("path_sha256")
        copied_path_hash = copied_state.get("path_sha256")
        if (
            (source_path_hash is None) is not (copied_path_hash is None)
            or (
                source_path_hash is not None
                and source_path_hash != copied_path_hash
                and not _toolpaths_copy_equivalent(source, copied, result)
            )
        ):
            mismatched.append("toolpath_commands")
        source_configuration = copy_configuration_sha256(
            source,
            source_canonical_names,
        )
        copied_configuration = copy_configuration_sha256(
            copied,
            copied_canonical_names,
        )
        if copied_configuration != source_configuration:
            mismatched.append("authored_configuration")
        if mismatched:
            source_configuration_state = copy_configuration_state(
                source,
                source_canonical_names,
            )
            copied_configuration_state = copy_configuration_state(
                copied,
                copied_canonical_names,
            )
            _error(
                f"Copied CAM object {copied.Name!r} does not reproduce source {source.Name!r}.",
                "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
                repair={
                    "source_object_name": str(source.Name),
                    "copied_object_name": str(copied.Name),
                    "mismatched_fields": mismatched,
                    "source_path_sha256": source_path_hash,
                    "copied_path_sha256": copied_path_hash,
                    "source_configuration_sha256": source_configuration,
                    "copied_configuration_sha256": copied_configuration,
                    "mismatched_configuration_properties": sorted(
                        name
                        for name in set(source_configuration_state).union(
                            copied_configuration_state
                        )
                        if source_configuration_state.get(name)
                        != copied_configuration_state.get(name)
                    )[:32],
                },
            )

    job_results = []
    for prepared_job in prepared.jobs:
        additions = tuple(
            value.copied for value in result.outputs if value.job is prepared_job.job
        )
        if tuple(prepared_job.job.Operations.Group or ()) != (
            *prepared_job.group_before,
            *additions,
        ):
            _error(
                f"Operation copy changed the ordering of CAM Job {prepared_job.job.Name!r}.",
                "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
            )
        current_existing_states = tuple(
            persistent_resource_state(operation)
            for operation in prepared_job.group_before
        )
        if current_existing_states != prepared_job.operation_states_before:
            mismatch_index = next(
                index
                for index, (before, current) in enumerate(
                    zip(
                        prepared_job.operation_states_before,
                        current_existing_states,
                        strict=True,
                    )
                )
                if before != current
            )
            before = prepared_job.operation_states_before[mismatch_index]
            current = current_existing_states[mismatch_index]
            mismatched_fields = sorted(
                key
                for key in set(before).union(current)
                if before.get(key) != current.get(key)
            )
            _error(
                f"Operation copy changed an existing operation in Job {prepared_job.job.Name!r}.",
                "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
                repair={
                    "object_name": str(
                        prepared_job.group_before[mismatch_index].Name
                    ),
                    "mismatched_fields": mismatched_fields,
                },
            )
        after = job_state(prepared_job.job)
        if (
            _job_invariants(after) != _job_invariants(prepared_job.before)
            or int(after["counts"]["operations"])
            != int(prepared_job.before["counts"]["operations"]) + len(additions)
        ):
            _error(
                f"Operation copy changed unrelated resources in Job {prepared_job.job.Name!r}.",
                "NATIVE_MANUFACTURE_OPERATION_COPY_POSTCONDITION_FAILED",
            )
        job_results.append(
            {
                "object_name": str(prepared_job.job.Name),
                "state_sha256": after["state_sha256"],
                "operation_count": int(after["counts"]["operations"]),
                "copied_operation_count": len(additions),
            }
        )

    copies = []
    for output in result.outputs:
        state = operation_reference_state(output.copied)
        copies.append(
            {
                "source_object_name": str(output.source.Name),
                "object_name": str(output.copied.Name),
                "label": str(getattr(output.copied, "Label", "") or "")[:160],
                "job_object_name": str(output.job.Name),
                "active": state.get("active"),
                "command_count": state.get("command_count"),
            }
        )
    return {
        "operation": "copy_operations",
        "copies": copies,
        "history": {
            "object_name": str(result.timeline_operation.Name),
            "grouped": len(result.outputs) > 1,
            "copied_operation_count": len(result.outputs),
            "closure_object_count": len(result.copied_source_order),
        },
        "jobs": job_results,
    }
