# SPDX-License-Identifier: LGPL-2.1-or-later

"""Bounded, read-only serialization of one live Sketcher sketch."""

from __future__ import annotations

import json
import math
from types import SimpleNamespace
from typing import Any, Callable, Iterator, Mapping

from VibeCADNativeTargets import object_reference


MAX_GEOMETRY_DETAILS = 48
MAX_CONSTRAINT_DETAILS = 64
MAX_EXTERNAL_REFERENCES = 32
MAX_EXTERNAL_GEOMETRY_DETAILS = 32
MAX_DIAGNOSTIC_INDICES = 32
MAX_PROFILE_DETAILS = 16
MAX_CURVE_VALUES = 24
MAX_CONSTRAINT_REFERENCES = 8
MAX_SERIALIZED_SKETCH_STATE_BYTES = 52 * 1024
_GEO_UNDEFINED = -2000
_EXTERNAL_GEOMETRY_FIRST_INDEX = -3

_DIMENSIONAL_CONSTRAINTS = {
    "Angle",
    "AngleViaPoint",
    "Diameter",
    "Distance",
    "DistanceX",
    "DistanceY",
    "Radius",
    "SnellsLaw",
    "Text",
    "Weight",
}
_EXTERNAL_KINDS = {
    0: "projection",
    1: "intersection",
    2: "projection_and_intersection",
}


def _read(value: Any, name: str, default: Any = None) -> Any:
    try:
        return getattr(value, name)
    except Exception:
        return default


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError, OverflowError):
        return default


def _number(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(result):
        return None
    if abs(result) < 1.0e-14:
        return 0.0
    return round(result, 12)


def _text(value: Any, limit: int = 160) -> str:
    return str(value or "").strip()[:limit]


def _profile_intent(sketch: Any) -> dict[str, str] | None:
    raw = _read(sketch, "VibeCADProfileIntent", {})
    if not isinstance(raw, Mapping):
        return None
    keys = ("kind", "global_axis", "sketch_axis", "axial", "radius", "axis")
    result = {key: _text(raw.get(key), 32) for key in keys}
    if result["kind"] != "axisymmetric" or not all(result.values()):
        return None
    return result


def _vector(value: Any) -> list[float] | None:
    if value is None:
        return None
    if isinstance(value, (list, tuple)) and len(value) in {2, 3}:
        raw = [*value, 0.0] if len(value) == 2 else list(value)
        converted = [_number(item) for item in raw]
        return converted if all(item is not None for item in converted) else None
    coordinates = []
    for lower, upper in (("x", "X"), ("y", "Y"), ("z", "Z")):
        coordinate = _number(_read(value, lower, _read(value, upper)))
        if coordinate is None:
            return None
        coordinates.append(coordinate)
    return coordinates


def _bounded_call(value: Any, name: str) -> tuple[list[Any], bool]:
    method = _read(value, name)
    if not callable(method):
        return [], False
    try:
        raw = list(method() or [])
    except Exception:
        return [], False
    return raw[:MAX_CURVE_VALUES], len(raw) > MAX_CURVE_VALUES


def _bool_call(value: Any, name: str) -> bool | None:
    method = _read(value, name)
    if not callable(method):
        return None
    try:
        return bool(method())
    except Exception:
        return None


def _flag_call(value: Any, flag: str) -> bool | None:
    method = _read(value, "testFlag")
    if not callable(method):
        return None
    try:
        return bool(method(flag))
    except Exception:
        return None


def _geometry_type(geometry: Any) -> str:
    type_id = _text(_read(geometry, "TypeId"), 128)
    if type_id:
        return type_id
    cls = type(geometry)
    module = str(getattr(cls, "__module__", "") or "")
    name = str(getattr(cls, "__name__", "") or "")
    return f"{module}.{name}".strip(".")[:128] or "unknown"


def _geometry_kind(type_id: str) -> str:
    lowered = type_id.lower()
    for fragment, kind in (
        ("arcofhyperbola", "hyperbolic_arc"),
        ("arcofparabola", "parabolic_arc"),
        ("arcofellipse", "elliptical_arc"),
        ("arcofcircle", "circular_arc"),
        ("linesegment", "line"),
        ("bspline", "b_spline"),
        ("bezier", "bezier"),
        ("hyperbola", "hyperbola"),
        ("parabola", "parabola"),
        ("ellipse", "ellipse"),
        ("circle", "circle"),
        ("point", "point"),
    ):
        if fragment in lowered:
            return kind
    return "curve"


def _add_vector(result: dict[str, Any], key: str, value: Any) -> None:
    converted = _vector(value)
    if converted is not None:
        result[key] = converted


def _add_number(result: dict[str, Any], key: str, value: Any) -> None:
    converted = _number(value)
    if converted is not None:
        result[key] = converted


def _add_curve_sequence(
    result: dict[str, Any],
    geometry: Any,
    *,
    method: str,
    key: str,
    converter: Callable[[Any], Any],
) -> None:
    values, truncated = _bounded_call(geometry, method)
    converted = [converter(value) for value in values]
    converted = [value for value in converted if value is not None]
    if converted:
        result[key] = converted
    if truncated:
        result[f"{key}_truncated"] = True


def _serialize_geometry_shape(geometry: Any, kind: str) -> dict[str, Any]:
    result: dict[str, Any] = {}
    if kind == "point":
        point = [_number(_read(geometry, axis)) for axis in ("X", "Y", "Z")]
        if all(value is not None for value in point):
            result["position_mm"] = point

    _add_vector(result, "start_mm", _read(geometry, "StartPoint"))
    _add_vector(result, "end_mm", _read(geometry, "EndPoint"))
    _add_vector(
        result,
        "center_mm",
        _read(geometry, "Location", _read(geometry, "Center")),
    )
    _add_vector(result, "axis", _read(geometry, "Axis"))
    _add_vector(result, "x_axis", _read(geometry, "XAxis"))

    for attribute, key in (
        ("Radius", "radius_mm"),
        ("MajorRadius", "major_radius_mm"),
        ("MinorRadius", "minor_radius_mm"),
        ("Focal", "focal_length_mm"),
        ("FirstParameter", "first_parameter"),
        ("LastParameter", "last_parameter"),
        ("AngleXU", "x_axis_angle_radians"),
    ):
        if _read(geometry, attribute) is not None:
            _add_number(result, key, _read(geometry, attribute))

    for attribute, key in (
        ("Degree", "degree"),
        ("NbPoles", "pole_count"),
        ("NbKnots", "knot_count"),
    ):
        raw = _read(geometry, attribute)
        if raw is not None:
            result[key] = _integer(raw)

    for method, key in (
        ("isRational", "rational"),
        ("isPeriodic", "periodic"),
        ("isClosed", "closed"),
    ):
        flag = _bool_call(geometry, method)
        if flag is not None:
            result[key] = flag

    _add_curve_sequence(
        result,
        geometry,
        method="getPoles",
        key="poles_mm",
        converter=_vector,
    )
    _add_curve_sequence(
        result,
        geometry,
        method="getWeights",
        key="weights",
        converter=_number,
    )
    _add_curve_sequence(
        result,
        geometry,
        method="getKnots",
        key="knots",
        converter=_number,
    )
    _add_curve_sequence(
        result,
        geometry,
        method="getMultiplicities",
        key="multiplicities",
        converter=lambda value: _integer(value),
    )
    return result


def _geometry_id(sketch: Any, facade: Any, index: int) -> int | None:
    getter = _read(sketch, "getGeometryId")
    if callable(getter):
        try:
            result = int(getter(index))
            if result >= 0:
                return result
        except Exception:
            pass
    result = _integer(_read(facade, "Id"), -1)
    return result if result >= 0 else None


def _construction(sketch: Any, facade: Any, index: int) -> bool:
    value = _read(facade, "Construction")
    if value is not None:
        return bool(value)
    method = _read(sketch, "getConstruction")
    if callable(method):
        try:
            return bool(method(index))
        except Exception:
            pass
    return False


def _geometry_record(
    sketch: Any,
    facade: Any,
    geometry: Any,
    index: int,
) -> dict[str, Any]:
    type_id = _geometry_type(geometry)
    result: dict[str, Any] = {
        "index": index,
        "type_id": type_id,
        "kind": _geometry_kind(type_id),
        "construction": _construction(sketch, facade, index),
        "blocked": bool(_read(facade, "Blocked", False)),
    }
    stable_id = _geometry_id(sketch, facade, index)
    if stable_id is not None:
        result["geometry_id"] = stable_id
    internal_type = _text(_read(facade, "InternalType"), 96)
    if internal_type and internal_type != "None":
        result["internal_type"] = internal_type
    layer = _integer(_read(facade, "GeometryLayerId"), 0)
    if layer:
        result["layer_id"] = layer
    tag = _text(_read(facade, "Tag", _read(geometry, "Tag")), 128)
    if tag:
        result["tag"] = tag
    result.update(_serialize_geometry_shape(geometry, result["kind"]))
    return result


def serialize_sketch_geometry_value(
    geometry: Any,
    index: int,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Serialize detached diagnostic geometry with exact Sketch facade metadata."""

    fields = {
        "Id",
        "Construction",
        "Blocked",
        "InternalType",
        "GeometryLayerId",
    }
    if type(index) is not int or index < 0:
        raise IndexError("Diagnostic Sketch geometry index must be non-negative.")
    if not isinstance(metadata, Mapping) or set(metadata) != fields:
        raise ValueError("Diagnostic Sketch geometry metadata is incomplete.")
    facade = SimpleNamespace(Geometry=geometry, **dict(metadata))
    return _geometry_record(None, facade, geometry, index)


def _geometry_from_sources(
    sketch: Any,
    index: int,
    raw_geometry: Any,
    facades: Any,
) -> dict[str, Any]:
    try:
        facade = facades[index]
    except (IndexError, KeyError, TypeError):
        facade = None
    geometry = _read(facade, "Geometry")
    if geometry is None:
        try:
            geometry = raw_geometry[index]
        except (IndexError, KeyError, TypeError) as exc:
            raise RuntimeError(
                "Sketch geometry is unavailable at its current index."
            ) from exc
    return _geometry_record(sketch, facade, geometry, index)


def iter_sketch_geometry_records(
    sketch: Any,
    count: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate exact current geometry records with one host-property read."""

    raw_geometry = _read(sketch, "Geometry", []) or []
    facades = _read(sketch, "GeometryFacadeList", []) or []
    actual_count = _integer(_read(sketch, "GeometryCount"), len(raw_geometry))
    requested = actual_count if count is None else count
    if type(requested) is not int or not 0 <= requested <= actual_count:
        raise IndexError("Sketch geometry count is outside the current sketch.")
    for index in range(requested):
        yield _geometry_from_sources(sketch, index, raw_geometry, facades)


def serialize_sketch_geometry(sketch: Any, index: int) -> dict[str, Any]:
    """Return one exact geometry record by its current zero-based index."""

    if type(index) is not int or index < 0:
        raise IndexError("Sketch geometry index must be a non-negative integer.")
    records = iter_sketch_geometry_records(sketch, index + 1)
    for _offset in range(index):
        next(records)
    return next(records)


def _geometry_records(sketch: Any) -> tuple[list[dict[str, Any]], int]:
    raw_geometry = list(_read(sketch, "Geometry", []) or [])
    facades = _read(sketch, "GeometryFacadeList", []) or []
    count = _integer(_read(sketch, "GeometryCount"), len(raw_geometry))
    details = []
    for index in range(min(count, MAX_GEOMETRY_DETAILS)):
        try:
            details.append(_geometry_from_sources(sketch, index, raw_geometry, facades))
        except (IndexError, RuntimeError):
            details.append({"index": index, "kind": "unavailable"})
    return details, count


def _constraint_record(constraint: Any, index: int) -> dict[str, Any]:
    constraint_type = _text(_read(constraint, "Type"), 96) or "Unknown"
    result: dict[str, Any] = {
        "index": index,
        "type": constraint_type,
        "driving": bool(_read(constraint, "Driving", False)),
        "active": bool(_read(constraint, "IsActive", True)),
        "virtual": bool(_read(constraint, "InVirtualSpace", False)),
    }
    references = []
    for slot, prefix in enumerate(("First", "Second", "Third"), start=1):
        geometry_index = _integer(_read(constraint, prefix), _GEO_UNDEFINED)
        if geometry_index <= _GEO_UNDEFINED:
            continue
        reference = {"slot": slot, "geometry_index": geometry_index}
        position = _integer(_read(constraint, f"{prefix}Pos"), 0)
        if position:
            reference["position"] = position
        references.append(reference)
    if references:
        result["references"] = references
    if constraint_type in {"Group", "Text"}:
        raw_elements = list(_read(constraint, "Elements", []) or [])
        elements = []
        for raw in raw_elements[:MAX_CONSTRAINT_REFERENCES]:
            if not isinstance(raw, (list, tuple)) or len(raw) != 2:
                continue
            elements.append(
                {
                    "geometry_index": _integer(raw[0], _GEO_UNDEFINED),
                    "position": _integer(raw[1]),
                }
            )
        result["element_count"] = len(raw_elements)
        result["elements"] = elements
        if len(raw_elements) > len(elements):
            result["elements_truncated"] = True
    if constraint_type == "Text":
        result["text"] = str(_read(constraint, "Text", "") or "")[:160]
        result["font_name"] = _text(_read(constraint, "Font"), 128)
        result["sizing_mode"] = (
            "height" if bool(_read(constraint, "IsTextHeight", True)) else "width"
        )
    value = _number(_read(constraint, "Value"))
    if value is not None and (
        constraint_type in _DIMENSIONAL_CONSTRAINTS or value != 0.0
    ):
        result["value"] = value
    if constraint_type in _DIMENSIONAL_CONSTRAINTS and constraint_type != "Text":
        label_distance = _number(_read(constraint, "LabelDistance"))
        label_position = _number(_read(constraint, "LabelPosition"))
        if label_distance is not None:
            result["label_distance"] = label_distance
        if label_position is not None:
            result["label_position"] = label_position
    name = _text(_read(constraint, "Name"), 128)
    if name:
        result["name"] = name
    return result


def serialize_sketch_constraint_value(
    constraint: Any,
    index: int,
) -> dict[str, Any]:
    """Serialize one detached diagnostic constraint with the live record contract."""

    if type(index) is not int or index < 0:
        raise IndexError("Diagnostic Sketch constraint index must be non-negative.")
    return _constraint_record(constraint, index)


def iter_sketch_constraint_records(
    sketch: Any,
    count: int | None = None,
) -> Iterator[dict[str, Any]]:
    """Iterate exact current constraint records with one host-property read."""

    constraints = _read(sketch, "Constraints", []) or []
    actual_count = _integer(_read(sketch, "ConstraintCount"), len(constraints))
    requested = actual_count if count is None else count
    if type(requested) is not int or not 0 <= requested <= actual_count:
        raise IndexError("Sketch constraint count is outside the current sketch.")
    for index in range(requested):
        try:
            constraint = constraints[index]
        except (IndexError, KeyError, TypeError) as exc:
            raise RuntimeError(
                "Sketch constraint is unavailable at its current index."
            ) from exc
        yield _constraint_record(constraint, index)


def serialize_sketch_constraint(sketch: Any, index: int) -> dict[str, Any]:
    """Return one exact constraint record by its current zero-based index."""

    if type(index) is not int or index < 0:
        raise IndexError("Sketch constraint index must be a non-negative integer.")
    records = iter_sketch_constraint_records(sketch, index + 1)
    for _offset in range(index):
        next(records)
    return next(records)


def _constraint_records(sketch: Any) -> tuple[list[dict[str, Any]], int]:
    constraints = list(_read(sketch, "Constraints", []) or [])
    total = _integer(_read(sketch, "ConstraintCount"), len(constraints))
    return [
        _constraint_record(value, index)
        for index, value in enumerate(constraints[:MAX_CONSTRAINT_DETAILS])
    ], total


def _external_geometry_index_map(sketch: Any) -> tuple[dict[str, list[int]], int]:
    native_geometry = list(_read(sketch, "ExternalGeo", []) or [])
    result: dict[str, list[int]] = {}
    for raw_index, geometry in enumerate(native_geometry[2:], start=2):
        method = _read(geometry, "getExtensionOfType")
        extension = None
        if callable(method):
            try:
                extension = method("Sketcher::ExternalGeometryExtension")
            except Exception:
                extension = None
        reference = _text(_read(extension, "Ref"), 256)
        if reference:
            result.setdefault(reference, []).append(-raw_index - 1)
    return result, max(0, len(native_geometry) - 2)


def _external_geometry_record(geometry: Any, raw_index: int) -> dict[str, Any]:
    geometry_index = -raw_index - 1
    method = _read(geometry, "getExtensionOfType")
    extension = None
    if callable(method):
        try:
            extension = method("Sketcher::ExternalGeometryExtension")
        except Exception:
            extension = None
    type_id = _geometry_type(geometry)
    result: dict[str, Any] = {
        "geometry_index": geometry_index,
        "type_id": type_id,
        "kind": _geometry_kind(type_id),
    }
    defining = _flag_call(extension, "Defining")
    if defining is not None:
        result["defining"] = defining
    reference = _text(_read(extension, "Ref"), 256)
    if reference:
        result["reference"] = reference
    for flag, key in (
        ("Frozen", "frozen"),
        ("Detached", "detached"),
        ("Missing", "missing"),
        ("Sync", "synchronized"),
    ):
        value = _flag_call(extension, flag)
        if value is not None:
            result[key] = value
    result.update(_serialize_geometry_shape(geometry, result["kind"]))
    return result


def iter_sketch_external_geometry_records(sketch: Any) -> Iterator[dict[str, Any]]:
    """Iterate exact durable external geometry records, excluding the two axes."""

    native_geometry = list(_read(sketch, "ExternalGeo", []) or [])
    for raw_index, geometry in enumerate(native_geometry[2:], start=2):
        yield _external_geometry_record(geometry, raw_index)


def serialize_sketch_external_geometry(sketch: Any, index: int) -> dict[str, Any]:
    """Return one exact external geometry record by its negative Sketch index."""

    if type(index) is not int or index > _EXTERNAL_GEOMETRY_FIRST_INDEX:
        raise IndexError("External Sketch geometry index must be -3 or lower.")
    raw_index = -index - 1
    native_geometry = list(_read(sketch, "ExternalGeo", []) or [])
    if raw_index < 2 or raw_index >= len(native_geometry):
        raise IndexError(
            "External Sketch geometry index is outside the current sketch."
        )
    return _external_geometry_record(native_geometry[raw_index], raw_index)


def serialize_sketch_external_geometry_value(
    geometry: Any,
    index: int,
    metadata: Mapping[str, Any],
) -> dict[str, Any]:
    """Serialize detached projected geometry with exact external-link metadata."""

    fields = {
        "reference",
        "defining",
        "frozen",
        "detached",
        "missing",
        "synchronized",
    }
    if type(index) is not int or index > _EXTERNAL_GEOMETRY_FIRST_INDEX:
        raise IndexError("Diagnostic external geometry index must be -3 or lower.")
    if not isinstance(metadata, Mapping) or set(metadata) != fields:
        raise ValueError("Diagnostic external geometry metadata is incomplete.")
    type_id = _geometry_type(geometry)
    result: dict[str, Any] = {
        "geometry_index": index,
        "type_id": type_id,
        "kind": _geometry_kind(type_id),
        **dict(metadata),
    }
    result.update(_serialize_geometry_shape(geometry, result["kind"]))
    return result


def _external_references(sketch: Any) -> tuple[list[dict[str, Any]], int, int]:
    document = _read(sketch, "Document")
    external_types = list(_read(sketch, "ExternalTypes", []) or [])
    index_map, native_count = _external_geometry_index_map(sketch)
    candidates = []
    for raw in list(_read(sketch, "ExternalGeometry", []) or []):
        if not isinstance(raw, (list, tuple)) or len(raw) < 2:
            continue
        obj = raw[0]
        raw_names = raw[1]
        names = [raw_names] if isinstance(raw_names, str) else list(raw_names or [])
        for raw_name in names:
            subelement = _text(raw_name, 128)
            candidates.append((obj, subelement))

    records = []
    use_ordered_fallback = not index_map and native_count == len(candidates)
    for external_index, (obj, subelement) in enumerate(candidates):
        if len(records) >= MAX_EXTERNAL_REFERENCES:
            continue
        record: dict[str, Any] = {
            "reference_index": external_index,
            "subelement": subelement,
        }
        object_name = _text(_read(obj, "Name"), 128)
        native_indices = list(index_map.get(f"{object_name}.{subelement}", []))
        if use_ordered_fallback:
            native_indices = [_EXTERNAL_GEOMETRY_FIRST_INDEX - external_index]
        if native_indices:
            record["geometry_indices"] = native_indices[:MAX_DIAGNOSTIC_INDICES]
            if len(native_indices) > MAX_DIAGNOSTIC_INDICES:
                record["geometry_indices_truncated"] = True
        if _read(obj, "Document") is document:
            record["object"] = object_reference(obj)
        else:
            record["object_unavailable"] = True
        if external_index < len(external_types):
            raw_type = _integer(external_types[external_index], -1)
            record["kind"] = _EXTERNAL_KINDS.get(raw_type, "unknown")
        records.append(record)
    return records, len(candidates), native_count


def _link_subelement_records(value: Any, document: Any) -> list[dict[str, Any]]:
    raw_values = list(value or []) if isinstance(value, list) else [value]
    result = []
    for raw in raw_values:
        if not isinstance(raw, (list, tuple)) or not raw:
            continue
        obj = raw[0]
        if _read(obj, "Document") is not document:
            continue
        raw_names = raw[1] if len(raw) > 1 else []
        names = [raw_names] if isinstance(raw_names, str) else list(raw_names or [])
        result.append(
            {
                "object": object_reference(obj),
                "subelements": [
                    _text(name, 128) for name in names[:8] if _text(name, 128)
                ],
            }
        )
        if len(result) >= 8:
            break
    return result


def _attachment(sketch: Any) -> dict[str, Any]:
    document = _read(sketch, "Document")
    raw_support = _read(sketch, "AttachmentSupport")
    if not raw_support:
        raw_support = _read(sketch, "Support")
    result: dict[str, Any] = {
        "map_mode": _text(_read(sketch, "MapMode"), 96) or "Deactivated",
        "support": _link_subelement_records(raw_support, document),
    }
    offset = _read(sketch, "AttachmentOffset")
    if offset is not None:
        placement: dict[str, Any] = {}
        _add_vector(placement, "origin_mm", _read(offset, "Base"))
        rotation = _read(offset, "Rotation")
        quaternion = list(_read(rotation, "Q", []) or [])
        converted = [_number(value) for value in quaternion[:4]]
        if len(converted) == 4 and all(value is not None for value in converted):
            placement["rotation_xyzw"] = converted
        if placement:
            result["offset"] = placement
    return result


def _global_profile_plane(sketch: Any) -> dict[str, Any]:
    placement = None
    global_placement = _read(sketch, "getGlobalPlacement")
    if callable(global_placement):
        try:
            placement = global_placement()
        except Exception:
            placement = None
    if placement is None:
        placement = _read(sketch, "Placement")
    origin = _vector(_read(placement, "Base"))
    quaternion = list(_read(_read(placement, "Rotation"), "Q", []) or [])[:4]
    converted = [_number(value) for value in quaternion]
    if origin is None or len(converted) != 4 or any(value is None for value in converted):
        return {}
    x, y, z, w = (float(value) for value in converted)
    magnitude = math.sqrt(x * x + y * y + z * z + w * w)
    if magnitude <= 1.0e-15:
        return {}
    x, y, z, w = (value / magnitude for value in (x, y, z, w))
    matrix = (
        (1.0 - 2.0 * (y * y + z * z), 2.0 * (x * y - z * w), 2.0 * (x * z + y * w)),
        (2.0 * (x * y + z * w), 1.0 - 2.0 * (x * x + z * z), 2.0 * (y * z - x * w)),
        (2.0 * (x * z - y * w), 2.0 * (y * z + x * w), 1.0 - 2.0 * (x * x + y * y)),
    )

    def column(index: int) -> list[float]:
        return [float(_number(matrix[row][index]) or 0.0) for row in range(3)]

    return {
        "space": "global",
        "origin_mm": origin,
        "x_direction": column(0),
        "y_direction": column(1),
        "normal": column(2),
    }


def _bounded_indices(value: Any) -> tuple[list[int], bool]:
    raw = sorted({_integer(item) for item in list(value or [])})
    return raw[:MAX_DIAGNOSTIC_INDICES], len(raw) > MAX_DIAGNOSTIC_INDICES


def _solver_state(sketch: Any) -> dict[str, Any]:
    result: dict[str, Any] = {
        "degrees_of_freedom": _integer(_read(sketch, "DoF")),
        "fully_constrained": bool(_read(sketch, "FullyConstrained", False)),
    }
    for attribute, key in (
        ("ConflictingConstraints", "conflicting_constraints"),
        ("RedundantConstraints", "redundant_constraints"),
        ("PartiallyRedundantConstraints", "partially_redundant_constraints"),
        ("MalformedConstraints", "malformed_constraints"),
    ):
        indices, truncated = _bounded_indices(_read(sketch, attribute, []))
        result[key] = indices
        if truncated:
            result[f"{key}_truncated"] = True
    vertices = []
    raw_vertices = list(_read(sketch, "OpenVertices", []) or [])
    for value in raw_vertices[:MAX_PROFILE_DETAILS]:
        converted = _vector(value)
        if converted is not None:
            vertices.append(converted)
    result["open_vertices_mm"] = vertices
    if len(raw_vertices) > MAX_PROFILE_DETAILS:
        result["open_vertices_truncated"] = True
    try:
        result["valid"] = bool(sketch.isValid())
    except Exception:
        result["valid"] = False
    status_method = _read(sketch, "getStatusString")
    if callable(status_method):
        try:
            status = " ".join(str(status_method() or "").split())[:240]
        except Exception:
            status = ""
        if status:
            result["status"] = status
    return result


def _profile_from_shape(sketch: Any) -> dict[str, Any]:
    shape = _read(sketch, "Shape")
    wires = list(_read(shape, "Wires", []) or []) if shape is not None else []
    closed_count = 0
    for wire in wires:
        try:
            closed_count += int(bool(wire.isClosed()))
        except Exception:
            continue
    return {
        "wire_count": len(wires),
        "closed_wire_count": closed_count,
        "open_wire_count": len(wires) - closed_count,
        "face_count": len(list(_read(shape, "Faces", []) or []))
        if shape is not None
        else 0,
        "closed_profile": bool(wires and closed_count == len(wires)),
    }


def _profile_state(sketch: Any) -> dict[str, Any]:
    method = _read(sketch, "getProfileDiagnostics")
    try:
        diagnostics = dict(method() or {}) if callable(method) else {}
    except Exception:
        diagnostics = {}
    if not diagnostics:
        return _profile_from_shape(sketch)

    wire_count = _integer(diagnostics.get("wire_count"))
    closed_count = _integer(diagnostics.get("closed_wire_count"))
    result: dict[str, Any] = {
        "wire_count": wire_count,
        "closed_wire_count": closed_count,
        "open_wire_count": max(0, wire_count - closed_count),
        "face_count": _integer(diagnostics.get("face_count")),
        "face_buildable_wire_count": _integer(
            diagnostics.get("face_buildable_wire_count")
        ),
        "face_maker_succeeded": bool(diagnostics.get("face_maker_succeeded", False)),
        "closed_profile": bool(wire_count and wire_count == closed_count),
    }
    status = _text(diagnostics.get("face_maker_status"), 160)
    if status:
        result["face_maker_status"] = status
    global_plane = _global_profile_plane(sketch)
    plane = diagnostics.get("support_plane")
    if global_plane:
        result["support_plane"] = global_plane
    elif isinstance(plane, str):
        result["support_plane"] = _text(plane, 96)
    elif isinstance(plane, dict):
        support_plane = {}
        for source_key, result_key in (
            ("origin", "origin_mm"),
            ("normal", "normal"),
            ("x_direction", "x_direction"),
            ("y_direction", "y_direction"),
        ):
            _add_vector(support_plane, result_key, plane.get(source_key))
        if support_plane:
            result["support_plane"] = support_plane

    raw_wires = list(diagnostics.get("wires") or [])
    invalid_wires = []
    open_wires = []
    for fallback_index, wire in enumerate(raw_wires):
        if not isinstance(wire, dict):
            continue
        wire_index = _integer(wire.get("wire_index"), fallback_index)
        if not bool(wire.get("brep_valid", False)):
            invalid_wires.append(wire_index)
        if bool(wire.get("closed", False)):
            continue
        detail: dict[str, Any] = {"wire_index": wire_index}
        _add_vector(detail, "start_mm", wire.get("open_start"))
        _add_vector(detail, "end_mm", wire.get("open_end"))
        _add_number(detail, "closure_gap_mm", wire.get("closure_gap"))
        if len(open_wires) < MAX_PROFILE_DETAILS:
            open_wires.append(detail)
    result["invalid_wire_indices"] = invalid_wires[:MAX_DIAGNOSTIC_INDICES]
    result["open_wires"] = open_wires
    if len(invalid_wires) > MAX_DIAGNOSTIC_INDICES:
        result["invalid_wire_indices_truncated"] = True
    if result["open_wire_count"] > len(open_wires):
        result["open_wires_truncated"] = True

    raw_faces = list(diagnostics.get("faces") or [])
    invalid_faces = [
        _integer(face.get("face_index"), index)
        for index, face in enumerate(raw_faces)
        if isinstance(face, dict) and not bool(face.get("brep_valid", False))
    ]
    result["invalid_face_indices"] = invalid_faces[:MAX_DIAGNOSTIC_INDICES]
    if len(invalid_faces) > MAX_DIAGNOSTIC_INDICES:
        result["invalid_face_indices_truncated"] = True
    return result


def serialize_sketch_state(sketch: Any) -> dict[str, Any]:
    """Return the exact bounded read state for the human-opened sketch."""

    geometry, geometry_count = _geometry_records(sketch)
    constraints, constraint_count = _constraint_records(sketch)
    external, external_count, external_geometry_count = _external_references(sketch)
    external_geometry = list(iter_sketch_external_geometry_records(sketch))[
        :MAX_EXTERNAL_GEOMETRY_DETAILS
    ]
    construction_count = sum(
        int(_construction(sketch, None, index)) for index in range(geometry_count)
    )
    result = {
        "geometry_count": geometry_count,
        "geometry": geometry,
        "geometry_truncated": geometry_count > len(geometry),
        "construction_geometry_count": construction_count,
        "constraint_count": constraint_count,
        "constraints": constraints,
        "constraints_truncated": constraint_count > len(constraints),
        "external_reference_count": external_count,
        "external_geometry_count": external_geometry_count,
        "external_geometry": external_geometry,
        "external_geometry_truncated": external_geometry_count > len(external_geometry),
        "external_references": external,
        "external_references_truncated": external_count > len(external),
        "attachment": _attachment(sketch),
        **serialize_sketch_diagnostics(sketch),
    }
    profile_intent = _profile_intent(sketch)
    if profile_intent is not None:
        result["profile_intent"] = profile_intent
    _enforce_state_bound(result)
    return result


def serialize_sketch_diagnostics(sketch: Any) -> dict[str, Any]:
    """Return the compact profile and solver postcondition for one sketch."""

    return {
        "profile": _profile_state(sketch),
        "solver": _solver_state(sketch),
    }


def _encoded_size(value: Any) -> int:
    return len(
        json.dumps(
            value, ensure_ascii=True, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _enforce_state_bound(result: dict[str, Any]) -> None:
    collections = (
        ("geometry", "geometry_count", "geometry_truncated"),
        ("constraints", "constraint_count", "constraints_truncated"),
        (
            "external_references",
            "external_reference_count",
            "external_references_truncated",
        ),
        (
            "external_geometry",
            "external_geometry_count",
            "external_geometry_truncated",
        ),
    )
    while _encoded_size(result) > MAX_SERIALIZED_SKETCH_STATE_BYTES:
        candidates = [
            (key, _encoded_size(result[key][-1]))
            for key, _count_key, _truncated_key in collections
            if result[key]
        ]
        if not candidates:
            break
        largest_key = max(candidates, key=lambda value: value[1])[0]
        result[largest_key].pop()
    if _encoded_size(result) > MAX_SERIALIZED_SKETCH_STATE_BYTES:
        raise RuntimeError("The fixed Sketch state fields exceed their byte budget.")
    for key, count_key, truncated_key in collections:
        result[truncated_key] = int(result[count_key]) > len(result[key])
