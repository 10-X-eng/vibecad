# SPDX-License-Identifier: LGPL-2.1-or-later

"""Concise live state for the Model ribbon."""

from __future__ import annotations

from typing import Any

from VibeCADFastenerModel import model_fastener_graph_from_body
from VibeCADNativeSketchReadiness import sketch_readiness
from VibeCADNativeSnapshot import concise_object, objects_of_type
from VibeCADReferenceContracts import (
    is_native_coordinate_system,
    native_interface_definitions,
)


MAX_MODEL_ITEMS = 24
MAX_COMPONENT_RESOURCES = 16
MAX_VISIBLE_MODEL_ITEMS = 24
MAX_HISTORY_OPERATIONS = 24


def _shape_counts(obj: Any) -> dict[str, int]:
    shape = getattr(obj, "Shape", None)
    if shape is None:
        return {}
    result = {}
    for key, attribute in (
        ("solids", "Solids"),
        ("faces", "Faces"),
        ("edges", "Edges"),
    ):
        try:
            count = len(getattr(shape, attribute))
        except Exception:
            continue
        if count:
            result[key] = count
    return result


def _component_resources(component: Any) -> dict[str, Any]:
    coordinate_systems = [
        value
        for value in list(getattr(component, "Group", []) or [])
        if is_native_coordinate_system(value)
    ][:MAX_COMPONENT_RESOURCES]
    definitions = native_interface_definitions(component)
    names_by_lcs = {
        str(dict(definition.get("selection") or {}).get("native_lcs") or ""): name
        for name, definition in definitions.items()
    }
    resources: dict[str, Any] = {}
    if coordinate_systems:
        resources["local_coordinate_systems"] = [
            {
                **concise_object(lcs),
                **(
                    {"published_interface": names_by_lcs[str(lcs.Name)]}
                    if str(lcs.Name) in names_by_lcs
                    else {}
                ),
            }
            for lcs in coordinate_systems
        ]
    if definitions:
        interfaces = []
        for name, definition in list(definitions.items())[:MAX_COMPONENT_RESOURCES]:
            selection = dict(definition.get("selection") or {})
            connector = dict(definition.get("connector") or {})
            frame = dict(
                dict(definition.get("resolved") or {}).get("connector_frame") or {}
            )
            document = getattr(component, "Document", None)
            lcs = (
                document.getObject(str(selection.get("native_lcs") or ""))
                if document is not None and hasattr(document, "getObject")
                else None
            )
            interface = {
                "name": str(name),
                "kind": str(connector.get("kind") or ""),
                "allowed_joints": list(connector.get("allowed_joints") or []),
                "compatibility": str(connector.get("compatibility") or ""),
            }
            if lcs is not None:
                interface["lcs"] = concise_object(lcs)
            for key in ("origin_mm", "axis_direction", "x_direction"):
                if key in frame:
                    interface[key] = list(frame[key])
            interfaces.append(interface)
        resources["published_interfaces"] = interfaces
    return resources


def _body_summary(body: Any) -> dict[str, Any]:
    result = concise_object(body)
    result["visible"] = bool(getattr(body.ViewObject, "Visibility", False))
    members = list(getattr(body, "Group", []) or [])
    result["feature_count"] = len(members)
    result.update(_component_resources(body))
    tip = getattr(body, "Tip", None)
    if tip is not None:
        result["tip"] = concise_object(tip)
        result["tip"]["shape"] = _shape_counts(tip)
        if str(getattr(tip, "TypeId", "") or "") == "PartDesign::DesignBodyPublication":
            result["tip_policy"] = "stable_body_result"
    return result


def _part_summary(part: Any) -> dict[str, Any]:
    result = concise_object(part)
    result["visible"] = bool(getattr(part.ViewObject, "Visibility", False))
    result["member_count"] = len(list(getattr(part, "Group", []) or []))
    result.update(_component_resources(part))
    return result


def _sketch_summary(sketch: Any) -> dict[str, Any]:
    result = concise_object(sketch)
    result["visible"] = bool(getattr(sketch.ViewObject, "Visibility", False))
    try:
        result["geometry_count"] = int(sketch.GeometryCount)
    except Exception:
        result["geometry_count"] = len(list(getattr(sketch, "Geometry", []) or []))
    result["constraint_count"] = len(list(getattr(sketch, "Constraints", []) or []))
    map_mode = str(getattr(sketch, "MapMode", "") or "")
    if map_mode:
        result["map_mode"] = map_mode
    readiness = sketch_readiness(
        sketch.Document,
        {
            "document_uid": str(sketch.Document.Uid),
            "object_name": str(sketch.Name),
        },
    )
    for key in (
        "fully_constrained",
        "profile",
        "valid",
        "surface_feature_ready",
        "solid_feature_ready",
    ):
        result[key] = readiness[key]
    return result


def _mesh_summary(mesh_object: Any) -> dict[str, Any]:
    result = concise_object(mesh_object)
    mesh = getattr(mesh_object, "Mesh", None)
    try:
        result["points"] = int(mesh.CountPoints)
        result["facets"] = int(mesh.CountFacets)
        bounds = mesh.BoundBox
        result["bounds_mm"] = [
            [float(bounds.XMin), float(bounds.YMin), float(bounds.ZMin)],
            [float(bounds.XMax), float(bounds.YMax), float(bounds.ZMax)],
        ]
    except Exception:
        pass
    result["visible"] = bool(getattr(mesh_object, "Visibility", False))
    return result


def _timeline_role(obj: Any) -> str:
    return str(getattr(obj, "VibeCADTimelineRole", "") or "").strip()


def _is_public_model_container(obj: Any) -> bool:
    check = getattr(obj, "isDerivedFrom", None)
    if not callable(check):
        return str(getattr(obj, "TypeId", "") or "") in {
            "App::Part",
            "PartDesign::Body",
            "PartDesign::Component",
        }
    try:
        return bool(check("App::Part") or check("PartDesign::Body"))
    except Exception:
        return False


def _visibility_parent(obj: Any) -> Any | None:
    for method_name in ("getParentGroup", "getParentGeoFeatureGroup"):
        parent = getattr(obj, method_name, None)
        if not callable(parent):
            continue
        try:
            value = parent()
        except (AttributeError, ReferenceError, RuntimeError):
            continue
        if value is not None:
            return value
    return None


def _effectively_visible(obj: Any) -> bool:
    visited = set()
    current = obj
    while current is not None:
        identity = id(current)
        if identity in visited:
            return False
        visited.add(identity)
        try:
            if not bool(current.ViewObject.Visibility):
                return False
        except (AttributeError, ReferenceError, RuntimeError):
            return False
        current = _visibility_parent(current)
    return True


def _is_intrinsic_origin_feature(obj: Any) -> bool:
    parent = getattr(obj, "getParentGeoFeatureGroup", None)
    if not callable(parent):
        return False
    try:
        owner = parent()
        origin = getattr(owner, "Origin", None) if owner is not None else None
        features = list(getattr(origin, "OriginFeatures", []) or [])
        return obj in features
    except (AttributeError, ReferenceError, RuntimeError, TypeError):
        return False


def _visible_geometry_summary(obj: Any) -> dict[str, Any] | None:
    shape = _shape_counts(obj)
    if (
        not shape
        or (
            _timeline_role(obj) in {"internal", "resource"}
            and not _is_public_model_container(obj)
        )
        or _is_intrinsic_origin_feature(obj)
    ):
        return None
    if not _effectively_visible(obj):
        return None
    result = concise_object(obj)
    result["shape"] = shape
    role = _timeline_role(obj)
    if role:
        result["history_role"] = role
    parent = _visibility_parent(obj)
    if parent is not None:
        result["container"] = concise_object(parent)
    return result


def _history_operation_summaries(document: Any) -> list[dict[str, Any]]:
    timelines = [
        obj
        for obj in list(getattr(document, "Objects", []) or [])
        if str(getattr(obj, "TypeId", "") or "") == "App::DocumentTimeline"
    ]
    if len(timelines) != 1:
        return []
    operations = list(getattr(timelines[0], "Operations", []) or [])
    result = []
    for position, operation in enumerate(operations):
        if operation is None or _timeline_role(operation) != "operation":
            continue
        summary = concise_object(operation)
        summary["history_position"] = position
        status = [
            str(value)
            for value in list(getattr(operation, "State", []) or [])
            if str(value)
        ][:8]
        if status:
            summary["status"] = status
        source_owned = bool(
            getattr(operation, "VibeCADVibeScriptProgramId", "")
            or getattr(operation, "VibeCADScriptedModelId", "")
            or str(getattr(operation, "TypeId", "") or "")
            == "PartDesign::DesignScriptOperation"
        )
        summary["source_owned"] = source_owned
        if hasattr(operation, "Suppressed"):
            summary["suppressed"] = bool(operation.Suppressed)
            summary["native_suppression_available"] = not source_owned
        summary["native_delete_available"] = not source_owned
        result.append(summary)
    return result


def _standard_fastener_summary(document: Any, body: Any) -> dict[str, Any] | None:
    publication = getattr(body, "Tip", None)
    state = getattr(publication, "CurrentState", None)
    operation = getattr(state, "Operation", None)
    if str(getattr(operation, "GeneratorKind", "") or "") != "standard-fastener":
        return None
    try:
        graph = model_fastener_graph_from_body(document, body)
        identity = graph.identity
        return {
            "body": concise_object(graph.body),
            "operation": concise_object(graph.operation),
            "part_number": str(identity["part_number"]),
            "canonical_key": str(identity["canonical_key"]),
            "definition": {
                "standard": str(identity["standard"]),
                "nominal_thread": str(identity["nominal_size"]),
                "length_mm": identity["length_mm"],
                "model_thread": bool(identity["model_thread"]),
                "left_handed": bool(identity["left_handed"]),
                "options": dict(identity["options"]),
            },
        }
    except (KeyError, RuntimeError, TypeError, ValueError):
        return None


def build_model_snapshot(document: Any) -> dict[str, Any]:
    bodies = objects_of_type(document, "PartDesign::Body")
    parts = objects_of_type(document, "App::Part")
    sketches = objects_of_type(document, "Sketcher::SketchObject")
    meshes = objects_of_type(document, "Mesh::Feature")
    shaped = [
        obj
        for obj in list(getattr(document, "Objects", []) or [])
        if _shape_counts(obj)
    ]
    standard_fasteners = [
        summary
        for body in bodies
        if (summary := _standard_fastener_summary(document, body)) is not None
    ]
    visible_geometry = [
        summary
        for obj in list(getattr(document, "Objects", []) or [])
        if (summary := _visible_geometry_summary(obj)) is not None
    ]
    history_operations = _history_operation_summaries(document)
    return {
        "kind": "model",
        "counts": {
            "bodies": len(bodies),
            "components": len(parts),
            "sketches": len(sketches),
            "shaped_objects": len(shaped),
            "meshes": len(meshes),
            "standard_fasteners": len(standard_fasteners),
        },
        "bodies": [_body_summary(value) for value in bodies[:MAX_MODEL_ITEMS]],
        "components": [_part_summary(value) for value in parts[:MAX_MODEL_ITEMS]],
        "sketches": [_sketch_summary(value) for value in sketches[:MAX_MODEL_ITEMS]],
        "meshes": [_mesh_summary(value) for value in meshes[:MAX_MODEL_ITEMS]],
        "standard_fasteners": standard_fasteners[:MAX_MODEL_ITEMS],
        "history_operations": history_operations[:MAX_HISTORY_OPERATIONS],
        "history_operations_truncated": len(history_operations) > MAX_HISTORY_OPERATIONS,
        "body_tip_policy": "stable_body_result",
        "visible_geometry": visible_geometry[:MAX_VISIBLE_MODEL_ITEMS],
        "visible_geometry_truncated": len(visible_geometry) > MAX_VISIBLE_MODEL_ITEMS,
    }
