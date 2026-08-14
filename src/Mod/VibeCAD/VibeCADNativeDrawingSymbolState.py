# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact bounded state for Drawing surface-finish and weld symbols."""

from __future__ import annotations

import hashlib
import json
import math
from pathlib import Path
from typing import Any

from VibeCADNativeDrawingLeaderState import drawing_leader_state


MAX_DRAWING_SYMBOL_TEXT_CHARACTERS = 256


class NativeDrawingSymbolStateError(RuntimeError):
    """A durable Drawing symbol is malformed or detached."""


def _digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(
            value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()


def _derived(obj: Any, type_id: str) -> bool:
    checker = getattr(obj, "isDerivedFrom", None)
    try:
        return bool(callable(checker) and checker(type_id))
    except Exception:
        return False


def _number(value: Any, noun: str) -> float:
    raw = getattr(value, "Value", value)
    try:
        result = float(raw)
    except (TypeError, ValueError) as exc:
        raise NativeDrawingSymbolStateError(
            f"Drawing symbol {noun} is not numeric."
        ) from exc
    if not math.isfinite(result) or abs(result) > 1_000_000.0:
        raise NativeDrawingSymbolStateError(
            f"Drawing symbol {noun} is outside the supported range."
        )
    rounded = round(result, 12)
    return 0.0 if rounded == 0.0 else rounded


def _timeline_usable(obj: Any) -> bool:
    checker = getattr(
        getattr(obj, "Document", None),
        "isObjectUsableAtCurrentTimelinePosition",
        None,
    )
    try:
        return bool(checker(obj)) if callable(checker) else True
    except Exception:
        return False


def _page(obj: Any) -> Any:
    document = getattr(obj, "Document", None)
    page = obj.findParentPage()
    if (
        document is None
        or page is None
        or getattr(page, "Document", None) is not document
        or obj not in tuple(getattr(page, "Views", ()) or ())
    ):
        raise NativeDrawingSymbolStateError(
            "Drawing symbol is not attached to one live page."
        )
    return page


def _file_digest(value: Any, noun: str) -> str:
    raw = str(value or "")
    if not raw:
        raise NativeDrawingSymbolStateError(
            f"Drawing weld tile has no {noun}."
        )
    try:
        path = Path(raw)
        if path.is_file():
            content = path.read_bytes()
        else:
            content = raw.encode("utf-8")
    except (OSError, UnicodeError) as exc:
        raise NativeDrawingSymbolStateError(
            f"Drawing weld tile {noun} cannot be inspected."
        ) from exc
    return hashlib.sha256(content).hexdigest()


def is_drawing_surface_finish_symbol(obj: Any) -> bool:
    return _derived(obj, "TechDraw::DrawViewSymbol")


def is_drawing_weld_symbol(obj: Any) -> bool:
    return _derived(obj, "TechDraw::DrawWeldSymbol")


def drawing_surface_finish_symbol_state(symbol: Any) -> dict[str, Any]:
    if not is_drawing_surface_finish_symbol(symbol):
        raise TypeError("symbol must be a TechDraw::DrawViewSymbol")
    page = _page(symbol)
    owner = getattr(symbol, "Owner", None)
    if owner is not None and (
        getattr(owner, "Document", None) is not symbol.Document
        or owner.findParentPage() is not page
    ):
        raise NativeDrawingSymbolStateError(
            "Drawing surface-finish symbol has a detached owner."
        )
    svg = str(getattr(symbol, "Symbol", "") or "")
    if not svg or len(svg) > 32 * 1024:
        raise NativeDrawingSymbolStateError(
            "Drawing surface-finish SVG is empty or exceeds 32768 characters."
        )
    exact = {
        "object_name": str(symbol.Name),
        "label": str(symbol.Label or "")[:160],
        "page_name": str(page.Name),
        "owner_name": str(getattr(owner, "Name", "") or "") or None,
        "placement_on_page_mm": {
            "x_mm": _number(symbol.X, "X coordinate"),
            "y_mm": _number(symbol.Y, "Y coordinate"),
        },
        "rotation_degrees": _number(symbol.Rotation, "rotation"),
        "svg_sha256": hashlib.sha256(svg.encode("utf-8")).hexdigest(),
        "svg_characters": len(svg),
        "timeline_usable": _timeline_usable(symbol),
        "valid": bool(symbol.isValid()),
    }
    return {**exact, "symbol_state_sha256": _digest(exact)}


def _weld_tile_state(tile: Any) -> dict[str, Any]:
    if not _derived(tile, "TechDraw::DrawTileWeld"):
        raise NativeDrawingSymbolStateError(
            "Drawing weld symbol owns an incompatible tile."
        )
    texts = {
        "left": str(getattr(tile, "LeftText", "") or ""),
        "center": str(getattr(tile, "CenterText", "") or ""),
        "right": str(getattr(tile, "RightText", "") or ""),
    }
    if any(len(value) > MAX_DRAWING_SYMBOL_TEXT_CHARACTERS for value in texts.values()):
        raise NativeDrawingSymbolStateError(
            "Drawing weld tile text exceeds 256 characters."
        )
    exact = {
        "object_name": str(tile.Name),
        "row": int(tile.TileRow),
        "column": int(tile.TileColumn),
        "text": texts,
        "source_svg_sha256": _file_digest(tile.SymbolFile, "source SVG"),
        "embedded_svg_sha256": _file_digest(tile.SymbolIncluded, "embedded SVG"),
        "timeline_role": str(getattr(tile, "VibeCADTimelineRole", "") or ""),
        "timeline_owner_name": str(
            getattr(getattr(tile, "VibeCADTimelineOwner", None), "Name", "") or ""
        ),
        "timeline_usable": _timeline_usable(tile),
        "valid": bool(tile.isValid()),
    }
    return {**exact, "tile_state_sha256": _digest(exact)}


def _weld_tiles(symbol: Any) -> tuple[Any, ...]:
    document = getattr(symbol, "Document", None)
    if document is None:
        return ()
    result = []
    for candidate in tuple(getattr(document, "Objects", ()) or ()):
        if not _derived(candidate, "TechDraw::DrawTileWeld"):
            continue
        parent = getattr(candidate, "TileParent", None)
        if (
            parent is not None
            and getattr(parent, "Document", None) is document
            and str(getattr(parent, "Name", "") or "") == str(symbol.Name)
        ):
            result.append(candidate)
    return tuple(result)


def drawing_weld_symbol_state(symbol: Any) -> dict[str, Any]:
    if not is_drawing_weld_symbol(symbol):
        raise TypeError("symbol must be a TechDraw::DrawWeldSymbol")
    page = _page(symbol)
    leader = getattr(symbol, "Leader", None)
    if leader is None or leader.findParentPage() is not page:
        raise NativeDrawingSymbolStateError(
            "Drawing weld symbol has no leader on its page."
        )
    leader_state = drawing_leader_state(leader)
    tiles = [_weld_tile_state(tile) for tile in _weld_tiles(symbol)]
    tiles.sort(key=lambda item: item["row"], reverse=True)
    if [item["row"] for item in tiles] != [0, -1]:
        raise NativeDrawingSymbolStateError(
            "Drawing weld symbol must retain arrow-side and other-side tiles."
        )
    tail = str(getattr(symbol, "TailText", "") or "")
    if len(tail) > MAX_DRAWING_SYMBOL_TEXT_CHARACTERS:
        raise NativeDrawingSymbolStateError(
            "Drawing weld tail text exceeds 256 characters."
        )
    exact = {
        "object_name": str(symbol.Name),
        "label": str(symbol.Label or "")[:160],
        "page_name": str(page.Name),
        "leader": {
            "object_name": leader_state["object_name"],
            "leader_state_sha256": leader_state["leader_state_sha256"],
        },
        "all_around": bool(symbol.AllAround),
        "field_weld": bool(symbol.FieldWeld),
        "alternating_weld": bool(symbol.AlternatingWeld),
        "tail_text": tail,
        "tiles": tiles,
        "timeline_role": str(getattr(symbol, "VibeCADTimelineRole", "") or ""),
        "timeline_usable": _timeline_usable(symbol),
        "valid": bool(symbol.isValid()),
    }
    return {**exact, "symbol_state_sha256": _digest(exact)}
