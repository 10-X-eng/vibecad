# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable detail process and authenticated result adoption."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Mapping

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeDrawingDetailInput import (
    DRAWING_DETAIL_PROTOCOL,
    MAX_DETAIL_REQUEST_BYTES,
    MAX_DETAIL_SNAPSHOT_BYTES,
    FrozenDrawingDetail,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import validate_frozen_file
from VibeCADNativeDrawingProjectionWorker import (
    PreparedDrawingProjection,
    prepared_projection_from_descriptor,
)
from VibeCADScriptedProcess import run_process


MAX_DETAIL_RESULT_BYTES = 16 * 1024 * 1024
MAX_DETAIL_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_DETAIL_SOLIDS = 10_000
MAX_DETAIL_FACES = 50_000
MAX_DETAIL_EDGES = 200_000
DETAIL_TIMEOUT_SECONDS = 600.0
DETAIL_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class DetailArtifact:
    path: Path = field(repr=False, compare=False)
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PreparedDetailGeometry:
    detail_shape: DetailArtifact = field(repr=False, compare=False)
    solid_count: int
    face_count: int
    edge_count: int


@dataclass(frozen=True, slots=True)
class PreparedDrawingDetail:
    frozen: FrozenDrawingDetail = field(repr=False, compare=False)
    projection: PreparedDrawingProjection
    detail: PreparedDetailGeometry
    effective_scale: float


def _error(message: str, code: str) -> None:
    raise NativeDrawingError(message, error_code=code)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _error(
            "A detached detail artifact escaped its private workspace.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeDrawingError(
            "A detached detail artifact is unavailable.",
            error_code="NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _error(
            "A detached detail artifact is not a regular file.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
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
            _error(
                "A detached detail artifact changed while opening.",
                "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _error(
                    "A detached detail artifact exceeds its safety bound.",
                    "NATIVE_DRAWING_DETAIL_LIMIT",
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _error(
            "A detached detail artifact is empty.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    return bytes(data)


def _environment(frozen: FrozenDrawingDetail) -> dict[str, str]:
    root = str(frozen.workspace.path)
    preserved = (
        "COMSPEC",
        "LANG",
        "LC_ALL",
        "LD_LIBRARY_PATH",
        "PATH",
        "PATHEXT",
        "SystemRoot",
        "WINDIR",
    )
    environment = {
        name: os.environ[name]
        for name in preserved
        if str(os.environ.get(name) or "").strip()
    }
    environment.update(
        {
            "HOME": root,
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "TEMP": root,
            "TMP": root,
            "TMPDIR": root,
            "VIBECAD_NATIVE_DRAWING_DETAIL_REQUEST": str(frozen.request.path),
            "VIBECAD_NATIVE_DRAWING_DETAIL_CHILD": str(frozen.workspace.child.path),
        }
    )
    if sys.platform == "win32":
        drive, tail = os.path.splitdrive(root)
        environment["USERPROFILE"] = root
        if drive:
            environment["HOMEDRIVE"] = drive
            environment["HOMEPATH"] = tail or "\\"
    return environment


def _validate_inputs(frozen: FrozenDrawingDetail) -> None:
    validate_frozen_file(frozen.workspace.freecadcmd, maximum=None, executable=True)
    validate_frozen_file(frozen.workspace.child, maximum=1024 * 1024)
    validate_frozen_file(frozen.request, maximum=MAX_DETAIL_REQUEST_BYTES)
    validate_frozen_file(frozen.snapshot, maximum=MAX_DETAIL_SNAPSHOT_BYTES)


def _artifact(value: Any, *, root: Path, expected: str) -> DetailArtifact:
    required = {"artifact", "artifact_bytes", "artifact_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        _error(
            "A detached detail artifact descriptor is malformed.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    relative = Path(str(value["artifact"] or ""))
    if relative != Path(expected):
        _error(
            "A detached detail artifact has an unexpected identity.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    data = _read_regular(
        root / relative,
        root=root,
        maximum=MAX_DETAIL_ARTIFACT_BYTES,
    )
    size = value["artifact_bytes"]
    digest = str(value["artifact_sha256"] or "")
    if (
        type(size) is not int
        or size != len(data)
        or len(digest) != 64
        or digest != hashlib.sha256(data).hexdigest()
    ):
        _error(
            "A detached detail artifact failed authentication.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    return DetailArtifact(root / relative, size, digest)


def _detail(value: Any, root: Path) -> PreparedDetailGeometry:
    required = {"detail_shape", "solid_count", "face_count", "edge_count"}
    if not isinstance(value, Mapping) or set(value) != required:
        _error(
            "Detached detail geometry is malformed.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    counts = tuple(value[name] for name in ("solid_count", "face_count", "edge_count"))
    if any(type(item) is not int for item in counts):
        _error(
            "Detached detail topology counts are malformed.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    solids, faces, edges = counts
    if (
        not 0 <= solids <= MAX_DETAIL_SOLIDS
        or not 0 <= faces <= MAX_DETAIL_FACES
        or not 1 <= edges <= MAX_DETAIL_EDGES
    ):
        _error(
            "Detached detail topology counts are invalid.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    return PreparedDetailGeometry(
        detail_shape=_artifact(
            value["detail_shape"],
            root=root,
            expected="outputs/detail-shape.brep",
        ),
        solid_count=solids,
        face_count=faces,
        edge_count=edges,
    )


def _read_result(frozen: FrozenDrawingDetail) -> PreparedDrawingDetail:
    data = _read_regular(
        frozen.workspace.path / "result.json",
        root=frozen.workspace.path,
        maximum=MAX_DETAIL_RESULT_BYTES,
    )
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeDrawingError(
            "The detached detail result is unreadable.",
            error_code="NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        ) from exc
    if not isinstance(value, Mapping):
        _error(
            "The detached detail result is malformed.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    if value.get("ok") is False:
        code = str(value.get("error_code") or "")
        message = str(value.get("message") or "")[:320]
        if not code.startswith("NATIVE_DRAWING_DETAIL_") or not message:
            _error(
                "The detached detail process failed.",
                "NATIVE_DRAWING_DETAIL_EXECUTION_FAILED",
            )
        raise NativeDrawingError(message, error_code=code)
    required = {
        "ok",
        "protocol",
        "request_sha256",
        "page_name",
        "base_name",
        "source_names",
        "effective_scale",
        "projection",
        "detail",
    }
    scale = value.get("effective_scale")
    if (
        set(value) != required
        or value.get("ok") is not True
        or str(value.get("protocol")) != DRAWING_DETAIL_PROTOCOL
        or str(value.get("request_sha256")) != frozen.request_sha256
        or str(value.get("page_name")) != frozen.page_name
        or str(value.get("base_name")) != frozen.base_name
        or tuple(value.get("source_names") or ()) != frozen.source_names
        or type(scale) not in {int, float}
        or not math.isfinite(float(scale))
        or not 1.0e-12 <= float(scale) <= 1_000.0
    ):
        _error(
            "The detached detail result failed protocol validation.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    projection = prepared_projection_from_descriptor(
        value["projection"],
        root=frozen.workspace.path,
        index=0,
    )
    if projection.key != "detail_view":
        _error(
            "The detached detail returned the wrong projection identity.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    return PreparedDrawingDetail(
        frozen=frozen,
        projection=projection,
        detail=_detail(value["detail"], frozen.workspace.path),
        effective_scale=float(scale),
    )


def execute_detail_projection(
    frozen: FrozenDrawingDetail,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedDrawingDetail:
    if not isinstance(frozen, FrozenDrawingDetail):
        raise TypeError("frozen must be a FrozenDrawingDetail")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(5, "Authenticating exact Drawing detail inputs")
    _validate_inputs(frozen)
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(12, "Computing the detail outside the UI process")
    code = (
        "import os,runpy;"
        "runpy.run_path(os.environ['VIBECAD_NATIVE_DRAWING_DETAIL_CHILD'],"
        "run_name='__main__')"
    )
    process = run_process(
        [str(frozen.workspace.freecadcmd.path), "--safe-mode", "-c", code],
        cwd=frozen.workspace.path,
        environment=_environment(frozen),
        cancellation_check=cancelled,
        timeout_seconds=DETAIL_TIMEOUT_SECONDS,
        memory_limit_bytes=DETAIL_MEMORY_LIMIT_BYTES,
    )
    if bool(process.get("cancelled")):
        raise NativeBackgroundCancelled()
    if not bool(process.get("started")):
        _error(
            "The isolated detail process could not start.",
            "NATIVE_DRAWING_DETAIL_EXECUTION_FAILED",
        )
    if bool(process.get("timed_out")):
        _error(
            "Detail computation exceeded its ten-minute safety limit.",
            "NATIVE_DRAWING_DETAIL_LIMIT",
        )
    if bool(process.get("memory_exceeded")):
        _error(
            "Detail computation exceeded its 2 GiB memory safety limit.",
            "NATIVE_DRAWING_DETAIL_LIMIT",
        )
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(82, "Authenticating projected and clipped detail geometry")
    prepared = _read_result(frozen)
    if int(process.get("returncode", 1)) != 0:
        _error(
            "The isolated detail process exited unsuccessfully.",
            "NATIVE_DRAWING_DETAIL_EXECUTION_FAILED",
        )
    progress(89, "Prepared exact Drawing detail")
    return prepared


def detail_snapshot(prepared: PreparedDetailGeometry) -> dict[str, Any]:
    if not isinstance(prepared, PreparedDetailGeometry):
        raise TypeError("prepared must be a PreparedDetailGeometry")
    import Part

    data = _read_regular(
        prepared.detail_shape.path,
        root=prepared.detail_shape.path.parents[1],
        maximum=MAX_DETAIL_ARTIFACT_BYTES,
    )
    if (
        len(data) != prepared.detail_shape.size_bytes
        or hashlib.sha256(data).hexdigest() != prepared.detail_shape.sha256
    ):
        _error(
            "A prepared detail artifact changed before document adoption.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_CHANGED",
        )
    shape = Part.Shape()
    try:
        shape.importBrep(str(prepared.detail_shape.path))
    except Exception as exc:
        raise NativeDrawingError(
            "A prepared detail artifact could not be imported.",
            error_code="NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        ) from exc
    if (
        len(tuple(shape.Solids)) != prepared.solid_count
        or len(tuple(shape.Faces)) != prepared.face_count
        or len(tuple(shape.Edges)) != prepared.edge_count
        or not bool(shape.isValid())
    ):
        _error(
            "The prepared detail topology changed during import.",
            "NATIVE_DRAWING_DETAIL_OUTPUT_INVALID",
        )
    return {"detail_shape": shape}


__all__ = [
    "PreparedDrawingDetail",
    "detail_snapshot",
    "execute_detail_projection",
]
