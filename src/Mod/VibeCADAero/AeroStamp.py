# SPDX-License-Identifier: LGPL-2.1-or-later

"""Evidence stamps the Aero wrapper attaches so the model cannot invent proof."""

from __future__ import annotations

from typing import Any

STATE_PASS = "pass"
STATE_WAITING = "evidence_waiting"
STATE_UNAVAILABLE = "capability_unavailable"
STATE_UNQUALIFIED = "model_unqualified"
STATE_FAILED = "failed"
STATE_REJECTED = "rejected"

CEILING_GEOMETRY_APPLIED = "geometry_applied"
CEILING_NOT_AIRWORTHY = "not_airworthy"
CEILING_NOT_SOLVED = "not_solved"
CEILING_MASS_DECLARED = "mass_declared"
CEILING_MASS_FROM_CAD = "mass_from_cad"

CLAIM_NOT_AIRWORTHY = (
    "This is a low-order aero/mass model (NeuralFoil, 8x6 VLM or AeroBuildup, "
    "momentum-theory hover). It is not flight test, CFD, or airworthiness."
)


def stamp(
    *,
    state: str,
    ceiling: str,
    method: str,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload = {
        "evidence_state": state,
        "claim_ceiling": ceiling,
        "method": method,
        "not_airworthy": True,
        "claim": CLAIM_NOT_AIRWORTHY,
    }
    if extra:
        payload.update(extra)
    return payload


def analysis_stamp(source: str | None) -> dict[str, Any]:
    return stamp(
        state=STATE_UNQUALIFIED,
        ceiling=CEILING_NOT_AIRWORTHY,
        method=str(source or "unknown"),
    )
