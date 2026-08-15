# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable exact-document TechDraw redraw worker and result protocol."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import math
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Mapping

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import validate_frozen_file
from VibeCADNativeDrawingProjectionWorker import (
    PreparedDrawingProjection,
    prepared_projection_from_descriptor,
)
from VibeCADNativeDrawingRedrawInput import (
    DRAWING_REDRAW_PROTOCOL,
    MAX_REDRAW_REQUEST_BYTES,
    MAX_REDRAW_SNAPSHOT_BYTES,
    FrozenDrawingRedraw,
)
from VibeCADScriptedProcess import run_process


MAX_REDRAW_RESULT_BYTES = 16 * 1024 * 1024
REDRAW_TIMEOUT_SECONDS = 900.0
REDRAW_MEMORY_LIMIT_BYTES = 3 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class RedrawnProjection:
    object_name: str
    type_id: str
    projection: PreparedDrawingProjection = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class RedrawnDimension:
    object_name: str
    type_id: str
    vectors: tuple[tuple[float, float, float], ...]
    scalars: tuple[float, ...]
    flags: tuple[bool, ...]


@dataclass(frozen=True, slots=True)
class PreparedDrawingRedraw:
    frozen: FrozenDrawingRedraw = field(repr=False, compare=False)
    page_name: str
    view_names: tuple[str, ...]
    projections: tuple[RedrawnProjection, ...]
    dimensions: tuple[RedrawnDimension, ...]
    dependents: tuple[tuple[str, str], ...]


def _error(message: str, code: str) -> None:
    raise NativeDrawingError(message, error_code=code)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _error(
            "A detached Drawing redraw result escaped its private workspace.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeDrawingError(
            "The detached Drawing redraw returned no result.",
            error_code="NATIVE_DRAWING_REDRAW_EXECUTION_FAILED",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _error(
            "The detached Drawing redraw result is not a regular file.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
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
                "The detached Drawing redraw result changed while opening.",
                "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _error(
                    "The detached Drawing redraw result exceeds its safety bound.",
                    "NATIVE_DRAWING_REDRAW_LIMIT",
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _error(
            "The detached Drawing redraw result is empty.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    return bytes(data)


def _environment(frozen: FrozenDrawingRedraw) -> dict[str, str]:
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
            "VIBECAD_NATIVE_DRAWING_REDRAW_REQUEST": str(frozen.request.path),
            "VIBECAD_NATIVE_DRAWING_REDRAW_CHILD": str(frozen.workspace.child.path),
        }
    )
    if sys.platform == "win32":
        drive, tail = os.path.splitdrive(root)
        environment["USERPROFILE"] = root
        if drive:
            environment["HOMEDRIVE"] = drive
            environment["HOMEPATH"] = tail or "\\"
    return environment


def _validate_inputs(frozen: FrozenDrawingRedraw) -> None:
    validate_frozen_file(frozen.workspace.freecadcmd, maximum=None, executable=True)
    validate_frozen_file(frozen.workspace.child, maximum=1024 * 1024)
    validate_frozen_file(frozen.request, maximum=MAX_REDRAW_REQUEST_BYTES)
    validate_frozen_file(frozen.snapshot, maximum=MAX_REDRAW_SNAPSHOT_BYTES)


def _dimension(value: Any) -> RedrawnDimension:
    required = {"object_name", "type_id", "vectors", "scalars", "flags"}
    if not isinstance(value, Mapping) or set(value) != required:
        _error(
            "A detached Drawing dimension result is malformed.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    raw_vectors = value["vectors"]
    raw_scalars = value["scalars"]
    raw_flags = value["flags"]
    if (
        not isinstance(raw_vectors, list)
        or not 1 <= len(raw_vectors) <= 32
        or not isinstance(raw_scalars, list)
        or len(raw_scalars) > 32
        or not isinstance(raw_flags, list)
        or not 1 <= len(raw_flags) <= 32
        or any(
            not isinstance(vector, list)
            or len(vector) != 3
            or any(type(item) not in {int, float} for item in vector)
            for vector in raw_vectors
        )
        or any(type(item) not in {int, float} for item in raw_scalars)
        or any(type(item) is not bool for item in raw_flags)
    ):
        _error(
            "A detached Drawing dimension result has invalid geometry.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    vectors = tuple(tuple(float(item) for item in vector) for vector in raw_vectors)
    scalars = tuple(float(item) for item in raw_scalars)
    if any(not math.isfinite(item) for vector in vectors for item in vector) or any(
        not math.isfinite(item) for item in scalars
    ):
        _error(
            "A detached Drawing dimension result is not finite.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    return RedrawnDimension(
        object_name=str(value["object_name"] or ""),
        type_id=str(value["type_id"] or ""),
        vectors=vectors,
        scalars=scalars,
        flags=tuple(bool(item) for item in raw_flags),
    )


def _pairs(value: Any, field_name: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list) or len(value) > 128:
        _error(
            f"Detached Drawing redraw {field_name} is malformed.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    result = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) != {"object_name", "type_id"}:
            _error(
                f"Detached Drawing redraw {field_name} is malformed.",
                "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
            )
        result.append((str(item["object_name"] or ""), str(item["type_id"] or "")))
    return tuple(result)


def _read_result(frozen: FrozenDrawingRedraw) -> PreparedDrawingRedraw:
    data = _read_regular(
        frozen.workspace.path / "result.json",
        root=frozen.workspace.path,
        maximum=MAX_REDRAW_RESULT_BYTES,
    )
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeDrawingError(
            "The detached Drawing redraw result is unreadable.",
            error_code="NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        ) from exc
    if not isinstance(value, Mapping):
        _error(
            "The detached Drawing redraw result is malformed.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    if value.get("ok") is False:
        code = str(value.get("error_code") or "")
        message = str(value.get("message") or "")[:320]
        if not code.startswith("NATIVE_DRAWING_REDRAW_") or not message:
            _error(
                "The detached Drawing redraw failed.",
                "NATIVE_DRAWING_REDRAW_EXECUTION_FAILED",
            )
        raise NativeDrawingError(message, error_code=code)
    required = {
        "ok",
        "protocol",
        "request_sha256",
        "page_name",
        "view_names",
        "projections",
        "dimensions",
        "dependents",
    }
    if (
        set(value) != required
        or value.get("ok") is not True
        or str(value.get("protocol")) != DRAWING_REDRAW_PROTOCOL
        or str(value.get("request_sha256")) != frozen.request_sha256
        or str(value.get("page_name")) != frozen.page_name
        or not isinstance(value.get("view_names"), list)
        or tuple(str(item) for item in value["view_names"]) != frozen.view_names
        or not isinstance(value.get("projections"), list)
        or not isinstance(value.get("dimensions"), list)
    ):
        _error(
            "The detached Drawing redraw result failed protocol validation.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    projections = tuple(
        RedrawnProjection(
            object_name=str(item.get("object_name") or ""),
            type_id=str(item.get("type_id") or ""),
            projection=prepared_projection_from_descriptor(
                item.get("projection"),
                root=frozen.workspace.path,
                index=index,
            ),
        )
        for index, item in enumerate(value["projections"])
        if isinstance(item, Mapping)
        and set(item) == {"object_name", "type_id", "projection"}
    )
    if len(projections) != len(value["projections"]):
        _error(
            "A detached Drawing projection result is malformed.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    dimensions = tuple(_dimension(item) for item in value["dimensions"])
    dependents = _pairs(value["dependents"], "dependents")
    classified_names = tuple(
        [item.object_name for item in projections]
        + [item.object_name for item in dimensions]
        + [item[0] for item in dependents]
    )
    if sorted(classified_names) != sorted(frozen.view_names):
        _error(
            "The detached Drawing redraw did not classify every exact view once.",
            "NATIVE_DRAWING_REDRAW_OUTPUT_INVALID",
        )
    return PreparedDrawingRedraw(
        frozen=frozen,
        page_name=frozen.page_name,
        view_names=frozen.view_names,
        projections=projections,
        dimensions=dimensions,
        dependents=dependents,
    )


def execute_page_redraw(
    frozen: FrozenDrawingRedraw,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedDrawingRedraw:
    if not isinstance(frozen, FrozenDrawingRedraw):
        raise TypeError("frozen must be a FrozenDrawingRedraw")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(15, "Authenticating exact Drawing snapshot")
    _validate_inputs(frozen)
    progress(25, "Redrawing exact page outside the UI process")
    code = (
        "import os,runpy;"
        "runpy.run_path(os.environ['VIBECAD_NATIVE_DRAWING_REDRAW_CHILD'],"
        "run_name='__main__')"
    )
    process = run_process(
        [str(frozen.workspace.freecadcmd.path), "--safe-mode", "-c", code],
        cwd=frozen.workspace.path,
        environment=_environment(frozen),
        cancellation_check=cancelled,
        timeout_seconds=REDRAW_TIMEOUT_SECONDS,
        memory_limit_bytes=REDRAW_MEMORY_LIMIT_BYTES,
    )
    if bool(process.get("cancelled")):
        raise NativeBackgroundCancelled()
    if not bool(process.get("started")):
        _error(
            "The isolated Drawing redraw process could not start.",
            "NATIVE_DRAWING_REDRAW_EXECUTION_FAILED",
        )
    if bool(process.get("timed_out")):
        _error(
            "Drawing page redraw exceeded its fifteen-minute safety limit.",
            "NATIVE_DRAWING_REDRAW_LIMIT",
        )
    if bool(process.get("memory_exceeded")):
        _error(
            "Drawing page redraw exceeded its 3 GiB memory safety limit.",
            "NATIVE_DRAWING_REDRAW_LIMIT",
        )
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(84, "Authenticating redrawn page geometry")
    prepared = _read_result(frozen)
    if int(process.get("returncode", 1)) != 0:
        _error(
            "The isolated Drawing redraw process exited unsuccessfully.",
            "NATIVE_DRAWING_REDRAW_EXECUTION_FAILED",
        )
    progress(89, "Prepared exact page redraw")
    return prepared
