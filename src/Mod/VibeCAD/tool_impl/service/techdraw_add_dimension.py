# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native TechDraw dimension on exact projected view elements."""

from __future__ import annotations

import math
from typing import Any

from VibeCADTransactions import run_freecad_transaction
import VibeCADReferenceContracts as reference_contracts

from . import domain_runtime
from .techdraw_add_view import _projected_element_inventory


_NATIVE_TYPES = {
    "length": "Distance",
    "horizontal_length": "DistanceX",
    "vertical_length": "DistanceY",
    "radius": "Radius",
    "diameter": "Diameter",
    "angle": "Angle",
    "angle_3_point": "Angle3Pt",
}

_REFERENCE_CONTRACTS = {
    "length": (
        "one straight projected edge, two projected vertices, or two parallel "
        "straight projected edges"
    ),
    "horizontal_length": "one straight projected edge or two projected vertices",
    "vertical_length": "one straight projected edge or two projected vertices",
    "radius": "exactly one projected Circle or ArcOfCircle edge",
    "diameter": "exactly one projected Circle or ArcOfCircle edge",
    "angle": "exactly two non-parallel straight projected edges",
    "angle_3_point": (
        "exactly three distinct, non-collinear projected vertices in end-apex-end order"
    ),
}


def _references_schema(
    description: str, *, min_items: int, max_items: int
) -> dict[str, Any]:
    return {
        "type": "array",
        "items": {
            "oneOf": [
                {
                    "type": "string",
                    "pattern": "^(Edge|Vertex)[0-9]+$",
                    "description": (
                        "Exact projected element name for a non-scripted view, "
                        "e.g. 'Edge0' or 'Vertex3'."
                    ),
                },
                reference_contracts.interface_selection_schema(),
            ]
        },
        "minItems": min_items,
        "maxItems": max_items,
        "description": description,
    }


def _variant(
    kind: str, kind_description: str, references: dict[str, Any]
) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": {
            "type": {"const": kind, "description": kind_description},
            "references": references,
        },
        "required": ["type", "references"],
        "additionalProperties": False,
    }


TOOL_SPEC = {
    "name": "techdraw.add_dimension",
    "description": (
        "Create one native TechDraw dimension measuring exact projected "
        "elements of one view. Projected elements are named Edge0, Vertex1, "
        "... within the view (indices start at 0 and differ from the 3D "
        "model's names). The dimension stays live: it re-measures when the "
        "model changes. The result reports the measured value in mm or "
        "degrees."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "TechDrawWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "page_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the drawing page from core.inspect scope='domain'."
                ),
            },
            "view_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the view that owns the referenced "
                    "elements, from core.inspect scope='domain'."
                ),
            },
            "dimension": {
                "description": "What to measure; choose exactly one variant.",
                "oneOf": [
                    _variant(
                        "length",
                        "True length between the references.",
                        _references_schema(
                            "One straight edge, or two vertices, or two "
                            "parallel edges.",
                            min_items=1,
                            max_items=2,
                        ),
                    ),
                    _variant(
                        "horizontal_length",
                        "Length projected onto the page's horizontal axis.",
                        _references_schema(
                            "One straight edge, or two vertices.",
                            min_items=1,
                            max_items=2,
                        ),
                    ),
                    _variant(
                        "vertical_length",
                        "Length projected onto the page's vertical axis.",
                        _references_schema(
                            "One straight edge, or two vertices.",
                            min_items=1,
                            max_items=2,
                        ),
                    ),
                    _variant(
                        "radius",
                        "Radius of one circular or arc edge.",
                        _references_schema(
                            "Exactly one circular or arc edge.",
                            min_items=1,
                            max_items=1,
                        ),
                    ),
                    _variant(
                        "diameter",
                        "Diameter of one circular or arc edge.",
                        _references_schema(
                            "Exactly one circular or arc edge.",
                            min_items=1,
                            max_items=1,
                        ),
                    ),
                    _variant(
                        "angle",
                        "Angle between two straight edges.",
                        _references_schema(
                            "Exactly two straight, non-parallel edges.",
                            min_items=2,
                            max_items=2,
                        ),
                    ),
                    _variant(
                        "angle_3_point",
                        "Angle defined by three vertices; the second vertex "
                        "is the apex.",
                        _references_schema(
                            "Exactly three vertices in order: end, apex, end.",
                            min_items=3,
                            max_items=3,
                        ),
                    ),
                ],
            },
        },
        "required": ["page_name", "view_name", "dimension"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    page_name: str,
    view_name: str,
    dimension: dict[str, Any],
) -> dict[str, Any]:
    if not isinstance(dimension, dict):
        return _invalid("dimension must be an object.")
    kind = str(dimension.get("type") or "")
    native_type = _NATIVE_TYPES.get(kind)
    if native_type is None:
        return _invalid(
            "dimension.type must be one of: " + ", ".join(sorted(_NATIVE_TYPES))
        )
    references = dimension.get("references")
    if not isinstance(references, list) or not references:
        return _invalid("dimension.references must be a non-empty array.")
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    page = doc.getObject(str(page_name or "").strip())
    if page is None or getattr(page, "TypeId", "") != "TechDraw::DrawPage":
        return _invalid(
            f"Drawing page not found by exact internal name: {page_name}. "
            "Call core.inspect with scope='domain' for exact names."
        )
    view = doc.getObject(str(view_name or "").strip())
    if view is None or not str(getattr(view, "TypeId", "")).startswith(
        "TechDraw::DrawViewPart"
    ):
        return _invalid(
            f"Projected view not found by exact internal name: {view_name}. "
            "Call core.inspect scope='domain' for exact names; dimensions attach to "
            "part views, not annotations."
        )
    page_views = [obj.Name for obj in list(getattr(page, "Views", []) or [])]
    if view.Name not in page_views:
        return _invalid(
            f"Projected view {view.Name!r} is not a member of page {page.Name!r}.",
            page_views=page_views,
            view=view.Name,
        )
    projection = _projected_element_inventory(view)
    if not projection.get("ok"):
        return _invalid(
            "The projected view has no usable native geometry inventory.",
            projection=projection,
        )
    resolved_input = _resolve_dimension_references(
        service,
        view,
        references,
        projection,
    )
    if not resolved_input.get("ok"):
        return resolved_input
    elements = list(resolved_input["elements"])
    compatibility = _validate_reference_contract(kind, elements, projection)
    if not compatibility.get("ok"):
        return _invalid(
            "The requested projected references do not satisfy this dimension type.",
            dimension_type=kind,
            expected_reference_contract=_REFERENCE_CONTRACTS[kind],
            resolved_references=compatibility.get("resolved_references"),
            reference_failures=compatibility.get("failures"),
            available_projected_elements={
                "edges": projection.get("edges"),
                "vertices": projection.get("vertices"),
            },
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target_page = active.getObject(page.Name)
        target_view = active.getObject(view.Name)
        if target_page is None or target_view is None:
            raise RuntimeError("The page or view no longer exists.")
        dim = active.addObject("TechDraw::DrawViewDimension", "Dimension")
        target_page.addView(dim)
        dim.Type = native_type
        dim.MeasureType = "Projected"
        dim.References2D = [(target_view, element) for element in elements]
        managed_references = list(resolved_input.get("managed_references") or [])
        if managed_references:
            reference_contracts.set_contract(
                dim,
                "techdraw_dimension",
                {
                    "view_name": target_view.Name,
                    "dimension_type": kind,
                    "references": managed_references,
                },
            )
        active.recompute()
        value = None
        text = None
        readback_errors: list[dict[str, str]] = []
        try:
            value = float(dim.getRawValue())
        except Exception as exc:
            readback_errors.append({"field": "raw_value", "native_error": str(exc)})
        try:
            text = str(dim.getText())
        except Exception as exc:
            readback_errors.append({"field": "display_text", "native_error": str(exc)})
        state = list(getattr(dim, "State", []) or [])
        page_members = [obj.Name for obj in list(getattr(target_page, "Views", []) or [])]
        return {
            "document": active.Name,
            "page": target_page.Name,
            "view": target_view.Name,
            "dimension": dim.Name,
            "dimension_label": dim.Label,
            "dimension_type": native_type,
            "requested_references": elements,
            "resolved_references": compatibility.get("resolved_references"),
            "expected_reference_contract": _REFERENCE_CONTRACTS[kind],
            "actual_references": _references_readback(dim),
            "value": value,
            "value_units": "degrees" if "angle" in kind else "mm",
            "display_text": text,
            "readback_errors": readback_errors,
            "feature_state": state,
            "page_views": page_members,
            "page_membership": dim.Name in page_members,
            "retained_dimension": dim.Name,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        value = result.get("value")
        checks = [
            {
                "name": "page_membership",
                "ok": result.get("page_membership") is True,
                "page_views": result.get("page_views"),
            },
            {
                "name": "native_dimension_type",
                "ok": result.get("dimension_type") == native_type,
                "requested": native_type,
                "actual": result.get("dimension_type"),
            },
            {
                "name": "reference_readback",
                "ok": _references_match(
                    result.get("actual_references"), view.Name, elements
                ),
                "requested": [
                    {"view": view.Name, "subelement": item} for item in elements
                ],
                "actual": result.get("actual_references"),
            },
            {
                "name": "measurement_computed",
                "ok": isinstance(value, (int, float))
                and math.isfinite(float(value))
                and not result.get("readback_errors"),
                "value": value,
                "readback_errors": result.get("readback_errors"),
                "feature_state": result.get("feature_state"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Add TechDraw {kind} dimension",
        create,
        verifier=verify,
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    envelope = domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": f"add_{kind}_dimension", **result},
        next_action=(
            "Check the returned value against the model, then add the next "
            "dimension or annotation."
        ),
    )
    return envelope


def _resolve_dimension_references(
    service: Any,
    view: Any,
    references: list[Any],
    projection: dict[str, Any],
) -> dict[str, Any]:
    sources = list(getattr(view, "Source", []) or [])
    has_published_source = any(
        reference_contracts.published_object(source) is not None for source in sources
    )
    elements: list[str] = []
    managed: list[dict[str, Any]] = []
    inventory = list(projection.get("edges") or []) + list(
        projection.get("vertices") or []
    )
    for raw in references:
        if isinstance(raw, str):
            element = raw.strip()
            if not (element.startswith("Edge") or element.startswith("Vertex")):
                return _invalid(
                    f"Unsupported projected element: {element!r}."
                )
            if has_published_source:
                return _invalid(
                    "A dimension on a regenerating scripted view must use "
                    "published semantic interfaces, not transient projected "
                    "EdgeN/VertexN indices.",
                    view=view.Name,
                )
            elements.append(element)
            continue
        if not isinstance(raw, dict) or raw.get("type") != "published_interface":
            return _invalid(
                "Each dimension reference must be a projected element name or "
                "a published_interface selection."
            )
        interface_name = str(raw.get("interface_name") or "").strip()
        candidates: list[dict[str, Any]] = []
        for source in sources:
            if reference_contracts.published_object(source) is None:
                continue
            try:
                candidates.append(
                    reference_contracts.resolve_interface(
                        service, source, interface_name
                    )
                )
            except reference_contracts.ReferenceContractError:
                continue
        if len(candidates) != 1:
            return _invalid(
                f"Published interface {interface_name!r} must resolve on "
                "exactly one source object in the view.",
                view=view.Name,
                source_candidates=[getattr(item, "Name", "") for item in sources],
                matching_source_count=len(candidates),
            )
        interface = candidates[0]
        subelements = list(interface.get("subelements") or [])
        if len(subelements) != 1:
            return _invalid(
                f"TechDraw interface {interface_name!r} must resolve to exactly "
                "one source edge or vertex.",
                resolved_subelements=subelements,
            )
        source_pair = (interface["publication_name"], subelements[0])
        projected_matches = []
        for descriptor in inventory:
            mapping = descriptor.get("source_mapping") or {}
            candidates_raw = list(mapping.get("candidates") or [])
            if any(
                (
                    str(candidate.get("object_name") or ""),
                    str(candidate.get("subelement") or ""),
                )
                == source_pair
                for candidate in candidates_raw
                if isinstance(candidate, dict)
            ):
                projected_matches.append(str(descriptor.get("name") or ""))
        if len(projected_matches) != 1:
            return _invalid(
                f"Published interface {interface_name!r} does not map to one "
                "unambiguous projected element in this view.",
                source_object=source_pair[0],
                source_subelement=source_pair[1],
                projected_matches=projected_matches,
                projection_mapping_summary=projection.get("mapping_summary"),
            )
        elements.append(projected_matches[0])
        managed.append(
            {
                "type": "published_interface",
                "interface_name": interface_name,
                "model_id": interface["model_id"],
                "publication_name": interface["publication_name"],
                "output_key": interface["output_key"],
            }
        )
    return {"ok": True, "elements": elements, "managed_references": managed}


def rebind_scripted_reference(
    service: Any,
    dimension_obj: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    doc = service._active_document()
    view_name = str(contract.get("view_name") or "")
    view = doc.getObject(view_name) if doc is not None else None
    if view is None:
        return _invalid("The TechDraw view recorded by the dimension no longer exists.")
    references = contract.get("references")
    if not isinstance(references, list) or not references:
        return _invalid("The TechDraw reference contract contains no references.")
    try:
        view.touch()
        dimension_obj.touch()
        revision = str(contract.get("source_revision") or "")
        reference_contracts.mark_stale(
            view,
            revision,
            "A referenced scripted model changed; regenerate this drawing view.",
        )
        reference_contracts.mark_stale(
            dimension_obj,
            revision,
            "The source view changed; regenerate and verify this dimension.",
        )
    except Exception as exc:
        return _invalid(
            "FreeCAD could not invalidate the affected TechDraw dimension.",
            native_error=str(exc),
        )
    return {
        "ok": True,
        "domain": "techdraw_dimension",
        "object": dimension_obj.Name,
        "view": view.Name,
        "rebind_deferred": True,
        "derived_state": "stale",
        "projection_recompute_deferred": True,
    }


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _validate_reference_contract(
    kind: str, elements: list[str], projection: dict[str, Any]
) -> dict[str, Any]:
    inventory = {
        str(item.get("name")): item
        for item in list(projection.get("edges") or [])
        + list(projection.get("vertices") or [])
        if isinstance(item, dict) and item.get("name")
    }
    resolved = [inventory.get(name) for name in elements]
    failures: list[dict[str, Any]] = []
    for index, (name, descriptor) in enumerate(zip(elements, resolved)):
        if descriptor is None:
            failures.append(
                {
                    "reference_index": index,
                    "reference": name,
                    "reason": "projected_element_not_found",
                }
            )
    if len(set(elements)) != len(elements):
        failures.append({"reason": "duplicate_references", "references": elements})
    if failures:
        return {
            "ok": False,
            "resolved_references": resolved,
            "failures": failures,
        }

    types = [item.get("element_type") for item in resolved if item]
    geometry = [item.get("geometry_type") for item in resolved if item]
    line_edges = all(
        item.get("element_type") == "edge" and item.get("geometry_type") == "Line"
        for item in resolved
        if item
    )
    vertices = all(item.get("element_type") == "vertex" for item in resolved if item)

    valid = False
    if kind == "length":
        valid = (
            len(resolved) == 1 and line_edges
        ) or (
            len(resolved) == 2
            and (vertices or (line_edges and _parallel_edges(resolved[0], resolved[1])))
        )
    elif kind in {"horizontal_length", "vertical_length"}:
        valid = (len(resolved) == 1 and line_edges) or (
            len(resolved) == 2 and vertices
        )
    elif kind in {"radius", "diameter"}:
        valid = len(resolved) == 1 and geometry[0] in {"Circle", "ArcOfCircle"}
    elif kind == "angle":
        valid = (
            len(resolved) == 2
            and line_edges
            and not _parallel_edges(resolved[0], resolved[1])
        )
    elif kind == "angle_3_point":
        valid = (
            len(resolved) == 3
            and vertices
            and _three_distinct_non_collinear(resolved)
        )
    if not valid:
        failures.append(
            {
                "reason": "reference_geometry_contract_mismatch",
                "expected": _REFERENCE_CONTRACTS[kind],
                "actual_element_types": types,
                "actual_geometry_types": geometry,
            }
        )
    return {
        "ok": valid,
        "resolved_references": resolved,
        "failures": failures,
    }


def _edge_vector(edge: dict[str, Any]) -> tuple[float, float]:
    start = edge.get("start_2d") or {}
    end = edge.get("end_2d") or {}
    return (
        float(end.get("x", 0.0)) - float(start.get("x", 0.0)),
        float(end.get("y", 0.0)) - float(start.get("y", 0.0)),
    )


def _parallel_edges(first: dict[str, Any], second: dict[str, Any]) -> bool:
    ax, ay = _edge_vector(first)
    bx, by = _edge_vector(second)
    a_length = math.hypot(ax, ay)
    b_length = math.hypot(bx, by)
    if a_length <= 1.0e-12 or b_length <= 1.0e-12:
        return False
    return abs(ax * by - ay * bx) / (a_length * b_length) <= 1.0e-8


def _three_distinct_non_collinear(items: list[dict[str, Any]]) -> bool:
    points = []
    for item in items:
        point = item.get("point_2d") or {}
        points.append((float(point.get("x", 0.0)), float(point.get("y", 0.0))))
    if len(set(points)) != 3:
        return False
    first, apex, third = points
    ax, ay = first[0] - apex[0], first[1] - apex[1]
    bx, by = third[0] - apex[0], third[1] - apex[1]
    scale = max(math.hypot(ax, ay) * math.hypot(bx, by), 1.0)
    return abs(ax * by - ay * bx) > 1.0e-9 * scale


def _references_readback(dimension: Any) -> list[dict[str, str]]:
    values = list(getattr(dimension, "References2D", []) or [])
    result: list[dict[str, str]] = []
    for value in values:
        if not isinstance(value, (tuple, list)) or len(value) < 2:
            result.append({"view": "", "subelement": str(value)})
            continue
        obj = value[0]
        subelements = value[1]
        if isinstance(subelements, str):
            names = [subelements]
        else:
            names = [str(item) for item in list(subelements or [])]
        for subelement in names:
            result.append(
                {
                    "view": str(getattr(obj, "Name", "") or ""),
                    "subelement": subelement,
                }
            )
    return result


def _references_match(actual: Any, view_name: str, elements: list[str]) -> bool:
    if not isinstance(actual, list):
        return False
    expected = [{"view": view_name, "subelement": item} for item in elements]
    return actual == expected
