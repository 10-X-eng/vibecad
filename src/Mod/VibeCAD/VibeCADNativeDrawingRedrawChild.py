# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated FreeCADCmd child that redraws one exact native TechDraw page."""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import stat
from typing import Any, Mapping


_REQUEST_ENV = "VIBECAD_NATIVE_DRAWING_REDRAW_REQUEST"
_PROTOCOL = "vibecad-native-drawing-redraw-v1"
_MAX_REQUEST_BYTES = 256 * 1024
_MAX_RESULT_BYTES = 16 * 1024 * 1024
_MAX_SNAPSHOT_BYTES = 2 * 1024 * 1024 * 1024
_MAX_ARTIFACT_BYTES = 256 * 1024 * 1024
_MAX_VIEWS = 128
_MAX_PROJECTION_EDGES = 200_000
_MAX_PROJECTION_FACES = 50_000


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
        _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "An input escaped its private workspace.")
    try:
        value = path.lstat()
    except OSError:
        _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "A detached redraw input is unavailable.")
    if not stat.S_ISREG(value.st_mode):
        _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "A detached redraw input is not a regular file.")
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
            _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "A detached redraw input changed while opening.")
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _fail("NATIVE_DRAWING_REDRAW_LIMIT", "A detached redraw input exceeds its safety bound.")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "A detached redraw input is empty.")
    return bytes(data)


def _write_private(path: Path, data: bytes) -> None:
    if not data or len(data) > _MAX_RESULT_BYTES:
        _fail("NATIVE_DRAWING_REDRAW_LIMIT", "Detached redraw metadata exceeds its safety bound.")
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
        _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "The detached redraw request is unavailable.")
    path = Path(path_text)
    root = path.parent.resolve()
    data = _read_regular(path, root=root, maximum=_MAX_REQUEST_BYTES)
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "The detached redraw request is unreadable.")
    required = {
        "protocol",
        "workspace",
        "snapshot",
        "snapshot_bytes",
        "snapshot_sha256",
        "page_name",
        "page_state_sha256",
        "views",
        "result",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "The detached redraw request is malformed.")
    if (
        str(value["protocol"]) != _PROTOCOL
        or Path(str(value["workspace"])).resolve() != root
        or str(value["snapshot"]) != "document.FCStd"
        or str(value["result"]) != "result.json"
    ):
        _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "The detached redraw request identity is invalid.")
    return dict(value), root, root / "result.json", hashlib.sha256(data).hexdigest()


def _views(value: Any) -> list[dict[str, str]]:
    if not isinstance(value, list) or not 1 <= len(value) <= _MAX_VIEWS:
        _fail("NATIVE_DRAWING_REDRAW_LIMIT", "The detached redraw view count is invalid.")
    result = []
    names = set()
    for item in value:
        required = {"object_name", "type_id", "kind", "state_sha256"}
        if not isinstance(item, Mapping) or set(item) != required:
            _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "A detached redraw view descriptor is malformed.")
        descriptor = {name: str(item[name] or "") for name in required}
        if (
            not descriptor["object_name"]
            or descriptor["object_name"] in names
            or not descriptor["type_id"]
            or descriptor["kind"] not in {"projection", "dimension", "dependent"}
            or len(descriptor["state_sha256"]) != 64
        ):
            _fail("NATIVE_DRAWING_REDRAW_CHILD_INVALID", "A detached redraw view descriptor is invalid.")
        names.add(descriptor["object_name"])
        result.append(descriptor)
    return result


def _artifact(root: Path, shape: Any, name: str) -> dict[str, Any]:
    relative = Path("outputs") / name
    path = root / relative
    try:
        shape.exportBrep(str(path))
        os.chmod(path, 0o600)
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
            "A detached redraw projection artifact could not be written.",
        ) from exc
    data = _read_regular(path, root=root, maximum=_MAX_ARTIFACT_BYTES)
    return {
        "artifact": str(relative),
        "artifact_bytes": len(data),
        "artifact_sha256": hashlib.sha256(data).hexdigest(),
    }


def _projection(
    view: Any,
    descriptor: Mapping[str, str],
    root: Path,
    index: int,
) -> dict[str, Any]:
    try:
        snapshot = view.getPrecomputedProjection()
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_REDRAW_PROJECTION_FAILED",
            f"Drawing projection {view.Name!r} produced no valid geometry.",
        ) from exc
    edges = snapshot["edges"]
    faces = snapshot["faces"]
    edge_count = len(tuple(edges.Edges))
    face_count = len(tuple(faces.Faces))
    classes = [int(value) for value in snapshot["edge_classes"]]
    visibility = [bool(value) for value in snapshot["edge_visibility"]]
    sources = [int(value) for value in snapshot["source_indices"]]
    if (
        not 1 <= edge_count <= _MAX_PROJECTION_EDGES
        or not 0 <= face_count <= _MAX_PROJECTION_FACES
        or len(classes) != edge_count
        or len(visibility) != edge_count
        or len(sources) != edge_count
        or not any(visibility)
    ):
        _fail("NATIVE_DRAWING_REDRAW_OUTPUT_INVALID", "A detached redraw projection is inconsistent.")
    centroid = snapshot["centroid"]
    centroid_values = [float(centroid.x), float(centroid.y), float(centroid.z)]
    if any(not math.isfinite(value) for value in centroid_values):
        _fail("NATIVE_DRAWING_REDRAW_OUTPUT_INVALID", "A detached redraw centroid is invalid.")
    prefix = f"projection-{index:03d}"
    return {
        "object_name": descriptor["object_name"],
        "type_id": descriptor["type_id"],
        "projection": {
            "key": descriptor["object_name"],
            "edge_count": edge_count,
            "face_count": face_count,
            "visible_edge_count": sum(visibility),
            "hidden_edge_count": edge_count - sum(visibility),
            "edges": _artifact(root, edges, f"{prefix}-edges.brep"),
            "faces": _artifact(root, faces, f"{prefix}-faces.brep"),
            "edge_classes": classes,
            "edge_visibility": visibility,
            "source_indices": sources,
            "centroid": centroid_values,
        },
    }


def _dimension(view: Any, descriptor: Mapping[str, str]) -> dict[str, Any]:
    try:
        snapshot = view.getPrecomputedDimension()
    except Exception as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_REDRAW_DIMENSION_FAILED",
            f"Drawing dimension {view.Name!r} produced no valid geometry.",
        ) from exc
    vectors = [
        [float(value.x), float(value.y), float(value.z)]
        for value in snapshot["vectors"]
    ]
    scalars = [float(value) for value in snapshot["scalars"]]
    flags = [bool(value) for value in snapshot["flags"]]
    if (
        not vectors
        or not flags
        or any(not math.isfinite(value) for vector in vectors for value in vector)
        or any(not math.isfinite(value) for value in scalars)
    ):
        _fail("NATIVE_DRAWING_REDRAW_OUTPUT_INVALID", "A detached redraw dimension is inconsistent.")
    return {
        "object_name": descriptor["object_name"],
        "type_id": descriptor["type_id"],
        "vectors": vectors,
        "scalars": scalars,
        "flags": flags,
    }


def _execute(request: Mapping[str, Any], root: Path, request_sha256: str) -> dict[str, Any]:
    import FreeCAD as App

    descriptors = _views(request["views"])
    snapshot_path = root / str(request["snapshot"])
    snapshot = _read_regular(snapshot_path, root=root, maximum=_MAX_SNAPSHOT_BYTES)
    if (
        type(request["snapshot_bytes"]) is not int
        or len(snapshot) != int(request["snapshot_bytes"])
        or hashlib.sha256(snapshot).hexdigest() != str(request["snapshot_sha256"])
    ):
        _fail("NATIVE_DRAWING_REDRAW_SNAPSHOT_CHANGED", "The detached Drawing snapshot changed before redraw.")
    preferences = App.ParamGet("User parameter:BaseApp/Preferences/Mod/TechDraw/General")
    preferences.SetBool("GlobalUpdateDrawings", True)
    preferences.SetBool("AllowPageOverride", True)
    # The snapshot and its authored References2D/References3D are the exact
    # inputs authorized by the live preflight.  Detached redraw must evaluate
    # those references, not silently rewrite them through TechDraw's
    # interactive reference-repair preference.  Invalid exact references still
    # fail during dimension execution because they cannot produce descriptive
    # geometry.
    dimension_preferences = App.ParamGet(
        "User parameter:BaseApp/Preferences/Mod/TechDraw/Dimensions"
    )
    dimension_preferences.SetBool("AutoCorrectRefs", False)
    try:
        os.mkdir(root / "outputs", 0o700)
    except OSError as exc:
        raise _ChildFailure(
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
            "The detached redraw output directory could not be created.",
        ) from exc
    document = App.openDocument(str(snapshot_path))
    try:
        page = document.getObject(str(request["page_name"]))
        if page is None or not page.isDerivedFrom("TechDraw::DrawPage"):
            _fail("NATIVE_DRAWING_REDRAW_PAGE_INVALID", "The exact Drawing page is missing from its snapshot.")
        getter = getattr(page, "getAllActiveViews", None)
        page_views = tuple(getter() or ()) if callable(getter) else tuple(page.Views or ())
        views = []
        names = set()
        for view in page_views:
            name = str(getattr(view, "Name", "") or "")
            object_id = int(getattr(view, "ID", -1))
            current = document.getObject(name) if name else None
            if (
                current is None
                or int(getattr(current, "ID", -2)) != object_id
                or name in names
            ):
                _fail(
                    "NATIVE_DRAWING_REDRAW_VIEW_GRAPH_CHANGED",
                    "The Drawing page contains an invalid or duplicate view identity in its snapshot.",
                )
            names.add(name)
            views.append(current)
        views = tuple(views)
        if tuple(view.Name for view in views) != tuple(item["object_name"] for item in descriptors):
            _fail("NATIVE_DRAWING_REDRAW_VIEW_GRAPH_CHANGED", "The Drawing page view graph changed in its snapshot.")
        if any(str(view.TypeId) != item["type_id"] for view, item in zip(views, descriptors, strict=True)):
            _fail("NATIVE_DRAWING_REDRAW_VIEW_GRAPH_CHANGED", "A Drawing page view type changed in its snapshot.")
        original_keep_updated = bool(page.KeepUpdated)
        page.KeepUpdated = True
        projection_views = tuple(
            view
            for view, descriptor in zip(views, descriptors, strict=True)
            if descriptor["kind"] == "projection"
        )
        dimension_views = tuple(
            view
            for view, descriptor in zip(views, descriptors, strict=True)
            if descriptor["kind"] == "dimension"
        )
        dependent_views = tuple(
            view
            for view, descriptor in zip(views, descriptors, strict=True)
            if descriptor["kind"] == "dependent"
        )

        def recompute_phase(phase_views: tuple[Any, ...], label: str) -> None:
            if not phase_views:
                return
            for phase_view in phase_views:
                phase_view.touch()
            if document.recompute(list(phase_views), True, True) is False:
                _fail(
                    "NATIVE_DRAWING_REDRAW_EXECUTION_FAILED",
                    f"TechDraw rejected the detached {label} redraw phase.",
                )

        recompute_phase(projection_views, "projection")
        for projection_view in projection_views:
            try:
                projection_view.getPrecomputedProjection()
            except Exception as exc:
                raise _ChildFailure(
                    "NATIVE_DRAWING_REDRAW_PROJECTION_FAILED",
                    f"Drawing projection {projection_view.Name!r} did not finish before dependent views.",
                ) from exc
        recompute_phase(dimension_views, "dimension")
        recompute_phase(dependent_views, "dependent view")
        page.KeepUpdated = original_keep_updated
        projections = []
        dimensions = []
        dependents = []
        projection_index = 0
        for view, descriptor in zip(views, descriptors, strict=True):
            if not bool(view.isValid()) or {"Invalid", "Error"} & set(view.State or ()):
                _fail(
                    "NATIVE_DRAWING_REDRAW_EXECUTION_FAILED",
                    f"Drawing view {view.Name!r} is invalid after redraw.",
                )
            if descriptor["kind"] == "projection":
                projections.append(_projection(view, descriptor, root, projection_index))
                projection_index += 1
            elif descriptor["kind"] == "dimension":
                dimensions.append(_dimension(view, descriptor))
            else:
                dependents.append(
                    {
                        "object_name": descriptor["object_name"],
                        "type_id": descriptor["type_id"],
                    }
                )
        return {
            "ok": True,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "page_name": str(page.Name),
            "view_names": [item["object_name"] for item in descriptors],
            "projections": projections,
            "dimensions": dimensions,
            "dependents": dependents,
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
            "error_code": "NATIVE_DRAWING_REDRAW_EXECUTION_FAILED",
            "message": "The isolated Drawing redraw process failed.",
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
