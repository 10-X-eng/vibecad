# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host Analysis Runtime adapter for detached VibeCAD Aero solves."""

from __future__ import annotations

from typing import Any, Callable

import AeroAirfoil
import AeroConfig
import AeroDetachedAnalysis
import AeroFlightCard
import AeroMass
import AeroPreview
import AeroRepair
import AeroResults
import AeroStamp
import VibeCADAero

from VibeCADNativeBackground import NativeBackgroundError
from VibeCADNativeTargets import document_uid


_SUPPORTED_OPERATIONS = frozenset({"analyze", "section", "vlm"})


class AeroAnalysisRuntimeError(RuntimeError):
    """A prepared Aero solve cannot be published against the exact document."""

    def __init__(
        self,
        message: str,
        *,
        error_code: str,
        current_revision: str | None = None,
    ) -> None:
        super().__init__(str(message).strip())
        self.error_code = str(error_code or "AERO_ANALYSIS_RUNTIME")
        self.current_revision = str(current_revision or "").strip() or None

    def failure(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_code": self.error_code,
            "message": str(self),
        }
        if self.current_revision is not None:
            result["current_revision"] = self.current_revision
        return result


def prepare_document_input(
    document: Any,
    operation: str,
) -> tuple[AeroDetachedAnalysis.PreparedAeroAnalysis, str]:
    """Freeze document-bound Aero inputs before detached execution starts."""

    clean_operation = str(operation or "").strip().lower()
    if clean_operation not in _SUPPORTED_OPERATIONS:
        raise AeroAnalysisRuntimeError(
            "Aero background operation must be analyze, section, or vlm.",
            error_code="AERO_OPERATION_INVALID",
        )
    cfg = AeroConfig.resolve_geometry(document)
    revision = AeroPreview.geometry_revision(document, cfg)
    coordinates, airfoil_source = AeroAirfoil.load_airfoil_coordinates(cfg["airfoil"])
    prepared = AeroDetachedAnalysis.PreparedAeroAnalysis.create(
        operation=clean_operation,
        config=cfg,
        coordinates=coordinates,
        airfoil_source=airfoil_source,
    )
    return prepared, revision


def run_detached(
    prepared: AeroDetachedAnalysis.PreparedAeroAnalysis,
    *,
    cancelled: Callable[[], bool] | None = None,
    progress: Callable[[int, str], None] | None = None,
) -> AeroDetachedAnalysis.CompletedAeroAnalysis:
    """Run only the document-free Aero computation."""

    return AeroDetachedAnalysis.execute(
        prepared,
        cancelled=cancelled,
        progress=progress,
    )


def validate_document_input(
    document: Any,
    expected_revision: str,
    *,
    active_document: Callable[[], Any] | None = None,
) -> None:
    """Reject publication when the exact document or Aero geometry changed."""

    if active_document is not None:
        if not callable(active_document):
            raise TypeError("active_document must be callable")
        if active_document() is not document:
            raise AeroAnalysisRuntimeError(
                "The exact Aero document is no longer active.",
                error_code="AERO_DOCUMENT_CHANGED",
            )
    current_cfg = AeroConfig.resolve_geometry(document)
    current_revision = AeroPreview.geometry_revision(document, current_cfg)
    if current_revision != str(expected_revision or ""):
        raise AeroAnalysisRuntimeError(
            "The Aero geometry changed while analysis was running; stale results were not published.",
            error_code="AERO_ANALYSIS_STALE",
            current_revision=current_revision,
        )


def publish_document_result(
    document: Any,
    completed: AeroDetachedAnalysis.CompletedAeroAnalysis,
) -> dict[str, Any]:
    """Publish a completed detached solve using the synchronous Aero result contract."""

    if not isinstance(completed, AeroDetachedAnalysis.CompletedAeroAnalysis):
        raise TypeError("completed must be CompletedAeroAnalysis")
    cfg = completed.prepared.config()
    payload = completed.payload()

    # Persistence belongs exclusively to the guarded document-thread commit.
    VibeCADAero._ensure_aeroconfig(document, cfg)
    changes: list[dict[str, Any]] = []
    payload["changes"] = changes
    payload["RepairPasses"] = 0
    payload["Corrections"] = []
    payload["user_message"] = AeroRepair.format_user_message(changes, payload, 0)
    mass = AeroMass.measure_document(document, cfg)
    payload["mass"] = mass
    payload["flight_card"] = AeroFlightCard.build_card(cfg, payload, mass)
    payload.update(AeroStamp.analysis_stamp(payload.get("source")))
    AeroResults.write_report(document, payload)
    return {
        "ok": True,
        **payload,
        "changes": changes,
        "user_message": payload["user_message"],
        "jsbsim_path": None,
        "jsbsim_boot_error": "",
    }


def submit_aero_analysis(
    document: Any,
    operation: str,
    *,
    background_manager: Any,
    document_thread_dispatch: Callable[[Callable[[], Any]], Any],
    active_document: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Schedule one detached Aero solve and return the standard ``native.job`` envelope."""

    if background_manager is None:
        raise AeroAnalysisRuntimeError(
            "Background Aero analysis is unavailable in this session.",
            error_code="AERO_BACKGROUND_UNAVAILABLE",
        )
    if not callable(document_thread_dispatch):
        raise TypeError("document_thread_dispatch must be callable")

    uid = document_uid(document)
    prepared, expected_revision = prepare_document_input(document, operation)

    def prepare(cancelled: Any, progress: Any) -> Any:
        return run_detached(prepared, cancelled=cancelled, progress=progress)

    def validate_before_commit() -> None:
        validate_document_input(
            document,
            expected_revision,
            active_document=active_document,
        )

    try:
        snapshot = background_manager.submit(
            document_uid=uid,
            capability_name=f"aero.{prepared.operation}",
            prepare=prepare,
            validate_before_commit=validate_before_commit,
            commit=lambda completed: publish_document_result(document, completed),
            dispatch_to_document_thread=document_thread_dispatch,
            finalize_message="Publishing verified Aero results",
        )
    except NativeBackgroundError as exc:
        raise AeroAnalysisRuntimeError(
            str(exc),
            error_code="AERO_ANALYSIS_QUEUE_FAILED",
        ) from exc

    return {
        "job": {
            "job_id": str(snapshot.job_id),
            "capability": str(snapshot.capability_name),
            "phase": str(snapshot.phase),
            "progress_percent": int(snapshot.progress_percent),
            "progress_message": str(snapshot.progress_message),
            "terminal": bool(snapshot.terminal),
        },
        "next": {
            "tool": "native.job",
            "operation": "status",
            "job_id": snapshot.job_id,
        },
    }


__all__ = (
    "AeroAnalysisRuntimeError",
    "prepare_document_input",
    "publish_document_result",
    "run_detached",
    "submit_aero_analysis",
    "validate_document_input",
)
