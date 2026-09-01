# SPDX-License-Identifier: LGPL-2.1-or-later

"""Workbench/domain read-tool implementations outside ``VibeCADCore``."""

from __future__ import annotations

import math
from typing import Any


GEOMETRY_TOLERANCE_MM = 1.0e-7


def _live_document_object(obj: Any, document: Any) -> bool:
    """Return whether *obj* is the exact live identity in *document*."""
    if obj is None or document is None:
        return False
    try:
        name = str(obj.Name)
        return obj.Document is document and document.getObject(name) is obj
    except (AttributeError, RuntimeError):
        return False


def _ensure_timeline_property(
    obj: Any,
    type_id: str,
    property_name: str,
    description: str,
) -> None:
    if property_name in obj.PropertiesList:
        actual_type = obj.getTypeIdOfProperty(property_name)
        if actual_type != type_id:
            raise TypeError(
                f"{obj.Name}.{property_name} must be "
                f"{type_id}, not {actual_type}."
            )
    else:
        obj.addProperty(
            type_id,
            property_name,
            "Timeline",
            description,
            attr=16,
            hidden=True,
            locked=True,
        )
    obj.setPropertyStatus(
        property_name,
        ("Hidden", "LockDynamic", "NoRecompute"),
    )
    obj.setEditorMode(property_name, 2)


def _mark_timeline_operation(operation: Any) -> None:
    document = getattr(operation, "Document", None)
    if not _live_document_object(operation, document):
        raise ValueError(
            "A timeline operation must be one exact live document object."
        )
    _ensure_timeline_property(
        operation,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document timeline classification",
    )
    if "VibeCADTimelineOwner" in operation.PropertiesList:
        if (
            operation.getTypeIdOfProperty("VibeCADTimelineOwner")
            != "App::PropertyLinkHidden"
            or operation.VibeCADTimelineOwner is not None
        ):
            raise TypeError(
                f"{operation.Name} cannot be a root operation while it "
                "retains timeline-owner metadata."
            )
        operation.setEditorMode("VibeCADTimelineOwner", 2)
    operation.VibeCADTimelineRole = "operation"


def _mark_timeline_resource(resource: Any, owner: Any) -> None:
    document = getattr(owner, "Document", None)
    if (
        resource is owner
        or not _live_document_object(owner, document)
        or not _live_document_object(resource, document)
    ):
        raise ValueError(
            "A timeline resource and its distinct owner must be exact live "
            "objects in one document."
        )
    _ensure_timeline_property(
        resource,
        "App::PropertyString",
        "VibeCADTimelineRole",
        "Document timeline classification",
    )
    _ensure_timeline_property(
        resource,
        "App::PropertyLinkHidden",
        "VibeCADTimelineOwner",
        "Timeline operation which owns this implementation object",
    )
    resource.VibeCADTimelineOwner = owner
    resource.VibeCADTimelineRole = "resource"


def finalize_new_timeline_operation(
    operation: Any,
    ordered_resources: list[Any] | tuple[Any, ...] = (),
) -> None:
    """Publish one exact newly-created operation and its implementation graph.

    The caller supplies every resource identity in canonical operation-block
    order.
    No document delta, type search, label match, or transaction-time guess is
    used. The native timeline proves that every supplied identity was enrolled
    by the still-active caller transaction before accepting the block.
    """
    document = getattr(operation, "Document", None)
    if not _live_document_object(operation, document):
        raise ValueError(
            "A new timeline operation must be one exact live document object."
        )

    resources = list(ordered_resources)
    seen = {operation}
    for resource in resources:
        if (
            resource in seen
            or not _live_document_object(resource, document)
        ):
            raise ValueError(
                "Every timeline resource must be one distinct exact live "
                "identity in the operation document."
            )
        seen.add(resource)

    publisher = getattr(
        document,
        "publishProvisionalTimelineOperationBlock",
        None,
    )
    if not callable(publisher):
        raise RuntimeError(
            "The native atomic timeline-publication API is unavailable."
        )
    publisher(
        operation,
        resources,
    )


class NewTimelineOperation:
    """Publish one exact new operation even when later setup fails.

    Callers register identities as their native factories return them. On
    scope exit, the still-live registered graph is published atomically by
    the document timeline. The original exception is never swallowed, so a
    failed native command remains both inspectable and one coherent history
    step.
    """

    def __init__(self) -> None:
        self._operation: Any | None = None
        self._resources: list[Any] = []
        self._publication_attempted = False

    def __enter__(self) -> NewTimelineOperation:
        return self

    def __exit__(self, _exc_type: Any, _exc: Any, _traceback: Any) -> bool:
        self.publish()
        return False

    def set_operation(self, operation: Any) -> Any:
        document = getattr(operation, "Document", None)
        if not _live_document_object(operation, document):
            raise ValueError(
                "A new timeline operation must be one exact live document "
                "object."
            )
        if self._operation is not None and self._operation is not operation:
            raise ValueError(
                "One creation scope cannot publish two timeline operations."
            )
        self._operation = operation
        return operation

    def add_resource(self, resource: Any) -> Any:
        operation = self._operation
        document = getattr(operation, "Document", None)
        if (
            operation is None
            or resource is operation
            or not _live_document_object(resource, document)
        ):
            raise ValueError(
                "A new timeline resource must be one distinct exact live "
                "object in the operation document."
            )
        if resource in self._resources:
            raise ValueError(
                "A new timeline resource cannot be registered twice."
            )
        self._resources.append(resource)
        return resource

    def publish(self) -> None:
        if self._publication_attempted:
            return
        self._publication_attempted = True
        operation = self._operation
        document = getattr(operation, "Document", None)
        if not _live_document_object(operation, document):
            return
        live_resources = [
            resource
            for resource in self._resources
            if _live_document_object(resource, document)
        ]
        finalize_new_timeline_operation(operation, live_resources)


def mark_modeling_replaced_inputs(obj: Any, inputs: list[Any]) -> bool:
    """Record visible inputs whose presentation is replaced by one root operation."""
    exact_inputs = [input_obj for input_obj in inputs if input_obj is not None]
    if not exact_inputs:
        return False
    try:
        import PartGui
    except ImportError:
        return False
    return bool(PartGui.setModelingReplacedInputs(obj, exact_inputs))


def adopt_part_result(obj: Any, replaced_inputs: list[Any] | None = None) -> Any:
    """Place a Part graph in the active Body and track root-result replacements."""
    adopted = None
    try:
        import PartDesignGui
    except ImportError:
        pass
    else:
        adopted = PartDesignGui.adoptPartResult(obj)
    if replaced_inputs:
        mark_modeling_replaced_inputs(obj, replaced_inputs)
    return adopted


def vector_values(vector: Any) -> list[float]:
    """Return one FreeCAD vector as a JSON-safe XYZ triplet."""
    return [float(vector.x), float(vector.y), float(vector.z)]


def normalized_vector_summary(vector: Any) -> dict[str, Any]:
    """Return requested magnitude and an exact normalized direction."""
    magnitude = float(vector.Length)
    if magnitude <= 1.0e-12:
        return {
            "ok": False,
            "magnitude": magnitude,
            "direction": None,
            "failure": "zero_length_vector",
        }
    normalized = vector / magnitude
    return {
        "ok": True,
        "magnitude": magnitude,
        "direction": vector_values(normalized),
    }


def view_visibility_summary(obj: Any) -> dict[str, Any]:
    """Read GUI visibility without pretending it exists in a headless process."""
    view = getattr(obj, "ViewObject", None)
    if view is None or not hasattr(view, "Visibility"):
        return {
            "supported": False,
            "visible": None,
            "reason": "Object has no native ViewObject.Visibility property in this process.",
        }
    try:
        return {"supported": True, "visible": bool(view.Visibility)}
    except Exception as exc:
        return {"supported": True, "visible": None, "error": str(exc)}


def shape_profile_diagnostics(obj: Any) -> dict[str, Any]:
    """Describe native planar-wire and FaceMaker eligibility for a shaped object."""
    shape = getattr(obj, "Shape", None)
    if shape is None or bool(shape.isNull()):
        return {
            "ok": False,
            "object": getattr(obj, "Name", None),
            "failure": "shape_is_null",
            "planar": False,
            "wire_count": 0,
            "face_buildable": False,
        }
    try:
        plane = shape.findPlane()
    except Exception as exc:
        plane = None
        plane_error = str(exc)
    else:
        plane_error = None
    vertices = list(getattr(shape, "Vertexes", []) or [])
    plane_deviations: list[float] = []
    if plane is not None:
        origin = plane.Position
        normal = plane.Axis
        for vertex in vertices:
            offset = vertex.Point - origin
            plane_deviations.append(abs(float(offset.dot(normal))))
    wires = list(getattr(shape, "Wires", []) or [])
    wire_facts: list[dict[str, Any]] = []
    for index, wire in enumerate(wires):
        wire_vertices = list(getattr(wire, "Vertexes", []) or [])
        closed = bool(wire.isClosed())
        endpoint_gap = 0.0 if closed else None
        endpoints: list[list[float]] = []
        if not closed and len(wire_vertices) >= 2:
            first = wire_vertices[0].Point
            last = wire_vertices[-1].Point
            endpoint_gap = float((last - first).Length)
            endpoints = [vector_values(first), vector_values(last)]
        try:
            wire_valid = bool(wire.isValid())
            wire_valid_error = None
        except Exception as exc:
            wire_valid = None
            wire_valid_error = str(exc)
        wire_facts.append(
            {
                "index": index,
                "edge_count": len(list(getattr(wire, "Edges", []) or [])),
                "vertex_count": len(wire_vertices),
                "closed": closed,
                "valid": wire_valid,
                "validity_error": wire_valid_error,
                "endpoints": endpoints,
                "endpoint_gap_mm": endpoint_gap,
            }
        )
    face_buildable = False
    face_builder: dict[str, Any] = {
        "algorithm": "Part::FaceMakerBullseye",
        "attempted": False,
        "face_count": 0,
        "valid": False,
    }
    if plane is not None and wires and all(item["closed"] for item in wire_facts):
        face_builder["attempted"] = True
        try:
            import Part

            face_shape = Part.makeFace(wires, "Part::FaceMakerBullseye")
            face_count = len(list(getattr(face_shape, "Faces", []) or []))
            face_valid = bool(face_shape.isValid()) and not bool(face_shape.isNull())
            face_buildable = face_count > 0 and face_valid
            face_builder.update(
                {
                    "face_count": face_count,
                    "valid": face_valid,
                    "shape": {
                        "faces": face_count,
                        "wires": len(list(getattr(face_shape, "Wires", []) or [])),
                    },
                }
            )
        except Exception as exc:
            face_builder["error"] = str(exc)
    max_deviation = max(plane_deviations, default=None)
    all_wires_closed = bool(wires) and all(item["closed"] for item in wire_facts)
    all_wires_valid = bool(wires) and all(item["valid"] is True for item in wire_facts)
    return {
        "ok": bool(plane is not None and all_wires_valid),
        "object": getattr(obj, "Name", None),
        "shape": shape_summary(obj),
        "planar": plane is not None,
        "plane": (
            {
                "origin": vector_values(plane.Position),
                "normal": vector_values(plane.Axis),
                "max_vertex_deviation_mm": max_deviation,
            }
            if plane is not None
            else None
        ),
        "plane_error": plane_error,
        "wire_count": len(wires),
        "wires": wire_facts,
        "all_wires_closed": all_wires_closed,
        "all_wires_valid": all_wires_valid,
        "existing_face_count": len(list(getattr(shape, "Faces", []) or [])),
        "face_buildable": face_buildable,
        "face_builder": face_builder,
    }


def ordered_point_diagnostics(
    vectors: list[Any],
    *,
    closed: bool,
    tolerance_mm: float = GEOMETRY_TOLERANCE_MM,
) -> dict[str, Any]:
    """Diagnose an ordered point chain before creating a Draft curve."""
    count = len(vectors)
    points = [vector_values(vector) for vector in vectors]
    duplicates: list[dict[str, Any]] = []
    for first in range(count):
        for second in range(first + 1, count):
            distance = float((vectors[second] - vectors[first]).Length)
            is_closure_pair = bool(closed and first == 0 and second == count - 1)
            if distance <= tolerance_mm and not is_closure_pair:
                duplicates.append(
                    {
                        "first_index": first,
                        "second_index": second,
                        "distance_mm": distance,
                    }
                )

    centroid = None
    plane_normal = None
    max_deviation = None
    projected: list[tuple[float, float]] = []
    if vectors:
        import FreeCAD as App

        centroid = App.Vector()
        for vector in vectors:
            centroid += vector
        centroid /= float(count)
        normal = App.Vector()
        for index in range(count):
            current = vectors[index]
            following = vectors[(index + 1) % count]
            normal.x += (current.y - following.y) * (current.z + following.z)
            normal.y += (current.z - following.z) * (current.x + following.x)
            normal.z += (current.x - following.x) * (current.y + following.y)
        if float(normal.Length) <= 1.0e-12:
            for first in range(1, count):
                for second in range(first + 1, count):
                    candidate = (vectors[first] - vectors[0]).cross(
                        vectors[second] - vectors[0]
                    )
                    if float(candidate.Length) > 1.0e-12:
                        normal = candidate
                        break
                if float(normal.Length) > 1.0e-12:
                    break
        if float(normal.Length) > 1.0e-12:
            normal.normalize()
            plane_normal = vector_values(normal)
            deviations = [abs(float((point - centroid).dot(normal))) for point in vectors]
            max_deviation = max(deviations, default=0.0)
            x_axis = None
            for vector in vectors[1:]:
                candidate = vector - vectors[0]
                candidate -= normal * float(candidate.dot(normal))
                if float(candidate.Length) > 1.0e-12:
                    candidate.normalize()
                    x_axis = candidate
                    break
            if x_axis is not None:
                y_axis = normal.cross(x_axis)
                projected = [
                    (
                        float((point - centroid).dot(x_axis)),
                        float((point - centroid).dot(y_axis)),
                    )
                    for point in vectors
                ]

    intersections: list[dict[str, Any]] = []
    segment_count = count if closed else max(0, count - 1)
    if len(projected) == count:
        for first in range(segment_count):
            first_next = (first + 1) % count
            for second in range(first + 1, segment_count):
                second_next = (second + 1) % count
                if second in {first, first_next} or second_next in {first, first_next}:
                    continue
                if closed and first == 0 and second_next == 0:
                    continue
                intersection = _segment_intersection_2d(
                    projected[first],
                    projected[first_next],
                    projected[second],
                    projected[second_next],
                    tolerance_mm,
                )
                if intersection is not None:
                    intersections.append(
                        {
                            "first_segment": [first, first_next],
                            "second_segment": [second, second_next],
                            "projected_point": [intersection[0], intersection[1]],
                        }
                    )
    closure_gap = (
        float((vectors[-1] - vectors[0]).Length) if vectors and not closed else 0.0
    )
    plane_under_determined = plane_normal is None
    planar = plane_under_determined or float(max_deviation or 0.0) <= tolerance_mm
    return {
        "ok": bool(not duplicates and planar and not intersections),
        "point_count": count,
        "points": points,
        "closed": bool(closed),
        "duplicate_points": duplicates,
        "plane": {
            "origin": vector_values(centroid) if centroid is not None else None,
            "normal": plane_normal,
            "max_deviation_mm": max_deviation,
            "tolerance_mm": float(tolerance_mm),
            "planar": planar,
            "under_determined": plane_under_determined,
        },
        "segment_self_intersections": intersections,
        "closure_gap_mm": closure_gap,
    }


def _segment_intersection_2d(
    first_start: tuple[float, float],
    first_end: tuple[float, float],
    second_start: tuple[float, float],
    second_end: tuple[float, float],
    tolerance: float,
) -> tuple[float, float] | None:
    ax, ay = first_start
    bx, by = first_end
    cx, cy = second_start
    dx, dy = second_end
    denominator = (bx - ax) * (dy - cy) - (by - ay) * (dx - cx)
    if abs(denominator) <= tolerance:
        return None
    first_t = ((cx - ax) * (dy - cy) - (cy - ay) * (dx - cx)) / denominator
    second_t = ((cx - ax) * (by - ay) - (cy - ay) * (bx - ax)) / denominator
    if tolerance < first_t < 1.0 - tolerance and tolerance < second_t < 1.0 - tolerance:
        return (ax + first_t * (bx - ax), ay + first_t * (by - ay))
    return None


def bound_box_summary(bound_box: Any) -> dict[str, Any] | None:
    if bound_box is None:
        return None
    try:
        return {
            "xmin": float(bound_box.XMin),
            "ymin": float(bound_box.YMin),
            "zmin": float(bound_box.ZMin),
            "xmax": float(bound_box.XMax),
            "ymax": float(bound_box.YMax),
            "zmax": float(bound_box.ZMax),
            "xlength": float(bound_box.XLength),
            "ylength": float(bound_box.YLength),
            "zlength": float(bound_box.ZLength),
        }
    except Exception as exc:
        raise RuntimeError(f"FreeCAD could not read shape bounds: {exc}") from exc


def shape_summary(obj: Any) -> dict[str, Any]:
    try:
        shape = getattr(obj, "Shape", None)
    except Exception as exc:
        return {
            "available": False,
            "inspection_complete": False,
            "solids": 0,
            "faces": 0,
            "edges": 0,
            "vertices": 0,
            "volume": 0.0,
            "error": str(exc),
        }
    if shape is None:
        return {
            "available": False,
            "inspection_complete": True,
            "solids": 0,
            "faces": 0,
            "edges": 0,
            "vertices": 0,
            "volume": 0.0,
        }
    try:
        summary = {
            "available": True,
            "inspection_complete": True,
            "solids": len(getattr(shape, "Solids", []) or []),
            "faces": len(getattr(shape, "Faces", []) or []),
            "edges": len(getattr(shape, "Edges", []) or []),
            "vertices": len(getattr(shape, "Vertexes", []) or []),
            "volume": float(getattr(shape, "Volume", 0.0) or 0.0),
        }
    except Exception as exc:
        return {
            "available": False,
            "inspection_complete": False,
            "solids": 0,
            "faces": 0,
            "edges": 0,
            "vertices": 0,
            "volume": 0.0,
            "error": str(exc),
        }
    try:
        bound_box = bound_box_summary(getattr(shape, "BoundBox", None))
    except Exception as exc:
        bound_box = None
        summary["inspection_complete"] = False
        summary["bound_box_error"] = str(exc)
    if bound_box:
        summary["bound_box"] = bound_box
    return summary


def shape_health(obj: Any) -> dict[str, Any]:
    """Return exact native shape validity and bounds for one document object."""
    summary = shape_summary(obj)
    state = feature_state_summary(obj)
    return {
        "object": getattr(obj, "Name", None),
        "type": getattr(obj, "TypeId", None),
        "shape": summary,
        "feature_state": state,
        "valid_non_null": bool(
            summary.get("available")
            and summary.get("inspection_complete") is True
            and state.get("inspection_complete") is True
            and not state.get("shape_null")
            and state.get("shape_valid") is True
            and not state.get("marked_invalid")
        ),
    }


def sketch_plane_summary(sketch: Any) -> dict[str, Any]:
    import FreeCAD as App

    placement = sketch.getGlobalPlacement()
    normal = placement.Rotation.multVec(App.Vector(0.0, 0.0, 1.0))
    x_axis = placement.Rotation.multVec(App.Vector(1.0, 0.0, 0.0))
    return {
        "origin": [
            float(placement.Base.x),
            float(placement.Base.y),
            float(placement.Base.z),
        ],
        "normal": [float(normal.x), float(normal.y), float(normal.z)],
        "x_axis": [float(x_axis.x), float(x_axis.y), float(x_axis.z)],
    }


def profile_input_summary(service: Any, sketch: Any) -> dict[str, Any]:
    owner = service._partdesign_body_for_feature(sketch)
    return {
        "name": getattr(sketch, "Name", None),
        "label": getattr(sketch, "Label", getattr(sketch, "Name", None)),
        "owner_body": getattr(owner, "Name", None),
        "global_plane": sketch_plane_summary(sketch),
        "profile": service._sketch_profile_status(sketch),
    }


def ordered_section_preflight(service: Any, sections: list[Any]) -> dict[str, Any]:
    summaries = [profile_input_summary(service, section) for section in sections]
    wire_counts = [int(item["profile"].get("wire_count") or 0) for item in summaries]
    edge_counts = [
        [int(wire.get("edge_count") or 0) for wire in item["profile"].get("wires", [])]
        for item in summaries
    ]
    repeated_planes = []
    for index in range(1, len(summaries)):
        before = summaries[index - 1]["global_plane"]["origin"]
        after = summaries[index]["global_plane"]["origin"]
        distance = math.sqrt(sum((float(after[i]) - float(before[i])) ** 2 for i in range(3)))
        if distance <= 1.0e-9:
            repeated_planes.append(
                {
                    "first_section_index": index - 1,
                    "second_section_index": index,
                    "origin_gap_mm": distance,
                }
            )
    compatible_wire_counts = bool(wire_counts) and len(set(wire_counts)) == 1
    return {
        "ok": compatible_wire_counts and not repeated_planes,
        "ordered_sections": summaries,
        "wire_counts": wire_counts,
        "edge_counts_by_wire": edge_counts,
        "compatible_wire_counts": compatible_wire_counts,
        "repeated_section_planes": repeated_planes,
        "first_failing_section": (
            repeated_planes[0]["second_section_index"]
            if repeated_planes
            else next(
                (
                    index
                    for index, count in enumerate(wire_counts)
                    if count != wire_counts[0]
                ),
                None,
            )
            if wire_counts
            else None
        ),
    }


def path_preflight(service: Any, profile: Any, spine: Any) -> dict[str, Any]:
    profile_facts = profile_input_summary(service, profile)
    spine_shape = getattr(spine, "Shape", None)
    path_facts: dict[str, Any] = {
        "name": getattr(spine, "Name", None),
        "type": getattr(spine, "TypeId", None),
        "shape": shape_health(spine),
    }
    if getattr(spine, "TypeId", "") == "Sketcher::SketchObject":
        path_facts["profile"] = service._sketch_profile_status(spine)
    if spine_shape is None or bool(spine_shape.isNull()):
        return {
            "ok": False,
            "profile": profile_facts,
            "path": path_facts,
            "failure": "spine_has_no_shape",
        }
    profile_shape = getattr(profile, "Shape", None)
    if profile_shape is None or bool(profile_shape.isNull()):
        return {
            "ok": False,
            "profile": profile_facts,
            "path": path_facts,
            "failure": "profile_has_no_shape",
        }
    try:
        distance, point_pairs, support = profile_shape.distToShape(spine_shape)
    except Exception as exc:
        return {
            "ok": False,
            "profile": profile_facts,
            "path": path_facts,
            "failure": "profile_path_distance_failed",
            "native_stage": "BRepExtrema_DistShapeShape",
            "native_error": str(exc),
        }
    intersects = float(distance) <= 1.0e-7
    return {
        "ok": intersects,
        "profile": profile_facts,
        "path": path_facts,
        "profile_path_distance_mm": float(distance),
        "profile_path_intersects": intersects,
        "closest_point_pairs": [
            {
                "profile": [float(pair[0].x), float(pair[0].y), float(pair[0].z)],
                "path": [float(pair[1].x), float(pair[1].y), float(pair[1].z)],
            }
            for pair in list(point_pairs or [])[:4]
        ],
        "native_support_count": len(list(support or [])),
        "failure": None if intersects else "profile_does_not_intersect_path",
    }


def shape_delta(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    delta = {
        "volume_delta": float(after.get("volume", 0.0) or 0.0)
        - float(before.get("volume", 0.0) or 0.0),
        "solids_delta": int(after.get("solids", 0) or 0)
        - int(before.get("solids", 0) or 0),
        "faces_delta": int(after.get("faces", 0) or 0)
        - int(before.get("faces", 0) or 0),
        "edges_delta": int(after.get("edges", 0) or 0)
        - int(before.get("edges", 0) or 0),
        "vertices_delta": int(after.get("vertices", 0) or 0)
        - int(before.get("vertices", 0) or 0),
    }
    before_box = (
        before.get("bound_box") if isinstance(before.get("bound_box"), dict) else {}
    )
    after_box = (
        after.get("bound_box") if isinstance(after.get("bound_box"), dict) else {}
    )
    box_delta = {}
    for key in (
        "xmin",
        "ymin",
        "zmin",
        "xmax",
        "ymax",
        "zmax",
        "xlength",
        "ylength",
        "zlength",
    ):
        if key in before_box and key in after_box:
            box_delta[f"{key}_delta"] = float(after_box.get(key, 0.0) or 0.0) - float(
                before_box.get(key, 0.0) or 0.0
            )
    if box_delta:
        delta["bound_box_delta"] = box_delta
    return delta


def partdesign_feature_effect(
    operation: str,
    body_shape_before: dict[str, Any],
    body_shape_after: dict[str, Any],
    feature_shape: dict[str, Any],
) -> dict[str, Any]:
    delta = shape_delta(body_shape_before, body_shape_after)
    feature_has_shape = bool(feature_shape.get("available")) and (
        int(feature_shape.get("solids", 0) or 0) > 0
        or int(feature_shape.get("faces", 0) or 0) > 0
        or abs(float(feature_shape.get("volume", 0.0) or 0.0)) > 1e-9
    )
    volume_delta = float(delta.get("volume_delta", 0.0) or 0.0)
    topology_changed = any(
        int(delta.get(key, 0) or 0) != 0
        for key in ("solids_delta", "faces_delta", "edges_delta", "vertices_delta")
    )
    bound_box_changed = any(
        abs(float(value or 0.0)) > 1e-9
        for value in (delta.get("bound_box_delta") or {}).values()
    )
    if operation in {
        "pad",
        "revolution",
        "additive_loft",
        "thin_loft",
        "additive_pipe",
        "additive_helix",
        "additive_primitive",
    }:
        expected_direction = volume_delta > 1e-9
        effect_ok = feature_has_shape and expected_direction
    elif operation in {
        "pocket",
        "hole",
        "groove",
        "subtractive_loft",
        "subtractive_pipe",
        "subtractive_helix",
        "subtractive_primitive",
        "boolean_cut",
        "boolean_common",
    }:
        expected_direction = volume_delta < -1e-9
        effect_ok = feature_has_shape and expected_direction
    elif operation in {
        "linear_pattern",
        "polar_pattern",
        "mirror",
        "multi_transform",
        "boolean_fuse",
    }:
        expected_direction = abs(volume_delta) > 1e-9
        effect_ok = feature_has_shape and expected_direction
    else:
        expected_direction = abs(volume_delta) > 1e-9
        effect_ok = feature_has_shape and expected_direction
    return {
        "ok": bool(effect_ok),
        "operation": operation,
        "feature_has_shape": bool(feature_has_shape),
        "expected_volume_direction": bool(expected_direction),
        "topology_changed": bool(topology_changed),
        "bound_box_changed": bool(bound_box_changed),
        "body_shape_delta": delta,
    }


def feature_state_summary(feature: Any) -> dict[str, Any]:
    """Snapshot a feature's recompute health.

    Captures FreeCAD's ``State`` flags (``Invalid``/``Touched``/...), whether
    the feature's own shape passes ``isValid()``, and the feature name.
    """
    field_errors: list[dict[str, str]] = []
    try:
        state = [str(item) for item in (getattr(feature, "State", []) or [])]
    except Exception as exc:
        state = []
        field_errors.append({"field": "State", "error": str(exc)})
    shape_valid: bool | None = None
    shape_null = True
    try:
        shape = getattr(feature, "Shape", None)
        shape_null = shape is None or bool(shape.isNull())
        if not shape_null:
            shape_valid = bool(shape.isValid())
    except Exception as exc:
        shape_valid = None
        field_errors.append({"field": "Shape", "error": str(exc)})
    return {
        "name": getattr(feature, "Name", None),
        "label": getattr(feature, "Label", getattr(feature, "Name", None)),
        "type": getattr(feature, "TypeId", None),
        "state": state,
        "marked_invalid": any("Invalid" in item for item in state),
        "shape_null": shape_null,
        "shape_valid": shape_valid,
        "inspection_complete": not field_errors,
        "field_errors": field_errors,
    }


def invalid_partdesign_tip(body: Any) -> dict[str, Any] | None:
    """Return exact invalid Tip state that must stop downstream feature creation."""
    tip = getattr(body, "Tip", None)
    if tip is None:
        return None
    type_id = str(getattr(tip, "TypeId", ""))
    if type_id == "Sketcher::SketchObject" or type_id in {
        "PartDesign::Plane",
        "PartDesign::Line",
        "PartDesign::Point",
    }:
        return None
    state = feature_state_summary(tip)
    if (
        state.get("inspection_complete") is not True
        or state.get("marked_invalid")
        or state.get("shape_valid") is not True
    ):
        return state
    if type_id.startswith("PartDesign::") and state.get("shape_null"):
        return state
    base_feature = getattr(tip, "BaseFeature", None)
    operation = partdesign_operation_for_feature(tip)
    if base_feature is not None and operation is not None:
        effect = partdesign_feature_effect(
            operation,
            shape_summary(base_feature),
            shape_summary(tip),
            shape_summary(tip),
        )
        if not effect.get("ok"):
            state["feature_effect"] = effect
            state["effect_invalid"] = True
            return state
    return None


def partdesign_operation_for_feature(feature: Any) -> str | None:
    type_id = str(getattr(feature, "TypeId", ""))
    if (
        type_id == "PartDesign::FeatureAdditivePython"
        and str(getattr(feature, "VibeCADFeatureType", "")) == "thin_loft"
    ):
        return "thin_loft"
    if type_id.startswith("PartDesign::Additive") and type_id.removeprefix(
        "PartDesign::Additive"
    ) in {"Box", "Cylinder", "Sphere", "Cone", "Ellipsoid", "Torus", "Prism", "Wedge"}:
        return "additive_primitive"
    if type_id.startswith("PartDesign::Subtractive") and type_id.removeprefix(
        "PartDesign::Subtractive"
    ) in {"Box", "Cylinder", "Sphere", "Cone", "Ellipsoid", "Torus", "Prism", "Wedge"}:
        return "subtractive_primitive"
    if type_id == "PartDesign::Boolean":
        return {
            "Fuse": "boolean_fuse",
            "Cut": "boolean_cut",
            "Common": "boolean_common",
        }.get(str(getattr(feature, "Type", "")))
    return {
        "PartDesign::Pad": "pad",
        "PartDesign::Pocket": "pocket",
        "PartDesign::Hole": "hole",
        "PartDesign::Revolution": "revolution",
        "PartDesign::Groove": "groove",
        "PartDesign::AdditiveLoft": "additive_loft",
        "PartDesign::SubtractiveLoft": "subtractive_loft",
        "PartDesign::AdditivePipe": "additive_pipe",
        "PartDesign::SubtractivePipe": "subtractive_pipe",
        "PartDesign::AdditiveHelix": "additive_helix",
        "PartDesign::SubtractiveHelix": "subtractive_helix",
        "PartDesign::LinearPattern": "linear_pattern",
        "PartDesign::PolarPattern": "polar_pattern",
        "PartDesign::Mirrored": "mirror",
        "PartDesign::MultiTransform": "multi_transform",
        "PartDesign::Fillet": "fillet",
        "PartDesign::Chamfer": "chamfer",
        "PartDesign::Draft": "draft",
        "PartDesign::Thickness": "thickness",
    }.get(type_id)


def finalize_partdesign_feature_effect(
    doc: Any,
    body: Any,
    feature: Any,
    operation: str,
    body_shape_before: dict[str, Any],
) -> dict[str, Any]:
    body_shape_after = shape_summary(body)
    feature_shape = shape_summary(feature)
    feature_state = feature_state_summary(feature)
    feature_effect = partdesign_feature_effect(
        operation,
        body_shape_before,
        body_shape_after,
        feature_shape,
    )
    return {
        "body_shape_before": body_shape_before,
        "body_shape_after": body_shape_after,
        "body_shape_delta": feature_effect["body_shape_delta"],
        "feature_shape": feature_shape,
        "feature_state": feature_state,
        "feature_effect": feature_effect,
        "failed_feature_retained": not bool(feature_effect.get("ok")),
    }


def partdesign_feature_response(
    service: Any,
    transaction: dict[str, Any],
    *,
    operation: str,
    profile_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Flatten one native PartDesign mutation and its exact post-recompute state."""
    result = (
        transaction.get("result")
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    effect = result.get("feature_effect") if isinstance(result, dict) else None
    effect_ok = bool(effect.get("ok")) if isinstance(effect, dict) else False
    transaction_ok = (
        bool(transaction.get("ok")) if isinstance(transaction, dict) else False
    )
    ok = transaction_ok and effect_ok
    native_diagnostics = recompute_diagnostics(transaction)
    body = (
        service._get_partdesign_body(result.get("body")) if result.get("body") else None
    )
    feature_state = result.get("feature_state") or {}
    failure_kind = None
    if not ok:
        if feature_state.get("inspection_complete") is not True:
            failure_kind = "feature_state_inspection_incomplete"
        elif feature_state.get("marked_invalid"):
            failure_kind = "freecad_feature_invalid"
        elif feature_state.get("shape_valid") is not True:
            failure_kind = "freecad_shape_invalid"
        elif not bool((effect or {}).get("feature_has_shape")):
            failure_kind = "feature_has_no_shape"
        elif not bool((effect or {}).get("expected_volume_direction")):
            failure_kind = "body_effect_does_not_match_operation"
        elif not transaction_ok:
            failure_kind = "native_operation_error"
        else:
            failure_kind = "feature_has_no_effect"
    response: dict[str, Any] = {
        "ok": ok,
        "operation": operation,
        "mutation": result,
        "document_delta": transaction.get("document_delta") or {},
        "native_diagnostics": native_diagnostics,
        "feature_state": feature_state,
        "feature_effect": effect or {},
        "body_state": service._partdesign_body_header(body)
        if body is not None
        else None,
        "profile_status": profile_status or {},
        "failed_feature_retained": bool(result.get("feature")) and not ok,
        "state_change": transaction.get("state_change") or {},
    }
    if not ok:
        first_native = native_diagnostics[0] if native_diagnostics else {}
        response["failure"] = {
            "kind": failure_kind,
            "feature": result.get("feature"),
            "body": result.get("body"),
            "native_stage": first_native.get("algorithm"),
            "offending_property": first_native.get("property"),
            "offending_subelement": first_native.get("subelement"),
            "native_diagnostics": native_diagnostics,
        }
        response["failure_code"] = (
            first_native.get("code")
            or transaction.get("failure_code")
            or "PARTDESIGN_FEATURE_FAILED"
        )
        response["failure_stage"] = transaction.get("failure_stage") or (
            "native_recompute" if first_native else "postcondition"
        )
        response["error"] = (
            transaction.get("error")
            or f"PartDesign {operation} did not produce a valid body effect."
        )
        response["retry_same_call"] = False
    return response


def recompute_diagnostics(transaction: dict[str, Any]) -> list[dict[str, Any]]:
    """Return structured diagnostics from a transaction's recompute generation."""
    if not isinstance(transaction, dict):
        return []
    summary = transaction.get("native_diagnostics")
    if not isinstance(summary, dict):
        return []
    return [
        dict(item)
        for item in list(summary.get("diagnostics") or [])
        if isinstance(item, dict)
    ]


def describe_ineffective_partdesign_feature(
    operation: str,
    *,
    feature_shape: dict[str, Any] | None,
    feature_effect: dict[str, Any] | None,
    feature_state: dict[str, Any] | None,
    native_diagnostics: list[dict[str, Any]] | None,
    lead_in: str | None = None,
) -> str:
    """Describe only measured PartDesign postconditions and native diagnostics."""
    parts: list[str] = [
        lead_in
        or (
            f"PartDesign {operation} was created but did not produce an "
            "effective body shape change."
        )
    ]
    shape = feature_shape if isinstance(feature_shape, dict) else {}
    if shape.get("available"):
        parts.append(
            "The feature itself computed a shape "
            f"({int(shape.get('solids', 0) or 0)} solid(s), "
            f"{int(shape.get('faces', 0) or 0)} face(s), "
            f"volume {float(shape.get('volume', 0.0) or 0.0):.3f} mm^3)."
        )
    else:
        parts.append("The feature did not compute a usable shape of its own.")
    state = feature_state if isinstance(feature_state, dict) else {}
    if state.get("marked_invalid"):
        parts.append("FreeCAD marked the feature Invalid after recompute.")
    effect = feature_effect if isinstance(feature_effect, dict) else {}
    delta = effect.get("body_shape_delta")
    if isinstance(delta, dict):
        parts.append(
            "Body shape delta: "
            f"volume {float(delta.get('volume_delta', 0.0) or 0.0):+.3f} mm^3, "
            f"solids {int(delta.get('solids_delta', 0) or 0):+d}, "
            f"faces {int(delta.get('faces_delta', 0) or 0):+d}."
        )
    diagnostics = [
        item for item in list(native_diagnostics or []) if isinstance(item, dict)
    ]
    if diagnostics:
        parts.append(
            "FreeCAD diagnostics: "
            + " | ".join(
                f"{item.get('code')} on {item.get('object')}: {item.get('message')}"
                for item in diagnostics
            )
        )
    parts.append(
        "The failed feature was left in the document for inspection or deletion."
    )
    return " ".join(parts)


def build_mutation_result(
    transaction: dict[str, Any],
    *,
    extra: dict[str, Any] | None = None,
    next_action: str | None = None,
) -> dict[str, Any]:
    """Standard rich result envelope for mutating service tools.

    Wraps a ``run_freecad_transaction`` result with the uniform keys every
    mutation should expose: ``ok``, ``error`` (when failed), ``transaction``
    (including document before/after/delta snapshots), and flattened
    structured ``native_diagnostics``. Tool-specific payload goes in ``extra``.
    """
    if not isinstance(transaction, dict):
        transaction = {"ok": False, "error": "Invalid transaction result."}
    envelope: dict[str, Any] = {
        "ok": bool(transaction.get("ok")),
        "transaction": transaction,
        "native_diagnostics": recompute_diagnostics(transaction),
        "document_delta": transaction.get("document_delta"),
    }
    if transaction.get("error"):
        envelope["error"] = str(transaction["error"])
    if extra:
        for key, value in extra.items():
            envelope.setdefault(key, value)
    if next_action is not None:
        envelope["next_action"] = next_action
    return envelope


def vector_schema(description: str, *, units: str | None = "mm") -> dict[str, Any]:
    """JSON-schema fragment for an exact ``{x, y, z}`` vector parameter."""
    suffix = f" in {units}" if units else ""
    return {
        "type": "object",
        "properties": {
            "x": {"type": "number", "description": f"X component{suffix}."},
            "y": {"type": "number", "description": f"Y component{suffix}."},
            "z": {"type": "number", "description": f"Z component{suffix}."},
        },
        "required": ["x", "y", "z"],
        "additionalProperties": False,
        "description": description,
    }


def parse_vector(value: Any) -> Any:
    """Build an ``App.Vector`` from a validated ``{x, y, z}`` mapping."""
    import FreeCAD as App

    return App.Vector(float(value["x"]), float(value["y"]), float(value["z"]))


def part_feature_result(
    transaction: dict[str, Any],
    *,
    operation: str,
    next_action: str = (
        "Inspect the created object, then continue modeling or capture a screenshot."
    ),
) -> dict[str, Any]:
    """Rich result envelope for Part-workbench feature mutations.

    Extends :func:`build_mutation_result` with the Part contract: the new
    object's shape summary, its recompute-health state, and an ``ok`` verdict
    that requires a computed, valid, non-empty shape.
    """
    envelope = build_mutation_result(transaction, next_action=next_action)
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    feature_state = (
        result.get("feature_state")
        if isinstance(result.get("feature_state"), dict)
        else {}
    )
    shape = result.get("shape") if isinstance(result.get("shape"), dict) else {}
    has_geometry = (
        int(shape.get("faces", 0) or 0) > 0 or int(shape.get("edges", 0) or 0) > 0
    )
    shape_ok = (
        bool(shape.get("available"))
        and shape.get("inspection_complete") is True
        and has_geometry
        and feature_state.get("inspection_complete") is True
        and not feature_state.get("marked_invalid")
        and feature_state.get("shape_valid") is True
    )
    envelope["ok"] = bool(envelope.get("ok")) and shape_ok
    envelope["operation"] = operation
    envelope["mutation"] = result
    envelope["feature_state"] = feature_state
    envelope["shape"] = shape
    envelope["failed_feature_retained"] = (
        bool(result.get("feature")) and not envelope["ok"]
    )
    if not envelope["ok"] and not envelope.get("error"):
        envelope["error"] = (
            f"Part {operation} was created but did not compute a valid shape. "
            "The failed object was left in the document for inspection or deletion."
        )
        envelope["recoverable"] = True
    return envelope


def build_partdesign_feature_result(
    service: Any,
    transaction: dict[str, Any],
    *,
    operation: str,
    active_sketch: str | None = None,
    profile_status: dict[str, Any] | None = None,
    next_action: str = (
        "Inspect the created feature, then create the next component/detail "
        "or capture a screenshot."
    ),
) -> dict[str, Any]:
    """Rich result envelope for PartDesign feature mutations.

    Extends :func:`build_mutation_result` with the feature-effect contract:
    body shape before/after/delta, feature shape summary, and effectiveness
    verdict.
    """
    if not isinstance(transaction, dict):
        transaction = {"ok": False, "error": "Invalid transaction result."}
    result = (
        transaction.get("result", {})
        if isinstance(transaction.get("result"), dict)
        else {}
    )
    feature_effect = result.get("feature_effect")
    effective = not isinstance(feature_effect, dict) or bool(feature_effect.get("ok"))
    ok = bool(transaction.get("ok")) and effective
    error: str | None = None
    feature_state = (
        result.get("feature_state")
        if isinstance(result.get("feature_state"), dict)
        else None
    )
    if transaction.get("ok") and not effective:
        error = describe_ineffective_partdesign_feature(
            operation,
            feature_shape=result.get("feature_shape"),
            feature_effect=feature_effect,
            feature_state=feature_state,
            native_diagnostics=recompute_diagnostics(transaction),
        )
    envelope: dict[str, Any] = {
        "ok": ok,
        "transaction": transaction,
        "native_diagnostics": recompute_diagnostics(transaction),
        "partdesign": partdesign_summary(service),
        "active_feature": result.get("feature"),
        "feature_shape": result.get("feature_shape"),
        "feature_state": feature_state,
        "body_shape_before": result.get("body_shape_before"),
        "body_shape_after": result.get("body_shape_after"),
        "body_shape_delta": result.get("body_shape_delta"),
        "feature_effect": feature_effect,
        "failed_feature_retained": result.get("failed_feature_retained"),
        "next_action": next_action,
    }
    if error:
        envelope["error"] = error
        envelope["recoverable"] = True
    elif transaction.get("error"):
        envelope["error"] = str(transaction["error"])
    if active_sketch is not None:
        envelope["active_sketch"] = active_sketch
    if profile_status is not None:
        envelope["profile_status"] = profile_status
    return envelope


def spreadsheet_summary(
    service: Any,
    sheet_name: str | None = None,
    max_columns: int = 8,
    max_rows: int = 20,
) -> dict[str, Any]:
    sheet = service._get_spreadsheet(sheet_name)
    sheets = service._spreadsheet_objects()
    if sheet is None:
        return {
            "found": False,
            "requested": sheet_name,
            "sheet_count": len(sheets),
            "sheets": [service._object_summary(item) for item in sheets],
        }

    safe_columns = max(1, min(int(max_columns), 26))
    safe_rows = max(1, min(int(max_rows), 200))
    cells = []
    for column_index in range(1, safe_columns + 1):
        for row in range(1, safe_rows + 1):
            cell = service._cell_name(column_index, row)
            try:
                contents = sheet.getContents(cell)
            except Exception:
                contents = ""
            if contents in ("", None):
                continue
            try:
                value = sheet.get(cell)
            except Exception as exc:
                value = f"<error: {exc}>"
            cells.append(
                {
                    "cell": cell,
                    "contents": service._short_value(contents),
                    "value": service._short_value(value),
                }
            )
    return {
        "found": True,
        "sheet": service._object_summary(sheet),
        "scanned_columns": safe_columns,
        "scanned_rows": safe_rows,
        "non_empty_count": len(cells),
        "cells": cells,
    }


def draft_summary(service: Any) -> dict[str, Any]:
    objects = [service._draft_object_summary(obj) for obj in service._draft_objects()]
    return {"object_count": len(objects), "objects": objects}


def partdesign_summary(service: Any, body_name: str | None = None) -> dict[str, Any]:
    bodies = service._partdesign_bodies()
    body = service._get_partdesign_body(body_name)
    return {
        "body_count": len(bodies),
        "bodies": [service._partdesign_body_summary(item) for item in bodies],
        "selected_body": service._partdesign_body_summary(body) if body else None,
    }


def techdraw_summary(service: Any, page_name: str | None = None) -> dict[str, Any]:
    pages = service._techdraw_pages()
    page = service._get_techdraw_page(page_name)
    return {
        "page_count": len(pages),
        "pages": [service._techdraw_page_summary(item) for item in pages],
        "selected_page": service._techdraw_page_summary(page) if page else None,
    }


def fem_summary(
    service: Any,
    analysis_name: str | None = None,
    *,
    offset: int = 0,
    limit: int = 80,
) -> dict[str, Any]:
    analyses = service._fem_analyses()
    analysis = service._get_fem_analysis(analysis_name)
    if type(offset) is not int or offset < 0:
        raise ValueError("FEM analysis offset must be a non-negative integer.")
    if type(limit) is not int or not 1 <= limit <= 80:
        raise ValueError("FEM analysis limit must be an integer from 1 through 80.")
    page = analyses[offset : offset + limit]
    return {
        "analysis_count": len(analyses),
        "returned_count": len(page),
        "offset": offset,
        "limit": limit,
        "has_more": offset + len(page) < len(analyses),
        "analyses": [service._fem_analysis_summary(item) for item in page],
        "selected_analysis": service._fem_analysis_summary(analysis)
        if analysis
        else None,
    }


def cam_summary(service: Any, job_name: str | None = None) -> dict[str, Any]:
    jobs = service._cam_jobs()
    job = service._get_cam_job(job_name)
    return {
        "job_count": len(jobs),
        "jobs": [service._cam_job_summary(item) for item in jobs],
        "selected_job": service._cam_job_summary(job) if job else None,
    }


def assembly_summary(service: Any) -> dict[str, Any]:
    doc = service._active_document()
    assemblies = [service._assembly_summary(obj) for obj in service._assembly_objects()]
    return {
        "document": getattr(doc, "Name", None) if doc else None,
        "assembly_count": len(assemblies),
        "assemblies": assemblies,
    }


def inspection_summary(service: Any) -> dict[str, Any]:
    features = [
        service._inspection_feature_summary(obj)
        for obj in service._inspection_features()
    ]
    candidates = [
        service._document_object_summary(obj)
        for obj in service._inspection_candidates()
    ]
    return {
        "feature_count": len(features),
        "features": features,
        "candidate_count": len(candidates),
        "candidates": candidates,
    }


def surface_summary(service: Any) -> dict[str, Any]:
    objects = [
        service._surface_object_summary(obj) for obj in service._surface_objects()
    ]
    return {"object_count": len(objects), "objects": objects}


def robot_summary(service: Any) -> dict[str, Any]:
    doc = service._active_document()
    objects = [service._robot_object_summary(obj) for obj in doc.Objects] if doc else []
    robot_like = [obj for obj in objects if obj.get("robot_role")]
    return {
        "object_count": len(objects),
        "robot_object_count": len(robot_like),
        "objects": objects,
    }


def meshpart_summary(service: Any) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return {
            "document": None,
            "part_candidate_count": 0,
            "mesh_count": 0,
            "part_candidates": [],
            "meshes": [],
        }
    part_candidates = [
        service._part_object_summary(obj)
        for obj in doc.Objects
        if service._is_meshpart_part_candidate(obj)
    ]
    meshes = [
        service._mesh_object_summary(obj)
        for obj in doc.Objects
        if service._is_meshpart_mesh_output(obj)
    ]
    return {
        "document": doc.Name,
        "part_candidate_count": len(part_candidates),
        "mesh_count": len(meshes),
        "part_candidates": part_candidates[:80],
        "meshes": meshes[:80],
    }


def part_summary(service: Any) -> dict[str, Any]:
    objects = [service._part_object_summary(obj) for obj in service._part_objects()]
    return {"object_count": len(objects), "objects": objects}


def mesh_summary(service: Any) -> dict[str, Any]:
    objects = [service._mesh_object_summary(obj) for obj in service._mesh_objects()]
    return {"object_count": len(objects), "objects": objects}


def points_summary(service: Any) -> dict[str, Any]:
    objects = [service._points_object_summary(obj) for obj in service._points_objects()]
    return {"object_count": len(objects), "objects": objects}


def material_summary(service: Any) -> dict[str, Any]:
    objects = [
        service._material_object_summary(obj)
        for obj in service._material_capable_objects()
    ]
    return {"object_count": len(objects), "objects": objects}


def placement_summary(obj: Any) -> dict[str, Any]:
    """JSON-safe snapshot of an object's stored local Placement property."""
    placement = getattr(obj, "Placement", None)
    if placement is None:
        return {
            "supported": False,
            "placement": None,
            "reason": "Object has no native Placement property.",
        }
    try:
        return {
            "supported": True,
            "position": {
                "x": float(placement.Base.x),
                "y": float(placement.Base.y),
                "z": float(placement.Base.z),
            },
            "rotation_axis": {
                "x": float(placement.Rotation.Axis.x),
                "y": float(placement.Rotation.Axis.y),
                "z": float(placement.Rotation.Axis.z),
            },
            "rotation_angle_degrees": math.degrees(float(placement.Rotation.Angle)),
        }
    except Exception as exc:
        return {"supported": True, "placement": None, "error": str(exc)}


def global_placement_summary(obj: Any) -> dict[str, Any]:
    """Read the object's native global placement without substituting local state."""
    getter = getattr(obj, "getGlobalPlacement", None)
    try:
        if callable(getter):
            placement = getter()
            method = "object.getGlobalPlacement"
            context = None
        else:
            import FreeCAD as App

            parents = [
                parent
                for parent in list(getattr(obj, "InList", []) or [])
                if parent.isDerivedFrom("Assembly::AssemblyObject")
            ]
            if len(parents) != 1:
                return {
                    "supported": False,
                    "placement": None,
                    "reason": "A non-GeoFeature requires exactly one assembly parent context.",
                    "assembly_parent_candidates": [parent.Name for parent in parents],
                }
            parent = parents[0]
            placement = App.GeoFeature.getGlobalPlacementOf(
                obj,
                parent,
                f"{obj.Name}.",
            )
            method = "App.GeoFeature.getGlobalPlacementOf"
            context = {"root_object": parent.Name, "subname": f"{obj.Name}."}
        return {
            "supported": True,
            "method": method,
            "context": context,
            "placement": {
                "position": {
                    "x": float(placement.Base.x),
                    "y": float(placement.Base.y),
                    "z": float(placement.Base.z),
                },
                "rotation_axis": {
                    "x": float(placement.Rotation.Axis.x),
                    "y": float(placement.Rotation.Axis.y),
                    "z": float(placement.Rotation.Axis.z),
                },
                "rotation_angle_degrees": math.degrees(float(placement.Rotation.Angle)),
            },
        }
    except Exception as exc:
        return {"supported": True, "placement": None, "error": str(exc)}


def assembly_joint_group(assembly: Any) -> Any:
    """Return the assembly's existing native JointGroup, or ``None``."""
    for child in list(getattr(assembly, "OutList", []) or []):
        if str(getattr(child, "TypeId", "")) == "Assembly::JointGroup":
            return child
    return None


def assembly_solver_diagnostics(assembly: Any) -> dict[str, Any]:
    getter = getattr(assembly, "getSolverDiagnostics", None)
    if not callable(getter):
        return {
            "available": False,
            "reason": "This FreeCAD build does not expose AssemblyObject.getSolverDiagnostics().",
        }
    try:
        diagnostics = getter()
    except Exception as exc:
        return {"available": False, "error": str(exc)}
    if not isinstance(diagnostics, dict):
        return {
            "available": False,
            "error": "getSolverDiagnostics() returned a non-object value.",
        }
    return {"available": True, **diagnostics}


ASSEMBLY_SOLVER_MEANINGS: dict[int, str] = {
    0: "solved",
    -1: "solver_error",
    -2: "redundant_constraints",
    -3: "conflicting_constraints",
    -4: "over_constrained",
    -5: "malformed_constraints",
    -6: "no_grounded_component",
}


def assembly_solver_verdict(code: int) -> str:
    """Map the native Assembly::AssemblyObject.solve() return code to a verdict."""
    return ASSEMBLY_SOLVER_MEANINGS.get(int(code), f"unknown_status_{int(code)}")


def is_spreadsheet(obj: Any) -> bool:
    """True when the object is a native Spreadsheet::Sheet."""
    return str(getattr(obj, "TypeId", "")) == "Spreadsheet::Sheet"


def spreadsheet_display_value(value: Any) -> Any:
    """Convert an evaluated spreadsheet cell value to a JSON-safe scalar.

    FreeCAD returns plain numbers/strings for simple cells and Quantity
    objects for cells with units; Quantities are rendered via their
    user-facing string so units stay visible to the model.
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    user_string = getattr(value, "UserString", None)
    if user_string is not None:
        return str(user_string)
    return str(value)
