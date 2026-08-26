# SPDX-License-Identifier: LGPL-2.1-or-later
"""Versioned solver qualification evidence contracts for VibeCADAero.

A result being numerically successful does not automatically qualify a solver
for a regime. Qualification is explicit evidence tied to solver build, model,
benchmark source, settings and a bounded applicability envelope.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping


@dataclass(frozen=True)
class QualificationEnvelope:
    reynolds_min: float | None = None
    reynolds_max: float | None = None
    mach_min: float | None = None
    mach_max: float | None = None
    alpha_min_deg: float | None = None
    alpha_max_deg: float | None = None
    geometry_classes: tuple[str, ...] = ()

    def contains(self, *, reynolds: float | None, mach: float | None, alpha_deg: float | None, geometry_class: str | None = None) -> bool:
        for value, low, high in (
            (reynolds, self.reynolds_min, self.reynolds_max),
            (mach, self.mach_min, self.mach_max),
            (alpha_deg, self.alpha_min_deg, self.alpha_max_deg),
        ):
            if value is None:
                if low is not None or high is not None:
                    return False
                continue
            if low is not None and value < low:
                return False
            if high is not None and value > high:
                return False
        if self.geometry_classes and str(geometry_class or "") not in self.geometry_classes:
            return False
        return True


@dataclass(frozen=True)
class BenchmarkResult:
    observable: str
    reference_value: float
    computed_value: float
    tolerance_abs: float | None = None
    tolerance_rel: float | None = None

    @property
    def passed(self) -> bool:
        error = abs(self.computed_value - self.reference_value)
        checks: list[bool] = []
        if self.tolerance_abs is not None:
            checks.append(error <= self.tolerance_abs)
        if self.tolerance_rel is not None:
            denom = max(abs(self.reference_value), 1e-15)
            checks.append(error / denom <= self.tolerance_rel)
        return bool(checks) and all(checks)


@dataclass(frozen=True)
class SolverQualification:
    qualification_id: str
    solver_backend: str
    solver_version: str
    model: str
    benchmark_name: str
    benchmark_source: str
    geometry_sha256: str
    settings_sha256: str
    envelope: QualificationEnvelope
    results: tuple[BenchmarkResult, ...]
    metadata: Mapping[str, Any] = field(default_factory=dict)

    @property
    def qualified(self) -> bool:
        return bool(self.results) and all(item.passed for item in self.results)


def qualification_applies(qualification: SolverQualification, *, solver_backend: str, solver_version: str, model: str, reynolds: float | None, mach: float | None, alpha_deg: float | None, geometry_class: str | None = None) -> bool:
    """True only when the exact qualified build/model covers the requested case."""
    return (
        qualification.qualified
        and qualification.solver_backend == str(solver_backend)
        and qualification.solver_version == str(solver_version)
        and qualification.model == str(model)
        and qualification.envelope.contains(
            reynolds=reynolds, mach=mach, alpha_deg=alpha_deg, geometry_class=geometry_class
        )
    )
