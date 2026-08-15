# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd child for one exact native TechDraw broken view."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Mapping


_REQUEST_ENV = "VIBECAD_NATIVE_DRAWING_BROKEN_REQUEST"
_PROTOCOL = "vibecad-native-drawing-broken-v1"
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_EDGES = 200_000
_MAX_FACES = 50_000
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
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "An input escaped its private workspace.")
    try:
        value = path.lstat()
    except OSError:
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "A detached broken-view input is unavailable.")
    if not stat.S_ISREG(value.st_mode):
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "A detached broken-view input is not a regular file.")
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
            _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "A detached broken-view input changed while opening.")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _fail("NATIVE_DRAWING_BROKEN_LIMIT", "A detached broken-view input exceeds its safety bound.")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "A detached broken-view input is empty.")
    return bytes(data)


def _write_private(path: Path, data: bytes) -> None:
    if not data or len(data) > _MAX_RESULT_BYTES:
        _fail("NATIVE_DRAWING_BROKEN_LIMIT", "Detached broken-view metadata exceeds its safety bound.")
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
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "The detached broken-view request is unavailable.")
    path = Path(path_text)
    root = path.parent.resolve()
    data = _read_regular(path, root=root, maximum=_MAX_REQUEST_BYTES)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "The detached broken-view request is unreadable.")
    required = {
        "protocol",
        "workspace",
        "snapshot",
        "snapshot_bytes",
        "snapshot_sha256",
        "page",
        "sources",
        "breaks",
        "view",
        "result",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "The detached broken-view request is malformed.")
    if (
        str(value["protocol"]) != _PROTOCOL
        or Path(str(value["workspace"])).resolve() != root
        or str(value["snapshot"]) != "document.FCStd"
        or str(value["result"]) != "result.json"
    ):
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "The detached broken-view request identity is invalid.")
    return dict(value), root, root / "result.json", hashlib.sha256(data).hexdigest()


def _object_descriptor(value: Any, *, break_definition: bool = False) -> dict[str, Any]:
    required = {"object_id", "object_name", "type_id", "state_sha256"}
    if break_definition:
        required.add("kind")
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "An exact object descriptor is malformed.")
    result = dict(value)
    if (
        type(result["object_id"]) is not int
        or int(result["object_id"]) < 0
        or not str(result["object_name"] or "")
        or not str(result["type_id"] or "")
        or len(str(result["state_sha256"] or "")) != 64
        or (break_definition and str(result["kind"]) not in {"single_edge", "two_line_sketch"})
    ):
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "An exact object descriptor is invalid.")
    return result


def _vector(value: Any, field_name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", f"{field_name} is malformed.")
    result = []
    for item in value:
        if type(item) not in {int, float} or not math.isfinite(float(item)):
            _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", f"{field_name} is malformed.")
        result.append(float(item))
    return result


def _view_descriptor(value: Any) -> dict[str, Any]:
    required = {"direction", "x_direction", "scale", "gap_mm", "line_flags"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "The broken-view settings are malformed.")
    result = dict(value)
    flags = result["line_flags"]
    if (
        type(result["scale"]) not in {int, float}
        or not 1.0e-12 <= float(result["scale"]) <= 1_000.0
        or type(result["gap_mm"]) not in {int, float}
        or not 0.0 <= float(result["gap_mm"]) <= 10_000.0
        or not isinstance(flags, Mapping)
        or set(flags) != _LINE_FLAGS
        or any(type(flag) is not bool for flag in flags.values())
    ):
        _fail("NATIVE_DRAWING_BROKEN_CHILD_INVALID", "The broken-view settings are invalid.")
    _vector(result["direction"], "direction")
    _vector(result["x_direction"], "x_direction")
    return result


def _canonical(document: Any, descriptor: Mapping[str, Any]) -> Any:
    obj = document.getObject(str(descriptor["object_name"]))
    if (
        obj is None
        or int(getattr(obj, "ID", -1)) != int(descriptor["object_id"])
        or str(getattr(obj, "TypeId", "") or "") != str(descriptor["type_id"])
    ):
        _fail("NATIVE_DRAWING_BROKEN_GRAPH_CHANGED", "An exact Drawing object identity changed in its snapshot.")
    return obj


def _artifact(root: Path, shape: Any, name: str) -> dict[str, Any]:
    relative = Path("outputs") / name
    path = root / relative
    try:
        shape.exportBrep(str(path))
        os.chmod(path, 0o600)
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
            "Detached broken-view geometry could not be written.",
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
            "NATIVE_DRAWING_BROKEN_PROJECTION_FAILED",
            "TechDraw produced no broken-view projection geometry.",
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
        _fail("NATIVE_DRAWING_BROKEN_OUTPUT_INVALID", "Detached broken-view projection geometry is inconsistent.")
    centroid = _vector(
        [snapshot["centroid"].x, snapshot["centroid"].y, snapshot["centroid"].z],
        "centroid",
    )
    return {
        "key": "broken_view",
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


def _semantic_value(value: Any) -> Any:
    if isinstance(value, Mapping):
        ignored = {"name", "source_mapping", "hlr_source_index"}
        return {
            str(key): _semantic_value(item)
            for key, item in sorted(value.items())
            if str(key) not in ignored
        }
    if isinstance(value, (list, tuple)):
        return [_semantic_value(item) for item in value]
    if type(value) is float:
        return round(value, 9)
    return value


def _semantic_sha256(view: Any) -> str:
    value = _semantic_value(view.getProjectedElementDescriptors())
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _configure_view(view: Any, sources: list[Any], settings: Mapping[str, Any]) -> None:
    import FreeCAD as App

    view.Source = sources
    view.Direction = App.Vector(*_vector(settings["direction"], "direction"))
    view.XDirection = App.Vector(*_vector(settings["x_direction"], "x_direction"))
    view.ScaleType = "Custom"
    view.Scale = float(settings["scale"])
    for name, flag in dict(settings["line_flags"]).items():
        setattr(view, name, bool(flag))


def _execute(request: Mapping[str, Any], root: Path, request_sha256: str) -> dict[str, Any]:
    import FreeCAD as App

    page_descriptor = _object_descriptor(request["page"])
    source_descriptors = request["sources"]
    break_descriptors = request["breaks"]
    if not isinstance(source_descriptors, list) or not 1 <= len(source_descriptors) <= 12:
        _fail("NATIVE_DRAWING_BROKEN_LIMIT", "The detached broken-view source count is invalid.")
    if not isinstance(break_descriptors, list) or not 1 <= len(break_descriptors) <= 16:
        _fail("NATIVE_DRAWING_BROKEN_LIMIT", "The detached broken-view definition count is invalid.")
    sources_spec = [_object_descriptor(item) for item in source_descriptors]
    breaks_spec = [_object_descriptor(item, break_definition=True) for item in break_descriptors]
    settings = _view_descriptor(request["view"])
    snapshot_path = root / str(request["snapshot"])
    snapshot = _read_regular(snapshot_path, root=root, maximum=_MAX_SNAPSHOT_BYTES)
    if (
        type(request["snapshot_bytes"]) is not int
        or len(snapshot) != int(request["snapshot_bytes"])
        or hashlib.sha256(snapshot).hexdigest() != str(request["snapshot_sha256"])
    ):
        _fail("NATIVE_DRAWING_BROKEN_SNAPSHOT_CHANGED", "The detached Drawing snapshot changed before projection.")
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/General")
    preferences.SetBool("GlobalUpdateDrawings", True)
    preferences.SetBool("AllowPageOverride", True)
    try:
        os.mkdir(root / "outputs", 0o700)
    except OSError as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
            "The detached broken-view output directory could not be created.",
        ) from exc
    document = App.openDocument(str(snapshot_path))
    try:
        page = _canonical(document, page_descriptor)
        if not page.isDerivedFrom("TechDraw::DrawPage"):
            _fail("NATIVE_DRAWING_BROKEN_PAGE_INVALID", "The exact Drawing page is invalid in its snapshot.")
        sources = [_canonical(document, item) for item in sources_spec]
        breaks = [_canonical(document, item) for item in breaks_spec]
        document.openTransaction("Detached broken-view projection")
        try:
            broken = document.addObject("TechDraw::DrawBrokenView", "DetachedBrokenView")
            _configure_view(broken, sources, settings)
            broken.Breaks = breaks
            broken.Gap = float(settings["gap_mm"])
            document.publishProvisionalTimelineOperationBlock(broken, (), ())
            if int(page.addPrecomputedView(broken)) < 1:
                _fail("NATIVE_DRAWING_BROKEN_EXECUTION_FAILED", "The detached broken view could not join its page.")
            control = document.addObject("TechDraw::DrawViewPart", "DetachedControlView")
            _configure_view(control, sources, settings)
            document.publishProvisionalTimelineOperationBlock(control, (), ())
            if int(page.addPrecomputedView(control)) < 1:
                _fail("NATIVE_DRAWING_BROKEN_EXECUTION_FAILED", "The detached control view could not join its page.")
            broken.touch()
            control.touch()
            if document.recompute([*sources, *breaks, broken, control, page], True, True) is False:
                _fail("NATIVE_DRAWING_BROKEN_EXECUTION_FAILED", "TechDraw rejected the detached broken view.")
            document.commitTransaction()
        except Exception:
            document.abortTransaction()
            raise
        for view in (broken, control):
            if not bool(view.isValid()) or {"Invalid", "Error"} & set(view.State or ()):
                _fail("NATIVE_DRAWING_BROKEN_EXECUTION_FAILED", "TechDraw produced an invalid detached view.")
        break_results = []
        for obj, descriptor in zip(breaks, breaks_spec, strict=True):
            value = broken.getBreakDefinition(obj)
            if not isinstance(value, Mapping) or value.get("valid") is not True:
                _fail("NATIVE_DRAWING_BROKEN_BREAK_INVALID", "TechDraw rejected an exact break definition.")
            removed = float(value["removed_length_mm"])
            if not math.isfinite(removed) or removed <= 1.0e-9:
                _fail("NATIVE_DRAWING_BROKEN_BREAK_INVALID", "TechDraw returned an invalid break span.")
            break_results.append(
                {
                    "object_name": str(obj.Name),
                    "kind": str(descriptor["kind"]),
                    "first": _vector([value["first"].x, value["first"].y, value["first"].z], "first"),
                    "second": _vector([value["second"].x, value["second"].y, value["second"].z], "second"),
                    "direction": _vector(
                        [value["direction"].x, value["direction"].y, value["direction"].z],
                        "direction",
                    ),
                    "removed_length_mm": removed,
                }
            )
        broken_semantic = _semantic_sha256(broken)
        control_semantic = _semantic_sha256(control)
        if broken_semantic == control_semantic:
            _fail("NATIVE_DRAWING_BREAK_NO_EFFECT", "The exact break definitions do not cut the projected source geometry.")
        return {
            "ok": True,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "page_name": str(page.Name),
            "projection": _projection(broken, root),
            "breaks": break_results,
            "broken_semantic_sha256": broken_semantic,
            "control_semantic_sha256": control_semantic,
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
            "error_code": "NATIVE_DRAWING_BROKEN_EXECUTION_FAILED",
            "message": "The isolated Drawing broken-view process failed.",
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
