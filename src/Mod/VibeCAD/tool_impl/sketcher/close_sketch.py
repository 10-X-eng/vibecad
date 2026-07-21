# SPDX-License-Identifier: LGPL-2.1-or-later

"""Finish the active native Sketcher edit session."""

from __future__ import annotations

from typing import Any

from VibeCADEditState import active_edit_state


TOOL_SPEC = {
    "name": "sketcher.close_sketch",
    "description": (
        "Finish the one native Sketcher sketch currently open for editing, preserve its "
        "authored geometry and constraints, recompute the active document, and return the "
        "closed sketch's live profile and solver diagnostics. This is the native equivalent "
        "of accepting Sketcher's Close/OK action. It closes only the active sketch and never "
        "claims that the profile is valid, fully constrained, or ready for a downstream "
        "feature. After success, the live provider surface refreshes in the same run so the "
        "next appropriate PartDesign operations become available. Takes no arguments."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "SketcherWorkbench",
    "edit_modes": ["sketch"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    document = service._active_document()
    if document is None:
        return _invalid("No active document. Open a saved document in FreeCAD first.")

    try:
        import FreeCADGui as Gui
    except Exception as exc:
        return _invalid(
            f"FreeCAD GUI is unavailable; the active sketch cannot be closed: {exc}"
        )

    gui_document = getattr(Gui, "ActiveDocument", None)
    if gui_document is None:
        return _invalid(
            "The active FreeCAD document has no GUI document for sketch editing."
        )
    gui_app_document = getattr(gui_document, "Document", None)
    if gui_app_document is not None and getattr(
        gui_app_document, "Name", None
    ) != getattr(document, "Name", None):
        return _invalid(
            "The active GUI document does not match the active application document.",
            application_document=getattr(document, "Name", None),
            gui_document=getattr(gui_app_document, "Name", None),
        )

    get_in_edit = getattr(gui_document, "getInEdit", None)
    reset_edit = getattr(gui_document, "resetEdit", None)
    if not callable(get_in_edit) or not callable(reset_edit):
        return _invalid(
            "The active FreeCAD GUI document does not expose native sketch edit controls."
        )

    edit_state = active_edit_state(gui_document)
    sketch = edit_state.document_object
    if not edit_state.active:
        if edit_state.error:
            return _invalid(
                f"FreeCAD edit state could not be verified: {edit_state.error}"
            )
        return _invalid("No Sketcher sketch is currently open for editing.")
    if getattr(sketch, "TypeId", "") != "Sketcher::SketchObject":
        return _invalid(
            "The active edit object is not a native Sketcher sketch.",
            active_edit_object={
                "name": getattr(sketch, "Name", None),
                "type": getattr(sketch, "TypeId", None),
            },
        )
    if getattr(sketch, "Document", None) is not document:
        return _invalid(
            "The active edit sketch does not belong to the active document.",
            active_sketch=getattr(sketch, "Name", None),
        )

    sketch_name = str(getattr(sketch, "Name", "") or "")
    sketch_label = str(getattr(sketch, "Label", sketch_name) or sketch_name)
    owner_body = _owner_body_name(sketch)
    before = service._cad_state_sketch_summary(sketch, edit_mode=True)

    try:
        reset_edit()
    except Exception as exc:
        return _invalid(
            f"FreeCAD failed to close sketch {sketch_name}: {exc}",
            active_sketch=sketch_name,
            sketch_snapshot=before,
        )

    active_after_state = active_edit_state(gui_document)
    active_after = active_after_state.document_object
    if active_after_state.active:
        return _invalid(
            f"Sketch {sketch_name} left edit mode, but FreeCAD retained another active edit object.",
            closed_sketch=sketch_name,
            closed=True,
            edit_state_error=active_after_state.error or None,
            active_edit_object={
                "name": getattr(active_after, "Name", None),
                "label": getattr(
                    active_after,
                    "Label",
                    getattr(active_after, "Name", None),
                ),
                "type": getattr(active_after, "TypeId", None),
            },
        )

    try:
        document.recompute()
    except Exception as exc:
        return _invalid(
            f"Sketch {sketch_name} closed, but the active document failed to recompute: {exc}",
            closed_sketch=sketch_name,
            closed=True,
        )

    diagnostics = service.recompute_diagnostics()
    snapshot = service._cad_state_sketch_summary(sketch, edit_mode=False)
    return {
        "ok": True,
        "title": f"Sketch {sketch_name} closed",
        "edit_session": {
            "document": getattr(document, "Name", None),
            "sketch": sketch_name,
            "sketch_label": sketch_label,
            "owner_body": owner_body,
            "is_open": False,
            "closed_now": True,
            "active_workbench": service.active_workbench_name(),
        },
        "sketch_snapshot": snapshot,
        "native_diagnostics": diagnostics,
    }


def _owner_body_name(sketch: Any) -> str | None:
    parent_getter = getattr(sketch, "getParentGeoFeatureGroup", None)
    owner = parent_getter() if callable(parent_getter) else None
    if getattr(owner, "TypeId", "") != "PartDesign::Body":
        return None
    return str(getattr(owner, "Name", "") or "") or None


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": message,
        "retry_same_call": False,
        **details,
    }
