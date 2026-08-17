# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded AeroConfig / AeroReport facts for assistant turn-start context."""

from __future__ import annotations

from typing import Any

_RESULT_FIELDS = (
    "CL",
    "CD",
    "CM",
    "CLalpha",
    "Cmalpha",
    "Re",
    "V_loaf",
    "P_hover",
    "P_cruise",
)
_GEOMETRY_FIELDS = (
    "span_mm",
    "chord_mm",
    "gap_c",
    "stagger_c",
    "decalage_deg",
    "auw_g",
    "alpha_deg",
    "n_props",
    "prop_diameter_mm",
    "thrust_to_weight",
)
_MAX_PATH = 240
_MAX_TEXT = 80


def document_aero_summary(doc: Any | None) -> dict[str, Any]:
    """Return a small, deterministic aero object for the active document.

    When an ``AeroReport`` exists the coefficients are included. When only
    ``AeroConfig`` is present the assistant still sees the intended aircraft.
    Solver traces are never copied.
    """

    if doc is None:
        return {"available": False}

    report = _named(doc, "AeroReport")
    config = _named(doc, "AeroConfig")
    solved = report is not None and getattr(report, "CL", None) is not None
    geometry_source = _first_text(
        config,
        ("geometry_source",),
        report,
        ("GeometrySource", "geometry_source"),
        default="AeroConfig" if config is not None else "",
    )
    summary: dict[str, Any] = {
        "available": bool(solved),
        "vehicle_type": _vehicle_type(config, report),
        "airfoil": _airfoil(config, report),
        "geometry": _geometry(config if config is not None else report),
        "geometry_source": geometry_source,
        "jsbsim_path": _jsbsim_path(doc, report),
    }
    if not solved:
        return summary

    for key in _RESULT_FIELDS:
        summary[key] = _as_float(getattr(report, key, None))
    summary["PitchUnstable"] = bool(getattr(report, "PitchUnstable", False))
    summary["source"] = _clip(getattr(report, "Source", "") or "", _MAX_TEXT)
    boot = _clip(
        getattr(report, "JSBSimBootError", None)
        or getattr(report, "jsbsim_boot_error", None)
        or "",
        _MAX_TEXT,
    )
    if boot:
        summary["jsbsim_boot"] = boot
    return summary


def _named(doc: Any, name: str) -> Any | None:
    getter = getattr(doc, "getObject", None)
    if callable(getter):
        obj = getter(name)
        if obj is not None:
            return obj
    for obj in getattr(doc, "Objects", []) or []:
        if str(getattr(obj, "Name", "") or "") == name:
            return obj
        if str(getattr(obj, "Label", "") or "") == name:
            return obj
    return None


def _geometry(obj: Any | None) -> dict[str, float]:
    if obj is None:
        return {}
    geometry: dict[str, float] = {}
    for key in _GEOMETRY_FIELDS:
        value = _as_float(getattr(obj, key, None))
        if value is not None:
            geometry[key] = value
    return geometry


def _airfoil(config: Any | None, report: Any | None) -> str:
    for obj, names in (
        (config, ("airfoil", "Airfoil")),
        (report, ("Airfoil", "airfoil")),
    ):
        text = _first_text(obj, names, default="")
        if text:
            return text
    return "e63"


def _vehicle_type(config: Any | None, report: Any | None) -> str:
    raw = _first_text(
        config,
        ("vehicle_type",),
        report,
        ("vehicle_type", "VehicleType"),
        default="tailsitter",
    )
    normalized = raw.strip().lower().replace(" ", "_")
    aliases = {
        "airplane": "airplane",
        "plane": "airplane",
        "fixed_wing": "airplane",
        "multirotor": "multirotor",
        "multirotor_drone": "multirotor",
        "drone": "multirotor",
        "quad": "multirotor",
        "tailsitter": "tailsitter",
        "tailsitter_vtol": "tailsitter",
        "voider": "tailsitter",
        "vtol": "tailsitter",
    }
    return aliases.get(normalized, "tailsitter")


def _jsbsim_path(doc: Any, report: Any | None) -> str:
    for source, name in (
        (report, "JSBSimPlantPath"),
        (report, "jsbsim_path"),
        (doc, "JSBSimPlantPath"),
    ):
        if source is None:
            continue
        text = _clip(getattr(source, name, "") or "", _MAX_PATH)
        if text:
            return text
    return ""


def _first_text(
    first: Any | None,
    first_names: tuple[str, ...],
    second: Any | None = None,
    second_names: tuple[str, ...] = (),
    *,
    default: str = "",
) -> str:
    for obj, names in ((first, first_names), (second, second_names)):
        if obj is None:
            continue
        for name in names:
            text = _clip(getattr(obj, name, None) or "", _MAX_TEXT)
            if text:
                return text
    return default


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _clip(value: Any, limit: int) -> str:
    text = str(value).strip()
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."
