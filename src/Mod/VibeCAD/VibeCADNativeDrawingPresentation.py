# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact guarded Drawing page presentation operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

from VibeCADNativeDrawingDimensionSupport import (
    drawing_dimension_error,
    drawing_object_key,
    drawing_selection_state,
    drawing_timeline_operations,
    drawing_visibility_state,
    exact_drawing_mapping,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingPresentationState import (
    drawing_page_presentation_state,
    drawing_frame_visibility_state,
    drawing_grid_visibility_state,
    drawing_hidden_edge_visibility_state,
    normalize_drawing_frame_visibility_plan,
    normalize_drawing_grid_visibility_plan,
    normalize_drawing_hidden_edge_visibility_plan,
    show_drawing_page,
)
from VibeCADNativeDrawingState import drawing_page_state
from VibeCADNativeDrawingViewState import drawing_view_state
from VibeCADNativeRuntimeContext import NativeRuntimeContext
from VibeCADNativeTargets import object_reference, resolve_object


_PAGE_FIELDS = frozenset(
    {
        "object_name",
        "expected_state_sha256",
        "expected_frame_visibility_state_sha256",
    }
)
_SHOW_PAGE_FIELDS = frozenset({"object_name", "expected_state_sha256"})
_GRID_PAGE_FIELDS = frozenset(
    {
        "object_name",
        "expected_state_sha256",
        "expected_grid_visibility_state_sha256",
    }
)
_VIEW_FIELDS = frozenset(
    {
        "object_name",
        "expected_state_sha256",
        "expected_hidden_edge_visibility_state_sha256",
    }
)


@dataclass(frozen=True, slots=True)
class PreparedDrawingFrameVisibility:
    page: Any
    visible: bool
    frame_state_before: dict[str, Any]
    page_state_before: dict[str, Any]
    host_validation: dict[str, Any]
    objects_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]
    undo_count_before: int
    transaction_before: int


@dataclass(frozen=True, slots=True)
class PreparedShowDrawing:
    page: Any
    page_state_before: dict[str, Any]
    presentation_before: dict[str, Any]
    objects_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]
    undo_count_before: int
    transaction_before: int


@dataclass(frozen=True, slots=True)
class PreparedDrawingGridVisibility:
    page: Any
    visible: bool
    presentation_before: dict[str, Any]
    page_state_before: dict[str, Any]
    host_validation: dict[str, Any]
    objects_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]
    undo_count_before: int
    transaction_before: int


@dataclass(frozen=True, slots=True)
class PreparedDrawingHiddenEdgeVisibility:
    page: Any
    view: Any
    visible: bool
    presentation_before: dict[str, Any]
    page_state_before: dict[str, Any]
    view_state_before: dict[str, Any]
    host_validation: dict[str, Any]
    objects_before: tuple[Any, ...]
    page_views_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]
    undo_count_before: int
    transaction_before: int


def _error(
    message: str,
    code: str,
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    drawing_dimension_error(message, code, repair=repair)


def prepare_show_drawing(
    context: NativeRuntimeContext,
    values: Mapping[str, Any],
) -> PreparedShowDrawing:
    exact = exact_drawing_mapping(
        values["page"],
        _SHOW_PAGE_FIELDS,
        "page",
        family="presentation",
        error_code="NATIVE_DRAWING_PRESENTATION_PARAMETERS_INVALID",
    )
    page = resolve_object(
        context.document,
        {
            "document_uid": context.document_uid,
            "object_name": str(exact["object_name"]),
        },
        expected_types=("TechDraw::DrawPage",),
    )
    state = drawing_page_state(page)
    if str(exact["expected_state_sha256"]) != state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PRESENTATION_PAGE_STALE",
            repair={"current_state_sha256": state["state_sha256"]},
        )
    checker = getattr(context.document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(page)):
        _error(
            "The exact Drawing page is unavailable at the current History position.",
            "NATIVE_DRAWING_PRESENTATION_PAGE_UNAVAILABLE",
        )
    try:
        presentation = drawing_page_presentation_state(page)
    except Exception as exc:
        _error(
            f"The exact Drawing page cannot be presented: {str(exc).strip()}",
            "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE",
        )
    document = context.document
    return PreparedShowDrawing(
        page=page,
        page_state_before=state,
        presentation_before=presentation,
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views or ()),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
        undo_count_before=int(document.UndoCount),
        transaction_before=int(document.getBookedTransactionID()),
    )


def _show_visibility_changes(prepared: PreparedShowDrawing) -> list[dict[str, Any]]:
    presentation_objects = {
        prepared.page,
        getattr(prepared.page, "Template", None),
    }
    before = {
        drawing_object_key(obj): visible
        for obj, visible in prepared.visibility_before
        if obj not in presentation_objects
    }
    after = {
        drawing_object_key(obj): visible
        for obj, visible in drawing_visibility_state(prepared.page.Document)
        if obj not in presentation_objects
    }
    return [
        {
            "object_name": key[1],
            "before": before.get(key),
            "after": after.get(key),
        }
        for key in sorted(set(before) | set(after))
        if before.get(key) is not after.get(key)
    ][:16]


def show_exact_drawing(
    context: NativeRuntimeContext,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    context.guard()
    prepared = prepare_show_drawing(context, values)
    context.guard()
    try:
        shown = show_drawing_page(prepared.page)
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            f"TechDraw could not show the exact Drawing page: {str(exc).strip()}",
            "NATIVE_DRAWING_PRESENTATION_FAILED",
        )
    context.guard()
    document = context.document
    visibility_changes = _show_visibility_changes(prepared)
    boundaries = {
        "objects": tuple(map(drawing_object_key, document.Objects))
        == tuple(map(drawing_object_key, prepared.objects_before)),
        "page_membership": tuple(
            map(drawing_object_key, tuple(prepared.page.Views or ()))
        )
        == tuple(map(drawing_object_key, prepared.page_views_before)),
        "history": tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        == tuple(map(drawing_object_key, prepared.timeline_before)),
        "selection": drawing_selection_state(document) == prepared.selection_before,
        "other_visibility": not visibility_changes,
        "page_definition": drawing_page_state(prepared.page)
        == prepared.page_state_before,
        "undo": int(document.UndoCount) == prepared.undo_count_before,
        "transaction": int(document.getBookedTransactionID())
        == prepared.transaction_before,
    }
    if not all(boundaries.values()):
        _error(
            "Show Drawing changed persistent Drawing, selection, History, or transaction state.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
            repair={
                "changed_boundaries": [
                    name for name, unchanged in boundaries.items() if not unchanged
                ],
                "visibility_changes": visibility_changes,
            },
        )
    current = drawing_page_presentation_state(prepared.page)
    if not current["open"] or not current["active"]:
        _error(
            "The exact Drawing page did not remain human-active.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    return {
        "operation": "show",
        "page": object_reference(prepared.page),
        "previous_open": shown["previous_open"],
        "previous_active": shown["previous_active"],
        "open": current["open"],
        "active": current["active"],
        "changed": shown["changed"],
    }


def _host_plan(page: Any, visible: bool, *, apply: bool) -> dict[str, Any]:
    try:
        import TechDrawGui

        function = (
            TechDrawGui.changeDrawingFrameVisibility
            if apply
            else TechDrawGui.validateDrawingFrameVisibility
        )
        return normalize_drawing_frame_visibility_plan(function(page, visible))
    except NativeDrawingError:
        raise
    except Exception as exc:
        action = "apply" if apply else "validate"
        _error(
            f"TechDraw could not {action} Drawing frame visibility: {str(exc).strip()}",
            "NATIVE_DRAWING_PRESENTATION_FAILED"
            if apply
            else "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE",
            repair={
                "requirement": (
                    "Open the exact Drawing page and set View Frames Visibility "
                    "to Manual."
                )
            },
        )


def prepare_drawing_frame_visibility(
    context: NativeRuntimeContext,
    values: Mapping[str, Any],
) -> PreparedDrawingFrameVisibility:
    if not isinstance(context, NativeRuntimeContext):
        raise TypeError("context must be a NativeRuntimeContext")
    visible = values["visible"]
    if type(visible) is not bool:
        _error(
            "Drawing frame visibility must be a boolean.",
            "NATIVE_DRAWING_PRESENTATION_PARAMETERS_INVALID",
        )
    exact = exact_drawing_mapping(
        values["page"],
        _PAGE_FIELDS,
        "page",
        family="frame visibility",
        error_code="NATIVE_DRAWING_PRESENTATION_PARAMETERS_INVALID",
    )
    page = resolve_object(
        context.document,
        {
            "document_uid": context.document_uid,
            "object_name": str(exact["object_name"]),
        },
        expected_types=("TechDraw::DrawPage",),
    )
    page_state = drawing_page_state(page)
    if str(exact["expected_state_sha256"]) != page_state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PRESENTATION_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    try:
        frame_state = drawing_frame_visibility_state(page)
    except Exception as exc:
        _error(
            f"The exact Drawing page frame state is unavailable: {str(exc).strip()}",
            "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE",
            repair={
                "requirement": (
                    "Open the exact Drawing page and set View Frames Visibility "
                    "to Manual."
                )
            },
        )
    if (
        str(exact["expected_frame_visibility_state_sha256"])
        != frame_state["frame_visibility_state_sha256"]
    ):
        _error(
            "Drawing frame visibility changed after it was inspected.",
            "NATIVE_DRAWING_PRESENTATION_STATE_STALE",
            repair={
                "current_frame_visibility_state_sha256": frame_state[
                    "frame_visibility_state_sha256"
                ],
                "current_visible": frame_state["visible"],
            },
        )
    host_validation = _host_plan(page, visible, apply=False)
    if (
        host_validation["page_name"] != str(page.Name)
        or host_validation["previous_visible"] is not frame_state["visible"]
        or host_validation["visible"] is not visible
        or host_validation["graphical_view_count"]
        != frame_state["graphical_view_count"]
    ):
        _error(
            "TechDraw's frame-visibility plan does not match the exact request.",
            "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE",
        )
    document = context.document
    return PreparedDrawingFrameVisibility(
        page=page,
        visible=visible,
        frame_state_before=frame_state,
        page_state_before=page_state,
        host_validation=host_validation,
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views or ()),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
        undo_count_before=int(document.UndoCount),
        transaction_before=int(document.getBookedTransactionID()),
    )


def _verify_boundary(
    context: NativeRuntimeContext,
    prepared: PreparedDrawingFrameVisibility,
) -> dict[str, Any]:
    document = context.document
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
    ):
        _error(
            "Frame visibility changed objects, page membership, or History.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    if drawing_selection_state(document) != prepared.selection_before:
        _error(
            "Frame visibility changed the human selection.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    if drawing_visibility_state(document) != prepared.visibility_before:
        _error(
            "Frame visibility changed persistent object visibility.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    if drawing_page_state(prepared.page) != prepared.page_state_before:
        _error(
            "Frame visibility changed the Drawing page definition.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    if (
        int(document.UndoCount) != prepared.undo_count_before
        or int(document.getBookedTransactionID()) != prepared.transaction_before
    ):
        _error(
            "Frame visibility opened a document transaction or undo step.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    current = drawing_frame_visibility_state(prepared.page)
    if (
        current["visible"] is not prepared.visible
        or current["graphical_view_count"]
        != prepared.frame_state_before["graphical_view_count"]
    ):
        _error(
            "The Drawing page did not retain the requested frame visibility.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    return current


def set_drawing_frame_visibility(
    context: NativeRuntimeContext,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    context.guard()
    prepared = prepare_drawing_frame_visibility(context, values)
    context.guard()
    try:
        applied = _host_plan(prepared.page, prepared.visible, apply=True)
        if applied != prepared.host_validation:
            _error(
                "TechDraw applied frame visibility inconsistent with preflight.",
                "NATIVE_DRAWING_PRESENTATION_FAILED",
            )
        context.guard()
        current = _verify_boundary(context, prepared)
    except Exception:
        try:
            now = drawing_frame_visibility_state(prepared.page)
            if now["visible"] is not prepared.frame_state_before["visible"]:
                _host_plan(
                    prepared.page,
                    prepared.frame_state_before["visible"],
                    apply=True,
                )
        except Exception:
            pass
        raise
    return {
        "operation": "set_frame_visibility",
        "page": object_reference(prepared.page),
        "previous_visible": prepared.frame_state_before["visible"],
        "visible": current["visible"],
        "changed": prepared.host_validation["changed"],
        "graphical_view_count": current["graphical_view_count"],
        "frame_visibility_state_sha256": current["frame_visibility_state_sha256"],
    }


def _grid_host_plan(page: Any, visible: bool, *, apply: bool) -> dict[str, Any]:
    try:
        import TechDrawGui

        function = (
            TechDrawGui.changeDrawingGridVisibility
            if apply
            else TechDrawGui.validateDrawingGridVisibility
        )
        return normalize_drawing_grid_visibility_plan(function(page, visible))
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            f"TechDraw could not {'apply' if apply else 'validate'} Drawing grid "
            f"visibility: {str(exc).strip()}",
            "NATIVE_DRAWING_PRESENTATION_FAILED"
            if apply
            else "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE",
            repair={"requirement": "Open the exact Drawing page."},
        )


def prepare_drawing_grid_visibility(
    context: NativeRuntimeContext,
    values: Mapping[str, Any],
) -> PreparedDrawingGridVisibility:
    visible = values["visible"]
    if type(visible) is not bool:
        _error(
            "Drawing grid visibility must be a boolean.",
            "NATIVE_DRAWING_PRESENTATION_PARAMETERS_INVALID",
        )
    exact = exact_drawing_mapping(
        values["page"],
        _GRID_PAGE_FIELDS,
        "page",
        family="grid visibility",
        error_code="NATIVE_DRAWING_PRESENTATION_PARAMETERS_INVALID",
    )
    page = resolve_object(
        context.document,
        {
            "document_uid": context.document_uid,
            "object_name": str(exact["object_name"]),
        },
        expected_types=("TechDraw::DrawPage",),
    )
    page_state = drawing_page_state(page)
    if str(exact["expected_state_sha256"]) != page_state["state_sha256"]:
        _error(
            "The exact Drawing page changed after it was inspected.",
            "NATIVE_DRAWING_PRESENTATION_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    presentation = drawing_grid_visibility_state(page)
    if (
        str(exact["expected_grid_visibility_state_sha256"])
        != presentation["grid_visibility_state_sha256"]
    ):
        _error(
            "Drawing grid visibility changed after it was inspected.",
            "NATIVE_DRAWING_PRESENTATION_STATE_STALE",
            repair={
                "current_grid_visibility_state_sha256": presentation[
                    "grid_visibility_state_sha256"
                ],
                "current_visible": presentation["visible"],
            },
        )
    host_validation = _grid_host_plan(page, visible, apply=False)
    if (
        host_validation["page_name"] != str(page.Name)
        or host_validation["previous_visible"] is not presentation["visible"]
        or host_validation["visible"] is not visible
    ):
        _error(
            "TechDraw's grid-visibility plan does not match the exact request.",
            "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE",
        )
    document = context.document
    return PreparedDrawingGridVisibility(
        page=page,
        visible=visible,
        presentation_before=presentation,
        page_state_before=page_state,
        host_validation=host_validation,
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views or ()),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
        undo_count_before=int(document.UndoCount),
        transaction_before=int(document.getBookedTransactionID()),
    )


def _verify_grid_boundary(
    context: NativeRuntimeContext,
    prepared: PreparedDrawingGridVisibility,
) -> dict[str, Any]:
    document = context.document
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
        or drawing_selection_state(document) != prepared.selection_before
        or drawing_visibility_state(document) != prepared.visibility_before
        or drawing_page_state(prepared.page) != prepared.page_state_before
        or int(document.UndoCount) != prepared.undo_count_before
        or int(document.getBookedTransactionID()) != prepared.transaction_before
    ):
        _error(
            "Grid visibility changed persistent Drawing or transaction state.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    current = drawing_grid_visibility_state(prepared.page)
    if current["visible"] is not prepared.visible:
        _error(
            "The Drawing page did not retain the requested grid visibility.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    return current


def set_drawing_grid_visibility(
    context: NativeRuntimeContext,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    context.guard()
    prepared = prepare_drawing_grid_visibility(context, values)
    context.guard()
    try:
        applied = _grid_host_plan(prepared.page, prepared.visible, apply=True)
        if applied != prepared.host_validation:
            _error(
                "TechDraw applied grid visibility inconsistent with preflight.",
                "NATIVE_DRAWING_PRESENTATION_FAILED",
            )
        context.guard()
        current = _verify_grid_boundary(context, prepared)
    except Exception:
        try:
            _grid_host_plan(
                prepared.page,
                prepared.presentation_before["visible"],
                apply=True,
            )
        except Exception:
            pass
        raise
    return {
        "operation": "set_grid_visibility",
        "page": object_reference(prepared.page),
        "previous_visible": prepared.presentation_before["visible"],
        "visible": current["visible"],
        "changed": prepared.host_validation["changed"],
        "grid_visibility_state_sha256": current["grid_visibility_state_sha256"],
    }


def _hidden_edge_host_plan(
    view: Any,
    visible: bool,
    *,
    apply: bool,
) -> dict[str, Any]:
    try:
        import TechDrawGui

        function = (
            TechDrawGui.changeDrawingHiddenEdgeVisibility
            if apply
            else TechDrawGui.validateDrawingHiddenEdgeVisibility
        )
        return normalize_drawing_hidden_edge_visibility_plan(function(view, visible))
    except NativeDrawingError:
        raise
    except Exception as exc:
        _error(
            f"TechDraw could not {'apply' if apply else 'validate'} hidden-edge "
            f"visibility: {str(exc).strip()}",
            "NATIVE_DRAWING_PRESENTATION_FAILED"
            if apply
            else "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE",
            repair={"requirement": "Open the Drawing page containing the exact view."},
        )


def prepare_drawing_hidden_edge_visibility(
    context: NativeRuntimeContext,
    values: Mapping[str, Any],
) -> PreparedDrawingHiddenEdgeVisibility:
    visible = values["visible"]
    if type(visible) is not bool:
        _error(
            "Hidden-edge visibility must be a boolean.",
            "NATIVE_DRAWING_PRESENTATION_PARAMETERS_INVALID",
        )
    exact = exact_drawing_mapping(
        values["view"],
        _VIEW_FIELDS,
        "view",
        family="hidden-edge visibility",
        error_code="NATIVE_DRAWING_PRESENTATION_PARAMETERS_INVALID",
    )
    view = resolve_object(
        context.document,
        {
            "document_uid": context.document_uid,
            "object_name": str(exact["object_name"]),
        },
        expected_types=("TechDraw::DrawViewPart", "TechDraw::DrawProjGroupItem"),
    )
    page = view.findParentPage()
    if page is None or page.Document is not context.document:
        _error(
            "The exact Drawing view is not attached to a live page.",
            "NATIVE_DRAWING_PRESENTATION_TARGET_INVALID",
        )
    view_state = drawing_view_state(view)
    if str(exact["expected_state_sha256"]) != view_state["state_sha256"]:
        _error(
            "The exact Drawing view changed after it was inspected.",
            "NATIVE_DRAWING_PRESENTATION_VIEW_STALE",
            repair={"current_state_sha256": view_state["state_sha256"]},
        )
    presentation = drawing_hidden_edge_visibility_state(view)
    if (
        str(exact["expected_hidden_edge_visibility_state_sha256"])
        != presentation["hidden_edge_visibility_state_sha256"]
    ):
        _error(
            "Hidden-edge visibility changed after it was inspected.",
            "NATIVE_DRAWING_PRESENTATION_STATE_STALE",
            repair={
                "current_hidden_edge_visibility_state_sha256": presentation[
                    "hidden_edge_visibility_state_sha256"
                ],
                "current_visible": presentation["visible"],
            },
        )
    host_validation = _hidden_edge_host_plan(view, visible, apply=False)
    if (
        host_validation["page_name"] != str(page.Name)
        or host_validation["view_name"] != str(view.Name)
        or host_validation["previous_visible"] is not presentation["visible"]
        or host_validation["visible"] is not visible
    ):
        _error(
            "TechDraw's hidden-edge plan does not match the exact request.",
            "NATIVE_DRAWING_PRESENTATION_UNAVAILABLE",
        )
    document = context.document
    return PreparedDrawingHiddenEdgeVisibility(
        page=page,
        view=view,
        visible=visible,
        presentation_before=presentation,
        page_state_before=drawing_page_state(page),
        view_state_before=view_state,
        host_validation=host_validation,
        objects_before=tuple(document.Objects),
        page_views_before=tuple(page.Views or ()),
        timeline_before=drawing_timeline_operations(document),
        selection_before=drawing_selection_state(document),
        visibility_before=drawing_visibility_state(document),
        undo_count_before=int(document.UndoCount),
        transaction_before=int(document.getBookedTransactionID()),
    )


def _verify_hidden_edge_boundary(
    context: NativeRuntimeContext,
    prepared: PreparedDrawingHiddenEdgeVisibility,
) -> dict[str, Any]:
    document = context.document
    if (
        tuple(map(drawing_object_key, document.Objects))
        != tuple(map(drawing_object_key, prepared.objects_before))
        or tuple(map(drawing_object_key, tuple(prepared.page.Views or ())))
        != tuple(map(drawing_object_key, prepared.page_views_before))
        or tuple(map(drawing_object_key, drawing_timeline_operations(document)))
        != tuple(map(drawing_object_key, prepared.timeline_before))
        or drawing_selection_state(document) != prepared.selection_before
        or drawing_visibility_state(document) != prepared.visibility_before
        or drawing_page_state(prepared.page) != prepared.page_state_before
        or drawing_view_state(prepared.view) != prepared.view_state_before
        or int(document.UndoCount) != prepared.undo_count_before
        or int(document.getBookedTransactionID()) != prepared.transaction_before
    ):
        _error(
            "Hidden-edge presentation changed persistent Drawing or transaction state.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    current = drawing_hidden_edge_visibility_state(prepared.view)
    if current["visible"] is not prepared.visible:
        _error(
            "The Drawing view did not retain the requested hidden-edge visibility.",
            "NATIVE_DRAWING_PRESENTATION_POSTCONDITION_FAILED",
        )
    return current


def set_drawing_hidden_edge_visibility(
    context: NativeRuntimeContext,
    values: Mapping[str, Any],
) -> dict[str, Any]:
    context.guard()
    prepared = prepare_drawing_hidden_edge_visibility(context, values)
    context.guard()
    try:
        applied = _hidden_edge_host_plan(
            prepared.view,
            prepared.visible,
            apply=True,
        )
        if applied != prepared.host_validation:
            _error(
                "TechDraw applied hidden-edge visibility inconsistent with preflight.",
                "NATIVE_DRAWING_PRESENTATION_FAILED",
            )
        context.guard()
        current = _verify_hidden_edge_boundary(context, prepared)
    except Exception:
        try:
            _hidden_edge_host_plan(
                prepared.view,
                prepared.presentation_before["visible"],
                apply=True,
            )
        except Exception:
            pass
        raise
    return {
        "operation": "set_hidden_edges_visible",
        "view": object_reference(prepared.view),
        "previous_visible": prepared.presentation_before["visible"],
        "visible": current["visible"],
        "changed": prepared.host_validation["changed"],
        "hidden_edge_visibility_state_sha256": current[
            "hidden_edge_visibility_state_sha256"
        ],
    }
