# SPDX-License-Identifier: LGPL-2.1-or-later

"""History-aware validation for whole-object Native Drawing sources."""

from __future__ import annotations

from typing import Any

from VibeCADNativeDrawingErrors import NativeDrawingError


def _is_body(obj: Any) -> bool:
    derived = getattr(obj, "isDerivedFrom", None)
    if callable(derived):
        try:
            return bool(derived("PartDesign::Body"))
        except Exception:
            return False
    return str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body"


def _is_live(document: Any, obj: Any) -> bool:
    name = str(getattr(obj, "Name", "") or "")
    if not name:
        return False
    try:
        return document.getObject(name) is obj
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def is_drawing_source_history_usable(document: Any, source: Any) -> bool:
    """Return whether *source* represents geometry at the current History position.

    A PartDesign Body is a stable presentation boundary and is intentionally
    timeline-internal.  Resolve it only for this availability check so its
    exact active modeling state is validated while the Body itself remains the
    whole-object TechDraw source.
    """

    if document is None or source is None or not _is_live(document, source):
        return False
    timeline_target = source
    if _is_body(source):
        try:
            import PartGui

            timeline_target = PartGui.resolveModelingObject(source)
        except (AttributeError, ImportError, ReferenceError, RuntimeError):
            return False
        if timeline_target is None or not _is_live(document, timeline_target):
            return False
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if not callable(checker):
        return True
    try:
        return bool(checker(timeline_target))
    except (AttributeError, ReferenceError, RuntimeError):
        return False


def require_drawing_source_history_usable(
    document: Any,
    source: Any,
    noun: str = "Drawing source",
) -> None:
    """Raise the shared exact Drawing error for an unavailable source."""

    if not is_drawing_source_history_usable(document, source):
        raise NativeDrawingError(
            f"The exact {noun} is not usable at the current History position.",
            error_code="NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )
