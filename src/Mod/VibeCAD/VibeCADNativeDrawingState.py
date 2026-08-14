# SPDX-License-Identifier: LGPL-2.1-or-later

"""Stable, path-free state for exact Native Drawing targets."""

from __future__ import annotations

import hashlib
import json
from typing import Any, Mapping

from VibeCADNativeInput import NativeInputError, inspect_native_input_file


MAX_DRAWING_TEMPLATE_BYTES = 4 * 1024 * 1024
MAX_EDITABLE_TEMPLATE_FIELDS = 64
MAX_TEMPLATE_FIELD_NAME_CHARACTERS = 128
MAX_TEMPLATE_FIELD_VALUE_CHARACTERS = 512


def _is_derived(obj: Any, type_id: str) -> bool:
    check = getattr(obj, "isDerivedFrom", None)
    if callable(check):
        try:
            return bool(check(type_id))
        except Exception:
            return False
    return str(getattr(obj, "TypeId", "") or "") == type_id


def is_drawing_page(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawPage")


def is_svg_template(obj: Any) -> bool:
    return _is_derived(obj, "TechDraw::DrawSVGTemplate")


def _identity(obj: Any) -> dict[str, str]:
    return {
        "object_name": str(getattr(obj, "Name", "") or ""),
        "label": str(getattr(obj, "Label", "") or ""),
        "type_id": str(getattr(obj, "TypeId", "") or ""),
    }


def editable_template_fields(template: Any) -> dict[str, str]:
    if not is_svg_template(template):
        return {}
    try:
        values = dict(getattr(template, "EditableTexts", {}) or {})
    except Exception:
        return {}
    return {
        str(name): str(value)
        for name, value in values.items()
    }


def template_content_state(template: Any) -> dict[str, Any]:
    if not is_svg_template(template):
        return {"available": False}
    source = str(getattr(template, "PageResult", "") or "")
    if not source:
        return {"available": False}
    try:
        record = inspect_native_input_file(
            source,
            maximum_bytes=MAX_DRAWING_TEMPLATE_BYTES,
        )
    except (NativeInputError, OSError, RuntimeError):
        return {"available": False}
    if not bool(record.get("configured")):
        return {"available": False}
    return {
        "available": True,
        "size_bytes": int(record["size_bytes"]),
        "sha256": str(record["sha256"]),
    }


def _template_geometry(template: Any) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, output_name in (
        ("Width", "width_mm"),
        ("Height", "height_mm"),
    ):
        try:
            result[output_name] = round(float(getattr(template, name)), 9)
        except Exception:
            result[output_name] = None
    result["orientation"] = str(getattr(template, "Orientation", "") or "")
    return result


def drawing_page_invariants(page: Any) -> dict[str, Any]:
    if not is_drawing_page(page):
        raise TypeError("page must be a TechDraw::DrawPage")
    template = getattr(page, "Template", None)
    views = tuple(getattr(page, "Views", ()) or ())
    return {
        "page": _identity(page),
        "keep_updated": bool(getattr(page, "KeepUpdated", False)),
        "projection_type": str(getattr(page, "ProjectionType", "") or ""),
        "scale": round(float(getattr(page, "Scale", 1.0)), 12),
        "view_names": [str(getattr(view, "Name", "") or "") for view in views],
        "template": _identity(template) if template is not None else None,
        "template_geometry": (
            _template_geometry(template) if template is not None else None
        ),
        "template_content": (
            template_content_state(template) if template is not None else None
        ),
    }


def _state_sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def drawing_page_state(page: Any) -> dict[str, Any]:
    """Return one exact page identity plus bounded editable template state."""

    invariants = drawing_page_invariants(page)
    template = getattr(page, "Template", None)
    fields = editable_template_fields(template)
    ordered_fields = sorted(fields.items())
    supported = bool(
        len(ordered_fields) <= MAX_EDITABLE_TEMPLATE_FIELDS
        and all(
            0 < len(name) <= MAX_TEMPLATE_FIELD_NAME_CHARACTERS
            and len(value) <= MAX_TEMPLATE_FIELD_VALUE_CHARACTERS
            for name, value in ordered_fields
        )
    )
    exact_state = {
        **invariants,
        "editable_fields": ordered_fields,
    }
    result = {
        **invariants["page"],
        "state_sha256": _state_sha256(exact_state),
        "keep_updated": invariants["keep_updated"],
        "projection_type": invariants["projection_type"],
        "scale": invariants["scale"],
        "view_count": len(invariants["view_names"]),
        "view_names": list(invariants["view_names"][:48]),
        "template": invariants["template"],
        "template_geometry": invariants["template_geometry"],
        "template_content": invariants["template_content"],
        "editable_field_count": len(ordered_fields),
        "editable_fields_supported": supported,
    }
    if supported:
        result["editable_fields"] = [
            {"field_name": name, "value": value}
            for name, value in ordered_fields
        ]
    if len(invariants["view_names"]) > 48:
        result["views_truncated"] = True
    return result
