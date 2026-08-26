# SPDX-License-Identifier: LGPL-2.1-or-later
"""Geometry-readiness vocabulary separate from artifact provenance.

An exact B-rep can still be unsuitable for CFD.  Conversely a derived mesh can
be a valid solver input.  This module prevents `exact` from being misread as
`watertight`, `mesh-ready`, `manufacturable`, or `airworthy`.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum
from typing import Iterable


class GeometryReadiness(IntEnum):
    UNKNOWN = 0
    BREP_ACCEPTED = 10
    SURFACE_CLOSED = 20
    SURFACE_WATERTIGHT = 30
    FLUID_DOMAIN_READY = 40
    MESH_READY = 50
    SOLVER_INPUT_FROZEN = 60


@dataclass(frozen=True)
class ReadinessEvidence:
    state: GeometryReadiness
    checks: tuple[str, ...] = ()
    failures: tuple[str, ...] = ()

    @property
    def ready(self) -> bool:
        return not self.failures

    def require(self, minimum: GeometryReadiness) -> None:
        if self.state < minimum or self.failures:
            detail = ", ".join(self.failures) or self.state.name.lower()
            raise ValueError(f"geometry is not {minimum.name.lower()}: {detail}")


def assessed(state: GeometryReadiness, *, checks: Iterable[str] = (), failures: Iterable[str] = ()) -> ReadinessEvidence:
    return ReadinessEvidence(state, tuple(checks), tuple(failures))
