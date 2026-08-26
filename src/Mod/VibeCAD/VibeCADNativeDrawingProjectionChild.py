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
_PROJECTION_VIEWS = {"front", "top", "right", "left", "bottom", "rear"}


class _ChildFailure(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def _fail(code: str, message: str) -> None:
    raise _ChildFailure(code, message)


def _sensible_scale(available_scale: float) -> float:
    """Return TechDraw's largest standard scale no greater than available_scale."""

    if not math.isfinite(available_scale) or available_scale <= 0.0:
        _fail(
            "NATIVE_DRAWING_PROJECTION_LAYOUT_INVALID",
            "The projected views cannot fit on the Drawing page.",
        )
    working = min(float(available_scale), 1_000.0)
    exponent = math.floor(math.log10(working))
    normalized = working * math.pow(10.0, -exponent)
    choices = (
        (1.0, 1.25, 2.0, 2.5, 3.75, 5.0, 7.5, 10.0, 50.0, 100.0)
        if exponent < 0
        else (1.0, 1.5, 2.0, 3.0, 4.0, 5.0, 8.0, 10.0, 10.0, 50.0, 100.0)
    )
    selected = choices[0]
    for candidate in choices:
        if candidate > normalized:
            break
        selected = candidate
    return float(selected * math.pow(10.0, exponent))


def _fit_projection_group_layout(
    bounds: Mapping[str, Any],
    *,
    convention: str,
    page_width_mm: float,
    page_height_mm: float,
    spacing_x_mm: float,
    spacing_y_mm: float,
    drawable_bounds_mm: tuple[float, float, float, float] | None = None,
) -> dict[str, Any]:
    """Fit exact 1:1 projected bounds into the selected projection convention."""

    if (
        not isinstance(bounds, Mapping)
        or not 2 <= len(bounds) <= len(_PROJECTION_VIEWS)
        or "front" not in bounds
        or not set(bounds) <= _PROJECTION_VIEWS
        or convention not in {"first_angle", "third_angle"}
    ):
        _fail(
            "NATIVE_DRAWING_PROJECTION_LAYOUT_INVALID",
            "The projection set cannot be arranged on the Drawing page.",
        )
    numbers = (page_width_mm, page_height_mm, spacing_x_mm, spacing_y_mm)
    if (
        any(type(value) not in {int, float} for value in numbers)
        or any(not math.isfinite(float(value)) for value in numbers)
        or float(page_width_mm) <= 0.0
        or float(page_height_mm) <= 0.0
        or float(spacing_x_mm) < 0.0
        or float(spacing_y_mm) < 0.0
    ):
        _fail(
            "NATIVE_DRAWING_PROJECTION_LAYOUT_INVALID",
            "The Drawing page or projection spacing is invalid.",
        )
    if drawable_bounds_mm is None:
        drawable = (0.0, 0.0, float(page_width_mm), float(page_height_mm))
    elif (
        not isinstance(drawable_bounds_mm, (list, tuple))
        or len(drawable_bounds_mm) != 4
        or any(type(value) not in {int, float} for value in drawable_bounds_mm)
    ):
        _fail(
            "NATIVE_DRAWING_PROJECTION_LAYOUT_INVALID",
            "The Drawing template has invalid drawable bounds.",
        )
    else:
        drawable = tuple(float(value) for value in drawable_bounds_mm)
    if (
        any(not math.isfinite(value) for value in drawable)
        or drawable[0] < 0.0
        or drawable[1] < 0.0
        or drawable[2] <= drawable[0]
        or drawable[3] <= drawable[1]
        or drawable[2] > float(page_width_mm)
        or drawable[3] > float(page_height_mm)
    ):
        _fail(
            "NATIVE_DRAWING_PROJECTION_LAYOUT_INVALID",
            "The Drawing template has invalid drawable bounds.",
        )

    extents: dict[str, tuple[float, float]] = {}
    for view, raw in bounds.items():
        if (
            not isinstance(raw, (list, tuple))
            or len(raw) != 4
            or any(type(value) not in {int, float} for value in raw)
        ):
            _fail(
                "NATIVE_DRAWING_PROJECTION_LAYOUT_INVALID",
                "A projected view has invalid bounds.",
            )
        minimum_x, minimum_y, maximum_x, maximum_y = (
            float(value) for value in raw
        )
        if (
            any(
                not math.isfinite(value)
                for value in (minimum_x, minimum_y, maximum_x, maximum_y)
            )
            or maximum_x <= minimum_x
            or maximum_y <= minimum_y
        ):
            _fail(
                "NATIVE_DRAWING_PROJECTION_LAYOUT_INVALID",
                "A projected view has invalid bounds.",
            )
        extents[str(view)] = (maximum_x - minimum_x, maximum_y - minimum_y)

    if convention == "third_angle":
        slots = {
            "front": (1, 1),
            "top": (1, 2),
            "right": (2, 1),
            "left": (0, 1),
            "bottom": (1, 0),
            "rear": (3, 1),
        }
    else:
        slots = {
            "front": (1, 1),
            "top": (1, 0),
            "right": (0, 1),
            "left": (2, 1),
            "bottom": (1, 2),
            "rear": (3, 1),
        }

    columns = sorted({slots[view][0] for view in extents})
    rows = sorted({slots[view][1] for view in extents})
    column_widths = {
        column: max(
            extents[view][0]
            for view in extents
            if slots[view][0] == column
        )
        for column in columns
    }
    row_heights = {
        row: max(
            extents[view][1]
            for view in extents
            if slots[view][1] == row
        )
        for row in rows
    }
    horizontal_gaps = float(spacing_x_mm) * max(0, len(columns) - 1)
    vertical_gaps = float(spacing_y_mm) * max(0, len(rows) - 1)
    available_width = drawable[2] - drawable[0] - horizontal_gaps
    available_height = drawable[3] - drawable[1] - vertical_gaps
    if available_width <= 0.0 or available_height <= 0.0:
        _fail(
            "NATIVE_DRAWING_PROJECTION_LAYOUT_INVALID",
            "The projected views cannot fit on the Drawing page.",
        )
    scale = _sensible_scale(
        min(
            available_width / sum(column_widths.values()),
            available_height / sum(row_heights.values()),
        )
    )

    scaled_columns = {key: value * scale for key, value in column_widths.items()}
    scaled_rows = {key: value * scale for key, value in row_heights.items()}
    used_width = sum(scaled_columns.values()) + horizontal_gaps
    used_height = sum(scaled_rows.values()) + vertical_gaps
    x_cursor = drawable[0] + (drawable[2] - drawable[0] - used_width) / 2.0
    column_centers: dict[int, float] = {}
    for index, column in enumerate(columns):
        width = scaled_columns[column]
        column_centers[column] = x_cursor + width / 2.0
        x_cursor += width
        if index + 1 < len(columns):
            x_cursor += float(spacing_x_mm)
    y_cursor = drawable[1] + (drawable[3] - drawable[1] - used_height) / 2.0
    row_centers: dict[int, float] = {}
    for index, row in enumerate(rows):
        height = scaled_rows[row]
        row_centers[row] = y_cursor + height / 2.0
        y_cursor += height
        if index + 1 < len(rows):
            y_cursor += float(spacing_y_mm)

    positions = {
        view: [column_centers[slots[view][0]], row_centers[slots[view][1]]]
        for view in extents
    }
    page_bounds = {}
    tolerance = 1.0e-8
    for view, position in positions.items():
        width, height = extents[view]
        rectangle = [
            position[0] - width * scale / 2.0,
            position[1] - height * scale / 2.0,
            position[0] + width * scale / 2.0,
            position[1] + height * scale / 2.0,
        ]
        if (
            rectangle[0] < drawable[0] - tolerance
            or rectangle[1] < drawable[1] - tolerance
            or rectangle[2] > drawable[2] + tolerance
            or rectangle[3] > drawable[3] + tolerance
        ):
            _fail(
                "NATIVE_DRAWING_PROJECTION_LAYOUT_INVALID",
                "The projected views cannot fit on the Drawing page.",
            )
        page_bounds[view] = rectangle
    return {
        "scale": scale,
        "positions_mm": positions,
        "page_bounds_mm": page_bounds,
        "page_width_mm": float(page_width_mm),
        "page_height_mm": float(page_height_mm),
        "spacing_x_mm": float(spacing_x_mm),
        "spacing_y_mm": float(spacing_y_mm),
        "drawable_bounds_mm": list(drawable),
    }


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
    required = {"protocol", "workspace", "sources", "projections", "fit", "result"}
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


def _fit_descriptor(
    value: Any,
    projections: list[dict[str, Any]],
) -> dict[str, Any] | None:
    if value is None:
        return None
    required = {
        "views",
        "convention",
        "page_width_mm",
        "page_height_mm",
        "spacing_x_mm",
        "spacing_y_mm",
        "drawable_bounds_mm",
    }
    if not isinstance(value, Mapping) or set(value) != required:
        _fail(
            "NATIVE_DRAWING_PROJECTION_CHILD_INVALID",
            "The projection fit request is malformed.",
        )
    views = value["views"]
    if (
        not isinstance(views, list)
        or not 2 <= len(views) <= len(_PROJECTION_VIEWS)
        or "front" not in views
        or len(views) != len(set(views))
        or any(view not in _PROJECTION_VIEWS for view in views)
        or [item["key"] for item in projections]
        != [f"projection_group:{view}" for view in views]
        or value["convention"] not in {"first_angle", "third_angle"}
    ):
        _fail(
            "NATIVE_DRAWING_PROJECTION_CHILD_INVALID",
            "The projection fit request is invalid.",
        )
    dimensions = tuple(
        value[name]
        for name in (
            "page_width_mm",
            "page_height_mm",
            "spacing_x_mm",
            "spacing_y_mm",
        )
    )
    if (
        any(type(number) not in {int, float} for number in dimensions)
        or any(not math.isfinite(float(number)) for number in dimensions)
        or float(value["page_width_mm"]) <= 0.0
        or float(value["page_height_mm"]) <= 0.0
        or float(value["spacing_x_mm"]) < 0.0
        or float(value["spacing_y_mm"]) < 0.0
    ):
        _fail(
            "NATIVE_DRAWING_PROJECTION_CHILD_INVALID",
            "The projection fit request is invalid.",
        )
    drawable = value["drawable_bounds_mm"]
    if (
        not isinstance(drawable, list)
        or len(drawable) != 4
        or any(type(number) not in {int, float} for number in drawable)
    ):
        _fail(
            "NATIVE_DRAWING_PROJECTION_CHILD_INVALID",
            "The projection drawable bounds are malformed.",
        )
    drawable = [float(number) for number in drawable]
    if (
        any(not math.isfinite(number) for number in drawable)
        or drawable[0] < 0.0
        or drawable[1] < 0.0
        or drawable[2] <= drawable[0]
        or drawable[3] <= drawable[1]
        or drawable[2] > float(value["page_width_mm"])
        or drawable[3] > float(value["page_height_mm"])
    ):
        _fail(
            "NATIVE_DRAWING_PROJECTION_CHILD_INVALID",
            "The projection drawable bounds are invalid.",
        )
    return dict(value)


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


def _projection_snapshot(
    document: Any,
    page: Any,
    sources: list[Any],
    descriptor: Mapping[str, Any],
    index: int,
) -> tuple[Any, Mapping[str, Any]]:
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
    return view, snapshot


def _snapshot_bounds(snapshot: Mapping[str, Any]) -> list[float]:
    bounds = snapshot["edges"].BoundBox
    result = [
        float(bounds.XMin),
        float(bounds.YMin),
        float(bounds.XMax),
        float(bounds.YMax),
    ]
    if (
        any(not math.isfinite(value) for value in result)
        or result[2] <= result[0]
        or result[3] <= result[1]
    ):
        _fail(
            "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
            "TechDraw returned invalid projected bounds.",
        )
    return result


def _projection_result(
    descriptor: Mapping[str, Any],
    snapshot: Mapping[str, Any],
    root: Path,
    index: int,
) -> dict[str, Any]:
    edge_count = len(tuple(snapshot["edges"].Edges))
    face_count = len(tuple(snapshot["faces"].Faces))
    classes = [int(value) for value in snapshot["edge_classes"]]
    visibility = [bool(value) for value in snapshot["edge_visibility"]]
    source_indices = [int(value) for value in snapshot["source_indices"]]
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
        "bounds": _snapshot_bounds(snapshot),
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
    fit = _fit_descriptor(request["fit"], projection_descriptors)
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
        projected = [
            _projection_snapshot(document, page, sources, descriptor, index)
            for index, descriptor in enumerate(projection_descriptors)
        ]
        layout = None
        if fit is not None:
            initial_bounds = {
                view: _snapshot_bounds(snapshot)
                for view, (_object, snapshot) in zip(
                    fit["views"],
                    projected,
                    strict=True,
                )
            }
            layout = _fit_projection_group_layout(
                initial_bounds,
                convention=str(fit["convention"]),
                page_width_mm=float(fit["page_width_mm"]),
                page_height_mm=float(fit["page_height_mm"]),
                spacing_x_mm=float(fit["spacing_x_mm"]),
                spacing_y_mm=float(fit["spacing_y_mm"]),
                drawable_bounds_mm=tuple(float(value) for value in fit["drawable_bounds_mm"]),
            )
            scale = float(layout["scale"])
            if not math.isclose(scale, 1.0, rel_tol=0.0, abs_tol=1.0e-12):
                refreshed = []
                for (view, _snapshot), descriptor in zip(
                    projected,
                    projection_descriptors,
                    strict=True,
                ):
                    view.Scale = scale
                    view.touch()
                    if document.recompute([*view.Source, view, page], True, True) is False:
                        _fail(
                            "NATIVE_DRAWING_PROJECTION_EXECUTION_FAILED",
                            "TechDraw rejected the fitted projection scale.",
                        )
                    try:
                        snapshot = view.getPrecomputedProjection()
                    except Exception as exc:
                        raise _ChildFailure(
                            "NATIVE_DRAWING_PROJECTION_EMPTY",
                            "TechDraw produced no fitted projected geometry.",
                        ) from exc
                    refreshed.append((view, snapshot))
                projected = refreshed
            for view_name, (_object, snapshot) in zip(
                fit["views"],
                projected,
                strict=True,
            ):
                initial = initial_bounds[view_name]
                final = _snapshot_bounds(snapshot)
                expected_width = (initial[2] - initial[0]) * scale
                expected_height = (initial[3] - initial[1]) * scale
                if (
                    not math.isclose(
                        final[2] - final[0],
                        expected_width,
                        rel_tol=1.0e-7,
                        abs_tol=1.0e-7,
                    )
                    or not math.isclose(
                        final[3] - final[1],
                        expected_height,
                        rel_tol=1.0e-7,
                        abs_tol=1.0e-7,
                    )
                ):
                    _fail(
                        "NATIVE_DRAWING_PROJECTION_OUTPUT_INVALID",
                        "TechDraw changed the fitted projection extents.",
                    )
        projections = [
            _projection_result(descriptor, snapshot, root, index)
            for index, (descriptor, (_view, snapshot)) in enumerate(
                zip(projection_descriptors, projected, strict=True)
            )
        ]
        return {
            "ok": True,
            "protocol": _PROTOCOL,
            "request_sha256": request_sha256,
            "projections": projections,
            "layout": layout,
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
