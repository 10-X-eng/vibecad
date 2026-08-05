# SPDX-License-Identifier: LGPL-2.1-or-later

"""Open and play one exact retained native Assembly simulation."""

from __future__ import annotations

import json
from typing import Any


TOOL_SPEC = {
    "name": "assembly.play_simulation",
    "description": (
        "Play a saved Assembly simulation. Use autoplay=false with time_seconds "
        "for one stable inspection frame. Optional presentation, hidden components, "
        "and camera settings are temporary; stop_simulation restores them. The "
        "result reports automatic deterministic collision evidence for the full trace and "
        "the displayed frame."
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
                "description": "Exact published simulation reference.",
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
                    "Optional published exploded_view applied to every frame."
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
                    "Exact Assembly components to hide during playback."
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
                "description": "Optional standard camera.",
            },
            "autoplay": {
                "type": "boolean",
                "description": (
                    "Start moving after generation; false holds time_seconds."
                ),
                "default": True,
            },
            "time_seconds": {
                "type": "number",
                "description": (
                    "Exact saved-simulation time to display, in seconds."
                ),
            },
        },
        "required": ["simulation"],
        "additionalProperties": False,
    },
}


def _collision_result(obj: Any, frame: int) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    encoded = str(getattr(obj, "VibeCADAssemblySimulationValidation", "") or "")
    try:
        validation = json.loads(encoded)
    except (TypeError, json.JSONDecodeError):
        validation = {}
    summary = validation.get("collision_summary")
    if (
        not isinstance(summary, dict)
        or summary.get("status") not in {"complete", "incomplete"}
    ):
        return (
            {
                "status": "unavailable",
                "reason": "saved_simulation_predates_automatic_collision_evaluation",
                "action": "Rebuild the existing VibeScript program to add collision evidence.",
            },
            [],
        )
    active: list[dict[str, Any]] = []
    for pair in list(summary.get("pairs") or []):
        if not isinstance(pair, dict):
            raise RuntimeError("The saved simulation collision summary is malformed")
        for interval in list(pair.get("intervals") or []):
            if (
                isinstance(interval, dict)
                and int(interval.get("first_frame", -1))
                <= frame
                <= int(interval.get("last_frame", -1))
            ):
                active.append(
                    {
                        "first_component": str(pair.get("first_component") or ""),
                        "second_component": str(pair.get("second_component") or ""),
                    }
                )
                break
    useful_summary = {
        name: summary.get(name)
        for name in (
            "status",
            "analysis_complete",
            "geometry_authority",
            "collision_definition",
            "collision_mesh_linear_deflection_mm",
            "collision_mesh_angular_deflection_radians",
            "evaluated_frame_count",
            "collision_free",
            "colliding_frame_count",
            "colliding_pair_count",
            "interference_volume_complete",
            "first_collision",
            "worst_collision",
            "pairs",
            "warning_count",
            "warnings",
        )
    }
    return useful_summary, active


def run(
    service: Any,
    simulation: dict[str, Any],
    presentation: dict[str, Any] | None = None,
    hidden_components: list[dict[str, Any]] | None = None,
    camera: str = "",
    autoplay: bool = True,
    time_seconds: float | None = None,
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
        from CommandCreateSimulation import _simulationFrameTime, openSimulation

        panel = openSimulation(
            obj,
            autoplay=bool(autoplay),
            time_seconds=time_seconds,
            presentation=presentation_obj,
            hidden_components=hidden_objects,
            camera=camera,
        )
        assembly = obj.Proxy.getAssembly(obj)
        frame = int(panel.form.frameSlider.value())
        collision_summary, frame_collisions = _collision_result(obj, frame)
        displayed_time = _simulationFrameTime(obj, frame)
        return {
            "ok": True,
            "simulation": dict(simulation),
            "assembly": {
                "document_uid": str(getattr(obj.Document, "Uid", "") or ""),
                "object_name": str(getattr(assembly, "Name", "") or ""),
            },
            "playing": bool(autoplay),
            "frame": frame,
            "time_seconds": displayed_time,
            "frame_kind": "input" if displayed_time is None else "solver_output",
            "frame_count": int(assembly.numberOfFrames()),
            "collision_alert": (
                None
                if collision_summary.get("status") == "unavailable"
                else not bool(collision_summary["collision_free"])
            ),
            "collision_alert_reason": (
                None
                if collision_summary.get("status") == "unavailable"
                else (
                    "analysis_incomplete"
                    if not bool(collision_summary.get("analysis_complete", True))
                    else (
                        "collision_detected"
                        if not bool(collision_summary["collision_free"])
                        else None
                    )
                )
            ),
            "collision_summary": collision_summary,
            "displayed_frame_collisions": frame_collisions,
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
