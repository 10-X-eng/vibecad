# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact native geometric measurements for PartDesign decisions."""

from __future__ import annotations

import math
import json
from pathlib import Path
import shutil
import tempfile
from typing import Any

from . import domain_runtime, partdesign_find_subelements


REFERENCE_SCHEMA = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "description": "Exact internal name of the measured object.",
        },
        "subelement": {
            "type": "string",
            "description": "Exact subelement name such as Face3 or Edge4; empty measures the whole object.",
        },
    },
    "required": ["object_name", "subelement"],
    "additionalProperties": False,
}

TOOL_SPEC = {
    "name": "partdesign.measure",
    "description": (
        "Measure exact native object/subelement geometry, minimum distance, or direction angle. "
        "Datum points, axes, and planes are measured analytically in global coordinates; "
        "bounded solids and subelements use OpenCascade. Returns CAD facts only; it does "
        "not infer requirement satisfaction or choose geometry."
    ),
    "contextual": True,
    "safety": "READ",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "measurement": {
                "description": "What to measure; choose exactly one variant.",
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "geometry", "description": "Report geometry facts for subelements."},
                            "object_name": {
                                "type": "string",
                                "description": "Exact internal name of the measured object.",
                            },
                            "subelements": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "Exact subelement names; empty reports the whole object.",
                            },
                        },
                        "required": ["type", "object_name", "subelements"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "distance", "description": "Minimum distance between two references."},
                            "first": {**REFERENCE_SCHEMA, "description": "First reference."},
                            "second": {**REFERENCE_SCHEMA, "description": "Second reference."},
                        },
                        "required": ["type", "first", "second"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {"const": "angle", "description": "Angle between two directed references."},
                            "first": {**REFERENCE_SCHEMA, "description": "First reference."},
                            "second": {**REFERENCE_SCHEMA, "description": "Second reference."},
                        },
                        "required": ["type", "first", "second"],
                        "additionalProperties": False,
                    },
                ]
            }
        },
        "required": ["measurement"],
        "additionalProperties": False,
    },
}


def run(service: Any, measurement: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(measurement, dict):
        return _invalid("measurement must be an object.")
    kind = str(measurement.get("type") or "")
    if kind == "geometry":
        return _measure_geometry(
            service,
            measurement.get("object_name"),
            measurement.get("subelements"),
        )
    if kind == "distance":
        prepared = prepare_isolated_measurement(service, measurement)
        if prepared.get("mode") == "immediate":
            return dict(prepared["payload"])
        cleanup_isolated_measurement(prepared)
        return _invalid(
            "Bounded-shape distance requires the isolated geometry runner.",
            failure_code="ISOLATED_GEOMETRY_RUNNER_REQUIRED",
            failure_stage="precondition",
        )
    if kind == "angle":
        return _measure_angle(service, measurement.get("first"), measurement.get("second"))
    return _invalid("measurement.type must be geometry, distance, or angle.")


def _measure_geometry(service: Any, object_name: Any, subelements: Any) -> dict[str, Any]:
    resolved = _resolve_object(service, object_name)
    if not resolved.get("ok"):
        return resolved
    obj = resolved["object"]
    if not isinstance(subelements, list):
        return _invalid("measurement.subelements must be an array.")
    names = [str(value or "").strip() for value in subelements]
    if len(set(names)) != len(names):
        return _invalid("measurement.subelements cannot contain duplicates.")
    native_reference = _resolve_distance_reference(
        service,
        {"object_name": obj.Name, "subelement": ""},
    )
    if native_reference.get("ok") and native_reference.get("kind") != "shape":
        if names:
            return _invalid(
                f"{obj.Name} is an unbounded {native_reference['kind']} reference; "
                "measure it without subelements.",
                reference=native_reference["summary"],
            )
        return {
            "ok": True,
            "measurement_type": "geometry",
            "object": service._document_object_summary(obj),
            "reference_geometry": native_reference["summary"],
            "subelements": [],
        }
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return _invalid(f"Object {obj.Name} has no measurable shape.")
    details = []
    for name in names:
        detail = _subelement_measurement(shape, name)
        if not detail.get("ok"):
            return _invalid(
                f"Cannot measure {obj.Name}.{name}: {detail.get('error')}",
                available={
                    "faces": len(shape.Faces),
                    "edges": len(shape.Edges),
                    "vertices": len(shape.Vertexes),
                },
            )
        details.append(detail["measurement"])
    center = shape.CenterOfMass
    result = {
        "ok": True,
        "measurement_type": "geometry",
        "object": service._document_object_summary(obj),
        "shape": domain_runtime.shape_summary(obj),
        "surface_area": float(getattr(shape, "Area", 0.0) or 0.0),
        "center_of_mass": _vector(center),
        "subelements": details,
    }
    return result


def _measure_distance(service: Any, first: Any, second: Any) -> dict[str, Any]:
    first_state = _resolve_distance_reference(service, first)
    if not first_state.get("ok"):
        first_state["reference_side"] = "first"
        return first_state
    second_state = _resolve_distance_reference(service, second)
    if not second_state.get("ok"):
        second_state["reference_side"] = "second"
        return second_state
    try:
        measured = _distance_between(first_state, second_state)
    except Exception as exc:
        return _invalid(
            f"OpenCascade distance evaluation failed: {exc}",
            failure_code="BREP_EXTREMA_FAILED",
            failure_stage="native_call",
            first=_reference_diagnostics(first_state),
            second=_reference_diagnostics(second_state),
            calculation_path=_distance_calculation_path(first_state, second_state),
            native_stage="BRepExtrema_DistShapeShape",
            native_exception={"type": type(exc).__name__, "message": str(exc)},
            partial_extrema_found=False,
        )
    return _distance_result(first_state, second_state, measured)


def _distance_result(
    first_state: dict[str, Any],
    second_state: dict[str, Any],
    measured: dict[str, Any],
) -> dict[str, Any]:
    return {
        "ok": True,
        "measurement_type": "distance",
        "first": first_state["summary"],
        "second": second_state["summary"],
        "distance": float(measured["distance"]),
        "calculation": measured["calculation"],
        "closest_point_pairs": [
            {"first": _vector(pair[0]), "second": _vector(pair[1])}
            for pair in list(measured.get("point_pairs") or [])
        ],
        "native_support": _plain_support(measured.get("native_support")),
        **(
            {"intersection": measured["intersection"]}
            if measured.get("intersection") is not None
            else {}
        ),
    }


def prepare_isolated_measurement(
    service: Any,
    measurement: dict[str, Any],
) -> dict[str, Any]:
    """Resolve a measurement and snapshot bounded shapes for isolated execution."""
    if not isinstance(measurement, dict) or measurement.get("type") != "distance":
        return {"mode": "immediate", "payload": run(service, measurement)}
    first_state = _resolve_distance_reference(service, measurement.get("first"))
    if not first_state.get("ok"):
        first_state["reference_side"] = "first"
        return {"mode": "immediate", "payload": first_state}
    second_state = _resolve_distance_reference(service, measurement.get("second"))
    if not second_state.get("ok"):
        second_state["reference_side"] = "second"
        return {"mode": "immediate", "payload": second_state}
    if first_state["kind"] != "shape" and second_state["kind"] != "shape":
        try:
            measured = _distance_between(first_state, second_state)
        except Exception as exc:
            return {
                "mode": "immediate",
                "payload": _invalid(
                    f"Analytic distance evaluation failed: {exc}",
                    failure_code="ANALYTIC_DISTANCE_FAILED",
                    failure_stage="native_call",
                ),
            }
        return {
            "mode": "immediate",
            "payload": _distance_result(first_state, second_state, measured),
        }

    staging = Path(tempfile.mkdtemp(prefix="vibecad-geometry-"))
    try:
        first_shape, second_shape = _bounded_shape_pair(first_state, second_state)
        first_path = _write_brep(first_shape, staging / "first.brep")
        second_path = _write_brep(second_shape, staging / "second.brep")
        artifact_format = "brep"
        fidelity = (
            "faceted_brep"
            if any(
                str(state.get("fidelity") or "") == "faceted_brep"
                for state in (first_state, second_state)
            )
            else "exact_brep"
        )
        result_path = staging / "result.json"
        request_path = staging / "request.json"
        request = {
            "schema": "vibecad-geometry-job-v1",
            "operation": "minimum_distance",
            "first": {"format": artifact_format, "path": str(first_path)},
            "second": {"format": artifact_format, "path": str(second_path)},
            "fidelity": fidelity,
            "tolerance": 1e-7,
            "deadline_ms": 30000,
            "result_path": str(result_path),
        }
        request_path.write_text(
            json.dumps(request, ensure_ascii=True, sort_keys=True),
            encoding="utf-8",
        )
        return {
            "mode": "worker",
            "staging": str(staging),
            "request_path": str(request_path),
            "result_path": str(result_path),
            "first": first_state["summary"],
            "second": second_state["summary"],
            "calculation_path": _distance_calculation_path(first_state, second_state),
            "input_complexity": {
                "first": _shape_complexity(first_shape),
                "second": _shape_complexity(second_shape),
            },
            "artifact_format": artifact_format,
            "fidelity": fidelity,
        }
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise


def finish_isolated_measurement(
    prepared: dict[str, Any],
    execution: dict[str, Any],
) -> dict[str, Any]:
    if not execution.get("ok"):
        return _invalid(
            str(execution.get("error") or "Isolated geometry measurement failed."),
            failure_code=str(
                execution.get("failure_code") or "ISOLATED_GEOMETRY_FAILED"
            ),
            failure_stage=str(
                execution.get("failure_stage") or "external_process"
            ),
            calculation_path=prepared.get("calculation_path"),
            input_complexity=prepared.get("input_complexity"),
            geometry_worker=execution,
        )
    return {
        "ok": True,
        "measurement_type": "distance",
        "first": prepared["first"],
        "second": prepared["second"],
        "distance": float(execution["distance"]),
        "calculation": execution.get("calculation"),
        "closest_point_pairs": list(execution.get("closest_point_pairs") or []),
        "native_support": list(execution.get("native_support") or []),
        "fidelity": execution.get("fidelity"),
        "input_complexity": prepared.get("input_complexity"),
        "geometry_worker": {
            "schema": execution.get("schema"),
            "elapsed_ms": execution.get("elapsed_ms"),
            "elapsed_seconds": execution.get("elapsed_seconds"),
        },
    }


def cleanup_isolated_measurement(prepared: dict[str, Any]) -> None:
    staging = Path(str(prepared.get("staging") or ""))
    if staging.name.startswith("vibecad-geometry-"):
        shutil.rmtree(staging, ignore_errors=True)


def _bounded_shape_pair(
    first_state: dict[str, Any],
    second_state: dict[str, Any],
) -> tuple[Any, Any]:
    first_shape = first_state.get("shape")
    second_shape = second_state.get("shape")
    if first_shape is None:
        first_shape = _reference_as_bounded_shape(first_state, second_shape)
    if second_shape is None:
        second_shape = _reference_as_bounded_shape(second_state, first_shape)
    return first_shape, second_shape


def _reference_as_bounded_shape(reference: dict[str, Any], other_shape: Any) -> Any:
    import Part

    kind = reference["kind"]
    if kind == "point":
        return Part.Vertex(reference["point"])
    if kind == "axis":
        return _bounded_axis_shape(
            reference["origin"], reference["direction"], other_shape
        )
    if kind == "plane":
        return _bounded_plane_shape(
            reference["origin"], reference["normal"], other_shape
        )
    raise RuntimeError(f"Cannot bound measurement reference kind {kind!r}.")


def _bounded_axis_shape(origin: Any, direction: Any, shape: Any) -> Any:
    import Part

    corners = _bound_box_corners(shape)
    parameters = [float((point - origin).dot(direction)) for point in corners]
    margin = max(_bound_box_diagonal(shape), 1.0)
    return Part.makeLine(
        origin + direction * (min(parameters) - margin),
        origin + direction * (max(parameters) + margin),
    )


def _bounded_plane_shape(origin: Any, normal: Any, shape: Any) -> Any:
    import FreeCAD as App
    import Part

    seed = (
        App.Vector(1.0, 0.0, 0.0)
        if abs(float(normal.x)) < 0.9
        else App.Vector(0.0, 1.0, 0.0)
    )
    first_axis = _unit_vector(normal.cross(seed), "Datum plane basis")
    second_axis = _unit_vector(normal.cross(first_axis), "Datum plane basis")
    corners = _bound_box_corners(shape)
    first_values = [float((point - origin).dot(first_axis)) for point in corners]
    second_values = [float((point - origin).dot(second_axis)) for point in corners]
    margin = max(_bound_box_diagonal(shape), 1.0)
    first_min = min(first_values) - margin
    first_max = max(first_values) + margin
    second_min = min(second_values) - margin
    second_max = max(second_values) + margin
    base = origin + first_axis * first_min + second_axis * second_min
    return Part.makePlane(
        first_max - first_min,
        second_max - second_min,
        base,
        normal,
        first_axis,
    )


def _shape_complexity(shape: Any) -> dict[str, Any]:
    return {
        "shape_type": str(shape.ShapeType),
        "solids": len(shape.Solids),
        "faces": len(shape.Faces),
        "edges": len(shape.Edges),
        "vertices": len(shape.Vertexes),
    }


def _write_brep(shape: Any, destination: Path) -> Path:
    shape.exportBrep(str(destination))
    return destination


def _measure_angle(service: Any, first: Any, second: Any) -> dict[str, Any]:
    first_state = _resolve_direction_reference(service, first)
    if not first_state.get("ok"):
        return first_state
    second_state = _resolve_direction_reference(service, second)
    if not second_state.get("ok"):
        return second_state
    dot = max(-1.0, min(1.0, float(first_state["direction"].dot(second_state["direction"]))))
    degrees = math.degrees(math.acos(dot))
    return {
        "ok": True,
        "measurement_type": "angle",
        "first": first_state["summary"],
        "second": second_state["summary"],
        "angle_degrees": degrees,
        "acute_angle_degrees": min(degrees, 180.0 - degrees),
    }


def _resolve_object(service: Any, object_name: Any) -> dict[str, Any]:
    doc = service._active_document()
    clean = str(object_name or "").strip()
    obj = doc.getObject(clean) if doc is not None and clean else None
    if obj is None:
        return _invalid(
            f"Object not found by exact internal name: {clean}",
            candidates=[
                service._document_object_summary(candidate)
                for candidate in list(getattr(doc, "Objects", []) or [])
            ],
        )
    return {"ok": True, "object": obj}


def _resolve_distance_reference(service: Any, reference: Any) -> dict[str, Any]:
    if not isinstance(reference, dict):
        return _invalid("A measurement reference must be an object.")
    resolved = _resolve_object(service, reference.get("object_name"))
    if not resolved.get("ok"):
        return resolved
    obj = resolved["object"]
    subelement = str(reference.get("subelement") or "").strip()
    type_id = str(getattr(obj, "TypeId", "") or "")
    placement = _global_placement(obj)
    origin = placement.Base

    if type_id in {"PartDesign::Line", "App::Line"}:
        try:
            direction = _axis_direction(obj)
        except Exception as exc:
            return _invalid(str(exc))
        return {
            "ok": True,
            "kind": "axis",
            "origin": origin,
            "direction": direction,
            "summary": {
                "object_name": obj.Name,
                "subelement": subelement,
                "reference_type": "datum_axis",
                "origin": _vector(origin),
                "direction": _vector(direction),
                "direction_source": "native_line_shape",
            },
        }
    if type_id in {"PartDesign::Plane", "App::Plane"}:
        normal = placement.Rotation.multVec(_z_axis())
        normal = _unit_vector(normal, f"Datum plane {obj.Name}")
        return {
            "ok": True,
            "kind": "plane",
            "origin": origin,
            "normal": normal,
            "summary": {
                "object_name": obj.Name,
                "subelement": subelement,
                "reference_type": "datum_plane",
                "origin": _vector(origin),
                "normal": _vector(normal),
            },
        }
    if type_id in {"PartDesign::Point", "App::Point"}:
        return {
            "ok": True,
            "kind": "point",
            "point": origin,
            "summary": {
                "object_name": obj.Name,
                "subelement": subelement,
                "reference_type": "datum_point",
                "point": _vector(origin),
            },
        }

    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return _invalid(f"Object {obj.Name} has no measurable geometry.")
    if subelement:
        try:
            shape = shape.getElement(subelement)
        except Exception:
            return _invalid(f"Subelement does not exist: {obj.Name}.{subelement}")
        if subelement.startswith("Vertex"):
            return {
                "ok": True,
                "kind": "point",
                "point": shape.Point,
                "summary": {
                    "object_name": obj.Name,
                    "subelement": subelement,
                    "reference_type": "vertex",
                    "point": _vector(shape.Point),
                },
            }
    if not _has_finite_bounds(shape):
        return _invalid(
            f"Reference {obj.Name}.{subelement} has unbounded geometry that is not a "
            "recognized datum point, axis, or plane."
        )
    fidelity = str(
        getattr(obj, "VibeCADGeometryFidelity", "exact_brep") or "exact_brep"
    )
    return {
        "ok": True,
        "kind": "shape",
        "shape": shape,
        "object": obj,
        "subelement": subelement,
        "fidelity": fidelity,
        "summary": {
            "object_name": obj.Name,
            "subelement": subelement,
            "reference_type": "bounded_subelement" if subelement else "bounded_shape",
            "fidelity": fidelity,
        },
    }


def _distance_between(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    first_kind = first["kind"]
    second_kind = second["kind"]
    if "shape" in {first_kind, second_kind}:
        raise RuntimeError(
            "Bounded geometry must be measured by the isolated geometry worker."
        )

    if first_kind == "point":
        if second_kind == "point":
            return _point_point_distance(first["point"], second["point"])
        if second_kind == "axis":
            return _point_axis_distance(
                first["point"], second["origin"], second["direction"]
            )
        if second_kind == "plane":
            return _point_plane_distance(
                first["point"], second["origin"], second["normal"]
            )
    if first_kind == "axis":
        if second_kind == "point":
            return _reverse_distance(_distance_between(second, first))
        if second_kind == "axis":
            return _axis_axis_distance(
                first["origin"],
                first["direction"],
                second["origin"],
                second["direction"],
            )
        if second_kind == "plane":
            return _axis_plane_distance(
                first["origin"],
                first["direction"],
                second["origin"],
                second["normal"],
            )
    if first_kind == "plane":
        if second_kind in {"point", "axis"}:
            return _reverse_distance(_distance_between(second, first))
        if second_kind == "plane":
            return _plane_plane_distance(
                first["origin"],
                first["normal"],
                second["origin"],
                second["normal"],
            )
    raise RuntimeError(
        f"Unsupported distance reference pair: {first_kind} to {second_kind}."
    )


def _point_point_distance(first: Any, second: Any) -> dict[str, Any]:
    return {
        "distance": float((first - second).Length),
        "point_pairs": [(first, second)],
        "calculation": "analytic_point_to_point",
    }


def _point_axis_distance(point: Any, origin: Any, direction: Any) -> dict[str, Any]:
    closest = origin + direction * float((point - origin).dot(direction))
    return {
        "distance": float((point - closest).Length),
        "point_pairs": [(point, closest)],
        "calculation": "analytic_point_to_axis",
    }


def _point_plane_distance(point: Any, origin: Any, normal: Any) -> dict[str, Any]:
    signed_distance = float((point - origin).dot(normal))
    closest = point - normal * signed_distance
    return {
        "distance": abs(signed_distance),
        "point_pairs": [(point, closest)],
        "calculation": "analytic_point_to_plane",
    }


def _axis_axis_distance(
    first_origin: Any,
    first_direction: Any,
    second_origin: Any,
    second_direction: Any,
) -> dict[str, Any]:
    offset = first_origin - second_origin
    dot = float(first_direction.dot(second_direction))
    denominator = 1.0 - dot * dot
    if abs(denominator) <= 1e-12:
        second_closest = second_origin
        first_closest = first_origin + first_direction * float(
            (second_origin - first_origin).dot(first_direction)
        )
        calculation = "analytic_parallel_axis_to_axis"
    else:
        first_projection = float(first_direction.dot(offset))
        second_projection = float(second_direction.dot(offset))
        first_parameter = (
            dot * second_projection - first_projection
        ) / denominator
        second_parameter = (
            second_projection - dot * first_projection
        ) / denominator
        first_closest = first_origin + first_direction * first_parameter
        second_closest = second_origin + second_direction * second_parameter
        calculation = "analytic_axis_to_axis"
    return {
        "distance": float((first_closest - second_closest).Length),
        "point_pairs": [(first_closest, second_closest)],
        "calculation": calculation,
    }


def _axis_plane_distance(
    axis_origin: Any,
    axis_direction: Any,
    plane_origin: Any,
    plane_normal: Any,
) -> dict[str, Any]:
    denominator = float(axis_direction.dot(plane_normal))
    if abs(denominator) > 1e-12:
        parameter = float((plane_origin - axis_origin).dot(plane_normal)) / denominator
        intersection = axis_origin + axis_direction * parameter
        return {
            "distance": 0.0,
            "point_pairs": [(intersection, intersection)],
            "calculation": "analytic_axis_plane_intersection",
            "intersection": {"type": "point", "point": _vector(intersection)},
        }
    measured = _point_plane_distance(axis_origin, plane_origin, plane_normal)
    measured["calculation"] = "analytic_parallel_axis_to_plane"
    return measured


def _plane_plane_distance(
    first_origin: Any,
    first_normal: Any,
    second_origin: Any,
    second_normal: Any,
) -> dict[str, Any]:
    direction = first_normal.cross(second_normal)
    denominator = float(direction.dot(direction))
    if denominator <= 1e-12:
        measured = _point_plane_distance(
            first_origin, second_origin, second_normal
        )
        measured["calculation"] = "analytic_parallel_plane_to_plane"
        return measured

    first_constant = float(first_normal.dot(first_origin))
    second_constant = float(second_normal.dot(second_origin))
    point = (
        second_normal.cross(direction) * first_constant
        + direction.cross(first_normal) * second_constant
    ) / denominator
    intersection_direction = direction / math.sqrt(denominator)
    return {
        "distance": 0.0,
        "point_pairs": [(point, point)],
        "calculation": "analytic_plane_plane_intersection",
        "intersection": {
            "type": "line",
            "origin": _vector(point),
            "direction": _vector(intersection_direction),
        },
    }


def _distance_calculation_path(
    first_state: dict[str, Any], second_state: dict[str, Any]
) -> str:
    first_kind = str(first_state.get("kind") or "unknown")
    second_kind = str(second_state.get("kind") or "unknown")
    return f"{first_kind}_to_{second_kind}"


def _reference_diagnostics(state: dict[str, Any]) -> dict[str, Any]:
    result = {
        "kind": state.get("kind"),
        "summary": state.get("summary"),
    }
    shape = state.get("shape")
    if shape is not None:
        try:
            result["shape"] = {
                "is_null": bool(shape.isNull()),
                "is_valid": bool(shape.isValid()),
                "bounds": partdesign_find_subelements._bounding_box_dict(
                    shape.BoundBox
                ),
            }
        except Exception as exc:
            result["shape_diagnostic_error"] = str(exc)
    return result


def _reverse_distance(measured: dict[str, Any]) -> dict[str, Any]:
    result = dict(measured)
    result["point_pairs"] = [
        (second, first) for first, second in list(measured.get("point_pairs") or [])
    ]
    return result


def _resolve_direction_reference(service: Any, reference: Any) -> dict[str, Any]:
    if not isinstance(reference, dict):
        return _invalid("An angle reference must be an object.")
    resolved = _resolve_object(service, reference.get("object_name"))
    if not resolved.get("ok"):
        return resolved
    obj = resolved["object"]
    subelement = str(reference.get("subelement") or "").strip()
    direction = None
    reference_type = None
    direction_source = None
    type_id = str(getattr(obj, "TypeId", "") or "")
    if type_id in {"PartDesign::Line", "App::Line"}:
        try:
            direction = _axis_direction(obj)
        except Exception as exc:
            return _invalid(str(exc))
        reference_type = "datum_axis"
        direction_source = "native_line_shape"
    elif type_id in {"PartDesign::Plane", "App::Plane"}:
        direction = _global_placement(obj).Rotation.multVec(_z_axis())
        reference_type = "datum_plane_normal"
        direction_source = "global_placement_local_z"
    elif subelement.startswith("Edge"):
        try:
            edge = obj.Shape.getElement(subelement)
            canonical = partdesign_find_subelements._canonical_geometry_type(
                type(edge.Curve).__name__
            )
            if canonical != "line":
                return _invalid(f"Angle edge must be linear: {obj.Name}.{subelement}")
            direction = edge.tangentAt(edge.FirstParameter)
            reference_type = "linear_edge"
            direction_source = "native_edge_tangent"
        except Exception as exc:
            return _invalid(f"Cannot resolve angle edge {obj.Name}.{subelement}: {exc}")
    elif subelement.startswith("Face"):
        try:
            face = obj.Shape.getElement(subelement)
            canonical = partdesign_find_subelements._canonical_geometry_type(
                type(face.Surface).__name__
            )
            if canonical != "plane":
                return _invalid(f"Angle face must be planar: {obj.Name}.{subelement}")
            direction, normal_error = partdesign_find_subelements._outward_normal(
                obj.Shape, face
            )
            if direction is None:
                detail = (normal_error or {}).get("native_error", "unknown error")
                return _invalid(
                    f"Cannot resolve outward normal for {obj.Name}.{subelement}: {detail}"
                )
            reference_type = "planar_face_normal"
            direction_source = "native_face_outward_normal"
        except Exception as exc:
            return _invalid(f"Cannot resolve angle face {obj.Name}.{subelement}: {exc}")
    else:
        return _invalid(
            "Angle references must be datum axes, datum planes, linear edges, or planar faces."
        )
    try:
        direction = _unit_vector(direction, "Angle reference")
    except Exception as exc:
        return _invalid(str(exc))
    return {
        "ok": True,
        "direction": direction,
        "summary": {
            "object_name": obj.Name,
            "subelement": subelement,
            "reference_type": reference_type,
            "direction": _vector(direction),
            "direction_source": direction_source,
        },
    }


def _subelement_measurement(shape: Any, name: str) -> dict[str, Any]:
    try:
        element = shape.getElement(name)
    except Exception as exc:
        return {"ok": False, "error": str(exc)}
    result: dict[str, Any] = {"name": name}
    if name.startswith("Vertex"):
        result.update({"element_type": "vertex", "point": _vector(element.Point)})
    elif name.startswith("Edge"):
        curve = getattr(element, "Curve", None)
        canonical = partdesign_find_subelements._canonical_geometry_type(
            type(curve).__name__ if curve is not None else ""
        )
        result.update(
            {
                "element_type": "edge",
                "geometry_type": canonical,
                "length": float(element.Length),
                "center_of_mass": _vector(element.CenterOfMass),
                "bounding_box": partdesign_find_subelements._bounding_box_dict(
                    element.BoundBox
                ),
                "endpoints": [_vector(vertex.Point) for vertex in list(element.Vertexes)],
            }
        )
        radius = partdesign_find_subelements._element_radius(curve)
        if radius is not None:
            result["radius"] = radius
        if canonical == "line":
            direction = element.tangentAt(element.FirstParameter)
            direction.normalize()
            result["direction"] = _vector(direction)
    elif name.startswith("Face"):
        surface = getattr(element, "Surface", None)
        canonical = partdesign_find_subelements._canonical_geometry_type(
            type(surface).__name__ if surface is not None else ""
        )
        result.update(
            {
                "element_type": "face",
                "geometry_type": canonical,
                "area": float(element.Area),
                "center_of_mass": _vector(element.CenterOfMass),
                "bounding_box": partdesign_find_subelements._bounding_box_dict(
                    element.BoundBox
                ),
            }
        )
        radius = partdesign_find_subelements._element_radius(surface)
        if radius is not None:
            result["radius"] = radius
        if canonical == "plane":
            normal, normal_error = partdesign_find_subelements._outward_normal(shape, element)
            if normal is not None:
                result["outward_normal"] = _vector(normal)
            elif normal_error is not None:
                result["outward_normal_error"] = normal_error
    else:
        return {"ok": False, "error": "Only Vertex, Edge, and Face subelements are supported."}
    return {"ok": True, "measurement": result}


def _global_placement(obj: Any) -> Any:
    method = getattr(obj, "getGlobalPlacement", None)
    if callable(method):
        return method()
    return obj.Placement


def _axis_direction(obj: Any) -> Any:
    """Return a datum/origin axis direction in global document coordinates."""
    shape = getattr(obj, "Shape", None)
    edges = list(getattr(shape, "Edges", []) or []) if shape is not None else []
    if len(edges) != 1:
        raise RuntimeError(
            f"Datum axis {getattr(obj, 'Name', '')} must expose exactly one native line edge; "
            f"found {len(edges)}."
        )
    edge = edges[0]
    canonical = partdesign_find_subelements._canonical_geometry_type(
        type(getattr(edge, "Curve", None)).__name__
    )
    if canonical != "line":
        raise RuntimeError(
            f"Datum axis {getattr(obj, 'Name', '')} exposes {canonical or 'unknown'} "
            "geometry instead of a native line."
        )
    local_direction = _unit_vector(
        edge.tangentAt(edge.FirstParameter),
        f"Datum axis {getattr(obj, 'Name', '')}",
    )

    # The line Shape already contains the object's own Placement. Apply only the
    # parent/container rotation; applying global Placement directly rotates origin
    # axes through their internal display basis a second time.
    local_placement = getattr(obj, "Placement", None)
    if local_placement is None:
        raise RuntimeError(
            f"Datum axis {getattr(obj, 'Name', '')} has no native Placement."
        )
    global_rotation = _global_placement(obj).Rotation
    parent_rotation = global_rotation.multiply(local_placement.Rotation.inverted())
    return _unit_vector(
        parent_rotation.multVec(local_direction),
        f"Datum axis {getattr(obj, 'Name', '')}",
    )


def _z_axis() -> Any:
    import FreeCAD as App

    return App.Vector(0.0, 0.0, 1.0)


def _unit_vector(value: Any, label: str) -> Any:
    if value is None or float(value.Length) <= 1e-12:
        raise RuntimeError(f"{label} has no non-zero direction.")
    return value / float(value.Length)


def _has_finite_bounds(shape: Any) -> bool:
    bounds = shape.BoundBox
    values = (
        float(bounds.XMin),
        float(bounds.YMin),
        float(bounds.ZMin),
        float(bounds.XMax),
        float(bounds.YMax),
        float(bounds.ZMax),
    )
    return all(math.isfinite(value) and abs(value) < 1e50 for value in values)


def _bound_box_corners(shape: Any) -> list[Any]:
    import FreeCAD as App

    if not _has_finite_bounds(shape):
        raise RuntimeError("A bounded-shape measurement received non-finite bounds.")
    bounds = shape.BoundBox
    return [
        App.Vector(x, y, z)
        for x in (float(bounds.XMin), float(bounds.XMax))
        for y in (float(bounds.YMin), float(bounds.YMax))
        for z in (float(bounds.ZMin), float(bounds.ZMax))
    ]


def _bound_box_diagonal(shape: Any) -> float:
    bounds = shape.BoundBox
    return math.sqrt(
        float(bounds.XLength) ** 2
        + float(bounds.YLength) ** 2
        + float(bounds.ZLength) ** 2
    )


def _plain_support(value: Any) -> Any:
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    if isinstance(value, (list, tuple)):
        return [_plain_support(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _plain_support(item) for key, item in value.items()}
    return str(value)


def _vector(value: Any) -> dict[str, float]:
    return {"x": float(value.x), "y": float(value.y), "z": float(value.z)}


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
