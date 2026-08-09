# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Drawing ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADNativeSnapshot import concise_object, objects_of_type


MAX_PAGES = 16


def _view_summary(view: Any) -> dict[str, Any]:
    result = concise_object(view)
    sources = list(getattr(view, "Source", []) or [])
    if sources:
        result["sources"] = [concise_object(value) for value in sources[:12]]
    for name in ("X", "Y", "Scale"):
        if hasattr(view, name):
            try:
                result[name.lower()] = float(getattr(view, name))
            except Exception:
                continue
    return result


def _page_summary(page: Any) -> dict[str, Any]:
    result = concise_object(page)
    views = list(getattr(page, "Views", []) or [])
    result["view_count"] = len(views)
    result["views"] = [_view_summary(value) for value in views[:48]]
    template = getattr(page, "Template", None)
    if template is not None:
        result["template"] = concise_object(template)
    return result


def build_drawing_snapshot(document: Any) -> dict[str, Any]:
    pages = objects_of_type(document, "TechDraw::DrawPage")
    return {
        "kind": "drawing",
        "page_count": len(pages),
        "pages": [_page_summary(value) for value in pages[:MAX_PAGES]],
    }
