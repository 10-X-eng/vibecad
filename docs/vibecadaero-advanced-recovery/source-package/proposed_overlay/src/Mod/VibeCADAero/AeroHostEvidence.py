# SPDX-License-Identifier: LGPL-2.1-or-later
"""Shared host-aligned evidence/artifact semantics for VibeCADAero reference code.

Pass 03 adopts VibeCAD's current evidence distinctions instead of inventing an
Aero-only vocabulary:
- preparing an analysis is not solving it;
- a finished numerical solve is not automatically a qualified model;
- exact/derived/presentation describe artifact provenance, not aerodynamic truth;
- airworthiness is never inferred from any of these states.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


class ArtifactClass(str, Enum):
    EXACT = "exact"
    DERIVED = "derived"
    PRESENTATION = "presentation"


class EvidenceState(str, Enum):
    WAITING = "evidence_waiting"
    UNAVAILABLE = "capability_unavailable"
    FAILED = "failed"
    MODEL_UNQUALIFIED = "model_unqualified"
    MODEL_QUALIFIED = "model_qualified"
    MEASURED = "measured"


@dataclass(frozen=True)
class EvidenceStamp:
    evidence_state: EvidenceState
    claim_ceiling: str
    method: str
    solver_finished: bool = False
    model_qualified: bool = False
    not_airworthy: bool = True
    metadata: Mapping[str, Any] | None = None

    def as_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "evidence_state": self.evidence_state.value,
            "claim_ceiling": self.claim_ceiling,
            "method": self.method,
            "solver_finished": self.solver_finished,
            "model_qualified": self.model_qualified,
            "not_airworthy": self.not_airworthy,
        }
        if self.metadata:
            payload.update(dict(self.metadata))
        return payload


def prepared_case(method: str) -> EvidenceStamp:
    return EvidenceStamp(EvidenceState.WAITING, "not_solved", str(method))


def solver_finished(method: str, *, qualified: bool = False, metadata: Mapping[str, Any] | None = None) -> EvidenceStamp:
    """Stamp a completed solver run without equating completion with qualification."""
    if qualified:
        return EvidenceStamp(
            EvidenceState.MODEL_QUALIFIED,
            "not_airworthy",
            str(method),
            solver_finished=True,
            model_qualified=True,
            metadata=metadata,
        )
    return EvidenceStamp(
        EvidenceState.MODEL_UNQUALIFIED,
        "model_unqualified",
        str(method),
        solver_finished=True,
        model_qualified=False,
        metadata=metadata,
    )


def failed(method: str, error: str) -> EvidenceStamp:
    return EvidenceStamp(EvidenceState.FAILED, "not_solved", str(method), metadata={"error": str(error)})


def unavailable(method: str, reason: str) -> EvidenceStamp:
    return EvidenceStamp(EvidenceState.UNAVAILABLE, "not_solved", str(method), metadata={"reason": str(reason)})


def artifact_metadata(kind: str, *, source_sha256: str | None = None) -> dict[str, Any]:
    """Map common Aero artifacts onto the current host provenance vocabulary."""
    normalized = str(kind).strip().lower()
    exact = {"brep", "step", "iges", "native_brep"}
    derived = {"stl", "obj", "surface_mesh", "volume_mesh", "voxel_grid", "cfd_field", "solver_result", "vtk", "vtu"}
    presentation = {"screenshot", "render", "preview_image", "animation_frame"}
    if normalized in exact:
        cls = ArtifactClass.EXACT
    elif normalized in derived:
        cls = ArtifactClass.DERIVED
    elif normalized in presentation:
        cls = ArtifactClass.PRESENTATION
    else:
        raise ValueError(f"unknown Aero artifact kind: {kind}")
    payload = {
        "artifact_class": cls.value,
        "exact": cls is ArtifactClass.EXACT,
        "derived": cls is ArtifactClass.DERIVED,
        "presentation_only": cls is ArtifactClass.PRESENTATION,
    }
    if source_sha256:
        payload["source_sha256"] = str(source_sha256)
        if cls is ArtifactClass.DERIVED:
            payload["derived_from_exact"] = True
    return payload
