# SPDX-License-Identifier: LGPL-2.1-or-later

"""Fusion-style 3D section view for VibeCAD.

Toggles a clipping plane in the active 3D view so the model can be cut and
inspected. The clip uses a manipulator so the plane can be dragged. The
default plane is XY through the visible bounding-box center.

The native ``VibeCAD_SectionView`` command owns the View-ribbon action. This
module only inspects and toggles the active Inventor view's clipping plane;
when no 3D view is open it reports inactive and does not create a clip.

The module imports safely outside FreeCAD (guarded imports) so tooling such
as linters and test collectors can load it.
"""

from __future__ import annotations

from typing import Any

try:
    import FreeCAD as App
except ImportError:  # pragma: no cover - only outside FreeCAD (tooling/tests)
    App = None  # type: ignore[assignment]


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


def bounds_center(objects: Any) -> tuple[float, float, float] | None:
    """Return the combined BoundBox center of ``objects``, or None if empty."""

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
    return ((xmin + xmax) / 2.0, (ymin + ymax) / 2.0, (zmin + zmax) / 2.0)


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


def section_view_placement(document: Any | None = None) -> Any:
    """XY plane through the document bounds center, or the origin if empty."""

    if App is None:
        raise RuntimeError("FreeCAD is unavailable.")
    target = document if document is not None else _active_document()
    objects = getattr(target, "Objects", ()) if target is not None else ()
    center = bounds_center(objects)
    if center is None:
        return App.Placement()
    return App.Placement(App.Vector(*center), App.Rotation())


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


def set_section_view(
    visible: bool,
    *,
    view: Any | None = None,
    document: Any | None = None,
) -> dict[str, bool]:
    """Enable or disable the active 3D section clip."""

    if type(visible) is not bool:
        raise TypeError("visible must be a boolean")
    active = view if view is not None else _active_3d_view()
    if active is None:
        raise RuntimeError("Section view requires an active 3D view.")
    current = is_section_view_active(active)
    if current == visible:
        return {"section_view": current}
    toggle = getattr(active, "toggleClippingPlane", None)
    if not callable(toggle):
        raise RuntimeError("The active 3D view cannot toggle a section plane.")
    if visible:
        placement = section_view_placement(document)
        toggle(toggle=1, noManip=False, pla=placement)
    else:
        toggle(toggle=0)
    observed = is_section_view_active(active)
    if observed != visible:
        raise RuntimeError("The active 3D view did not reach the requested section state.")
    return {"section_view": observed}


def toggle_section_view(
    *,
    view: Any | None = None,
    document: Any | None = None,
) -> dict[str, bool]:
    """Toggle the active 3D section clip and return the resulting state."""

    active = view if view is not None else _active_3d_view()
    if active is None:
        raise RuntimeError("Section view requires an active 3D view.")
    return set_section_view(
        not is_section_view_active(active),
        view=active,
        document=document,
    )
