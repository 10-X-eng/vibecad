# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated OCC executor for the production Part VibeScript API."""

from __future__ import annotations

from contextvars import ContextVar
import hashlib
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any, Callable, Mapping


class PartOperationError(ValueError):
    """Model-facing Part operation failure with precise operation context."""

    def __init__(
        self,
        message: str,
        *,
        stage: str = "part_operation",
        operation: str = "",
        parameter: str = "",
        correction: str = "",
        observed: Mapping[str, Any] | None = None,
    ) -> None:
        self.details = {
            "stage": str(stage or "part_operation"),
            **({"operation": str(operation)} if operation else {}),
            **({"parameter": str(parameter)} if parameter else {}),
            **({"observed": dict(observed)} if observed else {}),
            "correction": str(correction or "Inspect the rejected Part operation and change the exact stated cause before retrying."),
        }
        super().__init__(str(message))


MAX_SUBELEMENT_FACTS = 256
_REFERENCE_SHAPES: Mapping[tuple[str, str], Any] = MappingProxyType({})
_NESTED_SHAPE_RESOLVER: ContextVar[
    Callable[[dict[str, Any]], Any] | None
] = ContextVar("vibescript_part_nested_shape_resolver", default=None)


def configure_part_references(root: Path, entries: list[dict[str, Any]]) -> None:
    """Load and authenticate host-staged BREP snapshots for one worker request."""

    import Part

    resolved_root = Path(root).resolve()
    shapes: dict[tuple[str, str], Any] = {}
    for index, entry in enumerate(entries):
        if not isinstance(entry, dict):
            raise ValueError(f"document_references[{index}] must be an object.")
        document_uid = str(entry.get("document_uid") or "")
        object_name = str(entry.get("object_name") or "")
        key = (document_uid, object_name)
        if not all(key) or key in shapes:
            raise ValueError(
                f"document_references[{index}] has missing or duplicate identity."
            )
        path = (resolved_root / str(entry.get("artifact_path") or "")).resolve()
        if resolved_root not in path.parents or not path.is_file():
            raise ValueError(
                f"document_references[{index}] BREP is missing or outside worker staging."
            )
        digest = hashlib.sha256()
        with path.open("rb") as handle:
            for chunk in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(chunk)
        expected_digest = str(entry.get("brep_sha256") or "")
        if digest.hexdigest() != expected_digest:
            raise ValueError(
                f"document_references[{index}] BREP SHA-256 does not match the host snapshot."
            )
        shape = Part.Shape()
        shape.importBrep(str(path))
        if shape.isNull() or not shape.isValid():
            raise ValueError(f"document_references[{index}] is not a valid BREP shape.")
        expected_shape_type = str(entry.get("shape_type") or "")
        if expected_shape_type and str(shape.ShapeType) != expected_shape_type:
            raise ValueError(
                f"document_references[{index}] changed shape type during transfer: "
                f"expected {expected_shape_type}, received {shape.ShapeType}."
            )
        shapes[key] = shape
    global _REFERENCE_SHAPES
    _REFERENCE_SHAPES = MappingProxyType(shapes)


def detached_reference_shape(reference: Mapping[str, Any]) -> Any:
    """Return a copy of one authenticated host-staged reference shape.

    Shared domain workers use this narrow accessor instead of reading BREP
    artifacts again or reaching into the Part worker's private registry.
    """

    if not isinstance(reference, Mapping) or set(reference) != {
        "document_uid",
        "object_name",
    }:
        raise ValueError(
            "A staged shape reference must contain exactly document_uid and object_name."
        )
    key = (
        str(reference.get("document_uid") or ""),
        str(reference.get("object_name") or ""),
    )
    shape = _REFERENCE_SHAPES.get(key)
    if shape is None:
        raise ValueError(
            f"Reference {key[1]!r} was not authenticated from this worker request."
        )
    return shape.copy()


def _point_fact(value: Any) -> list[float]:
    return [float(value.x), float(value.y), float(value.z)]


def _bounds_fact(shape: Any) -> dict[str, list[float]]:
    bounds = shape.BoundBox
    return {
        "min": [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)],
        "max": [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)],
        "size": [float(bounds.XLength), float(bounds.YLength), float(bounds.ZLength)],
    }


def _geometry_type(value: Any) -> str:
    name = type(value).__name__
    return name.removeprefix("Part.") or "Unknown"


def _topology_geometry_type(value: Any, attribute: str) -> str:
    """Return an explicit type for valid degenerate OCC topology.

    Seam and pole edges can be topologically valid while FreeCAD raises
    ``TypeError: undefined curve type`` when their Curve property is read.
    Domain context and validation must preserve that fact instead of rejecting
    the entire BREP.
    """

    try:
        geometry = getattr(value, attribute)
    except (AttributeError, RuntimeError, TypeError):
        return "Undefined"
    return _geometry_type(geometry)


def _face_fact(index: int, face: Any) -> dict[str, Any]:
    center = getattr(face, "CenterOfMass", None)
    item: dict[str, Any] = {
        "index": index,
        "surface_type": _topology_geometry_type(face, "Surface"),
        "orientation": str(getattr(face, "Orientation", "") or ""),
        "area_mm2": float(face.Area),
        "center_mm": _point_fact(center) if center is not None else None,
        "bounds_mm": _bounds_fact(face),
        "edge_count": len(list(face.Edges)),
        "wire_count": len(list(face.Wires)),
    }
    try:
        u_min, u_max, v_min, v_max = (float(value) for value in face.ParameterRange)
        normal = face.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
        item["normal_at_center"] = _point_fact(normal)
    except Exception:
        item["normal_at_center"] = None
    return item


def _edge_fact(index: int, edge: Any) -> dict[str, Any]:
    center = getattr(edge, "CenterOfMass", None)
    vertices = list(edge.Vertexes)
    return {
        "index": index,
        "curve_type": _topology_geometry_type(edge, "Curve"),
        "orientation": str(getattr(edge, "Orientation", "") or ""),
        "length_mm": float(edge.Length),
        "center_mm": _point_fact(center) if center is not None else None,
        "bounds_mm": _bounds_fact(edge),
        "endpoints_mm": [_point_fact(vertex.Point) for vertex in vertices[:2]],
        "closed": bool(edge.isClosed()),
    }


def part_shape_facts(
    shape: Any,
    *,
    max_subelements: int = MAX_SUBELEMENT_FACTS,
) -> dict[str, Any]:
    """Return bounded, JSON-safe topology facts for model inspection and selectors."""

    detail_limit = max(0, min(int(max_subelements), MAX_SUBELEMENT_FACTS))
    bounds = shape.BoundBox
    center = getattr(shape, "CenterOfMass", None)
    faces = list(getattr(shape, "Faces", []) or [])
    edges = list(getattr(shape, "Edges", []) or [])
    return {
        "shape_type": str(getattr(shape, "ShapeType", "") or ""),
        "valid": bool(shape.isValid()),
        "null": bool(shape.isNull()),
        "solids": len(list(getattr(shape, "Solids", []) or [])),
        "shells": len(list(getattr(shape, "Shells", []) or [])),
        "faces": len(faces),
        "wires": len(list(getattr(shape, "Wires", []) or [])),
        "edges": len(edges),
        "vertices": len(list(getattr(shape, "Vertexes", []) or [])),
        "length_mm": float(getattr(shape, "Length", 0.0) or 0.0),
        "area_mm2": float(getattr(shape, "Area", 0.0) or 0.0),
        "volume_mm3": float(getattr(shape, "Volume", 0.0) or 0.0),
        "center_of_mass_mm": _point_fact(center) if center is not None else None,
        "bounds_center_mm": [
            float((bounds.XMin + bounds.XMax) / 2.0),
            float((bounds.YMin + bounds.YMax) / 2.0),
            float((bounds.ZMin + bounds.ZMax) / 2.0),
        ],
        "bounds_mm": _bounds_fact(shape),
        "face_details": [
            _face_fact(index, face)
            for index, face in enumerate(faces[:detail_limit], start=1)
        ],
        "edge_details": [
            _edge_fact(index, edge)
            for index, edge in enumerate(edges[:detail_limit], start=1)
        ],
        "subelement_detail_limit": detail_limit,
        "subelement_details_truncated": bool(
            len(faces) > detail_limit or len(edges) > detail_limit
        ),
    }


def _error(operation: str, parameter: str, message: str) -> PartOperationError:
    selection_failure = parameter in {"edges", "faces", "index"} or "outside 1.." in message
    correction = (
        "Copy a valid 1-based selector from the latest accepted face_details or "
        "edge_details for this exact shape, then change only the rejected selector."
        if selection_failure
        else (
            f"Correct api.{operation} parameter {parameter!r} using the exact "
            "describe_api signature and current accepted shape facts, then retry "
            "against the failed working revision."
        )
    )
    return PartOperationError(
        f"api.{operation}: {parameter}: {message}",
        stage=("part_topology_selection" if selection_failure else "part_argument"),
        operation=operation,
        parameter=parameter,
        correction=correction,
    )


def _properties(payload: dict[str, Any]) -> dict[str, Any]:
    value = payload.get("properties")
    if not isinstance(value, dict):
        raise _error(
            str(payload.get("operation") or "<unknown>"), "properties", "must be an object"
        )
    return dict(value)


def _argument(
    payload: dict[str, Any],
    index: int,
    name: str,
    default: Any = None,
) -> Any:
    arguments = payload.get("arguments")
    if not isinstance(arguments, list):
        raise _error(str(payload.get("operation") or "<unknown>"), "arguments", "must be an array")
    if index < len(arguments):
        return arguments[index]
    return _properties(payload).get(name, default)


def _vector(operation: str, parameter: str, value: Any, *, nonzero: bool = False):
    import FreeCAD as App

    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise _error(operation, parameter, "expected [x, y, z]")
    try:
        result = App.Vector(*(float(item) for item in value))
    except (TypeError, ValueError) as exc:
        raise _error(operation, parameter, "coordinates must be finite numbers") from exc
    if not all(math.isfinite(item) for item in (result.x, result.y, result.z)):
        raise _error(operation, parameter, "coordinates must be finite numbers")
    if nonzero and result.Length <= 1.0e-12:
        raise _error(operation, parameter, "vector magnitude must be non-zero")
    return result


def _serialized(operation: str, parameter: str, value: Any) -> dict[str, Any]:
    required = {"domain", "operation", "output_type", "arguments", "properties"}
    if not isinstance(value, dict) or not required <= set(value):
        raise _error(operation, parameter, "expected a serialized Part api value")
    if value.get("domain") != "part":
        raise _error(operation, parameter, "value belongs to another VibeScript domain")
    return dict(value)


def _shape(operation: str, parameter: str, value: Any):
    serialized = _serialized(operation, parameter, value)
    resolver = _NESTED_SHAPE_RESOLVER.get()
    if resolver is not None:
        return resolver(serialized)
    return build_part_shape(serialized)


def _shape_list(
    operation: str,
    parameter: str,
    value: Any,
    *,
    minimum: int = 1,
) -> list[Any]:
    if not isinstance(value, list) or len(value) < minimum:
        raise _error(operation, parameter, f"expected at least {minimum} Part value(s)")
    return [_shape(operation, f"{parameter}[{index}]", item) for index, item in enumerate(value)]


def _indexed_subshapes(
    operation: str,
    parameter: str,
    shape: Any,
    requested: Any,
    collection_name: str,
) -> list[Any]:
    collection = list(getattr(shape, collection_name, []) or [])
    if requested == "all":
        if not collection:
            raise _error(operation, parameter, f"shape contains no {collection_name.lower()}")
        return collection
    if not isinstance(requested, list) or not requested:
        raise _error(operation, parameter, "expected 'all' or a non-empty array of indices")
    result = []
    for index in requested:
        if (
            not isinstance(index, int)
            or isinstance(index, bool)
            or index < 1
            or index > len(collection)
        ):
            raise _error(
                operation,
                parameter,
                f"index {index!r} is outside 1..{len(collection)} for this shape",
            )
        result.append(collection[index - 1])
    return result


def _refine(shape: Any, enabled: bool, *, operation: str) -> Any:
    if not enabled:
        return shape
    refined = shape.removeSplitter()
    if refined.isNull():
        raise PartOperationError(
            f"api.{operation}: refinement produced a null shape",
            stage="part_result_validation",
            operation=operation,
            correction=(
                "Set refine=False to inspect the unrefined boolean result, or repair "
                "the upstream overlap/tolerance that caused refinement to collapse it."
            ),
        )
    return refined


def _wire_from_shape(operation: str, parameter: str, shape: Any):
    import Part

    if str(getattr(shape, "ShapeType", "")) == "Wire":
        return shape
    edges = list(getattr(shape, "Edges", []) or [])
    if not edges:
        raise _error(operation, parameter, "value contains no edges")
    try:
        return Part.Wire(edges)
    except Exception as exc:
        raise _error(operation, parameter, f"edges do not form one ordered wire: {exc}") from exc


def _build(
    operation: str,
    payload: dict[str, Any],
    diagnostics: dict[str, Any] | None = None,
):
    import FreeCAD as App
    import Part

    properties = _properties(payload)
    if operation == "from_object":
        reference = _argument(payload, 0, "reference")
        if not isinstance(reference, dict) or set(reference) != {
            "document_uid",
            "object_name",
        }:
            raise _error(
                operation,
                "reference",
                "expected document_uid and object_name from a validated program input",
            )
        key = (
            str(reference.get("document_uid") or ""),
            str(reference.get("object_name") or ""),
        )
        shape = _REFERENCE_SHAPES.get(key)
        if shape is None:
            raise _error(
                operation,
                "reference",
                "was not staged from this program's validated inputs; add the exact "
                "stable reference to inputs and its x-vibecad-reference schema",
            )
        return shape.copy()
    if operation == "box":
        return Part.makeBox(
            float(_argument(payload, 0, "length")),
            float(_argument(payload, 1, "width")),
            float(_argument(payload, 2, "height")),
            _vector(operation, "origin", properties.get("origin", [0.0, 0.0, 0.0])),
            _vector(
                operation,
                "direction",
                properties.get("direction", [0.0, 0.0, 1.0]),
                nonzero=True,
            ),
        )
    if operation == "wedge":
        length = float(_argument(payload, 0, "length"))
        width = float(_argument(payload, 1, "width"))
        height = float(_argument(payload, 2, "height"))
        ridge = float(properties.get("ridge_x", 0.0))
        return Part.makeWedge(
            0.0,
            0.0,
            0.0,
            0.0,
            ridge,
            length,
            width,
            height,
            height,
            ridge,
            _vector(operation, "origin", properties.get("origin", [0.0, 0.0, 0.0])),
            _vector(
                operation,
                "direction",
                properties.get("direction", [0.0, 0.0, 1.0]),
                nonzero=True,
            ),
        )
    if operation == "plane":
        return Part.makePlane(
            float(_argument(payload, 0, "length")),
            float(_argument(payload, 1, "width")),
            _vector(operation, "origin", properties.get("origin", [0.0, 0.0, 0.0])),
            _vector(
                operation,
                "normal",
                properties.get("normal", [0.0, 0.0, 1.0]),
                nonzero=True,
            ),
            _vector(
                operation,
                "x_direction",
                properties.get("x_direction", [1.0, 0.0, 0.0]),
                nonzero=True,
            ),
        )
    if operation == "prism":
        sides = int(_argument(payload, 0, "sides"))
        radius = float(_argument(payload, 1, "circumradius"))
        height = float(_argument(payload, 2, "height"))
        rotation_degrees = float(properties.get("rotation_degrees", 0.0))
        points = [
            App.Vector(
                radius * math.cos(math.radians(rotation_degrees + 360.0 * index / sides)),
                radius * math.sin(math.radians(rotation_degrees + 360.0 * index / sides)),
                0.0,
            )
            for index in range(sides)
        ]
        points.append(points[0])
        result = Part.Face(Part.makePolygon(points)).extrude(App.Vector(0.0, 0.0, height))
        direction = _vector(
            operation,
            "direction",
            properties.get("direction", [0.0, 0.0, 1.0]),
            nonzero=True,
        )
        rotation = App.Rotation(App.Vector(0.0, 0.0, 1.0), direction)
        result.Placement = App.Placement(
            _vector(operation, "center", properties.get("center", [0.0, 0.0, 0.0])),
            rotation,
        )
        return result
    if operation == "cylinder":
        return Part.makeCylinder(
            float(_argument(payload, 0, "radius")),
            float(_argument(payload, 1, "height")),
            _vector(operation, "origin", properties.get("origin", [0.0, 0.0, 0.0])),
            _vector(
                operation,
                "direction",
                properties.get("direction", [0.0, 0.0, 1.0]),
                nonzero=True,
            ),
            float(properties.get("angle", 360.0)),
        )
    if operation == "cone":
        return Part.makeCone(
            float(_argument(payload, 0, "radius1")),
            float(_argument(payload, 1, "radius2")),
            float(_argument(payload, 2, "height")),
            _vector(operation, "origin", properties.get("origin", [0.0, 0.0, 0.0])),
            _vector(
                operation,
                "direction",
                properties.get("direction", [0.0, 0.0, 1.0]),
                nonzero=True,
            ),
            float(properties.get("angle", 360.0)),
        )
    if operation == "sphere":
        return Part.makeSphere(
            float(_argument(payload, 0, "radius")),
            _vector(operation, "center", properties.get("center", [0.0, 0.0, 0.0])),
            _vector(
                operation,
                "direction",
                properties.get("direction", [0.0, 0.0, 1.0]),
                nonzero=True,
            ),
            float(properties.get("latitude1", -90.0)),
            float(properties.get("latitude2", 90.0)),
            float(properties.get("longitude", 360.0)),
        )
    if operation == "torus":
        return Part.makeTorus(
            float(_argument(payload, 0, "major_radius")),
            float(_argument(payload, 1, "minor_radius")),
            _vector(operation, "center", properties.get("center", [0.0, 0.0, 0.0])),
            _vector(
                operation,
                "direction",
                properties.get("direction", [0.0, 0.0, 1.0]),
                nonzero=True,
            ),
            float(properties.get("angle1", -180.0)),
            float(properties.get("angle2", 180.0)),
            float(properties.get("sweep", 360.0)),
        )
    if operation == "line":
        return Part.makeLine(
            _vector(operation, "start", _argument(payload, 0, "start")),
            _vector(operation, "end", _argument(payload, 1, "end")),
        )
    if operation == "arc":
        return Part.Arc(
            _vector(operation, "start", _argument(payload, 0, "start")),
            _vector(operation, "point", _argument(payload, 1, "point")),
            _vector(operation, "end", _argument(payload, 2, "end")),
        ).toShape()
    if operation == "circle":
        return Part.makeCircle(
            float(_argument(payload, 0, "radius")),
            _vector(operation, "center", properties.get("center", [0.0, 0.0, 0.0])),
            _vector(
                operation,
                "normal",
                properties.get("normal", [0.0, 0.0, 1.0]),
                nonzero=True,
            ),
            float(properties.get("start_angle", 0.0)),
            float(properties.get("end_angle", 360.0)),
        )
    if operation == "ellipse":
        center = _vector(operation, "center", properties.get("center", [0.0, 0.0, 0.0]))
        normal = _vector(
            operation,
            "normal",
            properties.get("normal", [0.0, 0.0, 1.0]),
            nonzero=True,
        )
        result = Part.Ellipse(
            center,
            float(_argument(payload, 0, "major_radius")),
            float(_argument(payload, 1, "minor_radius")),
        ).toShape()
        rotation = App.Rotation(App.Vector(0.0, 0.0, 1.0), normal)
        if abs(float(rotation.Angle)) > 1.0e-12:
            result.rotate(center, rotation.Axis, math.degrees(float(rotation.Angle)))
        return result
    if operation == "bezier":
        poles = _argument(payload, 0, "poles")
        if not isinstance(poles, list):
            raise _error(operation, "poles", "must be an array")
        curve = Part.BezierCurve()
        curve.setPoles(
            [_vector(operation, f"poles[{index}]", point) for index, point in enumerate(poles)]
        )
        weights = properties.get("weights", [])
        if not isinstance(weights, list):
            raise _error(operation, "weights", "must be an array")
        for index, weight in enumerate(weights, start=1):
            curve.setWeight(index, float(weight))
        return curve.toShape()
    if operation == "bspline":
        points = _argument(payload, 0, "points")
        if not isinstance(points, list):
            raise _error(operation, "points", "must be an array")
        curve = Part.BSplineCurve()
        curve.interpolate(
            Points=[
                _vector(operation, f"points[{index}]", point) for index, point in enumerate(points)
            ],
            PeriodicFlag=bool(properties.get("periodic")),
            Tolerance=float(properties.get("tolerance", 1.0e-7)),
        )
        return curve.toShape()
    if operation == "nurbs_curve":
        poles = _argument(payload, 0, "poles")
        degree = int(_argument(payload, 1, "degree"))
        knots = _argument(payload, 2, "knots")
        multiplicities = _argument(payload, 3, "multiplicities")
        weights = properties.get("weights", [])
        if not all(
            isinstance(value, list)
            for value in (poles, knots, multiplicities, weights)
        ):
            raise _error(
                operation,
                "poles/knots/multiplicities/weights",
                "must be arrays",
            )
        curve = Part.BSplineCurve()
        curve.buildFromPolesMultsKnots(
            [
                _vector(operation, f"poles[{index}]", point)
                for index, point in enumerate(poles)
            ],
            [int(value) for value in multiplicities],
            [float(value) for value in knots],
            bool(properties.get("periodic")),
            degree,
            [float(value) for value in weights] if weights else None,
            bool(weights),
        )
        return curve.toShape()
    if operation == "helix":
        representation = str(properties.get("representation") or "standard")
        arguments = (
            float(_argument(payload, 0, "pitch")),
            float(_argument(payload, 1, "height")),
            float(_argument(payload, 2, "radius")),
            float(properties.get("angle", 0.0)),
            bool(properties.get("left_handed")),
        )
        if representation == "segmented":
            return Part.makeLongHelix(*arguments)
        if representation != "standard":
            raise _error(
                operation,
                "representation",
                "must be standard or segmented",
            )
        standard_arguments = (*arguments, bool(properties.get("vertical_height")))
        return Part.makeHelix(*standard_arguments)
    if operation == "wire":
        items = _argument(payload, 0, "items")
        if not isinstance(items, list) or not items:
            raise _error(operation, "items", "must be a non-empty array")
        if all(isinstance(item, dict) for item in items):
            edges = []
            for index, item in enumerate(items):
                nested = _shape(operation, f"items[{index}]", item)
                edges.extend(list(getattr(nested, "Edges", []) or []))
            if not edges:
                raise _error(operation, "items", "Part values contain no edges")
            result = Part.Wire(edges)
        else:
            points = [
                _vector(operation, f"items[{index}]", point) for index, point in enumerate(items)
            ]
            if bool(properties.get("closed")) and not points[0].isEqual(points[-1], 1.0e-9):
                points.append(points[0])
            result = Part.makePolygon(points)
        if bool(properties.get("closed")) and not result.isClosed():
            raise _error(operation, "items", "edges do not form a closed wire")
        return result
    if operation == "face":
        outer = _wire_from_shape(
            operation,
            "outer",
            _shape(operation, "outer", _argument(payload, 0, "outer")),
        )
        holes = properties.get("holes", [])
        if not isinstance(holes, list):
            raise _error(operation, "holes", "must be an array")
        wires = [outer]
        for index, item in enumerate(holes):
            wires.append(
                _wire_from_shape(
                    operation,
                    f"holes[{index}]",
                    _shape(operation, f"holes[{index}]", item),
                )
            )
        # Part.Face([outer, holes...]) accepts same-oriented inner wires on some
        # OCC versions but can silently return an invalid face.  Subtracting
        # independently validated planar faces lets OCC orient each loop and
        # gives the worker a topology it can validate consistently.
        face = Part.Face(outer)
        for index, hole in enumerate(wires[1:]):
            hole_face = Part.Face(hole)
            if not hole_face.isValid():
                raise _error(operation, f"holes[{index}]", "does not define a valid planar face")
            face = face.cut(hole_face)
            if face.isNull() or not face.isValid():
                raise _error(
                    operation,
                    f"holes[{index}]",
                    "could not be subtracted from the outer face; ensure it is coplanar, "
                    "strictly inside, and does not cross another hole",
                )
        return face
    if operation == "shell":
        faces = _shape_list(operation, "faces", _argument(payload, 0, "faces"))
        flattened = [face for shape in faces for face in list(getattr(shape, "Faces", []) or [])]
        if not flattened:
            raise _error(operation, "faces", "values contain no faces")
        return Part.makeShell(flattened)
    if operation == "solid":
        shell = _shape(operation, "shell", _argument(payload, 0, "shell"))
        shells = list(getattr(shell, "Shells", []) or [])
        source = shell if str(getattr(shell, "ShapeType", "")) == "Shell" else None
        if source is None and len(shells) == 1:
            source = shells[0]
        if source is None:
            raise _error(operation, "shell", "must contain exactly one shell")
        return Part.makeSolid(source)
    if operation == "compound":
        return Part.makeCompound(_shape_list(operation, "shapes", _argument(payload, 0, "shapes")))
    if operation == "subshape":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        kind = str(_argument(payload, 1, "kind") or "").lower()
        collections = {
            "edge": "Edges",
            "wire": "Wires",
            "face": "Faces",
            "shell": "Shells",
            "solid": "Solids",
        }
        collection = collections.get(kind)
        if collection is None:
            raise _error(operation, "kind", f"unsupported subshape kind {kind!r}")
        return _indexed_subshapes(
            operation,
            "index",
            shape,
            [int(_argument(payload, 2, "index"))],
            collection,
        )[0]
    if operation == "extrude":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        vector = _vector(operation, "vector", _argument(payload, 1, "vector"), nonzero=True)
        return shape.extrude(vector)
    if operation == "revolve":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        return shape.revolve(
            _vector(operation, "axis_origin", _argument(payload, 1, "axis_origin")),
            _vector(
                operation,
                "axis_direction",
                _argument(payload, 2, "axis_direction"),
                nonzero=True,
            ),
            float(properties.get("angle", 360.0)),
        )
    if operation == "loft":
        sections = [
            _wire_from_shape(operation, f"sections[{index}]", shape)
            for index, shape in enumerate(
                _shape_list(operation, "sections", _argument(payload, 0, "sections"), minimum=2)
            )
        ]
        return Part.makeLoft(
            sections,
            solid=bool(properties.get("solid")),
            ruled=bool(properties.get("ruled")),
            closed=bool(properties.get("closed")),
            max_degree=int(properties.get("max_degree", 5)),
        )
    if operation == "sweep":
        raw_profile = _argument(payload, 0, "profile")
        if isinstance(raw_profile, list):
            profile_shapes = _shape_list(
                operation,
                "profile",
                raw_profile,
            )
        else:
            profile_shapes = [_shape(operation, "profile", raw_profile)]
        profiles = [
            _wire_from_shape(operation, f"profile[{index}]", profile)
            for index, profile in enumerate(profile_shapes)
        ]
        path = _wire_from_shape(
            operation,
            "path",
            _shape(operation, "path", _argument(payload, 1, "path")),
        )
        transitions = {"transformed": 0, "right_corner": 1, "round_corner": 2}
        return path.makePipeShell(
            profiles,
            bool(properties.get("solid")),
            bool(properties.get("frenet")),
            transitions[str(properties.get("transition") or "transformed")],
        )
    if operation == "ruled_surface":
        first = _shape(operation, "first", _argument(payload, 0, "first"))
        second = _shape(operation, "second", _argument(payload, 1, "second"))
        return Part.makeRuledSurface(first, second)
    if operation == "filled_surface":
        boundaries = _shape_list(
            operation,
            "boundaries",
            _argument(payload, 0, "boundaries"),
            minimum=1,
        )
        edges = [edge for boundary in boundaries for edge in list(boundary.Edges)]
        if len(edges) < 2:
            raise _error(operation, "boundaries", "must contain at least two edges")
        return Part.makeFilledFace(edges)
    if operation == "fuse":
        shapes = _shape_list(operation, "shapes", _argument(payload, 0, "shapes"), minimum=2)
        result = shapes[0].fuse(
            tuple(shapes[1:]),
            float(properties.get("tolerance", 0.0)),
        )
        return _refine(
            result,
            bool(properties.get("refine", True)),
            operation=operation,
        )
    if operation == "cut":
        base = _shape(operation, "base", _argument(payload, 0, "base"))
        tools = _shape_list(operation, "tools", _argument(payload, 1, "tools"))
        result = base.cut(tuple(tools), float(properties.get("tolerance", 0.0)))
        return _refine(
            result,
            bool(properties.get("refine", True)),
            operation=operation,
        )
    if operation == "common":
        shapes = _shape_list(operation, "shapes", _argument(payload, 0, "shapes"), minimum=2)
        result = shapes[0].common(
            tuple(shapes[1:]),
            float(properties.get("tolerance", 0.0)),
        )
        return _refine(
            result,
            bool(properties.get("refine", True)),
            operation=operation,
        )
    if operation == "section":
        left = _shape(operation, "left", _argument(payload, 0, "left"))
        right = _shape(operation, "right", _argument(payload, 1, "right"))
        return left.section(right, float(properties.get("tolerance", 0.0)))
    if operation == "general_fuse":
        shapes = _shape_list(operation, "shapes", _argument(payload, 0, "shapes"), minimum=2)
        result, provenance = shapes[0].generalFuse(
            tuple(shapes[1:]),
            float(properties.get("tolerance", 0.0)),
        )
        if diagnostics is not None:
            diagnostics["general_fuse"] = {
                "input_count": len(shapes),
                "source_fragment_counts": [
                    len(list(fragments or [])) for fragments in list(provenance or [])
                ],
                "result_solid_count": len(list(getattr(result, "Solids", []) or [])),
                "result_face_count": len(list(getattr(result, "Faces", []) or [])),
            }
        return result
    if operation == "slice":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        normal = _vector(operation, "normal", _argument(payload, 1, "normal"), nonzero=True)
        offsets = _argument(payload, 2, "offsets")
        if not isinstance(offsets, list) or not offsets:
            raise _error(operation, "offsets", "must be a non-empty array")
        raw_slices = shape.slices(normal, [float(value) for value in offsets])
        if hasattr(raw_slices, "ShapeType"):
            slices = list(getattr(raw_slices, "Wires", []) or [])
        else:
            slices = list(raw_slices or [])
        if not slices:
            raise _error(
                operation,
                "offsets",
                "none of the requested planes intersect the shape",
            )
        return Part.makeCompound(slices)
    if operation == "defeature":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        faces = _indexed_subshapes(
            operation,
            "faces",
            shape,
            _argument(payload, 1, "faces"),
            "Faces",
        )
        return shape.defeaturing(faces)
    if operation == "to_nurbs":
        return _shape(operation, "shape", _argument(payload, 0, "shape")).toNurbs()
    if operation == "reverse":
        return _shape(operation, "shape", _argument(payload, 0, "shape")).reversed()
    if operation == "sew":
        shapes = _shape_list(operation, "shapes", _argument(payload, 0, "shapes"))
        result = Part.makeCompound(shapes)
        result.sewShape()
        output_type = str(payload.get("output_type") or "")
        if output_type == "solid" and str(result.ShapeType) != "Solid":
            shells = list(getattr(result, "Shells", []) or [])
            if str(result.ShapeType) == "Shell":
                result = Part.makeSolid(result)
            elif len(shells) == 1:
                result = Part.makeSolid(shells[0])
            else:
                raise _error(
                    operation,
                    "output_type",
                    f"sewing produced {len(shells)} shells, so it cannot form one solid",
                )
        elif output_type == "compound" and str(result.ShapeType) != "Compound":
            result = Part.makeCompound([result])
        return result
    if operation == "repair":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        if not shape.fix(
            float(properties.get("working_tolerance", 1.0e-7)),
            float(properties.get("minimum_tolerance", 1.0e-7)),
            float(properties.get("maximum_tolerance", 1.0e-3)),
        ):
            raise _error(
                operation,
                "shape",
                "ShapeFix could not repair the topology within the requested tolerances",
            )
        return shape
    if operation in {"fillet", "chamfer"}:
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        selected = _indexed_subshapes(
            operation,
            "edges",
            shape,
            properties.get("edges", "all"),
            "Edges",
        )
        distance = float(_argument(payload, 1, "radius" if operation == "fillet" else "distance"))
        method = shape.makeFillet if operation == "fillet" else shape.makeChamfer
        return method(distance, selected)
    if operation == "offset":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        joins = {"arc": 0, "tangent": 1, "intersection": 2}
        return shape.makeOffsetShape(
            float(_argument(payload, 1, "distance")),
            float(properties.get("tolerance", 1.0e-7)),
            inter=False,
            self_inter=False,
            offsetMode=0,
            join=joins[str(properties.get("join") or "arc")],
            fill=bool(properties.get("fill")),
        )
    if operation == "offset2d":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        joins = {"arc": 0, "tangent": 1, "intersection": 2}
        return shape.makeOffset2D(
            float(_argument(payload, 1, "distance")),
            join=joins[str(properties.get("join") or "arc")],
            fill=bool(properties.get("fill")),
            openResult=bool(properties.get("open_result")),
            intersection=bool(properties.get("intersection")),
        )
    if operation == "thicken":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        faces = _indexed_subshapes(
            operation,
            "faces",
            shape,
            _argument(payload, 1, "faces"),
            "Faces",
        )
        joins = {"arc": 0, "tangent": 1, "intersection": 2}
        return shape.makeThickness(
            faces,
            float(_argument(payload, 2, "thickness")),
            float(properties.get("tolerance", 1.0e-7)),
            False,
            False,
            0,
            joins[str(properties.get("join") or "arc")],
        )
    if operation == "transform":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape")).copy()
        pivot = _vector(operation, "pivot", properties.get("pivot", [0.0, 0.0, 0.0]))
        scale = properties.get("scale", [1.0, 1.0, 1.0])
        if not isinstance(scale, list) or len(scale) != 3:
            raise _error(operation, "scale", "must contain three factors")
        if any(abs(float(value) - 1.0) > 1.0e-12 for value in scale):
            factors = [float(value) for value in scale]
            if max(factors) - min(factors) <= 1.0e-12:
                shape.scale(factors[0], pivot)
            else:
                matrix = App.Matrix()
                matrix.A11 = factors[0]
                matrix.A22 = factors[1]
                matrix.A33 = factors[2]
                matrix.A14 = pivot.x * (1.0 - factors[0])
                matrix.A24 = pivot.y * (1.0 - factors[1])
                matrix.A34 = pivot.z * (1.0 - factors[2])
                shape = shape.transformGeometry(matrix)
        rotation = float(properties.get("rotation_degrees", 0.0))
        if abs(rotation) > 1.0e-12:
            shape.rotate(
                pivot,
                _vector(
                    operation,
                    "rotation_axis",
                    properties.get("rotation_axis"),
                    nonzero=True,
                ),
                rotation,
            )
        shape.translate(_vector(operation, "translation", properties.get("translation")))
        return shape
    if operation == "mirror":
        shape = _shape(operation, "shape", _argument(payload, 0, "shape"))
        return shape.mirror(
            _vector(operation, "plane_origin", _argument(payload, 1, "plane_origin")),
            _vector(
                operation,
                "plane_normal",
                _argument(payload, 2, "plane_normal"),
                nonzero=True,
            ),
        )
    if operation == "project":
        target = _shape(operation, "target", _argument(payload, 0, "target"))
        profile = _shape(operation, "profile", _argument(payload, 1, "profile"))
        mode = str(properties.get("mode") or "parallel")
        vector = _vector(
            operation,
            "vector",
            _argument(payload, 2, "vector"),
            nonzero=mode == "parallel",
        )
        if mode == "parallel":
            result = target.makeParallelProjection(
                profile,
                vector,
            )
        elif mode == "perspective":
            result = target.makePerspectiveProjection(
                profile,
                vector,
            )
        else:
            raise _error(operation, "mode", "must be parallel or perspective")
        if str(payload.get("output_type") or "") == "wire":
            edges = list(getattr(result, "Edges", []) or [])
            if not edges:
                raise _error(operation, "profile", "projection produced no edges")
            try:
                result = Part.Wire(edges)
            except Exception as exc:
                raise _error(
                    operation,
                    "output_type",
                    "projection produced disconnected edges; request 'compound' instead",
                ) from exc
        elif str(result.ShapeType) != "Compound":
            result = Part.makeCompound([result])
        return result
    if operation == "refine":
        return _refine(
            _shape(operation, "shape", _argument(payload, 0, "shape")),
            True,
            operation=operation,
        )
    raise _error(operation, "operation", "is not implemented by the Part worker")


def build_part_shape(
    payload: dict[str, Any],
    *,
    diagnostics: dict[str, Any] | None = None,
    nested_shape_resolver: Callable[[dict[str, Any]], Any] | None = None,
):
    """Execute one validated Part definition and wrap OCC errors usefully."""

    operation = str(payload.get("operation") or "")
    if not operation:
        raise PartOperationError(
            "Part output has no operation name",
            stage="part_contract",
            correction="Return only values created by one declared Part runtime export.",
        )
    token = (
        _NESTED_SHAPE_RESOLVER.set(nested_shape_resolver)
        if nested_shape_resolver is not None
        else None
    )
    try:
        shape = _build(operation, payload, diagnostics)
    except PartOperationError:
        raise
    except Exception as exc:
        raise PartOperationError(
            f"api.{operation}: OpenCascade rejected the requested operation: "
            f"{exc.__class__.__name__}: {exc}",
            stage="part_kernel",
            operation=operation,
            observed={
                "exception_type": exc.__class__.__name__,
                "message": str(exc),
            },
            correction=(
                f"Inspect api.{operation} inputs and the upstream accepted shape facts. "
                "Change the smallest invalid geometry or tolerance; do not repeat the "
                "unchanged call."
            ),
        ) from exc
    finally:
        if token is not None:
            _NESTED_SHAPE_RESOLVER.reset(token)
    if shape is None or shape.isNull():
        raise PartOperationError(
            f"api.{operation}: OpenCascade produced a null shape",
            stage="part_result_validation",
            operation=operation,
            observed={"shape_type": "Null"},
            correction=(
                f"Inspect api.{operation} inputs for non-intersecting, degenerate, or "
                "self-intersecting geometry and change that exact cause."
            ),
        )
    if not shape.isValid():
        raise PartOperationError(
            f"api.{operation}: OpenCascade produced an invalid shape",
            stage="part_result_validation",
            operation=operation,
            observed={"shape_type": str(getattr(shape, "ShapeType", "") or "")},
            correction=(
                f"Repair the upstream geometry used by api.{operation}; use api.repair "
                "only with the smallest bounded tolerances justified by the defect."
            ),
        )
    output_type = str(payload.get("output_type") or "")
    exact_types = {
        "edge": ("Edge", "Edges"),
        "wire": ("Wire", "Wires"),
        "face": ("Face", "Faces"),
        "shell": ("Shell", "Shells"),
        "solid": ("Solid", "Solids"),
    }
    if output_type in exact_types:
        expected_shape_type, collection_name = exact_types[output_type]
        if str(getattr(shape, "ShapeType", "")) != expected_shape_type:
            children = list(getattr(shape, collection_name, []) or [])
            if len(children) != 1:
                raise PartOperationError(
                    f"api.{operation}: declared {output_type} but OpenCascade produced "
                    f"{getattr(shape, 'ShapeType', '<unknown>')} containing "
                    f"{len(children)} {collection_name.lower()}",
                    stage="part_output_type",
                    operation=operation,
                    parameter="output_type",
                    observed={
                        "declared_output_type": output_type,
                        "shape_type": str(getattr(shape, "ShapeType", "") or ""),
                        "matching_child_count": len(children),
                    },
                    correction=(
                        "Declare the topology class actually produced by this operation, "
                        "or change the source geometry so it produces exactly one requested "
                        f"{output_type}."
                    ),
                )
            shape = children[0]
        if shape.isNull() or not shape.isValid():
            raise PartOperationError(
                f"api.{operation}: normalized {output_type} topology is invalid",
                stage="part_result_validation",
                operation=operation,
                parameter="output_type",
                observed={"declared_output_type": output_type},
                correction=(
                    "Change the upstream operation so its normalized topology is valid; "
                    "do not weaken the declared output type."
                ),
            )
    elif output_type == "compound" and str(getattr(shape, "ShapeType", "")) != "Compound":
        raise PartOperationError(
            f"api.{operation}: declared compound but OpenCascade produced "
            f"{getattr(shape, 'ShapeType', '<unknown>')}",
            stage="part_output_type",
            operation=operation,
            parameter="output_type",
            observed={
                "declared_output_type": "compound",
                "shape_type": str(getattr(shape, "ShapeType", "") or ""),
            },
            correction=(
                "Use api.compound for an unfused multi-shape publication, or declare "
                "the exact topology produced by this operation."
            ),
        )
    return shape
