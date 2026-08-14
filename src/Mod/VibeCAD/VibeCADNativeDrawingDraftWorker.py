# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable offscreen Draft renderer and authenticated SVG adoption."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
from typing import Any, Callable, Mapping
import xml.etree.ElementTree as ET

from VibeCADNativeBackground import NativeBackgroundCancelled
from VibeCADNativeDrawingDraftInput import (
    DRAWING_DRAFT_PROTOCOL,
    MAX_DRAFT_REQUEST_BYTES,
    MAX_DRAFT_SNAPSHOT_BYTES,
    FrozenDrawingDraft,
)
from VibeCADNativeDrawingDraftState import MAX_DRAFT_SYMBOL_BYTES
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import validate_frozen_file
from VibeCADScriptedProcess import run_process


MAX_DRAFT_RESULT_BYTES = 1024 * 1024
MAX_DRAFT_SVG_ELEMENTS = 200_000
DRAFT_TIMEOUT_SECONDS = 600.0
DRAFT_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024
_DRAWABLES = {
    "circle",
    "ellipse",
    "image",
    "line",
    "path",
    "polygon",
    "polyline",
    "rect",
    "text",
}
_FORBIDDEN = {"embed", "foreignObject", "iframe", "object", "script"}


@dataclass(frozen=True, slots=True)
class DraftSymbolArtifact:
    path: Path = field(repr=False, compare=False)
    size_bytes: int
    sha256: str
    element_count: int
    drawable_count: int


@dataclass(frozen=True, slots=True)
class PreparedDrawingDraft:
    frozen: FrozenDrawingDraft = field(repr=False, compare=False)
    symbol: DraftSymbolArtifact


def _error(message: str, code: str) -> None:
    raise NativeDrawingError(message, error_code=code)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _error(
            "A detached Draft artifact escaped its private workspace.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        )
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeDrawingError(
            "A detached Draft artifact is unavailable.",
            error_code="NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _error(
            "A detached Draft artifact is not a regular file.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
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
                "A detached Draft artifact changed while opening.",
                "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _error(
                    "A detached Draft artifact exceeds its safety bound.",
                    "NATIVE_DRAWING_DRAFT_LIMIT",
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _error(
            "A detached Draft artifact is empty.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        )
    return bytes(data)


def _environment(frozen: FrozenDrawingDraft) -> dict[str, str]:
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
            "LIBGL_ALWAYS_SOFTWARE": "1",
            "PYTHONHASHSEED": "0",
            "PYTHONNOUSERSITE": "1",
            "QT_OPENGL": "software",
            "QT_QPA_PLATFORM": "offscreen",
            "TEMP": root,
            "TMP": root,
            "TMPDIR": root,
            "VIBECAD_NATIVE_DRAWING_DRAFT_REQUEST": str(frozen.request.path),
            "XDG_CACHE_HOME": root,
            "XDG_CONFIG_HOME": root,
            "XDG_DATA_HOME": root,
            "XDG_RUNTIME_DIR": root,
        }
    )
    if sys.platform == "win32":
        drive, tail = os.path.splitdrive(root)
        environment["USERPROFILE"] = root
        if drive:
            environment["HOMEDRIVE"] = drive
            environment["HOMEPATH"] = tail or "\\"
    return environment


def _validate_inputs(frozen: FrozenDrawingDraft) -> None:
    validate_frozen_file(frozen.workspace.freecadcmd, maximum=None, executable=True)
    validate_frozen_file(frozen.workspace.child, maximum=1024 * 1024)
    validate_frozen_file(frozen.request, maximum=MAX_DRAFT_REQUEST_BYTES)
    validate_frozen_file(frozen.snapshot, maximum=MAX_DRAFT_SNAPSHOT_BYTES)


def _local_name(value: str) -> str:
    return value.rsplit("}", 1)[-1]


def _validate_symbol(data: bytes) -> tuple[int, int]:
    upper = data.upper()
    if b"<!DOCTYPE" in upper or b"<!ENTITY" in upper:
        _error(
            "The detached Draft SVG contains declarations.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        )
    try:
        root = ET.fromstring(data)
    except ET.ParseError as exc:
        raise NativeDrawingError(
            "The detached Draft SVG is not valid XML.",
            error_code="NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        ) from exc
    if _local_name(root.tag) != "svg":
        _error(
            "The detached Draft artifact is not an SVG document.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        )
    count = 0
    drawables = 0
    for element in root.iter():
        count += 1
        if count > MAX_DRAFT_SVG_ELEMENTS:
            _error(
                "The detached Draft SVG has too many elements.",
                "NATIVE_DRAWING_DRAFT_LIMIT",
            )
        name = _local_name(element.tag)
        if name in _FORBIDDEN:
            _error(
                "The detached Draft SVG contains active content.",
                "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
            )
        drawables += int(name in _DRAWABLES)
        for attribute, raw in element.attrib.items():
            value = str(raw).strip()
            attr = _local_name(attribute).lower()
            if attr == "href" and value and not (
                value.startswith("#") or value.startswith("data:image/")
            ):
                _error(
                    "The detached Draft SVG references an external resource.",
                    "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
                )
            lowered = value.lower().replace(" ", "")
            if "url(" in lowered and "url(#" not in lowered:
                _error(
                    "The detached Draft SVG contains an external URL.",
                    "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
                )
    if drawables < 1:
        _error(
            "The exact Draft source produced no drawable SVG geometry.",
            "NATIVE_DRAWING_DRAFT_RENDER_FAILED",
        )
    return count, drawables


def _artifact(value: Any, *, root: Path) -> DraftSymbolArtifact:
    required = {
        "artifact",
        "artifact_bytes",
        "artifact_sha256",
        "element_count",
        "drawable_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _error(
            "The detached Draft SVG descriptor is malformed.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        )
    relative = Path(str(value["artifact"] or ""))
    if relative != Path("outputs/draft-view.svg"):
        _error(
            "The detached Draft SVG has an unexpected identity.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        )
    data = _read_regular(root / relative, root=root, maximum=MAX_DRAFT_SYMBOL_BYTES)
    size = value["artifact_bytes"]
    digest = str(value["artifact_sha256"] or "")
    element_count = value["element_count"]
    drawable_count = value["drawable_count"]
    counted_elements, counted_drawables = _validate_symbol(data)
    if (
        type(size) is not int
        or size != len(data)
        or len(digest) != 64
        or digest != hashlib.sha256(data).hexdigest()
        or type(element_count) is not int
        or type(drawable_count) is not int
        or element_count != counted_elements
        or drawable_count != counted_drawables
    ):
        _error(
            "The detached Draft SVG failed authentication.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        )
    return DraftSymbolArtifact(
        path=root / relative,
        size_bytes=size,
        sha256=digest,
        element_count=element_count,
        drawable_count=drawable_count,
    )


def _read_result(frozen: FrozenDrawingDraft) -> PreparedDrawingDraft:
    data = _read_regular(
        frozen.workspace.path / "result.json",
        root=frozen.workspace.path,
        maximum=MAX_DRAFT_RESULT_BYTES,
    )
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeDrawingError(
            "The detached Draft result is unreadable.",
            error_code="NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        ) from exc
    if not isinstance(value, Mapping):
        _error(
            "The detached Draft result is malformed.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        )
    if value.get("ok") is False:
        code = str(value.get("error_code") or "")
        message = str(value.get("message") or "")[:320]
        if not code.startswith("NATIVE_DRAWING_DRAFT_") or not message:
            _error(
                "The detached Draft process failed.",
                "NATIVE_DRAWING_DRAFT_EXECUTION_FAILED",
            )
        raise NativeDrawingError(message, error_code=code)
    required = {
        "ok",
        "protocol",
        "request_sha256",
        "page_name",
        "source_name",
        "source_state_sha256",
        "symbol",
    }
    if (
        set(value) != required
        or value.get("ok") is not True
        or str(value.get("protocol")) != DRAWING_DRAFT_PROTOCOL
        or str(value.get("request_sha256")) != frozen.request_sha256
        or str(value.get("page_name")) != frozen.page_name
        or str(value.get("source_name")) != frozen.source_name
        or str(value.get("source_state_sha256")) != frozen.source_state_sha256
    ):
        _error(
            "The detached Draft result failed protocol validation.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        )
    return PreparedDrawingDraft(
        frozen=frozen,
        symbol=_artifact(value["symbol"], root=frozen.workspace.path),
    )


def execute_draft_render(
    frozen: FrozenDrawingDraft,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedDrawingDraft:
    if not isinstance(frozen, FrozenDrawingDraft):
        raise TypeError("frozen must be a FrozenDrawingDraft")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(5, "Authenticating exact Draft rendering inputs")
    _validate_inputs(frozen)
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(12, "Rendering the Draft source outside the UI process")
    process = run_process(
        [
            str(frozen.workspace.freecadcmd.path),
            "--safe-mode",
            f"./{frozen.workspace.child.path.name}",
        ],
        cwd=frozen.workspace.path,
        environment=_environment(frozen),
        cancellation_check=cancelled,
        timeout_seconds=DRAFT_TIMEOUT_SECONDS,
        memory_limit_bytes=DRAFT_MEMORY_LIMIT_BYTES,
    )
    if bool(process.get("cancelled")):
        raise NativeBackgroundCancelled()
    if not bool(process.get("started")):
        _error(
            "The isolated Draft rendering process could not start.",
            "NATIVE_DRAWING_DRAFT_EXECUTION_FAILED",
        )
    if bool(process.get("timed_out")):
        _error(
            "Draft rendering exceeded its ten-minute safety limit.",
            "NATIVE_DRAWING_DRAFT_LIMIT",
        )
    if bool(process.get("memory_exceeded")):
        _error(
            "Draft rendering exceeded its 2 GiB memory safety limit.",
            "NATIVE_DRAWING_DRAFT_LIMIT",
        )
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(82, "Authenticating rendered Draft SVG")
    prepared = _read_result(frozen)
    if int(process.get("returncode", 1)) != 0:
        _error(
            "The isolated Draft rendering process exited unsuccessfully.",
            "NATIVE_DRAWING_DRAFT_EXECUTION_FAILED",
        )
    progress(89, "Prepared exact Draft drawing view")
    return prepared


def draft_symbol(prepared: PreparedDrawingDraft) -> str:
    if not isinstance(prepared, PreparedDrawingDraft):
        raise TypeError("prepared must be a PreparedDrawingDraft")
    data = _read_regular(
        prepared.symbol.path,
        root=prepared.symbol.path.parents[1],
        maximum=MAX_DRAFT_SYMBOL_BYTES,
    )
    if (
        len(data) != prepared.symbol.size_bytes
        or hashlib.sha256(data).hexdigest() != prepared.symbol.sha256
        or _validate_symbol(data)
        != (prepared.symbol.element_count, prepared.symbol.drawable_count)
    ):
        _error(
            "The prepared Draft SVG changed before document adoption.",
            "NATIVE_DRAWING_DRAFT_OUTPUT_CHANGED",
        )
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise NativeDrawingError(
            "The prepared Draft SVG is not UTF-8.",
            error_code="NATIVE_DRAWING_DRAFT_OUTPUT_INVALID",
        ) from exc


__all__ = [
    "PreparedDrawingDraft",
    "draft_symbol",
    "execute_draft_render",
]
