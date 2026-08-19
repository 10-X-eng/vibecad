# SPDX-License-Identifier: LGPL-2.1-or-later

"""One-shot repair preview bound to a document geometry fingerprint."""

from __future__ import annotations

import hashlib
import json
from typing import Any

PREVIEW_NAME = "AeroRepairPreview"


def geometry_revision(doc: Any, cfg: dict[str, Any]) -> str:
    payload = {
        "span_mm": cfg.get("span_mm"),
        "chord_mm": cfg.get("chord_mm"),
        "stagger_c": cfg.get("stagger_c"),
        "boom_length_mm": cfg.get("boom_length_mm"),
        "tail_span_mm": cfg.get("tail_span_mm"),
        "tail_chord_mm": cfg.get("tail_chord_mm"),
        "xyz_ref_c": cfg.get("xyz_ref_c"),
        "auw_g": cfg.get("auw_g"),
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
    record = read_preview(doc)
    if record is None:
        raise PreviewError("missing")
    if record.get("consumed"):
        raise PreviewError("already_consumed")
    if str(record.get("revision") or "") != str(current_revision):
        raise PreviewError("stale")
    stored_native = record.get("native_revision")
    if stored_native not in (None, "") and native_revision not in (None, ""):
        if str(stored_native) != str(native_revision):
            raise PreviewError("stale")
    record["consumed"] = True
    _store(doc, record)
    return list(record.get("proposals") or [])


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
