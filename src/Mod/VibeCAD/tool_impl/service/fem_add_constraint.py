# SPDX-License-Identifier: LGPL-2.1-or-later

"""Add one native FEM constraint (support or load) to an exact analysis."""

from __future__ import annotations

from typing import Any

from VibeCADTransactions import run_freecad_transaction
import VibeCADReferenceContracts as reference_contracts

from . import domain_runtime


_OBJECT_NAME_SCHEMA = {
    "type": "string",
    "description": (
        "Exact internal name of the shaped model object the constraint acts "
        "on (the object being analyzed, not the FEM mesh)."
    ),
}

_REFERENCE_ITEM_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "object_name": _OBJECT_NAME_SCHEMA,
                "element": {
                    "type": "string",
                    "pattern": "^(Face|Edge|Vertex)[1-9][0-9]*$",
                    "description": (
                        "Exact native subelement. Use only for a non-scripted "
                        "object whose topology is not regenerated."
                    ),
                },
            },
            "required": ["object_name", "element"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "object_name": _OBJECT_NAME_SCHEMA,
                "selection": reference_contracts.interface_selection_schema(),
            },
            "required": ["object_name", "selection"],
            "additionalProperties": False,
        },
    ]
}


TOOL_SPEC = {
    "name": "fem.add_constraint",
    "description": (
        "Add one native FEM constraint to an exact analysis on exact model "
        "subelements. A static analysis needs at least one fixed support "
        "and one load (force, pressure, or gravity) or it cannot solve. "
        "Constraints reference the model object's faces/edges/vertices, not "
        "the FEM mesh."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "FemWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "analysis_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the FEM analysis from core.inspect scope='domain'."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new constraint object.",
            },
            "constraint": {
                "description": "Constraint behavior; choose exactly one variant.",
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "fixed",
                                "description": (
                                    "Fixed support: the referenced elements "
                                    "cannot move in any direction. Every "
                                    "static analysis needs at least one."
                                ),
                            },
                            "references": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 20,
                                "items": _REFERENCE_ITEM_SCHEMA,
                                "description": (
                                    "Model subelements to hold fixed, "
                                    "typically mounting faces."
                                ),
                            },
                        },
                        "required": ["type", "references"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "force",
                                "description": (
                                    "Force load: a total force in newtons "
                                    "distributed evenly over the referenced "
                                    "elements, acting along the face normal "
                                    "unless a direction edge is given."
                                ),
                            },
                            "references": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 20,
                                "items": _REFERENCE_ITEM_SCHEMA,
                                "description": ("Model subelements the force acts on."),
                            },
                            "force_n": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Total force magnitude in newtons, "
                                    "distributed over all references."
                                ),
                            },
                            "direction": {
                                **_REFERENCE_ITEM_SCHEMA,
                                "description": (
                                    "Optional straight edge or planar face "
                                    "whose direction the force follows; omit "
                                    "to act along each referenced face's "
                                    "normal."
                                ),
                            },
                            "reversed": {
                                "type": "boolean",
                                "description": (
                                    "true flips the force to act opposite "
                                    "the direction edge or face normal."
                                ),
                            },
                        },
                        "required": ["type", "references", "force_n", "reversed"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "pressure",
                                "description": (
                                    "Pressure load in MPa acting normal to "
                                    "the referenced faces, e.g. hydraulic or "
                                    "contact pressure."
                                ),
                            },
                            "references": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 20,
                                "items": _REFERENCE_ITEM_SCHEMA,
                                "description": "Faces the pressure acts on.",
                            },
                            "pressure_mpa": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": ("Pressure magnitude in MPa (N/mm^2)."),
                            },
                            "reversed": {
                                "type": "boolean",
                                "description": (
                                    "false pushes into the face (compression), "
                                    "true pulls away from it (suction)."
                                ),
                            },
                        },
                        "required": ["type", "references", "pressure_mpa", "reversed"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "gravity",
                                "description": (
                                    "Self-weight load: standard gravity "
                                    "(9.81 m/s^2, global -Z) applied to the "
                                    "whole model; needs no references but "
                                    "does need a material with density."
                                ),
                            },
                        },
                        "required": ["type"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "temperature",
                                "description": (
                                    "Fixed temperature in kelvin on the "
                                    "referenced elements, for thermomech "
                                    "analyses."
                                ),
                            },
                            "references": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 20,
                                "items": _REFERENCE_ITEM_SCHEMA,
                                "description": (
                                    "Model subelements held at the temperature."
                                ),
                            },
                            "temperature_k": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Temperature in kelvin (293.15 K = 20 C)."
                                ),
                            },
                        },
                        "required": ["type", "references", "temperature_k"],
                        "additionalProperties": False,
                    },
                ],
            },
        },
        "required": ["analysis_name", "label", "constraint"],
        "additionalProperties": False,
    },
}


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


def run(
    service: Any,
    analysis_name: str,
    label: str,
    constraint: dict[str, Any],
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    analysis = service._get_fem_analysis(str(analysis_name or "").strip())
    if analysis is None:
        return _invalid(
            f"FEM analysis not found by exact internal name: {analysis_name}. "
            "Call core.inspect with scope='domain' for exact names."
        )
    if not isinstance(constraint, dict):
        return _invalid("constraint must be an object with a 'type' field.")
    constraint_type = str(constraint.get("type") or "").strip()
    if constraint_type not in ("fixed", "force", "pressure", "gravity", "temperature"):
        return _invalid(
            f"Unknown constraint type: {constraint_type}. Choose one of: "
            "fixed, force, pressure, gravity, temperature."
        )
    try:
        import ObjectsFem  # noqa: F401
    except ImportError:
        return _invalid(
            "The FEM workbench is not available in this FreeCAD build; "
            "constraints cannot be added."
        )
    solver = _analysis_solver(analysis)
    if solver is None:
        return _invalid(
            "The analysis must contain exactly one solver before constraints can be added."
        )
    analysis_type = str(getattr(solver, "AnalysisType", "") or "")
    supported_types = _CONSTRAINT_ANALYSIS_TYPES[constraint_type]
    if analysis_type not in supported_types:
        return _invalid(
            f"Constraint type {constraint_type!r} is not compatible with "
            f"analysis type {analysis_type!r}.",
            constraint_type=constraint_type,
            analysis_type=analysis_type,
            compatible_analysis_types=sorted(supported_types),
        )

    refs: list[dict[str, Any]] = []
    if constraint_type != "gravity":
        refs, error = _validate_references(service, constraint.get("references"))
        if error is not None:
            return _invalid(error)
    geometry_error = _validate_constraint_geometry(constraint_type, refs)
    if geometry_error:
        return _invalid(
            geometry_error,
            constraint_type=constraint_type,
            resolved_references=refs,
            required_geometry=_REQUIRED_GEOMETRY[constraint_type],
        )
    direction_ref: dict[str, Any] | None = None
    if constraint_type == "force" and constraint.get("direction") is not None:
        direction_refs, error = _validate_references(service, [constraint["direction"]])
        if error is not None:
            return _invalid(f"direction: {error}")
        direction_ref = direction_refs[0]
        direction_geometry = direction_ref.get("geometry") or {}
        if not (
            direction_geometry.get("geometry_type") == "line"
            or (
                direction_geometry.get("element_type") == "face"
                and direction_geometry.get("geometry_type") == "plane"
            )
        ):
            return _invalid(
                "Force direction must be a straight edge or planar face; no "
                "constraint was created.",
                resolved_direction=direction_ref,
                required_geometry="straight edge or planar face",
            )
    if constraint_type == "force" and float(constraint.get("force_n", 0.0)) <= 0.0:
        return _invalid("force_n must be positive.")
    if constraint_type == "pressure" and float(constraint.get("pressure_mpa", 0.0)) <= 0.0:
        return _invalid("pressure_mpa must be positive.")
    if constraint_type == "temperature" and float(constraint.get("temperature_k", 0.0)) <= 0.0:
        return _invalid("temperature_k must be positive.")
    if constraint_type == "gravity" and not _analysis_has_material_property(
        analysis, "Density"
    ):
        return _invalid(
            "Gravity requires an analysis material with Density; add that "
            "material before creating self-weight.",
            analysis_type=analysis_type,
            required_material_property="Density",
        )
    mesh_sources = _analysis_mesh_sources(analysis)
    referenced_objects = sorted({item["object_name"] for item in refs})
    if direction_ref is not None:
        referenced_objects = sorted(
            set(referenced_objects) | {direction_ref["object_name"]}
        )
    unrelated = [name for name in referenced_objects if mesh_sources and name not in mesh_sources]
    if unrelated:
        return _invalid(
            "One or more constraint references do not belong to the model "
            "source already meshed by this analysis.",
            meshed_source_objects=mesh_sources,
            unrelated_reference_objects=unrelated,
            resolved_references=refs,
        )
    model_relationship = {
        "status": "verified_against_mesh" if mesh_sources else "pending_mesh_creation",
        "meshed_source_objects": mesh_sources,
        "constraint_source_objects": referenced_objects,
    }

    def create() -> dict[str, Any]:
        import FreeCAD as App
        import ObjectsFem

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target = active.getObject(analysis.Name)
        if target is None:
            raise RuntimeError("The analysis no longer exists.")
        references = [
            (active.getObject(item["object_name"]), item["element"]) for item in refs
        ]
        if constraint_type == "fixed":
            con = ObjectsFem.makeConstraintFixed(active, "Fixed")
            con.References = references
        elif constraint_type == "force":
            con = ObjectsFem.makeConstraintForce(active, "Force")
            con.References = references
            con.Force = f"{float(constraint['force_n'])} N"
            if direction_ref is not None:
                direction_obj = active.getObject(direction_ref["object_name"])
                con.Direction = (direction_obj, [direction_ref["element"]])
            con.Reversed = bool(constraint.get("reversed"))
        elif constraint_type == "pressure":
            con = ObjectsFem.makeConstraintPressure(active, "Pressure")
            con.References = references
            con.Pressure = f"{float(constraint['pressure_mpa'])} MPa"
            con.Reversed = bool(constraint.get("reversed"))
        elif constraint_type == "gravity":
            con = ObjectsFem.makeConstraintSelfWeight(active, "Gravity")
        else:
            con = ObjectsFem.makeConstraintTemperature(active, "Temperature")
            con.References = references
            con.Temperature = f"{float(constraint['temperature_k'])} K"
        con.Label = clean_label
        target.addObject(con)
        contract_references = _contract_reference_entries(refs)
        contract_direction = (
            _contract_reference_entries([direction_ref])
            if direction_ref is not None
            else []
        )
        if any(item.get("selection") for item in contract_references + contract_direction):
            reference_contracts.set_contract(
                con,
                "fem_constraint",
                {
                    "references": contract_references,
                    "direction": contract_direction[0] if contract_direction else None,
                },
            )
        active.recompute()
        group_members = [obj.Name for obj in list(target.Group or [])]
        return {
            "document": active.Name,
            "analysis": target.Name,
            "constraint_object": con.Name,
            "constraint_object_label": con.Label,
            "constraint_type": constraint_type,
            "analysis_type": analysis_type,
            "required_geometry": _REQUIRED_GEOMETRY[constraint_type],
            "resolved_references": refs,
            "resolved_direction": direction_ref,
            "model_relationship": model_relationship,
            "actual_references": _link_sub_readback(getattr(con, "References", [])),
            "actual_direction": _link_sub_readback(getattr(con, "Direction", None))
            if constraint_type == "force" and direction_ref is not None
            else None,
            "actual_quantities": _quantity_readback(con, constraint_type),
            "analysis_group_members": group_members,
            "constraint_in_analysis": con.Name in group_members,
            "constraint_state": list(getattr(con, "State", []) or []),
            "retained_constraint": con.Name,
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        expected_refs = [
            {"object_name": item["object_name"], "element": item["element"]}
            for item in refs
        ]
        checks = [
            {
                "name": "analysis_membership",
                "ok": result.get("constraint_in_analysis") is True,
                "analysis_group_members": result.get("analysis_group_members"),
            },
            {
                "name": "reference_readback",
                "ok": result.get("actual_references") == expected_refs,
                "requested": expected_refs,
                "actual": result.get("actual_references"),
            },
            {
                "name": "quantity_readback",
                "ok": (result.get("actual_quantities") or {}).get("ok") is True,
                "actual": result.get("actual_quantities"),
            },
            {
                "name": "direction_readback",
                "ok": direction_ref is None
                or result.get("actual_direction")
                == [
                    {
                        "object_name": direction_ref["object_name"],
                        "element": direction_ref["element"],
                    }
                ],
                "actual": result.get("actual_direction"),
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Add FEM constraint: {clean_label}",
        create,
        verifier=verify,
    )
    result = (
        transaction.get("result", {})
        if isinstance(transaction, dict) and isinstance(transaction.get("result"), dict)
        else {}
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "add_constraint", **result},
        next_action=(
            "Add remaining supports/loads, then generate the mesh with "
            "fem.mesh_analysis and run fem.solve."
        ),
    )


def _contract_reference_entries(refs: list[dict[str, Any] | None]) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in refs:
        if not isinstance(item, dict):
            continue
        selection = item.get("managed_selection")
        if not isinstance(selection, dict):
            key = (
                str(item.get("object_name") or ""),
                "exact",
                str(item.get("element") or ""),
            )
            if key not in seen:
                seen.add(key)
                entries.append({"object_name": key[0], "element": key[2]})
            continue
        key = (
            str(item.get("object_name") or ""),
            str(selection.get("model_id") or ""),
            str(selection.get("interface_name") or ""),
        )
        if key in seen:
            continue
        seen.add(key)
        entries.append(
            {
                "object_name": key[0],
                "selection": dict(selection),
            }
        )
    return entries


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


_CONSTRAINT_ANALYSIS_TYPES = {
    "fixed": {"static", "frequency", "thermomech", "check", "buckling"},
    "force": {"static", "thermomech", "check", "buckling"},
    "pressure": {"static", "thermomech", "check", "buckling"},
    "gravity": {"static", "thermomech", "check", "buckling"},
    "temperature": {"thermomech", "check"},
}

_REQUIRED_GEOMETRY = {
    "fixed": "one or more faces, edges, or vertices",
    "force": "one or more faces, edges, or vertices",
    "pressure": "one or more faces",
    "gravity": "whole meshed model; no subelement references",
    "temperature": "one or more faces",
}


def _analysis_solver(analysis: Any) -> Any:
    solvers = [
        member
        for member in list(getattr(analysis, "Group", []) or [])
        if "Solver" in str(getattr(member, "TypeId", ""))
    ]
    return solvers[0] if len(solvers) == 1 else None


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


def _validate_constraint_geometry(
    constraint_type: str, refs: list[dict[str, Any]]
) -> str | None:
    if constraint_type in {"pressure", "temperature"} and any(
        (item.get("geometry") or {}).get("element_type") != "face" for item in refs
    ):
        return f"{constraint_type} constraints require face references."
    return None


def _analysis_mesh_sources(analysis: Any) -> list[str]:
    sources = []
    for member in list(getattr(analysis, "Group", []) or []):
        shape_link = getattr(member, "Shape", None)
        if shape_link is not None and hasattr(member, "FemMesh"):
            name = str(getattr(shape_link, "Name", "") or "")
            if name:
                sources.append(name)
    return sorted(set(sources))


def _analysis_has_material_property(analysis: Any, name: str) -> bool:
    for member in list(getattr(analysis, "Group", []) or []):
        material = getattr(member, "Material", None)
        if isinstance(material, dict) and name in material and str(material[name]).strip():
            return True
    return False


def _link_sub_readback(value: Any) -> list[dict[str, str]]:
    if value is None:
        return []
    if (
        isinstance(value, (list, tuple))
        and len(value) >= 2
        and hasattr(value[0], "Name")
    ):
        values = [value]
    else:
        values = list(value) if isinstance(value, (list, tuple)) else [value]
    result: list[dict[str, str]] = []
    for entry in values:
        if not isinstance(entry, (list, tuple)) or len(entry) < 2:
            continue
        obj = entry[0]
        names = entry[1] if isinstance(entry[1], (list, tuple)) else [entry[1]]
        for name in names:
            result.append(
                {
                    "object_name": str(getattr(obj, "Name", "") or ""),
                    "element": str(name),
                }
            )
    return result


def _quantity_readback(constraint: Any, constraint_type: str) -> dict[str, Any]:
    if constraint_type == "fixed":
        return {"ok": True}
    if constraint_type == "gravity":
        return {"ok": True, "acceleration": "standard gravity along global -Z"}
    property_name, unit = {
        "force": ("Force", "N"),
        "pressure": ("Pressure", "MPa"),
        "temperature": ("Temperature", "K"),
    }[constraint_type]
    try:
        value = getattr(constraint, property_name)
        return {
            "ok": True,
            "property": property_name,
            "value": float(value.getValueAs(unit)),
            "unit": unit,
            "user_string": str(value.UserString),
            "reversed": bool(getattr(constraint, "Reversed", False))
            if hasattr(constraint, "Reversed")
            else None,
        }
    except Exception as exc:
        return {
            "ok": False,
            "property": property_name,
            "native_error": str(exc),
        }
