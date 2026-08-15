# SPDX-License-Identifier: LGPL-2.1-or-later

"""Durable assembly-joint rebinding after VibeScript regeneration."""

from __future__ import annotations

from typing import Any

import VibeCADReferenceContracts as reference_contracts
import VibeCADReferenceSelection as reference_selection
from tool_impl.service import domain_runtime


def _resolve_reference(
    service: Any,
    assembly: Any,
    reference: Any,
    parameter_name: str,
) -> dict[str, Any]:
    if not isinstance(reference, dict):
        return _invalid(f"{parameter_name} must be an object.")
    doc = service._active_document()
    component_name = str(reference.get("component_name") or "").strip()
    component = doc.getObject(component_name) if doc is not None and component_name else None
    members = {
        child.Name: child for child in list(getattr(assembly, "Group", []) or [])
    }
    if component is None or component_name not in members:
        return _invalid(
            f"{parameter_name}.component_name must be an exact component child of assembly {assembly.Name}.",
            requested_component=component_name,
            component_candidates=[
                {"name": child.Name, "label": child.Label, "type": child.TypeId}
                for child in members.values()
                if str(child.TypeId) in {"App::Link", "Assembly::AssemblyLink"}
            ],
        )
    selection = reference.get("selection")
    if not isinstance(selection, dict):
        return _invalid(f"{parameter_name}.selection must be an object.")
    mode = str(selection.get("type") or "")
    linked_publication = reference_contracts.published_object(component)
    if linked_publication is not None and mode != "published_interface":
        return _invalid(
            f"{parameter_name} targets a regenerating scripted component. "
            "Select one of its published semantic interfaces instead of a "
            "transient FaceN/EdgeN/VertexN name.",
            parameter=parameter_name,
            component=component_name,
            available_interface_selection_type="published_interface",
        )
    if mode == "published_interface":
        interface_name = str(selection.get("interface_name") or "").strip()
        try:
            interface = reference_contracts.resolve_interface(
                service, component, interface_name
            )
        except reference_contracts.ReferenceContractError as exc:
            return _invalid(str(exc), parameter=parameter_name, **exc.details)
        names = list(interface.get("subelements") or [])
        geometry = list(interface.get("geometry") or [])
        if len(names) > 1 or len(geometry) > 1:
            return _invalid(
                f"{parameter_name} published interface must resolve to one "
                "connector subelement or the component origin.",
                interface=interface_name,
                resolved_subelements=names,
                resolved_geometry=geometry,
            )
        element = names[0] if names else ""
        geometry_item = geometry[0] if geometry else {
            "local_placement": domain_runtime.placement_summary(component),
            "global_placement": domain_runtime.global_placement_summary(component),
        }
        managed_selection = {
            "type": "published_interface",
            "interface_name": interface_name,
            "model_id": interface["model_id"],
            "publication_name": interface["publication_name"],
            "output_key": interface["output_key"],
        }
        geometry_type = (
            geometry_item.get("geometry_type")
            if geometry
            else (
                "component_frame"
                if dict(interface.get("selection") or {}).get("type") == "frame"
                else "component_origin"
            )
        )
        return {
            "ok": True,
            "parameter": parameter_name,
            "component_name": component_name,
            "component": component,
            "selection": managed_selection,
            "element": element,
            "element_type": (
                "face"
                if element.startswith("Face")
                else "edge"
                if element.startswith("Edge")
                else "origin"
            ),
            "geometry_type": geometry_type,
            "geometry": geometry_item,
            "interface_frame": (
                dict(interface["connector_frame"])
                if dict(interface.get("selection") or {}).get("type") == "frame"
                and isinstance(interface.get("connector_frame"), dict)
                else None
            ),
        }
    if mode == "component_origin":
        return {
            "ok": True,
            "parameter": parameter_name,
            "component_name": component_name,
            "component": component,
            "selection": dict(selection),
            "element": "",
            "element_type": "origin",
            "geometry_type": "component_origin",
            "geometry": {
                "local_placement": domain_runtime.placement_summary(component),
                "global_placement": domain_runtime.global_placement_summary(component),
            },
        }
    if mode == "exact_vertex":
        name = str(selection.get("subelement") or "")
        try:
            index = int(name.removeprefix("Vertex"))
        except ValueError:
            index = 0
        vertices = list(getattr(getattr(component, "Shape", None), "Vertexes", []) or [])
        if index < 1 or index > len(vertices):
            return _invalid(
                f"{parameter_name} vertex does not exist on the component.",
                requested_subelement=name,
                available_vertices=[f"Vertex{i}" for i in range(1, len(vertices) + 1)],
            )
        vertex = vertices[index - 1]
        return {
            "ok": True,
            "parameter": parameter_name,
            "component_name": component_name,
            "component": component,
            "selection": dict(selection),
            "element": name,
            "element_type": "vertex",
            "geometry_type": "point",
            "geometry": {"point": domain_runtime.vector_values(vertex.Point)},
        }
    selection_state = reference_selection.resolve_selection(
        service,
        component,
        selection,
        allow_all_edges=False,
        face_only=False,
    )
    if not selection_state.get("ok"):
        return _invalid(
            selection_state.get("error") or f"{parameter_name} selection failed.",
            parameter=parameter_name,
            selection_failure=selection_state,
        )
    names = list(selection_state.get("subelements") or [])
    geometry = list(selection_state.get("resolved_geometry") or [])
    if len(names) != 1 or len(geometry) != 1:
        return _invalid(
            f"{parameter_name} must resolve to exactly one subelement.",
            selection=selection_state,
        )
    name = names[0]
    return {
        "ok": True,
        "parameter": parameter_name,
        "component_name": component_name,
        "component": component,
        "selection": dict(selection),
        "element": name,
        "element_type": "face" if name.startswith("Face") else "edge",
        "geometry_type": geometry[0].get("geometry_type"),
        "geometry": geometry[0],
    }


def rebind_scripted_reference(
    service: Any,
    joint_obj: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    doc = service._active_document()
    assembly_name = str(contract.get("assembly_name") or "")
    assembly = doc.getObject(assembly_name) if doc is not None else None
    if assembly is None:
        return _invalid(
            "The assembly recorded by the joint reference contract no longer exists.",
            assembly_name=assembly_name,
        )
    raw_references = contract.get("references")
    if not isinstance(raw_references, list) or len(raw_references) != 2:
        return _invalid("The assembly joint reference contract must contain two references.")
    resolved: list[dict[str, Any]] = []
    for index, raw in enumerate(raw_references, start=1):
        item = _resolve_reference(service, assembly, raw, f"reference{index}")
        if not item.get("ok"):
            return item
        resolved.append(item)
    refs = []
    for item in resolved:
        component = doc.getObject(item["component_name"])
        if component is None:
            return _invalid("A joint component disappeared during rebinding.")
        element = item["element"]
        refs.append([component, [element, element]])
    try:
        import FreeCAD as App

        for index, item in enumerate(resolved, start=1):
            frame = item.get("interface_frame")
            setattr(
                joint_obj,
                f"Offset{index}",
                (
                    reference_contracts.connector_frame_placement(frame)
                    if frame is not None
                    else App.Placement()
                ),
            )
        joint_obj.Proxy.setJointConnectors(joint_obj, refs)
        joint_obj.touch()
        assembly.touch()
        reference_contracts.mark_stale(
            assembly,
            str(contract.get("source_revision") or ""),
            "A referenced scripted model changed; solve and verify this assembly.",
        )
    except Exception as exc:
        return _invalid(
            "FreeCAD could not rebind the assembly joint.",
            native_error=str(exc),
        )
    return {
        "ok": True,
        "domain": "assembly_joint",
        "object": joint_obj.Name,
        "assembly": assembly.Name,
        "resolved_references": [
            {
                "component_name": item["component_name"],
                "selection": item["selection"],
                "element": item["element"],
            }
            for item in resolved
        ],
        "solver_deferred": True,
    }


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
