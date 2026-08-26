# SPDX-License-Identifier: LGPL-2.1-or-later

"""Paged exact source discovery for Native Drawing tools."""

from __future__ import annotations

from typing import Any

from VibeCADNativeDrawingProviderState import compact_drawing_source
from VibeCADNativeDrawingViewState import drawing_source_state
from VibeCADNativeGeometrySources import active_design_geometry_sources


MAX_DRAWING_SOURCE_PAGE_SIZE = 48
MAX_DRAWING_SOURCE_OFFSET = 1_000_000


def drawing_source_catalog_page(
    document: Any,
    *,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    """Return a deterministic page of copyable exact Drawing source targets."""

    if type(offset) is not int or not 0 <= offset <= MAX_DRAWING_SOURCE_OFFSET:
        raise ValueError("Drawing source offset must be 0 through 1000000.")
    if type(page_size) is not int or not 1 <= page_size <= MAX_DRAWING_SOURCE_PAGE_SIZE:
        raise ValueError("Drawing source page_size must be 1 through 48.")
    candidates = active_design_geometry_sources(document)
    count = len(candidates)
    if offset > count or (count and offset == count):
        raise ValueError("Drawing source offset exceeds the source count.")
    stop = min(offset + page_size, count)
    sources = [
        compact_drawing_source(drawing_source_state(source))
        for source in candidates[offset:stop]
    ]
    return {
        "source_count": count,
        "offset": offset,
        "returned_count": len(sources),
        "next_offset": stop if stop < count else None,
        "sources": sources,
    }


__all__ = [
    "MAX_DRAWING_SOURCE_OFFSET",
    "MAX_DRAWING_SOURCE_PAGE_SIZE",
    "drawing_source_catalog_page",
]
