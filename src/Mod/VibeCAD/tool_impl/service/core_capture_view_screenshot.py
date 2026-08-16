# SPDX-License-Identifier: LGPL-2.1-or-later

"""Capture a target-aware viewport image for provider visual inspection."""

from __future__ import annotations

from contextlib import ExitStack
import hashlib
import json
from pathlib import Path
import re
import time
from typing import Any

from VibeCADTools import tool_failure

from . import core_set_view


CAPTURE_FRAME_MODES = ("auto",) + core_set_view.FRAME_MODES
CAPTURE_ANNOTATION_MODES = ("clean", "current")
DUPLICATE_VISUAL_DIFFERENCE_THRESHOLD = 0.005


TOOL_SPEC = {
    "description": (
        "Capture the 3D view for visual verification. auto frames an open sketch or "
        "the full model; clean temporarily hides sketch overlays. Omit camera for "
        "automatic orientation or pass camera='isometric'."
    ),
    "name": "core.capture_view_screenshot",
    "parameters": {
        "type": "object",
        "properties": {
            "camera": core_set_view.camera_schema(
                allow_auto=True,
                default_mode="auto",
            ),
            "frame": {
                "type": "string",
                "enum": list(CAPTURE_FRAME_MODES),
                "default": "auto",
                "description": "Viewport target.",
            },
            "object_names": {
                "type": "array",
                "items": {"type": "string", "minLength": 1},
                "description": (
                    "Published output names, internal names, or unique labels for "
                    "frame='objects'."
                ),
            },
            "sketch_annotations": {
                "type": "string",
                "enum": list(CAPTURE_ANNOTATION_MODES),
                "default": "clean",
                "description": (
                    "Hide or retain sketch overlays for this capture."
                ),
            },
        },
        "additionalProperties": False,
    },
    "safety": "VIEW",
}


def run(
    service: Any,
    camera: Any = None,
    frame: str = "auto",
    object_names: list[str] | None = None,
    sketch_annotations: str = "clean",
) -> dict[str, Any]:
    requested = {
        "camera": camera,
        "frame": frame,
        "object_names": list(object_names or []),
        "sketch_annotations": sketch_annotations,
    }
    frame_mode = str(frame or "auto").strip().lower()
    annotation_mode = str(sketch_annotations or "clean").strip().lower()
    if frame_mode not in CAPTURE_FRAME_MODES:
        return _remember_failure(
            service,
            "FRAME_MODE_INVALID",
            "precondition",
            f"Unknown frame mode {frame_mode!r}.",
            requested=requested,
            allowed_values=list(CAPTURE_FRAME_MODES),
        )
    if annotation_mode not in CAPTURE_ANNOTATION_MODES:
        return _remember_failure(
            service,
            "SKETCH_ANNOTATION_MODE_INVALID",
            "precondition",
            f"Unknown sketch_annotations mode {annotation_mode!r}.",
            requested=requested,
            allowed_values=list(CAPTURE_ANNOTATION_MODES),
        )

    try:
        import FreeCAD as App
        import FreeCADGui as Gui
    except Exception as exc:
        return _remember_failure(
            service,
            "FREECAD_GUI_UNAVAILABLE",
            "precondition",
            str(exc),
            requested=requested,
        )

    document = App.ActiveDocument
    if document is None:
        return _remember_failure(
            service,
            "NO_ACTIVE_DOCUMENT",
            "precondition",
            "No active document.",
            requested=requested,
        )
    gui_document = getattr(Gui, "ActiveDocument", None)
    view = getattr(gui_document, "ActiveView", None) if gui_document else None
    if view is None:
        return _remember_failure(
            service,
            "NO_ACTIVE_3D_VIEW",
            "precondition",
            "No active 3D view is available.",
            requested=requested,
        )

    active_sketch = service._get_sketch()
    camera_resolution = core_set_view.resolve_camera_request(
        camera,
        allow_auto=True,
        default_mode="auto",
        active_sketch=active_sketch is not None,
    )
    if not camera_resolution["ok"]:
        return _remember_failure(
            service,
            "CAMERA_REQUEST_INVALID",
            "precondition",
            str(camera_resolution.get("error") or "Camera could not be resolved."),
            requested=requested,
            observed={
                key: value
                for key, value in camera_resolution.items()
                if key not in {"ok", "error"}
            },
        )
    resolved_frame = frame_mode
    if resolved_frame == "auto":
        resolved_frame = "active_sketch" if active_sketch is not None else "all"

    frame_resolution = core_set_view.resolve_frame_objects(
        service,
        document,
        Gui,
        resolved_frame,
        object_names,
    )
    if not frame_resolution["ok"]:
        return _remember_failure(
            service,
            "FRAME_TARGET_INVALID",
            "precondition",
            str(
                frame_resolution.get("error")
                or "Viewport target could not be resolved."
            ),
            requested=requested,
            observed={
                key: value
                for key, value in frame_resolution.items()
                if key not in {"ok", "error"}
            },
            candidates=core_set_view._view_object_candidates(document),
        )
    frame_names = list(frame_resolution.get("object_names") or [])
    normalized = {
        "camera": camera_resolution.get("resolved"),
        "frame": resolved_frame,
        "framed_objects": frame_names,
        "sketch_annotations": annotation_mode,
    }
    stages: list[dict[str, Any]] = []
    path: Path | None = None
    empty_view_path: Path | None = None
    camera_before = core_set_view.camera_state(view)
    camera_result: dict[str, Any] = {}
    framing: dict[str, Any] = {"framed": False, "method": "unchanged"}
    annotations_excluded = False
    information_overlay_excluded = False
    internal_geometry_excluded = False
    temporarily_shown_objects: list[str] = []
    capture_width = 0
    capture_height = 0

    current_stage = "artifact_path"
    try:
        screenshot_dir = _screenshot_artifact_dir(service)
        screenshot_dir.mkdir(parents=True, exist_ok=True)
        document_name = _slug(document.Name or "view")
        path = screenshot_dir / f"{document_name}-{int(time.time() * 1000)}.png"
        stages.append({"stage": current_stage, "ok": True, "path": str(path)})

        current_stage = "capture_size"
        capture_width, capture_height = _capture_size(view)
        stages.append(
            {
                "stage": current_stage,
                "ok": True,
                "size": [capture_width, capture_height],
            }
        )
        current_stage = "document_fingerprint"
        fingerprint_state = _document_visual_fingerprint(document)
        if not fingerprint_state["complete"]:
            stages.append(
                {
                    "stage": current_stage,
                    "ok": False,
                    "errors": fingerprint_state["errors"],
                }
            )
            return _remember_failure(
                service,
                "DOCUMENT_VISUAL_FINGERPRINT_FAILED",
                "precondition",
                "FreeCAD could not read complete visual state for the active document.",
                requested=requested,
                normalized=normalized,
                observed={
                    "stages": stages,
                    "fingerprint_errors": fingerprint_state["errors"],
                    "camera_before": camera_before,
                },
            )
        document_visual_fingerprint = fingerprint_state["sha256"]
        stages.append({"stage": current_stage, "ok": True})
        previous_capture = (
            dict(service._last_view_screenshot)
            if isinstance(service._last_view_screenshot, dict)
            else None
        )

        current_stage = "temporary_view_setup"
        with ExitStack() as stack:
            if resolved_frame not in {"none", "all"}:
                stack.enter_context(
                    core_set_view.temporarily_isolate_objects(document, frame_names)
                )
            if annotation_mode == "clean" and active_sketch is not None:
                annotations_excluded = stack.enter_context(
                    core_set_view.temporarily_detach_sketch_annotations(view)
                )
                information_overlay_excluded = stack.enter_context(
                    core_set_view.temporarily_detach_sketch_information_overlay(view)
                )
                internal_geometry_excluded = stack.enter_context(
                    core_set_view.temporarily_hide_sketch_internal_geometry(view)
                )
            stages.append(
                {
                    "stage": current_stage,
                    "ok": True,
                    "temporary": True,
                    "isolated_objects": frame_names
                    if resolved_frame not in {"none", "all"}
                    else [],
                    "temporarily_shown_objects": temporarily_shown_objects,
                    "annotations_excluded": bool(annotations_excluded),
                }
            )

            current_stage = "camera"
            camera_result = core_set_view.apply_camera(view, camera_resolution)
            stages.append({"stage": current_stage, "ok": True, "result": camera_result})
            current_stage = "framing"
            if resolved_frame != "none":
                framing = core_set_view.frame_view(
                    service,
                    view,
                    document,
                    resolved_frame,
                    frame_names,
                    exclude_sketch_annotations=False,
                )
            stages.append({"stage": current_stage, "ok": True, "result": framing})
            current_stage = "save_image"
            capture_started = time.monotonic()
            _capture_framebuffer(
                view,
                path,
                capture_width,
                capture_height,
            )
            capture_elapsed_ms = round(
                (time.monotonic() - capture_started) * 1000.0,
                3,
            )
            stages.append(
                {
                    "stage": current_stage,
                    "ok": path.exists(),
                    "backend": "current_framebuffer",
                    "elapsed_ms": capture_elapsed_ms,
                }
            )
            current_stage = "empty_view_reference"
            empty_view_path = path.with_name(f".{path.stem}-empty.png")
            visible_document_objects = [
                obj.Name
                for obj in list(document.Objects)
                if getattr(obj, "ViewObject", None) is not None
                and bool(obj.ViewObject.Visibility)
            ]
            with core_set_view.temporarily_hide_objects(
                document,
                visible_document_objects,
            ):
                _capture_framebuffer(
                    view,
                    empty_view_path,
                    capture_width,
                    capture_height,
                )
            stages.append(
                {
                    "stage": current_stage,
                    "ok": empty_view_path.exists(),
                    "hidden_object_count": len(visible_document_objects),
                }
            )
        current_stage = "restore_temporary_view"
        view.redraw()
        stages.append({"stage": current_stage, "ok": True})
    except Exception as exc:
        if empty_view_path is not None:
            empty_view_path.unlink(missing_ok=True)
        stages.append(
            {
                "stage": current_stage,
                "ok": False,
                "exception_type": exc.__class__.__name__,
                "error": str(exc),
            }
        )
        return _remember_failure(
            service,
            f"SCREENSHOT_{current_stage.upper()}_FAILED",
            "native_call",
            str(exc),
            requested=requested,
            normalized=normalized,
            observed={
                "failure_stage": current_stage,
                "stages": stages,
                "camera_before": camera_before,
                "camera_after": _safe_camera_state(view),
                "temporary_changes_restored": current_stage
                not in {"artifact_path", "capture_size", "document_fingerprint"},
            },
            artifact=_artifact_state(path),
        )

    artifact = _artifact_state(path)
    if not artifact["created"]:
        return _remember_failure(
            service,
            "SCREENSHOT_FILE_NOT_CREATED",
            "postcondition",
            "View saveImage did not create a file.",
            requested=requested,
            normalized=normalized,
            observed={"stages": stages, "camera_after": _safe_camera_state(view)},
            artifact=artifact,
        )

    current_stage = "pixel_fingerprint"
    try:
        assert path is not None
        visual_fingerprint = _pixel_fingerprint(path)
        stages.append({"stage": current_stage, "ok": True})
    except Exception as exc:
        stages.append({"stage": current_stage, "ok": False, "error": str(exc)})
        return _remember_failure(
            service,
            "SCREENSHOT_FINGERPRINT_FAILED",
            "postcondition",
            str(exc),
            requested=requested,
            normalized=normalized,
            observed={"stages": stages, "camera_after": _safe_camera_state(view)},
            artifact=artifact,
        )

    current_stage = "visual_postcondition"
    try:
        visual_observation = service._screenshot_visual_observation(
            path,
            background_path=empty_view_path,
        )
    finally:
        if empty_view_path is not None:
            empty_view_path.unlink(missing_ok=True)
    if not bool(visual_observation.get("available")):
        return _remember_failure(
            service,
            "SCREENSHOT_OBSERVATION_FAILED",
            "postcondition",
            str(visual_observation.get("error") or "Screenshot could not be inspected."),
            requested=requested,
            normalized=normalized,
            observed={
                "stages": stages,
                "camera_after": _safe_camera_state(view),
                "visual_observation": visual_observation,
            },
            artifact=artifact,
        )
    if (
        frame_names
        and _targets_expect_visible_area(document, frame_names)
        and bool(visual_observation.get("mostly_blank"))
    ):
        return _remember_failure(
            service,
            "SCREENSHOT_TARGET_NOT_RENDERED",
            "postcondition",
            "The requested CAD targets did not produce visible pixels in the captured frame.",
            requested=requested,
            normalized=normalized,
            observed={
                "stages": stages,
                "framed_objects": frame_names,
                "temporarily_shown_objects": temporarily_shown_objects,
                "camera_after": _safe_camera_state(view),
                "visual_observation": visual_observation,
            },
            artifact=artifact,
        )
    stages.append(
        {"stage": current_stage, "ok": True, "result": visual_observation}
    )

    try:
        new_observation = True
        duplicate_of = None
        visual_difference = None
        if (
            previous_capture is not None
            and previous_capture.get("captured")
            and previous_capture.get("document") == document.Name
            and previous_capture.get("document_visual_fingerprint")
            == document_visual_fingerprint
        ):
            previous_path = Path(str(previous_capture.get("path") or ""))
            if previous_path.is_file():
                visual_difference = _pixel_difference(path, previous_path)
                if (
                    visual_difference is not None
                    and visual_difference <= DUPLICATE_VISUAL_DIFFERENCE_THRESHOLD
                ):
                    path.unlink()
                    path = previous_path
                    new_observation = False
                    duplicate_of = str(previous_path)
        stages.append(
            {
                "stage": "duplicate_comparison",
                "ok": True,
                "visual_difference": visual_difference,
                "duplicate_of": duplicate_of,
            }
        )
    except Exception as exc:
        stages.append({"stage": "duplicate_comparison", "ok": False, "error": str(exc)})
        return _remember_failure(
            service,
            "SCREENSHOT_COMPARISON_FAILED",
            "postcondition",
            str(exc),
            requested=requested,
            normalized=normalized,
            observed={"stages": stages, "camera_after": _safe_camera_state(view)},
            artifact=_artifact_state(path),
        )

    try:
        result_size = [capture_width, capture_height]
        if not new_observation and previous_capture is not None:
            prior_size = previous_capture.get("size")
            if isinstance(prior_size, list) and len(prior_size) == 2:
                result_size = list(prior_size)
        result = {
            "ok": True,
            "captured": True,
            # A viewport capture is a message attachment, not durable provider
            # context.  The session consumes this flag after handing one copy to
            # the next provider request (or to the screenshot tool result).
            "pending_attachment": True,
            "path": str(path),
            "file_size": path.stat().st_size,
            "size": result_size,
            "format": "png",
            "background": "Current",
            "capture_backend": "current_framebuffer",
            "camera": camera_result,
            "frame": resolved_frame,
            "framing": framing,
            "framed_objects": frame_names,
            "sketch_annotations": annotation_mode,
            "sketch_annotations_excluded": bool(annotations_excluded),
            "sketch_information_overlay_excluded": bool(information_overlay_excluded),
            "sketch_internal_geometry_excluded": bool(internal_geometry_excluded),
            "artifact_role": "visual_verification",
            "workbench": service.active_workbench_name(),
            "document": document.Name,
            "visual_fingerprint": visual_fingerprint,
            "document_visual_fingerprint": document_visual_fingerprint,
            "new_observation": new_observation,
            "requested": requested,
            "normalized": normalized,
            "stages": stages,
            "view_changes": {
                "camera": {
                    "temporary": False,
                    "before": camera_before,
                    "after": _safe_camera_state(view),
                },
                "object_isolation": {"temporary": True, "restored": True},
                "temporarily_shown_objects": {
                    "objects": temporarily_shown_objects,
                    "temporary": True,
                    "restored": True,
                },
                "sketch_annotations": {"temporary": True, "restored": True},
            },
            "artifact": _artifact_state(path),
        }
        if visual_difference is not None:
            result["visual_difference_from_previous"] = visual_difference
        if duplicate_of:
            result.update(
                {
                    "duplicate_of": duplicate_of,
                    "observation_status": (
                        "The CAD scene is unchanged and the viewport is visually "
                        "indistinguishable from the previous capture. "
                        "No new visual evidence was produced; edit the model or change "
                        "the view before capturing again."
                    ),
                }
            )
        result["visual_observation"] = visual_observation
        stages.append({"stage": "visual_observation", "ok": True})
        service._last_view_screenshot = result
        target = {
            "frame": resolved_frame,
            "object_count": len(frame_names),
        }
        if resolved_frame in {"objects", "selection", "active_sketch"}:
            target["object_names"] = frame_names
        concise_observation = {
            key: visual_observation[key]
            for key in (
                "inspection_summary",
                "layout_summary",
                "attention_flags",
                "mostly_blank",
                "foreground_pixel_ratio",
            )
            if visual_observation.get(key) not in (None, "", [], {})
        }
        return {
            "ok": True,
            "captured": True,
            "document": document.Name,
            "target": target,
            "camera": camera_resolution.get("resolved"),
            "size": result_size,
            "new_observation": new_observation,
            **({"duplicate_of": duplicate_of} if duplicate_of else {}),
            "visual_observation": concise_observation,
            "artifact": _artifact_state(path),
            "pending_attachment": True,
            "_vibecad_image_attachment": {
                "path": str(path),
                "name": f"{document.Name} viewport",
            },
        }
    except Exception as exc:
        stages.append({"stage": "visual_observation", "ok": False, "error": str(exc)})
        return _remember_failure(
            service,
            "SCREENSHOT_OBSERVATION_FAILED",
            "postcondition",
            str(exc),
            requested=requested,
            normalized=normalized,
            observed={"stages": stages, "camera_after": _safe_camera_state(view)},
            artifact=_artifact_state(path),
        )


def _remember_failure(
    service: Any,
    failure_code: str,
    failure_stage: str,
    error: str,
    *,
    requested: dict[str, Any],
    normalized: dict[str, Any] | None = None,
    observed: dict[str, Any] | None = None,
    candidates: list[dict[str, Any]] | None = None,
    allowed_values: Any = None,
    artifact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    artifact_state = artifact or {"created": False, "path": None, "file_size": 0}
    raw_observed = observed or {}
    compact_observed = {
        key: value
        for key, value in raw_observed.items()
        if key not in {"stages", "camera_after", "camera_before"}
    }
    stages = raw_observed.get("stages")
    if isinstance(stages, list):
        failed_stages = [
            {
                key: value
                for key, value in stage.items()
                if key in {"stage", "ok", "error"}
            }
            for stage in stages
            if isinstance(stage, dict) and stage.get("ok") is False
        ]
        if failed_stages:
            compact_observed["failed_stages"] = failed_stages
    result = tool_failure(
        TOOL_SPEC["name"],
        failure_code,
        failure_stage,
        error,
        requested=requested,
        normalized=normalized or {},
        observed=compact_observed,
        candidates=candidates or [],
        allowed_values=allowed_values or [],
        artifact=artifact_state,
        captured=bool(artifact_state.get("created")),
        pending_attachment=bool(artifact_state.get("created")),
        path=artifact_state.get("path"),
        file_size=int(artifact_state.get("file_size") or 0),
    )
    if artifact_state.get("created") and artifact_state.get("path"):
        result["_vibecad_image_attachment"] = {
            "path": str(artifact_state["path"]),
            "name": "failed viewport capture",
        }
    service._last_view_screenshot = result
    return result


def _capture_framebuffer(
    view: Any,
    path: Path,
    width: int,
    height: int,
) -> None:
    """Capture one completely rendered frame from the active viewer."""
    try:
        from PySide import QtCore
    except Exception:
        from PySide6 import QtCore
    import FreeCADGui as Gui

    get_viewer = getattr(view, "getViewer", None)
    if not callable(get_viewer):
        raise RuntimeError("The active 3D view does not expose its framebuffer viewer.")
    viewer = get_viewer()
    grab = getattr(viewer, "grabFramebuffer", None)
    if not callable(grab):
        raise RuntimeError("The active 3D viewer does not expose framebuffer capture.")
    view.redraw()
    # redraw() schedules Coin/Qt painting, but grabFramebuffer() can observe an
    # incomplete buffer immediately after document activation or a large scene
    # change. Flush that already-scheduled GUI work before reading pixels. This
    # is the same synchronization boundary used by core.set_view and does not
    # change the document, camera, visibility, or tool contract.
    Gui.updateGui()
    image = grab()
    if image is None or image.isNull():
        raise RuntimeError("The active 3D viewer returned an empty framebuffer.")
    if image.width() != width or image.height() != height:
        image = image.scaled(
            width,
            height,
            QtCore.Qt.IgnoreAspectRatio,
            QtCore.Qt.SmoothTransformation,
        )
    if not image.save(str(path), "PNG"):
        raise RuntimeError(f"Qt could not save the captured framebuffer to {path}.")


def _targets_expect_visible_area(document: Any, object_names: list[str]) -> bool:
    for name in object_names:
        obj = document.getObject(name)
        if obj is None:
            continue
        shape = getattr(obj, "Shape", None)
        if shape is not None and not bool(shape.isNull()) and len(shape.Faces) > 0:
            return True
        mesh = getattr(obj, "Mesh", None)
        if mesh is not None and int(getattr(mesh, "CountFacets", 0) or 0) > 0:
            return True
    return False


def _capture_size(view: Any) -> tuple[int, int]:
    width, height = view.getSize()
    width = int(width)
    height = int(height)
    if width <= 0 or height <= 0:
        raise RuntimeError("The active viewport has invalid dimensions.")
    scale = min(1280.0 / width, 900.0 / height)
    return max(1, int(round(width * scale))), max(1, int(round(height * scale)))


def _artifact_state(path: Path | None) -> dict[str, Any]:
    created = bool(path is not None and path.is_file())
    return {
        "created": created,
        "path": str(path) if created else str(path) if path is not None else None,
        "file_size": int(path.stat().st_size) if created and path is not None else 0,
    }


def _safe_camera_state(view: Any) -> dict[str, Any]:
    try:
        return core_set_view.camera_state(view)
    except Exception as exc:
        return {
            "available": False,
            "exception_type": exc.__class__.__name__,
            "error": str(exc),
        }


def _pixel_fingerprint(path: Path) -> str:
    from PySide import QtGui
    image = QtGui.QImage(str(path))
    if image.isNull():
        raise RuntimeError(f"Qt could not decode screenshot image: {path}")
    sample = image.scaled(96, 96)
    pixels = bytearray()
    for y in range(sample.height()):
        for x in range(sample.width()):
            color = QtGui.QColor(sample.pixel(x, y))
            pixels.extend((color.red(), color.green(), color.blue()))
    return hashlib.sha256(pixels).hexdigest()


def _pixel_difference(first_path: Path, second_path: Path) -> float:
    from PySide import QtGui
    first = QtGui.QImage(str(first_path))
    second = QtGui.QImage(str(second_path))
    if first.isNull() or second.isNull():
        raise RuntimeError(
            f"Qt could not decode images for comparison: {first_path}, {second_path}"
        )
    first = first.scaled(96, 96)
    second = second.scaled(96, 96)
    difference = 0
    samples = 0
    for y in range(96):
        for x in range(96):
            # The navigation cube animates independently of CAD/view state.
            if x >= 76 and y <= 24:
                continue
            first_color = QtGui.QColor(first.pixel(x, y))
            second_color = QtGui.QColor(second.pixel(x, y))
            difference += abs(first_color.red() - second_color.red())
            difference += abs(first_color.green() - second_color.green())
            difference += abs(first_color.blue() - second_color.blue())
            samples += 3
    return round(difference / max(1, samples * 255), 8)


def _document_visual_fingerprint(document: Any) -> dict[str, Any]:
    records = []
    errors: list[dict[str, str]] = []
    for obj in list(document.Objects):
        shape = getattr(obj, "Shape", None)
        shape_hash = None
        shape_hash_error = None
        type_id = str(getattr(obj, "TypeId", "") or "")
        is_reference_geometry = type_id in {
            "PartDesign::Line",
            "PartDesign::Plane",
            "PartDesign::Point",
            "App::Line",
            "App::Plane",
            "App::Point",
        }
        try:
            if (
                not is_reference_geometry
                and shape is not None
                and not bool(shape.isNull())
            ):
                shape_hash = int(shape.hashCode())
        except Exception as exc:
            shape_hash_error = str(exc)
            errors.append(
                {"object": str(obj.Name), "field": "Shape.hashCode", "error": str(exc)}
            )
        placement_method = getattr(obj, "getGlobalPlacement", None)
        placement_error = None
        placement_source = (
            "object.getGlobalPlacement" if callable(placement_method) else "object.Placement"
        )
        try:
            placement = (
                placement_method()
                if callable(placement_method)
                else getattr(obj, "Placement", None)
            )
        except Exception as exc:
            placement = None
            placement_error = str(exc)
            errors.append(
                {
                    "object": str(obj.Name),
                    "field": placement_source,
                    "error": str(exc),
                }
            )
        placement_matrix = None
        if placement is not None:
            try:
                placement_matrix = [float(value) for value in placement.toMatrix().A]
            except Exception as exc:
                placement_error = str(exc)
                errors.append(
                    {
                        "object": str(obj.Name),
                        "field": f"{placement_source}.toMatrix",
                        "error": str(exc),
                    }
                )
        view_object = getattr(obj, "ViewObject", None)
        records.append(
            {
                "name": str(obj.Name),
                "type": type_id,
                "shape_hash": shape_hash,
                "shape_hash_error": shape_hash_error,
                "placement": placement_matrix,
                "placement_source": placement_source,
                "placement_error": placement_error,
                "visible": bool(view_object.Visibility)
                if view_object is not None
                else None,
                "display_mode": (
                    str(getattr(view_object, "DisplayMode", ""))
                    if view_object is not None
                    else None
                ),
                "transparency": (
                    float(getattr(view_object, "Transparency", 0.0))
                    if view_object is not None
                    else None
                ),
            }
        )
    payload = json.dumps(records, sort_keys=True, separators=(",", ":"))
    return {
        "complete": not errors,
        "sha256": hashlib.sha256(payload.encode("utf-8")).hexdigest(),
        "errors": errors,
        "object_count": len(records),
    }


def _screenshot_artifact_dir(service: Any) -> Path:
    project_context = service.project_context()
    root = project_context.get("root") if isinstance(project_context, dict) else None
    if not root:
        raise RuntimeError("The active document has no VibeCAD project root.")
    return Path(str(root)).expanduser() / "screenshots"


def _slug(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9_.-]+", "_", str(value or "").strip()).strip("._")
    return slug[:64] or "view"
