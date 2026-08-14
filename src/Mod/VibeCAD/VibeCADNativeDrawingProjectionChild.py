# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd child for high-quality Native Drawing projection."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Mapping


_REQUEST_ENV = "VIBECAD_NATIVE_DRAWING_PROJECTION_REQUEST"
_PROTOCOL = "vibecad-native-drawing-projection-v1"
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_MAX_SOURCE_BYTES = 256 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_PROJECTIONS = 128
_MAX_SOURCES = 128
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


def _inside(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _read_regular(path: Path, *, maximum: int, root: Path) -> tuple[bytes, os.stat_result]:
    resolved = path.resolve()
    if not _inside(resolved, root):
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "An input escaped its private workspace.")
    try:
        value = path.lstat()
    except OSError:
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "A frozen input is unavailable.")
    if not stat.S_ISREG(value.st_mode):
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "A frozen input is not a regular file.")
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
            _fail(
                "NATIVE_DRAWING_PROJECTION_CHILD_INVALID",
                "A frozen input changed while opening.",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _fail(
                    "NATIVE_DRAWING_PROJECTION_LIMIT",
                    "A frozen input exceeds its safety bound.",
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "A frozen input is empty.")
    return bytes(data), value


def _write_private(path: Path, data: bytes) -> None:
    if len(data) > _MAX_RESULT_BYTES:
        _fail(
            "NATIVE_DRAWING_PROJECTION_LIMIT",
            "Detached Drawing projection metadata exceeds its safety bound.",
        )
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _request() -> tuple[dict[str, Any], Path, Path, str]:
    raw_path = str(os.environ.get(_REQUEST_ENV) or "").strip()
    if not raw_path:
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "The child request is unavailable.")
    path = Path(raw_path)
    root = path.parent.resolve()
    data, _identity = _read_regular(path, maximum=_MAX_REQUEST_BYTES, root=root)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "The child request is unreadable.")
    required = {"protocol", "workspace", "sources", "projections", "result"}
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "The child request is malformed.")
    if str(value["protocol"]) != _PROTOCOL or Path(str(value["workspace"])).resolve() != root:
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "The child request identity is invalid.")
    result_relative = Path(str(value["result"] or ""))
    if result_relative != Path("result.json"):
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "The child result path is invalid.")
    return dict(value), root, root / result_relative, hashlib.sha256(data).hexdigest()


def _number_vector(value: Any, field_name: str) -> list[float]:
    if not isinstance(value, list) or len(value) != 3:
        _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", f"{field_name} is malformed.")
    result = []
    for item in value:
        if type(item) not in {int, float}:
            _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", f"{field_name} is malformed.")
        number = float(item)
        if not math.isfinite(number):
            _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", f"{field_name} is malformed.")
        result.append(number)
    return result


def _source_descriptors(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_SOURCES:
        _fail("NATIVE_DRAWING_PROJECTION_LIMIT", "The detached source count is invalid.")
    result = []
    for index, item in enumerate(value):
        required = {
            "index",
            "object_name",
            "state_sha256",
            "artifact",
            "artifact_bytes",
            "artifact_sha256",
        }
        if not isinstance(item, Mapping) or set(item) != required:
            _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "A source descriptor is malformed.")
        if (
            type(item["index"]) is not int
            or item["index"] != index
            or str(item["artifact"]) != f"sources/source-{index:03d}.brep"
            or not 1 <= int(item["artifact_bytes"]) <= _MAX_SOURCE_BYTES
            or len(str(item["artifact_sha256"])) != 64
            or len(str(item["state_sha256"])) != 64
        ):
            _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "A source descriptor is invalid.")
        result.append(dict(item))
    return result


def _projection_descriptors(value: Any, source_count: int) -> list[dict[str, Any]]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_PROJECTIONS:
        _fail("NATIVE_DRAWING_PROJECTION_LIMIT", "The detached projection count is invalid.")
    result = []
    keys = set()
    for item in value:
        required = {
            "key",
            "source_indices",
            "direction",
            "x_direction",
            "scale",
            "line_flags",
        }
        if not isinstance(item, Mapping) or set(item) != required:
            _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "A projection descriptor is malformed.")
        key = str(item["key"] or "")
        indices = item["source_indices"]
        flags = item["line_flags"]
        scale = item["scale"]
        if (
            not key
            or len(key) > 128
            or key in keys
            or not isinstance(indices, list)
            or not 1 <= len(indices) <= 12
            or any(type(index) is not int or not 0 <= index < source_count for index in indices)
            or not isinstance(flags, Mapping)
            or set(flags) != _LINE_FLAGS
            or any(type(flag) is not bool for flag in flags.values())
            or type(scale) not in {int, float}
            or not math.isfinite(float(scale))
            or not 1.0e-12 <= float(scale) <= 1_000.0
        ):
            _fail("NATIVE_DRAWING_PROJECTION_CHILD_INVALID", "A projection descriptor is invalid.")
        _number_vector(item["direction"], "direction")
        _number_vector(item["x_direction"], "x_direction")
        keys.add(key)
        result.append(dict(item))
    return result


def _load_sources(root: Path, descriptors: list[dict[str, Any]], document: Any) -> list[Any]:
    import Part

    result = []
    for index, descriptor in enumerate(descriptors):
        path = root / str(descriptor["artifact"])
        data, _identity = _read_regular(path, maximum=_MAX_SOURCE_BYTES, root=root)
        if (
            len(data) != int(descriptor["artifact_bytes"])
            or hashlib.sha256(data).hexdigest() != str(descriptor["artifact_sha256"])
        ):
            _fail("NATIVE_DRAWING_PROJECTION_INPUT_CHANGED", "A frozen source changed before projection.")
        shape = Part.Shape()
        try:
            shape.importBrep(str(path))
        except Exception as exc:
            raise _ChildFailure(
                "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
                "A frozen Drawing source could not be imported.",
            ) from exc
        if shape.isNull() or not shape.isValid():
            _fail("NATIVE_DRAWING_PROJECTION_INPUT_INVALID", "A frozen Drawing source shape is invalid.")
        obj = document.addObject("Part::Feature", f"ProjectionSource{index:03d}")
        obj.Label = str(descriptor["object_name"])
        obj.Shape = shape
        result.append(obj)
    return result


def _artifact(root: Path, shape: Any, relative: Path) -> dict[str, Any]:
    path = root / relative
    try:
        shape.exportBrep(str(path))
        os.chmod(path, 0o600)
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            "A detached projection artifact could not be written.",
        ) from exc
    data, _identity = _read_regular(path, maximum=_MAX_ARTIFACT_BYTES, root=root)
    return {
        "artifact": str(relative),
        "artifact_bytes": len(data),
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
    }


def _projection(
    document: Any,
    page: Any,
    sources: list[Any],
    descriptor: Mapping[str, Any],
    root: Path,
    index: int,
) -> dict[str, Any]:
    import FreeCAD as App

    view = document.addObject("TechDraw::DrawViewPart", f"Projection{index:03d}")
    view.Source = [sources[source_index] for source_index in descriptor["source_indices"]]
    view.Direction = App.Vector(*_number_vector(descriptor["direction"], "direction"))
    view.XDirection = App.Vector(*_number_vector(descriptor["x_direction"], "x_direction"))
    view.ScaleType = "Custom"
    view.Scale = float(descriptor["scale"])
    for name, flag in dict(descriptor["line_flags"]).items():
        setattr(view, name, bool(flag))
    if int(page.addPrecomputedView(view)) < 1:
        _fail("NATIVE_DRAWING_PROJECTION_EXECUTION_FAILED", "The detached view could not join its page.")
    view.touch()
    if document.recompute([*view.Source, view, page], True, True) is False:
        _fail("NATIVE_DRAWING_PROJECTION_EXECUTION_FAILED", "TechDraw rejected the detached projection.")
    state = {str(value) for value in tuple(view.State or ())}
    if {"Invalid", "Error"} & state or not bool(view.isValid()):
        _fail("NATIVE_DRAWING_PROJECTION_EXECUTION_FAILED", "TechDraw produced an invalid detached view.")
    try:
        snapshot = view.getPrecomputedProjection()
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_PROJECTION_EMPTY",
            "TechDraw produced no projected geometry.",
        ) from exc
    required = {
        "edges",
        "faces",
        "edge_classes",
        "edge_visibility",
        "source_indices",
        "centroid",
    }
    if not isinstance(snapshot, Mapping) or set(snapshot) != required:
        _fail("NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID", "TechDraw returned malformed projection data.")
    edge_count = len(tuple(snapshot["edges"].Edges))
    face_count = len(tuple(snapshot["faces"].Faces))
    classes = [int(value) for value in snapshot["edge_classes"]]
    visibility = [bool(value) for value in snapshot["edge_visibility"]]
    source_indices = [int(value) for value in snapshot["source_indices"]]
    if (
        not 1 <= edge_count <= _MAX_EDGES
        or not 0 <= face_count <= _MAX_FACES
        or len(classes) != edge_count
        or len(visibility) != edge_count
        or len(source_indices) != edge_count
        or not any(visibility)
    ):
        _fail("NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID", "TechDraw returned inconsistent projection geometry.")
    outputs = root / "outputs"
    prefix = f"projection-{index:03d}"
    edge_artifact = _artifact(outputs, snapshot["edges"], Path(f"{prefix}-edges.brep"))
    face_artifact = _artifact(outputs, snapshot["faces"], Path(f"{prefix}-faces.brep"))
    centroid = snapshot["centroid"]
    return {
        "key": str(descriptor["key"]),
        "edge_count": edge_count,
        "face_count": face_count,
        "visible_edge_count": sum(visibility),
        "hidden_edge_count": edge_count - sum(visibility),
        "edges": {**edge_artifact, "artifact": f"outputs/{edge_artifact['artifact']}"},
        "faces": {**face_artifact, "artifact": f"outputs/{face_artifact['artifact']}"},
        "edge_classes": classes,
        "edge_visibility": visibility,
        "source_indices": source_indices,
        "centroid": _number_vector(
            [centroid.x, centroid.y, centroid.z],
            "centroid",
        ),
    }


def _execute(request: Mapping[str, Any], root: Path, request_sha256: str) -> dict[str, Any]:
    import FreeCAD as App

    source_descriptors = _source_descriptors(request["sources"])
    projection_descriptors = _projection_descriptors(
        request["projections"],
        len(source_descriptors),
    )
    outputs = root / "outputs"
    outputs.mkdir(mode=0o700)
    document = App.newDocument("NativeDrawingProjectionWorker")
    try:
        # The worker has a private HOME, so these deterministic preferences can
        # never alter the human's TechDraw configuration.
        preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/Mod/TechDraw/General"
        )
        preferences.SetBool("GlobalUpdateDrawings", True)
        preferences.SetBool("AllowPageOverride", True)
        sources = _load_sources(root, source_descriptors, document)
        page = document.addObject("TechDraw::DrawPage", "ProjectionPage")
        page.KeepUpdated = True
        projections = [
            _projection(document, page, sources, descriptor, root, index)
            for index, descriptor in enumerate(projection_descriptors)
        ]
        return {
            "ok": True,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "projections": projections,
        }
    finally:
        App.closeDocument(document.Name)


def _main() -> int:
    result_path: Path | None = None
    request_sha256 = ""
    try:
        request, root, result_path, request_sha256 = _request()
        result = _execute(request, root, request_sha256)
        encoded = json.dumps(
            result,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        _write_private(result_path, encoded)
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
            "error_code": "NATIVE_DRAWING_PROJECTION_EXECUTION_FAILED",
            "message": "The isolated Drawing projection process failed.",
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
