# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact boundary and Comment implementation for CAM program actions."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureContract import clean_path_operation_label
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import (
    MAX_JOB_OPERATIONS,
    job_state,
    operation_state,
    resolve_job_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


MAX_PROGRAM_COMMENT_CHARACTERS = 1024


@dataclass(frozen=True, slots=True)
class CommentCreateSpec:
    label: Any
    job: Mapping[str, Any]
    comment: Any


@dataclass(frozen=True, slots=True)
class ProgramTimelineState:
    timeline: Any
    operations: tuple[Any, ...]
    visibility: tuple[bool, ...]
    suppression: tuple[bool, ...]
    position: int


@dataclass(frozen=True, slots=True)
class PreparedProgramBoundary:
    noun: str
    job: Any
    job_before: Mapping[str, Any]
    job_operations_before: tuple[Any, ...]
    objects_before: tuple[Any, ...]
    visibility_before: tuple[tuple[Any, bool], ...]
    selection_before: Any
    timeline_before: ProgramTimelineState


@dataclass(frozen=True, slots=True)
class PreparedCommentCreate:
    label: str
    comment: str
    boundary: PreparedProgramBoundary


def program_error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def program_label(value: Any) -> str:
    """Return a label safe for operation types that emit it as G-code text."""

    return clean_path_operation_label(value, "program operation")


def _transaction_open(document: Any) -> bool:
    booked = getattr(document, "getBookedTransactionID", None)
    return bool(
        bool(getattr(document, "HasPendingTransaction", False))
        or (callable(booked) and int(booked() or 0) != 0)
    )


def _timeline_state(document: Any) -> ProgramTimelineState:
    timeline = document.getObject("VibeCADTimeline")
    if timeline is None or str(getattr(timeline, "TypeId", "")) != (
        "App::DocumentTimeline"
    ):
        program_error(
            "A CAM program operation requires a valid document History.",
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
        program_error(
            "The document History has inconsistent program-operation state.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    return ProgramTimelineState(
        timeline,
        operations,
        visibility,
        suppression,
        position,
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


def preflight_program_boundary(
    document: Any,
    job_target: Mapping[str, Any],
    *,
    noun: str,
) -> PreparedProgramBoundary:
    """Freeze one exact Job, graph, History marker, selection, and visibility."""

    clean_noun = str(noun or "").strip()
    if not clean_noun:
        raise TypeError("noun must be a nonempty string")
    if _transaction_open(document):
        program_error(
            f"Finish or cancel the open task before adding {clean_noun}.",
            "NATIVE_TRANSACTION_ACTIVE",
        )
    if bool(getattr(document, "Recomputing", False)) or bool(
        getattr(document, "RecomputePending", False)
    ):
        program_error(
            f"Wait for document recompute to finish before adding {clean_noun}.",
            "NATIVE_MANUFACTURE_RECOMPUTE_ACTIVE",
        )
    job, before = resolve_job_target(document, job_target)
    operations = tuple(getattr(getattr(job, "Operations", None), "Group", ()) or ())
    if len(operations) >= MAX_JOB_OPERATIONS:
        program_error(
            "The CAM Job has reached the bounded Native operation limit.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
            repair={
                "operation_count": len(operations),
                "maximum_operation_count": MAX_JOB_OPERATIONS,
            },
        )
    return PreparedProgramBoundary(
        noun=clean_noun,
        job=job,
        job_before=before,
        job_operations_before=operations,
        objects_before=tuple(document.Objects),
        visibility_before=tuple(
            (obj, bool(obj.ViewObject.Visibility))
            for obj in tuple(document.Objects)
            if getattr(obj, "ViewObject", None) is not None
        ),
        selection_before=read_current_selection(document),
        timeline_before=_timeline_state(document),
    )


def assert_program_boundary_current(
    document: Any,
    prepared: PreparedProgramBoundary,
) -> None:
    if not isinstance(prepared, PreparedProgramBoundary):
        raise TypeError("prepared must be a PreparedProgramBoundary")
    if (
        tuple(document.Objects) != prepared.objects_before
        or tuple(prepared.job.Operations.Group or ())
        != prepared.job_operations_before
        or job_state(prepared.job).get("state_sha256")
        != prepared.job_before.get("state_sha256")
        or _timeline_state(document) != prepared.timeline_before
        or read_current_selection(document) != prepared.selection_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        program_error(
            "The exact CAM Job, History, selection, or visibility changed after preflight.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def program_mutation_draft(
    prepared: PreparedProgramBoundary,
    operation: Any,
    *,
    value: Mapping[str, Any],
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedProgramBoundary):
        raise TypeError("prepared must be a PreparedProgramBoundary")
    return NativeMutationDraft(
        value={**dict(value), "operation": operation},
        recompute_targets=(operation, prepared.job),
        created=(object_identity(operation),),
        changed=(object_identity(prepared.job),),
    )


def _verify_timeline(
    document: Any,
    prepared: PreparedProgramBoundary,
    operation: Any,
) -> None:
    before = prepared.timeline_before
    after = _timeline_state(document)
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
        program_error(
            f"The {prepared.noun} was not inserted at the exact History marker.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )
    for old_index in range(len(before.operations)):
        new_index = old_index if old_index < before.position else old_index + 1
        if (
            after.visibility[new_index] is not before.visibility[old_index]
            or after.suppression[new_index] is not before.suppression[old_index]
        ):
            program_error(
                f"Creating {prepared.noun} changed existing History state.",
                "NATIVE_MANUFACTURE_HISTORY_INVALID",
            )
    if after.suppression[before.position]:
        program_error(
            f"The created {prepared.noun} was unexpectedly suppressed.",
            "NATIVE_MANUFACTURE_HISTORY_INVALID",
        )


def verify_program_operation(
    document: Any,
    prepared: PreparedProgramBoundary,
    operation: Any,
    *,
    label: str,
    proxy_type: type,
    view_proxy_type: type,
    allow_numeric_label_suffix: bool = False,
) -> tuple[Mapping[str, Any], Mapping[str, Any]]:
    """Prove the common graph, Job, History, UI, and receipt-ready contract."""

    if not isinstance(prepared, PreparedProgramBoundary):
        raise TypeError("prepared must be a PreparedProgramBoundary")
    if tuple(document.Objects) != (*prepared.objects_before, operation):
        program_error(
            f"Creating {prepared.noun} changed objects outside its exact output.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if tuple(prepared.job.Operations.Group or ()) != (
        *prepared.job_operations_before,
        operation,
    ):
        program_error(
            f"The {prepared.noun} is not the exact final operation in its Job group.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )

    import Path.Base.Util as PathUtil
    import PathScripts.PathUtils as PathUtils

    actual_label = str(operation.Label)
    label_suffix = (
        actual_label[len(label) :] if actual_label.startswith(label) else ""
    )
    label_matches = actual_label == label or (
        allow_numeric_label_suffix
        and len(label_suffix) >= 3
        and label_suffix.isdigit()
    )

    identity_failures = tuple(
        name
        for name, valid in (
            (
                "document_identity",
                document.getObject(str(operation.Name)) is operation,
            ),
            ("path_feature", operation.isDerivedFrom("Path::Feature")),
            ("object_validity", operation.isValid()),
            (
                "operation_proxy",
                isinstance(getattr(operation, "Proxy", None), proxy_type),
            ),
            (
                "view_provider",
                isinstance(
                    getattr(getattr(operation, "ViewObject", None), "Proxy", None),
                    view_proxy_type,
                ),
            ),
            ("label", label_matches),
            ("job_membership", PathUtils.findParentJob(operation) is prepared.job),
            ("history_job", PathUtil.timelineParentJob(operation) is prepared.job),
            (
                "history_role",
                str(getattr(operation, "VibeCADTimelineRole", "") or "")
                == "operation",
            ),
            (
                "replacement_state",
                not tuple(
                    getattr(operation, "VibeCADTimelineReplacedInputs", ()) or ()
                ),
            ),
        )
        if not valid
    )
    if identity_failures:
        program_error(
            f"The created {prepared.noun} failed exact identity checks: "
            f"{', '.join(identity_failures)}.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={
                "failed_invariants": list(identity_failures),
                **(
                    {
                        "expected_label": label,
                        "actual_label": actual_label,
                    }
                    if "label" in identity_failures
                    else {}
                ),
            },
        )
    _verify_timeline(document, prepared, operation)
    if (
        read_current_selection(document) != prepared.selection_before
        or any(
            bool(obj.ViewObject.Visibility) is not visible
            for obj, visible in prepared.visibility_before
        )
    ):
        program_error(
            f"Creating {prepared.noun} changed human selection or existing visibility.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    after_job = job_state(prepared.job)
    before_operations = tuple(prepared.job_before.get("operations", ()))
    after_operations = tuple(after_job.get("operations", ()))
    before_counts = dict(prepared.job_before.get("counts", {}))
    after_counts = dict(after_job.get("counts", {}))
    if (
        _job_invariants(after_job) != _job_invariants(prepared.job_before)
        or after_operations[:-1] != before_operations
        or len(after_operations) != len(before_operations) + 1
        or int(after_counts.get("operations", -1))
        != int(before_counts.get("operations", -1)) + 1
        or int(after_counts.get("active_operations", -1))
        != int(before_counts.get("active_operations", -1)) + 1
    ):
        program_error(
            f"Creating {prepared.noun} changed unrelated Job resources.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return operation_state(operation), after_job


def _comment(value: Any) -> str:
    if not isinstance(value, str):
        program_error(
            "comment must be one string.",
            repair={"field": "comment", "expected_type": "string"},
        )
    result = value.strip()
    if not result or len(result) > MAX_PROGRAM_COMMENT_CHARACTERS:
        program_error(
            "comment must contain 1 through 1024 characters.",
            repair={"field": "comment", "minimum_length": 1, "maximum_length": 1024},
        )
    rejected = next(
        (
            character
            for character in result
            if ord(character) < 0x20
            or ord(character) > 0x7E
            or character in "()"
        ),
        None,
    )
    if rejected is not None:
        program_error(
            "comment must use printable ASCII without parentheses or line breaks.",
            repair={
                "field": "comment",
                "accepted": "printable ASCII characters 0x20 through 0x7e except ( and )",
                "rejected_codepoint": f"U+{ord(rejected):04X}",
            },
        )
    return result


def preflight_comment_create(
    document: Any,
    spec: CommentCreateSpec,
) -> PreparedCommentCreate:
    if not isinstance(spec, CommentCreateSpec):
        raise TypeError("spec must be a CommentCreateSpec")
    return PreparedCommentCreate(
        label=program_label(spec.label),
        comment=_comment(spec.comment),
        boundary=preflight_program_boundary(
            document,
            spec.job,
            noun="CAM comment",
        ),
    )


def create_comment(
    document: Any,
    *,
    prepared: PreparedCommentCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedCommentCreate):
        raise TypeError("prepared must be a PreparedCommentCreate")
    boundary = prepared.boundary
    assert_program_boundary_current(document, boundary)
    try:
        import Path.Op.Gui.Comment as CommentGui

        operation = CommentGui.CreateInTransaction(
            document,
            boundary.job,
            name="Comment",
            text=prepared.comment,
        )
        operation.Label = prepared.label
        CommentGui._validate_comment_result(
            document,
            boundary.job,
            operation,
            require_path=False,
        )
        document.publishProvisionalTimelineOperationBlock(operation, (), ())
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The native CAM comment factory could not create the requested operation.",
            error_code="NATIVE_MANUFACTURE_OPERATION_CREATE_FAILED",
            repair={
                "native_error_type": type(exc).__name__,
                "native_error": str(exc)[:320],
            },
        ) from exc
    return program_mutation_draft(
        boundary,
        operation,
        value={"prepared": prepared},
    )


def verify_created_comment(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    value = draft.value if isinstance(draft.value, Mapping) else {}
    prepared = value.get("prepared")
    operation = value.get("operation")
    if not isinstance(prepared, PreparedCommentCreate) or operation is None:
        raise TypeError("draft must contain one exact prepared CAM comment")

    import Path.Op.Gui.Comment as CommentGui

    state, after_job = verify_program_operation(
        document,
        prepared.boundary,
        operation,
        label=prepared.label,
        proxy_type=CommentGui.Comment,
        view_proxy_type=CommentGui._ViewProviderComment,
    )
    commands = tuple(getattr(operation.Path, "Commands", ()) or ())
    expected_gcode = f"({prepared.comment})"
    if (
        str(operation.Comment) != prepared.comment
        or len(commands) != 1
        or str(commands[0].Name) != expected_gcode
        or dict(commands[0].Parameters) != {}
        or str(commands[0].toGCode()) != expected_gcode
    ):
        program_error(
            "The created CAM comment did not retain its exact safe comment command.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "comment",
        "object_name": str(operation.Name),
        "label": str(operation.Label)[:160],
        "job_object_name": str(prepared.boundary.job.Name),
        "comment_length": len(prepared.comment),
        "command_count": 1,
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": after_job.get("state_sha256"),
    }
