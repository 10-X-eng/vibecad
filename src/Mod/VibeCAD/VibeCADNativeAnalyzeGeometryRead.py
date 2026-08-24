# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact face geometry for FEM assignment decisions."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeAnalyzeErrors import NativeAnalyzeError
from VibeCADNativeMeshState import mesh_object_state
from VibeCADNativeTargets import NativeObjectRef, resolve_object


_SURFACE_KINDS = {
    "Plane": "plane",
    "Cylinder": "cylinder",
    "Cone": "cone",
    "Sphere": "sphere",
    "Torus": "torus",
    "BSplineSurface": "b_spline",
    "BezierSurface": "bezier",
    "SurfaceOfRevolution": "revolution",
    "SurfaceOfExtrusion": "extrusion",
    "OffsetSurface": "offset",
}

ANALYZE_FACE_PAGE_LIMIT = 128


def _number(value: Any) -> float:
    result = float(value)
    if not math.isfinite(result):
        raise NativeAnalyzeError("A geometry source contains a non-finite value.")
    if abs(result) < 1.0e-12:
        return 0.0
    return float(format(result, ".12g"))


def _vector(value: Any) -> list[float]:
    return [_number(getattr(value, axis)) for axis in ("x", "y", "z")]


def _bounds(value: Any) -> dict[str, list[float]]:
    box = value.BoundBox
    return {
        "minimum_mm": [_number(box.XMin), _number(box.YMin), _number(box.ZMin)],
        "maximum_mm": [_number(box.XMax), _number(box.YMax), _number(box.ZMax)],
    }


def _face_state(face: Any, index: int) -> dict[str, Any]:
    surface = face.Surface
    kind = _SURFACE_KINDS.get(type(surface).__name__, type(surface).__name__.lower())
    result: dict[str, Any] = {
        "subelement": f"Face{index}",
        "surface": kind,
        "area_mm2": _number(face.Area),
        "center_mm": _vector(face.CenterOfMass),
        "bounds": _bounds(face),
        "edge_count": len(face.Edges),
    }
    if kind == "plane":
        u_min, u_max, v_min, v_max = face.ParameterRange
        result["normal"] = _vector(
            face.normalAt((u_min + u_max) * 0.5, (v_min + v_max) * 0.5)
        )
        result["reference_direction"] = _vector(surface.Axis)
    axis = getattr(surface, "Axis", None)
    if axis is not None and kind != "plane":
        result["axis"] = _vector(axis)
    radius = getattr(surface, "Radius", None)
    if radius is not None:
        result["radius_mm"] = _number(radius)
    return result


def inspect_geometry_source(
    document: Any,
    document_uid: str,
    target: Any,
    *,
    offset: Any,
    page_size: Any,
) -> dict[str, Any]:
    if (
        not isinstance(target, Mapping)
        or set(target) != {"object_name", "expected_state_sha256"}
    ):
        raise NativeAnalyzeError(
            "target must contain only object_name and expected_state_sha256."
        )
    if type(offset) is not int or offset < 0:
        raise NativeAnalyzeError("offset must be a non-negative integer.")
    if (
        type(page_size) is not int
        or not 1 <= page_size <= ANALYZE_FACE_PAGE_LIMIT
    ):
        raise NativeAnalyzeError(
            f"page_size must be an integer from 1 to {ANALYZE_FACE_PAGE_LIMIT}."
        )
    source = resolve_object(
        document,
        NativeObjectRef(document_uid, str(target["object_name"])),
    )
    shape = getattr(source, "Shape", None)
    try:
        usable = shape is not None and not shape.isNull() and shape.isValid()
    except Exception:
        usable = False
    if not usable:
        raise NativeAnalyzeError("The geometry source has no valid shape.")
    try:
        import PartGui

        active = bool(PartGui.isModelingObjectActive(source))
    except Exception:
        active = False
    if not active:
        raise NativeAnalyzeError(
            "The geometry source is not active at current History.",
            error_code="NATIVE_ANALYZE_HISTORY_TARGET_INACTIVE",
        )
    state = mesh_object_state(source)
    if state["state_sha256"] != str(target["expected_state_sha256"]):
        raise NativeAnalyzeError(
            "The geometry source changed after the provider read it.",
            error_code="NATIVE_ANALYZE_STATE_STALE",
            repair={
                "source": {"object_name": str(source.Name)},
                "current_state_sha256": state["state_sha256"],
                "current_topology": state.get("topology"),
            },
        )
    faces = tuple(shape.Faces)
    stop = min(len(faces), offset + page_size)
    return {
        "face_page": {
            "source": {
                "object_name": str(source.Name),
                "expected_state_sha256": state["state_sha256"],
            },
            "offset": offset,
            "returned": max(0, stop - offset),
            "total": len(faces),
            "next_offset": stop if stop < len(faces) else None,
            "faces": [
                _face_state(faces[index], index + 1)
                for index in range(offset, stop)
            ],
        }
    }
