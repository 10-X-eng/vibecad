# SPDX-License-Identifier: LGPL-2.1-or-later
"""Deterministic, explainable solver/compute routing contracts.

"Auto" must never mean an opaque preference hidden in a provider adapter.  The
router evaluates already-resolved candidate capabilities and resource estimates.
It does not perform licensing/purpose classification and does not invent quota.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Mapping


@dataclass(frozen=True)
class RouteCandidate:
    solver: str
    compute_provider: str
    qualified: bool
    available: bool
    fidelity_rank: int
    estimated_wall_time_s: float | None = None
    estimated_memory_bytes: int | None = None
    quota_fit: bool | None = None
    reasons: tuple[str, ...] = ()
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class RoutingDecision:
    selected: RouteCandidate | None
    rejected: tuple[tuple[RouteCandidate, tuple[str, ...]], ...]
    rationale: tuple[str, ...]


def choose_route(candidates: Iterable[RouteCandidate]) -> RoutingDecision:
    """Choose the highest-fidelity qualified available candidate deterministically.

    A known ``quota_fit=False`` makes a remote candidate ineligible. Unknown
    quota remains explicit rather than being guessed. Ties prefer lower known
    wall time, then lexical solver/provider order for reproducibility.
    """
    eligible: list[RouteCandidate] = []
    rejected: list[tuple[RouteCandidate, tuple[str, ...]]] = []
    for candidate in candidates:
        why: list[str] = []
        if not candidate.available:
            why.append("capability_unavailable")
        if not candidate.qualified:
            why.append("model_unqualified_for_requested_case")
        if candidate.quota_fit is False:
            why.append("provider_quota_estimate_does_not_fit")
        if why:
            rejected.append((candidate, tuple(why)))
        else:
            eligible.append(candidate)
    if not eligible:
        return RoutingDecision(None, tuple(rejected), ("no_eligible_route",))

    def key(candidate: RouteCandidate) -> tuple[Any, ...]:
        wall = candidate.estimated_wall_time_s
        return (
            -int(candidate.fidelity_rank),
            float("inf") if wall is None else float(wall),
            candidate.solver,
            candidate.compute_provider,
        )

    selected = sorted(eligible, key=key)[0]
    rationale = [
        f"selected={selected.solver}@{selected.compute_provider}",
        f"fidelity_rank={selected.fidelity_rank}",
    ]
    if selected.estimated_wall_time_s is not None:
        rationale.append(f"estimated_wall_time_s={selected.estimated_wall_time_s:g}")
    if selected.quota_fit is None:
        rationale.append("quota_fit=unknown")
    elif selected.quota_fit:
        rationale.append("quota_fit=true")
    rationale.extend(selected.reasons)
    return RoutingDecision(selected, tuple(rejected), tuple(rationale))
