# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact-target lifecycle for one-output CAM replacement dress-ups."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import clean_operation_label, exact_fields
from VibeCADNativeManufactureState import (
    copy_configuration_state,
    job_state,
    operation_state,
    persistent_resource_state,
    resolve_job_target,
    resolve_operation_target,
    tool_controller_state,
)
from VibeCADNativeTargets import read_current_selection


MAX_DRESSUP_COMMANDS = 500_000
_TARGET_FIELDS = frozenset({"object_name", "expected_state_sha256"})
_CUTTING_COMMANDS = frozenset(
    {"G1", "G2", "G3", "G73", "G74", "G81", "G82", "G83", "G84", "G85"}
)


@dataclass(frozen=True, slots=True)
class DressupTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class PreparedDressupBase:
    noun: str
    label: str
    job: Any
    job_before: Mapping[str, Any]
    base: Any
    base_reference_before: Mapping[str, Any]
    base_state_before: Mapping[str, Any]
    base_configuration_before: Mapping[str, Any]
    base_was_visible: bool
    controller: Any
    controller_before: Mapping[str, Any]
    coolant: str
    job_operations_before: tuple[Any, ...]
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Any
    timeline_before: DressupTimelineState


def dressup_error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def normalize_exact_target(value: Any, noun: str) -> Mapping[str, Any]:
    target = exact_fields(value, _TARGET_FIELDS, noun)
    name = str(target["object_name"] or "").strip()
    digest = str(target["expected_state_sha256"] or "").strip()
    if (
        not name
        or len(name) > 128
        or not (name[0].isalpha() or name[0] == "_")
        or any(not (character.isalnum() or character == "_") for character in name)
    ):
        dressup_error(f"{noun} object_name must be one stable document object name.")
    if len(digest) != 64 or any(
        character not in "0123456789abcdef" for character in digest
    ):
        dressup_error(
            f"{noun} expected_state_sha256 must be one lowercase SHA-256 hash."
        )
    return {"object_name": name, "expected_state_sha256": digest}


def read_dressup_timeline(document: Any, noun: str) -> DressupTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != (
        "App::DocumentTimeline"
    ):
        dressup_error(
            f"{noun} requires a valid document History.",
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
        dressup_error(
            f"{noun} found malformed document History state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return DressupTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def command_path_sha256(commands: tuple[Any, ...], noun: str) -> str:
    digest = hashlib.sha256()
    for command in commands:
        try:
            encoded = str(command.toGCode()).encode("utf-8")
        except Exception as exc:
            raise NativeManufactureError(
                f"{noun} contains an unreadable toolpath command.",
                error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
            ) from exc
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


def cutting_command_count(commands: tuple[Any, ...]) -> int:
    return sum(
        1
        for command in commands
        if str(getattr(command, "Name", "")) in _CUTTING_COMMANDS
    )


def job_invariants(state: Mapping[str, Any]) -> dict[str, Any]:
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


def preflight_dressup_base(
    document: Any,
    *,
    label: Any,
    job_target: Any,
    base_target: Any,
    noun: str,
) -> PreparedDressupBase:
    """Freeze one exact active Job operation before dress-up-specific work."""

    clean_label = clean_operation_label(label, noun)
    job, job_before = resolve_job_target(
        document,
        normalize_exact_target(job_target, f"{noun} job"),
    )
    target = normalize_exact_target(base_target, f"{noun} base_operation")
    base, reference = resolve_operation_target(document, target)

    try:
        import Path.Base.Util as PathUtil
        import Path.Dressup.Utils as PathDressup
        import PathScripts.PathUtils as PathUtils
        from Path.CommandBoundary import is_timeline_input_usable

        group = tuple(job.Operations.Group or ())
        commands = tuple(getattr(getattr(base, "Path", None), "Commands", ()) or ())
        valid = (
            base in group
            and PathDressup.isOp(base)
            and base.isValid()
            and is_timeline_input_usable(base, document)
            and PathUtil.activeForOp(base)
            and PathUtils.findParentJob(base) is job
            and bool(commands)
        )
        controller = PathUtil.toolControllerForOp(base)
        coolant = str(PathUtil.coolantModeForOp(base))
    except Exception as exc:
        raise NativeManufactureError(
            f"The exact {noun} base could not be validated.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if not valid:
        dressup_error(
            f"{noun} base_operation must be one active, valid, current operation-group "
            "entry in the exact Job with a nonempty path.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "available_operation_names": [
                    str(operation.Name) for operation in group
                ]
            },
        )
    if len(commands) > MAX_DRESSUP_COMMANDS:
        dressup_error(
            f"{noun} base has {len(commands)} commands; the safety limit is "
            f"{MAX_DRESSUP_COMMANDS}.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    if controller is None or getattr(controller, "Document", None) is not document:
        dressup_error(
            f"{noun} base_operation requires one current tool controller.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )

    return PreparedDressupBase(
        noun=noun,
        label=clean_label,
        job=job,
        job_before=job_before,
        base=base,
        base_reference_before=reference,
        base_state_before=persistent_resource_state(base),
        base_configuration_before=copy_configuration_state(base, {}),
        base_was_visible=bool(base.ViewObject.Visibility),
        controller=controller,
        controller_before=tool_controller_state(controller),
        coolant=coolant,
        job_operations_before=group,
        objects_before=tuple(document.Objects),
        visibility_before=tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in tuple(document.Objects)
            if getattr(obj, "ViewObject", None) is not None
        ),
        selection_before=read_current_selection(document),
        timeline_before=read_dressup_timeline(document, noun),
    )


def assert_dressup_preflight_current(
    document: Any,
    prepared: PreparedDressupBase,
) -> None:
    if (
        tuple(document.Objects) != prepared.objects_before
        or read_current_selection(document) != prepared.selection_before
        or read_dressup_timeline(document, prepared.noun) != prepared.timeline_before
        or tuple(prepared.job.Operations.Group or ()) != prepared.job_operations_before
        or job_state(prepared.job).get("state_sha256")
        != prepared.job_before.get("state_sha256")
        or operation_state(prepared.base) != prepared.base_reference_before
        or persistent_resource_state(prepared.base) != prepared.base_state_before
        or tool_controller_state(prepared.controller).get("state_sha256")
        != prepared.controller_before.get("state_sha256")
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        dressup_error(
            f"The {prepared.noun} Job, base, History, selection, or visibility changed "
            "after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def publish_dressup_replacement(
    document: Any,
    prepared: PreparedDressupBase,
    operation: Any,
    resources: tuple[Any, ...] = (),
) -> None:
    if not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(operation):
        raise RuntimeError(f"The {prepared.noun} was not enrolled in History")
    if any(
        not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(resource)
        for resource in resources
    ):
        raise RuntimeError(
            f"A {prepared.noun} resource was not enrolled in History"
        )
    document.publishProvisionalTimelineOperationBlock(operation, resources, ())
    prepared.base.ViewObject.Visibility = False


def _verify_timeline(
    document: Any,
    prepared: PreparedDressupBase,
    operation: Any,
    resources: tuple[Any, ...],
) -> None:
    after = read_dressup_timeline(document, prepared.noun)
    before = prepared.timeline_before
    inserted = (*resources, operation)
    expected_operations = (
        *before.operations[: before.position],
        *inserted,
        *before.operations[before.position :],
    )
    if (
        after.timeline is not before.timeline
        or after.operations != expected_operations
        or after.position != before.position + len(inserted)
    ):
        dressup_error(
            f"The {prepared.noun} was not inserted at the exact History marker.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    for old_index, old_operation in enumerate(before.operations):
        new_index = (
            old_index
            if old_index < before.position
            else old_index + len(inserted)
        )
        expected_visibility = (
            False if old_operation is prepared.base else before.visibility[old_index]
        )
        if (
            after.visibility[new_index] is not expected_visibility
            or after.suppression[new_index] is not before.suppression[old_index]
        ):
            dressup_error(
                f"{prepared.noun} changed unrelated History visibility or suppression.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
                repair={
                    "object_name": str(old_operation.Name),
                    "old_index": old_index,
                    "new_index": new_index,
                    "expected_visibility": expected_visibility,
                    "actual_visibility": after.visibility[new_index],
                    "expected_suppression": before.suppression[old_index],
                    "actual_suppression": after.suppression[new_index],
                    "is_replaced_base": old_operation is prepared.base,
                },
            )
    for offset, resource in enumerate(resources):
        index = before.position + offset
        visible = bool(
            getattr(getattr(resource, "ViewObject", None), "Visibility", False)
        )
        if after.visibility[index] is not visible or after.suppression[index]:
            dressup_error(
                f"A {prepared.noun} resource has incorrect History state.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
            )
    operation_index = before.position + len(resources)
    if not after.visibility[operation_index] or after.suppression[operation_index]:
        dressup_error(
            f"The created {prepared.noun} is not the active visible History result.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )


def _label_matches(actual: str, requested: str) -> bool:
    if actual == requested:
        return True
    suffix = actual[len(requested) :] if actual.startswith(requested) else ""
    return len(suffix) >= 3 and suffix.isdigit()


def verify_dressup_envelope(
    document: Any,
    *,
    prepared: PreparedDressupBase,
    operation: Any,
    proxy_type: type,
    view_proxy_type: type,
    expected_command_count: int,
    expected_cutting_count: int,
    expected_path_sha256: str,
    owned_resources: tuple[Any, ...] = (),
    created_objects: tuple[Any, ...] | None = None,
) -> tuple[str, Mapping[str, Any], tuple[Any, ...], Mapping[str, Any]]:
    """Prove the common graph, resource, path, History, and UI contract."""

    expected_created = (
        created_objects
        if created_objects is not None
        else (operation, *owned_resources)
    )
    if (
        len({id(value) for value in expected_created}) != len(expected_created)
        or operation not in expected_created
        or tuple(document.Objects) != (*prepared.objects_before, *expected_created)
    ):
        dressup_error(
            f"{prepared.noun} creation changed objects outside its exact output.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    base_index = prepared.job_operations_before.index(prepared.base)
    expected_group = (
        *prepared.job_operations_before[:base_index],
        operation,
        *prepared.job_operations_before[base_index + 1 :],
    )
    if tuple(prepared.job.Operations.Group or ()) != expected_group:
        actual_group = tuple(prepared.job.Operations.Group or ())
        dressup_error(
            f"The {prepared.noun} did not replace its exact Job operation entry.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={
                "expected_operation_names": [
                    str(value.Name) for value in expected_group
                ],
                "actual_operation_names": [
                    str(value.Name) for value in actual_group
                ],
                "replaced_base_name": str(prepared.base.Name),
                "created_operation_name": str(operation.Name),
            },
        )

    import Path.Base.Util as PathUtil
    import PathScripts.PathUtils as PathUtils

    for resource in owned_resources:
        if (
            document.getObject(str(getattr(resource, "Name", ""))) is not resource
            or resource is operation
            or str(getattr(resource, "VibeCADTimelineRole", "") or "")
            != "resource"
            or getattr(resource, "VibeCADTimelineOwner", None) is not operation
        ):
            dressup_error(
                f"A created {prepared.noun} resource lost its exact owner or Job.",
                "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            )

    actual_label = str(operation.Label)
    expected_replacements = (prepared.base,) if prepared.base_was_visible else ()
    if (
        document.getObject(str(operation.Name)) is not operation
        or not operation.isDerivedFrom("Path::Feature")
        or not operation.isValid()
        or not isinstance(getattr(operation, "Proxy", None), proxy_type)
        or not isinstance(
            getattr(getattr(operation, "ViewObject", None), "Proxy", None),
            view_proxy_type,
        )
        or operation.Base is not prepared.base
        or PathUtils.findParentJob(operation) is not prepared.job
        or PathUtil.timelineParentJob(operation) is not prepared.job
        or str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation"
        or tuple(getattr(operation, "VibeCADTimelineReplacedInputs", ()) or ())
        != expected_replacements
        or PathUtil.toolControllerForOp(operation) is not prepared.controller
        or str(PathUtil.coolantModeForOp(operation)) != prepared.coolant
        or "ToolController" in tuple(operation.PropertiesList)
        or "CoolantMode" in tuple(operation.PropertiesList)
        or not _label_matches(actual_label, prepared.label)
    ):
        dressup_error(
            f"The created {prepared.noun} lost its exact base, Job, resources, or "
            "replacement identity.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    state = operation_state(operation)
    commands = tuple(operation.Path.Commands or ())
    actual_cutting = cutting_command_count(commands)
    if (
        len(commands) != expected_command_count
        or actual_cutting != expected_cutting_count
        or state.get("path_sha256") != expected_path_sha256
    ):
        dressup_error(
            f"The {prepared.noun} did not generate its exact prepared path.",
            "NATIVE_MANUFACTURE_OPERATION_GENERATION_FAILED",
            repair={
                "expected_command_count": expected_command_count,
                "actual_command_count": len(commands),
                "expected_cutting_command_count": expected_cutting_count,
                "actual_cutting_command_count": actual_cutting,
                "expected_path_sha256": expected_path_sha256,
                "actual_path_sha256": state.get("path_sha256"),
            },
        )

    base_after = persistent_resource_state(prepared.base)
    base_configuration_after = copy_configuration_state(prepared.base, {})
    if (
        base_configuration_after != prepared.base_configuration_before
        or base_after.get("path_sha256")
        != prepared.base_state_before.get("path_sha256")
        or base_after.get("command_count")
        != prepared.base_state_before.get("command_count")
        or base_after.get("active") is not prepared.base_state_before.get("active")
    ):
        dressup_error(
            f"{prepared.noun} creation changed its retained base operation.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if tool_controller_state(prepared.controller).get("state_sha256") != (
        prepared.controller_before.get("state_sha256")
    ):
        dressup_error(
            f"{prepared.noun} creation changed its inherited tool controller.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if (
        read_current_selection(document) != prepared.selection_before
        or bool(prepared.base.ViewObject.Visibility)
        or not bool(operation.ViewObject.Visibility)
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
            if obj is not prepared.base
        )
    ):
        dressup_error(
            f"{prepared.noun} changed selection or unrelated existing visibility.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    _verify_timeline(document, prepared, operation, owned_resources)
    after_job = job_state(prepared.job)
    if (
        job_invariants(after_job) != job_invariants(prepared.job_before)
        or int(after_job["counts"]["operations"])
        != int(prepared.job_before["counts"]["operations"])
    ):
        dressup_error(
            f"{prepared.noun} changed unrelated Job resources.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return actual_label, state, commands, after_job
