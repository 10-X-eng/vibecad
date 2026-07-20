# SPDX-License-Identifier: LGPL-2.1-or-later

"""Enter native edit mode for one exact existing PartDesign sketch."""

from __future__ import annotations

from typing import Any

from VibeCADEditState import active_edit_state

TOOL_SPEC = {
    "name": "partdesign.edit_sketch",
    "description": (
        "Open one exact existing native Sketcher sketch for geometry and constraint editing. "
        "The sketch must already exist: create it first with partdesign.create_sketch when "
        "authoring a new profile, then pass the returned internal sketch name to this tool. "
        "After this succeeds, the live tool surface refreshes within the same provider run and "
        "exposes the Sketcher geometry, constraint, measurement, and editing tools needed to "
        "author or modify the profile. Requires that nothing else is already in edit mode. "
        "Never creates or closes a sketch and never activates a workbench; success is verified "
        "from FreeCAD's native active edit object."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "sketch_name": {
                "type": "string",
                "minLength": 1,
                "description": (
                    "Exact internal Name of an existing Sketcher::SketchObject in the active "
                    "document. Labels and implicit first-sketch selection are not accepted."
                ),
            },
        },
        "required": ["sketch_name"],
        "additionalProperties": False,
    },
}


def run(service: Any, sketch_name: str) -> dict[str, Any]:
    requested_name = str(sketch_name or "").strip()
    if not requested_name:
        return _invalid("sketch_name is required.")

    document = service._active_document()
    if document is None:
        return _invalid(
            "No active document. Create or open a document in FreeCAD first."
        )
    sketch = document.getObject(requested_name)
    if sketch is None:
        return _invalid(
            f"Sketch not found by exact internal name: {requested_name}",
            requested_sketch=requested_name,
        )
    if getattr(sketch, "TypeId", "") != "Sketcher::SketchObject":
        return _invalid(
            f"Object {requested_name} is {getattr(sketch, 'TypeId', 'unknown')}, not a native Sketcher sketch.",
            requested_sketch=requested_name,
            object_type=getattr(sketch, "TypeId", None),
        )
    if getattr(sketch, "Document", None) is not document:
        return _invalid(
            f"Sketch {requested_name} does not belong to the active document.",
            requested_sketch=requested_name,
        )

    try:
        import FreeCADGui as Gui
    except Exception as exc:
        return _invalid(
            f"FreeCAD GUI is unavailable; the sketch cannot enter edit mode: {exc}"
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
    set_edit = getattr(gui_document, "setEdit", None)
    if not callable(get_in_edit) or not callable(set_edit):
        return _invalid(
            "The active FreeCAD GUI document does not expose native sketch edit controls."
        )

    current_state = active_edit_state(gui_document)
    current = current_state.document_object
    if _same_document_object(current, sketch):
        return _success_response(service, document, sketch, opened_now=False)
    if current_state.active:
        return _invalid(
            f"Cannot open sketch {sketch.Name}; {getattr(current, 'Name', 'another object')} "
            "is already in edit mode. The human must finish or close that edit session first.",
            requested_sketch=sketch.Name,
            active_edit_object={
                "name": getattr(current, "Name", None),
                "label": getattr(current, "Label", getattr(current, "Name", None)),
                "type": getattr(current, "TypeId", None),
            },
        )
    if current_state.error:
        return _invalid(
            f"FreeCAD edit state could not be verified: {current_state.error}",
            requested_sketch=sketch.Name,
        )

    control = getattr(Gui, "Control", None)
    active_dialog = getattr(control, "activeDialog", None)
    if not callable(active_dialog):
        return _invalid(
            "FreeCAD GUI task state is unavailable; refusing to enter sketch edit mode."
        )
    try:
        task_dialog_active = bool(active_dialog())
    except Exception as exc:
        return _invalid(f"FreeCAD GUI task state could not be read: {exc}")
    if task_dialog_active:
        return _invalid(
            f"Cannot open sketch {sketch.Name}; another FreeCAD Tasks dialog is active. "
            "The human must finish or close that task first.",
            requested_sketch=sketch.Name,
            active_task_dialog=True,
        )

    try:
        set_edit(sketch.Name)
    except Exception as exc:
        return _invalid(
            f"FreeCAD failed to open sketch {sketch.Name} for editing: {exc}",
            requested_sketch=sketch.Name,
        )

    active_after_state = active_edit_state(gui_document)
    active_after = active_after_state.document_object
    if not _same_document_object(active_after, sketch):
        return _invalid(
            f"FreeCAD did not retain sketch {sketch.Name} as the active edit object.",
            requested_sketch=sketch.Name,
            edit_state_error=active_after_state.error or None,
            active_edit_object={
                "name": getattr(active_after, "Name", None),
                "type": getattr(active_after, "TypeId", None),
            }
            if active_after is not None
            else None,
        )
    return _success_response(service, document, sketch, opened_now=True)


def _same_document_object(first: Any, second: Any) -> bool:
    if first is None or second is None:
        return False
    if getattr(first, "Name", None) != getattr(second, "Name", None):
        return False
    first_document = getattr(getattr(first, "Document", None), "Name", None)
    second_document = getattr(getattr(second, "Document", None), "Name", None)
    return first_document == second_document


def _owner_body_name(sketch: Any) -> str | None:
    parent_getter = getattr(sketch, "getParentGeoFeatureGroup", None)
    owner = parent_getter() if callable(parent_getter) else None
    if getattr(owner, "TypeId", "") != "PartDesign::Body":
        return None
    return str(getattr(owner, "Name", "") or "") or None


def _success_response(
    service: Any,
    document: Any,
    sketch: Any,
    *,
    opened_now: bool,
) -> dict[str, Any]:
    sketch_snapshot = service._cad_state_sketch_summary(sketch, edit_mode=True)
    return {
        "ok": True,
        "title": f"Sketch {sketch.Name} is open for editing",
        "edit_session": {
            "document": getattr(document, "Name", None),
            "sketch": sketch.Name,
            "sketch_label": getattr(sketch, "Label", sketch.Name),
            "owner_body": _owner_body_name(sketch),
            "is_open": True,
            "opened_now": opened_now,
            "active_workbench": service.active_workbench_name(),
            "geometry_count": len(list(getattr(sketch, "Geometry", []) or [])),
            "constraint_count": len(list(getattr(sketch, "Constraints", []) or [])),
        },
        "sketch_snapshot": sketch_snapshot,
    }


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": message,
        "retry_same_call": False,
        **details,
    }
