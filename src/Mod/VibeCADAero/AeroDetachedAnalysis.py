# SPDX-License-Identifier: LGPL-2.1-or-later

"""Serializable, document-free compute seam for VibeCAD Aero analysis.

This module deliberately owns only the physics input/output boundary.  It does
not read or mutate a FreeCAD document, publish AeroReport, apply repairs, stamp
qualification, schedule jobs, or choose where computation runs.  Those remain
with the Aero domain adapter and the host Analysis Runtime respectively.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
import json
from typing import Any


class AeroDetachedContractError(ValueError):
    """A detached Aero input or output is not portable JSON state."""


class AeroDetachedCancelled(RuntimeError):
    """A detached Aero computation was cancelled outside the solver call."""


_OPERATION_FLAGS = {
    "analyze": (True, True),
    "section": (True, False),
    "vlm": (False, True),
}


def _canonical_object(value: Any, field: str) -> str:
    if not isinstance(value, Mapping):
        raise AeroDetachedContractError(f"{field} must be a JSON object.")
    try:
        return json.dumps(
            dict(value),
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise AeroDetachedContractError(
            f"{field} must contain only portable JSON values."
        ) from exc


def _canonical_coordinates(value: Any) -> str:
    try:
        coordinates = [
            [float(point[0]), float(point[1])]
            for point in value
        ]
    except (TypeError, ValueError, IndexError) as exc:
        raise AeroDetachedContractError(
            "coordinates must contain numeric [x, y] points."
        ) from exc
    if len(coordinates) < 2:
        raise AeroDetachedContractError(
            "coordinates must contain at least two airfoil points."
        )
    try:
        return json.dumps(
            coordinates,
            ensure_ascii=True,
            separators=(",", ":"),
            allow_nan=False,
        )
    except ValueError as exc:
        raise AeroDetachedContractError(
            "coordinates must contain only finite numeric values."
        ) from exc


@dataclass(frozen=True, slots=True)
class PreparedAeroAnalysis:
    """Immutable portable identity of one Aero physics computation."""

    operation: str
    config_json: str
    coordinates_json: str
    airfoil_source: str
    run_section_solve: bool
    run_vlm_solve: bool

    @classmethod
    def create(
        cls,
        *,
        operation: str,
        config: Mapping[str, Any],
        coordinates: Any,
        airfoil_source: str,
    ) -> "PreparedAeroAnalysis":
        clean_operation = str(operation or "").strip().lower()
        flags = _OPERATION_FLAGS.get(clean_operation)
        if flags is None:
            raise AeroDetachedContractError(
                "operation must be analyze, section, or vlm."
            )
        source = str(airfoil_source or "").strip()
        if not source:
            raise AeroDetachedContractError("airfoil_source must be non-empty.")
        return cls(
            operation=clean_operation,
            config_json=_canonical_object(config, "config"),
            coordinates_json=_canonical_coordinates(coordinates),
            airfoil_source=source,
            run_section_solve=flags[0],
            run_vlm_solve=flags[1],
        )

    def config(self) -> dict[str, Any]:
        value = json.loads(self.config_json)
        if not isinstance(value, dict):  # defensive against corrupted construction
            raise AeroDetachedContractError("config_json no longer contains an object.")
        return value

    def coordinates(self) -> list[list[float]]:
        value = json.loads(self.coordinates_json)
        if not isinstance(value, list):  # defensive against corrupted construction
            raise AeroDetachedContractError(
                "coordinates_json no longer contains a point array."
            )
        return [[float(point[0]), float(point[1])] for point in value]


@dataclass(frozen=True, slots=True)
class CompletedAeroAnalysis:
    """Portable solver output awaiting Aero-owned document publication."""

    prepared: PreparedAeroAnalysis
    payload_json: str

    def payload(self) -> dict[str, Any]:
        value = json.loads(self.payload_json)
        if not isinstance(value, dict):  # defensive against corrupted construction
            raise AeroDetachedContractError("payload_json no longer contains an object.")
        return value


Solver = Callable[..., Mapping[str, Any]]
CancellationCheck = Callable[[], bool]
ProgressReporter = Callable[[int, str], None]


def execute(
    prepared: PreparedAeroAnalysis,
    *,
    solver: Solver | None = None,
    cancelled: CancellationCheck | None = None,
    progress: ProgressReporter | None = None,
) -> CompletedAeroAnalysis:
    """Execute only the document-free Aero solver portion of an analysis.

    Cancellation is checked immediately before and after the current solver
    call.  The solver itself remains Aero-owned; a later provider adapter may
    supply finer-grained cancellation without changing this portable contract.
    """

    if not isinstance(prepared, PreparedAeroAnalysis):
        raise TypeError("prepared must be PreparedAeroAnalysis")
    if cancelled is not None and not callable(cancelled):
        raise TypeError("cancelled must be callable")
    if progress is not None and not callable(progress):
        raise TypeError("progress must be callable")
    if cancelled is not None and cancelled():
        raise AeroDetachedCancelled()
    if progress is not None:
        progress(10, "Aero inputs detached")

    if solver is None:
        import AeroSolvers

        solver = AeroSolvers.analyze
    result = solver(
        prepared.config(),
        coords=prepared.coordinates(),
        run_section_solve=prepared.run_section_solve,
        run_vlm_solve=prepared.run_vlm_solve,
    )
    if not isinstance(result, Mapping):
        raise AeroDetachedContractError("Aero solver output must be a JSON object.")
    if cancelled is not None and cancelled():
        raise AeroDetachedCancelled()

    payload = dict(result)
    payload["airfoil_source"] = prepared.airfoil_source
    encoded = _canonical_object(payload, "solver output")
    if progress is not None:
        progress(85, "Aero result artifacts ready")
    return CompletedAeroAnalysis(prepared=prepared, payload_json=encoded)


__all__ = (
    "AeroDetachedCancelled",
    "AeroDetachedContractError",
    "CompletedAeroAnalysis",
    "PreparedAeroAnalysis",
    "execute",
)
