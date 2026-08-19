# SPDX-License-Identifier: LGPL-2.1-or-later

"""Deterministic flight card. Inference reads this; it does not invent the numbers."""

from __future__ import annotations

import math
from typing import Any

import AeroMass
import AeroStamp
import AeroSolvers

G = 9.80665


def build_card(
    cfg: dict[str, Any],
    results: dict[str, Any] | None,
    mass: dict[str, Any],
) -> dict[str, Any]:
    mass_kg = float(mass.get("used_mass_kg") or cfg.get("mass_kg") or 0.0)
    area = float(cfg.get("reference_area_m2") or 0.0)
    span = float(cfg.get("span_m") or 0.0)
    chord = float(cfg.get("chord_m") or 0.0)
    n_props = int(cfg.get("n_props") or 2)
    prop_d = float(cfg.get("prop_diameter_m") or 0.178)
    t_w = float(cfg.get("thrust_to_weight") or 1.9)
    disk = n_props * math.pi * (prop_d / 2.0) ** 2
    weight_n = mass_kg * G
    thrust_n = t_w * weight_n
    wing_loading = (weight_n / area) if area > 0.0 else None
    disk_loading = (thrust_n / disk) if disk > 0.0 else None
    hover_margin = t_w - 1.0

    tail_s = float(cfg.get("tail_span_m") or 0.0) * float(cfg.get("tail_chord_m") or 0.0)
    tail_arm = float(cfg.get("boom_length_m") or 0.0)
    tail_volume = None
    has_tail = bool(cfg.get("has_h_tail"))
    if has_tail and area > 0.0 and chord > 0.0:
        tail_volume = (tail_s * tail_arm) / (area * chord)

    cl = _num((results or {}).get("CL"))
    cd = _num((results or {}).get("CD"))
    cla = _num((results or {}).get("CLalpha"))
    cma = _num((results or {}).get("Cmalpha"))
    loaf = _num((results or {}).get("V_loaf"))
    hover_p = _num((results or {}).get("P_hover"))
    cruise_p = _num((results or {}).get("P_cruise"))
    source = str((results or {}).get("source") or "")
    hover_source = "momentum-theory"
    hover = (results or {}).get("hover") or {}
    if isinstance(hover, dict) and hover.get("source"):
        hover_source = str(hover["source"])

    pitch_stable = None
    if cma is not None:
        pitch_stable = not AeroSolvers.pitch_unstable(cma)
    static_margin_c = None
    if cla not in (None, 0.0) and cma is not None:
        static_margin_c = -cma / cla

    battery_wh = _num(cfg.get("battery_wh"))
    endurance = None
    endurance_state = AeroStamp.STATE_WAITING
    if battery_wh is not None and hover_p not in (None, 0.0):
        endurance = (battery_wh * 3600.0 * 0.85) / hover_p / 60.0
        endurance_state = AeroStamp.STATE_UNQUALIFIED

    checks = {
        "hover_tw_above_one": hover_margin > 0.0,
        "pitch_stable_cmalpha": pitch_stable,
        "has_solve": results is not None and cl is not None,
        "has_cad_mass": mass.get("used_mass_source") == "cad_volume",
        "has_battery": battery_wh is not None,
    }

    stamp = AeroStamp.stamp(
        state=AeroStamp.STATE_UNQUALIFIED if checks["has_solve"] else AeroStamp.STATE_WAITING,
        ceiling=AeroStamp.CEILING_NOT_AIRWORTHY,
        method=source or "no_solve",
        extra={
            "hover_method": hover_source,
            "mass_source": mass.get("used_mass_source"),
            "endurance_state": endurance_state,
        },
    )

    return {
        "vehicle_type": cfg.get("vehicle_type"),
        "mass_kg": mass_kg,
        "mass": mass,
        "reference_area_m2": area,
        "span_m": span,
        "chord_m": chord,
        "wing_loading_n_m2": wing_loading,
        "disk_loading_n_m2": disk_loading,
        "thrust_to_weight": t_w,
        "hover_margin_tw": hover_margin,
        "has_h_tail": has_tail,
        "tail_volume_coeff": tail_volume,
        "static_margin_c": static_margin_c,
        "CL": cl,
        "CD": cd,
        "CLalpha": cla,
        "Cmalpha": cma,
        "V_loaf_mps": loaf,
        "P_hover_w": hover_p,
        "P_cruise_w": cruise_p,
        "source": source,
        "hover_source": hover_source,
        "battery_wh": battery_wh,
        "endurance_hover_min": endurance,
        "pitch_stable": pitch_stable,
        "checks": checks,
        "geometry_source": cfg.get("geometry_source"),
        **stamp,
    }


def _num(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
