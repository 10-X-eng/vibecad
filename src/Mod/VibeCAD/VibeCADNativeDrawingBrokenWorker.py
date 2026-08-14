# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable exact-document broken-view worker and result authentication."""

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
from VibeCADNativeDrawingBrokenInput import (
    DRAWING_BROKEN_PROTOCOL,
    MAX_BROKEN_REQUEST_BYTES,
    MAX_BROKEN_SNAPSHOT_BYTES,
    FrozenDrawingBroken,
)
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import validate_frozen_file
from VibeCADNativeDrawingProjectionWorker import (
    PreparedDrawingProjection,
    prepared_projection_from_descriptor,
)
from VibeCADScriptedProcess import run_process


MAX_BROKEN_RESULT_BYTES = 16 * 1024 * 1024
BROKEN_TIMEOUT_SECONDS = 900.0
BROKEN_MEMORY_LIMIT_BYTES = 3 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class PreparedBreakDefinition:
    object_name: str
    kind: str
    first: tuple[float, float, float]
    second: tuple[float, float, float]
    direction: tuple[float, float, float]
    removed_length_mm: float


@dataclass(frozen=True, slots=True)
class PreparedBrokenProjection:
    frozen: FrozenDrawingBroken = field(repr=False, compare=False)
    page_name: str
    projection: PreparedDrawingProjection = field(repr=False, compare=False)
    breaks: tuple[PreparedBreakDefinition, ...]
    broken_semantic_sha256: str
    control_semantic_sha256: str


def _error(message: str, code: str) -> None:
    raise NativeDrawingError(message, error_code=code)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _error(
            "A detached broken-view result escaped its private workspace.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeDrawingError(
            "The detached broken-view worker returned no result.",
            error_code="NATIVE_DRAWING_BROKEN_EXECUTION_FAILED",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _error(
            "The detached broken-view result is not a regular file.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
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
                "The detached broken-view result changed while opening.",
                "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _error(
                    "The detached broken-view result exceeds its safety bound.",
                    "NATIVE_DRAWING_BROKEN_LIMIT",
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _error(
            "The detached broken-view result is empty.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    return bytes(data)


def _environment(frozen: FrozenDrawingBroken) -> dict[str, str]:
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
            "VIBECAD_NATIVE_DRAWING_BROKEN_REQUEST": str(frozen.request.path),
            "VIBECAD_NATIVE_DRAWING_BROKEN_CHILD": str(frozen.workspace.child.path),
        }
    )
    if sys.platform == "win32":
        drive, tail = os.path.splitdrive(root)
        environment["USERPROFILE"] = root
        if drive:
            environment["HOMEDRIVE"] = drive
            environment["HOMEPATH"] = tail or "\\"
    return environment


def _validate_inputs(frozen: FrozenDrawingBroken) -> None:
    validate_frozen_file(frozen.workspace.freecadcmd, maximum=None, executable=True)
    validate_frozen_file(frozen.workspace.child, maximum=1024 * 1024)
    validate_frozen_file(frozen.request, maximum=MAX_BROKEN_REQUEST_BYTES)
    validate_frozen_file(frozen.snapshot, maximum=MAX_BROKEN_SNAPSHOT_BYTES)


def _vector(value: Any, field_name: str) -> tuple[float, float, float]:
    if (
        not isinstance(value, list)
        or len(value) != 3
        or any(type(item) not in {int, float} for item in value)
    ):
        _error(
            f"Detached broken-view {field_name} is malformed.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    result = tuple(float(item) for item in value)
    if any(not math.isfinite(item) for item in result):
        _error(
            f"Detached broken-view {field_name} is invalid.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    return result


def _break(value: Any) -> PreparedBreakDefinition:
    required = {
        "object_name",
        "kind",
        "first",
        "second",
        "direction",
        "removed_length_mm",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _error(
            "A detached break-definition result is malformed.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    first = _vector(value["first"], "first point")
    second = _vector(value["second"], "second point")
    direction = _vector(value["direction"], "direction")
    removed = value["removed_length_mm"]
    if (
        str(value["kind"]) not in {"single_edge", "two_line_sketch"}
        or type(removed) not in {int, float}
        or not math.isfinite(float(removed))
        or float(removed) <= 1.0e-9
    ):
        _error(
            "A detached break-definition result is invalid.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    displacement = tuple(right - left for left, right in zip(first, second, strict=True))
    length = sum(item * item for item in displacement) ** 0.5
    unit_length = sum(item * item for item in direction) ** 0.5
    alignment = sum(
        item / length * axis
        for item, axis in zip(displacement, direction, strict=True)
    )
    if (
        not math.isclose(length, float(removed), rel_tol=1.0e-8, abs_tol=1.0e-8)
        or not math.isclose(unit_length, 1.0, rel_tol=1.0e-8, abs_tol=1.0e-8)
        or not math.isclose(alignment, 1.0, rel_tol=1.0e-8, abs_tol=1.0e-8)
    ):
        _error(
            "A detached break-definition result is internally inconsistent.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    return PreparedBreakDefinition(
        object_name=str(value["object_name"] or ""),
        kind=str(value["kind"]),
        first=first,
        second=second,
        direction=direction,
        removed_length_mm=float(removed),
    )


def _read_result(frozen: FrozenDrawingBroken) -> PreparedBrokenProjection:
    data = _read_regular(
        frozen.workspace.path / "result.json",
        root=frozen.workspace.path,
        maximum=MAX_BROKEN_RESULT_BYTES,
    )
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeDrawingError(
            "The detached broken-view result is unreadable.",
            error_code="NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        ) from exc
    if not isinstance(value, Mapping):
        _error(
            "The detached broken-view result is malformed.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    if value.get("ok") is False:
        code = str(value.get("error_code") or "")
        message = str(value.get("message") or "")[:320]
        if (
            not message
            or not (
                code.startswith("NATIVE_DRAWING_BROKEN_")
                or code == "NATIVE_DRAWING_BREAK_NO_EFFECT"
            )
        ):
            _error(
                "The detached broken-view process failed.",
                "NATIVE_DRAWING_BROKEN_EXECUTION_FAILED",
            )
        raise NativeDrawingError(message, error_code=code)
    required = {
        "ok",
        "protocol",
        "request_sha256",
        "page_name",
        "projection",
        "breaks",
        "broken_semantic_sha256",
        "control_semantic_sha256",
    }
    if (
        set(value) != required
        or value.get("ok") is not True
        or str(value.get("protocol")) != DRAWING_BROKEN_PROTOCOL
        or str(value.get("request_sha256")) != frozen.request_sha256
        or str(value.get("page_name")) != frozen.page_name
        or not isinstance(value.get("breaks"), list)
    ):
        _error(
            "The detached broken-view result failed protocol validation.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    breaks = tuple(_break(item) for item in value["breaks"])
    broken_hash = str(value["broken_semantic_sha256"] or "")
    control_hash = str(value["control_semantic_sha256"] or "")
    if (
        tuple(item.object_name for item in breaks) != frozen.break_names
        or len(broken_hash) != 64
        or len(control_hash) != 64
        or broken_hash == control_hash
    ):
        _error(
            "The detached broken-view proof does not match its exact request.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    projection = prepared_projection_from_descriptor(
        value["projection"],
        root=frozen.workspace.path,
        index=0,
    )
    if projection.key != "broken_view":
        _error(
            "The detached broken-view projection identity is invalid.",
            "NATIVE_DRAWING_BROKEN_OUTPUT_INVALID",
        )
    return PreparedBrokenProjection(
        frozen=frozen,
        page_name=frozen.page_name,
        projection=projection,
        breaks=breaks,
        broken_semantic_sha256=broken_hash,
        control_semantic_sha256=control_hash,
    )


def execute_broken_projection(
    frozen: FrozenDrawingBroken,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedBrokenProjection:
    if not isinstance(frozen, FrozenDrawingBroken):
        raise TypeError("frozen must be a FrozenDrawingBroken")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(15, "Authenticating exact Drawing snapshot")
    _validate_inputs(frozen)
    progress(25, "Computing broken view outside the UI process")
    code = (
        "import os,runpy;"
        "runpy.run_path(os.environ['VIBECAD_NATIVE_DRAWING_BROKEN_CHILD'],"
        "run_name='__main__')"
    )
    process = run_process(
        [str(frozen.workspace.freecadcmd.path), "--safe-mode", "-c", code],
        cwd=frozen.workspace.path,
        environment=_environment(frozen),
        cancellation_check=cancelled,
        timeout_seconds=BROKEN_TIMEOUT_SECONDS,
        memory_limit_bytes=BROKEN_MEMORY_LIMIT_BYTES,
    )
    if bool(process.get("cancelled")):
        raise NativeBackgroundCancelled()
    if not bool(process.get("started")):
        _error(
            "The isolated broken-view process could not start.",
            "NATIVE_DRAWING_BROKEN_EXECUTION_FAILED",
        )
    if bool(process.get("timed_out")):
        _error(
            "Broken-view projection exceeded its fifteen-minute safety limit.",
            "NATIVE_DRAWING_BROKEN_LIMIT",
        )
    if bool(process.get("memory_exceeded")):
        _error(
            "Broken-view projection exceeded its 3 GiB memory safety limit.",
            "NATIVE_DRAWING_BROKEN_LIMIT",
        )
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(84, "Authenticating broken-view geometry")
    prepared = _read_result(frozen)
    if int(process.get("returncode", 1)) != 0:
        _error(
            "The isolated broken-view process exited unsuccessfully.",
            "NATIVE_DRAWING_BROKEN_EXECUTION_FAILED",
        )
    progress(89, "Prepared exact broken view")
    return prepared
