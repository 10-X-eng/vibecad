# SPDX-License-Identifier: LGPL-2.1-or-later

"""Shared exact geometry validation for shipped CAM pocket operations."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import PreparedOperationBoundary


@dataclass(frozen=True, slots=True)
class PocketFeatureFacts:
    feature_count: int
    face_count: int
    edge_count: int
    closed_edge_wire_count: int


def _error(message: str) -> None:
    raise NativeManufactureError(
        message,
        error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
    )


def _supported_face(face: Any) -> bool:
    import Part
    import Path

    surface = face.Surface
    if isinstance(surface, Part.Plane):
        return bool(
            Path.Geom.isVertical(surface.Axis) or Path.Geom.isHorizontal(surface.Axis)
        )
    if isinstance(surface, Part.BSplineSurface):
        return bool(Path.Geom.isRoughly(face.BoundBox.ZLength, 0.0))
    if isinstance(surface, Part.Cylinder):
        return bool(Path.Geom.isVertical(surface.Axis))
    if isinstance(surface, Part.SurfaceOfExtrusion):
        return bool(Path.Geom.isRoughly(abs(float(surface.Direction.z)), 1.0))
    return False


def validate_pocket_feature_geometry(
    boundary: PreparedOperationBoundary,
    *,
    noun: str,
) -> PocketFeatureFacts:
    """Validate exactly the Face/Edge forms consumed by ``Path.Op.Pocket``."""

    if not isinstance(boundary, PreparedOperationBoundary):
        raise TypeError("boundary must be a PreparedOperationBoundary")
    clean_noun = str(noun or "").strip()
    if not clean_noun:
        raise ValueError("noun must not be empty")
    if boundary.geometry_kind != "subelements":
        _error(f"{clean_noun} requires exact Face or closed horizontal Edge geometry.")

    import Part
    import Path

    face_count = 0
    edge_count = 0
    wire_count = 0
    for item in boundary.geometry:
        names = item.subelements
        item_types = {"Face" if name.startswith("Face") else "Edge" for name in names}
        if len(item_types) != 1:
            _error(
                f"{clean_noun} geometry on {item.public_source.Name!r} cannot mix "
                "Faces and Edges in one model item; put each representation in a "
                "separate operation."
            )
        if item_types == {"Face"}:
            for name in names:
                face = item.public_source.Shape.getElement(name)
                if not _supported_face(face):
                    _error(
                        f"{clean_noun} cannot machine "
                        f"{item.public_source.Name}.{name}: select a planar "
                        "horizontal/vertical face, a horizontal B-spline face, "
                        "or a supported vertical cylindrical/extruded face."
                    )
                face_count += 1
            continue

        selected_edges = []
        for name in names:
            edge = item.public_source.Shape.getElement(name)
            if not Path.Geom.isHorizontal(edge):
                _error(
                    f"{clean_noun} edge {item.public_source.Name}.{name} is not "
                    "horizontal."
                )
            selected_edges.append(edge.copy())
            edge_count += 1
        try:
            groups = tuple(Part.sortEdges(selected_edges))
            closed = bool(groups) and all(
                Part.Wire(group).isClosed() for group in groups
            )
        except Exception:
            groups = ()
            closed = False
        if not closed:
            _error(
                f"{clean_noun} edges on {item.public_source.Name!r} must form one "
                "or more closed horizontal wires; add the missing connected edges "
                "or select the bounded face instead."
            )
        wire_count += len(groups)

    return PocketFeatureFacts(
        feature_count=face_count + edge_count,
        face_count=face_count,
        edge_count=edge_count,
        closed_edge_wire_count=wire_count,
    )
