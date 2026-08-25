# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact selection and isolation presentation for FEM assignments."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from typing import Any

from VibeCADNativeAnalyzeAssignments import (
    PreparedAssignmentTarget,
    assignment_records,
)
from VibeCADNativeAnalyzeErrors import NativeAnalyzeError


@dataclass(frozen=True, slots=True)
class _IsolationState:
    token: str
    document_uid: str
    visibility: tuple[tuple[str, bool], ...]
    selection: tuple[tuple[str, tuple[str, ...]], ...]


_ISOLATIONS: dict[str, _IsolationState] = {}


def _document(target: PreparedAssignmentTarget) -> Any:
    if not isinstance(target, PreparedAssignmentTarget):
        raise TypeError("target must be a PreparedAssignmentTarget")
    return target.analysis_target.analysis.Document


def _source_names(analysis: Any) -> tuple[str, ...]:
    names = []
    for record in assignment_records(analysis):
        if record.get("valid") is False:
            continue
        for reference in record.get("references") or ():
            name = str(reference.get("object_name") or "")
            if name and name not in names:
                names.append(name)
    return tuple(names)


def _selection_snapshot(document: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    import FreeCADGui as Gui

    result = []
    for item in tuple(Gui.Selection.getSelectionEx(str(document.Name)) or ()):
        name = str(getattr(item, "ObjectName", "") or "")
        if name:
            result.append(
                (name, tuple(str(value) for value in item.SubElementNames or ()))
            )
    return tuple(result)


def _apply_selection(
    document: Any, values: tuple[tuple[str, tuple[str, ...]], ...]
) -> None:
    import FreeCADGui as Gui

    Gui.Selection.clearSelection()
    for object_name, subelements in values:
        if subelements:
            for subelement in subelements:
                Gui.Selection.addSelection(str(document.Name), object_name, subelement)
        else:
            Gui.Selection.addSelection(str(document.Name), object_name)


def _assignment_selection(
    target: PreparedAssignmentTarget,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    references = tuple(target.record.get("references") or ())
    if not references:
        return ((str(target.assignment.Name), ()),)
    return tuple(
        (
            str(reference["object_name"]),
            tuple(str(value) for value in reference.get("subelements") or ()),
        )
        for reference in references
    )


def highlight_assignment(target: PreparedAssignmentTarget) -> dict[str, Any]:
    document = _document(target)
    selection = _assignment_selection(target)
    _apply_selection(document, selection)
    return {
        "assignment": {
            "object_name": str(target.assignment.Name),
            "category": str(target.record["category"]),
        },
        "highlighted": [
            {"object_name": name, "subelements": list(subelements)}
            for name, subelements in selection
        ],
    }


def isolate_assignment(target: PreparedAssignmentTarget) -> dict[str, Any]:
    document = _document(target)
    document_uid = str(document.Uid)
    source_names = _source_names(target.analysis_target.analysis)
    selected_names = {
        str(reference["object_name"])
        for reference in target.record.get("references") or ()
    }
    if not selected_names:
        raise NativeAnalyzeError(
            "This assignment has no geometry targets to isolate.",
            error_code="NATIVE_ANALYZE_PRESENTATION_INVALID",
        )
    state = _ISOLATIONS.get(document_uid)
    if state is None:
        visibility = []
        for name in source_names:
            obj = document.getObject(name)
            view = getattr(obj, "ViewObject", None) if obj is not None else None
            if view is not None:
                visibility.append((name, bool(view.Visibility)))
        state = _IsolationState(
            token=secrets.token_hex(16),
            document_uid=document_uid,
            visibility=tuple(visibility),
            selection=_selection_snapshot(document),
        )
        _ISOLATIONS[document_uid] = state
    for name in source_names:
        obj = document.getObject(name)
        view = getattr(obj, "ViewObject", None) if obj is not None else None
        if view is not None:
            view.Visibility = name in selected_names
    highlighted = highlight_assignment(target)
    return {
        **highlighted,
        "isolated": True,
        "restore_token": state.token,
    }


def restore_assignment_view(document: Any, token: Any) -> dict[str, Any]:
    document_uid = str(getattr(document, "Uid", "") or "")
    state = _ISOLATIONS.get(document_uid)
    requested = str(token or "")
    if (
        state is None
        or not requested
        or not secrets.compare_digest(state.token, requested)
    ):
        raise NativeAnalyzeError(
            "restore_token does not match the active assignment isolation.",
            error_code="NATIVE_ANALYZE_PRESENTATION_STATE_STALE",
        )
    restored = []
    for name, visible in state.visibility:
        obj = document.getObject(name)
        view = getattr(obj, "ViewObject", None) if obj is not None else None
        if view is None:
            continue
        view.Visibility = visible
        restored.append(name)
    _apply_selection(document, state.selection)
    del _ISOLATIONS[document_uid]
    return {"isolated": False, "restored_objects": restored}


def active_isolation_token(document: Any) -> str | None:
    state = _ISOLATIONS.get(str(getattr(document, "Uid", "") or ""))
    return state.token if state is not None else None
