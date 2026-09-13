# SPDX-License-Identifier: LGPL-2.1-or-later

"""Target-aware viewport control for visual CAD inspection."""

from __future__ import annotations

from contextlib import contextmanager
import math
from typing import Any, Iterator

from VibeCADTools import tool_failure


_HALF_SQRT_2 = math.sqrt(0.5)
PRESET_QUATERNIONS = {
    "front": (_HALF_SQRT_2, 0.0, 0.0, _HALF_SQRT_2),
    "top": (0.0, 0.0, 0.0, 1.0),
    "right": (0.5, 0.5, 0.5, 0.5),
    "rear": (0.0, _HALF_SQRT_2, _HALF_SQRT_2, 0.0),
    "bottom": (1.0, 0.0, 0.0, 0.0),
    "left": (-0.5, 0.5, 0.5, -0.5),
    "isometric": (0.424708, 0.17592, 0.339851, 0.820473),
    "axometric": (0.424708, 0.17592, 0.339851, 0.820473),
}

PRESET_ORIENTATIONS = tuple(PRESET_QUATERNIONS)
FRAME_MODES = ("none", "all", "active_sketch", "selection", "objects")
SKETCH_ANNOTATION_MODES = ("unchanged", "show", "hide")


def _coin_module() -> Any:
    """Load the Coin wrapper before asking FreeCAD for SWIG-backed objects."""
    try:
        from pivy import coin
    except Exception as exc:
        raise RuntimeError(
            "VibeCAD viewport control requires the bundled pivy.coin wrapper."
        ) from exc
    if not hasattr(coin, "SoNode"):
        raise RuntimeError(
            "The loaded pivy.coin module does not expose Coin3D wrapper types."
        )
    return coin


def camera_schema(*, allow_auto: bool, default_mode: str) -> dict[str, Any]:
    """Return the single camera contract shared by viewport tools."""
    modes: list[dict[str, Any]] = []
    if allow_auto:
        modes.append(
            {
                "type": "object",
                "properties": {
                    "mode": {
                        "const": "auto",
                        "description": "Normal to open sketch, otherwise isometric.",
                    }
                },
                "required": ["mode"],
                "additionalProperties": False,
            }
        )
    modes.extend(
        [
            {
                "type": "object",
                "properties": {
                    "mode": {
                        "const": "unchanged",
                        "description": "Keep orientation.",
                    }
                },
                "required": ["mode"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "mode": {
                        "const": "preset",
                        "description": "Use a CAD preset.",
                    },
                    "preset": {
                        "type": "string",
                        "enum": list(PRESET_ORIENTATIONS),
                        "description": "CAD orientation.",
                    },
                },
                "required": ["mode", "preset"],
                "additionalProperties": False,
            },
            {
                "type": "object",
                "properties": {
                    "mode": {
                        "const": "direction",
                        "description": "Use document-coordinate directions.",
                    },
                    "view_direction": _camera_vector_schema(
                        "Camera-to-scene direction; negate an outward face normal."
                    ),
                    "up_direction": _camera_vector_schema(
                        "Screen-up direction; not parallel to view_direction."
                    ),
                },
                "required": ["mode", "view_direction", "up_direction"],
                "additionalProperties": False,
            },
        ]
    )
    simple_values = list(PRESET_ORIENTATIONS)
    if allow_auto:
        simple_values.insert(0, "auto")
    simple_values.append("unchanged")
    return {
        "oneOf": [
            {
                "type": "string",
                "enum": simple_values,
                "description": "Common camera orientation.",
            },
            *modes,
        ],
        "default": default_mode,
        "description": (
            "Use a preset name such as 'isometric'. Use the direction object only "
            "for an exact custom camera basis. Framing is independent."
        ),
    }


def _camera_vector_schema(description: str) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "x": {"type": "number"},
            "y": {"type": "number"},
            "z": {"type": "number"},
        },
        "required": ["x", "y", "z"],
        "additionalProperties": False,
        "description": description,
    }


TOOL_SPEC = {
    "description": (
        "Set camera, framing, zoom, sketch annotations, and object visibility. "
        "Object references accept a published output name, internal name, or unique "
        "visible label."
    ),
    "name": "core.set_view",
    "parameters": {
        "type": "object",
        "properties": {
            "camera": camera_schema(allow_auto=False, default_mode="unchanged"),
            "frame": {
                "type": "string",
                "enum": list(FRAME_MODES),
                "default": "none",
                "description": (
                    "Target to center and fit."
                ),
            },
            "object_names": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": (
                    "Published output names, internal names, or unique labels for "
                    "frame='objects'."
                ),
            },
            "zoom_steps": {
                "type": "integer",
                "minimum": -12,
                "maximum": 12,
                "default": 0,
                "description": "Positive zooms in.",
            },
            "sketch_annotations": {
                "type": "string",
                "enum": list(SKETCH_ANNOTATION_MODES),
                "default": "unchanged",
                "description": (
                    "Sketch constraint/dimension overlays."
                ),
            },
            "show_objects": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": "Objects to show.",
            },
            "hide_objects": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": "Objects to hide.",
            },
        },
        "additionalProperties": False,
    },
    "safety": "VIEW",
}


def run(
    service: Any,
    camera: Any = None,
    frame: str = "none",
    object_names: list[str] | None = None,
    zoom_steps: int = 0,
    sketch_annotations: str = "unchanged",
    show_objects: list[str] | None = None,
    hide_objects: list[str] | None = None,
) -> dict[str, Any]:
    requested = {
        "camera": camera,
        "frame": frame,
        "object_names": list(object_names or []),
        "zoom_steps": zoom_steps,
        "sketch_annotations": sketch_annotations,
        "show_objects": list(show_objects or []),
        "hide_objects": list(hide_objects or []),
    }
    frame_mode = str(frame or "none").strip().lower()
    annotation_mode = str(sketch_annotations or "unchanged").strip().lower()
    camera_resolution = resolve_camera_request(
        camera,
        allow_auto=False,
        default_mode="unchanged",
        active_sketch=False,
    )
    if not camera_resolution["ok"]:
        return tool_failure(
            TOOL_SPEC["name"],
            "CAMERA_REQUEST_INVALID",
            "precondition",
            str(camera_resolution.get("error") or "Camera request is invalid."),
            requested=requested,
            observed={
                key: value
                for key, value in camera_resolution.items()
                if key not in {"ok", "error"}
            },
            allowed_values=list(PRESET_ORIENTATIONS),
        )
    if frame_mode not in FRAME_MODES:
        return tool_failure(
            TOOL_SPEC["name"],
            "FRAME_MODE_INVALID",
            "precondition",
            f"Unknown frame mode {frame_mode!r}.",
            requested=requested,
            normalized={"frame": frame_mode},
            allowed_values=list(FRAME_MODES),
        )
    if annotation_mode not in SKETCH_ANNOTATION_MODES:
        return tool_failure(
            TOOL_SPEC["name"],
            "SKETCH_ANNOTATION_MODE_INVALID",
            "precondition",
            f"Unknown sketch_annotations mode {annotation_mode!r}.",
            requested=requested,
            normalized={"sketch_annotations": annotation_mode},
            allowed_values=list(SKETCH_ANNOTATION_MODES),
        )
    if isinstance(zoom_steps, bool) or not isinstance(zoom_steps, int):
        return tool_failure(
            TOOL_SPEC["name"],
            "ZOOM_STEPS_INVALID",
            "precondition",
            "zoom_steps must be an integer from -12 through 12.",
            requested=requested,
            allowed_values={"minimum": -12, "maximum": 12},
        )
    if zoom_steps < -12 or zoom_steps > 12:
        return tool_failure(
            TOOL_SPEC["name"],
            "ZOOM_STEPS_INVALID",
            "precondition",
            "zoom_steps must be from -12 through 12.",
            requested=requested,
            allowed_values={"minimum": -12, "maximum": 12},
        )

    try:
        import FreeCAD as App
        import FreeCADGui as Gui
    except Exception as exc:
        return tool_failure(
            TOOL_SPEC["name"],
            "FREECAD_GUI_UNAVAILABLE",
            "precondition",
            str(exc),
            requested=requested,
            observed={"exception_type": exc.__class__.__name__},
        )

    document = App.ActiveDocument
    if document is None:
        return tool_failure(
            TOOL_SPEC["name"],
            "NO_ACTIVE_DOCUMENT",
            "precondition",
            "No active document.",
            requested=requested,
        )
    gui_document = getattr(Gui, "ActiveDocument", None)
    view = getattr(gui_document, "ActiveView", None) if gui_document else None
    if view is None:
        return tool_failure(
            TOOL_SPEC["name"],
            "NO_ACTIVE_3D_VIEW",
            "precondition",
            "No active 3D view is available.",
            requested=requested,
            observed={"document": document.Name},
        )

    visibility = _resolve_visibility(document, show_objects, hide_objects)
    if not visibility["ok"]:
        return tool_failure(
            TOOL_SPEC["name"],
            "VISIBILITY_TARGET_INVALID",
            "precondition",
            str(visibility.get("error") or "Visibility target is invalid."),
            requested=requested,
            observed={
                key: value
                for key, value in visibility.items()
                if key not in {"ok", "error", "changes"}
            },
            candidates=_view_object_candidates(document),
        )
    frame_resolution = resolve_frame_objects(
        service,
        document,
        Gui,
        frame_mode,
        object_names,
    )
    if not frame_resolution["ok"]:
        return tool_failure(
            TOOL_SPEC["name"],
            "FRAME_TARGET_INVALID",
            "precondition",
            str(frame_resolution.get("error") or "Frame target is invalid."),
            requested=requested,
            normalized={"frame": frame_mode},
            observed={
                key: value
                for key, value in frame_resolution.items()
                if key not in {"ok", "error"}
            },
            candidates=_view_object_candidates(document),
        )

    normalized = {
        "camera": camera_resolution.get("resolved"),
        "frame": frame_mode,
        "framed_objects": list(frame_resolution.get("object_names") or []),
        "zoom_steps": zoom_steps,
        "sketch_annotations": annotation_mode,
        "visibility": [
            {"object": obj.Name, "visible": visible}
            for obj, visible in visibility["changes"]
        ],
    }
    viewport_before = {
        "camera": camera_state(view),
        "visibility": _visibility_state(visibility["changes"]),
    }
    stages: list[dict[str, Any]] = []
    current_stage = "visibility"
    visibility_result: dict[str, Any] = {"shown": [], "hidden": [], "changes": []}
    camera_result: dict[str, Any] = {}
    annotation_result: dict[str, Any] = {}
    framing: dict[str, Any] = {"framed": False, "method": "unchanged"}
    frame_names = list(frame_resolution.get("object_names") or [])
    zoomed = 0
    try:
        visibility_result = _apply_visibility(visibility["changes"])
        stages.append({"stage": current_stage, "ok": True, "result": visibility_result})
        current_stage = "camera"
        camera_result = apply_camera(view, camera_resolution)
        stages.append({"stage": current_stage, "ok": True, "result": camera_result})
        current_stage = "sketch_annotations"
        annotation_result = set_sketch_annotations(view, annotation_mode)
        stages.append({"stage": current_stage, "ok": True, "result": annotation_result})
        current_stage = "framing"
        framing = frame_view(
            service,
            view,
            document,
            frame_mode,
            frame_names,
            exclude_sketch_annotations=(frame_mode == "active_sketch"),
        )
        stages.append({"stage": current_stage, "ok": True, "result": framing})
        current_stage = "zoom"
        zoomed = apply_zoom(view, zoom_steps)
        stages.append({"stage": current_stage, "ok": True, "result": {"steps": zoomed}})
        current_stage = "gui_update"
        Gui.updateGui()
        stages.append({"stage": current_stage, "ok": True})
    except Exception as exc:
        stages.append(
            {
                "stage": current_stage,
                "ok": False,
                "exception_type": exc.__class__.__name__,
                "error": str(exc),
            }
        )
        viewport_after = _viewport_state_after(view, visibility["changes"])
        return tool_failure(
            TOOL_SPEC["name"],
            f"VIEW_{current_stage.upper()}_FAILED",
            "native_call",
            str(exc),
            requested=requested,
            normalized=normalized,
            observed={
                "failure_stage": current_stage,
                "stages": stages,
                "viewport_before": viewport_before,
                "viewport_after": viewport_after,
                "persistent_changes": {
                    "camera_changed": viewport_before.get("camera")
                    != viewport_after.get("camera"),
                    "visibility": visibility_result.get("changes", []),
                    "sketch_annotations": annotation_result,
                    "framing": framing,
                    "zoom_steps_applied": zoomed,
                },
            },
        )

    viewport_after = _viewport_state_after(view, visibility["changes"])

    return {
        "ok": True,
        "document": document.Name,
        "requested": requested,
        "normalized": normalized,
        "stages": stages,
        "viewport_before": viewport_before,
        "viewport_after": viewport_after,
        "camera": camera_result,
        "frame": frame_mode,
        "framed": bool(framing.get("framed")),
        "framing": framing,
        "framed_objects": frame_names,
        "zoom_steps": zoomed,
        "sketch_annotations": annotation_result,
        "shown": visibility_result["shown"],
        "hidden": visibility_result["hidden"],
        "visibility_changes": visibility_result["changes"],
    }


def resolve_camera_request(
    camera: Any,
    *,
    allow_auto: bool,
    default_mode: str,
    active_sketch: bool,
) -> dict[str, Any]:
    requested = {"mode": default_mode} if camera is None else camera
    allowed_modes = ["unchanged", "preset", "direction"]
    if allow_auto:
        allowed_modes.insert(0, "auto")
    if isinstance(requested, str):
        simple = requested.strip().lower()
        if simple in PRESET_ORIENTATIONS:
            requested = {"mode": "preset", "preset": simple}
        elif simple in allowed_modes:
            requested = {"mode": simple}
        else:
            return _invalid(
                f"Unknown camera orientation {simple!r}.",
                allowed_camera_values=[
                    *(["auto"] if allow_auto else []),
                    "unchanged",
                    *PRESET_ORIENTATIONS,
                ],
            )
    if not isinstance(requested, dict):
        return _invalid(
            "camera must be a preset name or a structured direction object.",
            allowed_camera_modes=allowed_modes,
        )
    mode = str(requested.get("mode") or "").strip().lower()
    if mode not in allowed_modes:
        return _invalid(
            f"Unknown camera mode {mode!r}.",
            allowed_camera_modes=allowed_modes,
        )

    if mode in {"auto", "unchanged"}:
        unexpected = sorted(set(requested) - {"mode"})
        if unexpected:
            return _invalid(
                f"camera mode {mode!r} does not accept additional fields.",
                unexpected_fields=unexpected,
            )
        if mode == "auto":
            resolved = (
                {"mode": "active_sketch"}
                if active_sketch
                else {"mode": "preset", "preset": "isometric"}
            )
            return {
                "ok": True,
                "requested": {"mode": "auto"},
                "resolved": resolved,
                "auto_reason": (
                    "align_perpendicular_to_open_sketch"
                    if active_sketch
                    else "default_model_isometric"
                ),
            }
        return {
            "ok": True,
            "requested": {"mode": "unchanged"},
            "resolved": {"mode": "unchanged"},
        }

    if mode == "preset":
        unexpected = sorted(set(requested) - {"mode", "preset"})
        if unexpected:
            return _invalid(
                "camera mode 'preset' received unsupported fields.",
                unexpected_fields=unexpected,
            )
        preset = str(requested.get("preset") or "").strip().lower()
        if preset not in PRESET_ORIENTATIONS:
            return _invalid(
                f"Unknown camera preset {preset!r}.",
                allowed_camera_presets=list(PRESET_ORIENTATIONS),
            )
        return {
            "ok": True,
            "requested": {"mode": "preset", "preset": preset},
            "resolved": {"mode": "preset", "preset": preset},
        }

    unexpected = sorted(
        set(requested) - {"mode", "view_direction", "up_direction"}
    )
    if unexpected:
        return _invalid(
            "camera mode 'direction' received unsupported fields.",
            unexpected_fields=unexpected,
        )
    view_vector = _resolve_camera_vector(
        requested.get("view_direction"), "camera.view_direction"
    )
    if not view_vector["ok"]:
        return view_vector
    up_vector = _resolve_camera_vector(
        requested.get("up_direction"), "camera.up_direction"
    )
    if not up_vector["ok"]:
        return up_vector

    direction = view_vector["normalized"]
    up_reference = up_vector["normalized"]
    dot = sum(direction[index] * up_reference[index] for index in range(3))
    projected = [
        up_reference[index] - direction[index] * dot for index in range(3)
    ]
    projected_length = math.sqrt(sum(value * value for value in projected))
    if projected_length <= 1e-6:
        return _invalid(
            "camera.up_direction is parallel to camera.view_direction and cannot "
            "define screen-up.",
            normalized_dot_product=dot,
            minimum_projected_up_length=1e-6,
        )
    effective_up = [value / projected_length for value in projected]
    resolved = {
        "mode": "direction",
        "view_direction": _vector_mapping(direction),
        "up_direction": _vector_mapping(effective_up),
    }
    return {
        "ok": True,
        "requested": {
            "mode": "direction",
            "view_direction": _vector_mapping(view_vector["components"]),
            "up_direction": _vector_mapping(up_vector["components"]),
        },
        "resolved": resolved,
        "normalization": {
            "view_direction_length": view_vector["length"],
            "up_direction_length": up_vector["length"],
            "input_dot_product": dot,
            "projected_up_length": projected_length,
        },
    }


def apply_camera(view: Any, resolution: dict[str, Any]) -> dict[str, Any]:
    before = camera_state(view)
    resolved = dict(resolution["resolved"])
    mode = resolved["mode"]
    expected_sketch_orientation = None
    if mode == "preset":
        _set_camera_orientation(view, PRESET_QUATERNIONS[resolved["preset"]])
    elif mode == "active_sketch":
        expected_sketch_orientation = _active_sketch_camera_orientation()
        _set_camera_orientation(view, expected_sketch_orientation.Q)
    elif mode == "direction":
        _set_camera_basis(
            view,
            resolved["view_direction"],
            resolved["up_direction"],
        )
    elif mode != "unchanged":
        raise RuntimeError(f"Unsupported resolved camera mode: {mode}")
    view.redraw()
    effective = camera_state(view)

    if expected_sketch_orientation is not None:
        current_orientation = view.getCameraOrientation()
        if not bool(current_orientation.isSame(expected_sketch_orientation, 1.0e-9)):
            raise RuntimeError(
                "The active camera did not align perpendicular to the open Sketch."
            )
        direction_error = None
        up_error = None
    elif mode == "direction":
        direction_error = _vector_angle_degrees(
            resolved["view_direction"], effective["view_direction"]
        )
        up_error = _vector_angle_degrees(
            resolved["up_direction"], effective["up_direction"]
        )
        if direction_error > 1e-3 or up_error > 1e-3:
            raise RuntimeError(
                "FreeCAD did not apply the requested camera basis within tolerance: "
                f"view error {direction_error:.6f} degrees, "
                f"up error {up_error:.6f} degrees."
            )
    else:
        direction_error = None
        up_error = None

    result = {
        "requested": dict(resolution["requested"]),
        "resolved": resolved,
        "before": before,
        "effective": effective,
        "changed": (
            _vector_angle_degrees(
                before["view_direction"], effective["view_direction"]
            )
            > 1e-6
            or _vector_angle_degrees(
                before["up_direction"], effective["up_direction"]
            )
            > 1e-6
        ),
    }
    if resolution.get("auto_reason"):
        result["auto_reason"] = resolution["auto_reason"]
    if resolution.get("normalization"):
        result["normalization"] = dict(resolution["normalization"])
    if direction_error is not None:
        result["application_error_degrees"] = {
            "view_direction": direction_error,
            "up_direction": up_error,
        }
    return result


def _active_sketch_camera_orientation() -> Any:
    try:
        import FreeCAD as App
        import FreeCADGui as Gui

        gui_document = Gui.activeDocument()
        if gui_document is None:
            raise RuntimeError("No active GUI document")
        return App.Placement(gui_document.EditingTransform).Rotation
    except Exception as exc:
        raise RuntimeError(
            "The open Sketch edit plane has no usable camera orientation."
        ) from exc


def camera_state(view: Any) -> dict[str, Any]:
    direction = _unit_components(view.getViewDirection(), "camera view direction")
    up = _unit_components(view.getUpDirection(), "camera up direction")
    return {
        "view_direction": _vector_mapping(direction),
        "up_direction": _vector_mapping(up),
        "camera_type": str(view.getCameraType()),
    }


def _set_camera_basis(
    view: Any,
    direction_mapping: dict[str, float],
    up_mapping: dict[str, float],
) -> None:
    import FreeCAD as App

    direction = App.Vector(
        direction_mapping["x"],
        direction_mapping["y"],
        direction_mapping["z"],
    )
    up = App.Vector(up_mapping["x"], up_mapping["y"], up_mapping["z"])
    right = direction.cross(up)
    right_length = float(right.Length)
    if right_length <= 1e-12:
        raise RuntimeError("Resolved camera basis has no right direction.")
    right = right / right_length

    rotation_matrix = App.Matrix()
    rotation_matrix.A11 = float(right.x)
    rotation_matrix.A12 = float(up.x)
    rotation_matrix.A13 = float(-direction.x)
    rotation_matrix.A21 = float(right.y)
    rotation_matrix.A22 = float(up.y)
    rotation_matrix.A23 = float(-direction.y)
    rotation_matrix.A31 = float(right.z)
    rotation_matrix.A32 = float(up.z)
    rotation_matrix.A33 = float(-direction.z)
    _set_camera_orientation(view, App.Rotation(rotation_matrix).Q)


def _set_camera_orientation(view: Any, quaternion: Any) -> None:
    _coin_module()
    camera = view.getCameraNode()
    if camera is None:
        raise RuntimeError("The active viewport has no camera node.")
    current_direction = view.getViewDirection()
    current_length = float(current_direction.Length)
    if current_length <= 1e-12:
        raise RuntimeError("The active camera has no valid view direction.")
    current_direction = current_direction / current_length
    focal_distance = float(camera.focalDistance.getValue())
    position_value = camera.position.getValue()
    current_position = [float(position_value[index]) for index in range(3)]
    focal_point = [
        current_position[index]
        + float((current_direction.x, current_direction.y, current_direction.z)[index])
        * focal_distance
        for index in range(3)
    ]

    values = tuple(float(value) for value in quaternion)
    if len(values) != 4 or not all(math.isfinite(value) for value in values):
        raise RuntimeError("Resolved camera orientation is not a finite quaternion.")
    camera.orientation.setValue(values)

    new_direction = view.getViewDirection()
    new_length = float(new_direction.Length)
    if new_length <= 1e-12:
        raise RuntimeError("The applied camera orientation has no view direction.")
    new_direction = new_direction / new_length
    camera.position.setValue(
        focal_point[0] - float(new_direction.x) * focal_distance,
        focal_point[1] - float(new_direction.y) * focal_distance,
        focal_point[2] - float(new_direction.z) * focal_distance,
    )


def _resolve_camera_vector(value: Any, field: str) -> dict[str, Any]:
    if not isinstance(value, dict) or set(value) != {"x", "y", "z"}:
        return _invalid(f"{field} must contain exactly numeric x, y, and z fields.")
    components = []
    for axis in ("x", "y", "z"):
        component = value[axis]
        if isinstance(component, bool) or not isinstance(component, (int, float)):
            return _invalid(f"{field}.{axis} must be a finite number.")
        component = float(component)
        if not math.isfinite(component):
            return _invalid(f"{field}.{axis} must be a finite number.")
        components.append(component)
    length = math.sqrt(sum(component * component for component in components))
    if length <= 1e-12:
        return _invalid(f"{field} must be a non-zero vector.")
    return {
        "ok": True,
        "components": components,
        "normalized": [component / length for component in components],
        "length": length,
    }


def _unit_components(value: Any, field: str) -> list[float]:
    components = [float(value.x), float(value.y), float(value.z)]
    length = math.sqrt(sum(component * component for component in components))
    if not math.isfinite(length) or length <= 1e-12:
        raise RuntimeError(f"FreeCAD returned an invalid {field}.")
    return [component / length for component in components]


def _vector_mapping(components: Any) -> dict[str, float]:
    return {
        "x": float(components[0]),
        "y": float(components[1]),
        "z": float(components[2]),
    }


def _vector_angle_degrees(first: dict[str, float], second: dict[str, float]) -> float:
    dot = sum(first[axis] * second[axis] for axis in ("x", "y", "z"))
    return math.degrees(math.acos(max(-1.0, min(1.0, dot))))


def resolve_frame_objects(
    service: Any,
    document: Any,
    gui: Any,
    frame_mode: str,
    object_names: list[str] | None,
) -> dict[str, Any]:
    if frame_mode == "none":
        if object_names:
            return _invalid("object_names requires frame='objects'.")
        return {"ok": True, "object_names": []}

    if frame_mode == "all":
        if object_names:
            return _invalid("object_names requires frame='objects'.")
        return {
            "ok": True,
            "object_names": [
                name
                for name in model_display_target_names(document)
                if bool(document.getObject(name).ViewObject.Visibility)
            ],
        }

    if frame_mode == "active_sketch":
        sketch = service._get_sketch()
        if sketch is None:
            return _invalid(
                "No sketch is open for editing; active_sketch framing has no target."
            )
        return {"ok": True, "object_names": [sketch.Name]}

    if frame_mode == "selection":
        selected = [
            item
            for item in list(gui.Selection.getSelection() or [])
            if getattr(item, "Document", None) is document
        ]
        names = _unique_names(selected)
        if not names:
            return _invalid("The active document selection is empty.")
        return {"ok": True, "object_names": names}

    names = [str(name or "").strip() for name in list(object_names or [])]
    names = [name for name in names if name]
    if not names:
        return _invalid("object_names is required when frame='objects'.")
    resolution = _resolve_object_references(document, names)
    if not resolution["ok"]:
        return resolution
    objects = list(resolution["objects"])
    return {"ok": True, "object_names": _unique_names(objects)}


def frame_view(
    service: Any,
    view: Any,
    document: Any,
    frame_mode: str,
    object_names: list[str],
    *,
    exclude_sketch_annotations: bool = False,
) -> dict[str, Any]:
    if frame_mode == "none":
        return {"framed": False, "method": "unchanged"}
    if frame_mode == "all":
        # Fit the same top-level visible targets reported by resolution.  A
        # native Link can render delegated geometry without exposing a direct
        # Shape, while visible implementation children and datum origins can
        # otherwise dominate fitAll even though they are not independent
        # display targets.
        if object_names:
            with temporarily_isolate_objects(
                document,
                object_names,
                reveal_targets=False,
            ):
                _flush_view_visibility(view)
                if exclude_sketch_annotations:
                    with temporarily_detach_sketch_annotations(view):
                        view.fitAll()
                else:
                    view.fitAll()
            return {
                "framed": True,
                "method": "visible_model_target_fit",
                "object_names": list(object_names),
            }
        view.fitAll()
        return {"framed": True, "method": "empty_scene_fit"}
    if frame_mode == "active_sketch":
        sketch = service._get_sketch()
        if sketch is None:
            raise RuntimeError("No sketch is open for active_sketch framing.")
        return frame_active_sketch(view, sketch)

    objects = [document.getObject(name) for name in object_names]
    objects = [obj for obj in objects if obj is not None]
    finite_objects = [obj for obj in objects if _has_finite_shape_bounds(obj)]
    unbounded_names = [obj.Name for obj in objects if obj not in finite_objects]

    if not finite_objects:
        points = [_global_placement(obj).Base for obj in objects]
        framing = _frame_world_points(
            view,
            points,
            method="reference_origin_bounds",
            minimum_span=10.0,
        )
        framing["object_names"] = list(object_names)
        return framing

    with temporarily_isolate_objects(document, object_names):
        with temporarily_hide_objects(document, unbounded_names):
            _flush_view_visibility(view)
            if exclude_sketch_annotations:
                with temporarily_detach_sketch_annotations(view):
                    view.fitAll()
            else:
                view.fitAll()
    return {
        "framed": True,
        "method": "isolated_finite_scene_fit",
        "object_names": list(object_names),
        "fit_objects": [obj.Name for obj in finite_objects],
        "reference_objects_excluded_from_fit": unbounded_names,
    }


def _flush_view_visibility(view: Any) -> None:
    """Apply pending scene visibility before Coin computes fit bounds."""
    import FreeCADGui as Gui

    redraw = getattr(view, "redraw", None)
    if callable(redraw):
        redraw()
    update_gui = getattr(Gui, "updateGui", None)
    if callable(update_gui):
        update_gui()


def frame_active_sketch(view: Any, sketch: Any) -> dict[str, Any]:
    points, local_bounds, geometry_scope = _sketch_curve_world_points(sketch)
    framing = _frame_world_points(
        view,
        points,
        method="actual_sketch_curve_bounds",
        minimum_span=1.0,
    )
    framing.update(
        {
            "sketch": sketch.Name,
            "geometry_scope": geometry_scope,
            "local_bounds": local_bounds,
        }
    )
    return framing


def _frame_world_points(
    view: Any,
    points: list[Any],
    *,
    method: str,
    minimum_span: float,
) -> dict[str, Any]:
    import FreeCAD as App

    _coin_module()
    if not points:
        raise RuntimeError(
            "The requested viewport target has no finite framing points."
        )
    direction = view.getViewDirection()
    up = view.getUpDirection()
    direction_length = float(direction.Length)
    up_length = float(up.Length)
    if direction_length <= 1e-12 or up_length <= 1e-12:
        raise RuntimeError("The active camera has an invalid view or up direction.")
    direction = direction / direction_length
    up = up / up_length
    right = direction.cross(up)
    right_length = float(right.Length)
    if right_length <= 1e-12:
        raise RuntimeError("The active camera view and up directions are parallel.")
    right = right / right_length
    up = right.cross(direction)
    up = up / float(up.Length)

    right_coordinates = [float(point.dot(right)) for point in points]
    up_coordinates = [float(point.dot(up)) for point in points]
    width = max(right_coordinates) - min(right_coordinates)
    height = max(up_coordinates) - min(up_coordinates)
    viewport_width, viewport_height = view.getSize()
    if int(viewport_width) <= 0 or int(viewport_height) <= 0:
        raise RuntimeError("The active viewport has invalid dimensions.")
    aspect = float(viewport_width) / float(viewport_height)
    camera_height = max(height, width / aspect, minimum_span) * 1.15

    center = App.Vector()
    for point in points:
        center = center + point
    center = center / len(points)

    camera = view.getCameraNode()
    if not hasattr(camera, "height"):
        raise RuntimeError(
            "Active-sketch framing requires FreeCAD's orthographic Sketcher camera."
        )
    focal_distance = float(camera.focalDistance.getValue())
    position = center - direction * focal_distance
    camera.position.setValue(float(position.x), float(position.y), float(position.z))
    camera.height.setValue(float(camera_height))
    view.redraw()
    return {
        "framed": True,
        "method": method,
        "camera_height": camera_height,
        "projected_size": [width, height],
    }


def _sketch_curve_world_points(
    sketch: Any,
) -> tuple[list[Any], dict[str, list[float]], str]:
    import FreeCAD as App

    geometry = list(getattr(sketch, "Geometry", []) or [])
    defining_indices = [
        index
        for index in range(len(geometry))
        if not bool(sketch.getConstruction(index))
    ]
    geometry_scope = "defining_geometry"
    indices = defining_indices
    if not indices:
        indices = list(range(len(geometry)))
        geometry_scope = "construction_geometry"
    if not indices:
        raise RuntimeError(f"Sketch {sketch.Name} has no geometry to frame.")

    minimum = [float("inf"), float("inf"), float("inf")]
    maximum = [float("-inf"), float("-inf"), float("-inf")]
    for index in indices:
        shape = geometry[index].toShape()
        if shape is None or bool(shape.isNull()):
            raise RuntimeError(
                f"Sketch geometry:{index} produced a null shape during framing."
            )
        bounds = shape.BoundBox
        values_min = [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)]
        values_max = [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)]
        for axis in range(3):
            minimum[axis] = min(minimum[axis], values_min[axis])
            maximum[axis] = max(maximum[axis], values_max[axis])

    placement = sketch.getGlobalPlacement()
    points = []
    for x in (minimum[0], maximum[0]):
        for y in (minimum[1], maximum[1]):
            for z in (minimum[2], maximum[2]):
                points.append(placement.multVec(App.Vector(x, y, z)))
    return (
        points,
        {
            "min": minimum,
            "max": maximum,
            "size": [maximum[axis] - minimum[axis] for axis in range(3)],
        },
        geometry_scope,
    )


def apply_zoom(view: Any, zoom_steps: int) -> int:
    if zoom_steps == 0:
        return 0
    method = view.zoomIn if zoom_steps > 0 else view.zoomOut
    for _ in range(abs(zoom_steps)):
        method()
    return zoom_steps


def set_sketch_annotations(view: Any, mode: str) -> dict[str, Any]:
    if mode == "unchanged":
        return {"mode": mode, "changed": False}
    node = _constraint_group_node(view)
    enabled = mode == "show"
    count = int(node.enable.getNum()) if node is not None else 0
    if node is not None:
        for index in range(count):
            node.enable.set1Value(index, enabled)
    internal_changed = _set_internal_geometry_visible(view, enabled)
    return {
        "mode": mode,
        "changed": bool(node is not None or internal_changed),
        "constraint_count": count,
        "internal_geometry_changed": internal_changed,
    }


def _view_visibility_snapshot(objects: Iterator[Any]) -> list[tuple[Any, bool]]:
    snapshots = []
    for obj in objects:
        view_object = getattr(obj, "ViewObject", None)
        if view_object is not None:
            snapshots.append((view_object, bool(view_object.Visibility)))
    return snapshots


def _restore_view_visibility(snapshots: list[tuple[Any, bool]]) -> None:
    # Restore hidden containers before visible descendants. Hiding a FreeCAD
    # group can hide its children as a side effect, regardless of document order.
    for visible in (False, True):
        for view_object, original in snapshots:
            if original is visible and bool(view_object.Visibility) is not original:
                view_object.Visibility = original


@contextmanager
def temporarily_isolate_objects(
    document: Any,
    object_names: list[str],
    *,
    reveal_targets: bool = True,
) -> Iterator[None]:
    keep = _visible_container_closure(document, object_names)
    preserve = _linked_render_support_names(document, object_names) - keep
    objects = list(document.Objects)
    snapshots = _view_visibility_snapshot(iter(objects))
    for obj in objects:
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None:
            continue
        visible = bool(view_object.Visibility)
        # App::Link owns the occurrence's visibility, but its view provider may
        # still consume the linked definition's currently enabled Body Tip or
        # group children. Preserve those implementation switches without
        # showing the definition container itself in the isolated scene.
        if reveal_targets:
            desired = visible if obj.Name in preserve else obj.Name in keep
        else:
            # frame='all' starts from the exact set of currently visible
            # top-level model targets. Isolating that scene may hide unrelated
            # objects, but it must never reinterpret a hidden Body Tip or
            # container ancestor as something the user intended to show.
            desired = visible and (obj.Name in keep or obj.Name in preserve)
        if visible != desired:
            view_object.Visibility = desired
    try:
        yield
    finally:
        _restore_view_visibility(snapshots)


@contextmanager
def temporarily_show_objects(
    document: Any,
    object_names: list[str],
) -> Iterator[list[str]]:
    show = _visible_container_closure(document, object_names)
    objects = [document.getObject(name) for name in show]
    objects = [obj for obj in objects if obj is not None]
    snapshots = _view_visibility_snapshot(iter(objects))
    changed: list[str] = []
    for obj in objects:
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None:
            continue
        visible = bool(view_object.Visibility)
        if not visible:
            view_object.Visibility = True
            changed.append(obj.Name)
    try:
        yield changed
    finally:
        _restore_view_visibility(snapshots)


@contextmanager
def temporarily_hide_objects(
    document: Any,
    object_names: list[str],
) -> Iterator[None]:
    objects = [document.getObject(name) for name in object_names]
    objects = [obj for obj in objects if obj is not None]
    snapshots = _view_visibility_snapshot(iter(objects))
    for obj in objects:
        view_object = getattr(obj, "ViewObject", None)
        if view_object is None:
            continue
        visible = bool(view_object.Visibility)
        if visible:
            view_object.Visibility = False
    try:
        yield
    finally:
        _restore_view_visibility(snapshots)


def _visible_container_closure(document: Any, object_names: list[str]) -> set[str]:
    keep = {str(name) for name in object_names}
    pending = [document.getObject(name) for name in keep]
    while pending:
        obj = pending.pop()
        if obj is None:
            continue
        if str(getattr(obj, "TypeId", "") or "") == "PartDesign::Body":
            tip = getattr(obj, "Tip", None)
            tip_name = str(getattr(tip, "Name", "") or "")
            if tip_name and tip_name not in keep:
                keep.add(tip_name)
                pending.append(tip)
        parent = None
        for method_name in ("getParentGeoFeatureGroup", "getParentGroup"):
            method = getattr(obj, method_name, None)
            if not callable(method):
                continue
            try:
                candidate = method()
            except Exception as exc:
                raise RuntimeError(
                    f"FreeCAD failed to resolve "
                    f"{getattr(obj, 'Name', '<unnamed>')}'s parent with "
                    f"{method_name}(): {exc}"
                ) from exc
            if candidate is not None:
                parent = candidate
                break
        parent_name = str(getattr(parent, "Name", "") or "")
        if parent_name and parent_name not in keep:
            keep.add(parent_name)
            pending.append(parent)
    return keep


def _linked_render_support_names(document: Any, object_names: list[str]) -> set[str]:
    """Return definition children whose presentation an isolated Link consumes.

    A linked occurrence and its definition are separate visible objects.  The
    definition root must stay hidden during an occurrence-only capture, while
    the definition's active Body Tip or visible group members retain their
    existing presentation state so the Link can render its delegated scene.
    """

    support: set[str] = set()
    visited: set[int] = set()

    def retain_children(target: Any) -> None:
        if target is None or id(target) in visited:
            return
        visited.add(id(target))
        type_id = str(getattr(target, "TypeId", "") or "")
        if type_id == "PartDesign::Body":
            tip = getattr(target, "Tip", None)
            tip_name = str(getattr(tip, "Name", "") or "")
            if tip_name:
                support.add(tip_name)
            return
        members = list(getattr(target, "Group", []) or [])
        for member in members:
            member_name = str(getattr(member, "Name", "") or "")
            if member_name:
                support.add(member_name)
            retain_children(member)

    for name in object_names:
        occurrence = document.getObject(name)
        if occurrence is None:
            continue
        target = getattr(occurrence, "LinkedObject", None)
        if target is None:
            continue
        retain_children(target)
    return support


def _has_finite_shape_bounds(obj: Any) -> bool:
    shape = getattr(obj, "Shape", None)
    is_null = getattr(shape, "isNull", None)
    if shape is None or not callable(is_null):
        return False
    try:
        if bool(is_null()):
            return False
        bounds = shape.BoundBox
        values = (
            float(bounds.XMin),
            float(bounds.YMin),
            float(bounds.ZMin),
            float(bounds.XMax),
            float(bounds.YMax),
            float(bounds.ZMax),
        )
    except Exception:
        return False
    return all(math.isfinite(value) and abs(value) < 1e50 for value in values)


def model_display_target_names(document: Any) -> list[str]:
    targets: list[Any] = []
    for obj in list(getattr(document, "Objects", []) or []):
        type_id = str(getattr(obj, "TypeId", "") or "")
        if type_id in {"App::Link", "Assembly::AssemblyLink"}:
            targets.append(obj)
            continue
        if not _has_finite_shape_bounds(obj):
            continue
        if type_id == "Sketcher::SketchObject":
            continue
        if type_id == "PartDesign::Body":
            targets.append(obj)
            continue
        parent_method = getattr(obj, "getParentGeoFeatureGroup", None)
        try:
            parent = parent_method() if callable(parent_method) else None
        except Exception:
            parent = None
        if str(getattr(parent, "TypeId", "") or "") == "PartDesign::Body":
            continue
        targets.append(obj)
    return _unique_names(targets)


def _is_unbounded_reference(obj: Any) -> bool:
    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id in {"PartDesign::Line", "PartDesign::Plane", "App::Line", "App::Plane"}:
        return True
    shape = getattr(obj, "Shape", None)
    return (
        shape is not None
        and not bool(shape.isNull())
        and not _has_finite_shape_bounds(obj)
    )


def _global_placement(obj: Any) -> Any:
    method = getattr(obj, "getGlobalPlacement", None)
    if callable(method):
        return method()
    return obj.Placement


@contextmanager
def temporarily_detach_sketch_annotations(view: Any) -> Iterator[bool]:
    with _temporarily_detach_scene_node(view, "ConstraintGroup") as detached:
        yield detached


@contextmanager
def temporarily_detach_sketch_information_overlay(view: Any) -> Iterator[bool]:
    with _temporarily_detach_scene_node(view, "InformationGroup") as detached:
        yield detached


@contextmanager
def _temporarily_detach_scene_node(view: Any, name: str) -> Iterator[bool]:
    search, path = _scene_node_path(view, name)
    if path is None:
        yield False
        return
    if int(path.getLength()) < 2:
        raise RuntimeError(f"Sketcher {name} has no scene-graph parent.")
    node = path.getTail()
    parent = path.getNodeFromTail(1)
    child_index = int(parent.findChild(node))
    if child_index < 0:
        raise RuntimeError(f"Sketcher {name} is detached from its parent.")
    node.ref()
    parent.removeChild(child_index)
    try:
        yield True
    finally:
        parent.insertChild(node, child_index)
        node.unref()


@contextmanager
def temporarily_hide_sketch_internal_geometry(view: Any) -> Iterator[bool]:
    coin = _coin_module()

    search, path = _scene_node_path(view, "CurvesInternalDrawStyle")
    if path is None:
        yield False
        return
    node = path.getTail()
    style = getattr(node, "style", None)
    if style is None:
        raise RuntimeError("Sketcher internal geometry draw style has no style field.")
    previous = style.getValue()
    style.setValue(coin.SoDrawStyle.INVISIBLE)
    try:
        yield True
    finally:
        style.setValue(previous)


def _set_internal_geometry_visible(view: Any, visible: bool) -> bool:
    coin = _coin_module()

    _search, path = _scene_node_path(view, "CurvesInternalDrawStyle")
    if path is None:
        return False
    node = path.getTail()
    style = getattr(node, "style", None)
    if style is None:
        raise RuntimeError("Sketcher internal geometry draw style has no style field.")
    style.setValue(coin.SoDrawStyle.LINES if visible else coin.SoDrawStyle.INVISIBLE)
    return True


def _constraint_group_node(view: Any) -> Any | None:
    search, path = _constraint_group_path(view)
    return path.getTail() if path is not None else None


def _constraint_group_path(view: Any) -> tuple[Any, Any | None]:
    return _scene_node_path(view, "ConstraintGroup")


def _scene_node_path(view: Any, name: str) -> tuple[Any, Any | None]:
    coin = _coin_module()

    search = coin.SoSearchAction()
    search.setName(name)
    search.setInterest(coin.SoSearchAction.FIRST)
    search.apply(view.getSceneGraph())
    return search, search.getPath()


def _resolve_visibility(
    document: Any, show_objects: Any, hide_objects: Any
) -> dict[str, Any]:
    changes = []
    seen = set()
    for names, visible in ((show_objects, True), (hide_objects, False)):
        references = [str(raw_name).strip() for raw_name in list(names or [])]
        resolution = _resolve_object_references(document, references)
        if not resolution["ok"]:
            return resolution
        for obj in resolution["objects"]:
            if obj.Name in seen:
                return _invalid(
                    f"Object {obj.Name} appears in both visibility lists."
                )
            seen.add(obj.Name)
            if getattr(obj, "ViewObject", None) is None:
                return _invalid(f"Object {obj.Name} has no GUI ViewObject.")
            changes.append((obj, visible))
    return {"ok": True, "changes": changes}


def _resolve_object_references(
    document: Any, references: list[str]
) -> dict[str, Any]:
    """Resolve model-facing output names, internal names, then human labels."""
    objects = []
    missing = []
    ambiguous: dict[str, list[str]] = {}
    document_objects = list(getattr(document, "Objects", []) or [])
    for reference in references:
        obj = document.getObject(reference)
        if obj is not None:
            objects.append(obj)
            continue
        body_matches = [
            candidate
            for candidate in document_objects
            if str(getattr(candidate, "TypeId", "") or "")
            == "PartDesign::Body"
            and str(
                getattr(candidate, "VibeCADScriptedOutputKey", "") or ""
            )
            == reference
        ]
        if len(body_matches) == 1:
            objects.append(body_matches[0])
            continue
        if len(body_matches) > 1:
            ambiguous[reference] = [candidate.Name for candidate in body_matches]
            continue
        output_matches = [
            candidate
            for candidate in document_objects
            if str(
                getattr(candidate, "VibeCADVibeScriptOutputName", "") or ""
            )
            == reference
        ]
        if len(output_matches) == 1:
            objects.append(output_matches[0])
            continue
        if len(output_matches) > 1:
            ambiguous[reference] = [
                candidate.Name for candidate in output_matches
            ]
            continue
        label_matches = [
            candidate
            for candidate in document_objects
            if str(getattr(candidate, "Label", "") or "") == reference
        ]
        if len(label_matches) == 1:
            objects.append(label_matches[0])
        elif len(label_matches) > 1:
            ambiguous[reference] = [candidate.Name for candidate in label_matches]
        else:
            missing.append(reference)
    if ambiguous:
        return _invalid(
            "Object labels must be unique; use an internal name for: "
            + ", ".join(sorted(ambiguous)),
            ambiguous_labels=ambiguous,
        )
    if missing:
        return _invalid(
            "Objects not found by published output name, internal name, or unique "
            "label: "
            + ", ".join(missing),
            missing_objects=missing,
        )
    return {"ok": True, "objects": objects}


def _apply_visibility(changes: list[tuple[Any, bool]]) -> dict[str, Any]:
    shown: list[str] = []
    hidden: list[str] = []
    applied: list[dict[str, Any]] = []
    for obj, visible in changes:
        before = bool(obj.ViewObject.Visibility)
        obj.ViewObject.Visibility = visible
        after = bool(obj.ViewObject.Visibility)
        (shown if visible else hidden).append(obj.Name)
        applied.append(
            {
                "object": obj.Name,
                "before": before,
                "requested": bool(visible),
                "after": after,
                "changed": before != after,
                "effect_applied": after == bool(visible),
            }
        )
        if after != bool(visible):
            raise RuntimeError(f"FreeCAD did not apply visibility for {obj.Name}.")
    return {"shown": shown, "hidden": hidden, "changes": applied}


def _visibility_state(changes: list[tuple[Any, bool]]) -> list[dict[str, Any]]:
    return [
        {"object": obj.Name, "visible": bool(obj.ViewObject.Visibility)}
        for obj, _ in changes
    ]


def _viewport_state_after(
    view: Any,
    visibility_changes: list[tuple[Any, bool]],
) -> dict[str, Any]:
    state: dict[str, Any] = {"visibility": _visibility_state(visibility_changes)}
    try:
        state["camera"] = camera_state(view)
    except Exception as exc:
        state["camera_error"] = {
            "exception_type": exc.__class__.__name__,
            "message": str(exc),
        }
    return state


def _view_object_candidates(document: Any) -> list[dict[str, Any]]:
    return [
        {
            "name": obj.Name,
            "label": getattr(obj, "Label", obj.Name),
            "type": getattr(obj, "TypeId", ""),
            "visible": bool(obj.ViewObject.Visibility),
        }
        for obj in list(getattr(document, "Objects", []) or [])
        if getattr(obj, "ViewObject", None) is not None
    ][:80]


def _unique_names(objects: list[Any]) -> list[str]:
    names: list[str] = []
    seen: set[str] = set()
    for obj in objects:
        name = str(getattr(obj, "Name", "") or "").strip()
        if name and name not in seen:
            names.append(name)
            seen.add(name)
    return names


def _invalid(error: str, **details: Any) -> dict[str, Any]:
    return {
        "ok": False,
        "error": error,
        "retry_same_call": False,
        **details,
    }
