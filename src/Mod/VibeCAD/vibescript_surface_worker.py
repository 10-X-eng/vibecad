# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated OCC/Surface evaluator for production Surface VibeScript programs."""

from __future__ import annotations

from collections.abc import Mapping
import math
from pathlib import Path
import re
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue
from vibescript_part_worker import (
    configure_part_references,
    detached_reference_shape,
    part_shape_facts,
)


_OPERATIONS = frozenset(
    {
        "line",
        "circle",
        "bezier",
        "bspline",
        "wire",
        "from_object",
        "face",
        "surface",
        "boundary",
        "curve_constraint",
        "face_constraint",
        "point_constraint",
        "fill",
        "blend",
        "extend",
        "loft",
        "thicken",
        "shell",
    }
)
_OPERATION_TYPES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "line": frozenset({"edge"}),
        "circle": frozenset({"edge"}),
        "bezier": frozenset({"edge"}),
        "bspline": frozenset({"edge"}),
        "wire": frozenset({"wire"}),
        "from_object": frozenset({"vertex", "edge", "wire", "face", "shell", "solid"}),
        "face": frozenset({"face"}),
        "surface": frozenset({"surface"}),
        "boundary": frozenset({"boundary_constraint"}),
        "curve_constraint": frozenset({"curve_constraint"}),
        "face_constraint": frozenset({"face_constraint"}),
        "point_constraint": frozenset({"point_constraint"}),
        "fill": frozenset({"fill"}),
        "blend": frozenset({"blend"}),
        "extend": frozenset({"extension"}),
        "loft": frozenset({"loft", "solid"}),
        "thicken": frozenset({"solid"}),
        "shell": frozenset({"shell", "solid"}),
    }
)
_PROPERTIES: Mapping[str, frozenset[str]] = MappingProxyType(
    {
        "line": frozenset({"label"}),
        "circle": frozenset({"normal", "start_angle", "end_angle", "label"}),
        "bezier": frozenset({"label"}),
        "bspline": frozenset({"periodic", "label"}),
        "wire": frozenset({"mode", "closed", "label"}),
        "from_object": frozenset({"selection", "label"}),
        "face": frozenset({"holes", "label"}),
        "surface": frozenset(
            {
                "mode",
                "degree_min",
                "degree_max",
                "continuity",
                "tolerance",
                "parametrization",
                "smoothing",
                "label",
            }
        ),
        "boundary": frozenset({"continuity", "support_face"}),
        "curve_constraint": frozenset({"continuity", "support_face"}),
        "face_constraint": frozenset({"continuity"}),
        "point_constraint": frozenset(),
        "fill": frozenset(
            {
                "curve_constraints",
                "face_constraints",
                "point_constraints",
                "initial_face",
                "degree",
                "points_on_curve",
                "iterations",
                "anisotropy",
                "tolerance_2d",
                "tolerance_3d",
                "angular_tolerance",
                "curvature_tolerance",
                "maximum_degree",
                "maximum_segments",
                "label",
            }
        ),
        "blend": frozenset({"style", "reversed", "label"}),
        "extend": frozenset(
            {
                "u_negative",
                "u_positive",
                "v_negative",
                "v_positive",
                "tolerance",
                "samples_u",
                "samples_v",
                "label",
            }
        ),
        "loft": frozenset({"solid", "ruled", "closed", "max_degree", "label"}),
        "thicken": frozenset({"remove_faces", "tolerance", "join", "label"}),
        "shell": frozenset(
            {"make_solid", "tolerance", "cut_free_edges", "nonmanifold", "label"}
        ),
    }
)
_ARGUMENT_COUNTS = MappingProxyType(
    {
        "line": 2,
        "circle": 2,
        "bezier": 1,
        "bspline": 1,
        "wire": 1,
        "from_object": 1,
        "face": 1,
        "surface": 1,
        "boundary": 1,
        "curve_constraint": 1,
        "face_constraint": 1,
        "point_constraint": 1,
        "fill": 1,
        "blend": 1,
        "extend": 1,
        "loft": 1,
        "thicken": 2,
        "shell": 1,
    }
)
_CONTINUITY_ORDER = {"C0": 0, "G1": 1, "G2": 2}
_SUBELEMENT = re.compile(r"^(Vertex|Edge|Wire|Face|Shell|Solid)([1-9][0-9]*)$")
_REFERENCE_METADATA: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})
_GRAPH_CORRECTION = (
    "Return the direct value produced by the active Surface api; do not construct, "
    "copy, or edit serialized graph dictionaries."
)


class SurfaceCandidateError(RuntimeError):
    """Model-facing Surface failure with exact stage and operation details."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        super().__init__(message)


def _operation_correction(operation: str) -> str:
    return {
        "from_object": (
            "Use one exact authenticated input reference and choose whole_shape, one "
            "stable subelement, or one published semantic interface of the requested type."
        ),
        "face": (
            "Use one closed planar outer wire and closed coplanar holes that remain "
            "strictly inside it without touching or crossing."
        ),
        "surface": (
            "Use one rectangular finite point grid and correct only the selected "
            "interpolation/approximation degree, continuity, tolerance, or smoothing control."
        ),
        "fill": (
            "Order connected boundaries, provide support faces for G1/G2 constraints, "
            "and adjust only the reported filling control or tolerance."
        ),
        "blend": (
            "Provide two to four consecutive single-edge boundaries, then correct only "
            "the reported reversal or Stretched/Coons/Curved style."
        ),
        "extend": (
            "Use one valid face and reduce only the reported U/V extension fraction, "
            "sampling count, or tolerance."
        ),
        "loft": (
            "Use two or more compatible ordered edge/wire sections and correct only "
            "solid, ruled, closed, or maximum-degree settings."
        ),
        "thicken": (
            "Use a valid face/shell/solid, select only existing 1-based faces, and "
            "reduce or reverse thickness or change the reported join mode."
        ),
        "shell": (
            "Provide touching faces or shells that sew into exactly one connected shell; "
            "adjust only tolerance, free-edge cutting, non-manifold, or solid promotion."
        ),
    }.get(operation, _GRAPH_CORRECTION)


def _json_safe(value: Any, *, depth: int = 0) -> Any:
    if depth > 12:
        return "<maximum diagnostic depth reached>"
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(key): _json_safe(item, depth=depth + 1) for key, item in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [_json_safe(item, depth=depth + 1) for item in value]
    quantity = getattr(value, "Value", None)
    if isinstance(quantity, (int, float)):
        return float(quantity)
    return str(value)


def configure_surface_references(root: Path, entries: list[dict[str, Any]]) -> None:
    """Authenticate detached BREPs and bind bounded semantic-selection metadata."""

    configure_part_references(root, entries)
    metadata: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise ValueError(f"document_references[{index}] must be an object.")
        key = (
            str(raw.get("document_uid") or ""),
            str(raw.get("object_name") or ""),
        )
        if not all(key) or key in metadata:
            raise ValueError(
                f"document_references[{index}] has missing or duplicate identity."
            )
        shape = detached_reference_shape(
            {"document_uid": key[0], "object_name": key[1]}
        )
        facts = part_shape_facts(shape, max_subelements=64)
        if facts["null"] or not facts["valid"]:
            raise ValueError(
                f"Surface reference {key[1]!r} must contain one valid non-null Shape."
            )
        reported = raw.get("facts")
        if isinstance(reported, dict):
            for field in ("solids", "shells", "faces", "wires", "edges", "vertices"):
                if int(reported.get(field, -1)) != int(facts[field]):
                    raise ValueError(
                        f"Surface reference {key[1]!r} changed topology during transfer "
                        f"({field})."
                    )
        interfaces = raw.get("published_interfaces", {})
        if not isinstance(interfaces, dict) or len(interfaces) > 64:
            raise ValueError(
                f"Surface reference {key[1]!r} has invalid semantic interfaces."
            )
        metadata[key] = MappingProxyType(
            {
                "label": str(raw.get("label") or ""),
                "type_id": str(raw.get("type_id") or ""),
                "shape_type": str(raw.get("shape_type") or ""),
                "brep_sha256": str(raw.get("brep_sha256") or ""),
                "source_kind": str(raw.get("source_kind") or "shape"),
                "source_program_id": str(raw.get("source_program_id") or ""),
                "source_program_domain": str(raw.get("source_program_domain") or ""),
                "source_revision": str(raw.get("source_revision") or ""),
                "transient_topology": bool(raw.get("transient_topology")),
                "requires_semantic_interfaces": bool(
                    raw.get("requires_semantic_interfaces")
                ),
                "published_interfaces": _json_safe(interfaces),
                "facts": facts,
            }
        )
    global _REFERENCE_METADATA
    _REFERENCE_METADATA = MappingProxyType(metadata)


def _payload(value: Any, *, context: str) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise SurfaceCandidateError(
            f"{context} must be a value returned by the active Surface api.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": _GRAPH_CORRECTION,
            },
        )
    fields = {"domain", "operation", "output_type", "arguments", "properties"}
    if set(payload) != fields:
        raise SurfaceCandidateError(
            f"{context} has malformed Surface graph fields.",
            details={
                "stage": "graph_contract",
                "path": context,
                "missing": sorted(fields - set(payload)),
                "unexpected": sorted(set(payload) - fields),
                "correction": _GRAPH_CORRECTION,
            },
        )
    if payload.get("domain") != "surface":
        raise SurfaceCandidateError(
            f"{context} belongs to another VibeScript domain.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": _GRAPH_CORRECTION,
            },
        )
    operation = str(payload.get("operation") or "")
    output_type = str(payload.get("output_type") or "")
    if operation not in _OPERATIONS or output_type not in _OPERATION_TYPES.get(
        operation, frozenset()
    ):
        raise SurfaceCandidateError(
            f"{context} has unsupported Surface operation/type "
            f"{operation!r}/{output_type!r}.",
            details={
                "stage": "graph_contract",
                "path": context,
                "operation": operation,
                "output_type": output_type,
                "correction": _GRAPH_CORRECTION,
            },
        )
    arguments = payload.get("arguments")
    properties = payload.get("properties")
    if not isinstance(arguments, list) or not isinstance(properties, dict):
        raise SurfaceCandidateError(
            f"{context} arguments and properties must be serialized containers.",
            details={
                "stage": "graph_contract",
                "path": context,
                "correction": _GRAPH_CORRECTION,
            },
        )
    expected_count = int(_ARGUMENT_COUNTS[operation])
    if len(arguments) != expected_count:
        raise SurfaceCandidateError(
            f"{context} must serialize exactly {expected_count} positional argument(s).",
            details={
                "stage": "graph_contract",
                "path": context,
                "expected_argument_count": expected_count,
                "received_argument_count": len(arguments),
                "correction": _GRAPH_CORRECTION,
            },
        )
    expected_properties = set(_PROPERTIES[operation])
    if set(properties) != expected_properties:
        raise SurfaceCandidateError(
            f"{context} has malformed immutable properties.",
            details={
                "stage": "graph_contract",
                "path": context,
                "missing": sorted(expected_properties - set(properties)),
                "unexpected": sorted(set(properties) - expected_properties),
                "correction": _GRAPH_CORRECTION,
            },
        )
    return payload


def validate_surface_definition(value: Any, *, context: str = "Surface output") -> dict[str, Any]:
    """Validate the complete serialized graph without executing OCC operations."""

    seen = 0

    def walk(raw: Any, path: str, depth: int) -> dict[str, Any]:
        nonlocal seen
        seen += 1
        if seen > 8192:
            raise SurfaceCandidateError(
                f"{context} exceeds 8192 graph nodes.",
                details={
                    "stage": "graph_contract",
                    "path": path,
                    "correction": (
                        "Split the Surface design into smaller semantic programs with "
                        "stable published boundaries or patches."
                    ),
                },
            )
        if depth > 64:
            raise SurfaceCandidateError(
                f"{context} exceeds 64 nested operations.",
                details={
                    "stage": "graph_contract",
                    "path": path,
                    "correction": (
                        "Flatten repeated intermediate expressions or split the design into "
                        "smaller semantic Surface programs."
                    ),
                },
            )
        payload = _payload(raw, context=path)
        operation = str(payload["operation"])
        for index, argument in enumerate(payload["arguments"]):
            if isinstance(argument, dict) and argument.get("domain") is not None:
                walk(argument, f"{path}.arguments[{index}]", depth + 1)
            elif isinstance(argument, list):
                for child_index, child in enumerate(argument):
                    if isinstance(child, dict) and child.get("domain") is not None:
                        walk(
                            child,
                            f"{path}.arguments[{index}][{child_index}]",
                            depth + 1,
                        )
        for name, property_value in payload["properties"].items():
            if isinstance(property_value, dict) and property_value.get("domain") is not None:
                walk(property_value, f"{path}.properties.{name}", depth + 1)
            elif isinstance(property_value, list):
                for child_index, child in enumerate(property_value):
                    if isinstance(child, dict) and child.get("domain") is not None:
                        walk(
                            child,
                            f"{path}.properties.{name}[{child_index}]",
                            depth + 1,
                        )
        if operation == "loft":
            solid = payload["properties"].get("solid")
            expected = "solid" if solid is True else "loft"
            if payload["output_type"] != expected:
                raise SurfaceCandidateError(
                    f"{path} output type disagrees with api.loft(solid=...).",
                    details={
                        "stage": "graph_contract",
                        "path": path,
                        "correction": _GRAPH_CORRECTION,
                    },
                )
        if operation == "shell":
            solid = payload["properties"].get("make_solid")
            expected = "solid" if solid is True else "shell"
            if payload["output_type"] != expected:
                raise SurfaceCandidateError(
                    f"{path} output type disagrees with api.shell(make_solid=...).",
                    details={
                        "stage": "graph_contract",
                        "path": path,
                        "correction": _GRAPH_CORRECTION,
                    },
                )
        return payload

    return walk(value, context, 0)


def _number(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SurfaceCandidateError(f"{context} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise SurfaceCandidateError(f"{context} must be finite.")
    return result


def _vector(value: Any, *, context: str):
    import FreeCAD as App

    if not isinstance(value, list) or len(value) != 3:
        raise SurfaceCandidateError(f"{context} must be [x,y,z].")
    return App.Vector(
        *(_number(item, context=f"{context}[{index}]") for index, item in enumerate(value))
    )


def _shape_type(shape: Any) -> str:
    return str(getattr(shape, "ShapeType", "") or "")


def _require_shape(shape: Any, expected: set[str], *, context: str) -> Any:
    received = _shape_type(shape)
    if received not in expected:
        raise SurfaceCandidateError(
            f"{context} requires OCC ShapeType {sorted(expected)}, not {received or '<missing>'}.",
            details={
                "stage": "shape_contract",
                "path": context,
                "expected_shape_types": sorted(expected),
                "received_shape_type": received,
            },
        )
    if shape.isNull() or not shape.isValid():
        raise SurfaceCandidateError(
            f"{context} produced an invalid or null Shape.",
            details={"stage": "shape_contract", "path": context},
        )
    return shape


def _reference_key(value: Any, *, context: str) -> tuple[str, str]:
    if not isinstance(value, dict) or set(value) != {"document_uid", "object_name"}:
        raise SurfaceCandidateError(
            f"{context} must contain exactly document_uid and object_name.",
            details={"stage": "reference_selection", "path": context},
        )
    key = (str(value.get("document_uid") or ""), str(value.get("object_name") or ""))
    if key not in _REFERENCE_METADATA:
        raise SurfaceCandidateError(
            f"{context} reference {key[1]!r} was not authenticated from inputs.",
            details={
                "stage": "reference_selection",
                "path": context,
                "reference": {"document_uid": key[0], "object_name": key[1]},
                "available_objects": sorted(name for _uid, name in _REFERENCE_METADATA),
                "correction": (
                    "Put the exact stable reference in inputs and mark its input-schema "
                    "property with x-vibecad-reference=true."
                ),
            },
        )
    return key


def _subshape(shape: Any, name: str, *, context: str) -> Any:
    match = _SUBELEMENT.fullmatch(name)
    if match is None:
        raise SurfaceCandidateError(f"{context} has invalid subelement {name!r}.")
    collection_name = f"{match.group(1)}s"
    collection = list(getattr(shape, collection_name, []) or [])
    index = int(match.group(2))
    if index > len(collection):
        raise SurfaceCandidateError(
            f"{context} selects {name}, but the Shape contains only "
            f"{len(collection)} {collection_name}.",
            details={
                "stage": "reference_selection",
                "path": context,
                "requested": name,
                "available_range": f"1-{len(collection)}",
            },
        )
    return collection[index - 1]


def _surface_type_name(face: Any) -> str:
    try:
        return type(face.Surface).__name__.removeprefix("Part.")
    except Exception:
        return "Unknown"


class SurfaceEvaluator:
    """One bounded recursive evaluator tied to an isolated candidate document."""

    def __init__(self, document: Any) -> None:
        self.document = document
        self.operation_count = 0
        self.native_counter = 0

    def _next_name(self, prefix: str) -> str:
        self.native_counter += 1
        return f"{prefix}{self.native_counter}"

    def _holder(self, shape: Any, prefix: str = "SurfaceInput") -> Any:
        obj = self.document.addObject("Part::Feature", self._next_name(prefix))
        if obj is None:
            raise SurfaceCandidateError("FreeCAD could not create a Surface input holder.")
        obj.Shape = shape
        return obj

    def _native_result(self, obj: Any, operation: str) -> Any:
        try:
            self.document.recompute()
        except Exception as exc:
            raise SurfaceCandidateError(
                f"FreeCAD failed while recomputing native api.{operation}: {exc}",
                details={
                    "stage": "native_recompute",
                    "operation": operation,
                    "native_type": str(getattr(obj, "TypeId", "") or ""),
                    "exception_type": type(exc).__name__,
                },
            ) from exc
        shape = getattr(obj, "Shape", None)
        state = [str(value) for value in list(getattr(obj, "State", []) or [])]
        if shape is None or shape.isNull() or not shape.isValid():
            status_getter = getattr(obj, "getStatusString", None)
            status = str(status_getter() if callable(status_getter) else "")
            raise SurfaceCandidateError(
                f"Native api.{operation} produced no valid Shape: {status or state}.",
                details={
                    "stage": "native_recompute",
                    "operation": operation,
                    "native_type": str(getattr(obj, "TypeId", "") or ""),
                    "state": state,
                    "status": status,
                    "correction": (
                        "Check boundary ordering, continuity supports, section compatibility, "
                        "extension range, and solver tolerances."
                    ),
                },
            )
        return shape.copy(), state

    def build(
        self,
        raw: Any,
        *,
        context: str,
        depth: int = 0,
    ) -> tuple[Any, dict[str, Any]]:
        import Part

        self.operation_count += 1
        if self.operation_count > 8192:
            raise SurfaceCandidateError(
                "A Surface candidate may evaluate at most 8192 graph operations.",
                details={
                    "stage": "operation_budget",
                    "path": context,
                    "correction": (
                        "Split the Surface design into smaller semantic programs with "
                        "stable patch outputs."
                    ),
                },
            )
        if depth > 64:
            raise SurfaceCandidateError(
                "A Surface candidate may nest at most 64 graph operations.",
                details={
                    "stage": "operation_budget",
                    "path": context,
                    "correction": (
                        "Flatten nested intermediate expressions or split the Surface design."
                    ),
                },
            )
        payload = _payload(raw, context=context)
        operation = str(payload["operation"])
        output_type = str(payload["output_type"])
        arguments = list(payload["arguments"])
        properties = dict(payload["properties"])

        try:
            if operation == "line":
                shape = Part.makeLine(
                    _vector(arguments[0], context=f"{context}.start"),
                    _vector(arguments[1], context=f"{context}.end"),
                )
                data = {"engine": "Part.makeLine"}
            elif operation == "circle":
                shape = Part.makeCircle(
                    _number(arguments[1], context=f"{context}.radius"),
                    _vector(arguments[0], context=f"{context}.center"),
                    _vector(properties["normal"], context=f"{context}.normal"),
                    _number(properties["start_angle"], context=f"{context}.start_angle"),
                    _number(properties["end_angle"], context=f"{context}.end_angle"),
                )
                data = {"engine": "Part.makeCircle"}
            elif operation == "bezier":
                poles = arguments[0]
                if not isinstance(poles, list) or not 2 <= len(poles) <= 64:
                    raise SurfaceCandidateError(f"{context}.poles must contain 2-64 points.")
                curve = Part.BezierCurve()
                curve.setPoles(
                    [_vector(point, context=f"{context}.poles") for point in poles]
                )
                shape = curve.toShape()
                data = {"engine": "Part.BezierCurve", "pole_count": len(poles)}
            elif operation == "bspline":
                points = arguments[0]
                if not isinstance(points, list) or not 3 <= len(points) <= 4096:
                    raise SurfaceCandidateError(
                        f"{context}.points must contain 3-4096 points."
                    )
                curve = Part.BSplineCurve()
                curve.interpolate(
                    Points=[
                        _vector(point, context=f"{context}.points") for point in points
                    ],
                    PeriodicFlag=bool(properties["periodic"]),
                )
                shape = curve.toShape()
                data = {
                    "engine": "Part.BSplineCurve.interpolate",
                    "point_count": len(points),
                    "degree": int(curve.Degree),
                    "periodic": bool(curve.isPeriodic()),
                }
            elif operation == "wire":
                mode = str(properties["mode"])
                items = arguments[0]
                if not isinstance(items, list) or not items:
                    raise SurfaceCandidateError(f"{context}.items must be non-empty.")
                if mode == "points":
                    vectors = [
                        _vector(point, context=f"{context}.items") for point in items
                    ]
                    if bool(properties["closed"]) and not vectors[0].isEqual(
                        vectors[-1], 1.0e-9
                    ):
                        vectors.append(vectors[0])
                    shape = Part.makePolygon(vectors)
                elif mode == "curves":
                    edges = []
                    for index, item in enumerate(items):
                        nested, _data = self.build(
                            item,
                            context=f"{context}.items[{index}]",
                            depth=depth + 1,
                        )
                        _require_shape(nested, {"Edge", "Wire"}, context=f"{context}.items[{index}]")
                        edges.extend(list(nested.Edges))
                    shape = Part.Wire(edges)
                    if bool(properties["closed"]) and not shape.isClosed():
                        vertices = list(shape.Vertexes)
                        if len(vertices) < 2:
                            raise SurfaceCandidateError(
                                f"{context} cannot close a wire without two endpoints."
                            )
                        closing = Part.makeLine(vertices[-1].Point, vertices[0].Point)
                        shape = Part.Wire([*edges, closing])
                else:
                    raise SurfaceCandidateError(
                        f"{context}.mode {mode!r} is unsupported."
                    )
                data = {
                    "engine": "Part.Wire",
                    "mode": mode,
                    "closed": bool(shape.isClosed()),
                }
            elif operation == "from_object":
                shape, data = self._from_object(payload, context=context)
            elif operation == "face":
                outer, _data = self.build(
                    arguments[0], context=f"{context}.outer", depth=depth + 1
                )
                outer = _require_shape(outer, {"Wire"}, context=f"{context}.outer")
                if not outer.isClosed():
                    raise SurfaceCandidateError(f"{context}.outer must be closed.")
                outer_face = Part.Face(outer)
                if outer_face.isNull() or not outer_face.isValid():
                    raise SurfaceCandidateError(
                        f"{context}.outer does not define one valid planar face."
                    )
                holes = properties["holes"]
                if not isinstance(holes, list):
                    raise SurfaceCandidateError(f"{context}.holes must be an array.")
                oriented_wires = [outer]
                outer_normal = outer_face.normalAt(0.0, 0.0)
                for index, item in enumerate(holes):
                    hole, _data = self.build(
                        item,
                        context=f"{context}.holes[{index}]",
                        depth=depth + 1,
                    )
                    hole = _require_shape(
                        hole, {"Wire"}, context=f"{context}.holes[{index}]"
                    )
                    if not hole.isClosed():
                        raise SurfaceCandidateError(
                            f"{context}.holes[{index}] must be closed."
                        )
                    hole_face = Part.Face(hole)
                    if hole_face.isNull() or not hole_face.isValid():
                        raise SurfaceCandidateError(
                            f"{context}.holes[{index}] does not define one valid planar face."
                        )
                    oriented = hole.copy()
                    if outer_normal.dot(hole_face.normalAt(0.0, 0.0)) > 0.0:
                        oriented.reverse()
                    oriented_wires.append(oriented)
                shape = Part.Face(oriented_wires)
                if (
                    shape.isNull()
                    or not shape.isValid()
                    or _shape_type(shape) != "Face"
                    or len(shape.Wires) != len(oriented_wires)
                ):
                    raise SurfaceCandidateError(
                        f"{context}.holes could not form one valid planar Face; ensure "
                        "every hole is coplanar, strictly inside the outer wire, and "
                        "does not touch or cross another boundary.",
                        details={
                            "stage": "native_operation",
                            "operation": "face",
                            "path": context,
                            "hole_count": len(holes),
                        },
                    )
                data = {"engine": "Part.Face", "hole_count": len(holes)}
            elif operation == "surface":
                shape, data = self._surface(payload, context=context)
            elif operation in {
                "boundary",
                "curve_constraint",
                "face_constraint",
                "point_constraint",
            }:
                raise SurfaceCandidateError(
                    f"{context} is a filling constraint and cannot be evaluated as topology."
                )
            elif operation == "fill":
                shape, data = self._fill(payload, context=context, depth=depth)
            elif operation == "blend":
                shape, data = self._blend(payload, context=context, depth=depth)
            elif operation == "extend":
                shape, data = self._extend(payload, context=context, depth=depth)
            elif operation == "loft":
                shape, data = self._loft(payload, context=context, depth=depth)
            elif operation == "thicken":
                shape, data = self._thicken(payload, context=context, depth=depth)
            elif operation == "shell":
                shape, data = self._shell(payload, context=context, depth=depth)
            else:
                raise SurfaceCandidateError(
                    f"Unsupported Surface operation {operation!r}."
                )
        except SurfaceCandidateError as exc:
            details = dict(exc.details)
            details.setdefault("stage", "native_operation")
            details.setdefault("operation", operation)
            details.setdefault("path", context)
            details.setdefault("correction", _operation_correction(operation))
            if details == exc.details:
                raise
            raise SurfaceCandidateError(str(exc), details=details) from exc
        except Exception as exc:
            raise SurfaceCandidateError(
                f"FreeCAD rejected api.{operation}: {exc}",
                details={
                    "stage": "native_operation",
                    "operation": operation,
                    "path": context,
                    "exception_type": type(exc).__name__,
                    "correction": (
                        "Inspect the operation-specific boundary, topology class, dimensions, "
                        "continuity supports, and tolerance values."
                    ),
                },
            ) from exc
        shape = _require_shape(
            shape,
            {
                "vertex": {"Vertex"},
                "edge": {"Edge"},
                "wire": {"Wire"},
                "face": {"Face"},
                "surface": {"Face"},
                "fill": {"Face"},
                "blend": {"Face"},
                "extension": {"Face"},
                "loft": {"Face", "Shell"},
                "shell": {"Shell"},
                "solid": {"Solid"},
            }[output_type],
            context=context,
        )
        faces = list(getattr(shape, "Faces", []) or [])
        data = {
            "operation": operation,
            "output_type": output_type,
            **dict(data),
            "shape_type": _shape_type(shape),
            "face_surface_types": [_surface_type_name(face) for face in faces[:64]],
            "face_surface_types_truncated": len(faces) > 64,
        }
        return shape, data

    def _from_object(
        self, payload: Mapping[str, Any], *, context: str
    ) -> tuple[Any, dict[str, Any]]:
        arguments = list(payload["arguments"])
        properties = dict(payload["properties"])
        output_type = str(payload["output_type"])
        key = _reference_key(arguments[0], context=f"{context}.reference")
        metadata = _REFERENCE_METADATA[key]
        shape = detached_reference_shape(
            {"document_uid": key[0], "object_name": key[1]}
        )
        selection = properties["selection"]
        if not isinstance(selection, dict):
            raise SurfaceCandidateError(f"{context}.selection must be an object.")
        mode = str(selection.get("type") or "")
        resolved_subelement = ""
        semantic: dict[str, Any] | None = None
        if mode == "whole_shape" and set(selection) == {"type"}:
            selected = shape
        elif mode == "exact_subelement" and set(selection) == {"type", "subelement"}:
            if bool(metadata.get("transient_topology")) or bool(
                metadata.get("requires_semantic_interfaces")
            ):
                raise SurfaceCandidateError(
                    f"{context} targets regenerating scripted object {key[1]!r}; use "
                    "a published interface instead of transient topology.",
                    details={
                        "stage": "reference_selection",
                        "reference": {"document_uid": key[0], "object_name": key[1]},
                        "available_interfaces": sorted(
                            dict(metadata.get("published_interfaces") or {})
                        ),
                    },
                )
            resolved_subelement = str(selection["subelement"])
            selected = _subshape(shape, resolved_subelement, context=context)
        elif mode == "published_interface" and set(selection) == {
            "type",
            "interface_name",
        }:
            interface_name = str(selection["interface_name"] or "")
            interfaces = dict(metadata.get("published_interfaces") or {})
            raw = interfaces.get(interface_name)
            if not isinstance(raw, dict):
                raise SurfaceCandidateError(
                    f"{context} published interface {interface_name!r} does not exist "
                    f"on {key[1]!r}.",
                    details={
                        "stage": "reference_selection",
                        "available_interfaces": sorted(interfaces),
                    },
                )
            subelements = [str(value) for value in list(raw.get("subelements") or [])]
            if len(subelements) > 1:
                raise SurfaceCandidateError(
                    f"{context} interface {interface_name!r} resolves to multiple "
                    "subelements; Surface inputs require exactly one topology value.",
                    details={"stage": "reference_selection", "resolved": raw},
                )
            resolved_subelement = subelements[0] if subelements else ""
            selected = (
                _subshape(shape, resolved_subelement, context=context)
                if resolved_subelement
                else shape
            )
            semantic = {
                "interface_name": interface_name,
                "model_id": str(raw.get("model_id") or ""),
                "publication_name": str(raw.get("publication_name") or ""),
                "output_key": str(raw.get("output_key") or ""),
            }
        else:
            raise SurfaceCandidateError(
                f"{context}.selection has unsupported or malformed mode {mode!r}."
            )
        expected_shape = {
            "vertex": "Vertex",
            "edge": "Edge",
            "wire": "Wire",
            "face": "Face",
            "shell": "Shell",
            "solid": "Solid",
        }[output_type]
        _require_shape(selected, {expected_shape}, context=context)
        return selected.copy(), {
            "engine": "authenticated detached BREP selection",
            "reference": {
                "document_uid": key[0],
                "object_name": key[1],
                "brep_sha256": str(metadata.get("brep_sha256") or ""),
            },
            "selection": dict(selection),
            "resolved_subelement": resolved_subelement,
            "semantic_interface": semantic,
        }

    def _surface(
        self, payload: Mapping[str, Any], *, context: str
    ) -> tuple[Any, dict[str, Any]]:
        import Part

        points = list(payload["arguments"])[0]
        properties = dict(payload["properties"])
        if not isinstance(points, list) or not 2 <= len(points) <= 128:
            raise SurfaceCandidateError(f"{context}.points must contain 2-128 rows.")
        columns = len(points[0]) if isinstance(points[0], list) else 0
        if not 2 <= columns <= 128 or any(
            not isinstance(row, list) or len(row) != columns for row in points
        ):
            raise SurfaceCandidateError(
                f"{context}.points must be one rectangular 2x2 to 128x128 grid."
            )
        grid = [
            [
                _vector(point, context=f"{context}.points[{row_index}][{column_index}]")
                for column_index, point in enumerate(row)
            ]
            for row_index, row in enumerate(points)
        ]
        surface = Part.BSplineSurface()
        mode = str(properties["mode"])
        if mode == "interpolate":
            surface.interpolate(grid)
            engine = "Part.BSplineSurface.interpolate"
        elif mode == "approximate":
            continuity = {"C0": 0, "C1": 1, "C2": 2}.get(
                str(properties["continuity"])
            )
            if continuity is None:
                raise SurfaceCandidateError(
                    f"{context}.continuity must be C0, C1, or C2."
                )
            parametrization = {
                "uniform": "Uniform",
                "centripetal": "Centripetal",
                "chord_length": "ChordLength",
            }.get(str(properties["parametrization"]))
            if parametrization is None:
                raise SurfaceCandidateError(
                    f"{context}.parametrization is unsupported."
                )
            smoothing = properties["smoothing"]
            if not isinstance(smoothing, list) or len(smoothing) != 3:
                raise SurfaceCandidateError(
                    f"{context}.smoothing must contain three values."
                )
            surface.approximate(
                Points=grid,
                DegMin=int(properties["degree_min"]),
                DegMax=int(properties["degree_max"]),
                Continuity=continuity,
                Tolerance=_number(properties["tolerance"], context=f"{context}.tolerance"),
                ParamType=parametrization,
                LengthWeight=_number(smoothing[0], context=f"{context}.smoothing[0]"),
                CurvatureWeight=_number(smoothing[1], context=f"{context}.smoothing[1]"),
                TorsionWeight=_number(smoothing[2], context=f"{context}.smoothing[2]"),
            )
            engine = "Part.BSplineSurface.approximate"
        else:
            raise SurfaceCandidateError(f"{context}.mode {mode!r} is unsupported.")
        return surface.toShape(), {
            "engine": engine,
            "grid_rows": len(grid),
            "grid_columns": columns,
            "u_degree": int(surface.UDegree),
            "v_degree": int(surface.VDegree),
            "u_periodic": bool(surface.isUPeriodic()),
            "v_periodic": bool(surface.isVPeriodic()),
        }

    def _constraint_edges(
        self,
        raw: Any,
        *,
        expected_operation: str,
        context: str,
        depth: int,
    ) -> list[tuple[Any, Any | None, str]]:
        payload = _payload(raw, context=context)
        if payload["operation"] != expected_operation:
            raise SurfaceCandidateError(
                f"{context} must come from api.{expected_operation}."
            )
        curve, _data = self.build(
            payload["arguments"][0],
            context=f"{context}.curve",
            depth=depth + 1,
        )
        curve = _require_shape(curve, {"Edge", "Wire"}, context=f"{context}.curve")
        support_raw = payload["properties"]["support_face"]
        support = None
        if support_raw is not None:
            support, _data = self.build(
                support_raw,
                context=f"{context}.support_face",
                depth=depth + 1,
            )
            support = _require_shape(
                support, {"Face"}, context=f"{context}.support_face"
            )
        continuity = str(payload["properties"]["continuity"])
        if continuity not in _CONTINUITY_ORDER:
            raise SurfaceCandidateError(f"{context}.continuity is unsupported.")
        if support is None and continuity != "C0":
            raise SurfaceCandidateError(
                f"{context} requires a support face for {continuity}."
            )
        return [(edge, support, continuity) for edge in list(curve.Edges)]

    def _fill(
        self,
        payload: Mapping[str, Any],
        *,
        context: str,
        depth: int,
    ) -> tuple[Any, dict[str, Any]]:
        import Part

        boundaries = payload["arguments"][0]
        properties = dict(payload["properties"])
        if not isinstance(boundaries, list) or not 1 <= len(boundaries) <= 256:
            raise SurfaceCandidateError(
                f"{context}.boundaries must contain 1-256 constraints."
            )
        boundary_values: list[tuple[Any, Any | None, str]] = []
        for index, raw in enumerate(boundaries):
            boundary_values.extend(
                self._constraint_edges(
                    raw,
                    expected_operation="boundary",
                    context=f"{context}.boundaries[{index}]",
                    depth=depth,
                )
            )
        if len(boundary_values) > 1024:
            raise SurfaceCandidateError(
                f"{context} expands to more than 1024 boundary edges."
            )
        boundary_links = []
        boundary_faces = []
        boundary_orders = []
        for edge, support, continuity in boundary_values:
            holder_shape = edge if support is None else Part.makeCompound([edge, support])
            holder = self._holder(holder_shape, "FillBoundary")
            boundary_links.append((holder, ["Edge1"]))
            boundary_faces.append("" if support is None else "Face1")
            boundary_orders.append(_CONTINUITY_ORDER[continuity])

        unbound_links = []
        unbound_faces = []
        unbound_orders = []
        raw_curves = properties["curve_constraints"]
        if not isinstance(raw_curves, list) or len(raw_curves) > 256:
            raise SurfaceCandidateError(
                f"{context}.curve_constraints must contain at most 256 constraints."
            )
        for index, raw in enumerate(raw_curves):
            values = self._constraint_edges(
                raw,
                expected_operation="curve_constraint",
                context=f"{context}.curve_constraints[{index}]",
                depth=depth,
            )
            for edge, support, continuity in values:
                holder_shape = edge if support is None else Part.makeCompound([edge, support])
                holder = self._holder(holder_shape, "FillCurve")
                unbound_links.append((holder, ["Edge1"]))
                unbound_faces.append("" if support is None else "Face1")
                unbound_orders.append(_CONTINUITY_ORDER[continuity])

        free_links = []
        free_orders = []
        raw_faces = properties["face_constraints"]
        if not isinstance(raw_faces, list) or len(raw_faces) > 256:
            raise SurfaceCandidateError(
                f"{context}.face_constraints must contain at most 256 constraints."
            )
        for index, raw in enumerate(raw_faces):
            constraint = _payload(raw, context=f"{context}.face_constraints[{index}]")
            if constraint["operation"] != "face_constraint":
                raise SurfaceCandidateError(
                    f"{context}.face_constraints[{index}] must come from api.face_constraint."
                )
            face, _data = self.build(
                constraint["arguments"][0],
                context=f"{context}.face_constraints[{index}].face",
                depth=depth + 1,
            )
            face = _require_shape(
                face, {"Face"}, context=f"{context}.face_constraints[{index}].face"
            )
            continuity = str(constraint["properties"]["continuity"])
            if continuity not in _CONTINUITY_ORDER:
                raise SurfaceCandidateError(
                    f"{context}.face_constraints[{index}].continuity is unsupported."
                )
            holder = self._holder(face, "FillFace")
            free_links.append((holder, ["Face1"]))
            free_orders.append(_CONTINUITY_ORDER[continuity])

        point_links = []
        raw_points = properties["point_constraints"]
        if not isinstance(raw_points, list) or len(raw_points) > 256:
            raise SurfaceCandidateError(
                f"{context}.point_constraints must contain at most 256 constraints."
            )
        for index, raw in enumerate(raw_points):
            constraint = _payload(raw, context=f"{context}.point_constraints[{index}]")
            if constraint["operation"] != "point_constraint":
                raise SurfaceCandidateError(
                    f"{context}.point_constraints[{index}] must come from api.point_constraint."
                )
            point_raw = constraint["arguments"][0]
            if isinstance(point_raw, dict) and point_raw.get("domain") is not None:
                vertex, _data = self.build(
                    point_raw,
                    context=f"{context}.point_constraints[{index}].point",
                    depth=depth + 1,
                )
                vertex = _require_shape(
                    vertex,
                    {"Vertex"},
                    context=f"{context}.point_constraints[{index}].point",
                )
            else:
                vertex = Part.Vertex(
                    _vector(
                        point_raw,
                        context=f"{context}.point_constraints[{index}].point",
                    )
                )
            holder = self._holder(vertex, "FillPoint")
            point_links.append((holder, ["Vertex1"]))

        initial_link = None
        if properties["initial_face"] is not None:
            initial, _data = self.build(
                properties["initial_face"],
                context=f"{context}.initial_face",
                depth=depth + 1,
            )
            initial = _require_shape(initial, {"Face"}, context=f"{context}.initial_face")
            initial_holder = self._holder(initial, "FillInitial")
            initial_link = (initial_holder, ["Face1"])

        feature = self.document.addObject("Surface::Filling", self._next_name("Filling"))
        if feature is None:
            raise SurfaceCandidateError("The native Surface::Filling type is unavailable.")
        feature.BoundaryEdges = boundary_links
        feature.BoundaryFaces = boundary_faces
        feature.BoundaryOrder = boundary_orders
        feature.UnboundEdges = unbound_links
        feature.UnboundFaces = unbound_faces
        feature.UnboundOrder = unbound_orders
        feature.FreeFaces = free_links
        feature.FreeOrder = free_orders
        feature.Points = point_links
        if initial_link is not None:
            feature.InitialFace = initial_link
        feature.Degree = int(properties["degree"])
        feature.PointsOnCurve = int(properties["points_on_curve"])
        feature.Iterations = int(properties["iterations"])
        feature.Anisotropy = bool(properties["anisotropy"])
        feature.Tolerance2d = float(properties["tolerance_2d"])
        feature.Tolerance3d = float(properties["tolerance_3d"])
        feature.TolAngular = float(properties["angular_tolerance"])
        feature.TolCurvature = float(properties["curvature_tolerance"])
        feature.MaximumDegree = int(properties["maximum_degree"])
        feature.MaximumSegments = int(properties["maximum_segments"])
        shape, state = self._native_result(feature, "fill")
        return shape, {
            "engine": "Surface::Filling",
            "native_type": str(feature.TypeId),
            "native_state": state,
            "boundary_edge_count": len(boundary_links),
            "curve_constraint_edge_count": len(unbound_links),
            "face_constraint_count": len(free_links),
            "point_constraint_count": len(point_links),
            "has_initial_face": initial_link is not None,
            "native_properties": {
                "degree": int(feature.Degree),
                "points_on_curve": int(feature.PointsOnCurve),
                "iterations": int(feature.Iterations),
                "anisotropy": bool(feature.Anisotropy),
                "tolerance_2d": float(feature.Tolerance2d),
                "tolerance_3d": float(feature.Tolerance3d),
                "angular_tolerance": float(feature.TolAngular),
                "curvature_tolerance": float(feature.TolCurvature),
                "maximum_degree": int(feature.MaximumDegree),
                "maximum_segments": int(feature.MaximumSegments),
            },
        }

    def _blend(
        self,
        payload: Mapping[str, Any],
        *,
        context: str,
        depth: int,
    ) -> tuple[Any, dict[str, Any]]:
        boundaries = payload["arguments"][0]
        properties = dict(payload["properties"])
        if not isinstance(boundaries, list) or not 2 <= len(boundaries) <= 4:
            raise SurfaceCandidateError(
                f"{context}.boundaries must contain 2-4 curves."
            )
        reversed_values = properties["reversed"]
        if not isinstance(reversed_values, list) or len(reversed_values) != len(boundaries):
            raise SurfaceCandidateError(
                f"{context}.reversed must contain one value per boundary."
            )
        links = []
        preconditioned_boundaries = 0
        for index, raw in enumerate(boundaries):
            curve, _data = self.build(
                raw,
                context=f"{context}.boundaries[{index}]",
                depth=depth + 1,
            )
            curve = _require_shape(
                curve, {"Edge", "Wire"}, context=f"{context}.boundaries[{index}]"
            )
            edges = list(curve.Edges)
            if len(edges) != 1:
                raise SurfaceCandidateError(
                    f"{context}.boundaries[{index}] must contain exactly one edge, "
                    f"not {len(edges)}."
                )
            edge = edges[0]
            curve_name = type(edge.Curve).__name__.removeprefix("Part.")
            if curve_name == "Line":
                vertices = list(edge.Vertexes)
                if len(vertices) != 2:
                    raise SurfaceCandidateError(
                        f"{context}.boundaries[{index}] line has no two endpoints."
                    )
                import Part

                bezier = Part.BezierCurve()
                bezier.setPoles([vertices[0].Point, vertices[1].Point])
                edge = bezier.toShape()
                preconditioned_boundaries += 1
            elif curve_name not in {"BezierCurve", "BSplineCurve"}:
                converted = edge.toNurbs()
                converted_edges = list(converted.Edges)
                if len(converted_edges) != 1:
                    raise SurfaceCandidateError(
                        f"{context}.boundaries[{index}] could not be converted to one "
                        "native B-spline edge."
                    )
                edge = converted_edges[0]
                preconditioned_boundaries += 1
            holder = self._holder(edge, "BlendBoundary")
            links.append((holder, ["Edge1"]))
        feature = self.document.addObject(
            "Surface::GeomFillSurface", self._next_name("Blend")
        )
        if feature is None:
            raise SurfaceCandidateError(
                "The native Surface::GeomFillSurface type is unavailable."
            )
        feature.BoundaryList = links
        feature.ReversedList = [bool(value) for value in reversed_values]
        native_style = {
            "stretched": "Stretched",
            "coons": "Coons",
            "curved": "Curved",
        }.get(str(properties["style"]))
        if native_style is None:
            raise SurfaceCandidateError(f"{context}.style is unsupported.")
        feature.FillType = native_style
        shape, state = self._native_result(feature, "blend")
        return shape, {
            "engine": "Surface::GeomFillSurface",
            "native_type": str(feature.TypeId),
            "native_state": state,
            "boundary_count": len(links),
            "preconditioned_boundary_count": preconditioned_boundaries,
            "fill_type": str(feature.FillType),
            "reversed": [bool(value) for value in list(feature.ReversedList)],
        }

    def _extend(
        self,
        payload: Mapping[str, Any],
        *,
        context: str,
        depth: int,
    ) -> tuple[Any, dict[str, Any]]:
        face, _data = self.build(
            payload["arguments"][0], context=f"{context}.face", depth=depth + 1
        )
        face = _require_shape(face, {"Face"}, context=f"{context}.face")
        properties = dict(payload["properties"])
        holder = self._holder(face, "ExtendFace")
        feature = self.document.addObject("Surface::Extend", self._next_name("Extend"))
        if feature is None:
            raise SurfaceCandidateError("The native Surface::Extend type is unavailable.")
        feature.Face = (holder, ["Face1"])
        feature.ExtendUSymetric = False
        feature.ExtendVSymetric = False
        feature.ExtendUNeg = float(properties["u_negative"])
        feature.ExtendUPos = float(properties["u_positive"])
        feature.ExtendVNeg = float(properties["v_negative"])
        feature.ExtendVPos = float(properties["v_positive"])
        feature.Tolerance = float(properties["tolerance"])
        feature.SampleU = int(properties["samples_u"])
        feature.SampleV = int(properties["samples_v"])
        shape, state = self._native_result(feature, "extend")
        return shape, {
            "engine": "Surface::Extend",
            "native_type": str(feature.TypeId),
            "native_state": state,
            "native_properties": {
                "u_negative": float(feature.ExtendUNeg),
                "u_positive": float(feature.ExtendUPos),
                "v_negative": float(feature.ExtendVNeg),
                "v_positive": float(feature.ExtendVPos),
                "tolerance": float(feature.Tolerance),
                "samples_u": int(feature.SampleU),
                "samples_v": int(feature.SampleV),
            },
        }

    def _loft(
        self,
        payload: Mapping[str, Any],
        *,
        context: str,
        depth: int,
    ) -> tuple[Any, dict[str, Any]]:
        import Part

        sections = payload["arguments"][0]
        properties = dict(payload["properties"])
        if not isinstance(sections, list) or not 2 <= len(sections) <= 256:
            raise SurfaceCandidateError(f"{context}.sections must contain 2-256 curves.")
        wires = []
        section_types = []
        for index, raw in enumerate(sections):
            section, _data = self.build(
                raw,
                context=f"{context}.sections[{index}]",
                depth=depth + 1,
            )
            section = _require_shape(
                section, {"Edge", "Wire"}, context=f"{context}.sections[{index}]"
            )
            section_types.append(_shape_type(section))
            wires.append(section if _shape_type(section) == "Wire" else Part.Wire([section]))
        shape = Part.makeLoft(
            wires,
            solid=bool(properties["solid"]),
            ruled=bool(properties["ruled"]),
            closed=bool(properties["closed"]),
            max_degree=int(properties["max_degree"]),
        )
        return shape, {
            "engine": "Part.makeLoft",
            "section_count": len(wires),
            "section_types": section_types,
            "solid": bool(properties["solid"]),
            "ruled": bool(properties["ruled"]),
            "closed": bool(properties["closed"]),
            "max_degree": int(properties["max_degree"]),
        }

    def _thicken(
        self,
        payload: Mapping[str, Any],
        *,
        context: str,
        depth: int,
    ) -> tuple[Any, dict[str, Any]]:
        shape, _data = self.build(
            payload["arguments"][0], context=f"{context}.shape", depth=depth + 1
        )
        shape = _require_shape(
            shape, {"Face", "Shell", "Solid"}, context=f"{context}.shape"
        )
        thickness = _number(payload["arguments"][1], context=f"{context}.thickness")
        if abs(thickness) <= 1.0e-12:
            raise SurfaceCandidateError(f"{context}.thickness must be non-zero.")
        properties = dict(payload["properties"])
        raw_faces = properties["remove_faces"]
        if not isinstance(raw_faces, list) or len(raw_faces) > 256:
            raise SurfaceCandidateError(
                f"{context}.remove_faces must contain at most 256 indices."
            )
        faces = list(shape.Faces)
        selected = []
        for raw_index in raw_faces:
            index = int(raw_index)
            if index < 1 or index > len(faces):
                raise SurfaceCandidateError(
                    f"{context}.remove_faces index {index} is outside 1-{len(faces)}."
                )
            selected.append(faces[index - 1])
        joins = {"arc": 0, "tangent": 1, "intersection": 2}
        join = str(properties["join"])
        if join not in joins:
            raise SurfaceCandidateError(f"{context}.join {join!r} is unsupported.")
        tolerance = float(properties["tolerance"])
        if _shape_type(shape) == "Face" and not selected:
            result = shape.makeOffsetShape(
                thickness,
                tolerance,
                inter=False,
                self_inter=False,
                offsetMode=0,
                join=joins[join],
                fill=True,
            )
            engine = "TopoShape.makeOffsetShape(fill=True)"
        else:
            result = shape.makeThickness(
                selected,
                thickness,
                tolerance,
                False,
                False,
                0,
                joins[join],
            )
            engine = "TopoShape.makeThickness"
        return result, {
            "engine": engine,
            "source_shape_type": _shape_type(shape),
            "removed_face_indices": [int(value) for value in raw_faces],
            "thickness": thickness,
            "tolerance": tolerance,
            "join": join,
        }

    def _shell(
        self,
        payload: Mapping[str, Any],
        *,
        context: str,
        depth: int,
    ) -> tuple[Any, dict[str, Any]]:
        import Part

        values = payload["arguments"][0]
        properties = dict(payload["properties"])
        if not isinstance(values, list) or not 1 <= len(values) <= 1024:
            raise SurfaceCandidateError(f"{context}.faces must contain 1-1024 values.")
        links = []
        face_count = 0
        for value_index, raw in enumerate(values):
            value, _data = self.build(
                raw,
                context=f"{context}.faces[{value_index}]",
                depth=depth + 1,
            )
            value = _require_shape(
                value, {"Face", "Shell"}, context=f"{context}.faces[{value_index}]"
            )
            for face in list(value.Faces):
                holder = self._holder(face, "SewFace")
                links.append((holder, ["Face1"]))
                face_count += 1
        feature = self.document.addObject("Surface::Sewing", self._next_name("Sewing"))
        if feature is None:
            raise SurfaceCandidateError("The native Surface::Sewing type is unavailable.")
        feature.ShapeList = links
        feature.Tolerance = float(properties["tolerance"])
        feature.SewingOption = True
        feature.DegenerateShape = False
        feature.CutFreeEdges = bool(properties["cut_free_edges"])
        feature.Nonmanifold = bool(properties["nonmanifold"])
        native_shape, state = self._native_result(feature, "shell")
        shells = list(native_shape.Shells)
        if _shape_type(native_shape) == "Shell":
            shell = native_shape
        elif _shape_type(native_shape) == "Face":
            shell = Part.makeShell([native_shape])
        elif len(shells) == 1:
            shell = shells[0]
        else:
            raise SurfaceCandidateError(
                f"api.shell sewing produced {_shape_type(native_shape)} with "
                f"{len(shells)} shells; exactly one connected shell is required.",
                details={
                    "stage": "shape_contract",
                    "operation": "shell",
                    "native_shape_type": _shape_type(native_shape),
                    "shell_count": len(shells),
                    "face_count": face_count,
                },
            )
        result = Part.makeSolid(shell) if bool(properties["make_solid"]) else shell
        return result, {
            "engine": "Surface::Sewing",
            "native_type": str(feature.TypeId),
            "native_state": state,
            "input_face_count": face_count,
            "native_shape_type": _shape_type(native_shape),
            "native_shell_count": len(shells),
            "make_solid": bool(properties["make_solid"]),
            "native_properties": {
                "tolerance": float(feature.Tolerance),
                "sewing_option": bool(feature.SewingOption),
                "degenerate_shape": bool(feature.DegenerateShape),
                "cut_free_edges": bool(feature.CutFreeEdges),
                "nonmanifold": bool(feature.Nonmanifold),
            },
        }


def validate_and_build_surface(
    document: Any,
    raw_result: Mapping[str, Any],
    expected_outputs: list[dict[str, str]],
    root: Path,
    *,
    max_shape_subelements: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Execute, validate and serialize every declared Surface output."""

    evaluator = SurfaceEvaluator(document)
    outputs: list[dict[str, Any]] = []
    summary_outputs: list[dict[str, Any]] = []
    for index, expected in enumerate(expected_outputs):
        name = str(expected.get("name") or "")
        expected_type = str(expected.get("type") or "")
        raw = raw_result.get(name)
        definition = validate_surface_definition(raw, context=f"result[{name!r}]")
        if str(definition["output_type"]) != expected_type:
            raise SurfaceCandidateError(
                f"Output {name!r} returned type {definition['output_type']!r}; "
                f"expected {expected_type!r}.",
                details={
                    "stage": "result_contract",
                    "output_name": name,
                    "expected_type": expected_type,
                    "received_type": str(definition["output_type"]),
                    "correction": (
                        f"Return a Surface api value of type {expected_type!r} for "
                        f"result[{name!r}], or reconfigure expected_outputs together."
                    ),
                },
            )
        shape, surface_data = evaluator.build(
            raw, context=f"result[{name!r}]"
        )
        facts = part_shape_facts(shape, max_subelements=max_shape_subelements)
        if facts["null"] or not facts["valid"]:
            raise SurfaceCandidateError(
                f"Output {name!r} is not a valid BREP Shape.",
                details={
                    "stage": "shape_contract",
                    "output_name": name,
                    "operation": str(definition["operation"]),
                    "facts": _json_safe(facts),
                    "correction": _operation_correction(
                        str(definition["operation"])
                    ),
                },
            )
        relative = Path("outputs") / f"output-{index:03d}.brep"
        target = root / relative
        shape.exportBrep(str(target))
        if not target.is_file() or target.stat().st_size <= 0:
            raise SurfaceCandidateError(
                f"Could not export Surface output {name!r}.",
                details={
                    "stage": "artifact_export",
                    "output_name": name,
                    "operation": str(definition["operation"]),
                    "correction": (
                        "Retry this candidate; if export fails again, simplify only this "
                        "output while the prior accepted revision remains live."
                    ),
                },
            )
        item = {
            "name": name,
            "type": expected_type,
            "definition": definition,
            "artifact_kind": "brep",
            "artifact_path": str(relative),
            "facts": facts,
            "surface_data": surface_data,
            "operation_diagnostics": {
                "operation": str(definition["operation"]),
                "engine": str(surface_data.get("engine") or ""),
                "shape_type": str(facts["shape_type"]),
                "valid": bool(facts["valid"]),
            },
        }
        outputs.append(item)
        summary_outputs.append(
            {
                "name": name,
                "type": expected_type,
                "operation": str(definition["operation"]),
                "engine": str(surface_data.get("engine") or ""),
                "shape_type": str(facts["shape_type"]),
                "solids": int(facts["solids"]),
                "shells": int(facts["shells"]),
                "faces": int(facts["faces"]),
                "edges": int(facts["edges"]),
            }
        )
    return outputs, {
        "schema": "vibecad-vibescript-surface-validation-v1",
        "output_count": len(outputs),
        "operation_count": evaluator.operation_count,
        "outputs": summary_outputs,
    }
