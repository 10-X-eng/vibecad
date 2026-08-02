# SPDX-License-Identifier: LGPL-2.1-or-later

"""Isolated native Sketcher evaluator for production VibeScript programs."""

from __future__ import annotations

from collections.abc import Callable, Mapping
import json
import math
from pathlib import Path
from types import MappingProxyType
from typing import Any

from vibescript_domain_api import DomainValue


_NATIVE_CONSTRAINT = {
    "coincident": "Coincident",
    "horizontal": "Horizontal",
    "vertical": "Vertical",
    "parallel": "Parallel",
    "perpendicular": "Perpendicular",
    "tangent": "Tangent",
    "distance": "Distance",
    "distance_x": "DistanceX",
    "distance_y": "DistanceY",
    "angle": "Angle",
    "angle_via_point": "AngleViaPoint",
    "radius": "Radius",
    "diameter": "Diameter",
    "equal": "Equal",
    "point_on_object": "PointOnObject",
    "symmetric": "Symmetric",
    "block": "Block",
    "weight": "Weight",
    "snells_law": "SnellsLaw",
    "group": "Group",
    "text": "Text",
}
_INTERNAL_ALIGNMENT = {
    "ellipse_major_diameter": "EllipseMajorDiameter",
    "ellipse_minor_diameter": "EllipseMinorDiameter",
    "ellipse_focus1": "EllipseFocus1",
    "ellipse_focus2": "EllipseFocus2",
    "hyperbola_major_diameter": "HyperbolaMajor",
    "hyperbola_minor_diameter": "HyperbolaMinor",
    "hyperbola_focus": "HyperbolaFocus",
    "parabola_focus": "ParabolaFocus",
    "parabola_focal_axis": "ParabolaFocalAxis",
    "bspline_control_point": "BSplineControlPoint",
    "bspline_knot_point": "BSplineKnotPoint",
}
_DIMENSIONAL = frozenset(
    {
        "distance",
        "distance_x",
        "distance_y",
        "angle",
        "angle_via_point",
        "radius",
        "diameter",
        "weight",
        "snells_law",
    }
)
_POINT_POSITION = {
    "none": 0,
    "point": 1,
    "start": 1,
    "end": 2,
    "center": 3,
    "origin": 1,
}
_MAX_DIAGNOSTIC_CONSTRAINTS = 512
_MAX_UNDERCONSTRAINT_SUGGESTIONS = 128
_MAX_EQUALITY_DIAGNOSTIC_GEOMETRY = 64
_MAX_PROFILE_OPEN_VERTICES = 128
_MAX_PROFILE_ENDPOINT_MATCHES = 8
_SUGGESTION_POSITION_TOLERANCE_MM = 1.0e-6
_SUGGESTION_ANGLE_TOLERANCE_DEGREES = 0.5
_SUGGESTION_EQUALITY_TOLERANCE_MM = 1.0e-6
_PROFILE_ENDPOINT_MATCH_TOLERANCE_MM = 1.0e-6
_SKETCHER_REFERENCES: Mapping[tuple[str, str], Mapping[str, Any]] = MappingProxyType({})


class SketcherCandidateError(RuntimeError):
    """A model-facing Sketcher failure with structured corrective details."""

    def __init__(self, message: str, *, details: Mapping[str, Any] | None = None) -> None:
        self.details = dict(details or {})
        super().__init__(message)


def configure_sketcher_references(root: Path, entries: list[dict[str, Any]]) -> None:
    """Authenticate support BREPs and retain bounded semantic contracts."""

    from vibescript_part_worker import configure_part_references

    configure_part_references(root, entries)
    references: dict[tuple[str, str], Mapping[str, Any]] = {}
    for index, raw in enumerate(entries):
        if not isinstance(raw, dict):
            raise SketcherCandidateError(
                f"document_references[{index}] must be an object."
            )
        key = (
            str(raw.get("document_uid") or ""),
            str(raw.get("object_name") or ""),
        )
        if not all(key) or key in references:
            raise SketcherCandidateError(
                f"document_references[{index}] has missing or duplicate identity."
            )
        interfaces = raw.get("published_interfaces", {})
        if not isinstance(interfaces, dict) or len(interfaces) > 64:
            raise SketcherCandidateError(
                f"document_references[{index}] has invalid published interfaces."
            )
        references[key] = MappingProxyType(
            {
                "label": str(raw.get("label") or ""),
                "type_id": str(raw.get("type_id") or ""),
                "shape_type": str(raw.get("shape_type") or ""),
                "source_kind": str(raw.get("source_kind") or "native"),
                "source_revision": str(raw.get("source_revision") or ""),
                "transient_topology": bool(raw.get("transient_topology")),
                "requires_semantic_interfaces": bool(
                    raw.get("requires_semantic_interfaces")
                ),
                "published_interfaces": interfaces,
            }
        )
    global _SKETCHER_REFERENCES
    _SKETCHER_REFERENCES = MappingProxyType(references)


def _payload(value: Any, *, context: str) -> dict[str, Any]:
    if isinstance(value, DomainValue):
        payload = value.to_payload()
    elif isinstance(value, Mapping):
        payload = dict(value)
    else:
        raise SketcherCandidateError(
            f"{context} must be a value returned by the active Sketcher api."
        )
    if str(payload.get("domain") or "") != "sketcher":
        raise SketcherCandidateError(f"{context} belongs to another VibeScript domain.")
    return payload


def _argument(payload: Mapping[str, Any], index: int, *, context: str) -> Any:
    arguments = list(payload.get("arguments") or [])
    if index >= len(arguments):
        raise SketcherCandidateError(f"{context} is missing argument {index}.")
    return arguments[index]


def _properties(payload: Mapping[str, Any]) -> dict[str, Any]:
    raw = payload.get("properties")
    if not isinstance(raw, Mapping):
        raise SketcherCandidateError("A Sketcher graph value has malformed properties.")
    return dict(raw)


def _number(value: Any, *, context: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SketcherCandidateError(f"{context} must be a finite number.")
    result = float(value)
    if not math.isfinite(result):
        raise SketcherCandidateError(f"{context} must be finite.")
    return result


def _point(value: Any, *, context: str):
    import FreeCAD as App

    if not isinstance(value, list) or len(value) != 2:
        raise SketcherCandidateError(f"{context} must be a validated planar [x,y] point.")
    return App.Vector(
        _number(value[0], context=f"{context}[0]"),
        _number(value[1], context=f"{context}[1]"),
        0.0,
    )


def sketch_geometry_from_payload(payload: Mapping[str, Any]):
    """Build one native Part geometry from a validated serialized API value."""

    import FreeCAD as App
    import Part

    operation = str(payload.get("operation") or "")
    properties = _properties(payload)
    if str(payload.get("output_type") or "") != "sketch_geometry":
        raise SketcherCandidateError(
            f"api.{operation} did not produce Sketcher geometry."
        )
    if operation == "point":
        return Part.Point(
            _point(
                _argument(payload, 0, context="api.point"),
                context="api.point.position",
            )
        )
    if operation == "line":
        return Part.LineSegment(
            _point(_argument(payload, 0, context="api.line"), context="api.line.start"),
            _point(_argument(payload, 1, context="api.line"), context="api.line.end"),
        )
    if operation == "arc":
        return Part.Arc(
            _point(_argument(payload, 0, context="api.arc"), context="api.arc.start"),
            _point(_argument(payload, 1, context="api.arc"), context="api.arc.through"),
            _point(_argument(payload, 2, context="api.arc"), context="api.arc.end"),
        )
    if operation == "circle":
        return Part.Circle(
            _point(_argument(payload, 0, context="api.circle"), context="api.circle.center"),
            App.Vector(0.0, 0.0, 1.0),
            _number(_argument(payload, 1, context="api.circle"), context="api.circle.radius"),
        )
    if operation == "ellipse":
        center = _point(
            _argument(payload, 0, context="api.ellipse"),
            context="api.ellipse.center",
        )
        geometry = Part.Ellipse(
            center,
            _number(
                _argument(payload, 1, context="api.ellipse"),
                context="api.ellipse.major_radius",
            ),
            _number(
                _argument(payload, 2, context="api.ellipse"),
                context="api.ellipse.minor_radius",
            ),
        )
        rotation = _number(
            properties.get("rotation_degrees", 0.0),
            context="api.ellipse.rotation_degrees",
        )
        if abs(rotation) > 1.0e-12:
            angle = math.radians(rotation)
            geometry.XAxis = App.Vector(math.cos(angle), math.sin(angle), 0.0)
        return geometry
    if operation in {"elliptic_arc", "hyperbolic_arc"}:
        center = _point(
            _argument(payload, 0, context=f"api.{operation}"),
            context=f"api.{operation}.center",
        )
        major = _number(
            _argument(payload, 1, context=f"api.{operation}"),
            context=f"api.{operation}.major_radius",
        )
        minor = _number(
            _argument(payload, 2, context=f"api.{operation}"),
            context=f"api.{operation}.minor_radius",
        )
        conic = (
            Part.Ellipse(center, major, minor)
            if operation == "elliptic_arc"
            else Part.Hyperbola(center, major, minor)
        )
        rotation = _number(
            properties.get("rotation_degrees", 0.0),
            context=f"api.{operation}.rotation_degrees",
        )
        if abs(rotation) > 1.0e-12:
            angle = math.radians(rotation)
            conic.XAxis = App.Vector(math.cos(angle), math.sin(angle), 0.0)
        start = _number(
            _argument(payload, 3, context=f"api.{operation}"),
            context=f"api.{operation}.start_parameter",
        )
        end = _number(
            _argument(payload, 4, context=f"api.{operation}"),
            context=f"api.{operation}.end_parameter",
        )
        return (
            Part.ArcOfEllipse(conic, start, end)
            if operation == "elliptic_arc"
            else Part.ArcOfHyperbola(conic, start, end)
        )
    if operation == "parabolic_arc":
        vertex = _point(
            _argument(payload, 0, context="api.parabolic_arc"),
            context="api.parabolic_arc.vertex",
        )
        focal_length = _number(
            _argument(payload, 1, context="api.parabolic_arc"),
            context="api.parabolic_arc.focal_length",
        )
        rotation = math.radians(
            _number(
                properties.get("rotation_degrees", 0.0),
                context="api.parabolic_arc.rotation_degrees",
            )
        )
        focus = vertex + App.Vector(
            focal_length * math.cos(rotation),
            focal_length * math.sin(rotation),
            0.0,
        )
        parabola = Part.Parabola(focus, vertex, App.Vector(0.0, 0.0, 1.0))
        return Part.ArcOfParabola(
            parabola,
            _number(
                _argument(payload, 2, context="api.parabolic_arc"),
                context="api.parabolic_arc.start_parameter",
            ),
            _number(
                _argument(payload, 3, context="api.parabolic_arc"),
                context="api.parabolic_arc.end_parameter",
            ),
        )
    if operation == "bspline":
        points = _argument(payload, 0, context="api.bspline")
        if not isinstance(points, list) or len(points) < 3:
            raise SketcherCandidateError("api.bspline.points must contain at least three points.")
        poles = [
            _point(point, context=f"api.bspline.points[{index}]")
            for index, point in enumerate(points)
        ]
        curve = Part.BSplineCurve()
        degree = properties.get("degree")
        if degree is None:
            curve.interpolate(
                Points=poles,
                PeriodicFlag=bool(properties.get("periodic")),
                Tolerance=_number(
                    properties.get("tolerance", 1.0e-7),
                    context="api.bspline.tolerance",
                ),
            )
        else:
            knots = properties.get("knots")
            multiplicities = properties.get("multiplicities")
            weights = properties.get("weights")
            if not all(isinstance(item, list) for item in (knots, multiplicities, weights)):
                raise SketcherCandidateError(
                    "api.bspline exact knots, multiplicities, and weights must be arrays."
                )
            curve.buildFromPolesMultsKnots(
                poles,
                [int(item) for item in multiplicities],
                [float(item) for item in knots],
                bool(properties.get("periodic")),
                int(degree),
                [float(item) for item in weights] if weights else None,
                bool(weights),
            )
        return curve
    if operation == "external_geometry":
        raise SketcherCandidateError(
            "api.external_geometry must be resolved through an authenticated document reference."
        )
    raise SketcherCandidateError(
        f"Unsupported Sketcher geometry operation {operation!r}.",
        details={
            "stage": "geometry_type",
            "operation": operation,
            "supported_operations": [
                "point",
                "line",
                "arc",
                "circle",
                "ellipse",
                "elliptic_arc",
                "hyperbolic_arc",
                "parabolic_arc",
                "bspline",
            ],
        },
    )


def _entity(
    raw: Any,
    geometry_indexes: Mapping[str, int],
    *,
    context: str,
) -> tuple[int, int]:
    if not isinstance(raw, Mapping):
        raise SketcherCandidateError(f"{context} must be an entity object.")
    point = str(raw.get("point") or "none")
    position = _POINT_POSITION.get(point)
    if position is None:
        raise SketcherCandidateError(f"{context}.point {point!r} is unsupported.")
    if "external" in raw:
        external = str(raw.get("external") or "")
        if external == "x_axis":
            return -1, 0
        if external == "y_axis":
            return -2, 0
        if external == "origin":
            return -1, 1
        raise SketcherCandidateError(f"{context}.external {external!r} is unsupported.")
    geometry = raw.get("geometry")
    if not isinstance(geometry, Mapping):
        raise SketcherCandidateError(f"{context}.geometry is malformed.")
    properties = _properties(geometry)
    graph_id = str(properties.get("graph_id") or "")
    if graph_id not in geometry_indexes:
        raise SketcherCandidateError(
            f"{context} references geometry not returned in this sketch graph.",
            details={
                "stage": "constraint_graph",
                "entity": context,
                "graph_id": graph_id,
                "available_geometry_ids": sorted(geometry_indexes),
                "correction": (
                    "Reuse the exact geometry variable already included in api.sketch; "
                    "do not recreate equivalent geometry for this constraint."
                ),
            },
        )
    return int(geometry_indexes[graph_id]), position


def _projected_point_selectors(native_geometry: Any) -> tuple[str, frozenset[str]]:
    """Return the point selectors supported by one native projected geometry."""

    native_type = type(native_geometry).__name__
    lowered = native_type.lower()
    if "point" in lowered:
        # Sketcher's generic position 1 is spelled both ``point`` and ``start``
        # by the source API.  Retain both aliases for a projected point so an
        # EdgeN that collapses to a point on the sketch plane stays intuitive.
        selectors = frozenset({"point", "start"})
    elif any(
        token in lowered
        for token in ("arc", "circle", "ellipse", "hyperbola", "parabola")
    ):
        selectors = frozenset({"start", "end", "center"})
    else:
        selectors = frozenset({"start", "end"})
    return native_type, selectors


def _validate_external_constraint_points(
    constraint_payloads: list[dict[str, Any]],
    contracts: Mapping[str, tuple[str, frozenset[str]]],
) -> None:
    """Reject impossible external point selectors before native construction."""

    for constraint_index, definition in enumerate(constraint_payloads):
        kind = str(_argument(definition, 0, context="api.constraint") or "")
        entities = _argument(definition, 1, context="api.constraint")
        if not isinstance(entities, list):
            continue
        for entity_index, raw in enumerate(entities):
            if not isinstance(raw, Mapping) or "geometry" not in raw:
                continue
            geometry = raw.get("geometry")
            if not isinstance(geometry, Mapping):
                continue
            graph_id = str(_properties(geometry).get("graph_id") or "")
            contract = contracts.get(graph_id)
            if contract is None:
                continue
            selected = str(raw.get("point") or "none")
            if selected == "none":
                continue
            native_type, allowed = contract
            if selected in allowed:
                continue
            raise SketcherCandidateError(
                f"api.constraint({kind!r}) entity {entity_index} selects "
                f"{selected!r} on external {native_type} geometry {graph_id!r}; "
                f"use one of {', '.join(sorted(allowed))}.",
                details={
                    "stage": "external_geometry_point_selector",
                    "constraint_index": constraint_index,
                    "constraint_kind": kind,
                    "entity_index": entity_index,
                    "graph_id": graph_id,
                    "native_type": native_type,
                    "selected_point": selected,
                    "allowed_points": sorted(allowed),
                    "correction": (
                        "Choose a point selector supported by the projected native geometry, "
                        "or constrain the whole external geometry with point='none'."
                    ),
                },
            )


def sketch_constraint_from_payload(
    payload: Mapping[str, Any],
    geometry_indexes: Mapping[str, int],
    sketch: Any,
):
    """Build one native Sketcher.Constraint from stable serialized graph ids."""

    import Sketcher

    if (
        str(payload.get("operation") or "") != "constraint"
        or str(payload.get("output_type") or "") != "sketch_constraint"
    ):
        raise SketcherCandidateError("A constraint graph value must come from api.constraint.")
    kind = str(_argument(payload, 0, context="api.constraint") or "")
    raw_entities = _argument(payload, 1, context="api.constraint")
    if not isinstance(raw_entities, list):
        raise SketcherCandidateError("api.constraint.entities must be an array.")
    entities = [
        _entity(raw, geometry_indexes, context=f"api.constraint.entities[{index}]")
        for index, raw in enumerate(raw_entities)
    ]
    properties = _properties(payload)
    value = properties.get("value")
    if kind in {"angle", "angle_via_point"} and value is not None:
        value = math.radians(_number(value, context="api.constraint.value"))
    elif value is not None:
        value = _number(value, context="api.constraint.value")
    active = bool(properties.get("active", True))
    driving = bool(properties.get("driving", True))
    native = _NATIVE_CONSTRAINT.get(kind)
    args: list[Any]
    if kind in {"horizontal", "vertical", "block"}:
        args = [native, entities[0][0]]
    elif kind in {"parallel", "equal"}:
        args = [native, entities[0][0], entities[1][0]]
    elif kind in {"perpendicular", "tangent"}:
        first, second = entities
        if first[1] == 0 and second[1] == 0:
            args = [native, first[0], second[0]]
        elif first[1] != 0 and second[1] == 0:
            args = [native, first[0], first[1], second[0]]
        else:
            args = [native, first[0], first[1], second[0], second[1]]
    elif kind == "coincident":
        first, second = entities
        args = [native, first[0], first[1], second[0], second[1]]
    elif kind == "point_on_object":
        first, second = entities
        args = [native, first[0], first[1], second[0]]
    elif kind == "distance":
        if len(entities) == 1:
            args = [native, entities[0][0], value]
        else:
            first, second = entities
            args = [native, first[0], first[1], second[0], second[1], value]
    elif kind in {"distance_x", "distance_y"}:
        if len(entities) == 1:
            args = [native, entities[0][0], entities[0][1], value]
        else:
            first, second = entities
            args = [native, first[0], first[1], second[0], second[1], value]
    elif kind == "angle":
        if len(entities) == 1:
            args = [native, entities[0][0], value]
        elif all(item[1] == 0 for item in entities):
            args = [native, entities[0][0], entities[1][0], value]
        else:
            first, second = entities
            args = [native, first[0], first[1], second[0], second[1], value]
    elif kind == "angle_via_point":
        first, second, point = entities
        args = [native, first[0], second[0], point[0], point[1], value]
    elif kind in {"radius", "diameter", "weight"}:
        args = [native, entities[0][0], value]
    elif kind == "symmetric":
        first, second, third = entities
        args = [native, first[0], first[1], second[0], second[1], third[0]]
        if third[1] != 0:
            args.append(third[1])
    elif kind == "snells_law":
        first, second, third = entities
        args = [
            native,
            first[0],
            first[1],
            second[0],
            second[1],
            third[0],
            value,
        ]
    elif kind == "internal_alignment":
        alignment = str(properties.get("alignment") or "")
        suffix = _INTERNAL_ALIGNMENT.get(alignment)
        if suffix is None:
            raise SketcherCandidateError(
                f"Unsupported internal alignment {alignment!r}.",
                details={
                    "stage": "constraint_type",
                    "available_alignments": sorted(_INTERNAL_ALIGNMENT),
                    "correction": (
                        "Choose one exact alignment from available_alignments and use the "
                        "entity order documented by describe_api.constraint_forms."
                    ),
                },
            )
        native = f"InternalAlignment:{suffix}"
        first, second = entities
        if alignment in {
            "ellipse_major_diameter",
            "ellipse_minor_diameter",
            "hyperbola_major_diameter",
            "hyperbola_minor_diameter",
            "parabola_focal_axis",
        }:
            args = [native, first[0], second[0]]
        elif alignment in {
            "ellipse_focus1",
            "ellipse_focus2",
            "hyperbola_focus",
            "parabola_focus",
        }:
            args = [native, first[0], first[1], second[0]]
        else:
            internal_index = int(properties.get("internal_index") or 0)
            try:
                spline = sketch.Geometry[second[0]]
                native_values = list(
                    spline.getPoles()
                    if alignment == "bspline_control_point"
                    else spline.getKnots()
                )
            except Exception as exc:
                raise SketcherCandidateError(
                    f"Could not inspect the target B-spline for {alignment}: {exc}",
                    details={
                        "stage": "constraint_internal_alignment",
                        "alignment": alignment,
                        "geometry_index": second[0],
                        "native_error": str(exc),
                        "correction": (
                            "Use an exact api.bspline definition whose native poles or knots "
                            "can be inspected, then apply this internal alignment."
                        ),
                    },
                ) from exc
            native_value_name = (
                "control poles" if alignment == "bspline_control_point" else "knots"
            )
            if not 0 <= internal_index < len(native_values):
                raise SketcherCandidateError(
                    f"api.constraint.internal_index {internal_index} is outside the "
                    f"native B-spline's {len(native_values)} {native_value_name}.",
                    details={
                        "stage": "constraint_internal_alignment",
                        "alignment": alignment,
                        "internal_index": internal_index,
                        "native_count": len(native_values),
                        "valid_range": (
                            [0, len(native_values) - 1] if native_values else []
                        ),
                        "correction": (
                            "Choose an internal_index from valid_range; exact B-splines "
                            "make pole and knot counts predictable in source."
                        ),
                    },
                )
            args = [
                native,
                first[0],
                first[1],
                second[0],
                internal_index,
            ]
    elif kind in {"group", "text"}:
        flat_entities = [value for entity in entities for value in entity]
        if kind == "group":
            return Sketcher.Constraint("Group", flat_entities)
        return Sketcher.Constraint(
            "Text",
            flat_entities,
            str(properties.get("text") or ""),
            str(properties.get("font") or ""),
            bool(properties.get("text_height", True)),
        )
    else:
        raise SketcherCandidateError(
            f"Unsupported Sketcher constraint kind {kind!r}.",
            details={
                "stage": "constraint_type",
                "kind": kind,
                "supported_kinds": sorted(_NATIVE_CONSTRAINT) + ["internal_alignment"],
                "correction": (
                    "Choose one exact kind from supported_kinds; do not invent a per-kind "
                    "helper or native constraint spelling."
                ),
            },
        )
    if kind in _DIMENSIONAL:
        args.extend((active, driving))
    else:
        args.append(active)
    try:
        return Sketcher.Constraint(*args)
    except Exception as exc:
        raise SketcherCandidateError(
            f"FreeCAD rejected api.constraint({kind!r}): {exc}",
            details={
                "stage": "constraint_construction",
                "kind": kind,
                "resolved_entities": entities,
                "native_arguments": args[1:],
                "correction": (
                    "Check the constraint kind's entity count, point selectors, and dimensional value."
                ),
            },
        ) from exc


def _constraint_readback(sketch: Any, index: int, definition: Mapping[str, Any]) -> dict[str, Any]:
    constraint = sketch.Constraints[index]
    value = float(getattr(constraint, "Value", 0.0))
    kind = str(_argument(definition, 0, context="constraint readback") or "")
    expected_expression = str(_properties(definition).get("expression") or "")
    expression_path = f"Constraints[{index}]"
    native_expression = sketch_expression_map(sketch).get(expression_path)
    return {
        "index": index,
        "graph_id": str(_properties(definition).get("graph_id") or ""),
        "name": str(getattr(constraint, "Name", "") or ""),
        "kind": kind,
        "native_type": str(getattr(constraint, "Type", "") or ""),
        "value": math.degrees(value)
        if kind in {"angle", "angle_via_point"}
        else value,
        "value_unit": (
            "degrees" if kind in {"angle", "angle_via_point"} else "native"
        ),
        "driving": bool(getattr(constraint, "Driving", True)),
        "active": bool(getattr(constraint, "IsActive", True)),
        "virtual": bool(getattr(constraint, "InVirtualSpace", False)),
        "expression": expected_expression,
        "expression_bound": native_expression is not None,
    }


def sketch_expression_map(sketch: Any) -> dict[str, str]:
    """Return FreeCAD's expression bindings without relying on getExpression().

    ``App::FeaturePython`` and ``Sketcher::SketchObject`` expose their bindings
    through ``ExpressionEngine`` on every FreeCAD release supported by VibeCAD.
    Some releases do not expose the convenience ``getExpression`` Python
    method, so accepting a candidate through that method would make live
    publication version-dependent.
    """

    try:
        raw_bindings = list(getattr(sketch, "ExpressionEngine", []) or [])
    except Exception as exc:
        raise SketcherCandidateError(
            f"Could not inspect Sketcher expression bindings: {exc}",
            details={"stage": "constraint_expression_readback"},
        ) from exc
    bindings: dict[str, str] = {}
    for index, raw in enumerate(raw_bindings):
        if not isinstance(raw, (list, tuple)) or len(raw) != 2:
            raise SketcherCandidateError(
                "FreeCAD returned a malformed Sketcher expression binding.",
                details={
                    "stage": "constraint_expression_readback",
                    "binding_index": index,
                },
            )
        path = str(raw[0] or "")
        expression = str(raw[1] or "")
        if not path or path in bindings:
            raise SketcherCandidateError(
                "FreeCAD returned a missing or duplicate Sketcher expression path.",
                details={
                    "stage": "constraint_expression_readback",
                    "binding_index": index,
                    "path": path,
                },
            )
        bindings[path] = expression
    return bindings


def sketch_external_reference_records(sketch: Any) -> list[tuple[Any, str]]:
    """Flatten FreeCAD's object-grouped external-geometry Python readback.

    ``App::PropertyLinkSubList`` exposes one Python tuple per referenced object,
    with every referenced subelement in that tuple. Sketcher's native reference
    indexes and negative geometry ids are per subelement, so callers must not
    use the tuple count as the external-reference count.
    """

    records: list[tuple[Any, str]] = []
    for group_index, raw in enumerate(
        list(getattr(sketch, "ExternalGeometry", []) or [])
    ):
        if not isinstance(raw, (list, tuple)) or len(raw) < 2 or raw[0] is None:
            raise SketcherCandidateError(
                "FreeCAD returned malformed external geometry metadata.",
                details={
                    "stage": "external_geometry_readback",
                    "group_index": group_index,
                },
            )
        raw_subelements = raw[1]
        subelements = (
            [str(raw_subelements)]
            if isinstance(raw_subelements, str)
            else [str(value) for value in list(raw_subelements or [])]
        )
        if not subelements or any(not value for value in subelements):
            raise SketcherCandidateError(
                "FreeCAD returned an empty external geometry subelement list.",
                details={
                    "stage": "external_geometry_readback",
                    "group_index": group_index,
                },
            )
        records.extend((raw[0], subelement) for subelement in subelements)
    return records


def _integer_list(value: Any) -> list[int]:
    return sorted({int(item) for item in list(value or [])})


def _diagnostic_geometry_reference(
    metadata: Mapping[str, Any],
    *,
    point: str,
) -> dict[str, Any]:
    """Return one stable, source-oriented geometry reference for the model."""

    return {
        "graph_id": str(metadata.get("graph_id") or ""),
        "name": str(metadata.get("name") or ""),
        "source_index": int(metadata.get("index", -1)),
        "operation": str(metadata.get("operation") or ""),
        "construction": bool(metadata.get("construction")),
        "point": point,
    }


def _constraint_entity_key(raw: Any) -> tuple[str, str] | None:
    if not isinstance(raw, Mapping) or "geometry" not in raw:
        return None
    geometry = raw.get("geometry")
    if not isinstance(geometry, Mapping):
        return None
    graph_id = str(_properties(geometry).get("graph_id") or "")
    point = str(raw.get("point") or "none")
    return (graph_id, point) if graph_id else None


def _active_constraint_signatures(
    constraint_payloads: list[dict[str, Any]],
) -> tuple[set[tuple[str, tuple[tuple[str, str], ...]]], set[str]]:
    """Collect exact active graph signatures used to suppress obvious duplicates."""

    signatures: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    blocked: set[str] = set()
    for definition in constraint_payloads:
        properties = _properties(definition)
        if not bool(properties.get("active", True)) or bool(properties.get("virtual")):
            continue
        kind = str(_argument(definition, 0, context="constraint diagnostics") or "")
        raw_entities = _argument(definition, 1, context="constraint diagnostics")
        if not isinstance(raw_entities, list):
            continue
        entities = [
            key
            for key in (_constraint_entity_key(raw) for raw in raw_entities)
            if key is not None
        ]
        if len(entities) != len(raw_entities):
            continue
        if kind in {"coincident", "equal"}:
            entities.sort()
        signature = (kind, tuple(entities))
        signatures.add(signature)
        if kind == "block" and len(entities) == 1:
            blocked.add(entities[0][0])
    return signatures, blocked


def _underconstraint_guidance(
    sketch: Any,
    *,
    degrees_of_freedom: int,
    geometry_readback: list[dict[str, Any]],
    geometry_indexes: Mapping[str, int],
    constraint_payloads: list[dict[str, Any]],
) -> dict[str, Any]:
    """Translate bounded native repair heuristics into stable model actions.

    FreeCAD's missing-constraint detectors inspect geometry; they do not know
    design intent.  Consequently these records are deliberately suggestions,
    never an automatically applied constraint recipe.
    """

    guidance: dict[str, Any] = {
        "status": "not_needed" if degrees_of_freedom == 0 else "available",
        "canonical_operation": "api.constraint",
        "automatic_application": False,
        "workflow": ["connectivity", "orientation", "equality", "dimensions"],
        "position_tolerance_mm": _SUGGESTION_POSITION_TOLERANCE_MM,
        "angle_tolerance_degrees": _SUGGESTION_ANGLE_TOLERANCE_DEGREES,
        "equality_tolerance_mm": _SUGGESTION_EQUALITY_TOLERANCE_MM,
        "equality_geometry_limit": _MAX_EQUALITY_DIAGNOSTIC_GEOMETRY,
        "detected_counts": {
            "connectivity": 0,
            "orientation": 0,
            "equality": 0,
        },
        "filtered_existing_count": 0,
        "skipped_detectors": [],
        "suggestions": [],
        "suggestions_truncated": False,
        "native_error": "",
    }
    if degrees_of_freedom == 0:
        return guidance

    metadata_by_native_index = {
        int(geometry_indexes[str(item["graph_id"])]): item
        for item in geometry_readback
        if str(item.get("graph_id") or "") in geometry_indexes
        and int(geometry_indexes[str(item["graph_id"])]) >= 0
    }
    signatures, blocked = _active_constraint_signatures(constraint_payloads)
    candidates: list[dict[str, Any]] = []
    filtered_existing = 0

    def add_candidate(
        *,
        kind: str,
        category: str,
        reason: str,
        raw_entities: list[tuple[int, str]],
    ) -> None:
        nonlocal filtered_existing
        metadata = [metadata_by_native_index.get(index) for index, _point in raw_entities]
        if any(item is None for item in metadata):
            return
        entities = [
            _diagnostic_geometry_reference(item, point=point)
            for item, (_index, point) in zip(metadata, raw_entities)
            if item is not None
        ]
        signature_entities = [
            (str(entity["graph_id"]), str(entity["point"])) for entity in entities
        ]
        if kind in {"coincident", "equal"}:
            signature_entities.sort()
        signature = (kind, tuple(signature_entities))
        if signature in signatures or (
            kind in {"horizontal", "vertical"}
            and str(entities[0]["graph_id"]) in blocked
        ):
            filtered_existing += 1
            return
        candidates.append(
            {
                "kind": kind,
                "category": category,
                "reason": reason,
                "intent_required": True,
                "entities": entities,
            }
        )

    before = (
        int(getattr(sketch, "GeometryCount", 0)),
        int(getattr(sketch, "ConstraintCount", 0)),
    )
    try:
        sketch.detectMissingPointOnPointConstraints(
            _SUGGESTION_POSITION_TOLERANCE_MM,
            True,
        )
        point_records = list(
            getattr(sketch, "MissingPointOnPointConstraints", []) or []
        )
        guidance["detected_counts"]["connectivity"] = len(point_records)
        for raw in point_records:
            if not isinstance(raw, (list, tuple)) or len(raw) != 5:
                raise ValueError("malformed missing point-on-point diagnostic")
            first, first_pos, second, second_pos, native_type = map(int, raw)
            if native_type != 1 or first_pos not in {1, 2} or second_pos not in {1, 2}:
                raise ValueError("unexpected missing point-on-point diagnostic")
            add_candidate(
                kind="coincident",
                category="connectivity",
                reason="native_missing_endpoint_coincidence",
                raw_entities=[
                    (first, "start" if first_pos == 1 else "end"),
                    (second, "start" if second_pos == 1 else "end"),
                ],
            )

        sketch.detectMissingVerticalHorizontalConstraints(
            math.radians(_SUGGESTION_ANGLE_TOLERANCE_DEGREES)
        )
        orientation_records = list(
            getattr(sketch, "MissingVerticalHorizontalConstraints", []) or []
        )
        guidance["detected_counts"]["orientation"] = len(orientation_records)
        for raw in orientation_records:
            if not isinstance(raw, (list, tuple)) or len(raw) != 5:
                raise ValueError("malformed horizontal/vertical diagnostic")
            first, first_pos, _second, _second_pos, native_type = map(int, raw)
            if first_pos != 0 or native_type not in {2, 3}:
                raise ValueError("unexpected horizontal/vertical diagnostic")
            kind = "horizontal" if native_type == 2 else "vertical"
            add_candidate(
                kind=kind,
                category="orientation",
                reason=f"native_near_{kind}",
                raw_entities=[(first, "none")],
            )

        equality_eligible = sum(
            str(item.get("operation") or "") in {"line", "arc", "circle"}
            for item in geometry_readback
            if not str(item.get("operation") or "") == "external_geometry"
        )
        if equality_eligible <= _MAX_EQUALITY_DIAGNOSTIC_GEOMETRY:
            sketch.detectMissingEqualityConstraints(
                _SUGGESTION_EQUALITY_TOLERANCE_MM
            )
            line_equalities = list(
                getattr(sketch, "MissingLineEqualityConstraints", []) or []
            )
            radius_equalities = list(
                getattr(sketch, "MissingRadiusConstraints", []) or []
            )
            guidance["detected_counts"]["equality"] = len(line_equalities) + len(
                radius_equalities
            )
            for reason, records in (
                ("native_equal_line_length", line_equalities),
                ("native_equal_radius", radius_equalities),
            ):
                for raw in records:
                    if not isinstance(raw, (list, tuple)) or len(raw) != 4:
                        raise ValueError("malformed equality diagnostic")
                    first, first_pos, second, second_pos = map(int, raw)
                    if first_pos != 0 or second_pos != 0:
                        raise ValueError("unexpected equality diagnostic")
                    add_candidate(
                        kind="equal",
                        category="equality",
                        reason=reason,
                        raw_entities=[(first, "none"), (second, "none")],
                    )
        else:
            guidance["skipped_detectors"].append("equality_size_guard")

        after = (
            int(getattr(sketch, "GeometryCount", 0)),
            int(getattr(sketch, "ConstraintCount", 0)),
        )
        if after != before:
            raise ValueError(
                "native missing-constraint diagnostics mutated the sketch graph"
            )
    except Exception as exc:
        guidance.update(
            {
                "status": "native_diagnostics_unavailable",
                "filtered_existing_count": 0,
                "suggestions": [],
                "suggestions_truncated": False,
                "native_error": str(exc)[:512],
            }
        )
        return guidance

    category_order = {"connectivity": 0, "orientation": 1, "equality": 2}
    candidates.sort(
        key=lambda item: (
            category_order[str(item["category"])],
            str(item["kind"]),
            tuple(
                (str(entity["graph_id"]), str(entity["point"]))
                for entity in item["entities"]
            ),
        )
    )
    deduplicated: list[dict[str, Any]] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    for candidate in candidates:
        entity_key = tuple(
            (str(entity["graph_id"]), str(entity["point"]))
            for entity in candidate["entities"]
        )
        if candidate["kind"] in {"coincident", "equal"}:
            entity_key = tuple(sorted(entity_key))
        key = (str(candidate["kind"]), entity_key)
        if key in seen:
            continue
        seen.add(key)
        deduplicated.append(candidate)
    guidance["filtered_existing_count"] = filtered_existing
    guidance["suggestions_truncated"] = (
        len(deduplicated) > _MAX_UNDERCONSTRAINT_SUGGESTIONS
    )
    guidance["suggestions"] = deduplicated[:_MAX_UNDERCONSTRAINT_SUGGESTIONS]
    return guidance


def _profile_open_vertex_diagnostics(
    sketch: Any,
    *,
    open_wire_count: int,
    geometry_readback: list[dict[str, Any]],
    geometry_indexes: Mapping[str, int],
) -> dict[str, Any]:
    """Map native open profile vertices back to stable graph endpoints."""

    diagnostics: dict[str, Any] = {
        "status": "not_needed" if open_wire_count == 0 else "available",
        "match_tolerance_mm": _PROFILE_ENDPOINT_MATCH_TOLERANCE_MM,
        "vertices": [],
        "truncated": False,
        "native_error": "",
    }
    if open_wire_count == 0:
        return diagnostics
    try:
        endpoints: list[tuple[tuple[float, float], dict[str, Any]]] = []
        for metadata in geometry_readback:
            if bool(metadata.get("construction")) or str(
                metadata.get("operation") or ""
            ) not in {
                "line",
                "arc",
                "elliptic_arc",
                "hyperbolic_arc",
                "parabolic_arc",
                "bspline",
            }:
                continue
            graph_id = str(metadata.get("graph_id") or "")
            native_index = int(geometry_indexes[graph_id])
            if native_index < 0:
                continue
            for position, point_name in ((1, "start"), (2, "end")):
                point = sketch.getPoint(native_index, position)
                coordinates = (float(point.x), float(point.y))
                if not all(math.isfinite(value) for value in coordinates):
                    raise ValueError("native endpoint coordinates are non-finite")
                endpoints.append(
                    (
                        coordinates,
                        _diagnostic_geometry_reference(
                            metadata,
                            point=point_name,
                        ),
                    )
                )

        open_vertex_getter = getattr(sketch, "getOpenVertices", None)
        if callable(open_vertex_getter):
            raw_vertices = list(open_vertex_getter() or [])
        else:
            # Some supported Sketcher Python bindings do not expose the native
            # convenience method even though the C++ implementation exists.
            # OrderedVertexes is native OCC wire topology and gives the exact
            # two endpoints of each open wire without a quadratic edge scan.
            raw_vertices = []
            shape = getattr(sketch, "Shape", None)
            for wire in list(getattr(shape, "Wires", []) or []):
                if bool(wire.isClosed()):
                    continue
                ordered = list(getattr(wire, "OrderedVertexes", []) or [])
                if not ordered:
                    ordered = list(getattr(wire, "Vertexes", []) or [])
                if not ordered:
                    continue
                selected = [ordered[0]] if len(ordered) == 1 else [ordered[0], ordered[-1]]
                raw_vertices.extend(
                    (float(vertex.Point.x), float(vertex.Point.y), float(vertex.Point.z))
                    for vertex in selected
                )
        diagnostics["truncated"] = len(raw_vertices) > _MAX_PROFILE_OPEN_VERTICES
        vertices = []
        for raw in raw_vertices[:_MAX_PROFILE_OPEN_VERTICES]:
            if not isinstance(raw, (list, tuple)) or len(raw) != 3:
                raise ValueError("native open-vertex diagnostic is malformed")
            position = (float(raw[0]), float(raw[1]))
            if not all(math.isfinite(value) for value in position):
                raise ValueError("native open-vertex coordinates are non-finite")
            matches = [
                reference
                for endpoint, reference in endpoints
                if math.dist(position, endpoint)
                <= _PROFILE_ENDPOINT_MATCH_TOLERANCE_MM
            ]
            matches.sort(
                key=lambda item: (
                    int(item["source_index"]),
                    str(item["point"]),
                )
            )
            vertices.append(
                {
                    "position_mm": [position[0], position[1]],
                    "candidate_endpoints": matches[:_MAX_PROFILE_ENDPOINT_MATCHES],
                    "matches_truncated": len(matches)
                    > _MAX_PROFILE_ENDPOINT_MATCHES,
                }
            )
        diagnostics["vertices"] = vertices
    except Exception as exc:
        diagnostics.update(
            {
                "status": "native_diagnostics_unavailable",
                "vertices": [],
                "truncated": False,
                "native_error": str(exc)[:512],
            }
        )
    return diagnostics


def _constraint_issue_records(
    constraint_readback: list[dict[str, Any]],
    invalid_sets: Mapping[str, list[int]],
) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for category, indexes in invalid_sets.items():
        result[category] = [
            {
                "index": index,
                "graph_id": str(constraint_readback[index].get("graph_id") or ""),
                "name": str(constraint_readback[index].get("name") or ""),
                "kind": str(constraint_readback[index].get("kind") or ""),
            }
            for index in indexes
            if 0 <= index < len(constraint_readback)
        ]
    return result


def populate_sketch_without_solving(
    sketch: Any,
    payload: Mapping[str, Any],
    *,
    replace_existing: bool,
    external_resolver: Callable[
        [Mapping[str, Any]], tuple[Any, str, dict[str, Any]]
    ]
    | None = None,
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    dict[str, int],
    list[dict[str, Any]],
]:
    """Apply one validated graph without invoking Sketcher's solver or recompute.

    The live publisher intentionally uses this same primitive as the isolated
    evaluator.  Scalar ``addConstraint`` is forbidden here because FreeCAD
    solves immediately for that overload; the list overload is a no-solve
    batch operation.
    """

    if str(payload.get("operation") or "") != "sketch":
        raise SketcherCandidateError("A sketch definition must come from api.sketch.")
    geometry_values = _argument(payload, 0, context="api.sketch")
    constraint_values = _argument(payload, 1, context="api.sketch")
    if not isinstance(geometry_values, list) or not isinstance(constraint_values, list):
        raise SketcherCandidateError(
            "api.sketch geometry and constraints must be serialized arrays."
        )
    geometry_payloads = [
        _payload(item, context=f"geometry[{index}]")
        for index, item in enumerate(geometry_values)
    ]
    constraint_payloads = [
        _payload(item, context=f"constraints[{index}]")
        for index, item in enumerate(constraint_values)
    ]
    geometry_ids = [
        str(_properties(item).get("graph_id") or "") for item in geometry_payloads
    ]
    constraint_ids = [
        str(_properties(item).get("graph_id") or "") for item in constraint_payloads
    ]
    if (
        not all(geometry_ids)
        or len(set(geometry_ids)) != len(geometry_ids)
        or not all(constraint_ids)
        or len(set(constraint_ids)) != len(constraint_ids)
    ):
        raise SketcherCandidateError(
            "Sketch geometry and constraint graph ids must be present and unique.",
            details={
                "stage": "graph_identity",
                "geometry_ids": geometry_ids,
                "constraint_ids": constraint_ids,
            },
        )

    if replace_existing:
        for expression in list(getattr(sketch, "ExpressionEngine", []) or []):
            path = str(expression[0]) if isinstance(expression, (list, tuple)) else ""
            if path:
                sketch.setExpression(path, None)
        constraint_count = int(getattr(sketch, "ConstraintCount", 0))
        if constraint_count:
            sketch.delConstraints(list(range(constraint_count)), False, True)
        external_references = sketch_external_reference_records(sketch)
        if external_references:
            sketch.delExternals(list(range(len(external_references))))
            if sketch_external_reference_records(sketch):
                raise SketcherCandidateError(
                    "FreeCAD did not remove every previous external geometry link.",
                    details={"stage": "external_geometry_cleanup"},
                )
        geometry_count = int(getattr(sketch, "GeometryCount", 0))
        if geometry_count:
            sketch.delGeometries(list(range(geometry_count)), True)

    geometry_indexes: dict[str, int] = {}
    external_validations: list[dict[str, Any]] = []
    external_point_contracts: dict[str, tuple[str, frozenset[str]]] = {}
    worker_external_targets: dict[tuple[str, str], Any] = {}
    next_local_index = 0
    for index, definition in enumerate(geometry_payloads):
        properties = _properties(definition)
        graph_id = geometry_ids[index]
        if str(definition.get("operation") or "") == "external_geometry":
            resolver = external_resolver or (
                lambda value: _resolve_worker_external_geometry(
                    sketch,
                    value,
                    target_cache=worker_external_targets,
                )
            )
            target, subelement, resolution = resolver(definition)
            before_external_count = len(list(getattr(sketch, "ExternalGeo", []) or []))
            before_references = sketch_external_reference_records(sketch)
            try:
                sketch.addExternal(
                    str(getattr(target, "Name", "") or ""),
                    subelement,
                    bool(properties.get("defining")),
                    bool(properties.get("intersection")),
                )
            except Exception as exc:
                raise SketcherCandidateError(
                    f"FreeCAD rejected external geometry {graph_id!r} "
                    f"({subelement}): {exc}",
                    details={
                        "stage": "external_geometry_projection",
                        "geometry_index": index,
                        "graph_id": graph_id,
                        "subelement": subelement,
                        "native_error": str(exc),
                        "correction": (
                            "Verify that the selected stable interface resolves to one projectable "
                            "EdgeN or VertexN and choose point selectors supported by its projected type."
                        ),
                    },
                ) from exc
            after_external_count = len(list(getattr(sketch, "ExternalGeo", []) or []))
            after_references = sketch_external_reference_records(sketch)
            if (
                len(after_references) != len(before_references) + 1
                or after_external_count != before_external_count + 1
            ):
                raise SketcherCandidateError(
                    "One api.external_geometry value must project to exactly one native "
                    "Sketcher geometry.",
                    details={
                        "stage": "external_geometry_projection",
                        "graph_id": graph_id,
                        "reference_count_before": len(before_references),
                        "reference_count_after": len(after_references),
                        "native_count_before": before_external_count,
                        "native_count_after": after_external_count,
                        "correction": (
                            "Select exactly one EdgeN or VertexN; do not select a face or "
                            "compound that expands to multiple projected curves."
                        ),
                    },
                )
            matching_references = [
                record
                for record in after_references
                if record[0] is target and record[1] == subelement
            ]
            if len(matching_references) != 1:
                raise SketcherCandidateError(
                    "FreeCAD external geometry readback changed the source link.",
                    details={
                        "stage": "external_geometry_readback",
                        "graph_id": graph_id,
                        "source_object": str(getattr(target, "Name", "") or ""),
                        "subelement": subelement,
                        "matching_reference_count": len(matching_references),
                    },
                )
            native_index = -before_external_count - 1
            native_external = list(getattr(sketch, "ExternalGeo", []) or [])
            try:
                projected_geometry = native_external[before_external_count]
            except IndexError as exc:
                raise SketcherCandidateError(
                    "FreeCAD did not expose the projected external geometry for validation.",
                    details={
                        "stage": "external_geometry_readback",
                        "graph_id": graph_id,
                        "native_geometry_id": native_index,
                    },
                ) from exc
            external_point_contracts[graph_id] = _projected_point_selectors(
                projected_geometry
            )
            external_validations.append(
                {
                    **dict(resolution),
                    "graph_id": graph_id,
                    "native_geometry_id": native_index,
                    "defining": bool(properties.get("defining")),
                    "intersection": bool(properties.get("intersection")),
                }
            )
        else:
            try:
                native_index = int(
                    sketch.addGeometry(
                        sketch_geometry_from_payload(definition),
                        bool(properties.get("construction")),
                    )
                )
            except Exception as exc:
                raise SketcherCandidateError(
                    f"FreeCAD rejected geometry {graph_id!r} "
                    f"({definition.get('operation')}): {exc}",
                    details={
                        "stage": "geometry_construction",
                        "geometry_index": index,
                        "graph_id": graph_id,
                        "operation": definition.get("operation"),
                        "native_error": str(exc),
                        "correction": (
                            "Edit only this named geometry call: use finite non-degenerate values "
                            "that satisfy the exact api operation contract, then regenerate."
                        ),
                    },
                ) from exc
            if native_index != next_local_index:
                raise SketcherCandidateError(
                    "FreeCAD returned a non-contiguous local Sketcher geometry index.",
                    details={
                        "stage": "geometry_construction",
                        "expected_index": next_local_index,
                        "native_index": native_index,
                    },
                )
            next_local_index += 1
        geometry_indexes[graph_id] = native_index

    _validate_external_constraint_points(
        constraint_payloads,
        external_point_contracts,
    )
    native_constraints = [
        sketch_constraint_from_payload(definition, geometry_indexes, sketch)
        for definition in constraint_payloads
    ]
    try:
        added = list(sketch.addConstraint(native_constraints)) if native_constraints else []
    except Exception as exc:
        raise SketcherCandidateError(
            f"FreeCAD rejected the Sketcher constraint batch: {exc}",
            details={
                "stage": "constraint_batch",
                "constraint_count": len(native_constraints),
                "native_error": str(exc),
                "correction": (
                    "Inspect the named constraint definitions and their exact entity forms. "
                    "Repair the smallest invalid or incompatible constraint, then regenerate; "
                    "do not split the canonical batch into imperative add calls."
                ),
            },
        ) from exc
    if added != list(range(len(native_constraints))):
        raise SketcherCandidateError(
            "FreeCAD returned non-contiguous Sketcher constraint indexes.",
            details={"stage": "constraint_batch", "native_indexes": added},
        )
    for index, definition in enumerate(constraint_payloads):
        properties = _properties(definition)
        name = str(properties.get("name") or "")
        if name:
            sketch.renameConstraint(index, name)
        if bool(properties.get("virtual")):
            sketch.setVirtualSpace(index, True)
        expression = str(properties.get("expression") or "")
        if expression:
            try:
                sketch.setExpression(f"Constraints[{index}]", expression)
            except Exception as exc:
                raise SketcherCandidateError(
                    f"Constraint {name or index!r} expression {expression!r} "
                    f"is invalid: {exc}",
                    details={
                        "stage": "constraint_expression",
                        "constraint_index": index,
                        "constraint_name": name,
                        "expression": expression,
                        "native_error": str(exc),
                        "correction": (
                            "Correct this named driving dimension's expression using valid "
                            "document aliases/properties, or remove expression and supply value; "
                            "reference dimensions cannot be expression-driven."
                        ),
                    },
                ) from exc
    return geometry_payloads, constraint_payloads, geometry_indexes, external_validations


def _validated_support_subelements(
    shape: Any,
    values: Any,
    *,
    subject: str = "Sketch support",
    stage: str = "support_selection",
) -> list[str]:
    if not isinstance(values, list) or not 1 <= len(values) <= 4:
        raise SketcherCandidateError(
            f"{subject} must resolve to one through four subelements.",
            details={
                "stage": stage,
                "resolved_subelements": values,
                "correction": (
                    "Copy a bounded stable selection from the current domain context; external "
                    "geometry requires exactly one EdgeN/VertexN and support accepts one to four."
                ),
            },
        )
    result: list[str] = []
    for index, raw in enumerate(values):
        name = str(raw or "")
        prefix = next(
            (candidate for candidate in ("Face", "Edge", "Vertex") if name.startswith(candidate)),
            "",
        )
        suffix = name[len(prefix) :] if prefix else ""
        if not prefix or not suffix.isdigit() or int(suffix) < 1:
            raise SketcherCandidateError(
                f"{subject} subelement {name!r} must be FaceN, EdgeN, or VertexN.",
                details={
                    "stage": stage,
                    "selection_index": index,
                    "correction": (
                        "Copy an exact FaceN, EdgeN, or VertexN from current authenticated shape "
                        "facts; never invent a label or zero-based selector."
                    ),
                },
            )
        collection_name = {"Face": "Faces", "Edge": "Edges", "Vertex": "Vertexes"}[
            prefix
        ]
        collection = list(getattr(shape, collection_name, []) or [])
        if int(suffix) > len(collection):
            raise SketcherCandidateError(
                f"{subject} subelement {name!r} is outside 1..{len(collection)}.",
                details={
                    "stage": stage,
                    "selection_index": index,
                    "requested": name,
                    "available_count": len(collection),
                    "correction": (
                        "Choose an exact selector within the reported 1-based available_count "
                        "from current shape facts."
                    ),
                },
            )
        if name in result:
            raise SketcherCandidateError(
                f"{subject} subelement {name!r} is duplicated.",
                details={
                    "stage": stage,
                    "requested": name,
                    "correction": "Remove the duplicate selector and retain one occurrence.",
                },
            )
        result.append(name)
    return result


def _resolve_worker_external_geometry(
    sketch: Any,
    definition: Mapping[str, Any],
    *,
    target_cache: dict[tuple[str, str], Any] | None = None,
) -> tuple[Any, str, dict[str, Any]]:
    """Resolve one staged Edge/Vertex to an isolated native reference object."""

    if str(definition.get("operation") or "") != "external_geometry":
        raise SketcherCandidateError(
            "The external geometry resolver received a non-external graph value."
        )
    reference = _argument(definition, 0, context="api.external_geometry")
    selection = _argument(definition, 1, context="api.external_geometry")
    if not isinstance(reference, Mapping) or set(reference) != {
        "document_uid",
        "object_name",
    }:
        raise SketcherCandidateError(
            "api.external_geometry.reference must contain document_uid and object_name."
        )
    if not isinstance(selection, Mapping):
        raise SketcherCandidateError(
            "api.external_geometry.selection must be an object."
        )
    key = (
        str(reference.get("document_uid") or ""),
        str(reference.get("object_name") or ""),
    )
    metadata = _SKETCHER_REFERENCES.get(key)
    if metadata is None:
        raise SketcherCandidateError(
            f"External geometry source {key[1]!r} was not staged from validated inputs.",
            details={
                "stage": "external_geometry_reference",
                "reference": dict(reference),
                "correction": (
                    "Persist this exact object reference in an x-vibecad-reference input."
                ),
            },
        )
    from vibescript_part_worker import detached_reference_shape

    shape = detached_reference_shape(reference)
    selection_type = str(selection.get("type") or "")
    interface_name = ""
    if selection_type == "subelements":
        if set(selection) != {"type", "subelements"}:
            raise SketcherCandidateError(
                "A subelements external-geometry selection has unexpected fields."
            )
        if bool(metadata.get("transient_topology")) or bool(
            metadata.get("requires_semantic_interfaces")
        ):
            interfaces = dict(metadata.get("published_interfaces") or {})
            raise SketcherCandidateError(
                f"External geometry source {key[1]!r} regenerates, so raw Edge/Vertex "
                "names are not stable.",
                details={
                    "stage": "external_geometry_semantics",
                    "reference": dict(reference),
                    "source_kind": metadata.get("source_kind"),
                    "available_interfaces": sorted(str(name) for name in interfaces),
                    "correction": "Select one named published semantic interface.",
                },
            )
        requested = list(selection.get("subelements") or [])
    elif selection_type == "published_interface":
        if set(selection) != {"type", "interface_name"}:
            raise SketcherCandidateError(
                "A published_interface external-geometry selection has unexpected fields."
            )
        interface_name = str(selection.get("interface_name") or "")
        interfaces = dict(metadata.get("published_interfaces") or {})
        interface = interfaces.get(interface_name)
        if not isinstance(interface, Mapping):
            raise SketcherCandidateError(
                f"External geometry interface {interface_name!r} does not exist on "
                f"{key[1]!r}.",
                details={
                    "stage": "external_geometry_semantics",
                    "reference": dict(reference),
                    "available_interfaces": sorted(str(name) for name in interfaces),
                    "correction": (
                        "Copy one exact name from available_interfaces; if none expresses the "
                        "intent, ask for a stable interface to be published on the source."
                    ),
                },
            )
        requested = list(interface.get("subelements") or [])
    else:
        raise SketcherCandidateError(
            f"Unsupported external geometry selection type {selection_type!r}."
        )
    subelements = _validated_support_subelements(
        shape,
        requested,
        subject="External geometry",
        stage="external_geometry_selection",
    )
    if len(subelements) != 1 or not str(subelements[0]).startswith(("Edge", "Vertex")):
        raise SketcherCandidateError(
            "api.external_geometry must resolve to exactly one EdgeN or VertexN.",
            details={
                "stage": "external_geometry_selection",
                "resolved_subelements": subelements,
                "correction": (
                    "Publish/select a semantic interface containing one edge or vertex."
                ),
            },
        )
    cache = target_cache if target_cache is not None else {}
    target = cache.get(key)
    if target is None:
        document = getattr(sketch, "Document", None)
        if document is None:
            raise SketcherCandidateError("The candidate sketch has no isolated document.")
        target = document.addObject(
            "Part::Feature",
            f"CandidateSketchExternal{len(cache) + 1}",
        )
        if target is None:
            raise SketcherCandidateError(
                "FreeCAD did not create the isolated external-geometry source object."
            )
        target.Label = str(metadata.get("label") or key[1])
        target.Shape = shape
        cache[key] = target
    return (
        target,
        str(subelements[0]),
        {
            "reference": dict(reference),
            "requested_selection": dict(selection),
            "resolved_subelement": str(subelements[0]),
            "source_kind": str(metadata.get("source_kind") or "native"),
            "source_revision": str(metadata.get("source_revision") or ""),
            "interface_name": interface_name,
        },
    )


def _configure_sketch_support(
    document: Any,
    sketch: Any,
    properties: Mapping[str, Any],
) -> dict[str, Any] | None:
    import FreeCAD as App

    support = properties.get("support")
    resolved: dict[str, Any] | None = None
    if support is not None:
        if not isinstance(support, Mapping) or set(support) != {"reference", "selection"}:
            raise SketcherCandidateError(
                "api.sketch.support must contain exactly reference and selection."
            )
        reference = support.get("reference")
        selection = support.get("selection")
        if not isinstance(reference, Mapping) or set(reference) != {
            "document_uid",
            "object_name",
        }:
            raise SketcherCandidateError(
                "api.sketch.support.reference must contain document_uid and object_name."
            )
        if not isinstance(selection, Mapping):
            raise SketcherCandidateError("api.sketch.support.selection must be an object.")
        key = (
            str(reference.get("document_uid") or ""),
            str(reference.get("object_name") or ""),
        )
        metadata = _SKETCHER_REFERENCES.get(key)
        if metadata is None:
            raise SketcherCandidateError(
                f"Sketch support {key[1]!r} was not staged from validated program inputs.",
                details={
                    "stage": "support_reference",
                    "reference": dict(reference),
                    "correction": (
                        "Persist the exact reference in inputs with an "
                        "x-vibecad-reference input schema."
                    ),
                },
            )
        from vibescript_part_worker import detached_reference_shape

        shape = detached_reference_shape(reference)
        selection_type = str(selection.get("type") or "")
        interface_name = ""
        if selection_type == "subelements":
            if set(selection) != {"type", "subelements"}:
                raise SketcherCandidateError(
                    "A subelements support selection has unexpected fields."
                )
            if bool(metadata.get("transient_topology")) or bool(
                metadata.get("requires_semantic_interfaces")
            ):
                available = sorted(
                    str(name)
                    for name in dict(metadata.get("published_interfaces") or {})
                )
                raise SketcherCandidateError(
                    f"Sketch support {key[1]!r} regenerates, so raw Face/Edge/Vertex "
                    "names are not a stable contract.",
                    details={
                        "stage": "support_semantics",
                        "reference": dict(reference),
                        "source_kind": metadata.get("source_kind"),
                        "available_interfaces": available,
                        "correction": (
                            "Select a named published interface, or use a stable native "
                            "support object whose topology does not regenerate."
                        ),
                    },
                )
            subelements = _validated_support_subelements(
                shape,
                list(selection.get("subelements") or []),
            )
        elif selection_type == "published_interface":
            if set(selection) != {"type", "interface_name"}:
                raise SketcherCandidateError(
                    "A published_interface support selection has unexpected fields."
                )
            interface_name = str(selection.get("interface_name") or "")
            interfaces = dict(metadata.get("published_interfaces") or {})
            interface = interfaces.get(interface_name)
            if not isinstance(interface, Mapping):
                raise SketcherCandidateError(
                    f"Sketch support interface {interface_name!r} does not exist on "
                    f"{key[1]!r}.",
                    details={
                        "stage": "support_semantics",
                        "reference": dict(reference),
                        "available_interfaces": sorted(str(name) for name in interfaces),
                        "correction": (
                            "Copy one exact name from available_interfaces; if none is an "
                            "appropriate support, use a stable native support or ask for a new interface."
                        ),
                    },
                )
            subelements = _validated_support_subelements(
                shape,
                [str(value) for value in list(interface.get("subelements") or [])],
            )
        else:
            raise SketcherCandidateError(
                f"Unsupported Sketch support selection type {selection_type!r}."
            )
        support_object = document.addObject("Part::Feature", "CandidateSketchSupport")
        if support_object is None:
            raise SketcherCandidateError("FreeCAD did not create the isolated support object.")
        support_object.Label = str(metadata.get("label") or key[1])
        support_object.Shape = shape
        for property_name, property_value in (
            ("VibeCADWorkerReferenceDocumentUid", key[0]),
            ("VibeCADWorkerReferenceObjectName", key[1]),
            (
                "VibeCADWorkerReferenceSelection",
                json.dumps(selection, ensure_ascii=True, separators=(",", ":")),
            ),
        ):
            support_object.addProperty(
                "App::PropertyString",
                property_name,
                "VibeCAD Isolated Reference",
            )
            setattr(support_object, property_name, property_value)
        try:
            sketch.AttachmentSupport = (support_object, subelements)
        except Exception as exc:
            raise SketcherCandidateError(
                f"FreeCAD rejected Sketch support {key[1]!r} {subelements}: {exc}",
                details={
                    "stage": "support_attachment",
                    "reference": dict(reference),
                    "resolved_subelements": subelements,
                    "native_error": str(exc),
                    "correction": (
                        "Choose a map_mode compatible with the resolved support topology (for "
                        "example FlatFace for a planar face), or select a different stable support."
                    ),
                },
            ) from exc
        resolved = {
            "reference": dict(reference),
            "requested_selection": dict(selection),
            "resolved_subelements": subelements,
            "source_kind": str(metadata.get("source_kind") or "native"),
            "source_revision": str(metadata.get("source_revision") or ""),
            "interface_name": interface_name,
        }

    map_mode = str(properties.get("map_mode") or "")
    attachment = properties.get("attachment_offset")
    if not isinstance(attachment, Mapping):
        raise SketcherCandidateError("api.sketch.attachment_offset must be an object.")
    position = list(attachment.get("position") or [])
    rotation = list(attachment.get("rotation") or [])
    if len(position) != 3 or len(rotation) != 4:
        raise SketcherCandidateError(
            "api.sketch.attachment_offset must contain a position and quaternion."
        )
    try:
        sketch.MapMode = map_mode
        sketch.AttachmentOffset = App.Placement(
            App.Vector(*(_number(value, context="attachment position") for value in position)),
            App.Rotation(*(_number(value, context="attachment rotation") for value in rotation)),
        )
    except Exception as exc:
        raise SketcherCandidateError(
            f"FreeCAD rejected Sketch map_mode {map_mode!r} or attachment offset: {exc}",
            details={
                "stage": "support_attachment",
                "map_mode": map_mode,
                "has_support": support is not None,
                "native_error": str(exc),
                "correction": (
                    "Use a map_mode compatible with the selected support and a finite normalized "
                    "attachment quaternion; use Deactivated when no support is intended."
                ),
            },
        ) from exc
    if resolved is not None:
        resolved["map_mode"] = map_mode
        resolved["attachment_offset"] = {
            "position": [float(value) for value in position],
            "rotation": [float(value) for value in rotation],
        }
    return resolved


def validate_and_solve_sketch(
    document: Any,
    raw_result: Mapping[str, Any],
    outputs: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build, solve once, validate, and annotate one exact native sketch."""

    if not isinstance(raw_result, Mapping):
        raise SketcherCandidateError("A Sketcher program result must be an object.")
    sketch_values = [
        (str(name), value)
        for name, value in raw_result.items()
        if isinstance(value, DomainValue) and value.output_type == "sketch"
    ]
    if len(raw_result) != 1 or len(sketch_values) != 1 or len(outputs) != 1:
        raise SketcherCandidateError(
            "A Sketcher program must return exactly one sketch output.",
            details={
                "stage": "result_contract",
                "returned_outputs": sorted(str(name) for name in raw_result),
                "correction": (
                    "Assign result to exactly one entry whose key exactly matches the sole "
                    "expected_outputs name and whose value is the api.sketch result."
                ),
            },
        )
    output_name, value = sketch_values[0]
    payload = _payload(value, context=f"output {output_name!r}")
    if str(payload.get("operation") or "") != "sketch":
        raise SketcherCandidateError(
            f"Sketch output {output_name!r} must come from api.sketch."
        )
    sketch = document.addObject("Sketcher::SketchObject", "CandidateSketch")
    if sketch is None:
        raise SketcherCandidateError("FreeCAD did not create Sketcher::SketchObject.")
    properties = _properties(payload)
    sketch.Label = str(properties.get("label") or output_name)
    support_validation = _configure_sketch_support(
        document,
        sketch,
        properties,
    )
    geometry_payloads, constraint_payloads, geometry_indexes, external_geometry = (
        populate_sketch_without_solving(
            sketch,
            payload,
            replace_existing=False,
        )
    )
    geometry_readback = [
        {
            "index": index,
            "graph_id": str(_properties(definition).get("graph_id") or ""),
            "name": str(_properties(definition).get("name") or ""),
            "operation": str(definition.get("operation") or ""),
            "construction": (
                True
                if str(definition.get("operation") or "") == "external_geometry"
                else bool(
                    sketch.getConstruction(
                        geometry_indexes[
                            str(_properties(definition).get("graph_id") or "")
                        ]
                    )
                )
            ),
        }
        for index, definition in enumerate(geometry_payloads)
    ]

    document.recompute()
    try:
        solver_code = int(sketch.solve())
    except Exception as exc:
        raise SketcherCandidateError(
            f"The isolated native Sketcher solver raised an error: {exc}",
            details={
                "stage": "sketch_solver",
                "native_error": str(exc),
                "correction": (
                    "Inspect the exact named geometry and constraints in this candidate. Repair "
                    "non-finite/degenerate geometry or the smallest incompatible constraint, "
                    "then regenerate; do not retry the unchanged candidate."
                ),
            },
        ) from exc
    document.recompute()
    conflicts = _integer_list(getattr(sketch, "ConflictingConstraints", []))
    redundant = _integer_list(getattr(sketch, "RedundantConstraints", []))
    partial = _integer_list(getattr(sketch, "PartiallyRedundantConstraints", []))
    malformed = _integer_list(getattr(sketch, "MalformedConstraints", []))
    shape = getattr(sketch, "Shape", None)
    wires = list(getattr(shape, "Wires", []) or []) if shape is not None else []
    edges = list(getattr(shape, "Edges", []) or []) if shape is not None else []
    closed_wires = sum(bool(wire.isClosed()) for wire in wires)
    open_wires = len(wires) - closed_wires
    constraint_readback = [
        _constraint_readback(sketch, index, definition)
        for index, definition in enumerate(constraint_payloads)
    ]
    errors = []
    unavailable_errors = []
    for index in range(min(len(constraint_payloads), _MAX_DIAGNOSTIC_CONSTRAINTS)):
        try:
            error = float(sketch.calculateConstraintError(index))
        except Exception as exc:
            unavailable_errors.append(
                {
                    "index": index,
                    "reason": "native_error",
                    "message": str(exc)[:512],
                }
            )
            continue
        if math.isfinite(error):
            errors.append({"index": index, "error": error})
        else:
            unavailable_errors.append(
                {
                    "index": index,
                    "reason": "non_finite",
                    "message": "FreeCAD returned a non-finite constraint residual.",
                }
            )
    degrees_of_freedom = int(getattr(sketch, "DoF", 0))
    underconstraint_guidance = _underconstraint_guidance(
        sketch,
        degrees_of_freedom=degrees_of_freedom,
        geometry_readback=geometry_readback,
        geometry_indexes=geometry_indexes,
        constraint_payloads=constraint_payloads,
    )
    profile_open_vertices = _profile_open_vertex_diagnostics(
        sketch,
        open_wire_count=open_wires,
        geometry_readback=geometry_readback,
        geometry_indexes=geometry_indexes,
    )
    invalid_sets = {
        "conflicting": conflicts,
        "redundant": redundant,
        "partially_redundant": partial,
        "malformed": malformed,
    }
    constraint_issues = _constraint_issue_records(
        constraint_readback,
        invalid_sets,
    )
    validation = {
        "solver_code": solver_code,
        "geometry_count": len(geometry_payloads),
        "native_geometry_count": int(getattr(sketch, "GeometryCount", 0)),
        "external_geometry_count": len(external_geometry),
        "constraint_count": int(getattr(sketch, "ConstraintCount", len(constraint_payloads))),
        "degrees_of_freedom": degrees_of_freedom,
        "fully_constrained": bool(getattr(sketch, "FullyConstrained", False)),
        "conflicting_constraints": conflicts,
        "redundant_constraints": redundant,
        "partially_redundant_constraints": partial,
        "malformed_constraints": malformed,
        "edge_count": len(edges),
        "wire_count": len(wires),
        "closed_wire_count": closed_wires,
        "open_wire_count": open_wires,
        "profile_ready": bool(wires and closed_wires == len(wires)),
        "construction_geometry_count": sum(item["construction"] for item in geometry_readback),
        "geometry": geometry_readback,
        "external_geometry": external_geometry,
        "constraints": constraint_readback,
        "constraint_errors": errors,
        "constraint_error_unavailable": unavailable_errors,
        "constraint_errors_truncated": len(constraint_payloads)
        > _MAX_DIAGNOSTIC_CONSTRAINTS,
        "underconstraint_guidance": underconstraint_guidance,
        "profile_open_vertices": profile_open_vertices,
        "constraint_issues": constraint_issues,
        "requirements": {
            "fully_constrained": bool(properties.get("require_fully_constrained")),
            "closed_profile": bool(properties.get("require_closed_profile")),
        },
        "support": support_validation,
    }
    if solver_code != 0 or any(invalid_sets.values()):
        raise SketcherCandidateError(
            "The isolated native Sketcher solver rejected the constraint graph; "
            "inspect constraint_issues and repair only the named graph constraints.",
            details={
                "stage": "sketch_solver",
                "correction": (
                    "Use constraint_issues to locate each exact constraint by graph_id, "
                    "name, and kind. Remove or correct malformed/conflicting constraints; "
                    "remove duplicate intent, or change an intended measurement dimension "
                    "to driving=False. Do not renumber native indexes or rewrite unrelated "
                    "geometry. Regenerate and inspect the solver sets again."
                ),
                **validation,
            },
        )
    if bool(properties.get("require_fully_constrained")) and not validation[
        "fully_constrained"
    ]:
        raise SketcherCandidateError(
            f"api.sketch requires a fully constrained result, but {validation['degrees_of_freedom']} "
            "degrees of freedom remain. Add geometric/dimensional constraints or set "
            "require_fully_constrained=False intentionally.",
            details={
                "stage": "fully_constrained_requirement",
                "correction": (
                    "Keep require_fully_constrained=True when downstream intent needs it. "
                    "Read underconstraint_guidance, add only the smallest intent-compatible "
                    "connectivity suggestions, then regenerate before considering orientation "
                    "or equality suggestions. Remove remaining DoF with named dimensional or "
                    "anchoring constraints such as distance, distance_x, distance_y, angle, "
                    "radius, or coincidence to origin. Geometry coordinates are initial guesses, "
                    "not constraints; never apply every heuristic suggestion as a batch."
                ),
                **validation,
            },
        )
    if bool(properties.get("require_closed_profile")) and not validation["profile_ready"]:
        raise SketcherCandidateError(
            "api.sketch requires a closed profile, but the solved sketch contains "
            f"{closed_wires} closed and {open_wires} open wire(s). Make endpoints "
            "coincident or set require_closed_profile=False intentionally.",
            details={
                "stage": "profile_requirement",
                "correction": (
                    "Use profile_open_vertices.vertices[].candidate_endpoints to locate the "
                    "exact stable graph endpoints at each opening. Correct the source coordinates or add "
                    "an intent-compatible api.constraint('coincident', [point_a, point_b]); "
                    "then regenerate and require profile_ready=True. Do not add an arbitrary "
                    "closing segment or disable require_closed_profile unless an open path is "
                    "the explicit design intent."
                ),
                **validation,
            },
        )
    outputs[0]["sketch_validation"] = validation
    return validation
