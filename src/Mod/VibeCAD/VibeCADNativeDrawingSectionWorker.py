# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable straight-section process and authenticated result adoption."""

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
from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingProjectionInput import validate_frozen_file
from VibeCADNativeDrawingProjectionWorker import (
    PreparedDrawingProjection,
    prepared_projection_from_descriptor,
    projection_snapshot,
)
from VibeCADNativeDrawingSectionInput import (
    DRAWING_SECTION_PROTOCOL,
    MAX_SECTION_REQUEST_BYTES,
    MAX_SECTION_SNAPSHOT_BYTES,
    FrozenDrawingSection,
)
from VibeCADScriptedProcess import run_process


MAX_SECTION_RESULT_BYTES = 16 * 1024 * 1024
MAX_SECTION_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_SECTION_CUT_SOLIDS = 10_000
MAX_SECTION_CUT_FACES = 50_000
MAX_SECTION_CUT_EDGES = 200_000
SECTION_TIMEOUT_SECONDS = 600.0
SECTION_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class SectionArtifact:
    path: Path = field(repr=False, compare=False)
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PreparedSectionGeometry:
    cut_pieces: SectionArtifact = field(repr=False, compare=False)
    section_faces: SectionArtifact = field(repr=False, compare=False)
    centroid: tuple[float, float, float]
    cut_solid_count: int
    cut_face_count: int
    cut_edge_count: int
    section_face_count: int


@dataclass(frozen=True, slots=True)
class PreparedDrawingSection:
    frozen: FrozenDrawingSection = field(repr=False, compare=False)
    projection: PreparedDrawingProjection
    section: PreparedSectionGeometry
    effective_scale: float


def _error(message: str, code: str) -> None:
    raise NativeDrawingError(message, error_code=code)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _error(
            "A detached section artifact escaped its private workspace.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeDrawingError(
            "A detached section artifact is unavailable.",
            error_code="NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _error(
            "A detached section artifact is not a regular file.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
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
                "A detached section artifact changed while opening.",
                "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _error(
                    "A detached section artifact exceeds its safety bound.",
                    "NATIVE_DRAWING_SECTION_LIMIT",
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _error(
            "A detached section artifact is empty.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    return bytes(data)


def _environment(frozen: FrozenDrawingSection) -> dict[str, str]:
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
            "VIBECAD_NATIVE_DRAWING_SECTION_REQUEST": str(frozen.request.path),
            "VIBECAD_NATIVE_DRAWING_SECTION_CHILD": str(frozen.workspace.child.path),
        }
    )
    if sys.platform == "win32":
        drive, tail = os.path.splitdrive(root)
        environment["USERPROFILE"] = root
        if drive:
            environment["HOMEDRIVE"] = drive
            environment["HOMEPATH"] = tail or "\\"
    return environment


def _validate_inputs(frozen: FrozenDrawingSection) -> None:
    validate_frozen_file(frozen.workspace.freecadcmd, maximum=None, executable=True)
    validate_frozen_file(frozen.workspace.child, maximum=1024 * 1024)
    validate_frozen_file(frozen.request, maximum=MAX_SECTION_REQUEST_BYTES)
    validate_frozen_file(frozen.snapshot, maximum=MAX_SECTION_SNAPSHOT_BYTES)


def _artifact(value: Any, *, root: Path, expected: str) -> SectionArtifact:
    required = {"artifact", "artifact_bytes", "artifact_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        _error(
            "A detached section artifact descriptor is malformed.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    relative = Path(str(value["artifact"] or ""))
    if relative != Path(expected):
        _error(
            "A detached section artifact has an unexpected identity.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    data = _read_regular(
        root / relative,
        root=root,
        maximum=MAX_SECTION_ARTIFACT_BYTES,
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
            "A detached section artifact failed authentication.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    return SectionArtifact(root / relative, size, digest)


def _section(value: Any, root: Path) -> PreparedSectionGeometry:
    required = {
        "cut_pieces",
        "section_faces",
        "centroid",
        "cut_solid_count",
        "cut_face_count",
        "cut_edge_count",
        "section_face_count",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _error(
            "Detached section geometry is malformed.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    counts = tuple(
        value[name]
        for name in (
            "cut_solid_count",
            "cut_face_count",
            "cut_edge_count",
            "section_face_count",
        )
    )
    if any(type(item) is not int for item in counts):
        _error(
            "Detached section topology counts are malformed.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    solids, cut_faces, cut_edges, section_faces = counts
    if (
        not 1 <= solids <= MAX_SECTION_CUT_SOLIDS
        or not 1 <= cut_faces <= MAX_SECTION_CUT_FACES
        or not 1 <= cut_edges <= MAX_SECTION_CUT_EDGES
        or not 1 <= section_faces <= MAX_SECTION_CUT_FACES
    ):
        _error(
            "Detached section topology counts are invalid.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    centroid_value = value["centroid"]
    if not isinstance(centroid_value, list) or len(centroid_value) != 3:
        _error(
            "Detached section centroid is malformed.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    centroid = tuple(float(item) for item in centroid_value)
    if any(not math.isfinite(item) for item in centroid):
        _error(
            "Detached section centroid is invalid.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    return PreparedSectionGeometry(
        cut_pieces=_artifact(
            value["cut_pieces"],
            root=root,
            expected="outputs/section-cut-pieces.brep",
        ),
        section_faces=_artifact(
            value["section_faces"],
            root=root,
            expected="outputs/section-faces.brep",
        ),
        centroid=centroid,
        cut_solid_count=solids,
        cut_face_count=cut_faces,
        cut_edge_count=cut_edges,
        section_face_count=section_faces,
    )


def _read_result(frozen: FrozenDrawingSection) -> PreparedDrawingSection:
    data = _read_regular(
        frozen.workspace.path / "result.json",
        root=frozen.workspace.path,
        maximum=MAX_SECTION_RESULT_BYTES,
    )
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeDrawingError(
            "The detached section result is unreadable.",
            error_code="NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        ) from exc
    if not isinstance(value, Mapping):
        _error(
            "The detached section result is malformed.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    if value.get("ok") is False:
        code = str(value.get("error_code") or "")
        message = str(value.get("message") or "")[:320]
        if not code.startswith("NATIVE_DRAWING_SECTION_") or not message:
            _error(
                "The detached section process failed.",
                "NATIVE_DRAWING_SECTION_EXECUTION_FAILED",
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
        "section",
    }
    scale = value.get("effective_scale")
    if (
        set(value) != required
        or value.get("ok") is not True
        or str(value.get("protocol")) != DRAWING_SECTION_PROTOCOL
        or str(value.get("request_sha256")) != frozen.request_sha256
        or str(value.get("page_name")) != frozen.page_name
        or str(value.get("base_name")) != frozen.base_name
        or tuple(value.get("source_names") or ()) != frozen.source_names
        or type(scale) not in {int, float}
        or not math.isfinite(float(scale))
        or not 1.0e-12 <= float(scale) <= 1_000.0
    ):
        _error(
            "The detached section result failed protocol validation.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    projection = prepared_projection_from_descriptor(
        value["projection"],
        root=frozen.workspace.path,
        index=0,
    )
    if projection.key != "section_view":
        _error(
            "The detached section returned the wrong projection identity.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    return PreparedDrawingSection(
        frozen=frozen,
        projection=projection,
        section=_section(value["section"], frozen.workspace.path),
        effective_scale=float(scale),
    )


def execute_section_projection(
    frozen: FrozenDrawingSection,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedDrawingSection:
    if not isinstance(frozen, FrozenDrawingSection):
        raise TypeError("frozen must be a FrozenDrawingSection")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(5, "Authenticating exact Drawing section inputs")
    _validate_inputs(frozen)
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(12, "Computing the section outside the UI process")
    code = (
        "import os,runpy;"
        "runpy.run_path(os.environ['VIBECAD_NATIVE_DRAWING_SECTION_CHILD'],"
        "run_name='__main__')"
    )
    process = run_process(
        [str(frozen.workspace.freecadcmd.path), "--safe-mode", "-c", code],
        cwd=frozen.workspace.path,
        environment=_environment(frozen),
        cancellation_check=cancelled,
        timeout_seconds=SECTION_TIMEOUT_SECONDS,
        memory_limit_bytes=SECTION_MEMORY_LIMIT_BYTES,
    )
    if bool(process.get("cancelled")):
        raise NativeBackgroundCancelled()
    if not bool(process.get("started")):
        _error(
            "The isolated section process could not start.",
            "NATIVE_DRAWING_SECTION_EXECUTION_FAILED",
        )
    if bool(process.get("timed_out")):
        _error(
            "Section computation exceeded its ten-minute safety limit.",
            "NATIVE_DRAWING_SECTION_LIMIT",
        )
    if bool(process.get("memory_exceeded")):
        _error(
            "Section computation exceeded its 2 GiB memory safety limit.",
            "NATIVE_DRAWING_SECTION_LIMIT",
        )
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(82, "Authenticating projected and cut-surface geometry")
    prepared = _read_result(frozen)
    if int(process.get("returncode", 1)) != 0:
        _error(
            "The isolated section process exited unsuccessfully.",
            "NATIVE_DRAWING_SECTION_EXECUTION_FAILED",
        )
    progress(89, "Prepared exact Drawing section")
    return prepared


def section_snapshot(prepared: PreparedSectionGeometry) -> dict[str, Any]:
    if not isinstance(prepared, PreparedSectionGeometry):
        raise TypeError("prepared must be a PreparedSectionGeometry")
    import FreeCAD as App
    import Part

    def load(artifact: SectionArtifact, expected_count: int, member: str) -> Any:
        data = _read_regular(
            artifact.path,
            root=artifact.path.parents[1],
            maximum=MAX_SECTION_ARTIFACT_BYTES,
        )
        if len(data) != artifact.size_bytes or hashlib.sha256(data).hexdigest() != artifact.sha256:
            _error(
                "A prepared section artifact changed before document adoption.",
                "NATIVE_DRAWING_SECTION_OUTPUT_CHANGED",
            )
        shape = Part.Shape()
        try:
            shape.importBrep(str(artifact.path))
        except Exception as exc:
            raise NativeDrawingError(
                "A prepared section artifact could not be imported.",
                error_code="NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
            ) from exc
        if len(tuple(getattr(shape, member))) != expected_count:
            _error(
                "A prepared section artifact changed topology during import.",
                "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
            )
        return shape

    cut = load(prepared.cut_pieces, prepared.cut_solid_count, "Solids")
    faces = load(prepared.section_faces, prepared.section_face_count, "Faces")
    if (
        len(tuple(cut.Faces)) != prepared.cut_face_count
        or len(tuple(cut.Edges)) != prepared.cut_edge_count
    ):
        _error(
            "The prepared section cut topology changed during import.",
            "NATIVE_DRAWING_SECTION_OUTPUT_INVALID",
        )
    return {
        "cut_pieces": cut,
        "section_faces": faces,
        "centroid": App.Vector(*prepared.centroid),
    }


__all__ = [
    "PreparedDrawingSection",
    "execute_section_projection",
    "projection_snapshot",
    "section_snapshot",
]
