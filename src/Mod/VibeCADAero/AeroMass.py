# SPDX-License-Identifier: LGPL-2.1-or-later

"""CAD-derived mass and CG. Declared AUW is never silently treated as weighed."""

from __future__ import annotations

from typing import Any

import AeroStamp

# kg/m^3. These are design defaults, not lab measurements.
ASSUMED_DENSITIES = {
    "foam_eps": 20.0,
    "pla": 1240.0,
    "carbon_laminate": 1600.0,
    "default_airframe": 80.0,
}

_MASS_PARTS = (
    "lower_wing",
    "upper_wing",
    "boom",
    "h_tail",
    "avionics_pod",
    "camera_bay",
    "battery",
    "prop_left",
    "prop_right",
)


def measure_document(
    doc: Any,
    cfg: dict[str, Any],
    *,
    density_key: str = "default_airframe",
) -> dict[str, Any]:
    """Return declared vs CAD-integrated mass/CG with an honesty stamp."""

    declared_g = float(cfg.get("auw_g") or 0.0)
    declared_kg = declared_g / 1000.0
    density = float(cfg.get("airframe_density_kg_m3") or ASSUMED_DENSITIES[density_key])
    parts: list[dict[str, Any]] = []
    volume_m3 = 0.0
    moment = [0.0, 0.0, 0.0]
    for name in _MASS_PARTS:
        obj = _named(doc, name)
        if obj is None:
            continue
        volume_mm3 = _volume_mm3(obj)
        if volume_mm3 is None or volume_mm3 <= 0.0:
            continue
        vol_m3 = volume_mm3 * 1.0e-9
        mass_kg = vol_m3 * density
        centroid = _centroid_m(obj)
        volume_m3 += vol_m3
        for index, axis in enumerate(centroid):
            moment[index] += mass_kg * axis
        parts.append(
            {
                "name": name,
                "volume_mm3": volume_mm3,
                "mass_g": mass_kg * 1000.0,
                "centroid_m": centroid,
            }
        )

    cad_kg = sum(item["mass_g"] for item in parts) / 1000.0
    cad_available = bool(parts)
    cg_m = None
    if cad_available and cad_kg > 0.0:
        cg_m = [value / cad_kg for value in moment]
    delta_g = None
    if cad_available:
        delta_g = (cad_kg * 1000.0) - declared_g

    if cad_available:
        stamp = AeroStamp.stamp(
            state=AeroStamp.STATE_UNQUALIFIED,
            ceiling=AeroStamp.CEILING_MASS_FROM_CAD,
            method=f"volume*assumed_density:{density_key}",
            extra={"density_kg_m3": density, "density_assumed": True},
        )
    else:
        stamp = AeroStamp.stamp(
            state=AeroStamp.STATE_WAITING,
            ceiling=AeroStamp.CEILING_MASS_DECLARED,
            method="AeroConfig.auw_g",
            extra={"density_kg_m3": density, "density_assumed": True},
        )

    return {
        "declared_auw_g": declared_g,
        "declared_mass_kg": declared_kg,
        "cad_mass_kg": cad_kg if cad_available else None,
        "cad_mass_g": cad_kg * 1000.0 if cad_available else None,
        "delta_g": delta_g,
        "volume_m3": volume_m3 if cad_available else None,
        "cg_m": cg_m,
        "parts": parts,
        "used_mass_kg": cad_kg if cad_available else declared_kg,
        "used_mass_source": "cad_volume" if cad_available else "declared_auw",
        **stamp,
    }


def _named(doc: Any, name: str) -> Any | None:
    getter = getattr(doc, "getObject", None)
    if callable(getter):
        obj = getter(name)
        if obj is not None:
            return obj
    for obj in getattr(doc, "Objects", []) or []:
        if str(getattr(obj, "Name", "") or "") == name:
            return obj
    return None


def _volume_mm3(obj: Any) -> float | None:
    shape = getattr(obj, "Shape", None)
    volume = getattr(shape, "Volume", None) if shape is not None else None
    if volume is None:
        return None
    try:
        value = float(volume)
    except (TypeError, ValueError):
        return None
    return value if value > 0.0 else None


def _centroid_m(obj: Any) -> list[float]:
    shape = getattr(obj, "Shape", None)
    center = getattr(shape, "CenterOfMass", None) if shape is not None else None
    if center is not None and all(hasattr(center, axis) for axis in ("x", "y", "z")):
        return [float(center.x) / 1000.0, float(center.y) / 1000.0, float(center.z) / 1000.0]
    bbox = getattr(shape, "BoundBox", None) if shape is not None else None
    if bbox is None:
        return [0.0, 0.0, 0.0]
    return [
        0.0005 * (float(bbox.XMin) + float(bbox.XMax)),
        0.0005 * (float(bbox.YMin) + float(bbox.YMax)),
        0.0005 * (float(bbox.ZMin) + float(bbox.ZMax)),
    ]
