# SPDX-License-Identifier: LGPL-2.1-or-later

"""Resolve aero geometry from AeroConfig, document properties, or inference."""

from __future__ import annotations

from typing import Any

VOIDER_DEFAULTS: dict[str, Any] = {
    "span_mm": 500.0,
    "chord_mm": 90.0,
    "gap_c": 1.4,
    "stagger_c": 1.15,
    "decalage_deg": 2.0,
    "auw_g": 149.6,
    "airfoil": "e63",
    "alpha_deg": 4.0,
    "n_props": 2,
    "prop_diameter_mm": 178.0,
    "figure_of_merit": 0.55,
    "thrust_to_weight": 1.9,
    "cruise_prop_eta": 0.65,
}

_PARAM_KEYS = (
    "span_mm",
    "chord_mm",
    "gap_c",
    "stagger_c",
    "decalage_deg",
    "auw_g",
    "airfoil",
    "alpha_deg",
    "n_props",
    "prop_diameter_mm",
    "figure_of_merit",
    "thrust_to_weight",
    "cruise_prop_eta",
    "boom_length_mm",
)

_NAMED_PARTS = ("lower_wing", "upper_wing", "boom", "h_tail")


def resolve_geometry(doc: Any | None = None) -> dict[str, Any]:
    """Return a fully unit-converted config dict.

    Precedence:
    1. An ``AeroConfig`` document object
    2. Matching properties on the document itself
    3. Bounding boxes of voider-named objects
    4. Locked voider-ultimate defaults
    """

    values = dict(VOIDER_DEFAULTS)
    values["geometry_source"] = "defaults"
    if doc is None:
        return finalize(values)

    aero = _find_named(doc, "AeroConfig")
    if aero is not None and _has_any_param(aero):
        _merge_params(values, aero)
        values["geometry_source"] = "AeroConfig"
        return finalize(values)

    if _has_any_param(doc):
        _merge_params(values, doc)
        values["geometry_source"] = "document"
        return finalize(values)

    inferred = infer_from_named_objects(doc)
    if inferred:
        values.update(inferred)
        values["geometry_source"] = "inferred"
        return finalize(values)

    return finalize(values)


def finalize(values: dict[str, Any]) -> dict[str, Any]:
    cfg = dict(values)
    span_m = float(cfg["span_mm"]) / 1000.0
    chord_m = float(cfg["chord_mm"]) / 1000.0
    cfg["span_m"] = span_m
    cfg["chord_m"] = chord_m
    cfg["reference_area_m2"] = 2.0 * span_m * chord_m
    cfg["gap_m"] = float(cfg["gap_c"]) * chord_m
    cfg["stagger_m"] = float(cfg["stagger_c"]) * chord_m
    cfg["mass_kg"] = float(cfg["auw_g"]) / 1000.0
    cfg["prop_diameter_m"] = float(cfg["prop_diameter_mm"]) / 1000.0
    boom_mm = cfg.get("boom_length_mm")
    cfg["boom_length_m"] = (
        float(boom_mm) / 1000.0 if boom_mm else max(0.25, 3.5 * chord_m)
    )
    cfg["xyz_ref"] = [0.25 * chord_m, 0.0, cfg["gap_m"] / 2.0]
    cfg["airfoil"] = str(cfg.get("airfoil") or "e63")
    return cfg


def infer_from_named_objects(doc: Any) -> dict[str, Any]:
    lower = _find_named(doc, "lower_wing")
    bbox = _bbox(lower)
    if bbox is None:
        return {}

    span_mm, chord_mm = _span_and_chord_mm(bbox)
    inferred: dict[str, Any] = {
        "span_mm": span_mm,
        "chord_mm": chord_mm,
    }

    upper = _find_named(doc, "upper_wing")
    upper_bbox = _bbox(upper)
    if upper_bbox is not None and chord_mm:
        gap_mm = abs(_center(upper_bbox)[2] - _center(bbox)[2])
        stagger_mm = abs(float(bbox.XMin) - float(upper_bbox.XMin))
        inferred["gap_c"] = gap_mm / chord_mm
        inferred["stagger_c"] = stagger_mm / chord_mm

    boom = _find_named(doc, "boom")
    boom_bbox = _bbox(boom)
    if boom_bbox is not None:
        inferred["boom_length_mm"] = float(boom_bbox.XLength)

    return inferred


def _find_named(doc: Any, name: str) -> Any | None:
    getter = getattr(doc, "getObject", None)
    if callable(getter):
        obj = getter(name)
        if obj is not None:
            return obj
    for obj in getattr(doc, "Objects", []) or []:
        label = str(getattr(obj, "Label", "") or "")
        obj_name = str(getattr(obj, "Name", "") or "")
        if name.lower() in {label.lower(), obj_name.lower()}:
            return obj
    return None


def _has_any_param(obj: Any) -> bool:
    for key in _PARAM_KEYS:
        if getattr(obj, key, None) is not None:
            return True
        getter = getattr(obj, "getPropertyByName", None)
        if callable(getter):
            try:
                if getter(key) is not None:
                    return True
            except Exception:
                continue
    return False


def _merge_params(values: dict[str, Any], obj: Any) -> None:
    for key in _PARAM_KEYS:
        raw = getattr(obj, key, None)
        if raw is None:
            getter = getattr(obj, "getPropertyByName", None)
            if callable(getter):
                try:
                    raw = getter(key)
                except Exception:
                    raw = None
        if raw is None:
            continue
        if key == "airfoil":
            values[key] = str(raw)
        else:
            values[key] = float(raw)


def _bbox(obj: Any) -> Any | None:
    if obj is None:
        return None
    shape = getattr(obj, "Shape", None)
    box = getattr(shape, "BoundBox", None) if shape is not None else None
    if box is None:
        box = getattr(obj, "BoundBox", None)
    if box is None:
        return None
    if not all(hasattr(box, attr) for attr in ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax")):
        return None
    return box


def _center(bbox: Any) -> tuple[float, float, float]:
    return (
        0.5 * (float(bbox.XMin) + float(bbox.XMax)),
        0.5 * (float(bbox.YMin) + float(bbox.YMax)),
        0.5 * (float(bbox.ZMin) + float(bbox.ZMax)),
    )


def _span_and_chord_mm(bbox: Any) -> tuple[float, float]:
    x_len = abs(float(getattr(bbox, "XLength", float(bbox.XMax) - float(bbox.XMin))))
    y_len = abs(float(getattr(bbox, "YLength", float(bbox.YMax) - float(bbox.YMin))))
    span_mm = max(x_len, y_len)
    chord_mm = min(x_len, y_len) if min(x_len, y_len) > 1e-9 else x_len
    return span_mm, chord_mm
