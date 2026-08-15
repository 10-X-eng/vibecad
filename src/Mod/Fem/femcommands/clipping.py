# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared scene-graph engine for FEM clipping-plane presentation."""

from __future__ import annotations

import math

import FreeCAD


MAX_CLIPPING_PLANES = 32


def _scene_graph(gui_document):
    if gui_document is None:
        raise RuntimeError("The document has no active 3D view")
    try:
        return gui_document.ActiveView.getSceneGraph()
    except Exception as exc:
        raise RuntimeError("The document has no active 3D scene graph") from exc


def clipping_plane_nodes(gui_document):
    """Return exact root-level clipping nodes in stable scene order."""

    from pivy import coin

    scene = _scene_graph(gui_document)
    return tuple(
        (index, node)
        for index, node in enumerate(tuple(scene.getChildren()))
        if isinstance(node, coin.SoClipPlane)
    )


def _positive_manipulator_size(document):
    overall = None
    for obj in tuple(getattr(document, "Objects", ()) or ()):
        bounds = None
        try:
            bounds = obj.getPropertyOfGeometry().BoundBox
        except Exception:
            try:
                bounds = obj.FemMesh.BoundBox
            except Exception:
                pass
        try:
            if bounds is None or not bounds.isValid():
                continue
            if overall is None:
                overall = bounds
            else:
                overall.add(bounds)
        except Exception:
            continue
    if not overall:
        raise RuntimeError(
            "A clipping plane requires at least one shape or FEM mesh with finite bounds"
        )
    lengths = tuple(
        abs(float(value))
        for value in (overall.XLength, overall.YLength, overall.ZLength)
        if math.isfinite(float(value)) and abs(float(value)) > 0.0
    )
    if not lengths:
        raise RuntimeError(
            "A clipping plane requires shape bounds with a positive extent"
        )
    return min(lengths) * 0.2


def _finite_vector(value, label):
    try:
        vector = FreeCAD.Vector(value)
        coordinates = (float(vector.x), float(vector.y), float(vector.z))
    except Exception as exc:
        raise RuntimeError(f"The clipping {label} is invalid") from exc
    if any(not math.isfinite(component) for component in coordinates):
        raise RuntimeError(f"The clipping {label} must be finite")
    return vector


def add_clipping_plane(gui_document, document, point, normal):
    """Add one plane with the same manipulator used by the human FEM command."""

    from pivy import coin

    existing = clipping_plane_nodes(gui_document)
    if len(existing) >= MAX_CLIPPING_PLANES:
        raise RuntimeError(
            f"The active view already contains the {MAX_CLIPPING_PLANES}-plane limit"
        )
    origin = _finite_vector(point, "point")
    direction = _finite_vector(normal, "normal")
    if float(direction.Length) <= 1.0e-12:
        raise RuntimeError("The clipping normal must have a nonzero length")
    direction.normalize()
    size = _positive_manipulator_size(document)
    bounds = coin.SbBox3f(
        origin.x - size,
        origin.y - size,
        origin.z - size * 0.15,
        origin.x + size,
        origin.y + size,
        origin.z + size * 0.15,
    )
    coin_normal = coin.SbVec3f(-direction.x, -direction.y, -direction.z)
    clip_plane = coin.SoClipPlaneManip()
    clip_plane.setValue(bounds, coin_normal, 1)
    scene = _scene_graph(gui_document)
    scene.insertChild(clip_plane, 1)
    return clip_plane


def remove_exact_clipping_plane(gui_document, node):
    scene = _scene_graph(gui_document)
    if scene.findChild(node) >= 0:
        scene.removeChild(node)


def remove_all_clipping_planes(gui_document):
    """Remove all root-level clipping planes and return restorable identities."""

    scene = _scene_graph(gui_document)
    removed = clipping_plane_nodes(gui_document)
    for _index, node in removed:
        scene.removeChild(node)
    return removed


def restore_clipping_planes(gui_document, removed):
    """Restore an exact removed plane set at its original scene positions."""

    scene = _scene_graph(gui_document)
    for index, node in tuple(removed):
        if scene.findChild(node) < 0:
            scene.insertChild(node, min(int(index), int(scene.getNumChildren())))
