# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Part boolean in the consolidated modeling surface."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


TOOL_SPEC = {
    "name": "part.boolean",
    "description": (
        "Create one native Part boolean (union, cut, or intersection) from exact "
        "named shaped objects. The inputs become children of the result and are "
        "hidden, not deleted; the boolean stays parametric. Do not use this on "
        "features in the active Part Design Body; the result remains an editable "
        "Part boolean in that Body."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "operation": {
                "type": "string",
                "enum": ["union", "cut", "intersection"],
                "description": (
                    "union fuses all inputs; cut subtracts the tool objects from the "
                    "base object; intersection keeps only shared volume."
                ),
            },
            "base_object_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the base object. For cut, material is "
                    "removed from this object."
                ),
            },
            "tool_object_names": {
                "type": "array",
                "items": {"type": "string"},
                "minItems": 1,
                "description": (
                    "Exact internal names of the other operand objects. For cut, "
                    "these are subtracted from the base."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the boolean result.",
            },
            "refine": {
                "type": "boolean",
                "description": "Remove redundant edges from the result; usually true.",
            },
        },
        "required": [
            "operation",
            "base_object_name",
            "tool_object_names",
            "label",
            "refine",
        ],
        "additionalProperties": False,
    },
}

_NATIVE = {
    "union": ("Part::MultiFuse", "Fusion"),
    "cut": ("Part::Cut", "Cut"),
    "intersection": ("Part::MultiCommon", "Common"),
}


def run(
    service: Any,
    operation: str,
    base_object_name: str,
    tool_object_names: list[str],
    label: str,
    refine: bool,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    native = _NATIVE.get(str(operation or ""))
    if native is None:
        return _invalid("operation must be union, cut, or intersection.")
    base_name = str(base_object_name or "").strip()
    if not base_name:
        return _invalid("base_object_name is required.")
    if not isinstance(tool_object_names, list) or not tool_object_names:
        return _invalid("tool_object_names must contain at least one exact name.")
    tool_names = [str(name or "").strip() for name in tool_object_names]
    if any(not name for name in tool_names):
        return _invalid("tool_object_names cannot contain empty names.")
    if len(set(tool_names)) != len(tool_names):
        return _invalid("tool_object_names cannot contain duplicates.")
    if base_name in tool_names:
        return _invalid("The base object cannot also be a tool object.")
    if str(operation) == "cut" and len(tool_names) != 1:
        return _invalid(
            "part.boolean cut accepts exactly one tool object. Create an explicit union first when multiple cutters are intentional; no hidden helper feature will be created.",
            requested_tool_count=len(tool_names),
            required_tool_count=1,
        )
    operand_state = _resolve_operands(service, [base_name, *tool_names])
    if not operand_state.get("ok"):
        return operand_state
    overlap = _boolean_preflight(
        str(operation),
        operand_state["objects"][0],
        operand_state["objects"][1:],
    )
    if not overlap.get("ok"):
        return _invalid(
            "The requested Boolean operands do not satisfy the native overlap/connectivity precondition; no feature was created.",
            boolean_preflight=overlap,
            operands=operand_state["facts"],
        )
    type_id, base_object_name_hint = native
    relationship_before = {
        obj.Name: _object_relationships(obj) for obj in operand_state["objects"]
    }
    visibility_before = {
        obj.Name: domain_runtime.view_visibility_summary(obj)
        for obj in operand_state["objects"]
    }

    def create() -> dict[str, Any]:
        import FreeCAD as App

        doc = App.ActiveDocument
        if doc is None:
            raise RuntimeError("No active document.")
        base = doc.getObject(base_name)
        tools = [doc.getObject(name) for name in tool_names]
        if base is None or any(tool is None for tool in tools):
            raise RuntimeError("A boolean operand no longer exists.")
        boolean = doc.addObject(type_id, base_object_name_hint)
        boolean.Label = clean_label
        if type_id == "Part::Cut":
            boolean.Base = base
            boolean.Tool = tools[0]
        else:
            boolean.Shapes = [base, *tools]
        if hasattr(boolean, "Refine"):
            boolean.Refine = bool(refine)
        domain_runtime.adopt_part_result(boolean)
        doc.recompute()
        operands = [base, *tools]
        for operand in operands:
            view = getattr(operand, "ViewObject", None)
            if view is not None and hasattr(view, "Visibility"):
                view.Visibility = False
        return {
            "document": doc.Name,
            "feature": boolean.Name,
            "feature_label": boolean.Label,
            "feature_type": boolean.TypeId,
            "operation": operation,
            "base_object": base_name,
            "tool_objects": tool_names,
            "boolean_preflight": overlap,
            "operand_facts": operand_state["facts"],
            "operand_relationships_before": relationship_before,
            "operand_relationships_after": {
                operand.Name: _object_relationships(operand) for operand in operands
            },
            "operand_visibility_before": visibility_before,
            "operand_visibility_after": {
                operand.Name: domain_runtime.view_visibility_summary(operand)
                for operand in operands
            },
            "native_links": (
                {
                    "base": getattr(getattr(boolean, "Base", None), "Name", None),
                    "tool": getattr(getattr(boolean, "Tool", None), "Name", None),
                }
                if type_id == "Part::Cut"
                else {
                    "shapes": [
                        getattr(item, "Name", None)
                        for item in list(getattr(boolean, "Shapes", []) or [])
                    ]
                }
            ),
            "shape": domain_runtime.shape_summary(boolean),
            "feature_state": domain_runtime.feature_state_summary(boolean),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        visibility = result.get("operand_visibility_after") or {}
        hidden_checks = [
            not state.get("supported") or state.get("visible") is False
            for state in visibility.values()
            if isinstance(state, dict)
        ]
        result_shape = result.get("shape") or {}
        checks = [
            {
                "name": "result_is_valid_solid",
                "ok": bool(result_shape.get("available"))
                and int(result_shape.get("solids", 0)) > 0
                and not bool((result.get("feature_state") or {}).get("marked_invalid"))
                and (result.get("feature_state") or {}).get("shape_valid") is True,
                "actual": result_shape,
            },
            {
                "name": "operand_visibility",
                "ok": all(hidden_checks),
                "actual": visibility,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create Part boolean {operation}: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(
        transaction, operation=f"boolean_{operation}"
    )


def _resolve_operands(service: Any, names: list[str]) -> dict[str, Any]:
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    objects = []
    facts = []
    for name in names:
        obj = doc.getObject(name)
        if obj is None:
            return _invalid(
                f"Object not found by exact internal name: {name}",
                requested=name,
                candidates=[
                    {"name": item.Name, "label": item.Label, "type": item.TypeId}
                    for item in list(getattr(doc, "Objects", []) or [])
                    if getattr(item, "Shape", None) is not None
                ][:40],
            )
        shape = getattr(obj, "Shape", None)
        if shape is None or shape.isNull():
            return _invalid(f"Object has no shape geometry: {name}")
        health = domain_runtime.shape_health(obj)
        if not health.get("valid_non_null"):
            return _invalid(
                f"Object {name} does not have a valid native shape.",
                operand=health,
            )
        if int(len(getattr(shape, "Solids", []) or [])) < 1:
            return _invalid(
                f"Object {name} contains no solid; Part booleans need solid operands.",
                shape=domain_runtime.shape_summary(obj),
            )
        objects.append(obj)
        facts.append(health)
    return {"ok": True, "objects": objects, "facts": facts}


def _boolean_preflight(operation: str, base: Any, tools: list[Any]) -> dict[str, Any]:
    operands = [base, *tools]
    pairwise = []
    adjacency = {index: set() for index in range(len(operands))}
    for first in range(len(operands)):
        for second in range(first + 1, len(operands)):
            try:
                common = operands[first].Shape.common(operands[second].Shape)
                facts = {
                    "first_index": first,
                    "second_index": second,
                    "first_object": operands[first].Name,
                    "second_object": operands[second].Name,
                    "common_volume_mm3": float(getattr(common, "Volume", 0.0) or 0.0),
                    "common_faces": len(list(getattr(common, "Faces", []) or [])),
                    "common_edges": len(list(getattr(common, "Edges", []) or [])),
                    "native_stage": "BRepAlgoAPI_Common",
                }
                intersects = bool(
                    facts["common_volume_mm3"] > 1.0e-9
                    or facts["common_faces"] > 0
                    or facts["common_edges"] > 0
                )
                facts["intersects"] = intersects
                if intersects:
                    adjacency[first].add(second)
                    adjacency[second].add(first)
            except Exception as exc:
                facts = {
                    "first_index": first,
                    "second_index": second,
                    "first_object": operands[first].Name,
                    "second_object": operands[second].Name,
                    "intersects": None,
                    "native_stage": "BRepAlgoAPI_Common",
                    "native_error": str(exc),
                }
            pairwise.append(facts)
    if any(item.get("intersects") is None for item in pairwise):
        return {
            "ok": False,
            "operation": operation,
            "failure": "native_overlap_check_failed",
            "pairwise": pairwise,
        }
    if operation == "union":
        visited = {0}
        frontier = [0]
        while frontier:
            current = frontier.pop()
            for neighbor in adjacency[current] - visited:
                visited.add(neighbor)
                frontier.append(neighbor)
        ok = len(visited) == len(operands)
        failure = None if ok else "operands_are_not_overlap_connected"
    elif operation == "cut":
        ok = bool(pairwise and pairwise[0].get("intersects"))
        failure = None if ok else "tool_does_not_intersect_base"
    else:
        try:
            common = base.Shape
            for tool in tools:
                common = common.common(tool.Shape)
            ok = bool(
                float(getattr(common, "Volume", 0.0) or 0.0) > 1.0e-9
                or len(list(getattr(common, "Faces", []) or [])) > 0
            )
            failure = None if ok else "all_operands_have_no_common_region"
        except Exception as exc:
            return {
                "ok": False,
                "operation": operation,
                "failure": "native_multi_common_check_failed",
                "native_stage": "BRepAlgoAPI_Common",
                "native_error": str(exc),
                "pairwise": pairwise,
            }
    return {
        "ok": ok,
        "operation": operation,
        "failure": failure,
        "pairwise": pairwise,
    }


def _object_relationships(obj: Any) -> dict[str, Any]:
    return {
        "in_list": [item.Name for item in list(getattr(obj, "InList", []) or [])],
        "out_list": [item.Name for item in list(getattr(obj, "OutList", []) or [])],
    }


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
