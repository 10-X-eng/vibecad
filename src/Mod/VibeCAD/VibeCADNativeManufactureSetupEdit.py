# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact setup-scoped CAM Job configuration edits."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import is_job, job_state, resolve_job_target
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


@dataclass(frozen=True, slots=True)
class PreparedSetupUpdate:
    job: Any
    job_before: Mapping[str, Any]
    configuration_before: Mapping[str, Any]
    changes: Mapping[str, Any]
    objects_before: tuple[Any, ...]
    other_configurations: tuple[tuple[Any, Mapping[str, Any]], ...]
    selection_before: Mapping[str, Any]


def _service():
    try:
        from Path.Main import JobSetup

        return JobSetup
    except Exception as exc:
        raise NativeManufactureError(
            "The shared CAM setup editor is unavailable.",
            error_code="NATIVE_MANUFACTURE_ENVIRONMENT_UNAVAILABLE",
        ) from exc


def _configuration_inventory(document: Any, *, except_job: Any) -> tuple:
    service = _service()
    return tuple(
        (obj, service.setup_configuration_state(obj))
        for obj in tuple(getattr(document, "Objects", ()) or ())
        if obj is not except_job and is_job(obj)
    )


def prepare_setup_update(
    document: Any,
    *,
    target: Mapping[str, Any],
    changes: Mapping[str, Any],
) -> PreparedSetupUpdate:
    """Freeze one exact setup and validate the entire edit before mutation."""

    job, before = resolve_job_target(document, target)
    service = _service()
    try:
        normalized = service.normalize_setup_changes(changes)
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            str(exc),
            error_code="NATIVE_ARGUMENTS_INVALID",
        ) from exc
    return PreparedSetupUpdate(
        job=job,
        job_before=before,
        configuration_before=service.setup_configuration_state(job),
        changes=normalized,
        objects_before=tuple(document.Objects),
        other_configurations=_configuration_inventory(document, except_job=job),
        selection_before=read_current_selection(document),
    )


def _assert_unchanged(document: Any, prepared: PreparedSetupUpdate) -> None:
    service = _service()
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeManufactureError(
            "The CAM document graph changed before setup editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if job_state(prepared.job).get("state_sha256") != prepared.job_before.get(
        "state_sha256"
    ):
        raise NativeManufactureError(
            "The exact CAM setup changed before editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if service.setup_configuration_state(
        prepared.job
    ) != prepared.configuration_before or any(
        service.setup_configuration_state(job) != state
        for job, state in prepared.other_configurations
    ):
        raise NativeManufactureError(
            "A CAM setup configuration changed before editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "The human selection changed before setup editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )


def update_setup_configuration(
    document: Any,
    prepared: PreparedSetupUpdate,
) -> NativeMutationDraft:
    """Apply the prepared edit only to its explicit CAM setup."""

    if not isinstance(prepared, PreparedSetupUpdate):
        raise TypeError("prepared must be a PreparedSetupUpdate")
    _assert_unchanged(document, prepared)
    configuration = _service().apply_setup_configuration(
        prepared.job,
        prepared.changes,
    )
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "configuration": configuration,
        },
        recompute_targets=(prepared.job,),
        changed=(object_identity(prepared.job),),
    )


def verify_setup_update(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    """Prove requested fields and every unrelated setup remained exact."""

    prepared = draft.value["prepared"]
    service = _service()
    actual = service.setup_configuration_state(prepared.job)
    if any(actual.get(name) != value for name, value in prepared.changes.items()):
        raise NativeManufactureError(
            "The CAM setup edit failed its exact postcondition.",
            error_code="NATIVE_MANUFACTURE_SETUP_POSTCONDITION_FAILED",
        )
    if tuple(document.Objects) != prepared.objects_before or any(
        service.setup_configuration_state(job) != state
        for job, state in prepared.other_configurations
    ):
        raise NativeManufactureError(
            "Editing one CAM setup changed another setup or the document graph.",
            error_code="NATIVE_MANUFACTURE_SETUP_POSTCONDITION_FAILED",
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "Editing the CAM setup changed the human selection.",
            error_code="NATIVE_MANUFACTURE_SETUP_POSTCONDITION_FAILED",
        )
    return {
        "job": job_state(prepared.job),
        "configuration": actual,
    }
