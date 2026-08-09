# SPDX-License-Identifier: LGPL-2.1-or-later

"""Direct presentation capabilities shared by every Native surface."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Mapping

from VibeCADNativeTargets import NativeObjectRef, document_uid, resolve_object


MAX_NATIVE_SCREENSHOT_BYTES = 20 * 1024 * 1024
MAX_NATIVE_SCREENSHOT_RESULT_BYTES = 64 * 1024
MAX_NATIVE_ARTIFACT_PATH_CHARACTERS = 4096


class NativeViewError(RuntimeError):
    """The exact active view cannot satisfy a presentation request."""

    def failure(self) -> dict[str, str]:
        return {"error_code": "NATIVE_VIEW_FAILED", "message": str(self)}


def _active_view(document: Any, gui: Any | None = None) -> Any:
    if gui is None:
        import FreeCADGui as Gui

        gui = Gui
    gui_document = gui.activeDocument()
    if gui_document is None:
        raise NativeViewError("The exact document has no active GUI view.")
    gui_model = getattr(gui_document, "Document", None)
    if gui_model is not None and gui_model is not document:
        raise NativeViewError("The active GUI view belongs to another document.")
    active_view = getattr(gui_document, "activeView", None)
    view = (
        active_view()
        if callable(active_view)
        else getattr(gui_document, "ActiveView", None)
    )
    if view is None:
        raise NativeViewError("The exact document has no active 3D view.")
    return view


def fit_all(document: Any, *, gui: Any | None = None) -> dict[str, bool]:
    document_uid(document)
    view = _active_view(document, gui)
    fit = getattr(view, "fitAll", None)
    if not callable(fit):
        raise NativeViewError("The active 3D view cannot fit visible geometry.")
    fit()
    return {"fit_all": True}


def set_isometric(document: Any, *, gui: Any | None = None) -> dict[str, str]:
    document_uid(document)
    view = _active_view(document, gui)
    orient = getattr(view, "viewAxonometric", None)
    if not callable(orient):
        raise NativeViewError("The active 3D view cannot set isometric orientation.")
    orient()
    return {"orientation": "isometric"}


def set_grid_visible(document: Any, visible: bool) -> dict[str, bool]:
    document_uid(document)
    if type(visible) is not bool:
        raise TypeError("visible must be a boolean")
    from VibeCADGrid import is_grid_visible, toggle_grid

    toggle_grid(visible)
    observed = bool(is_grid_visible())
    for _cycle in range(8):
        if observed == visible:
            break
        try:
            import FreeCADGui as Gui
            from PySide import QtCore, QtWidgets

            Gui.updateGui()
            QtWidgets.QApplication.processEvents(
                QtCore.QEventLoop.AllEvents,
                25,
            )
        except Exception:
            break
        observed = bool(is_grid_visible())
    if observed != visible:
        raise NativeViewError("The active 3D grid did not reach the requested state.")
    return {"grid_visible": observed}


def capture_screenshot(
    service: Any,
    document: Any,
    *,
    frame: str = "all",
    targets: tuple[NativeObjectRef, ...] = (),
) -> dict[str, Any]:
    uid = document_uid(document)
    if str(getattr(service._active_document(), "Uid", "") or "") != uid:
        raise NativeViewError("The screenshot target document is no longer active.")
    frame_mode = str(frame or "").strip()
    if frame_mode not in {"all", "selection", "objects", "active_sketch"}:
        raise NativeViewError(
            "Screenshot frame must target all, selection, objects, or active_sketch."
        )
    names = []
    if frame_mode == "objects":
        if not targets:
            raise NativeViewError("Object-framed screenshots require exact targets.")
        names = [resolve_object(document, target).Name for target in targets]
    from tool_impl.service import core_capture_view_screenshot

    raw = core_capture_view_screenshot.run(
        service,
        camera={"mode": "unchanged"},
        frame=frame_mode,
        object_names=names,
        sketch_annotations="clean",
    )
    if not isinstance(raw, Mapping):
        raise NativeViewError("Viewport screenshot capture failed.")
    if raw.get("ok") is not True:
        message = str(raw.get("error") or "Viewport screenshot capture failed.")
        raise NativeViewError(message[:320])
    artifact = (
        raw.get("artifact") if isinstance(raw.get("artifact"), Mapping) else {}
    )
    artifact_path = str(artifact.get("path") or "")
    reported_size = int(
        artifact.get("file_size") or artifact.get("size_bytes") or 0
    )
    if not artifact_path or len(artifact_path) > MAX_NATIVE_ARTIFACT_PATH_CHARACTERS:
        raise NativeViewError("Viewport screenshot returned an invalid artifact path.")
    try:
        path = Path(artifact_path)
        actual_size = int(path.stat().st_size) if path.is_file() else 0
    except OSError as exc:
        raise NativeViewError("Viewport screenshot artifact could not be verified.") from exc
    if (
        actual_size <= 0
        or actual_size > MAX_NATIVE_SCREENSHOT_BYTES
        or reported_size != actual_size
    ):
        raise NativeViewError("Viewport screenshot artifact violates its size bound.")
    result: dict[str, Any] = {
        "captured": True,
        "artifact": {
            "path": artifact_path,
            "size_bytes": actual_size,
        },
    }
    observation = raw.get("visual_observation")
    if isinstance(observation, Mapping) and observation:
        result["visual_observation"] = dict(observation)
    attachment = raw.get("_vibecad_image_attachment")
    if isinstance(attachment, Mapping):
        attachment_path = str(attachment.get("path") or "")
        if attachment_path != artifact_path:
            raise NativeViewError("Viewport screenshot attachment target is inconsistent.")
        result["_vibecad_image_attachment"] = {
            "path": attachment_path,
            "name": str(attachment.get("name") or "viewport")[:160],
        }
    try:
        encoded = json.dumps(result, ensure_ascii=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise NativeViewError("Viewport screenshot result is not serializable.") from exc
    if len(encoded.encode("utf-8")) > MAX_NATIVE_SCREENSHOT_RESULT_BYTES:
        raise NativeViewError("Viewport screenshot result exceeds its bound.")
    return result
