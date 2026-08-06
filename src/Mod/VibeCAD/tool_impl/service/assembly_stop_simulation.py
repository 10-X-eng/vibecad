# SPDX-License-Identifier: LGPL-2.1-or-later

"""Stop the exact active read-only Assembly simulation playback task."""

from __future__ import annotations

from typing import Any


TOOL_SPEC = {
    "name": "assembly.stop_simulation",
    "description": (
        "Stop the active saved Assembly simulation and close its native player. "
        "This restores the exact placements, visibility, and camera captured "
        "when playback started. It never closes a different native task."
    ),
    "contextual": False,
    "requires_document": True,
    "safety": "VIEW",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {},
        "additionalProperties": False,
    },
}


def run(service: Any) -> dict[str, Any]:
    del service
    try:
        import FreeCAD as App

        if not bool(App.GuiUp):
            raise RuntimeError("Assembly simulation playback requires the GUI")
        import FreeCADGui as Gui

        dialog = Gui.Control.activeTaskDialog()
        if dialog is None:
            return {
                "ok": True,
                "stopped": False,
                "playing": False,
                "restored": True,
                "message": "No saved Assembly simulation is playing.",
            }

        content = list(dialog.getDialogContent())
        pending = list(content)
        task_widgets = []
        seen = set()
        while pending:
            widget = pending.pop()
            identity = id(widget)
            if identity in seen:
                continue
            seen.add(identity)
            task_widgets.append(widget)
            children = getattr(widget, "children", None)
            if callable(children):
                pending.extend(children())
        playback_widget = next(
            (
                widget
                for widget in task_widgets
                if bool(
                    widget.property(
                        "vibecadSavedAssemblySimulationPlayback"
                    )
                )
            ),
            None,
        )
        if playback_widget is None:
            return {
                "ok": False,
                "failure_code": "ACTIVE_TASK_NOT_SIMULATION_PLAYBACK",
                "failure_stage": "precondition",
                "error": (
                    "The active native task is not a saved Assembly simulation "
                    "player. Close or finish it with its own controls."
                ),
            }

        simulation_reference = {
            "document_uid": str(
                playback_widget.property("vibecadSimulationDocumentUid")
                or ""
            ),
            "object_name": str(
                playback_widget.property("vibecadSimulationObjectName")
                or ""
            ),
        }
        dialog.reject()
        Gui.updateGui()
        if Gui.Control.activeTaskDialog() is dialog:
            return {
                "ok": False,
                "failure_code": "SIMULATION_PLAYBACK_REMAINED_OPEN",
                "failure_stage": "native_call",
                "error": "The saved Assembly simulation player did not close.",
            }
        return {
            "ok": True,
            "stopped": True,
            "playing": False,
            "restored": True,
            "simulation": simulation_reference,
            "restored_state": ["placements", "visibility", "camera"],
        }
    except Exception as exc:
        return {
            "ok": False,
            "failure_code": "SIMULATION_STOP_FAILED",
            "failure_stage": "native_call",
            "error": str(exc),
        }
