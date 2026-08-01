# SPDX-License-Identifier: LGPL-2.1-or-later

"""Open and play one exact retained native Assembly simulation."""

from __future__ import annotations

from typing import Any


TOOL_SPEC = {
    "name": "assembly.play_simulation",
    "description": (
        "Open an exact saved Assembly simulation in FreeCAD's native player, "
        "regenerate its kinematic frames, and start forward playback. Playback is "
        "temporary; closing the task restores the solved component placements."
    ),
    "contextual": False,
    "requires_document": True,
    "safety": "VIEW",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "simulation": {
                "type": "object",
                "description": "Exact simulation reference returned by VibeScript publication.",
                "properties": {
                    "document_uid": {"type": "string", "minLength": 1},
                    "object_name": {"type": "string", "minLength": 1},
                    "document_path": {"type": "string", "minLength": 1},
                },
                "required": ["document_uid", "object_name"],
                "additionalProperties": False,
            }
        },
        "required": ["simulation"],
        "additionalProperties": False,
    },
}


def run(service: Any, simulation: dict[str, Any]) -> dict[str, Any]:
    try:
        import FreeCAD as App

        if not bool(App.GuiUp):
            raise RuntimeError("Assembly simulation playback requires the GUI")
        import FreeCADGui as Gui

        if Gui.Control.activeTaskDialog() is not None:
            return {
                "ok": False,
                "failure_code": "NATIVE_TASK_ACTIVE",
                "failure_stage": "precondition",
                "error": "Close the active task before playing a simulation.",
            }
        from VibeCADDocumentReferences import resolve_reference_target

        obj = resolve_reference_target(
            service._active_document(),
            simulation,
            "Assembly simulation",
            open_missing=False,
        )
        if str(getattr(obj, "VibeCADVibeScriptOutputType", "") or "") != "simulation":
            raise RuntimeError("The referenced object is not a VibeScript simulation output")
        from CommandCreateSimulation import openSimulation

        openSimulation(obj, autoplay=True)
        assembly = obj.Proxy.getAssembly(obj)
        return {
            "ok": True,
            "simulation": dict(simulation),
            "assembly": {
                "document_uid": str(getattr(obj.Document, "Uid", "") or ""),
                "object_name": str(getattr(assembly, "Name", "") or ""),
            },
            "playing": True,
            "placement_policy": "restored_when_task_closes",
        }
    except Exception as exc:
        return {
            "ok": False,
            "failure_code": "SIMULATION_PLAYBACK_FAILED",
            "failure_stage": "native_call",
            "error": str(exc),
        }
