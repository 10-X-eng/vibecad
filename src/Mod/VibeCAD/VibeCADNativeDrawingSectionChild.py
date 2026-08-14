# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd child for one exact straight TechDraw section."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Mapping


_REQUEST_ENV = "VIBECAD_NATIVE_DRAWING_SECTION_REQUEST"
_PROTOCOL = "vibecad-native-drawing-section-v1"
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
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "An input escaped its private workspace.")
    try:
        value = path.lstat()
    except OSError:
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "A detached section input is unavailable.")
    if not stat.S_ISREG(value.st_mode):
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "A detached section input is not a regular file.")
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
            _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "A detached section input changed while opening.")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _fail("NATIVE_DRAWING_SECTION_LIMIT", "A detached section input exceeds its safety bound.")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "A detached section input is empty.")
    return bytes(data)


def _write_private(path: Path, data: bytes) -> None:
    if not data or len(data) > _MAX_RESULT_BYTES:
        _fail("NATIVE_DRAWING_SECTION_LIMIT", "Detached section metadata exceeds its safety bound.")
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
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "The detached section request is unavailable.")
    path = Path(path_text)
    root = path.parent.resolve()
    data = _read_regular(path, root=root, maximum=_MAX_REQUEST_BYTES)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "The detached section request is unreadable.")
    required = {
        "protocol",
        "workspace",
        "snapshot",
        "snapshot_bytes",
        "snapshot_sha256",
        "page",
        "base_view",
        "sources",
        "section",
        "result",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "The detached section request is malformed.")
    if (
        str(value["protocol"]) != _PROTOCOL
        or Path(str(value["workspace"])).resolve() != root
        or str(value["snapshot"]) != "document.FCStd"
        or str(value["result"]) != "result.json"
    ):
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "The detached section request identity is invalid.")
    return dict(value), root, root / "result.json", hashlib.sha256(data).hexdigest()


def _object_descriptor(value: Any) -> dict[str, Any]:
    required = {"object_id", "object_name", "type_id", "state_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "An exact Drawing object descriptor is malformed.")
    result = dict(value)
    if (
        type(result["object_id"]) is not int
        or int(result["object_id"]) < 0
        or not str(result["object_name"] or "")
        or not str(result["type_id"] or "")
        or len(str(result["state_sha256"] or "")) != 64
    ):
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "An exact Drawing object descriptor is invalid.")
    return result


def _vector(value: Any, field_name: str, count: int = 3) -> list[float]:
    if not isinstance(value, list) or len(value) != count:
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", f"{field_name} is malformed.")
    result = []
    for item in value:
        if type(item) not in {int, float} or not math.isfinite(float(item)):
            _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", f"{field_name} is malformed.")
        result.append(float(item))
    return result


def _unit(value: Any, field_name: str) -> list[float]:
    result = _vector(value, field_name)
    length = sum(item * item for item in result) ** 0.5
    if not math.isclose(length, 1.0, rel_tol=1.0e-8, abs_tol=1.0e-8):
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", f"{field_name} is not normalized.")
    return result


def _section_descriptor(value: Any) -> dict[str, Any]:
    required = {
        "origin_mm",
        "normal",
        "x_direction",
        "rotation_degrees",
        "scale_kind",
        "requested_scale",
        "page_scale",
        "page_size_mm",
        "line_flags",
        "fuse_before_cut",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "The section settings are malformed.")
    result = dict(value)
    _vector(result["origin_mm"], "section origin")
    normal = _unit(result["normal"], "section normal")
    x_direction = _unit(result["x_direction"], "section x direction")
    if abs(sum(a * b for a, b in zip(normal, x_direction, strict=True))) > 1.0e-8:
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "The section axes are not orthogonal.")
    rotation = result["rotation_degrees"]
    kind = str(result["scale_kind"] or "")
    requested = result["requested_scale"]
    page_scale = result["page_scale"]
    page_size = _vector(result["page_size_mm"], "page size", count=2)
    flags = result["line_flags"]
    if (
        type(rotation) not in {int, float}
        or not math.isfinite(float(rotation))
        or kind not in {"page", "automatic", "custom"}
        or type(page_scale) not in {int, float}
        or not 1.0e-12 <= float(page_scale) <= 1_000.0
        or any(not 1.0e-9 <= item <= 1.0e6 for item in page_size)
        or not isinstance(flags, Mapping)
        or set(flags) != _LINE_FLAGS
        or any(type(flag) is not bool for flag in flags.values())
        or type(result["fuse_before_cut"]) is not bool
    ):
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "The section settings are invalid.")
    if kind == "custom":
        if type(requested) not in {int, float} or not 1.0e-12 <= float(requested) <= 1_000.0:
            _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "The custom section scale is invalid.")
    elif requested is not None:
        _fail("NATIVE_DRAWING_SECTION_CHILD_INVALID", "A non-custom section has a custom scale.")
    return result


def _canonical(document: Any, descriptor: Mapping[str, Any]) -> Any:
    obj = document.getObject(str(descriptor["object_name"]))
    if (
        obj is None
        or int(getattr(obj, "ID", -1)) != int(descriptor["object_id"])
        or str(getattr(obj, "TypeId", "") or "") != str(descriptor["type_id"])
    ):
        _fail("NATIVE_DRAWING_SECTION_GRAPH_CHANGED", "An exact Drawing object identity changed in its snapshot.")
    return obj


def _artifact(root: Path, shape: Any, name: str) -> dict[str, Any]:
    relative = Path("outputs") / name
    path = root / relative
    try:
        shape.exportBrep(str(path))
        os.chmod(path, 0o600)
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
            "Detached section geometry could not be written.",
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
            "NATIVE_DRAWING_SECTION_PROJECTION_FAILED",
            "TechDraw produced no section projection geometry.",
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
        _fail("NATIVE_DRAWING_SECTION_OUTPUT_INVALID", "Detached section projection geometry is inconsistent.")
    centroid = _vector(
        [snapshot["centroid"].x, snapshot["centroid"].y, snapshot["centroid"].z],
        "projection centroid",
    )
    return {
        "key": "section_view",
        "edge_count": edge_count,
        "face_count": face_count,
        "visible_edge_count": sum(visibility),
        "hidden_edge_count": edge_count - sum(visibility),
        "edges": _artifact(root, edges, "projection-000-edges.brep"),
        "faces": _artifact(root, faces, "projection-000-faces.brep"),
        "edge_classes": classes,
        "edge_visibility": visibility,
        "source_indices": sources,
        "centroid": centroid,
    }


def _section_geometry(view: Any, root: Path) -> dict[str, Any]:
    try:
        snapshot = view.getPrecomputedSection()
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_SECTION_CUT_FAILED",
            "TechDraw produced no completed section-cut geometry.",
        ) from exc
    cut = snapshot["cut_pieces"]
    faces = snapshot["section_faces"]
    counts = {
        "cut_solid_count": len(tuple(cut.Solids)),
        "cut_face_count": len(tuple(cut.Faces)),
        "cut_edge_count": len(tuple(cut.Edges)),
        "section_face_count": len(tuple(faces.Faces)),
    }
    if (
        not 1 <= counts["cut_solid_count"] <= _MAX_SOLIDS
        or not 1 <= counts["cut_face_count"] <= _MAX_FACES
        or not 1 <= counts["cut_edge_count"] <= _MAX_EDGES
        or not 1 <= counts["section_face_count"] <= _MAX_FACES
    ):
        _fail("NATIVE_DRAWING_SECTION_OUTPUT_INVALID", "Detached section cut geometry is inconsistent.")
    centroid = snapshot["centroid"]
    return {
        "cut_pieces": _artifact(root, cut, "section-cut-pieces.brep"),
        "section_faces": _artifact(root, faces, "section-faces.brep"),
        "centroid": _vector([centroid.x, centroid.y, centroid.z], "section centroid"),
        **counts,
    }


def _execute(request: Mapping[str, Any], root: Path, request_sha256: str) -> dict[str, Any]:
    import FreeCAD as App

    page_spec = _object_descriptor(request["page"])
    base_spec = _object_descriptor(request["base_view"])
    source_values = request["sources"]
    if not isinstance(source_values, list) or not 1 <= len(source_values) <= 12:
        _fail("NATIVE_DRAWING_SECTION_LIMIT", "The detached section source count is invalid.")
    source_specs = [_object_descriptor(item) for item in source_values]
    settings = _section_descriptor(request["section"])
    snapshot_path = root / str(request["snapshot"])
    snapshot = _read_regular(snapshot_path, root=root, maximum=_MAX_SNAPSHOT_BYTES)
    if (
        type(request["snapshot_bytes"]) is not int
        or len(snapshot) != int(request["snapshot_bytes"])
        or hashlib.sha256(snapshot).hexdigest() != str(request["snapshot_sha256"])
    ):
        _fail("NATIVE_DRAWING_SECTION_SNAPSHOT_CHANGED", "The detached Drawing snapshot changed before sectioning.")
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/General")
    preferences.SetBool("GlobalUpdateDrawings", True)
    preferences.SetBool("AllowPageOverride", True)
    try:
        os.mkdir(root / "outputs", 0o700)
    except OSError as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
            "The detached section output directory could not be created.",
        ) from exc
    document = App.openDocument(str(snapshot_path))
    try:
        page = _canonical(document, page_spec)
        base = _canonical(document, base_spec)
        sources = [_canonical(document, item) for item in source_specs]
        if not page.isDerivedFrom("TechDraw::DrawPage"):
            _fail("NATIVE_DRAWING_SECTION_PAGE_INVALID", "The exact Drawing page is invalid in its snapshot.")
        if not base.isDerivedFrom("TechDraw::DrawViewPart") or base.isDerivedFrom("TechDraw::DrawViewSection"):
            _fail("NATIVE_DRAWING_SECTION_BASE_INVALID", "The exact Drawing base view is unsupported in its snapshot.")
        if base not in tuple(page.Views or ()) or tuple(base.Source or ()) != tuple(sources):
            _fail("NATIVE_DRAWING_SECTION_GRAPH_CHANGED", "The exact base-view graph changed in its snapshot.")
        document.openTransaction("Detached straight-section projection")
        try:
            section = document.addObject("TechDraw::DrawViewSection", "DetachedSectionView")
            section.BaseView = base
            section.Source = sources
            section.SectionOrigin = App.Vector(*_vector(settings["origin_mm"], "section origin"))
            section.SectionDirection = "Aligned"
            section.Direction = App.Vector(*_unit(settings["normal"], "section normal"))
            section.SectionNormal = App.Vector(*_unit(settings["normal"], "section normal"))
            section.XDirection = App.Vector(*_unit(settings["x_direction"], "section x direction"))
            section.Rotation = float(settings["rotation_degrees"])
            kind = str(settings["scale_kind"])
            section.Scale = (
                float(settings["requested_scale"])
                if kind == "custom"
                else float(settings["page_scale"])
            )
            section.ScaleType = {"page": "Page", "automatic": "Automatic", "custom": "Custom"}[kind]
            section.FuseBeforeCut = bool(settings["fuse_before_cut"])
            section.TrimAfterCut = False
            section.UsePreviousCut = False
            for name, flag in dict(settings["line_flags"]).items():
                setattr(section, name, bool(flag))
            page_size = _vector(settings["page_size_mm"], "page size", count=2)
            section.X = page_size[0] / 2.0
            section.Y = page_size[1] / 2.0
            document.publishProvisionalTimelineOperationBlock(section, (), ())
            if int(page.addPrecomputedView(section)) < 1:
                _fail("NATIVE_DRAWING_SECTION_EXECUTION_FAILED", "The detached section could not join its page.")
            section.touch()
            if document.recompute([*sources, base, section, page], True, True) is False:
                _fail("NATIVE_DRAWING_SECTION_EXECUTION_FAILED", "TechDraw rejected the detached section.")
            document.commitTransaction()
        except Exception:
            document.abortTransaction()
            raise
        if not bool(section.isValid()) or {"Invalid", "Error"} & set(section.State or ()):
            _fail("NATIVE_DRAWING_SECTION_EXECUTION_FAILED", "TechDraw produced an invalid detached section.")
        effective_scale = float(section.Scale)
        if not math.isfinite(effective_scale) or not 1.0e-12 <= effective_scale <= 1_000.0:
            _fail("NATIVE_DRAWING_SECTION_OUTPUT_INVALID", "TechDraw produced an invalid section scale.")
        return {
            "ok": True,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "page_name": str(page.Name),
            "base_name": str(base.Name),
            "source_names": [str(source.Name) for source in sources],
            "effective_scale": effective_scale,
            "projection": _projection(section, root),
            "section": _section_geometry(section, root),
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
            "error_code": "NATIVE_DRAWING_SECTION_EXECUTION_FAILED",
            "message": "The isolated Drawing section process failed.",
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
