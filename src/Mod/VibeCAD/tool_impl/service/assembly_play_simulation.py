# SPDX-License-Identifier: LGPL-2.1-or-later

"""Open and play one exact retained native Assembly simulation."""

from __future__ import annotations

from typing import Any


TOOL_SPEC = {
    "name": "assembly.play_simulation",
    "description": (
        "Open an exact saved Assembly simulation in FreeCAD's native player, "
        "regenerate its kinematic frames, and start forward playback. Optionally "
        "apply one exact exploded presentation, hide named casing/components, and "
        "frame a standard camera. Playback is temporary; closing the task restores "
        "placements, visibility, and camera."
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
            },
            "presentation": {
                "type": "object",
                "description": (
                    "Optional exact exploded_view reference to compose onto every "
                    "simulation frame."
                ),
                "properties": {
                    "document_uid": {"type": "string", "minLength": 1},
                    "object_name": {"type": "string", "minLength": 1},
                    "document_path": {"type": "string", "minLength": 1},
                },
                "required": ["document_uid", "object_name"],
                "additionalProperties": False,
            },
            "hidden_components": {
                "type": "array",
                "description": (
                    "Exact Assembly component references to hide temporarily during "
                    "playback, such as removable casings."
                ),
                "maxItems": 4096,
                "uniqueItems": True,
                "items": {
                    "type": "object",
                    "properties": {
                        "document_uid": {"type": "string", "minLength": 1},
                        "object_name": {"type": "string", "minLength": 1},
                        "document_path": {"type": "string", "minLength": 1},
                    },
                    "required": ["document_uid", "object_name"],
                    "additionalProperties": False,
                },
            },
            "camera": {
                "type": "string",
                "enum": [
                    "front",
                    "rear",
                    "left",
                    "right",
                    "top",
                    "bottom",
                    "isometric",
                ],
                "description": "Optional standard playback camera; the prior camera is restored on close.",
            },
        },
        "required": ["simulation"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    simulation: dict[str, Any],
    presentation: dict[str, Any] | None = None,
    hidden_components: list[dict[str, Any]] | None = None,
    camera: str = "",
) -> dict[str, Any]:
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
        presentation_obj = None
        if presentation is not None:
            presentation_obj = resolve_reference_target(
                service._active_document(),
                presentation,
                "Assembly playback presentation",
                open_missing=False,
            )
            if str(
                getattr(presentation_obj, "VibeCADVibeScriptOutputType", "") or ""
            ) != "exploded_view":
                raise RuntimeError(
                    "The playback presentation must be a VibeScript exploded_view output"
                )
        hidden_objects = [
            resolve_reference_target(
                service._active_document(),
                reference,
                f"Hidden Assembly playback component {index + 1}",
                open_missing=False,
            )
            for index, reference in enumerate(hidden_components or [])
        ]
        from CommandCreateSimulation import openSimulation

        openSimulation(
            obj,
            autoplay=True,
            presentation=presentation_obj,
            hidden_components=hidden_objects,
            camera=camera,
        )
        assembly = obj.Proxy.getAssembly(obj)
        return {
            "ok": True,
            "simulation": dict(simulation),
            "assembly": {
                "document_uid": str(getattr(obj.Document, "Uid", "") or ""),
                "object_name": str(getattr(assembly, "Name", "") or ""),
            },
            "playing": True,
            "presentation": dict(presentation) if presentation is not None else None,
            "hidden_component_count": len(hidden_objects),
            "camera": str(camera or ""),
            "temporary_state_policy": "placements_visibility_and_camera_restored_when_task_closes",
        }
    except Exception as exc:
        return {
            "ok": False,
            "failure_code": "SIMULATION_PLAYBACK_FAILED",
            "failure_stage": "native_call",
            "error": str(exc),
        }
