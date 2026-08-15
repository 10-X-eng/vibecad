# SPDX-License-Identifier: LGPL-2.1-or-later

"""Durable FEM-reference rebinding after VibeScript regeneration."""

from __future__ import annotations

from typing import Any

import VibeCADReferenceContracts as reference_contracts

_ELEMENT_PREFIXES = ("Face", "Edge", "Vertex")


def _validate_references(
    service: Any,
    raw_refs: Any,
) -> tuple[list[dict[str, Any]], str | None]:
    doc = service._active_document()
    if doc is None:
        return [], "No active document."
    if not isinstance(raw_refs, list) or not raw_refs:
        return [], "references must contain at least one item."
    refs: list[dict[str, Any]] = []
    for entry in raw_refs:
        if not isinstance(entry, dict):
            return [], "Each references item must be an object."
        object_name = str(entry.get("object_name") or "").strip()
        element = str(entry.get("element") or "").strip()
        obj = doc.getObject(object_name) if object_name else None
        if obj is None:
            return [], f"Object not found by exact internal name: {object_name}"
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            return [], f"Object has no shape geometry: {object_name}"
        selection = entry.get("selection")
        if reference_contracts.published_object(obj) is not None and not isinstance(
            selection, dict
        ):
            return [], (
                f"{object_name} is a regenerating scripted output. Reference a "
                "published semantic interface instead of an exact FaceN/EdgeN/VertexN."
            )
        if isinstance(selection, dict):
            if selection.get("type") != "published_interface":
                return [], "references selection.type must be published_interface."
            interface_name = str(selection.get("interface_name") or "").strip()
            try:
                interface = reference_contracts.resolve_interface(
                    service, obj, interface_name
                )
            except reference_contracts.ReferenceContractError as exc:
                return [], f"{object_name}: {exc}"
            names = list(interface.get("subelements") or [])
            geometry = list(interface.get("geometry") or [])
            if not names or len(names) != len(geometry):
                return [], (
                    f"Published FEM interface {interface_name!r} must resolve "
                    "to one or more faces, edges, or vertices."
                )
            managed_selection = {
                "type": "published_interface",
                "interface_name": interface_name,
                "model_id": interface["model_id"],
                "publication_name": interface["publication_name"],
                "output_key": interface["output_key"],
            }
            for name, descriptor in zip(names, geometry):
                refs.append(
                    {
                        "object_name": object_name,
                        "element": name,
                        "geometry": descriptor,
                        "managed_selection": managed_selection,
                    }
                )
            continue
        if not element.startswith(_ELEMENT_PREFIXES):
            return [], (
                f"Element names must look like Face1, Edge3, or Vertex2; got: {element}"
            )
        try:
            subshape = shape.getElement(element)
        except Exception:
            return [], (
                f"{object_name} has no subelement named {element}. Provide an "
                "exact subelement name from the active FEM document context."
            )
        refs.append(
            {
                "object_name": object_name,
                "element": element,
                "geometry": _subelement_descriptor(subshape),
            }
        )
    keys = [(item["object_name"], item["element"]) for item in refs]
    if len(set(keys)) != len(keys):
        return [], "references cannot contain duplicate items."
    return refs, None


def rebind_scripted_reference(
    service: Any,
    constraint_obj: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    raw_references = contract.get("references")
    if not isinstance(raw_references, list) or not raw_references:
        return _invalid("The FEM reference contract contains no model references.")
    refs, error = _validate_references(service, raw_references)
    if error is not None:
        return _invalid(error)
    direction_ref = None
    raw_direction = contract.get("direction")
    if isinstance(raw_direction, dict):
        direction_refs, error = _validate_references(service, [raw_direction])
        if error is not None or len(direction_refs) != 1:
            return _invalid(error or "The FEM direction interface did not resolve once.")
        direction_ref = direction_refs[0]
    doc = service._active_document()
    try:
        constraint_obj.References = [
            (doc.getObject(item["object_name"]), item["element"]) for item in refs
        ]
        if direction_ref is not None and hasattr(constraint_obj, "Direction"):
            constraint_obj.Direction = (
                doc.getObject(direction_ref["object_name"]),
                [direction_ref["element"]],
            )
        constraint_obj.touch()
        reference_contracts.mark_stale(
            constraint_obj,
            str(contract.get("source_revision") or ""),
            "A referenced scripted model changed; regenerate the FEM analysis before solving.",
        )
    except Exception as exc:
        return _invalid(
            "FreeCAD could not rebind the FEM constraint.",
            native_error=str(exc),
        )
    return {
        "ok": True,
        "domain": "fem_constraint",
        "object": constraint_obj.Name,
        "resolved_references": [
            {"object_name": item["object_name"], "element": item["element"]}
            for item in refs
        ],
        "analysis_recompute_deferred": True,
    }


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _subelement_descriptor(subshape: Any) -> dict[str, Any]:
    shape_type = str(getattr(subshape, "ShapeType", "") or "").lower()
    geometry = None
    if shape_type == "face":
        geometry = getattr(subshape, "Surface", None)
    elif shape_type == "edge":
        geometry = getattr(subshape, "Curve", None)
    class_name = type(geometry).__name__.lower() if geometry is not None else ""
    if "plane" in class_name:
        geometry_type = "plane"
    elif "line" in class_name:
        geometry_type = "line"
    elif "circle" in class_name:
        geometry_type = "circle"
    elif "cylinder" in class_name:
        geometry_type = "cylinder"
    elif shape_type == "vertex":
        geometry_type = "point"
    else:
        geometry_type = class_name or "unknown"
    result = {"element_type": shape_type, "geometry_type": geometry_type}
    if shape_type == "face":
        result["area_mm2"] = float(getattr(subshape, "Area", 0.0))
    elif shape_type == "edge":
        result["length_mm"] = float(getattr(subshape, "Length", 0.0))
    elif shape_type == "vertex":
        point = getattr(subshape, "Point", None)
        if point is not None:
            result["point_mm"] = [float(point.x), float(point.y), float(point.z)]
    return result
