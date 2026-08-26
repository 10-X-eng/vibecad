# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact transient presentation state for the human-active Drawing page."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any, Mapping


_OBJECT_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
MAX_DRAWING_GRAPHICAL_VIEWS = 100_000
_FIELDS = frozenset(
    {
        "page_name",
        "previous_visible",
        "visible",
        "changed",
        "graphical_view_count",
    }
)
_PAGE_PRESENTATION_FIELDS = frozenset({"page_name", "open", "active"})
_SHOW_PAGE_RESULT_FIELDS = frozenset(
    {
        "page_name",
        "open",
        "active",
        "previous_open",
        "previous_active",
        "changed",
    }
)


class NativeDrawingPresentationStateError(RuntimeError):
    """The host returned malformed or inconsistent Drawing presentation state."""

    def __init__(
        self,
        message: str,
        *,
        repair: Mapping[str, Any] | None = None,
    ) -> None:
        super().__init__(str(message).strip())
        self.repair = dict(repair or {})

    def failure(self) -> dict[str, Any]:
        result: dict[str, Any] = {
            "error_code": "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE",
            "message": str(self),
        }
        if self.repair:
            result["repair"] = dict(self.repair)
        return result


def _normalize_page_presentation(
    raw: Any,
    *,
    applied: bool,
) -> dict[str, Any]:
    expected = _SHOW_PAGE_RESULT_FIELDS if applied else _PAGE_PRESENTATION_FIELDS
    if not isinstance(raw, Mapping) or frozenset(raw) != expected:
        raise NativeDrawingPresentationStateError(
            "TechDraw returned malformed Drawing-page presentation state."
        )
    page_name = raw["page_name"]
    if (
        not isinstance(page_name, str)
        or len(page_name) > 128
        or _OBJECT_NAME.fullmatch(page_name) is None
    ):
        raise NativeDrawingPresentationStateError(
            "TechDraw returned an invalid Drawing-page identity."
        )
    boolean_fields = ("open", "active")
    if applied:
        boolean_fields += ("previous_open", "previous_active", "changed")
    if any(type(raw[name]) is not bool for name in boolean_fields):
        raise NativeDrawingPresentationStateError(
            "TechDraw returned non-boolean Drawing-page presentation state."
        )
    if raw["active"] and not raw["open"]:
        raise NativeDrawingPresentationStateError(
            "An active Drawing page must have an open page tab."
        )
    result = {"page_name": page_name, "open": raw["open"], "active": raw["active"]}
    if applied:
        if not raw["open"] or not raw["active"]:
            raise NativeDrawingPresentationStateError(
                "Show Drawing did not activate the requested page."
            )
        if raw["changed"] is (raw["previous_active"] is True):
            raise NativeDrawingPresentationStateError(
                "TechDraw returned inconsistent Show Drawing change state."
            )
        result.update(
            previous_open=raw["previous_open"],
            previous_active=raw["previous_active"],
            changed=raw["changed"],
        )
    return result


def drawing_page_presentation_state(page: Any) -> dict[str, Any]:
    import TechDrawGui

    return _normalize_page_presentation(
        TechDrawGui.drawingPagePresentation(page),
        applied=False,
    )


def show_drawing_page(page: Any) -> dict[str, Any]:
    import TechDrawGui

    return _normalize_page_presentation(
        TechDrawGui.showDrawingPage(page),
        applied=True,
    )


def _sha256(value: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        dict(value),
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def normalize_drawing_frame_visibility_plan(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping) or frozenset(raw) != _FIELDS:
        raise NativeDrawingPresentationStateError(
            "TechDraw returned malformed frame-visibility state."
        )
    page_name = raw["page_name"]
    if (
        not isinstance(page_name, str)
        or len(page_name) > 128
        or _OBJECT_NAME.fullmatch(page_name) is None
    ):
        raise NativeDrawingPresentationStateError(
            "TechDraw returned an invalid frame-visibility page identity."
        )
    previous = raw["previous_visible"]
    visible = raw["visible"]
    changed = raw["changed"]
    if any(type(value) is not bool for value in (previous, visible, changed)):
        raise NativeDrawingPresentationStateError(
            "TechDraw returned non-boolean frame visibility."
        )
    if changed is not (previous is not visible):
        raise NativeDrawingPresentationStateError(
            "TechDraw returned inconsistent frame-visibility change state."
        )
    count = raw["graphical_view_count"]
    if type(count) is not int or not 0 <= count <= MAX_DRAWING_GRAPHICAL_VIEWS:
        raise NativeDrawingPresentationStateError(
            "TechDraw returned an unsupported graphical-view count."
        )
    return {
        "page_name": page_name,
        "previous_visible": previous,
        "visible": visible,
        "changed": changed,
        "graphical_view_count": count,
    }


def drawing_frame_visibility_state(page: Any) -> dict[str, Any]:
    import TechDrawGui

    if TechDrawGui.drawingFrameVisibilityAvailable() is not True:
        raise NativeDrawingPresentationStateError(
            "Drawing frame visibility is available only in Manual mode.",
            repair={
                "requirement": (
                    "Open the Drawing page and set View Frames Visibility "
                    "to Manual."
                )
            },
        )
    plan = normalize_drawing_frame_visibility_plan(
        TechDrawGui.drawingFrameVisibility(page)
    )
    if plan["changed"] or plan["previous_visible"] is not plan["visible"]:
        raise NativeDrawingPresentationStateError(
            "TechDraw returned a non-current frame-visibility state."
        )
    exact = {
        "page_name": plan["page_name"],
        "visible": plan["visible"],
        "graphical_view_count": plan["graphical_view_count"],
    }
    return {
        **exact,
        "frame_visibility_state_sha256": _sha256(exact),
    }


def _normalize_boolean_visibility_plan(
    raw: Any,
    *,
    identity_fields: tuple[str, ...],
    noun: str,
) -> dict[str, Any]:
    fields = frozenset((*identity_fields, "previous_visible", "visible", "changed"))
    if not isinstance(raw, Mapping) or frozenset(raw) != fields:
        raise NativeDrawingPresentationStateError(
            f"TechDraw returned malformed {noun} state."
        )
    identities = {}
    for field in identity_fields:
        value = raw[field]
        if (
            not isinstance(value, str)
            or len(value) > 128
            or _OBJECT_NAME.fullmatch(value) is None
        ):
            raise NativeDrawingPresentationStateError(
                f"TechDraw returned an invalid {noun} identity."
            )
        identities[field] = value
    previous = raw["previous_visible"]
    visible = raw["visible"]
    changed = raw["changed"]
    if any(type(value) is not bool for value in (previous, visible, changed)):
        raise NativeDrawingPresentationStateError(
            f"TechDraw returned non-boolean {noun} visibility."
        )
    if changed is not (previous is not visible):
        raise NativeDrawingPresentationStateError(
            f"TechDraw returned inconsistent {noun} change state."
        )
    return {
        **identities,
        "previous_visible": previous,
        "visible": visible,
        "changed": changed,
    }


def normalize_drawing_grid_visibility_plan(raw: Any) -> dict[str, Any]:
    return _normalize_boolean_visibility_plan(
        raw,
        identity_fields=("page_name",),
        noun="grid",
    )


def normalize_drawing_hidden_edge_visibility_plan(raw: Any) -> dict[str, Any]:
    return _normalize_boolean_visibility_plan(
        raw,
        identity_fields=("page_name", "view_name"),
        noun="hidden-edge",
    )


def drawing_grid_visibility_state(page: Any) -> dict[str, Any]:
    import TechDrawGui

    plan = normalize_drawing_grid_visibility_plan(
        TechDrawGui.drawingGridVisibility(page)
    )
    if plan["changed"] or plan["previous_visible"] is not plan["visible"]:
        raise NativeDrawingPresentationStateError(
            "TechDraw returned a non-current grid-visibility state."
        )
    exact = {"page_name": plan["page_name"], "visible": plan["visible"]}
    return {**exact, "grid_visibility_state_sha256": _sha256(exact)}


def drawing_hidden_edge_visibility_state(view: Any) -> dict[str, Any]:
    import TechDrawGui

    plan = normalize_drawing_hidden_edge_visibility_plan(
        TechDrawGui.drawingHiddenEdgeVisibility(view)
    )
    if plan["changed"] or plan["previous_visible"] is not plan["visible"]:
        raise NativeDrawingPresentationStateError(
            "TechDraw returned a non-current hidden-edge visibility state."
        )
    exact = {
        "page_name": plan["page_name"],
        "view_name": plan["view_name"],
        "visible": plan["visible"],
    }
    return {
        **exact,
        "hidden_edge_visibility_state_sha256": _sha256(exact),
    }
