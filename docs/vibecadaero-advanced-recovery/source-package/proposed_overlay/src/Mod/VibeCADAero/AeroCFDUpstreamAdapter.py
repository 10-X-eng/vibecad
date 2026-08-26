# SPDX-License-Identifier: LGPL-2.1-or-later
"""Adapter from existing upstream AeroConfig dictionaries into canonical CFD cases."""

from __future__ import annotations

import hashlib
import json
import math
from typing import Any

from AeroCFDContracts import (
    AeroCase,
    ComputeSpec,
    FlowConditions,
    GeometryArtifact,
    ReferenceQuantities,
    SolverSpec,
    Vector3,
)


def _case_id(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return "aero-" + hashlib.sha256(raw).hexdigest()[:20]


def case_from_upstream_config(
    cfg: dict[str, Any],
    geometry: GeometryArtifact,
    *,
    speed_mps: float,
    alpha_deg: float | None = None,
    beta_deg: float = 0.0,
    density_kg_m3: float = 1.225,
    dynamic_viscosity_pa_s: float = 1.81e-5,
    solver_backend: str,
    solver_model: str,
    solver_settings: dict[str, Any] | None = None,
    compute_provider: str = "local",
    compute_settings: dict[str, Any] | None = None,
    moment_reference_body_m: Vector3 = Vector3(),
) -> AeroCase:
    """Build a solver case from the current ``AeroConfig.resolve_geometry`` shape.

    ``geometry`` must already be expressed in the canonical body frame.  This reference
    refuses to guess the transform from raw CAD axes because the live upstream
    has an explicit ``CadAeroFrame`` mapping that must be reused during later
    integration.  This is an intentional safety seam, not missing bookkeeping.
    """

    if speed_mps <= 0.0:
        raise ValueError("speed_mps must be positive")
    alpha = math.radians(float(cfg.get("alpha_deg", 0.0) if alpha_deg is None else alpha_deg))
    beta = math.radians(float(beta_deg))
    # Aircraft velocity relative to the air in canonical body axes: +X forward,
    # +Y right, +Z down.  Positive alpha therefore has positive body-Z velocity.
    velocity = Vector3(
        speed_mps * math.cos(alpha) * math.cos(beta),
        speed_mps * math.sin(beta),
        speed_mps * math.sin(alpha) * math.cos(beta),
    )

    area = float(cfg.get("reference_area_m2") or 0.0)
    chord = float(cfg.get("chord_m") or 0.0)
    span = float(cfg.get("span_m") or 0.0)
    if area <= 0.0 or chord <= 0.0 or span <= 0.0:
        raise ValueError("upstream AeroConfig must provide positive reference area/chord/span")

    identity = {
        "geometry": geometry.artifact.sha256,
        "revision": geometry.geometry_revision,
        "speed_mps": speed_mps,
        "alpha_deg": math.degrees(alpha),
        "beta_deg": beta_deg,
        "solver": solver_backend,
        "model": solver_model,
        "solver_settings": solver_settings or {},
        "compute": compute_provider,
        "compute_settings": compute_settings or {},
    }
    case = AeroCase(
        case_id=_case_id(identity),
        geometry=geometry,
        flow=FlowConditions(
            freestream_body_mps=velocity,
            density_kg_m3=float(density_kg_m3),
            dynamic_viscosity_pa_s=float(dynamic_viscosity_pa_s),
        ),
        references=ReferenceQuantities(
            area_m2=area,
            length_m=chord,
            span_m=span,
            moment_reference_body_m=moment_reference_body_m,
            area_definition=str(cfg.get("reference_area_definition") or "upstream_AeroConfig"),
        ),
        solver=SolverSpec(
            backend=solver_backend,
            model=solver_model,
            settings=dict(solver_settings or {}),
        ),
        compute=ComputeSpec(provider=compute_provider, settings=dict(compute_settings or {})),
        metadata={
            "vehicle_type": cfg.get("vehicle_type"),
            "airfoil": cfg.get("airfoil"),
            "config_source": cfg.get("source"),
            "source_span_m": span,
            "source_chord_m": chord,
        },
    )
    case.validate()
    return case
