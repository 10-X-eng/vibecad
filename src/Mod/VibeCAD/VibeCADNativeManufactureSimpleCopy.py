# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free flattening of current CAM toolpaths into one Custom operation."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import clean_operation_label, exact_fields
from VibeCADNativeManufactureState import (
    job_state,
    operation_state,
    persistent_resource_state,
    resolve_job_target,
    resolve_operation_target,
    tool_controller_state,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


MAX_SIMPLE_COPY_SOURCES = 64
MAX_SIMPLE_COPY_COMMANDS = 500_000
MAX_SIMPLE_COPY_GCODE_BYTES = 16 * 1024 * 1024
MAX_SIMPLE_COPY_LINE_BYTES = 4096
_TARGET_FIELDS = frozenset({"object_name", "expected_state_sha256"})


@dataclass(frozen=True, slots=True)
class SimpleCopyCreateSpec:
    label: Any
    job: Mapping[str, Any]
    source_operations: Any


@dataclass(frozen=True, slots=True)
class SimpleCopyTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class PreparedSimpleCopyCreate:
    label: str
    job: Any
    job_before: Mapping[str, Any]
    sources: tuple[Any, ...]
    source_reference_before: tuple[Mapping[str, Any], ...]
    source_state_before: tuple[Mapping[str, Any], ...]
    placed_gcode: tuple[str, ...]
    placed_gcode_sha256: str
    cutting_command_count: int
    controller: Any
    controller_before: Mapping[str, Any]
    coolant: str
    job_operations_before: tuple[Any, ...]
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Any
    timeline_before: SimpleCopyTimelineState


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _target(value: Any, noun: str) -> Mapping[str, Any]:
    target = exact_fields(value, _TARGET_FIELDS, noun)
    name = str(target["object_name"] or "").strip()
    digest = str(target["expected_state_sha256"] or "").strip()
    if (
        not name
        or len(name) > 128
        or not (name[0].isalpha() or name[0] == "_")
        or any(not (character.isalnum() or character == "_") for character in name)
    ):
        _error(f"{noun} object_name must be one stable document object name.")
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        _error(f"{noun} expected_state_sha256 must be one lowercase SHA-256 hash.")
    return {"object_name": name, "expected_state_sha256": digest}


def _timeline_state(document: Any) -> SimpleCopyTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != "App::DocumentTimeline":
        _error(
            "CAM Simple Copy requires a valid document History.",
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
            "CAM Simple Copy found malformed document History state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return SimpleCopyTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
    )


def _resolve_source(
    document: Any,
    value: Any,
    index: int,
) -> tuple[Any, Mapping[str, Any]]:
    target = _target(value, f"source_operations[{index}]")
    return resolve_operation_target(document, target)


def _gcode_sha256(lines: tuple[str, ...]) -> str:
    digest = hashlib.sha256()
    for line in lines:
        encoded = line.encode("utf-8")
        digest.update(len(encoded).to_bytes(8, "big"))
        digest.update(encoded)
    return digest.hexdigest()


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


def preflight_simple_copy_create(
    document: Any,
    spec: SimpleCopyCreateSpec,
) -> PreparedSimpleCopyCreate:
    """Freeze exact placed command streams and their shared Job resources."""

    if not isinstance(spec, SimpleCopyCreateSpec):
        raise TypeError("spec must be a SimpleCopyCreateSpec")
    job, job_before = resolve_job_target(
        document,
        _target(spec.job, "CAM Simple Copy job"),
    )
    group = tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
    raw_sources = spec.source_operations
    if (
        not isinstance(raw_sources, list)
        or not 1 <= len(raw_sources) <= MAX_SIMPLE_COPY_SOURCES
    ):
        _error(
            f"source_operations must contain one through {MAX_SIMPLE_COPY_SOURCES} exact operations."
        )
    resolved = tuple(
        _resolve_source(document, value, index)
        for index, value in enumerate(raw_sources)
    )
    sources = tuple(value[0] for value in resolved)
    reference_states = tuple(value[1] for value in resolved)
    names = tuple(str(source.Name) for source in sources)
    if len(set(names)) != len(names):
        _error("CAM Simple Copy source_operations must be distinct.")
    if any(source not in group for source in sources):
        _error(
            "Every CAM Simple Copy source must be an exact operation-group entry in the target Job.",
            "NATIVE_MANUFACTURE_TARGET_STALE",
            repair={"available_operation_names": [str(value.Name) for value in group]},
        )

    import Path.Base.Util as PathUtil
    import Path.Dressup.Utils as PathDressup
    import PathScripts.PathUtils as PathUtils
    from Path.CommandBoundary import is_timeline_input_usable

    controller = PathUtil.toolControllerForOp(sources[0])
    coolant = PathUtil.coolantModeForOp(sources[0])
    if (
        controller is None
        or getattr(controller, "Document", None) is not document
        or not is_timeline_input_usable(controller, document)
    ):
        _error(
            "CAM Simple Copy sources require one current tool controller.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    for source in sources:
        if (
            not PathDressup.isOp(source)
            or not source.isValid()
            or not PathUtil.activeForOp(source)
            or not is_timeline_input_usable(source, document)
            or not tuple(getattr(getattr(source, "Path", None), "Commands", ()) or ())
        ):
            _error(
                f"CAM Simple Copy source {source.Name!r} is not one active current toolpath.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        if (
            PathUtil.toolControllerForOp(source) is not controller
            or PathUtil.coolantModeForOp(source) != coolant
        ):
            _error(
                "CAM Simple Copy sources must share one tool controller and coolant mode.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )

    lines: list[str] = []
    total_bytes = 0
    cutting_count = 0
    try:
        for source in sources:
            for command in tuple(PathUtils.getPathWithPlacement(source).Commands or ()):
                line = str(command.toGCode())
                encoded_size = len(line.encode("utf-8"))
                if encoded_size > MAX_SIMPLE_COPY_LINE_BYTES:
                    _error(
                        f"CAM Simple Copy source {source.Name!r} contains a command longer than "
                        f"{MAX_SIMPLE_COPY_LINE_BYTES} bytes.",
                        "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
                    )
                lines.append(line)
                total_bytes += encoded_size
                if str(getattr(command, "Name", "")) in {"G1", "G2", "G3"}:
                    cutting_count += 1
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "A CAM Simple Copy source command stream could not be read.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        ) from exc
    if not lines or cutting_count <= 0:
        _error(
            "CAM Simple Copy sources must contain at least one cutting command.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if len(lines) > MAX_SIMPLE_COPY_COMMANDS or total_bytes > MAX_SIMPLE_COPY_GCODE_BYTES:
        _error(
            "CAM Simple Copy exceeds the synchronous flattened-toolpath workload limit.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
            repair={
                "command_count": len(lines),
                "gcode_bytes": total_bytes,
                "maximum_command_count": MAX_SIMPLE_COPY_COMMANDS,
                "maximum_gcode_bytes": MAX_SIMPLE_COPY_GCODE_BYTES,
            },
        )
    placed_gcode = tuple(lines)
    return PreparedSimpleCopyCreate(
        label=clean_operation_label(spec.label, "CAM Simple Copy"),
        job=job,
        job_before=job_before,
        sources=sources,
        source_reference_before=reference_states,
        source_state_before=tuple(persistent_resource_state(value) for value in sources),
        placed_gcode=placed_gcode,
        placed_gcode_sha256=_gcode_sha256(placed_gcode),
        cutting_command_count=cutting_count,
        controller=controller,
        controller_before=tool_controller_state(controller),
        coolant=str(coolant),
        job_operations_before=group,
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
    prepared: PreparedSimpleCopyCreate,
) -> None:
    if (
        tuple(document.Objects) != prepared.objects_before
        or read_current_selection(document) != prepared.selection_before
        or _timeline_state(document) != prepared.timeline_before
        or tuple(prepared.job.Operations.Group or ()) != prepared.job_operations_before
        or job_state(prepared.job).get("state_sha256")
        != prepared.job_before.get("state_sha256")
        or tool_controller_state(prepared.controller).get("state_sha256")
        != prepared.controller_before.get("state_sha256")
        or tuple(operation_state(source) for source in prepared.sources)
        != prepared.source_reference_before
        or tuple(persistent_resource_state(source) for source in prepared.sources)
        != prepared.source_state_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        _error(
            "The CAM Simple Copy Job, sources, History, selection, or visibility changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def create_simple_copy(
    document: Any,
    *,
    prepared: PreparedSimpleCopyCreate,
) -> NativeMutationDraft:
    """Create one flattened Custom operation inside the owned transaction."""

    if not isinstance(prepared, PreparedSimpleCopyCreate):
        raise TypeError("prepared must be a PreparedSimpleCopyCreate")
    _assert_preflight_current(document, prepared)
    try:
        import Path.Op.Gui.SimpleCopy as PathSimpleCopyGui

        operation = PathSimpleCopyGui.Create(
            prepared.label,
            prepared.sources,
            prepared.job,
            prepared.placed_gcode,
        )
        for existing, visible in prepared.visibility_before:
            if bool(existing.ViewObject.Visibility) is not visible:
                existing.ViewObject.Visibility = visible
        if not document.isProvisionallyEnrolledInTimelineByCurrentTransaction(operation):
            raise RuntimeError("The CAM Simple Copy was not provisionally enrolled in History")
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM Simple Copy factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return NativeMutationDraft(
        value={"prepared": prepared, "operation": operation},
        recompute_targets=(operation, prepared.job),
        created=(object_identity(operation),),
        changed=(object_identity(prepared.job),),
    )


def _label_matches_requested(actual: str, requested: str) -> bool:
    if actual == requested:
        return True
    suffix = actual[len(requested) :] if actual.startswith(requested) else ""
    return len(suffix) >= 3 and suffix.isdigit()


def _verify_timeline(
    document: Any,
    prepared: PreparedSimpleCopyCreate,
    operation: Any,
) -> None:
    after = _timeline_state(document)
    before = prepared.timeline_before
    expected = (
        *before.operations[: before.position],
        operation,
        *before.operations[before.position :],
    )
    if (
        after.timeline is not before.timeline
        or after.operations != expected
        or after.position != before.position + 1
    ):
        _error(
            "The CAM Simple Copy was not inserted at the exact History marker.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    for old_index in range(len(before.operations)):
        new_index = old_index if old_index < before.position else old_index + 1
        if (
            after.visibility[new_index] is not before.visibility[old_index]
            or after.suppression[new_index] is not before.suppression[old_index]
        ):
            _error(
                "CAM Simple Copy changed existing History visibility or suppression.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
            )
    if after.suppression[before.position]:
        _error(
            "The created CAM Simple Copy was unexpectedly suppressed.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )


def verify_created_simple_copy(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact flattening, source preservation, ownership, and History state."""

    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedSimpleCopyCreate) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM Simple Copy")
    if tuple(document.Objects) != (*prepared.objects_before, operation):
        _error(
            "CAM Simple Copy creation changed objects outside its exact output.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if tuple(prepared.job.Operations.Group or ()) != (
        *prepared.job_operations_before,
        operation,
    ):
        _error(
            "The CAM Simple Copy is not the exact final operation in its Job group.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if (
        document.getObject(str(operation.Name)) is not operation
        or not operation.isDerivedFrom("Path::Feature")
        or not operation.isValid()
        or str(getattr(operation, "VibeCADTimelineRole", "") or "") != "operation"
    ):
        _error(
            "The created CAM Simple Copy is not one valid exact History operation.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    import Path.Base.Util as PathUtil
    import Path as PathModule
    import Path.Op.Custom as PathCustom
    import Path.Op.Gui.SimpleCopy as PathSimpleCopyGui
    import PathScripts.PathUtils as PathUtils

    actual_label = str(operation.Label)
    if (
        not isinstance(getattr(operation, "Proxy", None), PathCustom.ObjectEmbeddedPath)
        or not isinstance(
            getattr(getattr(operation, "ViewObject", None), "Proxy", None),
            PathSimpleCopyGui.ViewProvider,
        )
        or PathUtils.findParentJob(operation) is not prepared.job
        or PathUtil.timelineParentJob(operation) is not prepared.job
        or PathUtil.toolControllerForOp(operation) is not prepared.controller
        or PathUtil.coolantModeForOp(operation) != prepared.coolant
        or not _label_matches_requested(actual_label, prepared.label)
    ):
        _error(
            "The created CAM Simple Copy lost its exact proxy, Job, controller, coolant, or label.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if any(source in tuple(operation.OutList or ()) for source in prepared.sources):
        _error(
            "The flattened CAM Simple Copy unexpectedly retained source links.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    stored_gcode = tuple(str(value) for value in operation.Gcode)
    actual_commands = tuple(
        str(command.toGCode()) for command in tuple(operation.Path.Commands or ())
    )
    actual_cutting_count = sum(
        1
        for command in tuple(operation.Path.Commands or ())
        if str(getattr(command, "Name", "")) in {"G1", "G2", "G3"}
    )
    stored_matches = stored_gcode == prepared.placed_gcode
    stored_hash_matches = _gcode_sha256(stored_gcode) == prepared.placed_gcode_sha256
    try:
        normalized_stream = tuple(
            PathModule.Command(line).toGCode() for line in prepared.placed_gcode
        )
    except Exception as exc:
        raise NativeManufactureError(
            "The frozen CAM Simple Copy command stream could not be parsed.",
            error_code="NATIVE_MANUFACTURE_TOOLPATH_INVALID",
        ) from exc
    expected_output = (
        PathModule.Command(f"({actual_label})").toGCode(),
        PathModule.Command("(Begin Custom)").toGCode(),
        *normalized_stream,
        PathModule.Command("(End Custom)").toGCode(),
    )
    output_matches = actual_commands == expected_output
    if (
        not stored_matches
        or not stored_hash_matches
        or not output_matches
        or actual_cutting_count != prepared.cutting_command_count
    ):
        first_stored_difference = next(
            (
                index
                for index, (expected, actual) in enumerate(
                    zip(prepared.placed_gcode, stored_gcode)
                )
                if expected != actual
            ),
            None,
        )
        first_output_difference = next(
            (
                index
                for index in range(max(len(expected_output), len(actual_commands)))
                if index >= len(expected_output)
                or index >= len(actual_commands)
                or expected_output[index] != actual_commands[index]
            ),
            None,
        )
        _error(
            "The CAM Simple Copy did not retain the exact flattened placed command stream.",
            "NATIVE_MANUFACTURE_OPERATION_GENERATION_FAILED",
            repair={
                "expected_gcode_line_count": len(prepared.placed_gcode),
                "actual_gcode_line_count": len(stored_gcode),
                "expected_cutting_command_count": prepared.cutting_command_count,
                "actual_cutting_command_count": actual_cutting_count,
                "stored_gcode_matches": stored_matches,
                "stored_gcode_hash_matches": stored_hash_matches,
                "output_command_stream_matches": output_matches,
                "expected_output_command_count": len(expected_output),
                "actual_output_command_count": len(actual_commands),
                "first_stored_difference": first_stored_difference,
                "expected_stored_line": (
                    prepared.placed_gcode[first_stored_difference][:256]
                    if first_stored_difference is not None
                    else None
                ),
                "actual_stored_line": (
                    stored_gcode[first_stored_difference][:256]
                    if first_stored_difference is not None
                    else None
                ),
                "first_output_difference": first_output_difference,
                "expected_output_line": (
                    expected_output[first_output_difference][:256]
                    if first_output_difference is not None
                    and first_output_difference < len(expected_output)
                    else None
                ),
                "actual_output_line": (
                    actual_commands[first_output_difference][:256]
                    if first_output_difference is not None
                    and first_output_difference < len(actual_commands)
                    else None
                ),
            },
        )
    _verify_timeline(document, prepared, operation)
    if (
        tuple(persistent_resource_state(source) for source in prepared.sources)
        != prepared.source_state_before
    ):
        _error(
            "CAM Simple Copy creation changed one of its source toolpaths.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if (
        read_current_selection(document) != prepared.selection_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        _error(
            "CAM Simple Copy changed the human selection or existing visibility.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    after_job = job_state(prepared.job)
    if (
        _job_invariants(after_job) != _job_invariants(prepared.job_before)
        or int(after_job["counts"]["operations"])
        != int(prepared.job_before["counts"]["operations"]) + 1
    ):
        _error(
            "CAM Simple Copy changed unrelated Job resources.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    state = operation_state(operation)
    return {
        "operation": "simple_copy",
        "object_name": str(operation.Name),
        "label": actual_label[:160],
        "job_object_name": str(prepared.job.Name),
        "source_operation_names": [str(source.Name) for source in prepared.sources],
        "tool_controller_name": str(prepared.controller.Name),
        "coolant": prepared.coolant.lower(),
        "flattened_command_count": len(prepared.placed_gcode),
        "output_command_count": len(actual_commands),
        "cutting_command_count": actual_cutting_count,
        "flattened_gcode_sha256": prepared.placed_gcode_sha256,
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
