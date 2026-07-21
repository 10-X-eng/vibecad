# SPDX-License-Identifier: LGPL-2.1-or-later

"""Create one native assembly joint between two exact component references."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from VibeCADTransactions import run_freecad_transaction
import VibeCADReferenceContracts as reference_contracts

from . import domain_runtime, partdesign_dressup_feature


_JOINT_TYPE_NATIVE = {
    "fixed": "Fixed",
    "revolute": "Revolute",
    "cylindrical": "Cylindrical",
    "slider": "Slider",
    "ball": "Ball",
    "distance": "Distance",
    "parallel": "Parallel",
    "perpendicular": "Perpendicular",
    "angle": "Angle",
    "rack_pinion": "RackPinion",
    "screw": "Screw",
    "gears": "Gears",
    "belt": "Belt",
}


def _joint_variant(kind: str, description: str, **properties: Any) -> dict[str, Any]:
    schema_properties: dict[str, Any] = {
        "type": {"const": kind, "description": description},
        **properties,
    }
    return {
        "type": "object",
        "properties": schema_properties,
        "required": ["type", *properties],
        "additionalProperties": False,
    }


_JOINT_VARIANTS = [
    _joint_variant("fixed", "Rigidly lock both connector frames."),
    _joint_variant("revolute", "Leave one rotation around the shared axis."),
    _joint_variant("cylindrical", "Leave rotation and translation along the shared axis."),
    _joint_variant("slider", "Leave one translation along the shared axis."),
    _joint_variant("ball", "Coincide connector origins while leaving three rotations."),
    _joint_variant(
        "distance",
        "Maintain one connector separation.",
        distance_mm={"type": "number", "minimum": 0},
    ),
    _joint_variant("parallel", "Keep connector axes parallel."),
    _joint_variant("perpendicular", "Keep connector axes perpendicular."),
    _joint_variant(
        "angle",
        "Maintain an explicit angle between connector axes.",
        angle_degrees={"type": "number", "minimum": -360, "maximum": 360},
    ),
    _joint_variant(
        "rack_pinion",
        "Couple rack travel to pinion rotation using the pitch radius.",
        pitch_radius_mm={"type": "number", "exclusiveMinimum": 0},
    ),
    _joint_variant(
        "screw",
        "Couple axial travel to rotation using the thread pitch.",
        thread_pitch_mm={"type": "number", "exclusiveMinimum": 0},
    ),
    _joint_variant(
        "gears",
        "Couple two external gears using their pitch radii.",
        radius1_mm={"type": "number", "exclusiveMinimum": 0},
        radius2_mm={"type": "number", "exclusiveMinimum": 0},
    ),
    _joint_variant(
        "belt",
        "Couple two pulleys using their pitch radii.",
        radius1_mm={"type": "number", "exclusiveMinimum": 0},
        radius2_mm={"type": "number", "exclusiveMinimum": 0},
    ),
]


_REFERENCE_SELECTION_SCHEMA = deepcopy(
    partdesign_dressup_feature.selection_schema(
        allow_all_edges=False,
        required_count=1,
    )
)
_REFERENCE_SELECTION_SCHEMA["oneOf"].insert(
    0,
    {
        "type": "object",
        "properties": {"type": {"const": "component_origin"}},
        "required": ["type"],
        "additionalProperties": False,
    },
)
_REFERENCE_SELECTION_SCHEMA["oneOf"].append(
    {
        "type": "object",
        "properties": {
            "type": {"const": "exact_vertex"},
            "subelement": {"type": "string", "pattern": "^Vertex[1-9][0-9]*$"},
        },
        "required": ["type", "subelement"],
        "additionalProperties": False,
    }
)
_REFERENCE_SELECTION_SCHEMA["oneOf"].append(
    reference_contracts.interface_selection_schema()
)


def _reference_schema(which: str) -> dict[str, Any]:
    return {
        "type": "object",
        "description": (
            f"The {which} joint connector: an exact component and the exact "
            "subelement on it to attach to."
        ),
        "properties": {
            "component_name": {
                "type": "string",
                "description": (
                    f"Exact internal name of the {which} component inside the "
                    "assembly (from core.inspect scope='domain'), not the linked "
                    "source object."
                ),
            },
            "selection": _REFERENCE_SELECTION_SCHEMA,
        },
        "required": ["component_name", "selection"],
        "additionalProperties": False,
    }


TOOL_SPEC = {
    "name": "assembly.create_joint",
    "description": (
        "Create one native assembly joint connecting two exact component "
        "references, then run the solver so unfixed components move to "
        "satisfy it. Joint types remove degrees of freedom: fixed locks all "
        "six, revolute leaves one rotation, cylindrical leaves rotation plus "
        "translation along one axis, slider leaves one translation, ball "
        "leaves all three rotations, distance holds a set separation. Ground "
        "one component first or the solver cannot run."
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
                    "Exact internal name of the assembly from core.inspect scope='domain'."
                ),
            },
            "reference1": _reference_schema("first"),
            "reference2": _reference_schema("second"),
            "joint": {
                "description": "Joint behavior; choose exactly one variant.",
                "oneOf": _JOINT_VARIANTS,
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new joint, e.g. 'HingePin'.",
            },
        },
        "required": ["assembly_name", "reference1", "reference2", "joint", "label"],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    assembly_name: str,
    reference1: dict[str, Any],
    reference2: dict[str, Any],
    joint: dict[str, Any],
    label: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    if not isinstance(joint, dict):
        return _invalid("joint must be an object.")
    kind = str(joint.get("type") or "")
    native_type = _JOINT_TYPE_NATIVE.get(kind)
    if native_type is None:
        return _invalid(
            "joint.type must be one of: " + ", ".join(sorted(_JOINT_TYPE_NATIVE))
        )
    doc = service._active_document()
    if doc is None:
        return _invalid("No active document.")
    assembly = _find_assembly(service, assembly_name)
    if assembly is None:
        return _invalid(
            f"Assembly not found by exact internal name: {assembly_name}. "
            "Call core.inspect with scope='domain' for exact names."
        )
    joint_group = domain_runtime.assembly_joint_group(assembly)
    if joint_group is None:
        return _invalid(
            "The assembly has no native Assembly::JointGroup; joint creation cannot proceed.",
            assembly=assembly.Name,
            children=[
                {"name": child.Name, "label": child.Label, "type": child.TypeId}
                for child in list(getattr(assembly, "Group", []) or [])
            ],
        )
    try:
        import JointObject
    except Exception as exc:
        return _invalid("The native Assembly JointObject module is unavailable.", native_error=str(exc))
    supported_types = list(JointObject.JointTypes)
    if native_type not in supported_types:
        return _invalid(
            "The selected joint type is not supported by this FreeCAD build.",
            requested_type=native_type,
            native_supported_types=supported_types,
        )
    parsed_refs: list[dict[str, Any]] = []
    for key, reference in (("reference1", reference1), ("reference2", reference2)):
        resolved = _resolve_reference(service, assembly, reference, key)
        if not resolved.get("ok"):
            return resolved
        parsed_refs.append(resolved)
    if parsed_refs[0]["component_name"] == parsed_refs[1]["component_name"]:
        return _invalid(
            "reference1 and reference2 must be on two different components; "
            "a joint between a component and itself does nothing."
        )
    compatibility = _joint_compatibility(kind, parsed_refs)
    if not compatibility.get("ok"):
        return _invalid(
            "The resolved connector geometry is incompatible with the selected joint type.",
            joint_type=kind,
            native_joint_type=native_type,
            references=parsed_refs,
            compatibility=compatibility,
        )
    placements_before = {
        item["component_name"]: {
            "assembly_local": domain_runtime.placement_summary(item["component"]),
            "global": domain_runtime.global_placement_summary(item["component"]),
        }
        for item in parsed_refs
    }

    def create() -> dict[str, Any]:
        import FreeCAD as App
        import JointObject
        import UtilsAssembly

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        target_assembly = active.getObject(assembly.Name)
        if target_assembly is None:
            raise RuntimeError("The assembly no longer exists.")
        refs = []
        for parsed in parsed_refs:
            component_name = parsed["component_name"]
            element = parsed["element"]
            component = active.getObject(component_name)
            if component is None:
                raise RuntimeError(f"Component no longer exists: {component_name}")
            refs.append([component, [element, element]])
        native_joint_group = domain_runtime.assembly_joint_group(target_assembly)
        if native_joint_group is None:
            raise RuntimeError("The assembly's native JointGroup disappeared before execution.")
        joint_obj = native_joint_group.newObject("App::FeaturePython", "Joint")
        type_index = JointObject.JointTypes.index(native_type)
        JointObject.Joint(joint_obj, type_index)
        JointObject.ensureViewProviderJoint(joint_obj)
        joint_obj.Label = clean_label
        _apply_joint_parameters(joint_obj, kind, joint)
        joint_obj.Proxy.setJointConnectors(joint_obj, refs)
        managed_references = [
            {
                "component_name": parsed["component_name"],
                "selection": dict(parsed["selection"]),
            }
            for parsed in parsed_refs
        ]
        if any(
            item["selection"].get("type") == "published_interface"
            for item in managed_references
        ):
            reference_contracts.set_contract(
                joint_obj,
                "assembly_joint",
                {
                    "assembly_name": target_assembly.Name,
                    "references": managed_references,
                },
            )
        connectors = []
        for index in (1, 2):
            reference_value = getattr(joint_obj, f"Reference{index}")
            placement = getattr(joint_obj, f"Placement{index}")
            global_placement = UtilsAssembly.getJcsGlobalPlc(placement, reference_value)
            connectors.append(
                {
                    "index": index,
                    "reference": _native_reference_readback(reference_value),
                    "local_frame": _placement_value_summary(placement),
                    "global_frame": _placement_value_summary(global_placement),
                }
            )
        solver_code = int(target_assembly.solve(False))
        active.recompute()
        solver_diagnostics = domain_runtime.assembly_solver_diagnostics(target_assembly)
        return {
            "document": active.Name,
            "assembly": target_assembly.Name,
            "joint": joint_obj.Name,
            "joint_label": joint_obj.Label,
            "joint_type": native_type,
            "native_supported_joint_types": supported_types,
            "requested_parameters": dict(joint),
            "actual_parameters": _joint_parameter_readback(joint_obj, kind),
            "resolved_references": [
                {key: value for key, value in parsed.items() if key != "component"}
                for parsed in parsed_refs
            ],
            "connector_frames": connectors,
            "compatibility": compatibility,
            "solver_code": solver_code,
            "solver_verdict": domain_runtime.assembly_solver_verdict(solver_code),
            "solver_diagnostics": solver_diagnostics,
            "component_placements_before": placements_before,
            "component_placements_after": {
                parsed["component_name"]: {
                    "assembly_local": domain_runtime.placement_summary(
                        active.getObject(parsed["component_name"])
                    ),
                    "global": domain_runtime.global_placement_summary(
                        active.getObject(parsed["component_name"])
                    ),
                }
                for parsed in parsed_refs
            },
        }

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        diagnostics = result.get("solver_diagnostics") or {}
        connectors = list(result.get("connector_frames") or [])
        checks = [
            {
                "name": "connector_readback",
                "ok": len(connectors) == 2
                and all((item.get("reference") or {}).get("component") for item in connectors),
                "actual": connectors,
            },
            {
                "name": "native_solver_diagnostics",
                "ok": diagnostics.get("available") is True,
                "actual": diagnostics,
            },
            {
                "name": "solver_result",
                "ok": int(result.get("solver_code", -1)) == 0
                and not diagnostics.get("has_conflicts")
                and not diagnostics.get("has_redundancies")
                and not diagnostics.get("has_partial_redundancies")
                and not diagnostics.get("has_malformed_constraints"),
                "solver_code": result.get("solver_code"),
                "diagnostics": diagnostics,
            },
        ]
        return {"ok": all(check["ok"] for check in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Create assembly {kind} joint: {clean_label}",
        create,
        verifier=verify,
    )
    mutation = transaction.get("result") if isinstance(transaction.get("result"), dict) else {}
    envelope = domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": f"create_{kind}_joint", "mutation": mutation},
        next_action=(
            "Check solver_verdict and the returned component placements, then "
            "add the next joint or run assembly.solve."
        ),
    )
    return envelope


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
            else "component_origin"
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
    selection_state = partdesign_dressup_feature.resolve_selection(
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


def _joint_compatibility(kind: str, references: list[dict[str, Any]]) -> dict[str, Any]:
    geometry = [str(reference.get("geometry_type") or "") for reference in references]
    axis_capable = {"line", "circle", "plane", "cylinder", "cone", "component_origin"}
    rotary = {"circle", "cylinder", "cone"}
    linear = {"line", "plane"}
    orientation_capable = axis_capable
    criteria = "any connector geometry"
    ok = True
    if kind in {"revolute", "cylindrical", "screw", "gears", "belt"}:
        criteria = "both connectors must define axes"
        ok = all(value in axis_capable for value in geometry)
    elif kind == "slider":
        criteria = "both connectors must define linear axes or plane normals"
        ok = all(value in linear | {"component_origin"} for value in geometry)
    elif kind == "rack_pinion":
        criteria = "one linear connector and one circular/cylindrical connector"
        ok = any(value in linear for value in geometry) and any(value in rotary for value in geometry)
    elif kind in {"parallel", "perpendicular", "angle"}:
        criteria = "both connectors must define orientations"
        ok = all(value in orientation_capable for value in geometry)
    elif kind == "ball":
        criteria = "both connectors must define points or natural centers"
        ok = all(bool(value) for value in geometry)
    return {
        "ok": ok,
        "joint_type": kind,
        "criteria": criteria,
        "resolved_geometry_types": geometry,
    }


def _apply_joint_parameters(joint_obj: Any, kind: str, definition: dict[str, Any]) -> None:
    if kind == "distance":
        joint_obj.Distance = float(definition["distance_mm"])
    elif kind == "angle":
        joint_obj.Angle = float(definition["angle_degrees"])
    elif kind == "rack_pinion":
        joint_obj.Distance = float(definition["pitch_radius_mm"])
    elif kind == "screw":
        joint_obj.Distance = float(definition["thread_pitch_mm"])
    elif kind in {"gears", "belt"}:
        joint_obj.Distance = float(definition["radius1_mm"])
        joint_obj.Distance2 = float(definition["radius2_mm"])


def _joint_parameter_readback(joint_obj: Any, kind: str) -> dict[str, Any]:
    result = {"joint_type": str(joint_obj.JointType)}
    if kind == "distance":
        result["distance_mm"] = float(joint_obj.Distance)
    elif kind == "angle":
        result["angle_degrees"] = float(joint_obj.Angle)
    elif kind == "rack_pinion":
        result["pitch_radius_mm"] = float(joint_obj.Distance)
    elif kind == "screw":
        result["thread_pitch_mm"] = float(joint_obj.Distance)
    elif kind in {"gears", "belt"}:
        result["radius1_mm"] = float(joint_obj.Distance)
        result["radius2_mm"] = float(joint_obj.Distance2)
    return result


def _native_reference_readback(reference: Any) -> dict[str, Any]:
    if not isinstance(reference, tuple) or len(reference) < 2:
        return {"component": None, "subelements": [], "raw_type": type(reference).__name__}
    obj = reference[0]
    subs = reference[1]
    if isinstance(subs, str):
        subs = [subs]
    return {
        "component": getattr(obj, "Name", None),
        "subelements": [str(value) for value in list(subs or [])],
    }


def _placement_value_summary(placement: Any) -> dict[str, Any]:
    return {
        "position": domain_runtime.vector_values(placement.Base),
        "rotation_axis": domain_runtime.vector_values(placement.Rotation.Axis),
        "rotation_angle_degrees": float(placement.Rotation.Angle) * 180.0 / 3.141592653589793,
    }


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
