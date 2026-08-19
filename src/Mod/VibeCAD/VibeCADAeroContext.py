# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded AeroConfig / AeroReport facts for assistant turn-start context."""

from __future__ import annotations

import json
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
    "boom_length_mm",
    "tail_span_mm",
    "tail_chord_mm",
    "xyz_ref_c",
)
_MAX_PATH = 240
_MAX_TEXT = 80
_MAX_CORRECTION = 160
_MAX_CORRECTIONS = 12


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
        "evidence_state": "model_unqualified" if solved else "evidence_waiting",
        "claim_ceiling": "not_airworthy",
        "not_airworthy": True,
        "vehicle_type": _vehicle_type(config, report),
        "airfoil": _airfoil(config, report),
        "geometry": _geometry(config if config is not None else report),
        "geometry_source": geometry_source,
        "jsbsim_path": _jsbsim_path(doc, report),
        "hover_source": _clip(
            getattr(report, "HoverSource", None) or "momentum-theory" if report is not None else "",
            _MAX_TEXT,
        ),
    }
    assistant = _assistant_json(doc, report)
    if assistant:
        summary["assistant_json"] = assistant
    corrections = _corrections(report, doc)
    if corrections:
        summary["corrections"] = corrections
    if not solved:
        return summary

    for key in _RESULT_FIELDS:
        summary[key] = _as_float(getattr(report, key, None))
    summary["PitchUnstable"] = bool(getattr(report, "PitchUnstable", False))
    summary["RepairPasses"] = int(getattr(report, "RepairPasses", 0) or 0)
    summary["Corrections"] = corrections
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


def _assistant_json(doc: Any, report: Any | None) -> dict[str, Any]:
    raw = _assistant_json_source(getattr(doc, "AeroAssistantJson", None))
    if raw in (None, ""):
        raw = _assistant_json_source(getattr(doc, "AeroAssistantJsonText", None))
    if raw in (None, ""):
        obj = _named(doc, "AeroAssistantJson")
        if obj is not None:
            raw = _assistant_json_source(obj)
    if raw in (None, "") and report is not None:
        raw = _assistant_json_source(getattr(report, "AeroAssistantJson", None))
    parsed = _parse_assistant_json(raw)
    if parsed:
        return parsed
    return {}


def _assistant_json_source(raw: Any) -> Any:
    """Unwrap the named TextDocument used by real FreeCAD documents."""

    if raw is None or isinstance(raw, (str, dict)):
        return raw
    text = getattr(raw, "Text", None)
    if text not in (None, ""):
        return text
    nested = getattr(raw, "AeroAssistantJson", None)
    if nested is not None and nested is not raw:
        return nested
    return raw


def _parse_assistant_json(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        payload = raw
    else:
        text = str(raw or "").strip()
        if not text:
            return {}
        try:
            loaded = json.loads(text)
        except Exception:
            return {}
        if not isinstance(loaded, dict):
            return {}
        payload = loaded
    result: dict[str, Any] = {}
    for key in ("CL", "CD", "CM", "CLalpha", "Cmalpha"):
        value = _as_float(payload.get(key))
        if value is not None:
            result[key] = value
    if "PitchUnstable" in payload:
        result["PitchUnstable"] = bool(payload.get("PitchUnstable"))
    source = _clip(payload.get("source") or "", _MAX_TEXT)
    if source:
        result["source"] = source
    hover_source = _clip(payload.get("hover_source") or "", _MAX_TEXT)
    if hover_source:
        result["hover_source"] = hover_source
    result["claim_ceiling"] = _clip(
        payload.get("claim_ceiling") or "not_airworthy", _MAX_TEXT
    )
    result["evidence_state"] = _clip(
        payload.get("evidence_state") or "model_unqualified", _MAX_TEXT
    )
    result["not_airworthy"] = True
    card = payload.get("flight_card")
    if isinstance(card, dict):
        trimmed: dict[str, Any] = {}
        for key in (
            "mass_kg",
            "wing_loading_n_m2",
            "disk_loading_n_m2",
            "thrust_to_weight",
            "hover_margin_tw",
            "tail_volume_coeff",
            "static_margin_c",
            "endurance_hover_min",
            "pitch_stable",
            "used_mass_source",
        ):
            if key in card:
                trimmed[key] = card[key]
        if card.get("mass") and isinstance(card["mass"], dict):
            trimmed["used_mass_source"] = card["mass"].get("used_mass_source")
        if trimmed:
            result["flight_card"] = trimmed
    corrections = _bounded_corrections(payload.get("corrections"))
    if not corrections:
        corrections = _bounded_corrections(payload.get("Corrections"))
    if not corrections:
        corrections = _bounded_corrections(
            [
                str(change.get("sentence") or "")
                for change in payload.get("changes") or []
                if isinstance(change, dict) and change.get("sentence")
            ]
        )
    if corrections:
        result["corrections"] = corrections
    return result


def _corrections(report: Any | None, doc: Any) -> list[str]:
    if report is not None:
        items = _bounded_corrections(getattr(report, "Corrections", None))
        if items:
            return items
    assistant = _parse_assistant_json(
        _assistant_json_source(getattr(doc, "AeroAssistantJson", None))
    )
    items = list(assistant.get("corrections") or [])
    if items:
        return items
    obj = _named(doc, "AeroAssistantJson")
    if obj is not None:
        assistant = _parse_assistant_json(_assistant_json_source(obj))
        return list(assistant.get("corrections") or [])
    return []


def _bounded_corrections(value: Any) -> list[str]:
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        text = str(value or "").strip()
        if not text:
            return []
        try:
            loaded = json.loads(text)
        except Exception:
            loaded = None
        if isinstance(loaded, list):
            items = [str(item).strip() for item in loaded]
        else:
            items = [line.strip().lstrip("- ") for line in text.splitlines()]
    result: list[str] = []
    for item in items:
        clean = _clip(item, _MAX_CORRECTION)
        if clean:
            result.append(clean)
        if len(result) >= _MAX_CORRECTIONS:
            break
    return result


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
