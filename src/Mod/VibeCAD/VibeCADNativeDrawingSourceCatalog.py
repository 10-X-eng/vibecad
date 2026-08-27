# SPDX-License-Identifier: LGPL-2.1-or-later

"""Paged exact source discovery for Native Drawing tools."""

from __future__ import annotations

from collections import OrderedDict
from copy import deepcopy
import threading
from typing import Any

from VibeCADNativeDrawingProviderState import compact_drawing_source
from VibeCADNativeDrawingViewState import (
    drawing_source_catalog_identity_state,
    drawing_source_catalog_state,
)
from VibeCADNativeGeometrySources import (
    active_design_geometry_sources,
    drawing_analysis_artifact_names,
    is_potential_design_geometry_source,
)


MAX_DRAWING_SOURCE_PAGE_SIZE = 48
MAX_DRAWING_SOURCE_OFFSET = 1_000_000
MAX_DRAWING_SOURCE_CACHE_PAGES = 32
MAX_DRAWING_SOURCE_CACHE_DOCUMENTS = 4


class DrawingSourceCatalogNotReady(RuntimeError):
    """The responsive source catalog has not completed for this revision."""


_source_page_cache_lock = threading.RLock()
_source_page_cache: OrderedDict[
    tuple[str, int, int, int],
    dict[str, Any],
] = OrderedDict()
_source_catalog_cache: OrderedDict[
    tuple[str, int],
    tuple[dict[str, Any], ...],
] = OrderedDict()


def capture_drawing_source_catalog_inventory(document: Any) -> dict[str, Any]:
    """Detach every effectively available Drawing source without reading BREP."""

    analysis_artifacts = drawing_analysis_artifact_names(document)
    names = []
    sources = []
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        if not is_potential_design_geometry_source(
            document,
            obj,
            analysis_artifact_names=analysis_artifacts,
        ):
            continue
        try:
            source = drawing_source_catalog_identity_state(obj)
        except (AttributeError, ReferenceError, RuntimeError, TypeError, ValueError):
            continue
        names.append(str(source["object_name"]))
        sources.append(source)
    return {
        "object_names": names,
        "sources": sources,
        "analysis_artifact_names": sorted(analysis_artifacts),
    }


def invalidate_drawing_source_catalog_cache(
    document_uid: str | None = None,
) -> int:
    """Discard cached detached pages for one document, or for every document."""

    uid = str(document_uid or "").strip()
    with _source_page_cache_lock:
        page_keys = tuple(
            key for key in _source_page_cache if not uid or key[0] == uid
        )
        catalog_keys = tuple(
            key for key in _source_catalog_cache if not uid or key[0] == uid
        )
        for key in page_keys:
            _source_page_cache.pop(key, None)
        for key in catalog_keys:
            _source_catalog_cache.pop(key, None)
    return len(page_keys) + len(catalog_keys)


def cache_drawing_source_catalog_state(
    document_uid: str,
    structural_revision: int,
    sources: Any,
) -> dict[str, int]:
    """Store one complete detached source catalog for an exact revision."""

    uid = str(document_uid or "").strip()
    if not uid:
        raise ValueError("Drawing source caching requires an exact document UID.")
    if type(structural_revision) is not int or structural_revision < 0:
        raise ValueError("Drawing source structural_revision must be non-negative.")
    if not isinstance(sources, (list, tuple)) or any(
        not isinstance(source, dict) for source in sources
    ):
        raise TypeError("Drawing source cache entries must be detached objects.")
    detached = tuple(deepcopy(source) for source in sources)
    key = (uid, structural_revision)
    with _source_page_cache_lock:
        for stale in tuple(_source_catalog_cache):
            if stale[0] == uid and stale != key:
                _source_catalog_cache.pop(stale, None)
        for stale in tuple(_source_page_cache):
            if stale[0] == uid and stale[1] != structural_revision:
                _source_page_cache.pop(stale, None)
        _source_catalog_cache[key] = detached
        _source_catalog_cache.move_to_end(key)
        while len(_source_catalog_cache) > MAX_DRAWING_SOURCE_CACHE_DOCUMENTS:
            _source_catalog_cache.popitem(last=False)
    return {
        "structural_revision": structural_revision,
        "source_count": len(detached),
    }


def drawing_source_catalog_is_cached(
    document_uid: str,
    structural_revision: int,
) -> bool:
    """Return whether a complete detached catalog exists for one revision."""

    uid = str(document_uid or "").strip()
    if not uid or type(structural_revision) is not int or structural_revision < 0:
        return False
    key = (uid, structural_revision)
    with _source_page_cache_lock:
        cached = _source_catalog_cache.get(key)
        if cached is None:
            return False
        _source_catalog_cache.move_to_end(key)
        return True


def cached_drawing_source_catalog_state(
    document_uid: str,
    structural_revision: int,
) -> list[dict[str, Any]] | None:
    """Return a detached complete catalog when its exact revision is cached."""

    uid = str(document_uid or "").strip()
    if not uid or type(structural_revision) is not int or structural_revision < 0:
        return None
    key = (uid, structural_revision)
    with _source_page_cache_lock:
        cached = _source_catalog_cache.get(key)
        if cached is None:
            return None
        _source_catalog_cache.move_to_end(key)
        return deepcopy(list(cached))


def _validate_page_arguments(offset: int, page_size: int) -> None:
    if type(offset) is not int or not 0 <= offset <= MAX_DRAWING_SOURCE_OFFSET:
        raise ValueError("Drawing source offset must be 0 through 1000000.")
    if type(page_size) is not int or not 1 <= page_size <= MAX_DRAWING_SOURCE_PAGE_SIZE:
        raise ValueError("Drawing source page_size must be 1 through 48.")


def _uncached_source_state_page(
    document: Any,
    *,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    candidates = active_design_geometry_sources(document, validate_brep=False)
    count = len(candidates)
    if offset > count or (count and offset == count):
        raise ValueError("Drawing source offset exceeds the source count.")
    stop = min(offset + page_size, count)
    sources = [
        drawing_source_catalog_state(source)
        for source in candidates[offset:stop]
    ]
    return {
        "source_count": count,
        "offset": offset,
        "returned_count": len(sources),
        "next_offset": stop if stop < count else None,
        "sources": sources,
    }


def drawing_source_catalog_state_page(
    document: Any,
    *,
    offset: int,
    page_size: int,
    structural_revision: int | None = None,
    require_cached: bool = False,
) -> dict[str, Any]:
    """Return one detached raw source page, cached for an exact revision."""

    _validate_page_arguments(offset, page_size)
    if type(require_cached) is not bool:
        raise TypeError("require_cached must be a boolean")
    if structural_revision is None:
        if require_cached:
            raise DrawingSourceCatalogNotReady(
                "A responsive Drawing source read requires an exact revision."
            )
        return _uncached_source_state_page(
            document,
            offset=offset,
            page_size=page_size,
        )
    if type(structural_revision) is not int or structural_revision < 0:
        raise ValueError("Drawing source structural_revision must be non-negative.")
    document_uid = str(getattr(document, "Uid", "") or "").strip()
    if not document_uid:
        raise ValueError("Drawing source caching requires an exact document UID.")
    key = (document_uid, structural_revision, offset, page_size)
    with _source_page_cache_lock:
        complete_key = (document_uid, structural_revision)
        complete = _source_catalog_cache.get(complete_key)
        if complete is not None:
            _source_catalog_cache.move_to_end(complete_key)
            count = len(complete)
            if offset > count or (count and offset == count):
                raise ValueError("Drawing source offset exceeds the source count.")
            stop = min(offset + page_size, count)
            return {
                "source_count": count,
                "offset": offset,
                "returned_count": stop - offset,
                "next_offset": stop if stop < count else None,
                "sources": deepcopy(list(complete[offset:stop])),
            }
        cached = _source_page_cache.get(key)
        if cached is not None:
            _source_page_cache.move_to_end(key)
            return deepcopy(cached)
    if require_cached:
        raise DrawingSourceCatalogNotReady(
            "Drawing sources are still being prepared for the current document revision."
        )

    result = _uncached_source_state_page(
        document,
        offset=offset,
        page_size=page_size,
    )
    with _source_page_cache_lock:
        for stale in tuple(_source_page_cache):
            if stale[0] == document_uid and stale[1] != structural_revision:
                _source_page_cache.pop(stale, None)
        _source_page_cache[key] = deepcopy(result)
        _source_page_cache.move_to_end(key)
        while len(_source_page_cache) > MAX_DRAWING_SOURCE_CACHE_PAGES:
            _source_page_cache.popitem(last=False)
    return deepcopy(result)


def drawing_source_catalog_page(
    document: Any,
    *,
    offset: int,
    page_size: int,
    structural_revision: int | None = None,
    require_cached: bool = False,
) -> dict[str, Any]:
    """Return a deterministic page of copyable exact Drawing source targets."""

    state = drawing_source_catalog_state_page(
        document,
        offset=offset,
        page_size=page_size,
        structural_revision=structural_revision,
        require_cached=require_cached,
    )
    sources = [compact_drawing_source(source) for source in state["sources"]]
    return {
        "source_count": state["source_count"],
        "offset": state["offset"],
        "returned_count": len(sources),
        "next_offset": state["next_offset"],
        "sources": sources,
    }


__all__ = [
    "capture_drawing_source_catalog_inventory",
    "DrawingSourceCatalogNotReady",
    "MAX_DRAWING_SOURCE_OFFSET",
    "MAX_DRAWING_SOURCE_PAGE_SIZE",
    "cached_drawing_source_catalog_state",
    "cache_drawing_source_catalog_state",
    "drawing_source_catalog_page",
    "drawing_source_catalog_is_cached",
    "drawing_source_catalog_state_page",
    "invalidate_drawing_source_catalog_cache",
]
