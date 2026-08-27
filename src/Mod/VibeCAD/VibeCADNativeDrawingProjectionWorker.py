# SPDX-License-Identifier: LGPL-2.1-or-later

"""Cancellable Drawing projection process and authenticated result adoption."""

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
from VibeCADNativeDrawingProjectionInput import (
    DRAWING_PROJECTION_PROTOCOL,
    MAX_PROJECTIONS,
    DrawingProjectionFit,
    FrozenDrawingProjectionBatch,
    validate_frozen_file,
)
from VibeCADScriptedProcess import run_process


MAX_PROJECTION_RESULT_BYTES = 16 * 1024 * 1024
MAX_PROJECTION_ARTIFACT_BYTES = 256 * 1024 * 1024
MAX_PROJECTION_EDGES = 200_000
MAX_PROJECTION_FACES = 50_000
PROJECTION_TIMEOUT_SECONDS = 600.0
PROJECTION_MEMORY_LIMIT_BYTES = 2 * 1024 * 1024 * 1024


@dataclass(frozen=True, slots=True)
class ProjectionArtifact:
    path: Path = field(repr=False, compare=False)
    size_bytes: int
    sha256: str


@dataclass(frozen=True, slots=True)
class PreparedDrawingProjection:
    key: str
    edges: ProjectionArtifact = field(repr=False, compare=False)
    faces: ProjectionArtifact = field(repr=False, compare=False)
    edge_classes: tuple[int, ...]
    edge_visibility: tuple[bool, ...]
    source_indices: tuple[int, ...]
    centroid: tuple[float, float, float]
    edge_count: int
    face_count: int
    visible_edge_count: int
    hidden_edge_count: int
    bounds: tuple[float, float, float, float] | None


@dataclass(frozen=True, slots=True)
class PreparedDrawingProjectionLayout:
    scale: float
    positions_mm: tuple[tuple[str, tuple[float, float]], ...]
    page_bounds_mm: tuple[tuple[str, tuple[float, float, float, float]], ...]
    page_width_mm: float
    page_height_mm: float
    spacing_x_mm: float
    spacing_y_mm: float
    drawable_bounds_mm: tuple[float, float, float, float]

    def position(self, view: str) -> tuple[float, float]:
        return dict(self.positions_mm)[view]

    def page_bounds(self, view: str) -> tuple[float, float, float, float]:
        return dict(self.page_bounds_mm)[view]


@dataclass(frozen=True, slots=True)
class PreparedDrawingProjectionBatch:
    frozen: FrozenDrawingProjectionBatch = field(repr=False, compare=False)
    projections: tuple[PreparedDrawingProjection, ...]
    layout: PreparedDrawingProjectionLayout | None

    def projection(self, key: str) -> PreparedDrawingProjection:
        matches = tuple(item for item in self.projections if item.key == key)
        if len(matches) != 1:
            raise NativeDrawingError(
                "The detached Drawing result is missing its exact projection.",
                error_code="NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            )
        return matches[0]


def _error(message: str, code: str) -> None:
    raise NativeDrawingError(message, error_code=code)


def _read_regular(path: Path, *, root: Path, maximum: int) -> bytes:
    resolved = path.resolve()
    try:
        resolved.relative_to(root)
    except ValueError:
        _error(
            "A Drawing projection artifact escaped its private workspace.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    try:
        value = path.lstat()
    except OSError as exc:
        raise NativeDrawingError(
            "A Drawing projection artifact is unavailable.",
            error_code="NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        ) from exc
    if not stat.S_ISREG(value.st_mode):
        _error(
            "A Drawing projection artifact is not a regular file.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
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
                "A Drawing projection artifact changed while opening.",
                "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            )
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            data.extend(chunk)
            if len(data) > maximum:
                _error(
                    "A Drawing projection artifact exceeds its safety bound.",
                    "NATIVE_DRAWING_PROJECTION_LIMIT",
                )
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    if not data:
        _error(
            "A Drawing projection artifact is empty.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    return bytes(data)


def _isolated_environment(frozen: FrozenDrawingProjectionBatch) -> dict[str, str]:
    root = str(frozen.workspace_path)
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
            "VIBECAD_NATIVE_DRAWING_PROJECTION_REQUEST": str(frozen.request.path),
            "VIBECAD_NATIVE_DRAWING_PROJECTION_CHILD": str(frozen.child.path),
        }
    )
    if sys.platform == "win32":
        drive, tail = os.path.splitdrive(root)
        environment["USERPROFILE"] = root
        if drive:
            environment["HOMEDRIVE"] = drive
            environment["HOMEPATH"] = tail or "\\"
    return environment


def _validate_inputs(frozen: FrozenDrawingProjectionBatch) -> None:
    validate_frozen_file(
        frozen.freecadcmd,
        maximum=None,
        executable=True,
    )
    validate_frozen_file(frozen.child, maximum=1024 * 1024)
    validate_frozen_file(frozen.request, maximum=256 * 1024)
    for source in frozen.source_files:
        validate_frozen_file(source, maximum=256 * 1024 * 1024)


def _artifact(
    value: Any,
    *,
    root: Path,
    expected: str,
) -> ProjectionArtifact:
    required = {"artifact", "artifact_bytes", "artifact_sha256"}
    if not isinstance(value, Mapping) or set(value) != required:
        _error(
            "A detached Drawing projection artifact descriptor is malformed.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    relative = Path(str(value["artifact"] or ""))
    if relative != Path(expected):
        _error(
            "A detached Drawing projection artifact has an unexpected identity.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    data = _read_regular(
        root / relative,
        root=root,
        maximum=MAX_PROJECTION_ARTIFACT_BYTES,
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
            "A detached Drawing projection artifact failed authentication.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    return ProjectionArtifact(root / relative, size, digest)


def _integer_list(value: Any, *, count: int, field_name: str) -> tuple[int, ...]:
    if not isinstance(value, list) or len(value) != count or any(
        type(item) is not int for item in value
    ):
        _error(
            f"Detached Drawing projection {field_name} is malformed.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    return tuple(int(item) for item in value)


def _bool_list(value: Any, *, count: int) -> tuple[bool, ...]:
    if not isinstance(value, list) or len(value) != count or any(
        type(item) is not bool for item in value
    ):
        _error(
            "Detached Drawing projection edge_visibility is malformed.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    return tuple(bool(item) for item in value)


def prepared_projection_from_descriptor(
    value: Any,
    *,
    root: Path,
    index: int,
) -> PreparedDrawingProjection:
    required = {
        "key",
        "edge_count",
        "face_count",
        "visible_edge_count",
        "hidden_edge_count",
        "edges",
        "faces",
        "edge_classes",
        "edge_visibility",
        "source_indices",
        "centroid",
    }
    if (
        not isinstance(value, Mapping)
        or not required <= set(value)
        or set(value) - required != ({"bounds"} if "bounds" in value else set())
    ):
        _error(
            "A detached Drawing projection result is malformed.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    counts = tuple(
        value[name]
        for name in (
            "edge_count",
            "face_count",
            "visible_edge_count",
            "hidden_edge_count",
        )
    )
    if any(type(item) is not int for item in counts):
        _error(
            "Detached Drawing projection counts are malformed.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    edge_count, face_count, visible_count, hidden_count = counts
    if (
        not 1 <= edge_count <= MAX_PROJECTION_EDGES
        or not 0 <= face_count <= MAX_PROJECTION_FACES
        or visible_count < 1
        or hidden_count < 0
        or visible_count + hidden_count != edge_count
    ):
        _error(
            "Detached Drawing projection counts are inconsistent.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    classes = _integer_list(
        value["edge_classes"],
        count=edge_count,
        field_name="edge_classes",
    )
    visibility = _bool_list(value["edge_visibility"], count=edge_count)
    source_indices = _integer_list(
        value["source_indices"],
        count=edge_count,
        field_name="source_indices",
    )
    if sum(visibility) != visible_count:
        _error(
            "Detached Drawing projection visibility counts are inconsistent.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    centroid_value = value["centroid"]
    if not isinstance(centroid_value, list) or len(centroid_value) != 3:
        _error(
            "Detached Drawing projection centroid is malformed.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    centroid = tuple(float(item) for item in centroid_value)
    if any(not math.isfinite(item) for item in centroid):
        _error(
            "Detached Drawing projection centroid is invalid.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    bounds_value = value.get("bounds")
    bounds = None
    if bounds_value is not None:
        if not isinstance(bounds_value, list) or len(bounds_value) != 4:
            _error(
                "Detached Drawing projection bounds are malformed.",
                "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            )
        bounds = tuple(float(item) for item in bounds_value)
        if (
            any(not math.isfinite(item) for item in bounds)
            or bounds[2] <= bounds[0]
            or bounds[3] <= bounds[1]
        ):
            _error(
                "Detached Drawing projection bounds are invalid.",
                "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            )
    prefix = f"outputs/projection-{index:03d}"
    return PreparedDrawingProjection(
        key=str(value["key"] or ""),
        edges=_artifact(value["edges"], root=root, expected=f"{prefix}-edges.brep"),
        faces=_artifact(value["faces"], root=root, expected=f"{prefix}-faces.brep"),
        edge_classes=classes,
        edge_visibility=visibility,
        source_indices=source_indices,
        centroid=centroid,
        edge_count=edge_count,
        face_count=face_count,
        visible_edge_count=visible_count,
        hidden_edge_count=hidden_count,
        bounds=bounds,
    )


def _prepared_layout(
    value: Any,
    *,
    fit: DrawingProjectionFit | None,
    projections: tuple[PreparedDrawingProjection, ...],
) -> PreparedDrawingProjectionLayout | None:
    if fit is None:
        if value is not None:
            _error(
                "The detached Drawing result added an unrequested layout.",
                "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            )
        return None
    required = {
        "scale",
        "positions_mm",
        "page_bounds_mm",
        "page_width_mm",
        "page_height_mm",
        "spacing_x_mm",
        "spacing_y_mm",
        "drawable_bounds_mm",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _error(
            "The detached Drawing projection layout is malformed.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    scale = float(value["scale"])
    dimensions = tuple(
        float(value[name])
        for name in (
            "page_width_mm",
            "page_height_mm",
            "spacing_x_mm",
            "spacing_y_mm",
        )
    )
    if (
        not math.isfinite(scale)
        or not 1.0e-12 <= scale <= 1_000.0
        or any(not math.isfinite(number) for number in dimensions)
        or not math.isclose(dimensions[0], float(fit.page_width_mm), abs_tol=1.0e-9)
        or not math.isclose(dimensions[1], float(fit.page_height_mm), abs_tol=1.0e-9)
        or not math.isclose(dimensions[2], float(fit.spacing_x_mm), abs_tol=1.0e-9)
        or not math.isclose(dimensions[3], float(fit.spacing_y_mm), abs_tol=1.0e-9)
    ):
        _error(
            "The detached Drawing projection layout changed its page contract.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    positions_value = value["positions_mm"]
    bounds_value = value["page_bounds_mm"]
    drawable_value = value["drawable_bounds_mm"]
    if (
        not isinstance(drawable_value, list)
        or len(drawable_value) != 4
        or any(type(number) not in {int, float} for number in drawable_value)
    ):
        _error(
            "The detached Drawing projection drawable bounds are malformed.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    drawable = tuple(float(number) for number in drawable_value)
    expected_drawable = fit.drawable_bounds_mm or (
        0.0,
        0.0,
        float(fit.page_width_mm),
        float(fit.page_height_mm),
    )
    if (
        any(not math.isfinite(number) for number in drawable)
        or drawable[0] < 0.0
        or drawable[1] < 0.0
        or drawable[2] <= drawable[0]
        or drawable[3] <= drawable[1]
        or drawable[2] > dimensions[0]
        or drawable[3] > dimensions[1]
        or any(
            not math.isclose(actual, float(expected), abs_tol=1.0e-9)
            for actual, expected in zip(drawable, expected_drawable, strict=True)
        )
    ):
        _error(
            "The detached Drawing projection drawable bounds are invalid.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    if (
        not isinstance(positions_value, Mapping)
        or not isinstance(bounds_value, Mapping)
        or set(positions_value) != set(fit.views)
        or set(bounds_value) != set(fit.views)
    ):
        _error(
            "The detached Drawing projection layout changed its view set.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    positions = []
    page_bounds = []
    projection_by_view = {
        key.removeprefix("projection_group:"): projection
        for key, projection in zip(
            (projection.key for projection in projections),
            projections,
            strict=True,
        )
    }
    for view in fit.views:
        raw_position = positions_value[view]
        raw_bounds = bounds_value[view]
        if (
            not isinstance(raw_position, list)
            or len(raw_position) != 2
            or not isinstance(raw_bounds, list)
            or len(raw_bounds) != 4
        ):
            _error(
                "The detached Drawing projection placement is malformed.",
                "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            )
        position = tuple(float(number) for number in raw_position)
        rectangle = tuple(float(number) for number in raw_bounds)
        if (
            any(not math.isfinite(number) for number in (*position, *rectangle))
            or rectangle[0] < drawable[0] - 1.0e-8
            or rectangle[1] < drawable[1] - 1.0e-8
            or rectangle[2] > drawable[2] + 1.0e-8
            or rectangle[3] > drawable[3] + 1.0e-8
            or rectangle[2] <= rectangle[0]
            or rectangle[3] <= rectangle[1]
            or not math.isclose(
                position[0],
                (rectangle[0] + rectangle[2]) / 2.0,
                abs_tol=1.0e-8,
            )
            or not math.isclose(
                position[1],
                (rectangle[1] + rectangle[3]) / 2.0,
                abs_tol=1.0e-8,
            )
        ):
            _error(
                "The detached Drawing projection placement is invalid.",
                "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            )
        projected_bounds = projection_by_view[view].bounds
        if (
            projected_bounds is None
            or not math.isclose(
                rectangle[2] - rectangle[0],
                projected_bounds[2] - projected_bounds[0],
                rel_tol=1.0e-7,
                abs_tol=1.0e-7,
            )
            or not math.isclose(
                rectangle[3] - rectangle[1],
                projected_bounds[3] - projected_bounds[1],
                rel_tol=1.0e-7,
                abs_tol=1.0e-7,
            )
        ):
            _error(
                "The detached Drawing placement does not match its projected geometry.",
                "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            )
        positions.append((view, position))
        page_bounds.append((view, rectangle))
    return PreparedDrawingProjectionLayout(
        scale=scale,
        positions_mm=tuple(positions),
        page_bounds_mm=tuple(page_bounds),
        page_width_mm=dimensions[0],
        page_height_mm=dimensions[1],
        spacing_x_mm=dimensions[2],
        spacing_y_mm=dimensions[3],
        drawable_bounds_mm=drawable,
    )


def _read_result(frozen: FrozenDrawingProjectionBatch) -> PreparedDrawingProjectionBatch:
    path = frozen.workspace_path / "result.json"
    data = _read_regular(
        path,
        root=frozen.workspace_path,
        maximum=MAX_PROJECTION_RESULT_BYTES,
    )
    try:
        value = json.loads(data.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise NativeDrawingError(
            "The detached Drawing projection result is unreadable.",
            error_code="NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        ) from exc
    if not isinstance(value, Mapping):
        _error(
            "The detached Drawing projection result is malformed.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    if value.get("ok") is False:
        code = str(value.get("error_code") or "")
        message = str(value.get("message") or "")[:320]
        if not code.startswith("NATIVE_DRAWING_PROJECTION_") or not message:
            _error(
                "The detached Drawing projection process failed.",
                "NATIVE_DRAWING_PROJECTION_EXECUTION_FAILED",
            )
        raise NativeDrawingError(message, error_code=code)
    required = {"ok", "protocol", "request_sha256", "projections", "layout"}
    projections_value = value.get("projections")
    if (
        set(value) != required
        or value.get("ok") is not True
        or str(value.get("protocol")) != DRAWING_PROJECTION_PROTOCOL
        or str(value.get("request_sha256")) != frozen.request_sha256
        or not isinstance(projections_value, list)
        or not 1 <= len(projections_value) <= MAX_PROJECTIONS
    ):
        _error(
            "The detached Drawing projection result failed protocol validation.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    projections = tuple(
        prepared_projection_from_descriptor(
            item,
            root=frozen.workspace_path,
            index=index,
        )
        for index, item in enumerate(projections_value)
    )
    if tuple(item.key for item in projections) != frozen.projection_keys:
        _error(
            "The detached Drawing projection result changed the requested view order.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    return PreparedDrawingProjectionBatch(
        frozen=frozen,
        projections=projections,
        layout=_prepared_layout(value["layout"], fit=frozen.fit, projections=projections),
    )


def execute_projection_batch(
    frozen: FrozenDrawingProjectionBatch,
    *,
    cancelled: Callable[[], bool],
    progress: Callable[[int, str], None],
) -> PreparedDrawingProjectionBatch:
    """Run high-quality HLR in an isolated, windowless FreeCAD process."""

    if not isinstance(frozen, FrozenDrawingProjectionBatch):
        raise TypeError("frozen must be a FrozenDrawingProjectionBatch")
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(5, "Authenticating frozen Drawing sources")
    _validate_inputs(frozen)
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(12, "Projecting Drawing geometry outside the UI process")
    code = (
        "import os,runpy;"
        "runpy.run_path(os.environ['VIBECAD_NATIVE_DRAWING_PROJECTION_CHILD'],"
        "run_name='__main__')"
    )
    process = run_process(
        [str(frozen.freecadcmd.path), "--safe-mode", "-c", code],
        cwd=frozen.workspace_path,
        environment=_isolated_environment(frozen),
        cancellation_check=cancelled,
        timeout_seconds=PROJECTION_TIMEOUT_SECONDS,
        memory_limit_bytes=PROJECTION_MEMORY_LIMIT_BYTES,
    )
    if bool(process.get("cancelled")):
        raise NativeBackgroundCancelled()
    if not bool(process.get("started")):
        _error(
            "The isolated Drawing projection process could not start.",
            "NATIVE_DRAWING_PROJECTION_EXECUTION_FAILED",
        )
    if bool(process.get("timed_out")):
        _error(
            "Drawing projection exceeded its ten-minute safety limit.",
            "NATIVE_DRAWING_PROJECTION_LIMIT",
        )
    if bool(process.get("memory_exceeded")):
        _error(
            "Drawing projection exceeded its 2 GiB memory safety limit.",
            "NATIVE_DRAWING_PROJECTION_LIMIT",
        )
    if cancelled():
        raise NativeBackgroundCancelled()
    progress(82, "Authenticating projected Drawing geometry")
    prepared = _read_result(frozen)
    if int(process.get("returncode", 1)) != 0:
        _error(
            "The isolated Drawing projection process exited unsuccessfully.",
            "NATIVE_DRAWING_PROJECTION_EXECUTION_FAILED",
        )
    progress(89, "Prepared exact Drawing projection")
    return prepared


def projection_snapshot(
    prepared: PreparedDrawingProjection,
) -> dict[str, Any]:
    """Load authenticated worker artifacts on the document thread."""

    if not isinstance(prepared, PreparedDrawingProjection):
        raise TypeError("prepared must be a PreparedDrawingProjection")
    import FreeCAD as App
    import Part

    def load(artifact: ProjectionArtifact) -> Any:
        data = _read_regular(
            artifact.path,
            root=artifact.path.parents[1],
            maximum=MAX_PROJECTION_ARTIFACT_BYTES,
        )
        if (
            len(data) != artifact.size_bytes
            or hashlib.sha256(data).hexdigest() != artifact.sha256
        ):
            _error(
                "A prepared Drawing projection changed before document adoption.",
                "NATIVE_DRAWING_PROJECTION_OUTPUT_CHANGED",
            )
        shape = Part.Shape()
        try:
            shape.importBrep(str(artifact.path))
        except Exception as exc:
            raise NativeDrawingError(
                "A prepared Drawing projection could not be imported.",
                error_code="NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            ) from exc
        return shape

    edges = load(prepared.edges)
    faces = load(prepared.faces)
    if len(tuple(edges.Edges)) != prepared.edge_count or len(tuple(faces.Faces)) != prepared.face_count:
        _error(
            "A prepared Drawing projection changed topology during import.",
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
        )
    return {
        "edges": edges,
        "faces": faces,
        "edge_classes": list(prepared.edge_classes),
        "edge_visibility": list(prepared.edge_visibility),
        "source_indices": list(prepared.source_indices),
        "centroid": App.Vector(*prepared.centroid),
    }
