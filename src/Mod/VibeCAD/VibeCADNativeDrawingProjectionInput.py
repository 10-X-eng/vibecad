# SPDX-License-Identifier: LGPL-2.1-or-later

"""Frozen, private input for detached Native Drawing projections."""

from __future__ import annotations

from dataclasses import dataclass, field
import hashlib
import json
import math
import os
from pathlib import Path
import stat
import sys
import tempfile
from typing import Any, Mapping, Sequence

from VibeCADNativeDrawingErrors import NativeDrawingError


DRAWING_PROJECTION_PROTOCOL = "vibecad-native-drawing-projection-v1"
MAX_PROJECTION_SOURCES = 128
MAX_PROJECTIONS = 128
MAX_SOURCE_BREP_BYTES = 256 * 1024 * 1024
MAX_TOTAL_SOURCE_BREP_BYTES = 512 * 1024 * 1024
MAX_PROJECTION_REQUEST_BYTES = 256 * 1024


@dataclass(frozen=True, slots=True)
class DrawingProjectionSource:
    object_name: str
    state_sha256: str
    source: Any = field(repr=False, compare=False)


@dataclass(frozen=True, slots=True)
class DrawingProjectionJob:
    key: str
    sources: tuple[DrawingProjectionSource, ...]
    direction: tuple[float, float, float]
    x_direction: tuple[float, float, float]
    scale: float
    line_flags: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class DrawingProjectionFit:
    views: tuple[str, ...]
    convention: str
    page_width_mm: float
    page_height_mm: float
    spacing_x_mm: float = 15.0
    spacing_y_mm: float = 15.0
    drawable_bounds_mm: tuple[float, float, float, float] | None = None


@dataclass(frozen=True, slots=True)
class FrozenFile:
    path: Path = field(repr=False, compare=False)
    size_bytes: int
    sha256: str
    device: int
    inode: int
    modified_ns: int


@dataclass(slots=True)
class FrozenDrawingProjectionBatch:
    workspace: tempfile.TemporaryDirectory[str] = field(repr=False)
    workspace_path: Path = field(repr=False)
    request: FrozenFile = field(repr=False)
    request_sha256: str
    freecadcmd: FrozenFile = field(repr=False)
    child: FrozenFile = field(repr=False)
    source_files: tuple[FrozenFile, ...] = field(repr=False)
    projection_keys: tuple[str, ...]
    fit: DrawingProjectionFit | None

    def cleanup(self) -> None:
        self.workspace.cleanup()


def _error(message: str, code: str) -> None:
    raise NativeDrawingError(message, error_code=code)


def _hash_open_file(path: Path, *, maximum: int | None) -> FrozenFile:
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeDrawingError(
            "A required Drawing projection runtime file is unavailable.",
            error_code="NATIVE_DRAWING_PROJECTION_RUNTIME_UNAVAILABLE",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _error(
            "A required Drawing projection runtime path is not a regular file.",
            "NATIVE_DRAWING_PROJECTION_RUNTIME_UNAVAILABLE",
        )
    descriptor = -1
    digest = hashlib.sha256()
    size = 0
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or int(opened.st_dev) != int(value.st_dev)
            or int(opened.st_ino) != int(value.st_ino)
        ):
            _error(
                "A required Drawing projection runtime file changed while opening.",
                "NATIVE_DRAWING_PROJECTION_RUNTIME_UNAVAILABLE",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            size += len(chunk)
            if maximum is not None and size > maximum:
                _error(
                    "A frozen Drawing projection file exceeds its safety bound.",
                    "NATIVE_DRAWING_PROJECTION_LIMIT",
                )
            digest.update(chunk)
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if size <= 0:
        _error(
            "A frozen Drawing projection file is empty.",
            "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
        )
    return FrozenFile(
        path=path,
        size_bytes=size,
        sha256=digest.hexdigest(),
        device=int(value.st_dev),
        inode=int(value.st_ino),
        modified_ns=int(value.st_mtime_ns),
    )


def validate_frozen_file(
    frozen: FrozenFile,
    *,
    maximum: int | None,
    executable: bool = False,
) -> None:
    current = _hash_open_file(frozen.path, maximum=maximum)
    if (
        current.size_bytes,
        current.sha256,
        current.device,
        current.inode,
        current.modified_ns,
    ) != (
        frozen.size_bytes,
        frozen.sha256,
        frozen.device,
        frozen.inode,
        frozen.modified_ns,
    ):
        _error(
            "A frozen Drawing projection runtime file changed after preflight.",
            "NATIVE_DRAWING_PROJECTION_RUNTIME_CHANGED",
        )
    if executable and not os.access(frozen.path, os.X_OK):
        _error(
            "The windowless Drawing projection runtime is no longer executable.",
            "NATIVE_DRAWING_PROJECTION_RUNTIME_UNAVAILABLE",
        )


def freeze_regular_file(path: Path, *, maximum: int | None) -> FrozenFile:
    """Authenticate one fixed runtime or private artifact."""

    return _hash_open_file(Path(path), maximum=maximum)


def resolve_freecadcmd() -> FrozenFile:
    import FreeCAD

    names = (
        ("FreeCADCmd.exe", "freecadcmd.exe")
        if sys.platform == "win32"
        else ("FreeCADCmd", "freecadcmd")
    )
    root = Path(str(FreeCAD.getHomePath())) / "bin"
    for name in names:
        candidate = root / name
        if candidate.is_file():
            frozen = _hash_open_file(candidate, maximum=None)
            if os.access(candidate, os.X_OK):
                return frozen
    _error(
        "The fixed windowless FreeCADCmd runtime is unavailable.",
        "NATIVE_DRAWING_PROJECTION_RUNTIME_UNAVAILABLE",
    )
    raise AssertionError("unreachable")


def _finite_vector(value: Sequence[float], field_name: str) -> list[float]:
    result = [float(item) for item in value]
    if len(result) != 3 or any(not math.isfinite(item) for item in result):
        _error(
            f"{field_name} must contain exactly three finite coordinates.",
            "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
        )
    return result


def _write_private(path: Path, data: bytes) -> None:
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        offset = 0
        while offset < len(data):
            offset += os.write(descriptor, data[offset:])
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _source_key(source: DrawingProjectionSource) -> tuple[str, str]:
    return source.object_name, source.state_sha256


def _validated_jobs(
    jobs: Sequence[DrawingProjectionJob],
) -> tuple[DrawingProjectionJob, ...]:
    result = tuple(jobs)
    if not 1 <= len(result) <= MAX_PROJECTIONS:
        _error(
            f"A Drawing projection batch requires 1 to {MAX_PROJECTIONS} views.",
            "NATIVE_DRAWING_PROJECTION_LIMIT",
        )
    keys = tuple(str(job.key or "") for job in result)
    if any(not key or len(key) > 128 for key in keys) or len(keys) != len(set(keys)):
        _error(
            "Every detached Drawing projection requires one unique bounded key.",
            "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
        )
    for job in result:
        if not 1 <= len(job.sources) <= 12:
            _error(
                "Each Drawing projection requires 1 to 12 exact sources.",
                "NATIVE_DRAWING_PROJECTION_LIMIT",
            )
        _finite_vector(job.direction, "direction")
        _finite_vector(job.x_direction, "x_direction")
        scale = float(job.scale)
        if not math.isfinite(scale) or not 1.0e-12 <= scale <= 1_000.0:
            _error(
                "Drawing projection scale must be finite and positive.",
                "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
            )
        required_flags = {
            "SmoothVisible",
            "SeamVisible",
            "IsoVisible",
            "HardHidden",
            "SmoothHidden",
            "SeamHidden",
            "IsoHidden",
        }
        if set(job.line_flags) != required_flags or any(
            type(value) is not bool for value in job.line_flags.values()
        ):
            _error(
                "Drawing projection line flags are malformed.",
                "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
            )
    return result


def freeze_projection_batch(
    jobs: Sequence[DrawingProjectionJob],
    *,
    fit: DrawingProjectionFit | None = None,
) -> FrozenDrawingProjectionBatch:
    """Freeze exact source B-reps and an authenticated child request."""

    validated = _validated_jobs(jobs)
    ordered_sources: list[DrawingProjectionSource] = []
    source_indexes: dict[tuple[str, str], int] = {}
    for job in validated:
        for source in job.sources:
            key = _source_key(source)
            if key not in source_indexes:
                if len(ordered_sources) >= MAX_PROJECTION_SOURCES:
                    _error(
                        f"A Drawing projection batch exceeds {MAX_PROJECTION_SOURCES} sources.",
                        "NATIVE_DRAWING_PROJECTION_LIMIT",
                    )
                source_indexes[key] = len(ordered_sources)
                ordered_sources.append(source)

    workspace = tempfile.TemporaryDirectory(prefix="vibecad-native-drawing-projection-")
    root = Path(workspace.name).resolve()
    try:
        os.chmod(root, 0o700)
        sources_root = root / "sources"
        sources_root.mkdir(mode=0o700)
        descriptors = []
        source_files = []
        total_bytes = 0
        for index, source in enumerate(ordered_sources):
            path = sources_root / f"source-{index:03d}.brep"
            try:
                shape = source.source.Shape.copy()
                shape.exportBrep(str(path))
                os.chmod(path, 0o600)
            except Exception as exc:
                raise NativeDrawingError(
                    f"Drawing source {source.object_name!r} could not be frozen.",
                    error_code="NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
                ) from exc
            frozen = _hash_open_file(path, maximum=MAX_SOURCE_BREP_BYTES)
            total_bytes += frozen.size_bytes
            if total_bytes > MAX_TOTAL_SOURCE_BREP_BYTES:
                _error(
                    "Frozen Drawing projection sources exceed the 512 MiB batch bound.",
                    "NATIVE_DRAWING_PROJECTION_LIMIT",
                )
            source_files.append(frozen)
            descriptors.append(
                {
                    "index": index,
                    "object_name": source.object_name,
                    "state_sha256": source.state_sha256,
                    "artifact": f"sources/source-{index:03d}.brep",
                    "artifact_bytes": frozen.size_bytes,
                    "artifact_sha256": frozen.sha256,
                }
            )

        fit_value = None
        if fit is not None:
            views = tuple(str(value or "") for value in fit.views)
            if (
                not 2 <= len(views) <= 6
                or "front" not in views
                or len(views) != len(set(views))
                or set(views) - {"front", "top", "right", "left", "bottom", "rear"}
                or fit.convention not in {"first_angle", "third_angle"}
                or tuple(job.key for job in validated)
                != tuple(f"projection_group:{view}" for view in views)
            ):
                _error(
                    "A Drawing projection fit request is malformed.",
                    "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
                )
            dimensions = (
                fit.page_width_mm,
                fit.page_height_mm,
                fit.spacing_x_mm,
                fit.spacing_y_mm,
            )
            if (
                any(type(value) not in {int, float} for value in dimensions)
                or any(not math.isfinite(float(value)) for value in dimensions)
                or float(fit.page_width_mm) <= 0.0
                or float(fit.page_height_mm) <= 0.0
                or float(fit.spacing_x_mm) < 0.0
                or float(fit.spacing_y_mm) < 0.0
            ):
                _error(
                    "A Drawing projection fit request is malformed.",
                    "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
                )
            drawable = fit.drawable_bounds_mm or (
                0.0,
                0.0,
                float(fit.page_width_mm),
                float(fit.page_height_mm),
            )
            if (
                not isinstance(drawable, (list, tuple))
                or len(drawable) != 4
                or any(type(value) not in {int, float} for value in drawable)
            ):
                _error(
                    "A Drawing projection fit request has invalid drawable bounds.",
                    "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
                )
            drawable = tuple(float(value) for value in drawable)
            if (
                any(not math.isfinite(value) for value in drawable)
                or drawable[0] < 0.0
                or drawable[1] < 0.0
                or drawable[2] <= drawable[0]
                or drawable[3] <= drawable[1]
                or drawable[2] > float(fit.page_width_mm)
                or drawable[3] > float(fit.page_height_mm)
            ):
                _error(
                    "A Drawing projection fit request has invalid drawable bounds.",
                    "NATIVE_DRAWING_PROJECTION_INPUT_INVALID",
                )
            fit_value = {
                "views": list(views),
                "convention": fit.convention,
                "page_width_mm": float(fit.page_width_mm),
                "page_height_mm": float(fit.page_height_mm),
                "spacing_x_mm": float(fit.spacing_x_mm),
                "spacing_y_mm": float(fit.spacing_y_mm),
                "drawable_bounds_mm": list(drawable),
            }

        request_value = {
            "protocol": DRAWING_PROJECTION_PROTOCOL,
            "workspace": str(root),
            "sources": descriptors,
            "projections": [
                {
                    "key": job.key,
                    "source_indices": [
                        source_indexes[_source_key(source)] for source in job.sources
                    ],
                    "direction": _finite_vector(job.direction, "direction"),
                    "x_direction": _finite_vector(job.x_direction, "x_direction"),
                    "scale": float(job.scale),
                    "line_flags": dict(job.line_flags),
                }
                for job in validated
            ],
            "fit": fit_value,
            "result": "result.json",
        }
        encoded = json.dumps(
            request_value,
            ensure_ascii=True,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        if len(encoded) > MAX_PROJECTION_REQUEST_BYTES:
            _error(
                "The frozen Drawing projection request exceeds its metadata bound.",
                "NATIVE_DRAWING_PROJECTION_LIMIT",
            )
        request_path = root / "request.json"
        _write_private(request_path, encoded)
        request = _hash_open_file(
            request_path,
            maximum=MAX_PROJECTION_REQUEST_BYTES,
        )
        child_path = Path(__file__).with_name(
            "VibeCADNativeDrawingProjectionChild.py"
        ).resolve()
        return FrozenDrawingProjectionBatch(
            workspace=workspace,
            workspace_path=root,
            request=request,
            request_sha256=request.sha256,
            freecadcmd=resolve_freecadcmd(),
            child=_hash_open_file(child_path, maximum=1024 * 1024),
            source_files=tuple(source_files),
            projection_keys=tuple(job.key for job in validated),
            fit=fit,
        )
    except Exception:
        workspace.cleanup()
        raise
