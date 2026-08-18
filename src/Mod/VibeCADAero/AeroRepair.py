# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded pitch-stability geometry repairs for Analyze.

The AeroSandbox airplane is a staggered biplane. Without an ``h_tail`` the
neutral point stays forward of the default quarter-chord ``xyz_ref``, so
``Cmα`` is positive. Analyze therefore grows tail volume / boom arm, walks
avionics mass toward the nose, and nudges upper-wing stagger / decalage.
"""

from __future__ import annotations

from typing import Any

import AeroConfig

MAX_REPAIR_PASSES = 2

TAIL_SPAN_GROW = 1.20
TAIL_CHORD_GROW = 1.15
BOOM_GROW = 1.20
TAIL_SPAN_MAX_FRAC = 0.55
TAIL_CHORD_MAX_FRAC = 1.0
BOOM_MAX_CHORD_MULT = 6.0
BOOM_MIN_M = 0.25

STAGGER_DELTA = -0.10
STAGGER_MIN = 0.80
STAGGER_MAX = 1.50

DECALAGE_DELTA = 0.5
DECALAGE_MIN = 0.0
DECALAGE_MAX = 4.0

CG_SHIFT_MM = 18.0
CG_X_MIN_CHORD = -1.2
NOSE_PARTS = ("avionics_pod", "camera_bay")

_CONFIG_FIELDS = frozenset(
    {
        "tail_span_mm",
        "tail_chord_mm",
        "boom_length_mm",
        "stagger_c",
        "decalage_deg",
        "cg_x_m",
    }
)
_MM_THRESHOLD = 0.5
_UNIT_THRESHOLD = 1e-6


def cg_x_floor_m(cfg: dict[str, Any]) -> float:
    return float(cfg["chord_m"]) * CG_X_MIN_CHORD


def propose_repairs(
    cfg: dict[str, Any],
    payload: dict[str, Any],
    doc: Any | None = None,
) -> list[dict[str, Any]]:
    """Return bounded geometry changes that should make ``Cmα`` more negative."""

    if not payload.get("PitchUnstable") and not _positive_cmalpha(payload):
        return []

    changes: list[dict[str, Any]] = []
    span_mm = float(cfg["span_mm"])
    chord_mm = float(cfg["chord_mm"])
    tail_span = float(cfg.get("tail_span_mm") or 0.30 * span_mm)
    tail_chord = float(cfg.get("tail_chord_mm") or 0.60 * chord_mm)
    boom_mm = float(cfg.get("boom_length_mm") or (cfg.get("boom_length_m") or BOOM_MIN_M) * 1000.0)
    stagger = float(cfg.get("stagger_c") or 1.15)
    decalage = float(cfg.get("decalage_deg") or 2.0)
    cg_x = _current_cg_x_m(cfg)

    new_tail_span = min(span_mm * TAIL_SPAN_MAX_FRAC, tail_span * TAIL_SPAN_GROW)
    if new_tail_span - tail_span > _MM_THRESHOLD:
        changes.append(
            _change(
                "h_tail",
                "tail_span_mm",
                tail_span,
                new_tail_span,
                (
                    f"Grew the horizontal tail span from {_mm(tail_span)} mm "
                    f"to {_mm(new_tail_span)} mm."
                ),
            )
        )

    new_tail_chord = min(chord_mm * TAIL_CHORD_MAX_FRAC, tail_chord * TAIL_CHORD_GROW)
    if new_tail_chord - tail_chord > _MM_THRESHOLD:
        changes.append(
            _change(
                "h_tail",
                "tail_chord_mm",
                tail_chord,
                new_tail_chord,
                (
                    f"Grew the horizontal tail chord from {_mm(tail_chord)} mm "
                    f"to {_mm(new_tail_chord)} mm."
                ),
            )
        )

    boom_cap = chord_mm * BOOM_MAX_CHORD_MULT
    new_boom = min(boom_cap, max(BOOM_MIN_M * 1000.0, boom_mm * BOOM_GROW))
    if new_boom - boom_mm > _MM_THRESHOLD:
        changes.append(
            _change(
                "boom",
                "boom_length_mm",
                boom_mm,
                new_boom,
                f"Lengthened the boom from {_mm(boom_mm)} mm to {_mm(new_boom)} mm.",
            )
        )

    new_stagger = _clamp(stagger + STAGGER_DELTA, STAGGER_MIN, STAGGER_MAX)
    if abs(new_stagger - stagger) > _UNIT_THRESHOLD:
        changes.append(
            _change(
                "upper_wing",
                "stagger_c",
                stagger,
                new_stagger,
                (
                    f"Reduced upper-wing stagger from {stagger:.2f}c to {new_stagger:.2f}c."
                    if new_stagger < stagger
                    else f"Increased upper-wing stagger from {stagger:.2f}c to {new_stagger:.2f}c."
                ),
            )
        )

    new_decalage = _clamp(decalage + DECALAGE_DELTA, DECALAGE_MIN, DECALAGE_MAX)
    if abs(new_decalage - decalage) > _UNIT_THRESHOLD:
        changes.append(
            _change(
                "upper_wing",
                "decalage_deg",
                decalage,
                new_decalage,
                (
                    f"Increased upper-wing decalage from {decalage:.1f}° to {new_decalage:.1f}°."
                    if new_decalage > decalage
                    else f"Decreased upper-wing decalage from {decalage:.1f}° to {new_decalage:.1f}°."
                ),
            )
        )

    floor = cg_x_floor_m(cfg)
    new_cg = max(floor, cg_x - CG_SHIFT_MM / 1000.0)
    if cg_x - new_cg > _UNIT_THRESHOLD:
        changes.append(
            _change(
                None,
                "cg_x_m",
                cg_x,
                new_cg,
                (
                    "Moved the aero CG "
                    f"{_mm((cg_x - new_cg) * 1000.0)} mm toward the nose."
                ),
            )
        )

    if doc is not None:
        for name in NOSE_PARTS:
            obj = AeroConfig.find_named(doc, name)
            if obj is None:
                continue
            current = _part_x_mm(obj)
            if current is None:
                continue
            after = current - CG_SHIFT_MM
            changes.append(
                _change(
                    name,
                    "x_mm",
                    current,
                    after,
                    f"Moved {name} {_mm(CG_SHIFT_MM)} mm toward the nose.",
                )
            )

    return changes


def apply_repairs(
    doc: Any,
    cfg: dict[str, Any],
    proposals: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Write accepted changes onto ``AeroConfig`` and live named parts."""

    landed: list[dict[str, Any]] = []
    updates: dict[str, Any] = {}
    boom_delta_mm = 0.0
    for proposal in proposals:
        item = dict(proposal)
        field = str(item.get("field") or "")
        cad_ok = False
        if item.get("part"):
            cad_ok = _apply_cad(doc, item, cfg)
        if field == "boom_length_mm":
            boom_delta_mm = float(item["after"]) - float(item["before"])
        if field in _CONFIG_FIELDS:
            updates[field] = item["after"]
            item["config"] = True
        else:
            item["config"] = False
        item["cad"] = cad_ok
        if item["config"] or item["cad"]:
            landed.append(item)

    if boom_delta_mm > _MM_THRESHOLD and doc is not None:
        tail = AeroConfig.find_named(doc, "h_tail")
        if tail is not None and _translate(tail, boom_delta_mm, 0.0, 0.0):
            for item in landed:
                if item.get("part") == "h_tail" and item.get("field") == "tail_span_mm":
                    item["cad"] = True

    if updates:
        _persist_config(doc, cfg, updates)
        _sync_cfg(cfg, updates)
    return landed


def format_user_message(
    changes: list[dict[str, Any]],
    payload: dict[str, Any],
    passes: int = 0,
) -> str:
    lines = [str(item.get("sentence") or "") for item in changes if item.get("sentence")]
    if changes:
        if payload.get("PitchUnstable") or _positive_cmalpha(payload):
            label = "pass" if passes == 1 else "passes"
            lines.append(
                f"Analyze is still pitch-unstable (Cmα > 0) after {passes} repair {label}."
            )
        else:
            lines.append("Analyze is now pitch-stable (Cmα ≤ 0).")
    elif payload.get("PitchUnstable") or _positive_cmalpha(payload):
        lines.append(
            "Analyze is pitch-unstable (Cmα > 0), but no bounded geometry change could be applied."
        )
    return "\n".join(line for line in lines if line)


def _persist_config(doc: Any, cfg: dict[str, Any], updates: dict[str, Any]) -> None:
    payload = {key: cfg.get(key) for key in AeroConfig._WRITE_KEYS if cfg.get(key) is not None}
    payload.update(updates)
    for key in AeroConfig._REPAIR_KEYS:
        if key in updates:
            payload[key] = updates[key]
        elif cfg.get(key) is not None:
            payload[key] = cfg[key]
    AeroConfig.write_config(doc, payload)


def _sync_cfg(cfg: dict[str, Any], updates: dict[str, Any]) -> None:
    cfg.update(updates)
    if "tail_span_mm" in updates:
        cfg["tail_span_m"] = float(updates["tail_span_mm"]) / 1000.0
    if "tail_chord_mm" in updates:
        cfg["tail_chord_m"] = float(updates["tail_chord_mm"]) / 1000.0
    if "boom_length_mm" in updates:
        cfg["boom_length_m"] = float(updates["boom_length_mm"]) / 1000.0
    if "stagger_c" in updates:
        cfg["stagger_m"] = float(updates["stagger_c"]) * float(cfg["chord_m"])
    if "cg_x_m" in updates:
        gap = float(cfg.get("gap_m") or 0.0)
        cfg["xyz_ref"] = [float(updates["cg_x_m"]), 0.0, gap / 2.0]


def _apply_cad(doc: Any, change: dict[str, Any], cfg: dict[str, Any]) -> bool:
    obj = AeroConfig.find_named(doc, str(change["part"]))
    if obj is None:
        return False
    field = change["field"]
    before = float(change["before"])
    after = float(change["after"])
    if abs(after - before) <= _UNIT_THRESHOLD:
        return False
    if field in {"x_mm"}:
        return _translate(obj, after - before, 0.0, 0.0)
    if field == "tail_span_mm":
        return _scale_part(obj, 1.0, after / before, 1.0, _center_y0_le(obj))
    if field == "tail_chord_mm":
        return _scale_part(obj, after / before, 1.0, 1.0, _center_y0_le(obj))
    if field == "boom_length_mm":
        return _scale_part(obj, after / before, 1.0, 1.0, _center_y0_le(obj))
    if field == "stagger_c":
        dx = -(after - before) * float(cfg["chord_mm"])
        return _translate(obj, dx, 0.0, 0.0)
    if field == "decalage_deg":
        return _rotate_about_y(obj, after - before)
    return False


def _scale_part(
    obj: Any,
    sx: float,
    sy: float,
    sz: float,
    center: tuple[float, float, float],
) -> bool:
    changed = _try_freecad_scale(obj, sx, sy, sz, center)
    bbox = _bbox_of(obj)
    if bbox is not None:
        _scale_bbox(bbox, sx, sy, sz, center)
        changed = True
    if hasattr(obj, "Length") and abs(sx - 1.0) > _UNIT_THRESHOLD:
        try:
            obj.Length = float(obj.Length) * sx
            changed = True
        except Exception:
            pass
    if hasattr(obj, "Width") and abs(sy - 1.0) > _UNIT_THRESHOLD:
        try:
            obj.Width = float(obj.Width) * sy
            changed = True
        except Exception:
            pass
    return changed


def _try_freecad_scale(
    obj: Any,
    sx: float,
    sy: float,
    sz: float,
    center: tuple[float, float, float],
) -> bool:
    shape = getattr(obj, "Shape", None)
    if shape is None or not hasattr(shape, "transformGeometry"):
        return False
    try:
        import FreeCAD

        matrix = FreeCAD.Matrix()
        matrix.move(FreeCAD.Vector(-center[0], -center[1], -center[2]))
        matrix.scale(sx, sy, sz)
        matrix.move(FreeCAD.Vector(center[0], center[1], center[2]))
        obj.Shape = shape.transformGeometry(matrix)
        return True
    except Exception:
        return False


def _scale_bbox(
    bbox: Any,
    sx: float,
    sy: float,
    sz: float,
    center: tuple[float, float, float],
) -> None:
    cx, cy, cz = center

    def _edge(vmin: float, vmax: float, origin: float, scale: float) -> tuple[float, float]:
        return origin + (vmin - origin) * scale, origin + (vmax - origin) * scale

    xmin, xmax = _edge(float(bbox.XMin), float(bbox.XMax), cx, sx)
    ymin, ymax = _edge(float(bbox.YMin), float(bbox.YMax), cy, sy)
    zmin, zmax = _edge(float(bbox.ZMin), float(bbox.ZMax), cz, sz)
    bbox.XMin, bbox.XMax = xmin, xmax
    bbox.YMin, bbox.YMax = ymin, ymax
    bbox.ZMin, bbox.ZMax = zmin, zmax
    bbox.XLength = abs(xmax - xmin)
    bbox.YLength = abs(ymax - ymin)
    bbox.ZLength = abs(zmax - zmin)


def _translate(obj: Any, dx: float, dy: float, dz: float) -> bool:
    placement = _ensure_placement(obj)
    base = getattr(placement, "Base", None)
    if base is None:
        return False
    if hasattr(base, "x"):
        base.x = float(base.x) + dx
        if hasattr(base, "y"):
            base.y = float(base.y) + dy
        if hasattr(base, "z"):
            base.z = float(base.z) + dz
    elif isinstance(base, (list, tuple)) and len(base) >= 3:
        placement.Base = [float(base[0]) + dx, float(base[1]) + dy, float(base[2]) + dz]
    else:
        return False
    bbox = _bbox_of(obj)
    if bbox is not None:
        bbox.XMin = float(bbox.XMin) + dx
        bbox.XMax = float(bbox.XMax) + dx
        bbox.YMin = float(bbox.YMin) + dy
        bbox.YMax = float(bbox.YMax) + dy
        bbox.ZMin = float(bbox.ZMin) + dz
        bbox.ZMax = float(bbox.ZMax) + dz
    return True


def _rotate_about_y(obj: Any, delta_deg: float) -> bool:
    placement = _ensure_placement(obj)
    try:
        import FreeCAD

        extra = FreeCAD.Rotation(FreeCAD.Vector(0.0, 1.0, 0.0), float(delta_deg))
        current = getattr(placement, "Rotation", None)
        placement.Rotation = extra.multiply(current) if current is not None else extra
        return True
    except Exception:
        pass
    rotation = getattr(placement, "Rotation", None)
    if rotation is not None and hasattr(rotation, "Angle"):
        rotation.Angle = float(getattr(rotation, "Angle", 0.0)) + float(delta_deg)
        return True
    return False


def _ensure_placement(obj: Any) -> Any:
    placement = getattr(obj, "Placement", None)
    if placement is None:
        obj.Placement = type("_Pl", (), {})()
        obj.Placement.Base = type("_V", (), {"x": 0.0, "y": 0.0, "z": 0.0})()
        obj.Placement.Rotation = type("_R", (), {"Angle": 0.0})()
        return obj.Placement
    if getattr(placement, "Base", None) is None:
        placement.Base = type("_V", (), {"x": 0.0, "y": 0.0, "z": 0.0})()
    return placement


def _center_y0_le(obj: Any) -> tuple[float, float, float]:
    bbox = _bbox_of(obj)
    if bbox is None:
        return (0.0, 0.0, 0.0)
    return (float(bbox.XMin), 0.0, 0.5 * (float(bbox.ZMin) + float(bbox.ZMax)))


def _bbox_of(obj: Any) -> Any | None:
    shape = getattr(obj, "Shape", None)
    box = getattr(shape, "BoundBox", None) if shape is not None else None
    if box is None:
        box = getattr(obj, "BoundBox", None)
    return box


def _part_x_mm(obj: Any) -> float | None:
    placement = getattr(obj, "Placement", None)
    base = getattr(placement, "Base", None) if placement is not None else None
    if base is not None and hasattr(base, "x"):
        return float(base.x)
    bbox = _bbox_of(obj)
    if bbox is None:
        return None
    return 0.5 * (float(bbox.XMin) + float(bbox.XMax))


def _current_cg_x_m(cfg: dict[str, Any]) -> float:
    if cfg.get("cg_x_m") is not None:
        return float(cfg["cg_x_m"])
    ref = cfg.get("xyz_ref")
    if isinstance(ref, (list, tuple)) and ref:
        return float(ref[0])
    return 0.25 * float(cfg["chord_m"])


def _positive_cmalpha(payload: dict[str, Any]) -> bool:
    value = payload.get("Cmalpha")
    return value is not None and float(value) > 0.0


def _change(
    part: str | None,
    field: str,
    before: float,
    after: float,
    sentence: str,
) -> dict[str, Any]:
    return {
        "part": part,
        "field": field,
        "before": float(before),
        "after": float(after),
        "sentence": sentence,
    }


def _mm(value: float) -> str:
    return f"{float(value):.0f}"


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))
