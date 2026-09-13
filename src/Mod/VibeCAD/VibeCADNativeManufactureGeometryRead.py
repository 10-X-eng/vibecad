# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded exact model geometry for CAM feature selection."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufacturePostInput import (
    FileIdentity,
    _file_identity,
    _freecadcmd,
    validate_file_identity,
)
from VibeCADNativeManufactureState import candidate_model_state, resolve_model_target
from VibeCADScriptedProcess import run_process


MAX_GEOMETRY_PAGE_SIZE = 128
MAX_GEOMETRY_BREP_BYTES = 4 * 1024 * 1024 * 1024
MAX_GEOMETRY_RESULT_BYTES = 1024 * 1024
GEOMETRY_TIMEOUT_SECONDS = 300.0
GEOMETRY_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
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
_CURVE_KINDS = {
    "Line": "line",
    "Circle": "circle",
    "Ellipse": "ellipse",
    "Hyperbola": "hyperbola",
    "Parabola": "parabola",
    "BSplineCurve": "b_spline",
    "BezierCurve": "bezier",
    "OffsetCurve": "offset",
}


@dataclass(frozen=True, slots=True)
class FrozenModelGeometryRead:
    workspace: Any = field(repr=False, compare=False)
    workspace_path: Path = field(repr=False, compare=False)
    shape_path: Path = field(repr=False, compare=False)
    shape_size: int
    shape_sha256: str
    model: Any = field(repr=False, compare=False)
    target: Mapping[str, Any]
    model_before: Mapping[str, Any]
    elements: str
    offset: int
    page_size: int
    freecadcmd: FileIdentity
    child_script: FileIdentity


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _validate_request(elements: Any, offset: Any, page_size: Any) -> tuple[str, int, int]:
    mode = str(elements or "")
    if mode not in {"faces", "edges", "drillable"}:
        _error("elements must be faces, edges, or drillable.")
    if type(offset) is not int or offset < 0:
        _error("offset must be a non-negative integer.")
    if type(page_size) is not int or not 1 <= page_size <= MAX_GEOMETRY_PAGE_SIZE:
        _error(
            f"page_size must be an integer from 1 to {MAX_GEOMETRY_PAGE_SIZE}."
        )
    return mode, offset, page_size


def _hash_file(path: Path, maximum: int) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as stream:
            while True:
                chunk = stream.read(1024 * 1024)
                if not chunk:
                    break
                size += len(chunk)
                if size > maximum:
                    _error(
                        "The detached CAM geometry exceeds its execution bound.",
                        "NATIVE_MANUFACTURE_GEOMETRY_LIMIT",
                    )
                digest.update(chunk)
    except OSError as exc:
        raise NativeManufactureError(
            "The detached CAM geometry is unavailable.",
            error_code="NATIVE_MANUFACTURE_GEOMETRY_UNAVAILABLE",
        ) from exc
    if size <= 0:
        _error(
            "The detached CAM geometry is empty.",
            "NATIVE_MANUFACTURE_GEOMETRY_UNAVAILABLE",
        )
    return size, digest.hexdigest()


def _number(value: Any) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise NativeManufactureError(
            "The CAM model contains unreadable geometry.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        ) from exc
    if not math.isfinite(result):
        raise NativeManufactureError(
            "The CAM model contains non-finite geometry.",
            error_code="NATIVE_MANUFACTURE_STATE_INVALID",
        )
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


def _drilling_facts(shape: Any, feature: Any) -> dict[str, Any] | None:
    try:
        import Part
        import Path.Base.Drillable as Drillable

        accepted = bool(
            Drillable.isDrillable(shape, feature, vector=None, allowPartial=True)
        )
    except Exception:
        return None
    if not accepted:
        return None

    center = None
    radius = None
    if isinstance(feature, Part.Edge) and isinstance(feature.Curve, Part.Circle):
        center = feature.Curve.Center
        radius = feature.Curve.Radius
    elif isinstance(feature, Part.Face):
        surface = feature.Surface
        if isinstance(surface, Part.Cylinder):
            center = surface.Center
            radius = surface.Radius
        if center is None or radius is None:
            circular = [
                edge for edge in feature.Edges if isinstance(edge.Curve, Part.Circle)
            ]
            if circular:
                center = circular[0].Curve.Center
                radius = circular[0].Curve.Radius
    if center is None or radius is None:
        return {"accepted": True}
    diameter = 2.0 * _number(radius)
    return {
        "accepted": True,
        "center_mm": _vector(center),
        "diameter_mm": diameter,
        "identity": {
            "center_x_mm": _number(center.x),
            "center_y_mm": _number(center.y),
            "diameter_mm": diameter,
        },
    }


def _face_record(face: Any, index: int) -> dict[str, Any]:
    surface = face.Surface
    kind = _SURFACE_KINDS.get(type(surface).__name__, type(surface).__name__.lower())
    result: dict[str, Any] = {
        "subelement": f"Face{index}",
        "element": "face",
        "surface": kind,
        "area_mm2": _number(face.Area),
        "center_mm": _vector(face.CenterOfMass),
        "bounds": _bounds(face),
        "edge_count": len(face.Edges),
    }
    try:
        u_min, u_max, v_min, v_max = face.ParameterRange
        result["normal"] = _vector(
            face.normalAt((u_min + u_max) * 0.5, (v_min + v_max) * 0.5)
        )
    except Exception:
        pass
    axis = getattr(surface, "Axis", None)
    if axis is not None:
        result["axis"] = _vector(axis)
    radius = getattr(surface, "Radius", None)
    if radius is not None:
        result["radius_mm"] = _number(radius)
    return result


def _edge_record(edge: Any, index: int) -> dict[str, Any]:
    curve = edge.Curve
    kind = _CURVE_KINDS.get(type(curve).__name__, type(curve).__name__.lower())
    result: dict[str, Any] = {
        "subelement": f"Edge{index}",
        "element": "edge",
        "curve": kind,
        "length_mm": _number(edge.Length),
        "bounds": _bounds(edge),
        "closed": bool(edge.isClosed()),
    }
    vertices = tuple(edge.Vertexes)
    if vertices:
        result["endpoints_mm"] = [_vector(value.Point) for value in vertices[:2]]
    center = getattr(curve, "Center", None)
    if center is not None:
        result["center_mm"] = _vector(center)
    axis = getattr(curve, "Axis", None)
    if axis is not None:
        result["axis"] = _vector(axis)
    radius = getattr(curve, "Radius", None)
    if radius is not None:
        result["radius_mm"] = _number(radius)
    return result


def geometry_page(
    shape: Any,
    *,
    elements: str,
    offset: int,
    page_size: int,
) -> dict[str, Any]:
    """Inspect one detached shape without reading document state."""

    mode, offset, page_size = _validate_request(elements, offset, page_size)

    faces = tuple(shape.Faces)
    edges = tuple(shape.Edges)
    if mode == "faces":
        total = len(faces)
        start = min(offset, total)
        stop = min(start + page_size, total)
        items = [
            _face_record(faces[index], index + 1) for index in range(start, stop)
        ]
    elif mode == "edges":
        total = len(edges)
        start = min(offset, total)
        stop = min(start + page_size, total)
        items = [
            _edge_record(edges[index], index + 1) for index in range(start, stop)
        ]
    else:
        drillable = []
        for index, face in enumerate(faces, 1):
            facts = _drilling_facts(shape, face)
            if facts is not None:
                item = _face_record(face, index)
                item["drilling"] = facts
                drillable.append(item)
        for index, edge in enumerate(edges, 1):
            facts = _drilling_facts(shape, edge)
            if facts is not None:
                item = _edge_record(edge, index)
                item["drilling"] = facts
                drillable.append(item)
        total = len(drillable)
        start = min(offset, total)
        stop = min(start + page_size, total)
        items = drillable[start:stop]
    return {
        "elements": mode,
        "offset": start,
        "count": stop - start,
        "total": total,
        "next_offset": stop if stop < total else None,
        "items": items,
    }


def preflight_model_geometry_read(
    document: Any,
    *,
    target: Mapping[str, Any],
    elements: Any,
    offset: Any,
    page_size: Any,
) -> FrozenModelGeometryRead:
    """Freeze one exact model into a private BREP for isolated inspection."""

    mode, clean_offset, clean_page_size = _validate_request(
        elements,
        offset,
        page_size,
    )
    model, before = resolve_model_target(document, target)
    workspace = tempfile.TemporaryDirectory(prefix="vibecad-cam-geometry-")
    workspace_path = Path(workspace.name).resolve()
    shape_path = workspace_path / "model.brep"
    try:
        model.Shape.exportBrep(str(shape_path))
        shape_size, shape_sha256 = _hash_file(
            shape_path,
            MAX_GEOMETRY_BREP_BYTES,
        )
        after = candidate_model_state(model)
        if after.get("state_sha256") != before.get("state_sha256"):
            _error(
                "The CAM model changed while its geometry was frozen.",
                "NATIVE_MANUFACTURE_STATE_STALE",
            )
        child_path = Path(__file__).resolve().with_name(
            "VibeCADNativeManufactureGeometryChild.py"
        )
        child = _file_identity(
            child_path,
            executable=False,
            hash_limit=16 * 1024 * 1024,
        )
        command = _freecadcmd()
    except Exception:
        workspace.cleanup()
        raise
    return FrozenModelGeometryRead(
        workspace=workspace,
        workspace_path=workspace_path,
        shape_path=shape_path,
        shape_size=shape_size,
        shape_sha256=shape_sha256,
        model=model,
        target=dict(target),
        model_before=dict(before),
        elements=mode,
        offset=clean_offset,
        page_size=clean_page_size,
        freecadcmd=command,
        child_script=child,
    )


def _validate_frozen_files(frozen: FrozenModelGeometryRead) -> None:
    validate_file_identity(frozen.freecadcmd, executable=True)
    validate_file_identity(frozen.child_script)
    size, digest = _hash_file(frozen.shape_path, MAX_GEOMETRY_BREP_BYTES)
    if size != frozen.shape_size or digest != frozen.shape_sha256:
        _error(
            "The detached CAM geometry changed before inspection.",
            "NATIVE_MANUFACTURE_GEOMETRY_UNAVAILABLE",
        )


def prepare_model_geometry_read(
    frozen: FrozenModelGeometryRead,
    *,
    cancelled: Any,
    progress: Any,
) -> dict[str, Any]:
    """Inspect the private BREP in an isolated FreeCADCmd process."""

    if cancelled():
        raise NativeBackgroundCancelled()
    progress(5, "Validating detached CAM geometry")
    _validate_frozen_files(frozen)
    request_path = frozen.workspace_path / "request.json"
    result_path = frozen.workspace_path / "result.json"
    request_path.write_text(
        json.dumps(
            {
                "schema": "vibecad-cam-geometry-v1",
                "shape_path": str(frozen.shape_path),
                "shape_sha256": frozen.shape_sha256,
                "elements": frozen.elements,
                "offset": frozen.offset,
                "page_size": frozen.page_size,
                "result_path": str(result_path),
            },
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    progress(10, "Inspecting CAM geometry in isolated FreeCADCmd")
    environment = dict(os.environ)
    environment["VIBECAD_NATIVE_CAM_GEOMETRY_REQUEST"] = str(request_path)
    process = run_process(
        [str(frozen.freecadcmd.path), str(frozen.child_script.path)],
        cwd=frozen.workspace_path,
        environment=environment,
        cancellation_check=cancelled,
        timeout_seconds=GEOMETRY_TIMEOUT_SECONDS,
        memory_limit_bytes=GEOMETRY_MEMORY_LIMIT_BYTES,
    )
    if process.get("cancelled"):
        raise NativeBackgroundCancelled()
    if process.get("timed_out") or process.get("memory_exceeded"):
        _error(
            "The isolated CAM geometry reader exceeded its execution bound.",
            "NATIVE_MANUFACTURE_GEOMETRY_LIMIT",
        )
    if not process.get("started") or process.get("returncode") != 0:
        _error(
            "The isolated CAM geometry reader failed.",
            "NATIVE_MANUFACTURE_GEOMETRY_FAILED",
        )
    try:
        if not 1 <= result_path.stat().st_size <= MAX_GEOMETRY_RESULT_BYTES:
            raise ValueError("result size is outside its bound")
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        raise NativeManufactureError(
            "The isolated CAM geometry reader returned an invalid result.",
            error_code="NATIVE_MANUFACTURE_GEOMETRY_FAILED",
        ) from exc
    if not isinstance(result, Mapping) or result.get("ok") is not True:
        _error(
            str(result.get("message") or "The isolated CAM geometry reader failed.")[
                :320
            ]
            if isinstance(result, Mapping)
            else "The isolated CAM geometry reader failed.",
            str(result.get("error_code") or "NATIVE_MANUFACTURE_GEOMETRY_FAILED")[
                :80
            ]
            if isinstance(result, Mapping)
            else "NATIVE_MANUFACTURE_GEOMETRY_FAILED",
        )
    progress(85, "Prepared exact CAM geometry page")
    return {
        "model_geometry": {
            "model": {
                "object_name": frozen.model_before["object_name"],
                "label": frozen.model_before.get(
                    "label",
                    frozen.model_before["object_name"],
                ),
                "state_sha256": frozen.model_before["state_sha256"],
            },
            **dict(result["page"]),
        }
    }


def validate_model_geometry_read(
    document: Any,
    frozen: FrozenModelGeometryRead,
) -> None:
    model, current = resolve_model_target(document, frozen.target)
    if (
        model is not frozen.model
        or current.get("state_sha256")
        != frozen.model_before.get("state_sha256")
    ):
        _error(
            "The CAM model changed during geometry inspection.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    _validate_frozen_files(frozen)


def cleanup_model_geometry_read(frozen: FrozenModelGeometryRead) -> None:
    if isinstance(frozen, FrozenModelGeometryRead):
        frozen.workspace.cleanup()
