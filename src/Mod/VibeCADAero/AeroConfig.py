# SPDX-License-Identifier: LGPL-2.1-or-later

"""Resolve aero geometry from AeroConfig, document properties, or inference."""

from __future__ import annotations

from typing import Any

VEHICLE_TYPES = ("airplane", "multirotor", "tailsitter")
DEFAULT_VEHICLE_TYPE = "tailsitter"

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
    "vehicle_type": DEFAULT_VEHICLE_TYPE,
    "battery_wh": None,
    "airframe_density_kg_m3": 80.0,
}

_STRING_KEYS = ("airfoil", "vehicle_type")
_WRITE_KEYS = (
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
    "vehicle_type",
    "boom_length_mm",
    "tail_span_mm",
    "tail_chord_mm",
    "xyz_ref_c",
    "battery_wh",
    "airframe_density_kg_m3",
)

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
    "tail_span_mm",
    "tail_chord_mm",
    "xyz_ref_c",
    "cg_x_m",
    "battery_wh",
    "airframe_density_kg_m3",
)

_REPAIR_KEYS = (
    "boom_length_mm",
    "tail_span_mm",
    "tail_chord_mm",
    "xyz_ref_c",
    "cg_x_m",
)

_NAMED_PARTS = ("lower_wing", "upper_wing", "boom", "h_tail")
_NAMED_TAIL_KEYS = ("boom_length_mm", "tail_span_mm", "tail_chord_mm")


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
    values["vehicle_type"] = DEFAULT_VEHICLE_TYPE
    if doc is None:
        return finalize(values)
    if find_named(doc, "h_tail") is not None:
        values["has_h_tail"] = True

    aero = find_named(doc, "AeroConfig")
    if aero is not None and _has_any_param(aero):
        _merge_params(values, aero)
        values["geometry_source"] = "AeroConfig"
        _seed_missing_named_parts(values, doc, aero)
        return _with_layout(finalize(values), doc)

    if _has_any_param(doc):
        _merge_params(values, doc)
        values["geometry_source"] = "document"
        _seed_missing_named_parts(values, doc, doc)
        return _with_layout(finalize(values), doc)

    inferred = infer_from_named_objects(doc)
    if inferred and inference_is_plausible(inferred):
        values.update(inferred)
        values["geometry_source"] = "inferred"
        return _with_layout(finalize(values), doc)
    if inferred:
        values["inference_rejected"] = True
        values["inferred_span_mm"] = inferred.get("span_mm")
        values["inferred_chord_mm"] = inferred.get("chord_mm")

    return _with_layout(finalize(values), doc)


def inference_is_plausible(inferred: dict[str, Any]) -> bool:
    """Reject loft-sized bounding boxes that are far from the locked airframe."""

    default_span = float(VOIDER_DEFAULTS["span_mm"])
    default_chord = float(VOIDER_DEFAULTS["chord_mm"])
    span = inferred.get("span_mm")
    if span is None:
        return False
    span_ratio = float(span) / default_span
    if span_ratio > 2.0 or span_ratio < 0.5:
        return False
    chord = inferred.get("chord_mm")
    if chord is not None:
        chord_ratio = float(chord) / default_chord
        if chord_ratio > 2.0 or chord_ratio < 0.5:
            return False
    return True


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
    if not boom_mm:
        cfg["boom_length_mm"] = cfg["boom_length_m"] * 1000.0
    explicit_tail = values.get("tail_span_mm") or values.get("tail_chord_mm")
    has_h_tail = bool(cfg.get("has_h_tail") or explicit_tail)
    cfg["has_h_tail"] = has_h_tail
    if has_h_tail:
        if not cfg.get("tail_span_mm"):
            cfg["tail_span_mm"] = 0.30 * float(cfg["span_mm"])
        if not cfg.get("tail_chord_mm"):
            cfg["tail_chord_mm"] = 0.60 * float(cfg["chord_mm"])
        cfg["tail_span_m"] = float(cfg["tail_span_mm"]) / 1000.0
        cfg["tail_chord_m"] = float(cfg["tail_chord_mm"]) / 1000.0
    else:
        cfg["tail_span_m"] = 0.0
        cfg["tail_chord_m"] = 0.0
    if cfg.get("xyz_ref_c") is not None:
        xyz_c = float(cfg["xyz_ref_c"])
    elif cfg.get("cg_x_m") is not None and chord_m:
        xyz_c = float(cfg["cg_x_m"]) / chord_m
    else:
        xyz_c = 0.25
    cfg["xyz_ref_c"] = xyz_c
    cfg["xyz_ref"] = [xyz_c * chord_m, 0.0, cfg["gap_m"] / 2.0]
    cfg["cg_x_m"] = cfg["xyz_ref"][0]
    cfg["upper_le_x_m"] = float(cfg.get("upper_le_x_m") if cfg.get("upper_le_x_m") is not None else cfg["stagger_m"])
    cfg["tail_le_x_m"] = float(cfg.get("tail_le_x_m") if cfg.get("tail_le_x_m") is not None else cfg["boom_length_m"])
    cfg["airfoil"] = str(cfg.get("airfoil") or "e63")
    cfg["vehicle_type"] = normalize_vehicle_type(cfg.get("vehicle_type"))
    return cfg


def normalize_vehicle_type(value: Any) -> str:
    raw = str(value or DEFAULT_VEHICLE_TYPE).strip().lower().replace(" ", "_")
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
    return aliases.get(raw, DEFAULT_VEHICLE_TYPE)


def write_config(doc: Any, values: dict[str, Any]) -> Any | None:
    """Create or update ``AeroConfig`` on ``doc`` from explicit field edits."""

    if doc is None:
        return None
    adder = getattr(doc, "addObject", None)
    if not callable(adder):
        return None
    obj = find_named(doc, "AeroConfig")
    if obj is None:
        try:
            obj = adder("App::FeaturePython", "AeroConfig")
        except Exception:
            return None
        if obj is not None and not getattr(obj, "Label", None):
            try:
                obj.Label = "AeroConfig"
            except Exception:
                pass
    payload = dict(VOIDER_DEFAULTS)
    payload.update(values or {})
    payload["airfoil"] = str(payload.get("airfoil") or "e63")
    payload["vehicle_type"] = normalize_vehicle_type(payload.get("vehicle_type"))
    for key in _WRITE_KEYS:
        _set_param(obj, key, payload.get(key))
    if values:
        for key in _REPAIR_KEYS:
            if key in values:
                _set_param(obj, key, values[key])
    recompute = getattr(doc, "recompute", None)
    if callable(recompute):
        try:
            recompute()
        except Exception:
            pass
    return obj


def infer_from_named_objects(doc: Any) -> dict[str, Any]:
    lower = find_named(doc, "lower_wing")
    bbox = _bbox(lower)
    if bbox is None:
        return {}

    span_mm, chord_mm = _span_and_chord_mm(bbox)
    inferred: dict[str, Any] = {
        "span_mm": span_mm,
        "chord_mm": chord_mm,
    }

    upper = find_named(doc, "upper_wing")
    upper_bbox = _bbox(upper)
    if upper_bbox is not None and chord_mm:
        gap_mm = abs(_center(upper_bbox)[2] - _center(bbox)[2])
        inferred["gap_c"] = gap_mm / chord_mm
        frame = document_aero_frame(doc)
        inferred["stagger_c"] = (
            frame.asb_x_m(frame.wing_le_cad_mm(upper_bbox)) * 1000.0 / chord_mm
        )

    inferred.update(named_part_geometry(doc))
    return inferred


def _with_layout(cfg: dict[str, Any], doc: Any | None) -> dict[str, Any]:
    cfg.update(airplane_layout(cfg, doc))
    return cfg


class CadAeroFrame:
    """Map CAD millimetres onto AeroSandbox metres.

    AeroSandbox +X is aft. CAD +X is whatever the document used; the live
    voider has +X toward the nose (camera) and −X toward the tail. The
    frame is inferred from named-part bboxes instead of a hard-coded flip.
    """

    def __init__(self, cad_plus_is_nose: bool, lower_le_mm: float):
        self.cad_plus_is_nose = bool(cad_plus_is_nose)
        self.lower_le_mm = float(lower_le_mm)

    def asb_x_m(self, cad_x_mm: float) -> float:
        if self.cad_plus_is_nose:
            return (self.lower_le_mm - float(cad_x_mm)) / 1000.0
        return (float(cad_x_mm) - self.lower_le_mm) / 1000.0

    def wing_le_cad_mm(self, bbox: Any) -> float:
        return float(bbox.XMax) if self.cad_plus_is_nose else float(bbox.XMin)

    def cad_dx_mm_for_asb_aft(self, asb_dx_m: float) -> float:
        mm = float(asb_dx_m) * 1000.0
        return -mm if self.cad_plus_is_nose else mm

    def cad_dx_mm_toward_nose(self, mm: float) -> float:
        return float(mm) if self.cad_plus_is_nose else -float(mm)


def document_aero_frame(doc: Any | None) -> CadAeroFrame:
    """Infer CAD nose/aft from ``h_tail``, ``camera_bay``, or boom extent."""

    lower = _bbox(find_named(doc, "lower_wing")) if doc is not None else None
    tail = _bbox(find_named(doc, "h_tail")) if doc is not None else None
    camera = _bbox(find_named(doc, "camera_bay")) if doc is not None else None
    boom = _bbox(find_named(doc, "boom")) if doc is not None else None

    cad_plus_is_nose = True
    if lower is not None:
        lower_c = _center(lower)[0]
        if tail is not None:
            cad_plus_is_nose = _center(tail)[0] < lower_c
        elif camera is not None:
            cad_plus_is_nose = _center(camera)[0] > lower_c
        elif boom is not None:
            cad_plus_is_nose = abs(float(boom.XMin) - lower_c) > abs(
                float(boom.XMax) - lower_c
            )

    if lower is None:
        return CadAeroFrame(cad_plus_is_nose, 0.0)
    probe = CadAeroFrame(cad_plus_is_nose, 0.0)
    return CadAeroFrame(cad_plus_is_nose, probe.wing_le_cad_mm(lower))


def map_named_parts_to_asb(doc: Any | None) -> dict[str, Any]:
    """Return ASB +X-aft locations for named voider parts, when present."""

    if doc is None:
        return {}
    frame = document_aero_frame(doc)
    mapped: dict[str, Any] = {"cad_plus_is_nose": frame.cad_plus_is_nose}
    upper = _bbox(find_named(doc, "upper_wing"))
    if upper is not None:
        mapped["upper_le_x_m"] = frame.asb_x_m(frame.wing_le_cad_mm(upper))
    tail = _bbox(find_named(doc, "h_tail"))
    if tail is not None:
        mapped["tail_le_x_m"] = frame.asb_x_m(frame.wing_le_cad_mm(tail))
    boom = _bbox(find_named(doc, "boom"))
    if boom is not None:
        x0 = frame.asb_x_m(float(boom.XMin))
        x1 = frame.asb_x_m(float(boom.XMax))
        mapped["boom_x0_m"] = min(x0, x1)
        mapped["boom_x1_m"] = max(x0, x1)
    return mapped


def airplane_layout(cfg: dict[str, Any], doc: Any | None = None) -> dict[str, Any]:
    """ASB layout. Default stagger is aft (+X). Named bboxes override when present."""

    stagger = float(cfg.get("stagger_m") or 0.0)
    boom = float(cfg.get("boom_length_m") or 0.25)
    layout = {
        "cad_plus_is_nose": True,
        "upper_le_x_m": stagger,
        "tail_le_x_m": boom,
        "boom_x0_m": -0.02,
        "boom_x1_m": boom,
    }
    layout.update(map_named_parts_to_asb(doc))
    return layout


def named_part_geometry(doc: Any) -> dict[str, Any]:
    """Read boom length and h_tail span/chord from named objects when present."""

    inferred: dict[str, Any] = {}
    boom = find_named(doc, "boom")
    boom_bbox = _bbox(boom)
    if boom_bbox is not None:
        inferred["boom_length_mm"] = float(boom_bbox.XLength)
    tail = find_named(doc, "h_tail")
    tail_bbox = _bbox(tail)
    if tail_bbox is not None:
        span_mm, chord_mm = _span_and_chord_mm(tail_bbox)
        inferred["tail_span_mm"] = span_mm
        inferred["tail_chord_mm"] = chord_mm
    return inferred


def _seed_missing_named_parts(values: dict[str, Any], doc: Any, source: Any) -> None:
    named = named_part_geometry(doc)
    for key in _NAMED_TAIL_KEYS:
        if key not in named:
            continue
        if _has_param(source, key):
            continue
        values[key] = named[key]


def _has_param(obj: Any, key: str) -> bool:
    if getattr(obj, key, None) is not None:
        return True
    getter = getattr(obj, "getPropertyByName", None)
    if callable(getter):
        try:
            return getter(key) is not None
        except Exception:
            return False
    return False


def find_named(doc: Any, name: str) -> Any | None:
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
        if key in _STRING_KEYS:
            values[key] = str(raw)
        else:
            values[key] = float(raw)
    vehicle = getattr(obj, "vehicle_type", None)
    if vehicle is None:
        getter = getattr(obj, "getPropertyByName", None)
        if callable(getter):
            try:
                vehicle = getter("vehicle_type")
            except Exception:
                vehicle = None
    if vehicle is not None:
        values["vehicle_type"] = str(vehicle)


def _set_param(obj: Any, key: str, value: Any) -> None:
    if value is None:
        return
    stored = str(value) if key in _STRING_KEYS else float(value)
    if not hasattr(obj, key):
        adder = getattr(obj, "addProperty", None)
        if callable(adder):
            typ = "App::PropertyString" if key in _STRING_KEYS else "App::PropertyFloat"
            try:
                adder(typ, key, "Aero", key)
            except Exception:
                setattr(obj, key, stored)
                return
        else:
            setattr(obj, key, stored)
            return
    try:
        setattr(obj, key, stored)
    except Exception:
        pass


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
