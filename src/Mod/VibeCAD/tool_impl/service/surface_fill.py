# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Surface filling patch from exact boundary edges."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import (
    domain_runtime,
    partdesign_dressup_feature,
    partdesign_find_subelements,
)


_CURVE_SELECTION_SCHEMA = deepcopy(
    partdesign_dressup_feature.selection_schema(
        allow_all_edges=False,
        edge_only=True,
        required_count=1,
    )
)
_CURVE_SELECTION_SCHEMA["oneOf"].insert(
    0,
    {
        "type": "object",
        "properties": {
            "type": {
                "const": "whole_wire",
                "description": "Use the object's one and only native wire as this curve reference.",
            }
        },
        "required": ["type"],
        "additionalProperties": False,
    },
)

CURVE_REF_ITEM_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "object_name": {
            "type": "string",
            "description": "Exact internal name of the curve or shaped object.",
        },
        "selection": _CURVE_SELECTION_SCHEMA,
    },
    "required": ["object_name", "selection"],
    "additionalProperties": False,
}


TOOL_SPEC = {
    "name": "surface.fill",
    "description": (
        "Create one native Surface filling patch that covers a closed loop of "
        "exact boundary edges. The referenced edges must connect end-to-end "
        "into one closed loop or the patch fails. Use exact edge names from "
        "the active Surface context; never guess them."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "SurfaceWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "boundary_edges": {
                "type": "array",
                "items": CURVE_REF_ITEM_SCHEMA,
                "minItems": 1,
                "description": (
                    "Boundary edge references, in loop order, that together "
                    "form one closed loop."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new surface patch.",
            },
        },
        "required": ["boundary_edges", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    boundary_edges: list[dict[str, Any]],
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    validation = validate_curve_refs(service, boundary_edges, "boundary_edges")
    if not validation.get("ok"):
        return validation
    refs = validation["refs"]
    boundary = boundary_diagnostics(service, refs)
    if not boundary.get("closed_loop"):
        return _invalid(
            "The resolved boundary does not form one connected closed loop; no surface was created.",
            resolved_curves=validation["resolved_curves"],
            boundary_diagnostics=boundary,
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        filling = active.addObject("Surface::Filling", "SurfaceFill")
        filling.Label = clean_label
        filling.BoundaryEdges = build_link_sub_list(active, refs)
        active.recompute()
        return {
            "document": active.Name,
            "feature": filling.Name,
            "feature_label": filling.Label,
            "feature_type": filling.TypeId,
            "boundary_edges": validation["resolved_curves"],
            "boundary_diagnostics": boundary,
            "actual_boundary_links": _link_sub_readback(filling.BoundaryEdges),
            "shape": domain_runtime.shape_summary(filling),
            "feature_state": domain_runtime.feature_state_summary(filling),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        shape = result.get("shape") or {}
        state = result.get("feature_state") or {}
        checks = [
            {
                "name": "boundary_links",
                "ok": _link_sub_subelement_count(result.get("actual_boundary_links") or []) == len(refs),
                "expected": len(refs),
                "actual_count": _link_sub_subelement_count(result.get("actual_boundary_links") or []),
                "actual": result.get("actual_boundary_links"),
            },
            {
                "name": "surface_created",
                "ok": int(shape.get("faces", 0)) > 0
                and state.get("shape_valid") is True
                and not state.get("marked_invalid"),
                "actual": shape,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create surface fill: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation="surface_fill")


def validate_curve_refs(
    service: Any,
    raw_refs: Any,
    param_name: str,
) -> dict[str, Any]:
    """Resolve each count-guarded curve reference to one native edge or wire."""
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    if not isinstance(raw_refs, list) or not raw_refs:
        return _invalid(f"{param_name} must contain at least one curve reference.")
    refs: list[tuple[str, str]] = []
    resolved_curves: list[dict[str, Any]] = []
    for ref_index, entry in enumerate(raw_refs):
        if not isinstance(entry, dict):
            return _invalid(f"Each {param_name} item must be an object.", reference_index=ref_index)
        object_name = str(entry.get("object_name") or "").strip()
        obj = doc.getObject(object_name) if object_name else None
        if obj is None:
            return _invalid(
                f"Object not found by exact internal name: {object_name}",
                reference_index=ref_index,
                candidates=[
                    {"name": candidate.Name, "label": candidate.Label, "type": candidate.TypeId}
                    for candidate in list(getattr(doc, "Objects", []) or [])
                    if getattr(candidate, "Shape", None) is not None
                ][:40],
            )
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            return _invalid(
                f"Object has no shape geometry: {object_name}",
                reference_index=ref_index,
            )
        selection = entry.get("selection")
        if not isinstance(selection, dict):
            return _invalid("selection must be an object.", reference_index=ref_index)
        mode = str(selection.get("type") or "")
        if mode == "whole_wire":
            wires = list(getattr(shape, "Wires", []) or [])
            if len(wires) != 1:
                return _invalid(
                    "whole_wire requires exactly one native wire on the source object.",
                    reference_index=ref_index,
                    object_name=object_name,
                    wire_count=len(wires),
                    shape=domain_runtime.shape_summary(obj),
                )
            edge_names = [
                _edge_name_in_shape(shape, edge)
                for edge in list(wires[0].Edges)
            ]
            if any(name is None for name in edge_names):
                return _invalid(
                    "FreeCAD could not map every wire edge back to a stable source subelement.",
                    reference_index=ref_index,
                    object_name=object_name,
                )
            edge_names = [str(name) for name in edge_names]
            if param_name == "profiles" and len(edge_names) != 1:
                return _invalid(
                    "surface.loft requires each section reference to resolve to one native curve; this wire contains multiple edges.",
                    reference_index=ref_index,
                    object_name=object_name,
                    edge_names=edge_names,
                )
            ref_pairs = [(object_name, name) for name in edge_names]
            edge_name = edge_names[0] if len(edge_names) == 1 else ""
            try:
                geometry = _edge_summaries(service, obj, edge_names)
            except RuntimeError as exc:
                return _invalid(
                    str(exc),
                    reference_index=ref_index,
                    object_name=object_name,
                    requested_edges=edge_names,
                )
        else:
            selection_state = partdesign_dressup_feature.resolve_selection(
                service,
                obj,
                selection,
                allow_all_edges=False,
                face_only=False,
                edge_only=True,
            )
            if not selection_state.get("ok"):
                return _invalid(
                    selection_state.get("error") or "Curve selection failed.",
                    reference_index=ref_index,
                    selection_failure=selection_state,
                )
            names = list(selection_state.get("subelements") or [])
            if len(names) != 1:
                return _invalid(
                    "Each curve reference must resolve to exactly one edge.",
                    reference_index=ref_index,
                    resolved_count=len(names),
                    resolved_geometry=selection_state.get("resolved_geometry") or [],
                )
            edge_name = names[0]
            edge_names = [edge_name]
            ref_pairs = [(object_name, edge_name)]
            geometry = list(selection_state.get("resolved_geometry") or [])
        refs.extend(ref_pairs)
        resolved_curves.append(
            {
                "reference_index": ref_index,
                "object_name": object_name,
                "object_label": obj.Label,
                "object_type": obj.TypeId,
                "selection": dict(selection),
                "native_edge_name": edge_name or None,
                "expanded_native_edges": edge_names,
                "whole_wire": mode == "whole_wire",
                "geometry": geometry,
                "shape_health": domain_runtime.shape_health(obj),
            }
        )
    if len(set(refs)) != len(refs):
        return _invalid(
            f"{param_name} cannot contain duplicate references.",
            resolved_curves=resolved_curves,
        )
    return {"ok": True, "refs": refs, "resolved_curves": resolved_curves}


def build_link_sub_list(active: Any, refs: list[tuple[str, str]]) -> list[Any]:
    """Build a FreeCAD LinkSubList value from validated curve references."""
    entries: list[Any] = []
    for object_name, edge_name in refs:
        obj = active.getObject(object_name)
        if obj is None:
            raise RuntimeError(f"The object no longer exists: {object_name}")
        if edge_name:
            entries.append((obj, edge_name))
        else:
            entries.append(obj)
    return entries


def boundary_diagnostics(service: Any, refs: list[tuple[str, str]]) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return {"closed_loop": False, "failure": "no_active_document"}
    curves: list[dict[str, Any]] = []
    native_edges: list[Any] = []
    for object_name, edge_name in refs:
        obj = doc.getObject(object_name)
        if obj is None:
            return {"closed_loop": False, "failure": "source_disappeared", "object": object_name}
        if edge_name:
            edges = [obj.Shape.Edges[int(edge_name.removeprefix("Edge")) - 1]]
            names = [edge_name]
        else:
            wires = list(obj.Shape.Wires)
            if len(wires) != 1:
                return {"closed_loop": False, "failure": "whole_wire_count_changed", "object": object_name}
            edges = list(wires[0].Edges)
            names = [f"Edge{index}" for index in range(1, len(edges) + 1)]
        for name, edge in zip(names, edges):
            descriptor = _native_edge_descriptor(object_name, name, edge)
            curves.append(descriptor)
            native_edges.append(edge)
    if not curves:
        return {"closed_loop": False, "failure": "no_resolved_edges", "curves": []}
    pair_gaps = []
    connected = True
    for index in range(len(curves)):
        following = (index + 1) % len(curves)
        gap = _endpoint_gap(curves[index], curves[following])
        gap.update({"from_curve_index": index, "to_curve_index": following})
        pair_gaps.append(gap)
        if float(gap["minimum_gap_mm"]) > 1.0e-6:
            connected = False
    intersections = []
    for first in range(len(native_edges)):
        for second in range(first + 1, len(native_edges)):
            if second == first + 1 or first == 0 and second == len(native_edges) - 1:
                continue
            try:
                section = native_edges[first].section(native_edges[second])
                vertices = [
                    domain_runtime.vector_values(vertex.Point)
                    for vertex in list(getattr(section, "Vertexes", []) or [])
                ]
                edges = len(list(getattr(section, "Edges", []) or []))
                if vertices or edges:
                    intersections.append(
                        {
                            "first_curve_index": first,
                            "second_curve_index": second,
                            "vertices": vertices,
                            "overlap_edge_count": edges,
                        }
                    )
            except Exception as exc:
                intersections.append(
                    {
                        "first_curve_index": first,
                        "second_curve_index": second,
                        "native_stage": "BRepAlgoAPI_Section",
                        "native_error": str(exc),
                    }
                )
    descriptor_errors = [
        {
            "curve_index": index,
            "object_name": curve.get("object_name"),
            "edge_name": curve.get("edge_name"),
            "native_stage": "BRepAdaptor_Curve::tangentAt",
            "native_error": curve.get("tangent_error"),
        }
        for index, curve in enumerate(curves)
        if curve.get("tangent_error")
    ]
    closed_single = len(curves) == 1 and bool(curves[0].get("closed"))
    closed_loop = bool(
        (connected or closed_single) and not intersections and not descriptor_errors
    )
    return {
        "closed_loop": closed_loop,
        "connected_in_supplied_order": connected,
        "curve_count": len(curves),
        "curves": curves,
        "ordered_endpoint_gaps": pair_gaps,
        "nonadjacent_intersections": intersections,
        "descriptor_errors": descriptor_errors,
        "complete": not descriptor_errors
        and not any(item.get("native_error") for item in intersections),
        "tolerance_mm": 1.0e-6,
    }


def loft_profile_diagnostics(service: Any, refs: list[tuple[str, str]]) -> dict[str, Any]:
    doc = service._active_document()
    profiles = []
    native_shapes = []
    for index, (object_name, edge_name) in enumerate(refs):
        obj = doc.getObject(object_name) if doc is not None else None
        if obj is None:
            return {"ok": False, "failure": "source_disappeared", "profile_index": index}
        shape = (
            obj.Shape.Edges[int(edge_name.removeprefix("Edge")) - 1]
            if edge_name
            else obj.Shape.Wires[0]
        )
        native_shapes.append(shape)
        profiles.append(
            {
                "index": index,
                "object_name": object_name,
                "edge_name": edge_name or None,
                "closed": bool(shape.isClosed()),
                "edge_count": len(list(getattr(shape, "Edges", []) or [])),
                "bounds": domain_runtime.bound_box_summary(shape.BoundBox),
                "valid": bool(shape.isValid()),
            }
        )
    pairwise = []
    intersects = False
    for index in range(len(native_shapes) - 1):
        first = native_shapes[index]
        second = native_shapes[index + 1]
        try:
            distance, point_pairs, _ = first.distToShape(second)
            section = first.section(second)
            section_vertices = len(list(getattr(section, "Vertexes", []) or []))
            section_edges = len(list(getattr(section, "Edges", []) or []))
            pair_intersects = section_vertices > 0 or section_edges > 0
            intersects = intersects or pair_intersects
            pairwise.append(
                {
                    "first_profile_index": index,
                    "second_profile_index": index + 1,
                    "distance_mm": float(distance),
                    "closest_point_pairs": [
                        {
                            "first": domain_runtime.vector_values(pair[0]),
                            "second": domain_runtime.vector_values(pair[1]),
                        }
                        for pair in list(point_pairs or [])[:4]
                    ],
                    "intersects": pair_intersects,
                    "section_vertices": section_vertices,
                    "section_edges": section_edges,
                }
            )
        except Exception as exc:
            pairwise.append(
                {
                    "first_profile_index": index,
                    "second_profile_index": index + 1,
                    "native_stage": "BRepExtrema_DistShapeShape/BRepAlgoAPI_Section",
                    "native_error": str(exc),
                }
            )
    closure_modes = {profile["closed"] for profile in profiles}
    native_errors = [item for item in pairwise if item.get("native_error")]
    return {
        "ok": bool(not intersects and not native_errors and len(closure_modes) == 1),
        "ordered_profiles": profiles,
        "pairwise": pairwise,
        "compatible_closure": len(closure_modes) == 1,
        "profiles_intersect": intersects,
        "first_failing_profile": next(
            (
                item.get("second_profile_index")
                for item in pairwise
                if item.get("intersects") or item.get("native_error")
            ),
            None,
        ),
    }


def _edge_summaries(service: Any, obj: Any, names: list[str]) -> list[dict[str, Any]]:
    result = partdesign_find_subelements.run(
        service,
        object_name=obj.Name,
        element_type="edge",
    )
    if not result.get("ok"):
        raise RuntimeError(
            "Could not inspect the requested source edges: "
            f"{result.get('failure_code') or 'SUBELEMENT_INSPECTION_FAILED'}: "
            f"{result.get('error') or 'no native diagnostic message'}"
        )
    by_name = {item["name"]: item for item in result.get("matches") or []}
    missing = [name for name in names if name not in by_name]
    if missing:
        raise RuntimeError(
            f"FreeCAD did not return descriptors for source edges {missing}; "
            f"available edges were {sorted(by_name)}."
        )
    return [by_name[name] for name in names]


def _edge_name_in_shape(shape: Any, edge: Any) -> str | None:
    for index, candidate in enumerate(list(getattr(shape, "Edges", []) or []), start=1):
        try:
            if edge.isSame(candidate):
                return f"Edge{index}"
        except Exception as exc:
            raise RuntimeError(
                f"FreeCAD could not compare a wire edge with source Edge{index}: {exc}"
            ) from exc
    return None


def _native_edge_descriptor(object_name: str, edge_name: str, edge: Any) -> dict[str, Any]:
    first = edge.valueAt(edge.FirstParameter)
    last = edge.valueAt(edge.LastParameter)
    tangent_error = None
    try:
        first_tangent = edge.tangentAt(edge.FirstParameter)
        last_tangent = edge.tangentAt(edge.LastParameter)
    except Exception as exc:
        first_tangent = None
        last_tangent = None
        tangent_error = str(exc)
    return {
        "object_name": object_name,
        "edge_name": edge_name,
        "curve_type": type(edge.Curve).__name__,
        "closed": bool(edge.isClosed()),
        "length_mm": float(edge.Length),
        "start": domain_runtime.vector_values(first),
        "end": domain_runtime.vector_values(last),
        "start_tangent": domain_runtime.vector_values(first_tangent) if first_tangent is not None else None,
        "end_tangent": domain_runtime.vector_values(last_tangent) if last_tangent is not None else None,
        "tangent_error": tangent_error,
    }


def _endpoint_gap(first: dict[str, Any], second: dict[str, Any]) -> dict[str, Any]:
    import FreeCAD as App

    candidates = []
    for first_role in ("start", "end"):
        for second_role in ("start", "end"):
            first_point = App.Vector(*first[first_role])
            second_point = App.Vector(*second[second_role])
            candidates.append(
                {
                    "first_role": first_role,
                    "second_role": second_role,
                    "gap_mm": float((second_point - first_point).Length),
                }
            )
    best = min(candidates, key=lambda item: item["gap_mm"])
    return {
        "minimum_gap_mm": best["gap_mm"],
        "join_roles": [best["first_role"], best["second_role"]],
        "orientation_reversal_needed": best["first_role"] == best["second_role"],
    }


def _link_sub_readback(value: Any) -> list[dict[str, Any]]:
    result = []
    for entry in list(value or []):
        if isinstance(entry, tuple):
            obj = entry[0] if entry else None
            subs = entry[1] if len(entry) > 1 else []
            if isinstance(subs, str):
                subs = [subs]
            result.append({"object_name": getattr(obj, "Name", None), "subelements": list(subs or [])})
        else:
            result.append({"object_name": getattr(entry, "Name", None), "subelements": []})
    return result


def _link_sub_subelement_count(entries: list[dict[str, Any]]) -> int:
    return sum(len(list(entry.get("subelements") or [])) for entry in entries)


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
