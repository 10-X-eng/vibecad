# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact setup-scoped CAM stock configuration."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureState import (
    candidate_model_state,
    is_job,
    job_state,
    resolve_job_target,
    resolve_model_target,
)
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection


@dataclass(frozen=True, slots=True)
class PreparedStockConfiguration:
    job: Any
    job_before: Mapping[str, Any]
    stock_before: Any
    stock_state_before: Mapping[str, Any]
    specification: Mapping[str, Any]
    source: Any | None
    source_before: Mapping[str, Any] | None
    objects_before: tuple[Any, ...]
    other_jobs_before: tuple[tuple[Any, str], ...]
    selection_before: Mapping[str, Any]


def _service():
    try:
        from Path.Main import JobStock

        return JobStock
    except Exception as exc:
        raise NativeManufactureError(
            "The shared CAM stock editor is unavailable.",
            error_code="NATIVE_MANUFACTURE_ENVIRONMENT_UNAVAILABLE",
        ) from exc


def _other_job_states(document: Any, job: Any) -> tuple[tuple[Any, str], ...]:
    return tuple(
        (candidate, str(job_state(candidate)["state_sha256"]))
        for candidate in tuple(getattr(document, "Objects", ()) or ())
        if candidate is not job and is_job(candidate)
    )


def prepare_stock_configuration(
    document: Any,
    *,
    target: Mapping[str, Any],
    stock: Mapping[str, Any],
) -> PreparedStockConfiguration:
    """Freeze one exact setup, stock meaning, and optional source solid."""

    job, before = resolve_job_target(document, target)
    service = _service()
    source = None
    source_before = None
    semantic_stock = dict(stock)
    if semantic_stock.get("kind") == "existing_solid":
        source, source_before = resolve_model_target(document, semantic_stock["source"])
        semantic_stock["source"] = {"object_name": str(source.Name)}
    try:
        normalized = service.normalize_stock_specification(semantic_stock)
        normalized = service.validate_stock_configuration(
            job,
            normalized,
            source=source,
        )
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            str(exc),
            error_code="NATIVE_ARGUMENTS_INVALID",
        ) from exc
    return PreparedStockConfiguration(
        job=job,
        job_before=before,
        stock_before=getattr(job, "Stock", None),
        stock_state_before=service.stock_configuration_state(job),
        specification=normalized,
        source=source,
        source_before=source_before,
        objects_before=tuple(document.Objects),
        other_jobs_before=_other_job_states(document, job),
        selection_before=read_current_selection(document),
    )


def _assert_current(document: Any, prepared: PreparedStockConfiguration) -> None:
    service = _service()
    if tuple(document.Objects) != prepared.objects_before:
        raise NativeManufactureError(
            "The CAM document graph changed before stock editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if job_state(prepared.job)["state_sha256"] != prepared.job_before["state_sha256"]:
        raise NativeManufactureError(
            "The exact CAM setup changed before stock editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if service.stock_configuration_state(prepared.job) != prepared.stock_state_before:
        raise NativeManufactureError(
            "The exact CAM stock changed before editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if any(
        job_state(job)["state_sha256"] != state
        for job, state in prepared.other_jobs_before
    ):
        raise NativeManufactureError(
            "Another CAM setup changed before stock editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if prepared.source is not None and candidate_model_state(
        prepared.source
    ) != prepared.source_before:
        raise NativeManufactureError(
            "The exact existing-stock source changed before editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "The human selection changed before stock editing.",
            error_code="NATIVE_MANUFACTURE_STATE_STALE",
        )


def configure_stock(
    document: Any,
    prepared: PreparedStockConfiguration,
) -> NativeMutationDraft:
    """Apply stock only to its prepared setup."""

    if not isinstance(prepared, PreparedStockConfiguration):
        raise TypeError("prepared must be a PreparedStockConfiguration")
    _assert_current(document, prepared)
    try:
        configuration = _service().apply_stock_configuration(
            prepared.job,
            prepared.specification,
            source=prepared.source,
        )
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            str(exc),
            error_code="NATIVE_ARGUMENTS_INVALID",
        ) from exc
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            "The CAM stock could not be configured.",
            error_code="NATIVE_MANUFACTURE_STOCK_EDIT_FAILED",
            repair={
                "failure_type": type(exc).__name__,
                "failure": str(exc)[:320],
            },
        ) from exc
    current_stock = prepared.job.Stock
    return NativeMutationDraft(
        value={
            "prepared": prepared,
            "configuration": configuration,
            "stock": current_stock,
        },
        recompute_targets=(prepared.job, current_stock),
        changed=(object_identity(prepared.job), object_identity(current_stock)),
    )


def _matches(configuration: Mapping[str, Any], expected: Mapping[str, Any]) -> bool:
    for name, value in expected.items():
        if name == "source":
            actual = configuration.get("source")
            if not isinstance(actual, Mapping) or actual.get("object_name") != value.get(
                "object_name"
            ):
                return False
        elif configuration.get(name) != value:
            return False
    return True


def verify_stock_configuration(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    """Prove exact stock state and isolation from every unrelated setup."""

    prepared = draft.value["prepared"]
    service = _service()
    actual = service.stock_configuration_state(prepared.job)
    if not _matches(actual, prepared.specification):
        raise NativeManufactureError(
            "The CAM stock edit failed its exact postcondition.",
            error_code="NATIVE_MANUFACTURE_STOCK_POSTCONDITION_FAILED",
        )
    if any(
        job_state(job)["state_sha256"] != state
        for job, state in prepared.other_jobs_before
    ):
        raise NativeManufactureError(
            "Editing one CAM stock changed another setup.",
            error_code="NATIVE_MANUFACTURE_STOCK_POSTCONDITION_FAILED",
        )
    if read_current_selection(document) != prepared.selection_before:
        raise NativeManufactureError(
            "Editing CAM stock changed the human selection.",
            error_code="NATIVE_MANUFACTURE_STOCK_POSTCONDITION_FAILED",
        )
    current_stock = prepared.job.Stock
    expected_objects = tuple(
        obj for obj in prepared.objects_before if obj is not prepared.stock_before
    )
    actual_objects = tuple(obj for obj in document.Objects if obj is not current_stock)
    if actual_objects != expected_objects:
        raise NativeManufactureError(
            "Editing CAM stock changed an unrelated document object.",
            error_code="NATIVE_MANUFACTURE_STOCK_POSTCONDITION_FAILED",
        )
    return {
        "job": job_state(prepared.job),
        "stock": actual,
    }
