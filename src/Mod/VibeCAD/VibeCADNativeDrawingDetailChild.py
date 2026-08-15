# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd child for one exact TechDraw detail view."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Mapping


_REQUEST_ENV = "VIBECAD_NATIVE_DRAWING_DETAIL_REQUEST"
_PROTOCOL = "vibecad-native-drawing-detail-v1"
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_EDGES = 200_000
_MAX_FACES = 50_000
_MAX_SOLIDS = 10_000
_LINE_FLAGS = {
    "SmoothVisible",
    "SeamVisible",
    "IsoVisible",
    "HardHidden",
    "SmoothHidden",
    "SeamHidden",
    "IsoHidden",
}


class _ChildFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise _ChildFailure(code, message)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "An input escaped its private workspace.")
    try:
        value = path.lstat()
    except OSError:
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "A detached detail input is unavailable.")
    if not stat.S_ISREG(value.st_mode):
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "A detached detail input is not a regular file.")
    descriptor = -1
    data = bytearray()
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != int(value.st_dev)
            or int(opened.st_ino) != int(value.st_ino)
        ):
            _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "A detached detail input changed while opening.")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _fail("NATIVE_DRAWING_DETAIL_LIMIT", "A detached detail input exceeds its safety bound.")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "A detached detail input is empty.")
    return bytes(data)


def _write_private(path: Path, data: bytes) -> None:
    if not data or len(data) > _MAX_RESULT_BYTES:
        _fail("NATIVE_DRAWING_DETAIL_LIMIT", "Detached detail metadata exceeds its safety bound.")
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _request() -> tuple[dict[str, Any], Path, Path, str]:
    path_text = str(os.environ.get(_REQUEST_ENV) or "").strip()
    if not path_text:
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "The detached detail request is unavailable.")
    path = Path(path_text)
    root = path.parent.resolve()
    data = _read_regular(path, root=root, maximum=_MAX_REQUEST_BYTES)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "The detached detail request is unreadable.")
    required = {
        "protocol",
        "workspace",
        "snapshot",
        "snapshot_bytes",
        "snapshot_sha256",
        "page",
        "base_view",
        "sources",
        "detail",
        "result",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "The detached detail request is malformed.")
    if (
        str(value["protocol"]) != _PROTOCOL
        or Path(str(value["workspace"])).resolve() != root
        or str(value["snapshot"]) != "document.FCStd"
        or str(value["result"]) != "result.json"
    ):
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "The detached detail request identity is invalid.")
    return dict(value), root, root / "result.json", hashlib.sha256(data).hexdigest()


def _object_descriptor(value: Any) -> dict[str, Any]:
    required = {"object_id", "object_name", "type_id", "state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "An exact Drawing object descriptor is malformed.")
    result = dict(value)
    if (
        type(result["object_id"]) is not int
        or int(result["object_id"]) < 0
        or not str(result["object_name"] or "")
        or not str(result["type_id"] or "")
        or len(str(result["state_sha256"] or "")) != 64
    ):
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "An exact Drawing object descriptor is invalid.")
    return result


def _vector(value: Any, field_name: str, count: int) -> list[float]:
    if not isinstance(value, list) or len(value) != count:
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", f"{field_name} is malformed.")
    result = []
    for item in value:
        if type(item) not in {int, float} or not math.isfinite(float(item)):
            _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", f"{field_name} is malformed.")
        result.append(float(item))
    return result


def _detail_descriptor(value: Any) -> dict[str, Any]:
    required = {
        "anchor_mm",
        "radius_mm",
        "position_mm",
        "scale_kind",
        "requested_scale",
        "page_scale",
        "base_scale",
        "line_flags",
        "matting_style",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "The detail settings are malformed.")
    result = dict(value)
    _vector(result["anchor_mm"], "detail anchor", 2)
    _vector(result["position_mm"], "detail position", 2)
    radius = result["radius_mm"]
    kind = str(result["scale_kind"] or "")
    requested = result["requested_scale"]
    page_scale = result["page_scale"]
    base_scale = result["base_scale"]
    flags = result["line_flags"]
    if (
        type(radius) not in {int, float}
        or not math.isfinite(float(radius))
        or not 1.0e-9 <= float(radius) <= 1.0e9
        or kind not in {"page", "automatic", "custom"}
        or type(page_scale) not in {int, float}
        or not 1.0e-12 <= float(page_scale) <= 1_000.0
        or type(base_scale) not in {int, float}
        or not 1.0e-12 <= float(base_scale) <= 1_000.0
        or not isinstance(flags, Mapping)
        or set(flags) != _LINE_FLAGS
        or any(type(flag) is not bool for flag in flags.values())
        or type(result["matting_style"]) is not int
        or int(result["matting_style"]) not in {0, 1}
    ):
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "The detail settings are invalid.")
    if kind == "custom":
        if type(requested) not in {int, float} or not 1.0e-12 <= float(requested) <= 1_000.0:
            _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "The custom detail scale is invalid.")
    elif requested is not None:
        _fail("NATIVE_DRAWING_DETAIL_CHILD_INVALID", "A non-custom detail has a custom scale.")
    return result


def _canonical(document: Any, descriptor: Mapping[str, Any]) -> Any:
    obj = document.getObject(str(descriptor["object_name"]))
    if (
        obj is None
        or int(getattr(obj, "ID", -1)) != int(descriptor["object_id"])
        or str(getattr(obj, "TypeId", "") or "") != str(descriptor["type_id"])
    ):
        _fail("NATIVE_DRAWING_DETAIL_GRAPH_CHANGED", "An exact Drawing object identity changed in its snapshot.")
    return obj


def _artifact(root: Path, shape: Any, name: str) -> dict[str, Any]:
    relative = Path("outputs") / name
    path = root / relative
    try:
        shape.exportBrep(str(path))
        os.chmod(path, 0o600)
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
            "Detached detail geometry could not be written.",
        ) from exc
    data = _read_regular(path, root=root, maximum=_MAX_ARTIFACT_BYTES)
    return {
        "artifact": str(relative),
        "artifact_bytes": len(data),
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
    }


def _projection(view: Any, root: Path) -> dict[str, Any]:
    try:
        snapshot = view.getPrecomputedProjection()
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_DETAIL_PROJECTION_FAILED",
            "TechDraw produced no detail projection geometry.",
        ) from exc
    edges = snapshot["edges"]
    faces = snapshot["faces"]
    edge_count = len(tuple(edges.Edges))
    face_count = len(tuple(faces.Faces))
    classes = [int(value) for value in snapshot["edge_classes"]]
    visibility = [bool(value) for value in snapshot["edge_visibility"]]
    sources = [int(value) for value in snapshot["source_indices"]]
    if (
        not 1 <= edge_count <= _MAX_EDGES
        or not 0 <= face_count <= _MAX_FACES
        or len(classes) != edge_count
        or len(visibility) != edge_count
        or len(sources) != edge_count
        or not any(visibility)
    ):
        _fail("NATIVE_DRAWING_DETAIL_OUTPUT_INVALID", "Detached detail projection geometry is inconsistent.")
    centroid = snapshot["centroid"]
    return {
        "key": "detail_view",
        "edge_count": edge_count,
        "face_count": face_count,
        "visible_edge_count": sum(visibility),
        "hidden_edge_count": edge_count - sum(visibility),
        "edges": _artifact(root, edges, "projection-000-edges.brep"),
        "faces": _artifact(root, faces, "projection-000-faces.brep"),
        "edge_classes": classes,
        "edge_visibility": visibility,
        "source_indices": sources,
        "centroid": _vector([centroid.x, centroid.y, centroid.z], "projection centroid", 3),
    }


def _detail_geometry(view: Any, root: Path) -> dict[str, Any]:
    try:
        shape = view.getPrecomputedDetail()["detail_shape"]
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_DETAIL_CUT_FAILED",
            "TechDraw produced no completed detail-cut geometry.",
        ) from exc
    counts = {
        "solid_count": len(tuple(shape.Solids)),
        "face_count": len(tuple(shape.Faces)),
        "edge_count": len(tuple(shape.Edges)),
    }
    if (
        not 0 <= counts["solid_count"] <= _MAX_SOLIDS
        or not 0 <= counts["face_count"] <= _MAX_FACES
        or not 1 <= counts["edge_count"] <= _MAX_EDGES
        or not bool(shape.isValid())
    ):
        _fail("NATIVE_DRAWING_DETAIL_OUTPUT_INVALID", "Detached detail cut geometry is inconsistent.")
    return {
        "detail_shape": _artifact(root, shape, "detail-shape.brep"),
        **counts,
    }


def _execute(request: Mapping[str, Any], root: Path, request_sha256: str) -> dict[str, Any]:
    import FreeCAD as App

    page_spec = _object_descriptor(request["page"])
    base_spec = _object_descriptor(request["base_view"])
    source_values = request["sources"]
    if not isinstance(source_values, list) or not 1 <= len(source_values) <= 12:
        _fail("NATIVE_DRAWING_DETAIL_LIMIT", "The detached detail source count is invalid.")
    source_specs = [_object_descriptor(item) for item in source_values]
    settings = _detail_descriptor(request["detail"])
    snapshot_path = root / str(request["snapshot"])
    snapshot = _read_regular(snapshot_path, root=root, maximum=_MAX_SNAPSHOT_BYTES)
    if (
        type(request["snapshot_bytes"]) is not int
        or len(snapshot) != int(request["snapshot_bytes"])
        or hashlib.sha256(snapshot).hexdigest() != str(request["snapshot_sha256"])
    ):
        _fail("NATIVE_DRAWING_DETAIL_SNAPSHOT_CHANGED", "The detached Drawing snapshot changed before detailing.")
    techdraw_root = "User parameter:BaseApp/Preferences/Mod/TechDraw"
    general = App.ParamGet(f"{techdraw_root}/General")
    general.SetBool("GlobalUpdateDrawings", True)
    general.SetBool("AllowPageOverride", True)
    App.ParamGet(f"{techdraw_root}/Decorations").SetInt(
        "MattingStyle",
        int(settings["matting_style"]),
    )
    try:
        os.mkdir(root / "outputs", 0o700)
    except OSError as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
            "The detached detail output directory could not be created.",
        ) from exc
    document = App.openDocument(str(snapshot_path))
    try:
        page = _canonical(document, page_spec)
        base = _canonical(document, base_spec)
        sources = [_canonical(document, item) for item in source_specs]
        if not page.isDerivedFrom("TechDraw::DrawPage"):
            _fail("NATIVE_DRAWING_DETAIL_PAGE_INVALID", "The exact Drawing page is invalid in its snapshot.")
        if not base.isDerivedFrom("TechDraw::DrawViewPart"):
            _fail("NATIVE_DRAWING_DETAIL_BASE_INVALID", "The exact Drawing base view is invalid in its snapshot.")
        if base not in tuple(page.Views or ()) or tuple(base.Source or ()) != tuple(sources):
            _fail("NATIVE_DRAWING_DETAIL_GRAPH_CHANGED", "The exact base-view graph changed in its snapshot.")
        document.openTransaction("Detached detail projection")
        try:
            detail = document.addObject("TechDraw::DrawViewDetail", "DetachedDetailView")
            detail.BaseView = base
            detail.Source = sources
            anchor = _vector(settings["anchor_mm"], "detail anchor", 2)
            detail.AnchorPoint = App.Vector(*anchor, 0.0)
            detail.Radius = float(settings["radius_mm"])
            detail.Direction = base.Direction
            detail.XDirection = base.XDirection
            detail.Rotation = float(base.Rotation)
            position = _vector(settings["position_mm"], "detail position", 2)
            detail.X, detail.Y = position
            kind = str(settings["scale_kind"])
            detail.Scale = (
                float(settings["requested_scale"])
                if kind == "custom"
                else (
                    float(settings["page_scale"])
                    if kind == "page"
                    else float(settings["base_scale"])
                )
            )
            detail.ScaleType = {
                "page": "Page",
                "automatic": "Automatic",
                "custom": "Custom",
            }[kind]
            for name, flag in dict(settings["line_flags"]).items():
                setattr(detail, name, bool(flag))
            document.publishProvisionalTimelineOperationBlock(detail, (), ())
            if int(page.addPrecomputedView(detail)) < 1:
                _fail("NATIVE_DRAWING_DETAIL_EXECUTION_FAILED", "The detached detail could not join its page.")
            detail.touch()
            if document.recompute([*sources, base, detail, page], True, True) is False:
                _fail("NATIVE_DRAWING_DETAIL_EXECUTION_FAILED", "TechDraw rejected the detached detail.")
            document.commitTransaction()
        except Exception:
            document.abortTransaction()
            raise
        if not bool(detail.isValid()) or {"Invalid", "Error"} & set(detail.State or ()):
            _fail("NATIVE_DRAWING_DETAIL_EXECUTION_FAILED", "TechDraw produced an invalid detached detail.")
        effective_scale = float(detail.Scale)
        if not math.isfinite(effective_scale) or not 1.0e-12 <= effective_scale <= 1_000.0:
            _fail("NATIVE_DRAWING_DETAIL_OUTPUT_INVALID", "TechDraw produced an invalid detail scale.")
        return {
            "ok": True,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "page_name": str(page.Name),
            "base_name": str(base.Name),
            "source_names": [str(source.Name) for source in sources],
            "effective_scale": effective_scale,
            "projection": _projection(detail, root),
            "detail": _detail_geometry(detail, root),
        }
    finally:
        App.closeDocument(document.Name)


def _main() -> int:
    result_path: Path | None = None
    request_sha256 = ""
    try:
        request, root, result_path, request_sha256 = _request()
        result = _execute(request, root, request_sha256)
        _write_private(
            result_path,
            json.dumps(result, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8"),
        )
        return 0
    except _ChildFailure as exc:
        failure = {
            "ok": False,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "error_code": exc.code,
            "message": str(exc)[:320],
        }
    except Exception:
        failure = {
            "ok": False,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "error_code": "NATIVE_DRAWING_DETAIL_EXECUTION_FAILED",
            "message": "The isolated Drawing detail process failed.",
        }
    if result_path is not None and not result_path.exists():
        try:
            _write_private(
                result_path,
                json.dumps(failure, sort_keys=True, separators=(",", ":")).encode("utf-8"),
            )
        except Exception:
            pass
    return 1


if __name__ == "__main__":
    raise SystemExit(_main())
