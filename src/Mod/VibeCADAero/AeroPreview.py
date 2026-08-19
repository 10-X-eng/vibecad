# SPDX-License-Identifier: LGPL-2.1-or-later

"""One-shot repair preview bound to a document geometry fingerprint."""

from __future__ import annotations

import hashlib
import json
from typing import Any

PREVIEW_NAME = "AeroRepairPreview"

_REVISION_CONFIG_FIELDS = (
    "vehicle_type",
    "airfoil",
    "span_mm",
    "chord_mm",
    "gap_c",
    "stagger_c",
    "decalage_deg",
    "alpha_deg",
    "auw_g",
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
    "has_h_tail",
)

_REVISION_PARTS = (
    "lower_wing",
    "upper_wing",
    "boom",
    "h_tail",
    "avionics_pod",
    "camera_bay",
)


def geometry_revision(doc: Any, cfg: dict[str, Any]) -> str:
    payload = {
        "config": {
            key: _json_value(cfg.get(key)) for key in _REVISION_CONFIG_FIELDS
        },
        "parts": [
            _part_revision(name, _named(doc, name)) for name in _REVISION_PARTS
        ],
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def write_preview(
    doc: Any,
    *,
    revision: str,
    proposals: list[dict[str, Any]],
    native_revision: str | None = None,
) -> dict[str, Any]:
    record = {
        "revision": revision,
        "native_revision": native_revision,
        "proposals": proposals,
        "consumed": False,
    }
    _store(doc, record)
    return record


def read_preview(doc: Any) -> dict[str, Any] | None:
    getter = getattr(doc, "getObject", None)
    obj = getter(PREVIEW_NAME) if callable(getter) else None
    raw = getattr(obj, "Text", None) if obj is not None else getattr(doc, PREVIEW_NAME, None)
    if not raw:
        return None
    if isinstance(raw, dict):
        return dict(raw)
    try:
        loaded = json.loads(str(raw))
    except Exception:
        return None
    return loaded if isinstance(loaded, dict) else None


def consume_preview(
    doc: Any,
    current_revision: str,
    *,
    native_revision: str | None = None,
) -> list[dict[str, Any]]:
    proposals = validate_preview(
        doc,
        current_revision,
        native_revision=native_revision,
    )
    mark_preview_consumed(
        doc,
        current_revision,
        native_revision=native_revision,
    )
    return proposals


def validate_preview(
    doc: Any,
    current_revision: str,
    *,
    native_revision: str | None = None,
) -> list[dict[str, Any]]:
    """Validate a preview without consuming its one-shot authorization."""

    record = read_preview(doc)
    if record is None:
        raise PreviewError("missing")
    if record.get("consumed"):
        raise PreviewError("already_consumed")
    if str(record.get("revision") or "") != str(current_revision):
        raise PreviewError("stale")
    stored_native = str(record.get("native_revision") or "")
    supplied_native = str(native_revision or "")
    if stored_native != supplied_native:
        raise PreviewError("stale")
    return list(record.get("proposals") or [])


def mark_preview_consumed(
    doc: Any,
    current_revision: str,
    *,
    native_revision: str | None = None,
) -> dict[str, Any]:
    """Consume a still-valid preview after its repair has landed."""

    validate_preview(doc, current_revision, native_revision=native_revision)
    record = read_preview(doc)
    if record is None:
        raise PreviewError("missing")
    record["consumed"] = True
    _store(doc, record)
    return record


def discard_preview(doc: Any) -> dict[str, Any] | None:
    record = read_preview(doc)
    if record is None:
        return None
    record["consumed"] = True
    record["rejected"] = True
    _store(doc, record)
    return record


class PreviewError(RuntimeError):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _store(doc: Any, record: dict[str, Any]) -> None:
    encoded = json.dumps(record, ensure_ascii=True)
    adder = getattr(doc, "addObject", None)
    getter = getattr(doc, "getObject", None)
    obj = getter(PREVIEW_NAME) if callable(getter) else None
    if obj is None and callable(adder):
        try:
            obj = adder("App::TextDocument", PREVIEW_NAME)
        except Exception:
            obj = None
    if obj is not None and hasattr(obj, "Text"):
        obj.Text = encoded
    try:
        setattr(doc, PREVIEW_NAME, encoded)
    except Exception:
        pass


def _named(doc: Any, name: str) -> Any | None:
    getter = getattr(doc, "getObject", None)
    if callable(getter):
        try:
            obj = getter(name)
        except Exception:
            obj = None
        if obj is not None:
            return obj
    for obj in getattr(doc, "Objects", ()) or ():
        if str(getattr(obj, "Name", "") or "") == name:
            return obj
    return None


def _part_revision(name: str, obj: Any | None) -> dict[str, Any]:
    if obj is None:
        return {"name": name, "present": False}
    shape = getattr(obj, "Shape", None)
    bbox = getattr(shape, "BoundBox", None) if shape is not None else None
    placement = getattr(obj, "Placement", None)
    base = getattr(placement, "Base", None) if placement is not None else None
    rotation = getattr(placement, "Rotation", None) if placement is not None else None
    axis = getattr(rotation, "Axis", None) if rotation is not None else None
    shape_hash = None
    hash_code = getattr(shape, "hashCode", None) if shape is not None else None
    if callable(hash_code):
        try:
            shape_hash = int(hash_code())
        except Exception:
            shape_hash = None
    return {
        "name": name,
        "present": True,
        "type_id": str(getattr(obj, "TypeId", "") or ""),
        "shape_type": str(getattr(shape, "ShapeType", "") or ""),
        "shape_hash": shape_hash,
        "topology": {
            key: _sequence_length(getattr(shape, key, ()))
            for key in ("Solids", "Shells", "Faces", "Edges", "Vertexes")
        },
        "bounds": _vector_fields(
            bbox,
            ("XMin", "XMax", "YMin", "YMax", "ZMin", "ZMax"),
        ),
        "placement": {
            "base": _vector_fields(base, ("x", "y", "z")),
            "angle": _number(getattr(rotation, "Angle", None)),
            "axis": _vector_fields(axis, ("x", "y", "z")),
            "quaternion": _json_value(getattr(rotation, "Q", None)),
        },
    }


def _sequence_length(value: Any) -> int | None:
    try:
        return len(value)
    except Exception:
        return None


def _vector_fields(value: Any, fields: tuple[str, ...]) -> list[float | None] | None:
    if value is None:
        return None
    return [_number(getattr(value, field, None)) for field in fields]


def _number(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return round(float(value), 12)
    except (TypeError, ValueError, OverflowError):
        return None


def _json_value(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return _number(value)
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    numeric = _number(value)
    return numeric if numeric is not None else str(value)
