# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact setup-scoped CAM workpiece orientation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import candidate_model_state, job_state, resolve_job_target
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


@dataclass(frozen=True, slots=True)
class PreparedWorkpieceOrientation:
    job: Any
    job_before: Mapping[str, Any]
    frame: Mapping[str, Any]
    include_stock: bool
    workpiece_before: Mapping[str, Any]
    source_states_before: tuple[tuple[Any, str], ...]
    objects_before: tuple[Any, ...]
    other_jobs_before: tuple[tuple[Any, str], ...]
    selection_before: Mapping[str, Any]


def _service():
    try:
        from Path.Main import JobWorkCoordinate

        return JobWorkCoordinate
    except Exception as exc:
        raise NativeManufactureError(
            "The shared CAM work-coordinate editor is unavailable.",
            error_code="NATIVE_MANUFACTURE_ENVIRONMENT_UNAVAILABLE",
        ) from exc


def _other_job_states(document: Any, job: Any) -> tuple[tuple[Any, str], ...]:
    from VibeCADNativeManufactureState import is_job

    return tuple(
        (candidate, str(job_state(candidate)["state_sha256"]))
        for candidate in tuple(document.Objects)
        if candidate is not job and is_job(candidate)
    )


def _source_states(job: Any) -> tuple[tuple[Any, str], ...]:
    result = []
    for resource in tuple(getattr(getattr(job, "Model", None), "Group", ()) or ()):
        try:
            source = job.Proxy.baseObject(job, resource)
            result.append((source, str(candidate_model_state(source)["state_sha256"])))
        except NativeManufactureError:
            raise
        except Exception as exc:
            raise NativeManufactureError(
                "A CAM workpiece model has no exact public source.",
                error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            ) from exc
    return tuple(result)


def prepare_workpiece_orientation(
    document: Any,
    *,
    target: Mapping[str, Any],
    frame: Mapping[str, Any],
    include_stock: bool,
) -> PreparedWorkpieceOrientation:
    """Freeze one setup and one workpiece frame."""

    job, before = resolve_job_target(document, target)
    if not isinstance(include_stock, bool):
        raise NativeManufactureError(
            "include_stock must be boolean.",
            error_code="NATIVE_ARGUMENTS_INVALID",
        )
    service = _service()
    try:
        normalized = service.normalize_workpiece_frame(frame)
        if not tuple(getattr(getattr(job, "Model", None), "Group", ()) or ()):
            raise ValueError("the CAM setup has no workpiece models")
        if include_stock and getattr(job, "Stock", None) is None:
            raise ValueError("the CAM setup has no stock")
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            str(exc),
            error_code="NATIVE_ARGUMENTS_INVALID",
        ) from exc
    return PreparedWorkpieceOrientation(
        job=job,
        job_before=before,
        frame=normalized,
        include_stock=include_stock,
        workpiece_before=service.workpiece_configuration_state(job),
        source_states_before=_source_states(job),
        objects_before=tuple(document.Objects),
        other_jobs_before=_other_job_states(document, job),
        selection_before=read_current_selection(document),
    )


def _assert_current(document: Any, prepared: PreparedWorkpieceOrientation) -> None:
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeManufactureError(
            "The CAM document graph changed before workpiece orientation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if job_state(prepared.job)["state_sha256"] != prepared.job_before["state_sha256"]:
        raise NativeManufactureError(
            "The exact CAM setup changed before workpiece orientation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if _service().workpiece_configuration_state(
        prepared.job
    ) != prepared.workpiece_before:
        raise NativeManufactureError(
            "The exact CAM workpiece changed before orientation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if any(
        job_state(job)["state_sha256"] != state
        for job, state in prepared.other_jobs_before
    ):
        raise NativeManufactureError(
            "Another CAM setup changed before workpiece orientation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if any(
        candidate_model_state(source)["state_sha256"] != state
        for source, state in prepared.source_states_before
    ):
        raise NativeManufactureError(
            "A public workpiece source changed before orientation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "The human selection changed before workpiece orientation.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )


def orient_workpiece(
    document: Any,
    prepared: PreparedWorkpieceOrientation,
) -> NativeMutationDraft:
    """Map one prepared workpiece frame onto machine XYZ."""

    if not isinstance(prepared, PreparedWorkpieceOrientation):
        raise TypeError("prepared must be a PreparedWorkpieceOrientation")
    _assert_current(document, prepared)
    try:
        configuration = _service().apply_workpiece_frame(
            prepared.job,
            prepared.frame,
            include_stock=prepared.include_stock,
        )
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            str(exc),
            error_code="NATIVE_ARGUMENTS_INVALID",
        ) from exc
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM workpiece could not be oriented.",
            error_code="NATIVE_MANUFACTURE_WORKPIECE_EDIT_FAILED",
        ) from exc
    resources = tuple(getattr(prepared.job.Model, "Group", ()) or ())
    if prepared.include_stock:
        resources += (prepared.job.Stock,)
    return NativeMutationDraft(
        value={"prepared": prepared, "configuration": configuration},
        recompute_targets=(prepared.job, *resources),
        changed=tuple(object_identity(value) for value in (prepared.job, *resources)),
    )


def verify_workpiece_orientation(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact workpiece state and isolation from other setups."""

    prepared = draft.value["prepared"]
    actual = _service().workpiece_configuration_state(prepared.job)
    if actual != draft.value["configuration"]:
        raise NativeManufactureError(
            "The CAM workpiece edit failed its exact postcondition.",
            error_code="NATIVE_MANUFACTURE_WORKPIECE_POSTCONDITION_FAILED",
        )
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeManufactureError(
            "Orienting a CAM workpiece changed the document graph.",
            error_code="NATIVE_MANUFACTURE_WORKPIECE_POSTCONDITION_FAILED",
        )
    if any(
        job_state(job)["state_sha256"] != state
        for job, state in prepared.other_jobs_before
    ):
        raise NativeManufactureError(
            "Orienting one CAM workpiece changed another setup.",
            error_code="NATIVE_MANUFACTURE_WORKPIECE_POSTCONDITION_FAILED",
        )
    if any(
        candidate_model_state(source)["state_sha256"] != state
        for source, state in prepared.source_states_before
    ):
        raise NativeManufactureError(
            "Orienting CAM workpiece clones changed a public source.",
            error_code="NATIVE_MANUFACTURE_WORKPIECE_POSTCONDITION_FAILED",
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "Orienting CAM workpiece clones changed the human selection.",
            error_code="NATIVE_MANUFACTURE_WORKPIECE_POSTCONDITION_FAILED",
        )
    return {"job": job_state(prepared.job), "workpiece": actual}
