# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native Part fillet on exact named edges."""

from __future__ import annotations

import json
from typing import Any

import VibeCADReferenceContracts as reference_contracts
import VibeCADScriptedPublication as publication
from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime, partdesign_dressup_feature


def edge_selection_schema() -> dict[str, Any]:
    native = partdesign_dressup_feature.selection_schema(
        allow_all_edges=True,
        edge_only=True,
    )
    return {
        "oneOf": [
            reference_contracts.interface_selection_schema(),
            *list(native["oneOf"]),
        ],
        "description": (
            "Select edges by a count-guarded geometric query. On a direct VibeScript "
            "published output, use a declared published_interface. On a native Part "
            "feature derived from scripted geometry, use a query so the selection can "
            "be resolved again after regeneration."
        ),
    }


TOOL_SPEC = {
    "name": "part.fillet",
    "description": (
        "Create one native Part fillet that rounds count-guarded geometric edges of one shaped "
        "object. Finishing operation; apply after the primary form is complete. "
        "Use count-guarded geometric selection; scripted outputs require a declared "
        "published interface. "
        "The source object becomes a hidden child of the fillet result."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "PartDesignWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "object_name": {
                "type": "string",
                "description": "Exact internal name of the object whose edges are filleted.",
            },
            "selection": edge_selection_schema(),
            "radius_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Fillet radius in mm; must be smaller than the adjacent faces "
                    "can absorb or the fillet fails."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the fillet result.",
            },
        },
        "required": ["object_name", "selection", "radius_mm", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    object_name: str,
    selection: dict[str, Any],
    radius_mm: float,
    label: str,
) -> dict[str, Any]:
    return run_edge_finish(
        service,
        object_name=object_name,
        selection=selection,
        size_mm=radius_mm,
        label=label,
        native_type="Part::Fillet",
        operation="fillet",
    )


def run_edge_finish(
    service: Any,
    *,
    object_name: str,
    selection: dict[str, Any],
    size_mm: float,
    label: str,
    native_type: str,
    operation: str,
) -> dict[str, Any]:
    """Shared implementation for Part fillet and chamfer."""
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    size = float(size_mm)
    if size <= 0:
        return _invalid(f"{operation} size must be positive.")
    clean_name = str(object_name or "").strip()
    doc = service._active_document()
    obj = doc.getObject(clean_name) if doc is not None and clean_name else None
    if obj is None:
        return _invalid(f"Object not found by exact internal name: {object_name}")
    shape = getattr(obj, "Shape", None)
    if shape is None or shape.isNull():
        return _invalid(f"Object has no shape geometry: {clean_name}")
    selection_state = _resolve_edge_selection(service, obj, selection)
    if not selection_state.get("ok"):
        return selection_state
    names = list(selection_state["subelements"])
    if selection_state.get("use_all_edges"):
        names = [item["name"] for item in selection_state["resolved_geometry"]]
    indexes = [int(name.removeprefix("Edge")) for name in names]
    source_health = domain_runtime.shape_health(obj)
    visibility_before = domain_runtime.view_visibility_summary(obj)

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        base = active.getObject(clean_name)
        if base is None:
            raise RuntimeError("The object no longer exists.")
        feature = active.addObject(native_type, operation.capitalize())
        feature.Label = clean_label
        feature.Base = base
        feature.Edges = [(index, size, size) for index in indexes]
        if selection_state.get("managed"):
            reference_contracts.set_contract(
                feature,
                "part_edge_finish",
                {
                    "operation": operation,
                    "source_object_name": base.Name,
                    "selection": selection_state["contract_selection"],
                    "dependencies": reference_contracts.dependency_records(
                        selection_state["model_dependencies"]
                    ),
                    "size_mm": size,
                },
            )
        domain_runtime.adopt_part_result(feature)
        active.recompute()
        view = getattr(base, "ViewObject", None)
        if view is not None and hasattr(view, "Visibility"):
            view.Visibility = False
        return {
            "document": active.Name,
            "feature": feature.Name,
            "feature_label": feature.Label,
            "feature_type": feature.TypeId,
            "source_object": base.Name,
            "selection_request": dict(selection),
            "resolved_edges": selection_state["resolved_geometry"],
            "native_edge_indices": indexes,
            "size_mm": size,
            "source_shape": source_health,
            "source_visibility_before": visibility_before,
            "source_visibility_after": domain_runtime.view_visibility_summary(base),
            "native_edge_property": [list(item) for item in list(feature.Edges or [])],
            "managed_reference_contract": reference_contracts.read_contract(feature),
            "shape": domain_runtime.shape_summary(feature),
            "feature_state": domain_runtime.feature_state_summary(feature),
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        visibility = result.get("source_visibility_after") or {}
        feature_state = result.get("feature_state") or {}
        result_shape = result.get("shape") or {}
        checks = [
            {
                "name": "valid_dressup_shape",
                "ok": bool(result_shape.get("available"))
                and int(result_shape.get("solids", 0)) > 0
                and feature_state.get("shape_valid") is True
                and not feature_state.get("marked_invalid"),
                "actual": result_shape,
            },
            {
                "name": "resolved_edge_count",
                "ok": len(result.get("resolved_edges") or []) == len(indexes),
                "expected": len(indexes),
                "actual": len(result.get("resolved_edges") or []),
            },
            {
                "name": "source_visibility",
                "ok": not visibility.get("supported") or visibility.get("visible") is False,
                "actual": visibility,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create Part {operation}: {clean_label}",
        create,
        verifier=verify,
    )
    return domain_runtime.part_feature_result(transaction, operation=operation)


def rebind_scripted_reference(
    service: Any,
    feature: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Re-resolve a managed Part fillet/chamfer after source regeneration."""

    prepared = prepare_scripted_rebind(service, feature, contract)
    if not prepared.get("ok"):
        return prepared
    selection_state = prepared.get("resolved_selection")
    if not isinstance(selection_state, dict):
        selection_state = _resolve_edge_selection(
            service,
            prepared["source"],
            prepared["selection"],
        )
    return apply_scripted_rebind(feature, prepared, selection_state)


def prepare_scripted_rebind(
    service: Any,
    feature: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    """Capture a managed edge rebind without traversing source topology."""

    operation = str(contract.get("operation") or "")
    expected_type = {
        "fillet": "Part::Fillet",
        "chamfer": "Part::Chamfer",
    }.get(operation)
    if expected_type is None or str(getattr(feature, "TypeId", "")) != expected_type:
        return _invalid(
            "The managed Part edge-finish contract does not match its native feature.",
            operation=operation,
            expected_type=expected_type,
            actual_type=str(getattr(feature, "TypeId", "") or ""),
        )
    doc = service._active_document()
    source_name = str(contract.get("source_object_name") or "")
    source = doc.getObject(source_name) if doc is not None and source_name else None
    if source is None:
        return _invalid(
            "The source object recorded by this Part edge finish no longer exists.",
            source_object_name=source_name,
        )
    selection = contract.get("selection")
    if not isinstance(selection, dict):
        return _invalid("The managed Part edge-finish selection is missing or invalid.")
    expected_dependencies = reference_contracts.referenced_model_ids(contract)
    actual_dependencies = set(
        reference_contracts.scripted_model_dependencies(source)
    )
    if not expected_dependencies.issubset(actual_dependencies):
        return _invalid(
            "The Part edge finish is no longer connected to every scripted model "
            "recorded by its reference contract.",
            expected_model_ids=sorted(expected_dependencies),
            actual_model_ids=sorted(actual_dependencies),
        )
    try:
        size = float(contract.get("size_mm"))
    except (TypeError, ValueError):
        return _invalid("The managed Part edge-finish size is invalid.")
    prepared = {
        "ok": True,
        "feature_name": feature.Name,
        "operation": operation,
        "source": source,
        "selection": dict(selection),
        "size_mm": size,
    }
    if selection.get("type") == "published_interface":
        prepared["resolved_selection"] = _resolve_edge_selection(
            service, source, selection
        )
    return prepared


def apply_scripted_rebind(
    feature: Any,
    prepared: dict[str, Any],
    selection_state: dict[str, Any],
) -> dict[str, Any]:
    """Apply one already resolved edge selection without recomputing."""

    if not selection_state.get("ok"):
        return _invalid(
            "The managed Part edge selection no longer resolves on the regenerated source.",
            selection_failure=selection_state,
        )
    source = prepared["source"]
    operation = str(prepared.get("operation") or "")
    names = list(selection_state.get("subelements") or [])
    if selection_state.get("use_all_edges"):
        names = [
            str(item.get("name") or "")
            for item in list(selection_state.get("resolved_geometry") or [])
        ]
    if not names or any(not name.startswith("Edge") for name in names):
        return _invalid(
            "The managed Part edge selection must resolve to one or more edges.",
            resolved_subelements=names,
        )
    size = float(prepared["size_mm"])
    indexes = [int(name.removeprefix("Edge")) for name in names]
    try:
        feature.Base = source
        feature.Edges = [(index, size, size) for index in indexes]
        feature.touch()
    except Exception as exc:
        return _invalid(
            "FreeCAD could not stage the Part edge finish for asynchronous recompute.",
            native_error=str(exc),
            resolved_edges=names,
        )
    edge_links = getattr(feature, "EdgeLinks", None)
    actual_source = edge_links[0] if isinstance(edge_links, (tuple, list)) and edge_links else None
    actual_names = (
        [str(item) for item in list(edge_links[1] or [])]
        if isinstance(edge_links, (tuple, list)) and len(edge_links) > 1
        else []
    )
    if actual_source is not source or actual_names != names:
        return _invalid(
            "FreeCAD did not retain the exact staged Part edge rebind.",
            requested_source=source.Name,
            actual_source=getattr(actual_source, "Name", None),
            requested_edges=names,
            actual_edges=actual_names,
        )
    return {
        "ok": True,
        "domain": "part_edge_finish",
        "object": feature.Name,
        "operation": operation,
        "source_object": source.Name,
        "resolved_edges": names,
        "asynchronous_recompute_required": True,
    }


def _resolve_edge_selection(
    service: Any,
    obj: Any,
    selection: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(selection, dict):
        return _invalid("selection must be an object.")
    mode = str(selection.get("type") or "")
    direct_publication = reference_contracts.published_object(obj)
    dependencies = reference_contracts.scripted_model_dependencies(obj)
    if direct_publication is not None:
        if mode != "published_interface":
            return _invalid(
                "A regenerating scripted output requires a declared published_interface "
                "for Part fillet or chamfer edges; raw queries and EdgeN names are not "
                "a durable cross-workbench contract.",
                available_interfaces=_published_interface_names(service, direct_publication),
            )
        interface_name = str(selection.get("interface_name") or "").strip()
        try:
            interface = reference_contracts.resolve_interface(
                service,
                obj,
                interface_name,
            )
        except reference_contracts.ReferenceContractError as exc:
            return _invalid(str(exc), reference_details=exc.details)
        names = list(interface.get("subelements") or [])
        geometry = list(interface.get("geometry") or [])
        if not names or len(names) != len(geometry) or any(
            not name.startswith("Edge") for name in names
        ):
            return _invalid(
                f"Published interface {interface_name!r} must resolve only to one "
                "or more edges.",
                resolved_subelements=names,
                resolved_geometry=geometry,
            )
        return {
            "ok": True,
            "mode": "published_interface",
            "subelements": names,
            "resolved_geometry": geometry,
            "use_all_edges": False,
            "request": dict(selection),
            "managed": True,
            "model_dependencies": sorted(
                set(dependencies) | {str(interface["model_id"])}
            ),
            "contract_selection": {
                "type": "published_interface",
                "interface_name": interface_name,
                "model_id": interface["model_id"],
                "publication_name": interface["publication_name"],
                "output_key": interface["output_key"],
            },
        }
    if dependencies and mode != "query":
        return _invalid(
            "A Part feature derived from scripted geometry requires a count-guarded "
            "geometric query. Exact EdgeN and all_edges selections cannot be rebound "
            "safely after upstream regeneration.",
            scripted_model_ids=dependencies,
        )
    if mode == "published_interface":
        return _invalid(
            "published_interface can only be used directly on a VibeCAD published "
            "output or an App::Link to one. Use a count-guarded query on this derived "
            "Part feature instead."
        )
    state = partdesign_dressup_feature.resolve_selection(
        service,
        obj,
        selection,
        allow_all_edges=True,
        face_only=False,
        edge_only=True,
    )
    if not state.get("ok"):
        return state
    state["managed"] = bool(dependencies)
    state["model_dependencies"] = dependencies
    state["contract_selection"] = dict(selection)
    return state


def _published_interface_names(service: Any, published: Any) -> list[str]:
    model_id = str(
        getattr(published, publication.PROP_MODEL_ID, "") or ""
    )
    doc = service._active_document()
    for obj in list(getattr(doc, "Objects", []) or []):
        if (
            publication.role_of(obj) == publication.ROLE_MODEL
            and str(
                getattr(obj, publication.PROP_MODEL_ID, "") or ""
            )
            == model_id
        ):
            try:
                interfaces = json.loads(
                    str(
                        getattr(
                            obj,
                            publication.PROP_INTERFACES,
                            "{}",
                        )
                        or "{}"
                    )
                )
            except (TypeError, ValueError):
                return []
            return sorted(str(name) for name in interfaces)
    return []


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
