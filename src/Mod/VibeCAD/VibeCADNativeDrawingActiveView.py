# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, path-private capture of the human's active 3D view on a Drawing page."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import tempfile
from typing import Any, Mapping

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeGeometrySources import (
    drawing_analysis_artifact_names,
    drawing_source_exclusion_reason,
    filter_drawing_selection,
)
from VibeCADNativeDrawingState import drawing_page_state, is_drawing_page
from VibeCADNativeMutation import NativeMutationDraft
from VibeCADNativeTargets import object_identity, read_current_selection, resolve_object


DEFAULT_CAPTURE_WIDTH_PX = 1280
DEFAULT_CAPTURE_HEIGHT_PX = 1024
MAX_CAPTURE_DIMENSION_PX = 4096
MAX_CAPTURE_PIXELS = 16 * 1024 * 1024
MAX_CAPTURE_BYTES = 64 * 1024 * 1024
_PROVENANCE_PROPERTIES = {
    "VibeCADViewportStateSHA256": "App::PropertyString",
    "VibeCADImageSHA256": "App::PropertyString",
    "VibeCADImageWidthPx": "App::PropertyInteger",
    "VibeCADImageHeightPx": "App::PropertyInteger",
    "VibeCADImageBackground": "App::PropertyString",
}


@dataclass(frozen=True, slots=True)
class ActiveViewSpec:
    label: str
    x_mm: float
    y_mm: float
    scale: float
    crop: bool
    width_mm: float
    height_mm: float
    background_kind: str
    background_rgb: tuple[int, int, int] | None


@dataclass(frozen=True, slots=True)
class PreparedActiveView:
    page: Any
    page_state_before: dict[str, Any]
    page_views_before: tuple[Any, ...]
    viewport_state_before: dict[str, Any]
    spec: ActiveViewSpec
    objects_before: tuple[Any, ...]
    timeline_before: tuple[Any, ...]
    selection_before: dict[str, Any]
    visibility_before: tuple[tuple[Any, bool], ...]


@dataclass(frozen=True, slots=True)
class CapturedActiveView:
    prepared: PreparedActiveView
    path: Path
    image_sha256: str
    size_bytes: int
    width_px: int
    height_px: int

    def cleanup(self) -> None:
        try:
            self.path.unlink(missing_ok=True)
        except OSError:
            pass


def _timeline_operations(document: Any) -> tuple[Any, ...]:
    timeline = document.getObject("VibeCADTimeline")
    return tuple(getattr(timeline, "Operations", ()) or ()) if timeline else ()


def _identity(obj: Any) -> tuple[int, str, str]:
    return (
        int(getattr(obj, "ID", -1)),
        str(getattr(obj, "Name", "") or ""),
        str(getattr(obj, "TypeId", "") or ""),
    )


def _identities(objects: tuple[Any, ...]) -> tuple[tuple[int, str, str], ...]:
    return tuple(_identity(obj) for obj in objects)


def _visibility(document: Any) -> tuple[tuple[Any, bool], ...]:
    return tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj in tuple(document.Objects)
        if getattr(obj, "ViewObject", None) is not None
    )


def _current_selection(document: Any) -> dict[str, Any]:
    try:
        return read_current_selection(document)
    except (AttributeError, ImportError, RuntimeError):
        return {
            "document_uid": str(document.Uid),
            "selected_count": 0,
            "items": [],
        }


def _finite(value: Any, *, name: str, minimum: float, maximum: float) -> float:
    result = float(value)
    if not math.isfinite(result) or result < minimum or result > maximum:
        raise NativeDrawingError(
            f"{name} must be finite and between {minimum:g} and {maximum:g}.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_PARAMETERS_INVALID",
        )
    return result


def _resolution_pixels_per_mm() -> float:
    import FreeCAD as App

    value = float(
        App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/Rez").GetFloat(
            "Resolution",
            10.0,
        )
    )
    if not math.isfinite(value) or not 0.1 <= value <= 1000.0:
        raise NativeDrawingError(
            "The TechDraw display resolution is outside its supported range.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_RESOLUTION_INVALID",
        )
    return value


def _active_gui_view(document: Any, gui: Any | None = None) -> Any:
    if gui is None:
        import FreeCADGui as Gui

        gui = Gui
    gui_document = gui.activeDocument()
    if gui_document is None:
        raise NativeDrawingError(
            "The exact document has no active GUI view.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_UNAVAILABLE",
        )
    gui_model = getattr(gui_document, "Document", None)
    if gui_model is not None and gui_model is not document:
        raise NativeDrawingError(
            "The active GUI view belongs to another document.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_STALE",
        )
    getter = getattr(gui_document, "activeView", None)
    view = getter() if callable(getter) else getattr(gui_document, "ActiveView", None)
    required = ("getCamera", "getCameraType", "getSize", "saveImage")
    if view is not None and all(
        callable(getattr(view, name, None)) for name in required
    ):
        return view
    candidates_getter = getattr(gui_document, "mdiViewsOfType", None)
    candidates = (
        tuple(candidates_getter("Gui::View3DInventor") or ())
        if callable(candidates_getter)
        else ()
    )
    candidates = tuple(
        candidate
        for candidate in candidates
        if all(callable(getattr(candidate, name, None)) for name in required)
    )
    if len(candidates) == 1:
        return candidates[0]
    if len(candidates) > 1:
        raise NativeDrawingError(
            "Several 3D viewports are open; the human must activate the exact "
            "viewport to capture before starting the turn.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_AMBIGUOUS",
        )
    if view is None or any(not callable(getattr(view, name, None)) for name in required):
        raise NativeDrawingError(
            "The human must leave one 3D viewport open in the exact document.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_UNAVAILABLE",
        )
    return view


def _vector(value: Any) -> list[float]:
    result = [float(getattr(value, name)) for name in ("x", "y", "z")]
    if any(not math.isfinite(component) for component in result):
        raise ValueError("non-finite viewport vector")
    return result


def _color(value: Any) -> list[float] | str:
    if isinstance(value, (tuple, list)):
        return [round(float(component), 8) for component in value]
    try:
        return [
            round(float(getattr(value, name)), 8)
            for name in ("r", "g", "b")
        ]
    except (AttributeError, TypeError, ValueError):
        return str(value)


def _presentation_value(name: str, value: Any) -> bool | int | float | str | list[float]:
    if name in {"ShapeColor", "LineColor", "PointColor"}:
        return _color(value)
    if name == "Visibility":
        return bool(value)
    if name == "DisplayMode":
        return str(value)
    if name == "Transparency":
        return int(value)
    numeric = float(value)
    if not math.isfinite(numeric):
        raise ValueError(f"non-finite {name} presentation value")
    return round(numeric, 8)


def _placement(obj: Any) -> list[float] | None:
    getter = getattr(obj, "getGlobalPlacement", None)
    placement = getter() if callable(getter) else getattr(obj, "Placement", None)
    if placement is None:
        return None
    matrix = placement.toMatrix()
    return [round(float(value), 12) for value in matrix.A]


def _geometry_record(obj: Any) -> tuple[dict[str, Any], bool]:
    shape_hash = None
    shape = getattr(obj, "Shape", None)
    is_null = getattr(shape, "isNull", None)
    hash_code = getattr(shape, "hashCode", None)
    if callable(is_null) and callable(hash_code) and not bool(is_null()):
        shape_hash = int(hash_code())
    mesh = getattr(obj, "Mesh", None)
    mesh_record = None
    if mesh is not None:
        facets = int(getattr(mesh, "CountFacets", 0) or 0)
        points = int(getattr(mesh, "CountPoints", 0) or 0)
        if facets or points:
            bounds = getattr(mesh, "BoundBox", None)
            mesh_record = {
                "facets": facets,
                "points": points,
                "bounds": [
                    round(float(getattr(bounds, name)), 9)
                    for name in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")
                ]
                if bounds is not None
                else None,
            }
    point_cloud = getattr(obj, "Points", None)
    point_count = int(getattr(point_cloud, "Count", 0) or 0) if point_cloud else 0
    has_geometry = shape_hash is not None or mesh_record is not None or point_count > 0
    view = getattr(obj, "ViewObject", None)
    record: dict[str, Any] = {
        "id": int(getattr(obj, "ID", -1)),
        "name": str(obj.Name),
        "type": str(getattr(obj, "TypeId", "") or ""),
        "shape_hash": shape_hash,
        "mesh": mesh_record,
        "point_count": point_count,
        "placement": _placement(obj),
    }
    if view is not None:
        record["presentation"] = {
            name: _presentation_value(name, getattr(view, name))
            for name in (
                "Visibility",
                "DisplayMode",
                "Transparency",
                "ShapeColor",
                "LineColor",
                "PointColor",
                "LineWidth",
                "PointSize",
            )
            if hasattr(view, name)
        }
    visible_geometry = bool(
        has_geometry and view is not None and bool(getattr(view, "Visibility", False))
    )
    return record, visible_geometry


def drawing_active_viewport_state(
    document: Any,
    *,
    gui: Any | None = None,
) -> dict[str, Any]:
    """Return one bounded exact hash of the current 3D viewport and visual scene."""

    view = _active_gui_view(document, gui)
    width, height = (int(value) for value in view.getSize())
    if width <= 0 or height <= 0:
        raise NativeDrawingError(
            "The active 3D viewport has invalid pixel dimensions.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_UNAVAILABLE",
        )
    records = []
    visible_geometry_count = 0
    analysis_artifacts = drawing_analysis_artifact_names(document)
    for obj in tuple(document.Objects):
        if drawing_source_exclusion_reason(
            document,
            obj,
            analysis_artifact_names=analysis_artifacts,
        ) is not None:
            continue
        try:
            record, visible_geometry = _geometry_record(obj)
        except Exception as exc:
            raise NativeDrawingError(
                f"The visual state of {obj.Name!r} could not be inspected.",
                error_code="NATIVE_DRAWING_ACTIVE_VIEW_STATE_UNAVAILABLE",
            ) from exc
        records.append(record)
        visible_geometry_count += int(visible_geometry)
    direction_getter = getattr(view, "getViewDirection", None)
    up_getter = getattr(view, "getUpDirection", None)
    direction = _vector(direction_getter()) if callable(direction_getter) else None
    up = _vector(up_getter()) if callable(up_getter) else None
    resolution = _resolution_pixels_per_mm()
    payload = {
        "document_uid": str(document.Uid),
        "camera": str(view.getCamera()),
        "camera_type": str(view.getCameraType()),
        "view_direction": direction,
        "up_direction": up,
        "viewport_size_px": [width, height],
        "resolution_pixels_per_mm": resolution,
        "selection": filter_drawing_selection(
            document,
            _current_selection(document),
            analysis_artifact_names=analysis_artifacts,
        ),
        "objects": records,
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        "state_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        "camera_type": payload["camera_type"],
        "view_direction": direction,
        "up_direction": up,
        "viewport_size_px": [width, height],
        "resolution_pixels_per_mm": resolution,
        "visible_geometry_count": visible_geometry_count,
    }


def safe_drawing_active_viewport_state(document: Any) -> dict[str, Any] | None:
    try:
        return drawing_active_viewport_state(document)
    except (AttributeError, ImportError, NativeDrawingError, RuntimeError):
        return None


def _active_view_spec(values: Mapping[str, Any]) -> ActiveViewSpec:
    label = str(values["label"] or "").strip()
    if not label or len(label) > 160:
        raise NativeDrawingError(
            "An active-view label must contain 1 to 160 characters.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_PARAMETERS_INVALID",
        )
    position = values["position"]
    if not isinstance(position, Mapping) or set(position) != {"x_mm", "y_mm"}:
        raise NativeDrawingError(
            "Active-view position requires only x_mm and y_mm.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_PARAMETERS_INVALID",
        )
    crop = values["crop"]
    if not isinstance(crop, Mapping):
        raise NativeDrawingError(
            "Active-view crop requires one exact full or rectangle choice.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_PARAMETERS_INVALID",
        )
    crop_kind = str(crop.get("kind") or "")
    resolution = _resolution_pixels_per_mm()
    if crop_kind == "full" and set(crop) == {"kind"}:
        crop_enabled = False
        width_mm = DEFAULT_CAPTURE_WIDTH_PX / resolution
        height_mm = DEFAULT_CAPTURE_HEIGHT_PX / resolution
    elif crop_kind == "rectangle" and set(crop) == {"kind", "width_mm", "height_mm"}:
        crop_enabled = True
        width_mm = _finite(
            crop["width_mm"],
            name="Active-view crop width_mm",
            minimum=0.1,
            maximum=1000.0,
        )
        height_mm = _finite(
            crop["height_mm"],
            name="Active-view crop height_mm",
            minimum=0.1,
            maximum=1000.0,
        )
    else:
        raise NativeDrawingError(
            "Active-view crop must be exactly full or rectangle with width_mm and height_mm.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_PARAMETERS_INVALID",
        )
    background = values["background"]
    if not isinstance(background, Mapping):
        raise NativeDrawingError(
            "Active-view background requires one exact transparent, viewport, or solid choice.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_PARAMETERS_INVALID",
        )
    background_kind = str(background.get("kind") or "")
    background_rgb = None
    if background_kind in {"transparent", "viewport"} and set(background) == {"kind"}:
        pass
    elif background_kind == "solid" and set(background) == {"kind", "rgb"}:
        rgb = background["rgb"]
        if not isinstance(rgb, Mapping) or set(rgb) != {"red", "green", "blue"}:
            raise NativeDrawingError(
                "Solid active-view background requires only red, green, and blue.",
                error_code="NATIVE_DRAWING_ACTIVE_VIEW_PARAMETERS_INVALID",
            )
        components = tuple(rgb[name] for name in ("red", "green", "blue"))
        if any(type(value) is not int or not 0 <= value <= 255 for value in components):
            raise NativeDrawingError(
                "Solid active-view background components must be integers from 0 to 255.",
                error_code="NATIVE_DRAWING_ACTIVE_VIEW_PARAMETERS_INVALID",
            )
        background_rgb = components
    else:
        raise NativeDrawingError(
            "Active-view background must be exactly transparent, viewport, or solid RGB.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_PARAMETERS_INVALID",
        )
    return ActiveViewSpec(
        label=label,
        x_mm=_finite(
            position["x_mm"],
            name="Active-view x_mm",
            minimum=-10_000.0,
            maximum=10_000.0,
        ),
        y_mm=_finite(
            position["y_mm"],
            name="Active-view y_mm",
            minimum=-10_000.0,
            maximum=10_000.0,
        ),
        scale=_finite(
            values["scale"],
            name="Active-view scale",
            minimum=1.0e-6,
            maximum=1000.0,
        ),
        crop=crop_enabled,
        width_mm=width_mm,
        height_mm=height_mm,
        background_kind=background_kind,
        background_rgb=background_rgb,
    )


def _require_usable(document: Any, obj: Any, noun: str) -> None:
    checker = getattr(document, "isObjectUsableAtCurrentTimelinePosition", None)
    if callable(checker) and not bool(checker(obj)):
        raise NativeDrawingError(
            f"The exact {noun} is not usable at the current History position.",
            error_code="NATIVE_DRAWING_HISTORY_TARGET_UNAVAILABLE",
        )


def prepare_active_view_create(
    document: Any,
    *,
    values: Mapping[str, Any],
) -> PreparedActiveView:
    spec = _active_view_spec(values)
    target = values["page"]
    page = resolve_object(
        document,
        {"document_uid": str(document.Uid), "object_name": target["object_name"]},
        expected_types=("TechDraw::DrawPage",),
    )
    page_state = drawing_page_state(page)
    if str(target["expected_state_sha256"]) != page_state["state_sha256"]:
        raise NativeDrawingError(
            "The exact Drawing page changed after it was inspected.",
            error_code="NATIVE_DRAWING_PAGE_STALE",
            repair={"current_state_sha256": page_state["state_sha256"]},
        )
    _require_usable(document, page, "Drawing page")
    viewport_state = drawing_active_viewport_state(document)
    viewport_target = values["viewport"]
    if str(viewport_target["expected_state_sha256"]) != viewport_state["state_sha256"]:
        raise NativeDrawingError(
            "The human's active 3D viewport changed after it was inspected.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_STALE",
            repair={"current_state_sha256": viewport_state["state_sha256"]},
        )
    if int(viewport_state["visible_geometry_count"]) < 1:
        raise NativeDrawingError(
            "The active 3D viewport contains no visible model geometry to capture.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_EMPTY",
        )
    return PreparedActiveView(
        page=page,
        page_state_before=page_state,
        page_views_before=tuple(getattr(page, "Views", ()) or ()),
        viewport_state_before=viewport_state,
        spec=spec,
        objects_before=tuple(document.Objects),
        timeline_before=_timeline_operations(document),
        selection_before=_current_selection(document),
        visibility_before=_visibility(document),
    )


def _capture_dimensions(prepared: PreparedActiveView) -> tuple[int, int]:
    if not prepared.spec.crop:
        return DEFAULT_CAPTURE_WIDTH_PX, DEFAULT_CAPTURE_HEIGHT_PX
    resolution = float(prepared.viewport_state_before["resolution_pixels_per_mm"])
    width = max(1, int(round(prepared.spec.width_mm * resolution)))
    height = max(1, int(round(prepared.spec.height_mm * resolution)))
    if (
        width > MAX_CAPTURE_DIMENSION_PX
        or height > MAX_CAPTURE_DIMENSION_PX
        or width * height > MAX_CAPTURE_PIXELS
    ):
        raise NativeDrawingError(
            "The requested active-view crop exceeds the published raster bounds.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_SIZE_INVALID",
            repair={
                "maximum_dimension_px": MAX_CAPTURE_DIMENSION_PX,
                "maximum_pixels": MAX_CAPTURE_PIXELS,
            },
        )
    return width, height


def _background_argument(spec: ActiveViewSpec) -> str:
    if spec.background_kind == "transparent":
        return "Transparent"
    if spec.background_kind == "viewport":
        return "Current"
    assert spec.background_rgb is not None
    return "#{:02x}{:02x}{:02x}".format(*spec.background_rgb)


def capture_active_view_image(
    document: Any,
    *,
    prepared: PreparedActiveView,
) -> CapturedActiveView:
    if not isinstance(prepared, PreparedActiveView):
        raise TypeError("prepared must be a PreparedActiveView")
    current = drawing_active_viewport_state(document)
    if current["state_sha256"] != prepared.viewport_state_before["state_sha256"]:
        raise NativeDrawingError(
            "The human's active 3D viewport changed before capture.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_STALE",
            repair={"current_state_sha256": current["state_sha256"]},
        )
    width, height = _capture_dimensions(prepared)
    view = _active_gui_view(document)
    handle = tempfile.NamedTemporaryFile(
        prefix="vibecad-active-view-",
        suffix=".png",
        delete=False,
    )
    path = Path(handle.name)
    handle.close()
    try:
        view.saveImage(
            str(path),
            width,
            height,
            _background_argument(prepared.spec),
            "",
            8,
        )
        content = path.read_bytes()
        if (
            not content.startswith(b"\x89PNG\r\n\x1a\n")
            or not 0 < len(content) <= MAX_CAPTURE_BYTES
        ):
            raise NativeDrawingError(
                "The active 3D viewport did not produce a bounded PNG image.",
                error_code="NATIVE_DRAWING_ACTIVE_VIEW_CAPTURE_FAILED",
            )
        from PySide import QtGui

        image = QtGui.QImage(str(path))
        if image.isNull() or image.width() != width or image.height() != height:
            raise NativeDrawingError(
                "The active-view PNG dimensions do not match the exact request.",
                error_code="NATIVE_DRAWING_ACTIVE_VIEW_CAPTURE_FAILED",
            )
        return CapturedActiveView(
            prepared=prepared,
            path=path,
            image_sha256=hashlib.sha256(content).hexdigest(),
            size_bytes=len(content),
            width_px=width,
            height_px=height,
        )
    except Exception:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _add_capture_provenance(view: Any, captured: CapturedActiveView) -> None:
    for name, property_type in _PROVENANCE_PROPERTIES.items():
        if name not in set(getattr(view, "PropertiesList", ()) or ()):
            view.addProperty(
                property_type,
                name,
                "VibeCAD",
                "Immutable provenance for the Native active-view capture.",
            )
    spec = captured.prepared.spec
    background = spec.background_kind
    if spec.background_rgb is not None:
        background += ":#{:02x}{:02x}{:02x}".format(*spec.background_rgb)
    view.VibeCADViewportStateSHA256 = captured.prepared.viewport_state_before[
        "state_sha256"
    ]
    view.VibeCADImageSHA256 = captured.image_sha256
    view.VibeCADImageWidthPx = captured.width_px
    view.VibeCADImageHeightPx = captured.height_px
    view.VibeCADImageBackground = background
    for name in _PROVENANCE_PROPERTIES:
        view.setEditorMode(name, 2)


def _embedded_image(view: Any) -> tuple[Path, bytes]:
    path = Path(str(getattr(view, "ImageIncluded", "") or ""))
    try:
        content = path.read_bytes()
    except OSError as exc:
        raise NativeDrawingError(
            "The active-view image was not embedded in the document.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_POSTCONDITION_FAILED",
        ) from exc
    return path, content


def create_active_view(
    document: Any,
    *,
    captured: CapturedActiveView,
) -> NativeMutationDraft:
    if not isinstance(captured, CapturedActiveView):
        raise TypeError("captured must be a CapturedActiveView")
    prepared = captured.prepared
    content = captured.path.read_bytes()
    if (
        len(content) != captured.size_bytes
        or hashlib.sha256(content).hexdigest() != captured.image_sha256
    ):
        raise NativeDrawingError(
            "The captured active-view PNG changed before document insertion.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_CAPTURE_STALE",
        )
    view = document.addObject("TechDraw::DrawViewImage", "ActiveView")
    if view is None or not bool(view.isDerivedFrom("TechDraw::DrawViewImage")):
        raise NativeDrawingError(
            "The active-view factory returned the wrong object type.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_CREATE_FAILED",
    )
    spec = prepared.spec
    view.Label = spec.label
    view.ScaleType = "Custom"
    view.Scale = spec.scale
    view.Width = spec.width_mm
    view.Height = spec.height_mm
    _add_capture_provenance(view, captured)
    document.publishProvisionalTimelineOperationBlock(view, (), ())
    prepared.page.addView(view)
    # TechDraw initializes a newly added page view at the page's default
    # position. Apply the exact requested placement after page membership.
    view.X = spec.x_mm
    view.Y = spec.y_mm
    view.ImageFile = str(captured.path)
    view.ViewObject.Crop = spec.crop
    return NativeMutationDraft(
        value={"captured": captured, "view": view},
        recompute_targets=(view, prepared.page),
        created=(object_identity(view),),
        changed=(object_identity(prepared.page),),
    )


def drawing_active_view_image_state(view: Any) -> dict[str, Any]:
    if view is None or not bool(view.isDerivedFrom("TechDraw::DrawViewImage")):
        raise TypeError("view must be a TechDraw::DrawViewImage")
    _path, content = _embedded_image(view)
    payload = {
        "object_name": str(view.Name),
        "label": str(view.Label),
        "type_id": str(view.TypeId),
        "x_mm": float(view.X),
        "y_mm": float(view.Y),
        "scale": float(view.Scale),
        "crop": bool(view.ViewObject.Crop),
        "width_mm": float(view.Width),
        "height_mm": float(view.Height),
        "image_sha256": hashlib.sha256(content).hexdigest(),
        "image_size_bytes": len(content),
        "image_width_px": int(getattr(view, "VibeCADImageWidthPx", 0) or 0),
        "image_height_px": int(getattr(view, "VibeCADImageHeightPx", 0) or 0),
        "background": str(getattr(view, "VibeCADImageBackground", "") or ""),
        "viewport_state_sha256": str(
            getattr(view, "VibeCADViewportStateSHA256", "") or ""
        ),
        "timeline_role": str(getattr(view, "VibeCADTimelineRole", "") or ""),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return {
        **payload,
        "state_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
    }


def _assert_presentation_unchanged(
    document: Any,
    prepared: PreparedActiveView,
) -> None:
    if _current_selection(document) != prepared.selection_before:
        raise NativeDrawingError(
            "Active-view creation changed the human selection.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_POSTCONDITION_FAILED",
        )
    actual = tuple(
        (obj, bool(obj.ViewObject.Visibility))
        for obj, _visible in prepared.visibility_before
    )
    if actual != prepared.visibility_before:
        raise NativeDrawingError(
            "Active-view creation changed existing object visibility.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_POSTCONDITION_FAILED",
        )


def verify_active_view_create(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    captured: CapturedActiveView = draft.value["captured"]
    prepared = captured.prepared
    view = draft.value["view"]
    object_ids_before = set(_identities(prepared.objects_before))
    new_objects = tuple(
        obj for obj in document.Objects if _identity(obj) not in object_ids_before
    )
    embedded_path, embedded_content = _embedded_image(view)
    transient = Path(str(document.TransientDir)).resolve()
    try:
        embedded_private = embedded_path.resolve().is_relative_to(transient)
    except (OSError, ValueError):
        embedded_private = False
    spec = prepared.spec
    page_views = tuple(getattr(prepared.page, "Views", ()) or ())
    timeline_expected = (*prepared.timeline_before, view)
    checks = {
        "created_objects": _identities(new_objects) == (_identity(view),),
        "page_type": is_drawing_page(prepared.page),
        "view_type": bool(view.isDerivedFrom("TechDraw::DrawViewImage")),
        "label": str(view.Label) == spec.label,
        "x_mm": math.isclose(float(view.X), spec.x_mm, abs_tol=1.0e-9),
        "y_mm": math.isclose(float(view.Y), spec.y_mm, abs_tol=1.0e-9),
        "scale_type": str(view.ScaleType) == "Custom",
        "scale": math.isclose(float(view.Scale), spec.scale, abs_tol=1.0e-12),
        "crop": bool(view.ViewObject.Crop) is spec.crop,
        "width_mm": math.isclose(float(view.Width), spec.width_mm, abs_tol=1.0e-9),
        "height_mm": math.isclose(
            float(view.Height),
            spec.height_mm,
            abs_tol=1.0e-9,
        ),
        "page_views": _identities(page_views)
        == _identities((*prepared.page_views_before, view)),
        "timeline_role": str(getattr(view, "VibeCADTimelineRole", "") or "")
        == "operation",
        "timeline_owner": getattr(view, "VibeCADTimelineOwner", None) is None,
        "timeline_order": _identities(_timeline_operations(document))
        == _identities(timeline_expected),
        "view_valid": bool(view.isValid()),
        "embedded_private": embedded_private,
        "embedded_size": len(embedded_content) == captured.size_bytes,
        "embedded_sha256": hashlib.sha256(embedded_content).hexdigest()
        == captured.image_sha256,
        "viewport_sha256": str(view.VibeCADViewportStateSHA256)
        == prepared.viewport_state_before["state_sha256"],
        "provenance_sha256": str(view.VibeCADImageSHA256)
        == captured.image_sha256,
        "image_width_px": int(view.VibeCADImageWidthPx) == captured.width_px,
        "image_height_px": int(view.VibeCADImageHeightPx) == captured.height_px,
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    if failed_checks:
        raise NativeDrawingError(
            "The active-view image did not retain its exact page, capture, and "
            f"History state: {', '.join(failed_checks)}.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_POSTCONDITION_FAILED",
            repair={"failed_checks": failed_checks},
        )
    page_state = drawing_page_state(prepared.page)
    if (
        page_state["view_count"] != prepared.page_state_before["view_count"] + 1
        or page_state["view_names"][-1:] != [str(view.Name)]
    ):
        raise NativeDrawingError(
            "The exact Drawing page did not retain the new active view.",
            error_code="NATIVE_DRAWING_ACTIVE_VIEW_POSTCONDITION_FAILED",
        )
    _assert_presentation_unchanged(document, prepared)
    state = drawing_active_view_image_state(view)
    return {
        "page": {
            "object_name": page_state["object_name"],
            "state_sha256": page_state["state_sha256"],
            "view_count": page_state["view_count"],
        },
        "view": state,
        "capture": {
            "mime_type": "image/png",
            "sha256": captured.image_sha256,
            "size_bytes": captured.size_bytes,
            "size_px": [captured.width_px, captured.height_px],
            "background": state["background"],
            "crop": spec.crop,
        },
    }
