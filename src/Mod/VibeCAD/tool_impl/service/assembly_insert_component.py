# SPDX-License-Identifier: LGPL-2.1-or-later

"""Insert one existing object into an assembly as a linked component."""

from __future__ import annotations

from typing import Any

import VibeCADScriptedPublication as scripted_publication
from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "assembly.insert_component",
    "description": (
        "Insert one existing named object (Part feature, PartDesign Body, or "
        "primitive) into an exact assembly as a new linked component "
        "(App::Link). The source object is not moved or modified; the link is "
        "an independent occurrence that joints and the solver position. Insert "
        "the same source repeatedly for multiple occurrences."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "AssemblyWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "assembly_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the target assembly from "
                    "assembly.list_structure."
                ),
            },
            "source_object_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the existing object to link into "
                    "the assembly."
                ),
            },
            "label": {
                "type": "string",
                "description": (
                    "Visible label for the new component occurrence, e.g. 'Bracket_1'."
                ),
            },
            "local_position": domain_runtime.vector_schema(
                "Initial position in the target assembly's local coordinate system in mm; the solver "
                "moves unfixed components when joints are solved. Use "
                "{x:0,y:0,z:0} when unsure."
            ),
        },
        "required": ["assembly_name", "source_object_name", "label", "local_position"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    assembly_name: str,
    source_object_name: str,
    label: str,
    local_position: dict[str, Any],
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    assembly = _find_assembly(service, assembly_name)
    if assembly is None:
        return _invalid(
            f"Assembly not found by exact internal name: {assembly_name}. "
            "Call assembly.list_structure for exact names."
        )
    source_name = str(source_object_name or "").strip()
    source = doc.getObject(source_name) if source_name else None
    if source is None:
        return _invalid(
            f"Source object not found by exact internal name: {source_object_name}",
            candidates=_component_candidates(service, doc),
        )
    if source is assembly:
        return _invalid("An assembly cannot be inserted into itself.")
    source_validation = _validate_component_source(service, source)
    if not source_validation.get("ok"):
        return source_validation
    source_state_before = _source_container_state(service, source)

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target_assembly = active.getObject(assembly.Name)
        base = active.getObject(source_name)
        if target_assembly is None or base is None:
            raise RuntimeError("The assembly or source object no longer exists.")
        link_type = (
            "Assembly::AssemblyLink"
            if base.isDerivedFrom("Assembly::AssemblyObject")
            else "App::Link"
        )
        component = target_assembly.newObject(link_type, base.Name)
        if component is None:
            raise RuntimeError("FreeCAD did not create the component link.")
        component.LinkedObject = base
        component.Label = clean_label
        component.Placement = App.Placement(
            domain_runtime.parse_vector(local_position), App.Rotation()
        )
        active.recompute()
        return {
            "document": active.Name,
            "assembly": target_assembly.Name,
            "component": component.Name,
            "component_label": component.Label,
            "component_type": component.TypeId,
            "linked_object": base.Name,
            "native_link_target": getattr(getattr(component, "LinkedObject", None), "Name", None),
            "assembly_membership": [
                child.Name for child in list(getattr(target_assembly, "Group", []) or [])
            ],
            "assembly_local_placement": domain_runtime.placement_summary(component),
            "verified_global_placement": domain_runtime.global_placement_summary(component),
            "source_state_before": source_state_before,
            "source_state_after": _source_container_state(service, base),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        checks = [
            {
                "name": "link_target",
                "ok": result.get("native_link_target") == source_name,
                "expected": source_name,
                "actual": result.get("native_link_target"),
            },
            {
                "name": "assembly_membership",
                "ok": result.get("component") in list(result.get("assembly_membership") or []),
                "actual": result.get("assembly_membership"),
            },
            {
                "name": "source_container_unchanged",
                "ok": _container_state_unchanged(
                    result.get("source_state_before") or {},
                    result.get("source_state_after") or {},
                ),
                "before": result.get("source_state_before"),
                "after": result.get("source_state_after"),
            },
            {
                "name": "global_placement_available",
                "ok": bool((result.get("verified_global_placement") or {}).get("supported"))
                and (result.get("verified_global_placement") or {}).get("placement") is not None,
                "actual": result.get("verified_global_placement"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Insert assembly component: {clean_label}",
        create,
        verifier=verify,
    )
    mutation = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "insert_component", "mutation": mutation},
        next_action=(
            "Ground the first component with assembly.ground_component, then "
            "relate components with assembly.create_joint using the returned "
            "exact component name."
        ),
    )


def _find_assembly(service: Any, assembly_name: str) -> Any:
    clean = str(assembly_name or "").strip()
    if not clean:
        return None
    for assembly in service._assembly_objects():
        if assembly.Name == clean:
            return assembly
    return None


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _validate_component_source(service: Any, source: Any) -> dict[str, Any]:
    type_id = str(getattr(source, "TypeId", ""))
    if type_id == "App::Link" and scripted_publication.is_publication(source):
        try:
            root = scripted_publication.model_root_for(source)
            output_key = str(
                getattr(source, scripted_publication.PROP_OUTPUT_KEY, "") or ""
            )
            registered = scripted_publication.model_publications(root).get(output_key)
        except scripted_publication.PublicationError as exc:
            return _invalid(
                "The scripted publication link is not structurally valid.",
                requested_object={
                    "name": source.Name,
                    "label": source.Label,
                    "type": type_id,
                },
                publication_error=str(exc),
                publication_details=exc.details,
            )
        if registered is not source:
            return _invalid(
                "The scripted publication link is not registered to its model root.",
                requested_object={
                    "name": source.Name,
                    "label": source.Label,
                    "type": type_id,
                },
            )
        health = domain_runtime.shape_health(source)
        if health.get("valid_non_null") and int(
            (health.get("shape") or {}).get("solids", 0)
        ) > 0:
            return {
                "ok": True,
                "component_type": "VibeScript publication",
                "shape": health,
            }
        return _invalid(
            "A scripted publication component must contain at least one valid solid.",
            requested_object=health,
        )
    if type_id == "PartDesign::Body" or source.isDerivedFrom("Assembly::AssemblyObject"):
        return {"ok": True, "component_type": type_id}
    owner = service._partdesign_body_for_feature(source)
    if owner is not None:
        return _invalid(
            "A PartDesign feature inside a Body is not a standalone assembly component.",
            requested_object={"name": source.Name, "label": source.Label, "type": type_id},
            owning_body={"name": owner.Name, "label": owner.Label, "type": owner.TypeId},
            correction={"source_object_name": owner.Name},
        )
    if source.isDerivedFrom("App::Part"):
        return {"ok": True, "component_type": type_id}
    shape = getattr(source, "Shape", None)
    if source.isDerivedFrom("Part::Feature") and shape is not None and not bool(shape.isNull()):
        health = domain_runtime.shape_health(source)
        if health.get("valid_non_null") and int((health.get("shape") or {}).get("solids", 0)) > 0:
            return {"ok": True, "component_type": type_id, "shape": health}
        return _invalid(
            "A standalone Part component must contain at least one valid solid.",
            requested_object=health,
        )
    return _invalid(
        "The source is not an explicit standalone component type.",
        requested_object={"name": source.Name, "label": source.Label, "type": type_id},
        allowed_types=[
            "PartDesign::Body",
            "App::Part",
            "Assembly::AssemblyObject",
            "registered solid VibeScript publication",
            "standalone solid Part::Feature",
        ],
    )


def _source_container_state(service: Any, source: Any) -> dict[str, Any]:
    owner = service._partdesign_body_for_feature(source)
    in_list = list(getattr(source, "InList", []) or [])
    return {
        "name": source.Name,
        "dependency_in_list": [obj.Name for obj in in_list],
        "container_memberships": [
            obj.Name
            for obj in in_list
            if hasattr(obj, "Group") and source in list(getattr(obj, "Group", []) or [])
        ],
        "out_list": [obj.Name for obj in list(getattr(source, "OutList", []) or [])],
        "group": [obj.Name for obj in list(getattr(source, "Group", []) or [])]
        if hasattr(source, "Group")
        else None,
        "tip": getattr(getattr(source, "Tip", None), "Name", None)
        if hasattr(source, "Tip")
        else None,
        "owning_body": getattr(owner, "Name", None),
        "owning_body_group": [obj.Name for obj in list(getattr(owner, "Group", []) or [])]
        if owner is not None
        else None,
        "owning_body_tip": getattr(getattr(owner, "Tip", None), "Name", None)
        if owner is not None
        else None,
    }


def _container_state_unchanged(before: dict[str, Any], after: dict[str, Any]) -> bool:
    keys = (
        "container_memberships",
        "group",
        "tip",
        "owning_body",
        "owning_body_group",
        "owning_body_tip",
    )
    return all(before.get(key) == after.get(key) for key in keys)


def _component_candidates(service: Any, doc: Any) -> list[dict[str, Any]]:
    candidates = []
    for obj in list(getattr(doc, "Objects", []) or []):
        state = _validate_component_source(service, obj)
        if state.get("ok"):
            candidates.append({"name": obj.Name, "label": obj.Label, "type": obj.TypeId})
    return candidates[:40]
