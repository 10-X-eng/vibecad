# SPDX-License-Identifier: LGPL-2.1-or-later

"""SolidWorks/Fusion-style 3D section view for VibeCAD.

Cuts the active 3D view with a Front (XY), Top (XZ), or Right (YZ) plane
through the model. Offset slides the plane along its normal; Flip keeps the
opposite half. The clip is a plain Inventor clipping plane, not the Coin
manipulator, and does not change model geometry.

The native ``VibeCAD_SectionView`` command owns the View-ribbon action. This
module inspects and applies the active Inventor view's clipping plane; when no
3D view is open it reports inactive and does not create a clip.

The module imports safely outside FreeCAD (guarded imports) so tooling such
as linters and test collectors can load it.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any, Iterable

try:
    import FreeCAD as App
except ImportError:  # pragma: no cover - only outside FreeCAD (tooling/tests)
    App = None  # type: ignore[assignment]


SECTION_PLANES = ("front", "top", "right")
_PLANE_NORMALS = {
    "front": (0.0, 0.0, 1.0),
    "top": (0.0, 1.0, 0.0),
    "right": (1.0, 0.0, 0.0),
}
_OVERLAY_NAME = "VibeCADSectionPlaneOverlay"


@dataclass(frozen=True)
class SectionViewSettings:
    plane: str = "front"
    offset: float = 0.0
    flipped: bool = False
    show_plane: bool = True

    def __post_init__(self) -> None:
        plane = str(self.plane).strip().casefold()
        if plane not in _PLANE_NORMALS:
            raise ValueError("Section plane must be front, top, or right.")
        object.__setattr__(self, "plane", plane)
        object.__setattr__(self, "offset", float(self.offset))
        object.__setattr__(self, "flipped", bool(self.flipped))
        object.__setattr__(self, "show_plane", bool(self.show_plane))


@dataclass(frozen=True)
class ModelBounds:
    xmin: float
    xmax: float
    ymin: float
    ymax: float
    zmin: float
    zmax: float

    @property
    def center(self) -> tuple[float, float, float]:
        return (
            (self.xmin + self.xmax) / 2.0,
            (self.ymin + self.ymax) / 2.0,
            (self.zmin + self.zmax) / 2.0,
        )

    def axis_half_extent(self, plane: str) -> float:
        name = str(plane).strip().casefold()
        if name == "front":
            return abs(self.zmax - self.zmin) / 2.0
        if name == "top":
            return abs(self.ymax - self.ymin) / 2.0
        if name == "right":
            return abs(self.xmax - self.xmin) / 2.0
        raise ValueError("Section plane must be front, top, or right.")


_settings = SectionViewSettings()
_overlay_node: Any | None = None


def reset_section_view_settings() -> SectionViewSettings:
    global _settings
    _settings = SectionViewSettings()
    return _settings


def current_section_view_settings() -> SectionViewSettings:
    return _settings


def _object_bound_box(obj: Any) -> Any | None:
    for source in (
        getattr(getattr(obj, "Shape", None), "BoundBox", None),
        getattr(obj, "BoundBox", None),
    ):
        box = source
        if box is None:
            continue
        is_valid = getattr(box, "isValid", None)
        try:
            if callable(is_valid) and not bool(is_valid()):
                continue
        except Exception:
            continue
        try:
            xmin = float(box.XMin)
            xmax = float(box.XMax)
            ymin = float(box.YMin)
            ymax = float(box.YMax)
            zmin = float(box.ZMin)
            zmax = float(box.ZMax)
        except Exception:
            continue
        if any(value != value for value in (xmin, xmax, ymin, ymax, zmin, zmax)):
            continue
        return box
    return None


def model_bounds(objects: Any) -> ModelBounds | None:
    """Return the combined BoundBox of ``objects``, or None if empty."""

    xmin = ymin = zmin = float("inf")
    xmax = ymax = zmax = float("-inf")
    found = False
    for obj in tuple(objects or ()):
        box = _object_bound_box(obj)
        if box is None:
            continue
        found = True
        xmin = min(xmin, float(box.XMin))
        xmax = max(xmax, float(box.XMax))
        ymin = min(ymin, float(box.YMin))
        ymax = max(ymax, float(box.YMax))
        zmin = min(zmin, float(box.ZMin))
        zmax = max(zmax, float(box.ZMax))
    if not found:
        return None
    return ModelBounds(xmin, xmax, ymin, ymax, zmin, zmax)


def bounds_center(objects: Any) -> tuple[float, float, float] | None:
    """Return the combined BoundBox center of ``objects``, or None if empty."""

    bounds = model_bounds(objects)
    if bounds is None:
        return None
    return bounds.center


def section_plane_normal(plane: str, flipped: bool = False) -> tuple[float, float, float]:
    name = str(plane).strip().casefold()
    normal = _PLANE_NORMALS.get(name)
    if normal is None:
        raise ValueError("Section plane must be front, top, or right.")
    if flipped:
        return (-normal[0], -normal[1], -normal[2])
    return normal


def section_offset_range(bounds: ModelBounds, plane: str) -> tuple[float, float]:
    half = float(bounds.axis_half_extent(plane))
    return (-half, half)


def clip_plane_from_settings(
    settings: SectionViewSettings,
    center: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Return (origin, clip-normal) for the SolidWorks/Fusion section plane."""

    normal = section_plane_normal(settings.plane, settings.flipped)
    origin = (
        center[0] + normal[0] * settings.offset,
        center[1] + normal[1] * settings.offset,
        center[2] + normal[2] * settings.offset,
    )
    return origin, normal


def _cross(
    left: tuple[float, float, float],
    right: tuple[float, float, float],
) -> tuple[float, float, float]:
    return (
        left[1] * right[2] - left[2] * right[1],
        left[2] * right[0] - left[0] * right[2],
        left[0] * right[1] - left[1] * right[0],
    )


def _unit(vector: tuple[float, float, float]) -> tuple[float, float, float]:
    length = (vector[0] ** 2 + vector[1] ** 2 + vector[2] ** 2) ** 0.5
    if length == 0.0:
        raise ValueError("A section-plane axis is degenerate.")
    return (vector[0] / length, vector[1] / length, vector[2] / length)


def section_plane_axes(
    normal: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    direction = _unit(normal)
    helper = (0.0, 0.0, 1.0) if abs(direction[2]) < 0.9 else (0.0, 1.0, 0.0)
    u_axis = _unit(_cross(direction, helper))
    v_axis = _unit(_cross(direction, u_axis))
    return u_axis, v_axis


def section_plane_corners(
    origin: tuple[float, float, float],
    normal: tuple[float, float, float],
    half_width: float,
    half_height: float,
) -> tuple[
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
    tuple[float, float, float],
]:
    u_axis, v_axis = section_plane_axes(normal)
    corners = []
    for u_sign, v_sign in ((-1.0, -1.0), (1.0, -1.0), (1.0, 1.0), (-1.0, 1.0)):
        corners.append(
            (
                origin[0] + u_axis[0] * u_sign * half_width + v_axis[0] * v_sign * half_height,
                origin[1] + u_axis[1] * u_sign * half_width + v_axis[1] * v_sign * half_height,
                origin[2] + u_axis[2] * u_sign * half_width + v_axis[2] * v_sign * half_height,
            )
        )
    return (corners[0], corners[1], corners[2], corners[3])


def _active_document() -> Any | None:
    if App is None:
        return None
    document = getattr(App, "ActiveDocument", None)
    return document


def _active_3d_view(gui: Any | None = None) -> Any | None:
    if gui is None:
        try:
            import FreeCADGui as Gui
        except ImportError:
            return None
        gui = Gui
    gui_document = getattr(gui, "ActiveDocument", None)
    if gui_document is None:
        active_document = getattr(gui, "activeDocument", None)
        gui_document = active_document() if callable(active_document) else None
    if gui_document is None:
        return None
    active_view = getattr(gui_document, "activeView", None)
    view = (
        active_view()
        if callable(active_view)
        else getattr(gui_document, "ActiveView", None)
    )
    if view is None:
        return None
    if not callable(getattr(view, "toggleClippingPlane", None)):
        return None
    if not callable(getattr(view, "hasClippingPlane", None)):
        return None
    return view


def _document_objects(document: Any | None) -> Iterable[Any]:
    target = document if document is not None else _active_document()
    return getattr(target, "Objects", ()) if target is not None else ()


def section_view_placement(
    document: Any | None = None,
    settings: SectionViewSettings | None = None,
) -> Any:
    """Front/Top/Right plane through the document bounds center, or the origin."""

    if App is None:
        raise RuntimeError("FreeCAD is unavailable.")
    active = settings if settings is not None else _settings
    bounds = model_bounds(_document_objects(document))
    center = bounds.center if bounds is not None else (0.0, 0.0, 0.0)
    origin, normal = clip_plane_from_settings(active, center)
    rotation = App.Rotation(App.Vector(0.0, 0.0, -1.0), App.Vector(*normal))
    return App.Placement(App.Vector(*origin), rotation)


def is_section_view_active(view: Any | None = None) -> bool:
    active = view if view is not None else _active_3d_view()
    if active is None:
        return False
    has_clip = getattr(active, "hasClippingPlane", None)
    if not callable(has_clip):
        return False
    try:
        return bool(has_clip())
    except Exception:
        return False


def _coin_vec3(coin: Any, xyz: tuple[float, float, float]) -> Any:
    """SbVec3f from three floats. Star-unpacking hits a Pivy SWIG mismatch."""

    x, y, z = float(xyz[0]), float(xyz[1]), float(xyz[2])
    try:
        return coin.SbVec3f(x, y, z)
    except Exception:
        vec = coin.SbVec3f()
        vec.setValue(x, y, z)
        return vec


def _top_level_nodes(scene: Any) -> tuple[Any, ...]:
    children = getattr(scene, "getChildren", None)
    if not callable(children):
        return ()
    try:
        return tuple(children() or ())
    except Exception:
        return ()


def _update_clip_plane(view: Any, placement: Any) -> bool:
    get_scene = getattr(view, "getSceneGraph", None)
    if not callable(get_scene) or App is None:
        return False
    try:
        from pivy import coin
    except ImportError:
        return False
    try:
        scene = get_scene()
    except Exception:
        return False
    if scene is None:
        return False
    try:
        origin = (
            float(placement.Base.x),
            float(placement.Base.y),
            float(placement.Base.z),
        )
        direction = placement.Rotation.multVec(App.Vector(0.0, 0.0, -1.0))
        normal = (float(direction.x), float(direction.y), float(direction.z))
    except Exception:
        return False
    for node in _top_level_nodes(scene):
        if type(node).__name__ == "SoClipPlane":
            node.plane.setValue(
                coin.SbPlane(_coin_vec3(coin, normal), _coin_vec3(coin, origin))
            )
            return True
    return False


def _overlay_size(
    bounds: ModelBounds | None,
    normal: tuple[float, float, float],
) -> tuple[float, float]:
    if bounds is None:
        return (50.0, 50.0)
    u_axis, v_axis = section_plane_axes(normal)
    extents = (
        (bounds.xmax - bounds.xmin, 1.0, 0.0, 0.0),
        (bounds.ymax - bounds.ymin, 0.0, 1.0, 0.0),
        (bounds.zmax - bounds.zmin, 0.0, 0.0, 1.0),
    )
    half_width = 0.0
    half_height = 0.0
    for length, x_dir, y_dir, z_dir in extents:
        half = abs(length) / 2.0
        half_width = max(
            half_width, abs(u_axis[0] * x_dir + u_axis[1] * y_dir + u_axis[2] * z_dir) * half
        )
        half_height = max(
            half_height,
            abs(v_axis[0] * x_dir + v_axis[1] * y_dir + v_axis[2] * z_dir) * half,
        )
    pad = 1.05
    return (max(half_width, 1.0) * pad, max(half_height, 1.0) * pad)


def _remove_overlay(view: Any) -> None:
    global _overlay_node
    if _overlay_node is None:
        return
    get_scene = getattr(view, "getSceneGraph", None)
    if callable(get_scene):
        try:
            scene = get_scene()
        except Exception:
            scene = None
        if scene is not None:
            try:
                index = scene.findChild(_overlay_node)
            except Exception:
                index = -1
            if index >= 0:
                scene.removeChild(_overlay_node)
    _overlay_node = None


def _sync_overlay(
    view: Any,
    document: Any | None,
    settings: SectionViewSettings,
) -> None:
    global _overlay_node
    _remove_overlay(view)
    if not settings.show_plane:
        return
    get_scene = getattr(view, "getSceneGraph", None)
    if not callable(get_scene):
        return
    try:
        from pivy import coin
    except ImportError:
        return
    try:
        scene = get_scene()
    except Exception:
        return
    if scene is None:
        return
    bounds = model_bounds(_document_objects(document))
    center = bounds.center if bounds is not None else (0.0, 0.0, 0.0)
    origin, normal = clip_plane_from_settings(settings, center)
    half_width, half_height = _overlay_size(bounds, normal)
    corners = section_plane_corners(origin, normal, half_width, half_height)
    try:
        _install_overlay_node(coin, scene, corners)
    except Exception:
        return


def _install_overlay_node(coin: Any, scene: Any, corners: Any) -> None:
    global _overlay_node
    separator = coin.SoSeparator()
    separator.setName(_OVERLAY_NAME)
    light = coin.SoLightModel()
    light.model = coin.SoLightModel.BASE_COLOR
    material = coin.SoMaterial()
    material.diffuseColor.setValue(0.15, 0.62, 0.78)
    material.transparency.setValue(0.72)
    material.emissiveColor.setValue(0.08, 0.35, 0.45)
    coords = coin.SoCoordinate3()
    for index, corner in enumerate(corners):
        coords.point.set1Value(
            index, float(corner[0]), float(corner[1]), float(corner[2])
        )
    faces = coin.SoIndexedFaceSet()
    for index, value in enumerate((0, 1, 2, 3, -1)):
        faces.coordIndex.set1Value(index, value)
    style = coin.SoDrawStyle()
    style.style = coin.SoDrawStyle.LINES
    style.lineWidth = 2
    line_material = coin.SoMaterial()
    line_material.diffuseColor.setValue(0.05, 0.42, 0.58)
    lines = coin.SoIndexedLineSet()
    for index, value in enumerate((0, 1, 2, 3, 0, -1)):
        lines.coordIndex.set1Value(index, value)
    separator.addChild(light)
    separator.addChild(material)
    separator.addChild(coords)
    separator.addChild(faces)
    separator.addChild(style)
    separator.addChild(line_material)
    separator.addChild(lines)
    try:
        scene.insertChild(separator, 0)
    except Exception:
        return
    _overlay_node = separator


def _apply_clip(
    view: Any,
    document: Any | None,
    settings: SectionViewSettings,
) -> None:
    placement = section_view_placement(document, settings)
    if is_section_view_active(view):
        if not _update_clip_plane(view, placement):
            view.toggleClippingPlane(toggle=0)
            view.toggleClippingPlane(toggle=1, noManip=True, pla=placement)
    else:
        view.toggleClippingPlane(toggle=1, noManip=True, pla=placement)
    _sync_overlay(view, document, settings)


def _close_ui() -> None:
    try:
        import VibeCADSectionViewGui as gui
    except Exception:
        return
    closer = getattr(gui, "close_section_view_dialog", None)
    if callable(closer):
        closer()


def _show_ui() -> None:
    try:
        import VibeCADSectionViewGui as gui
    except Exception:
        return
    shower = getattr(gui, "show_section_view_dialog", None)
    if callable(shower):
        shower()


def set_section_view(
    visible: bool,
    *,
    view: Any | None = None,
    document: Any | None = None,
    show_ui: bool = False,
) -> dict[str, bool]:
    """Enable or disable the active 3D section clip."""

    global _settings
    if type(visible) is not bool:
        raise TypeError("visible must be a boolean")
    active = view if view is not None else _active_3d_view()
    if active is None:
        raise RuntimeError("Section view requires an active 3D view.")
    current = is_section_view_active(active)
    if current == visible:
        if visible and show_ui:
            _show_ui()
        if not visible:
            _remove_overlay(active)
            _close_ui()
        return {"section_view": current}
    toggle = getattr(active, "toggleClippingPlane", None)
    if not callable(toggle):
        raise RuntimeError("The active 3D view cannot toggle a section plane.")
    if visible:
        _apply_clip(active, document, _settings)
        if show_ui:
            _show_ui()
    else:
        _remove_overlay(active)
        toggle(toggle=0)
        _close_ui()
    observed = is_section_view_active(active)
    if observed != visible:
        raise RuntimeError("The active 3D view did not reach the requested section state.")
    return {"section_view": observed}


def configure_section_view(
    *,
    plane: str | None = None,
    offset: float | None = None,
    flipped: bool | None = None,
    show_plane: bool | None = None,
    view: Any | None = None,
    document: Any | None = None,
) -> dict[str, object]:
    """Update the live Front/Top/Right section without changing geometry."""

    global _settings
    updates: dict[str, object] = {}
    if plane is not None:
        updates["plane"] = plane
    if offset is not None:
        updates["offset"] = offset
    if flipped is not None:
        updates["flipped"] = flipped
    if show_plane is not None:
        updates["show_plane"] = show_plane
    _settings = replace(_settings, **updates) if updates else _settings
    active = view if view is not None else _active_3d_view()
    if active is not None and is_section_view_active(active):
        _apply_clip(active, document, _settings)
    return {
        "plane": _settings.plane,
        "offset": _settings.offset,
        "flipped": _settings.flipped,
        "show_plane": _settings.show_plane,
        "section_view": is_section_view_active(active) if active is not None else False,
    }


def toggle_section_view(
    *,
    view: Any | None = None,
    document: Any | None = None,
    show_ui: bool = True,
) -> dict[str, bool]:
    """Toggle the active 3D section clip and return the resulting state."""

    active = view if view is not None else _active_3d_view()
    if active is None:
        raise RuntimeError("Section view requires an active 3D view.")
    if is_section_view_active(active):
        return set_section_view(False, view=active, document=document)
    reset_section_view_settings()
    return set_section_view(
        True,
        view=active,
        document=document,
        show_ui=show_ui,
    )
