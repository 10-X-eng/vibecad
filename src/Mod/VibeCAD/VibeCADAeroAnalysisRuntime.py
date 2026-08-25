# SPDX-License-Identifier: LGPL-2.1-or-later

"""Host Analysis Runtime lifecycle helpers for detached VibeCAD Aero solves."""

from __future__ import annotations

from pathlib import Path
import sys
from typing import Any, Callable

_AERO_DIR = Path(__file__).resolve().parent.parent / "VibeCADAero"
if _AERO_DIR.is_dir() and str(_AERO_DIR) not in sys.path:
    sys.path.insert(0, str(_AERO_DIR))

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


__all__ = (
    "AeroAnalysisRuntimeError",
    "prepare_document_input",
    "publish_document_result",
    "run_detached",
    "validate_document_input",
)
