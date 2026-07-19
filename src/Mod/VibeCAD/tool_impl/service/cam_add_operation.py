# SPDX-License-Identifier: LGPL-2.1-or-later

"""Add one machining operation to an exact CAM job."""

from __future__ import annotations

from typing import Any
import math
import pathlib

from VibeCADTransactions import run_freecad_transaction
import VibeCADReferenceContracts as reference_contracts

from . import domain_runtime


_FACE_ITEM_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "object_name": {
                    "type": "string",
                    "description": "Exact internal name of the job model clone.",
                },
                "face_name": {
                    "type": "string",
                    "pattern": "^Face[1-9][0-9]*$",
                    "description": (
                        "Exact face on a non-scripted job model clone."
                    ),
                },
            },
            "required": ["object_name", "face_name"],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": {
                "object_name": {
                    "type": "string",
                    "description": "Exact internal name of the job model clone.",
                },
                "selection": reference_contracts.interface_selection_schema(),
            },
            "required": ["object_name", "selection"],
            "additionalProperties": False,
        },
    ]
}

_BOUNDARY_MAP = {
    "boundbox": "Boundbox",
    "stock": "Stock",
    "perimeter": "Perimeter",
}


TOOL_SPEC = {
    "name": "cam.add_operation",
    "description": (
        "Add one machining operation to an exact CAM job and generate its "
        "toolpath. Name one exact controller; controller selection is never "
        "implicit. Depths are absolute Z "
        "in the job coordinate system: start_depth_mm is where cutting "
        "begins (usually the stock top) and final_depth_mm is the deepest "
        "cut (usually the model bottom for through cuts)."
    ),
    "contextual": True,
    "safety": "SAFE_WRITE",
    "workbench": "CAMWorkbench",
    "edit_modes": ["none"],
    "parameters": {
        "type": "object",
        "properties": {
            "job_name": {
                "type": "string",
                "description": (
                    "Exact internal name of the CAM job from cam.list_jobs."
                ),
            },
            "label": {
                "type": "string",
                "description": "Visible label for the new operation.",
            },
            "start_depth_mm": {
                "type": "number",
                "description": (
                    "Absolute Z in mm where cutting starts, usually the stock top face."
                ),
            },
            "final_depth_mm": {
                "type": "number",
                "description": (
                    "Absolute Z in mm of the deepest cut; must be below start_depth_mm."
                ),
            },
            "simulation_resolution_mm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Native stock-removal height-field cell size in mm. "
                    "Smaller values resolve finer cuts but use more memory; "
                    "choose explicitly for the part and tool scale."
                ),
            },
            "tool_controller_name": {
                "type": "string",
                "description": (
                    "Exact internal name of a controller in this job. Labels "
                    "are not accepted because they are not unique."
                ),
            },
            "operation": {
                "description": "Machining strategy; choose exactly one variant.",
                "oneOf": [
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "profile",
                                "description": (
                                    "Contour cut around the model outline "
                                    "or around exact faces, with cutter "
                                    "radius compensation."
                                ),
                            },
                            "side": {
                                "type": "string",
                                "enum": ["outside", "inside"],
                                "description": (
                                    "Which side of the contour the tool "
                                    "cuts on: 'outside' for external "
                                    "profiles, 'inside' for cutouts."
                                ),
                            },
                            "step_down_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Depth of cut per pass in mm; typically "
                                    "half the tool diameter or less."
                                ),
                            },
                            "faces": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 20,
                                "items": _FACE_ITEM_SCHEMA,
                                "description": (
                                    "Exact faces whose contours to profile; "
                                    "omit to profile the whole model outline."
                                ),
                            },
                        },
                        "required": ["type", "side", "step_down_mm"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "pocket",
                                "description": (
                                    "Clear all material inside closed "
                                    "pocket faces down to the pocket floor."
                                ),
                            },
                            "faces": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 20,
                                "items": _FACE_ITEM_SCHEMA,
                                "description": (
                                    "Exact pocket floor or wall faces that "
                                    "bound the material to clear."
                                ),
                            },
                            "step_down_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Depth of cut per pass in mm; typically "
                                    "half the tool diameter or less."
                                ),
                            },
                            "step_over_percent": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 100,
                                "description": (
                                    "Sideways overlap between adjacent "
                                    "passes as percent of tool diameter; "
                                    "40-60 is typical."
                                ),
                            },
                        },
                        "required": [
                            "type",
                            "faces",
                            "step_down_mm",
                            "step_over_percent",
                        ],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "drilling",
                                "description": (
                                    "Drill every circular hole in the model "
                                    "that the operation auto-detects; use a "
                                    "drill tool."
                                ),
                            },
                            "peck_depth_mm": {
                                "type": "number",
                                "minimum": 0,
                                "description": (
                                    "Depth in mm drilled per peck before "
                                    "retracting to clear chips; 0 drills "
                                    "each hole in one plunge."
                                ),
                            },
                            "faces": {
                                "type": "array",
                                "minItems": 1,
                                "maxItems": 100,
                                "items": _FACE_ITEM_SCHEMA,
                                "description": (
                                    "Exact cylindrical or concentric-circular "
                                    "hole faces to drill. Hole auto-detection "
                                    "is intentionally not implicit."
                                ),
                            },
                        },
                        "required": ["type", "peck_depth_mm", "faces"],
                        "additionalProperties": False,
                    },
                    {
                        "type": "object",
                        "properties": {
                            "type": {
                                "const": "face",
                                "description": (
                                    "Mill the top of the stock flat down to "
                                    "start of the model, e.g. to clean up "
                                    "a rough blank."
                                ),
                            },
                            "boundary": {
                                "type": "string",
                                "enum": sorted(_BOUNDARY_MAP),
                                "description": (
                                    "Area to face: 'boundbox' covers the "
                                    "model bounding box, 'stock' the whole "
                                    "stock top, 'perimeter' the model "
                                    "outline only."
                                ),
                            },
                            "step_down_mm": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "description": (
                                    "Depth of cut per pass in mm; facing "
                                    "passes are usually shallow, 0.5-2 mm."
                                ),
                            },
                            "step_over_percent": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 100,
                                "description": (
                                    "Sideways overlap between adjacent "
                                    "passes as percent of tool diameter; "
                                    "40-60 is typical."
                                ),
                            },
                        },
                        "required": [
                            "type",
                            "boundary",
                            "step_down_mm",
                            "step_over_percent",
                        ],
                        "additionalProperties": False,
                    },
                ],
            },
        },
        "required": [
            "job_name",
            "label",
            "start_depth_mm",
            "final_depth_mm",
            "simulation_resolution_mm",
            "tool_controller_name",
            "operation",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    job_name: str,
    label: str,
    start_depth_mm: float,
    final_depth_mm: float,
    simulation_resolution_mm: float,
    operation: dict[str, Any],
    tool_controller_name: str,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    if not isinstance(operation, dict) or "type" not in operation:
        return _invalid(
            "operation must be an object with a 'type' of "
            "profile, pocket, drilling, or face."
        )
    op_type = str(operation.get("type") or "").strip()
    if op_type not in ("profile", "pocket", "drilling", "face"):
        return _invalid(
            f"Unknown operation type: {op_type}. "
            "Choose profile, pocket, drilling, or face."
        )
    start_depth = float(start_depth_mm)
    final_depth = float(final_depth_mm)
    simulation_resolution = float(simulation_resolution_mm)
    if not math.isfinite(start_depth) or not math.isfinite(final_depth):
        return _invalid("start_depth_mm and final_depth_mm must be finite.")
    if final_depth >= start_depth:
        return _invalid(
            "final_depth_mm must be below start_depth_mm "
            f"(got start {start_depth_mm}, final {final_depth_mm})."
        )
    if not math.isfinite(simulation_resolution) or simulation_resolution <= 0.0:
        return _invalid("simulation_resolution_mm must be finite and positive.")
    job = service._get_cam_job(str(job_name or "").strip() or None)
    if job is None:
        return _invalid(
            f"CAM job not found: {job_name}. Use cam.list_jobs for exact names."
        )
    controllers = list(getattr(getattr(job, "Tools", None), "Group", []) or [])
    if not controllers:
        return _invalid(
            "This job has no tool controller; add one with cam.add_tool first."
        )
    wanted = str(tool_controller_name or "").strip()
    if not wanted:
        return _invalid(
            "tool_controller_name is required; CAM operations never select a controller implicitly."
        )
    controller = next((candidate for candidate in controllers if candidate.Name == wanted), None)
    if controller is None:
        return _invalid(
            f"Tool controller not found by exact internal name in this job: {wanted}.",
            available_controllers=[
                {"name": candidate.Name, "label": candidate.Label}
                for candidate in controllers
            ],
        )
    tool = getattr(controller, "Tool", None)
    if tool is None:
        return _invalid(
            f"Tool controller {controller.Name} has no native Tool link.",
            stage="tool_preflight",
        )
    shape_id = pathlib.Path(str(getattr(tool, "ShapeID", "") or "")).stem.casefold()
    compatibility = _tool_compatibility(op_type, shape_id)
    if not compatibility["ok"]:
        return _invalid(
            compatibility["message"],
            stage="tool_preflight",
            tool={
                "controller": controller.Name,
                "tool": tool.Name,
                "shape": shape_id,
                "diameter_mm": _numeric(getattr(tool, "Diameter", 0.0)),
            },
            allowed_tool_shapes=compatibility["allowed"],
        )
    operation_error = _validate_operation_payload(op_type, operation)
    if operation_error:
        return _invalid(operation_error, stage="operation_preflight")
    doc = service._active_document()
    face_refs, face_details, error = _validate_faces(
        service, doc, job, operation.get("faces"), operation_type=op_type
    )
    if error:
        return _invalid(error, stage="base_geometry_preflight", resolved_base=face_details)
    if op_type == "pocket" and not face_refs:
        return _invalid("A pocket operation needs at least one face reference.")
    if op_type == "drilling" and not face_refs:
        return _invalid("A drilling operation needs at least one exact hole face.")
    stock = getattr(job, "Stock", None)
    stock_shape = getattr(stock, "Shape", None)
    if (
        stock_shape is None
        or stock_shape.isNull()
        or not bool(stock_shape.isValid())
        or len(list(stock_shape.Solids)) != 1
    ):
        return _invalid(
            "The CAM job does not have one valid solid stock shape.",
            stage="stock_preflight",
        )
    stock_bounds = stock_shape.BoundBox
    if start_depth > stock_bounds.ZMax + 1.0e-7 or start_depth < stock_bounds.ZMin - 1.0e-7:
        return _invalid(
            "start_depth_mm must lie within the stock Z bounds.",
            stock_z_bounds_mm=[float(stock_bounds.ZMin), float(stock_bounds.ZMax)],
            requested_start_depth_mm=start_depth,
        )
    if final_depth < stock_bounds.ZMin - 1.0e-7 or final_depth > stock_bounds.ZMax + 1.0e-7:
        return _invalid(
            "final_depth_mm must lie within the stock Z bounds.",
            stock_z_bounds_mm=[float(stock_bounds.ZMin), float(stock_bounds.ZMax)],
            requested_final_depth_mm=final_depth,
        )
    try:
        create_op = _op_factory(op_type)
    except ImportError:
        return _invalid(
            "The CAM workbench is not available in this FreeCAD build; "
            "operations cannot be added."
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App
        import Path.Main.Simulation as PathSimulation

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        op_obj = create_op(clean_label, parentJob=job)
        op_obj.Label = clean_label
        result: dict[str, Any] = {
            "document": active.Name,
            "job": job.Name,
            "operation_object": op_obj.Name,
            "operation_label": op_obj.Label,
            "operation_type": op_type,
            "tool_controller": controller.Name,
            "tool": {
                "object": tool.Name,
                "shape": shape_id,
                "diameter_mm": _numeric(tool.Diameter),
            },
            "resolved_base": face_details,
            "stage": "native_property_contract",
            "error": None,
            "retained_operation": True,
        }
        op_obj.ToolController = controller
        if face_refs:
            base: dict[str, list[str]] = {}
            for object_name, face_name in face_refs:
                base.setdefault(object_name, []).append(face_name)
            op_obj.Base = [
                (active.getObject(object_name), faces)
                for object_name, faces in base.items()
            ]
        contract_faces = _contract_face_entries(face_details)
        if any(item.get("selection") for item in contract_faces):
            reference_contracts.set_contract(
                op_obj,
                "cam_reference",
                {
                    "job_name": job.Name,
                    "operation_type": op_type,
                    "faces": contract_faces,
                },
            )
        required_properties = ["ToolController", "StartDepth", "FinalDepth", "Path"]
        if op_type in ("profile", "pocket", "face"):
            required_properties.append("StepDown")
        if op_type == "profile":
            required_properties.append("Side")
        if op_type in ("pocket", "face"):
            required_properties.append("StepOver")
        if op_type == "face":
            required_properties.append("BoundaryShape")
        if op_type == "drilling":
            required_properties.extend(["PeckEnabled", "PeckDepth"])
        missing_properties = [
            property_name
            for property_name in required_properties
            if not hasattr(op_obj, property_name)
        ]
        result["native_supported_properties"] = {
            name: op_obj.getTypeIdOfProperty(name) for name in op_obj.PropertiesList
        }
        result["required_properties"] = required_properties
        if missing_properties:
            result["error"] = {
                "code": "native_property_contract_missing",
                "message": "Native operation lacks required properties: "
                + ", ".join(missing_properties),
            }
            return result
        result["stage"] = "property_assignment"
        assignments: dict[str, Any] = {
            "StartDepth": f"{start_depth} mm",
            "FinalDepth": f"{final_depth} mm",
        }
        if op_type in ("profile", "pocket", "face"):
            assignments["StepDown"] = f"{float(operation['step_down_mm'])} mm"
        if op_type == "profile":
            assignments["Side"] = "Outside" if operation["side"] == "outside" else "Inside"
        if op_type in ("pocket", "face"):
            assignments["StepOver"] = int(operation["step_over_percent"])
        if op_type == "face":
            assignments["BoundaryShape"] = _BOUNDARY_MAP[operation["boundary"]]
        if op_type == "drilling":
            peck = float(operation["peck_depth_mm"])
            assignments["PeckEnabled"] = peck > 0.0
            assignments["PeckDepth"] = f"{peck} mm"
        expression_clears = {}
        for property_name in ("StartDepth", "FinalDepth", "StepDown", "PeckDepth"):
            if property_name not in assignments:
                continue
            try:
                op_obj.setExpression(property_name, None)
                expression_clears[property_name] = {
                    "ok": _expression_for(op_obj, property_name) is None
                }
            except Exception as exc:
                expression_clears[property_name] = {"ok": False, "error": str(exc)}
        failed_expression_clears = [
            name for name, status in expression_clears.items() if not status["ok"]
        ]
        result["expression_clears"] = expression_clears
        if failed_expression_clears:
            result["error"] = {
                "code": "native_expression_clear_failed",
                "message": "Could not clear native expressions for: "
                + ", ".join(failed_expression_clears),
            }
            return result
        try:
            for property_name, value in assignments.items():
                setattr(op_obj, property_name, value)
            active.recompute()
        except Exception as exc:
            result["error"] = {
                "code": "native_path_generation_failed",
                "message": str(exc),
                "exception_type": type(exc).__name__,
            }
            result["generation"] = _generation_diagnostics(op_obj)
            return result
        result["actual_properties"] = _read_operation_properties(op_obj, assignments)
        if not _operation_properties_match(result["actual_properties"], assignments):
            result["error"] = {
                "code": "native_property_readback_mismatch",
                "message": "Native operation properties do not match the requested values.",
            }
            result["generation"] = _generation_diagnostics(op_obj)
            return result
        commands = list(getattr(getattr(op_obj, "Path", None), "Commands", []) or [])
        command_types: dict[str, int] = {}
        for command in commands:
            name = str(command.Name)
            command_types[name] = command_types.get(name, 0) + 1
        generation = _generation_diagnostics(op_obj)
        result.update(
            {
                "stage": "native_generation",
                "generation": generation,
                "path": {
                    "command_count": len(commands),
                    "command_types": command_types,
                    "cycle_time": getattr(op_obj, "CycleTime", None),
                },
                "job_membership": {
                    "operations_group": job.Operations.Name,
                    "members": [item.Name for item in job.Operations.Group],
                    "operation_is_member": op_obj in list(job.Operations.Group),
                },
            }
        )
        if generation.get("status") != "succeeded":
            result["error"] = {
                "code": "native_generation_not_successful",
                "message": "Native path generation did not reach a successful state.",
                "native_error": generation.get("error"),
            }
            return result
        result["stage"] = "stock_and_collision_analysis"
        try:
            simulation = PathSimulation.analyze_operation(
                job,
                op_obj,
                simulation_resolution_mm=simulation_resolution,
            )
        except Exception as exc:
            result["simulation"] = {
                "complete": False,
                "stage": "native_simulation_exception",
                "error": {
                    "code": "native_simulation_exception",
                    "message": str(exc),
                    "exception_type": type(exc).__name__,
                },
            }
            result["error"] = result["simulation"]["error"]
            return result
        result["simulation"] = simulation
        if not simulation.get("complete"):
            result["error"] = {
                "code": "native_simulation_incomplete",
                "message": "Native stock-removal analysis did not complete.",
                "native_error": simulation.get("error"),
            }
            return result
        collision = simulation.get("collision", {})
        if collision.get("protected_model_collision"):
            result["error"] = {
                "code": "protected_model_collision",
                "message": "The generated cutter sweep removes protected model volume.",
                "collision_volume_mm3": collision.get("protected_model_volume_mm3"),
            }
            return result
        result["stage"] = "complete"
        return result

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        op_obj = doc.getObject(str(result.get("operation_object") or ""))
        generation = result.get("generation") or {}
        simulation = result.get("simulation") or {}
        checks = [
            {"name": "operation_retained", "ok": op_obj is not None},
            {"name": "native_stage_complete", "ok": result.get("stage") == "complete"},
            {"name": "native_generation_succeeded", "ok": generation.get("status") == "succeeded"},
            {"name": "native_path_nonempty", "ok": int(generation.get("cutting_command_count", 0) or 0) > 0},
            {"name": "simulation_complete", "ok": bool(simulation.get("complete"))},
            {
                "name": "protected_model_collision_free",
                "ok": simulation.get("collision", {}).get("protected_model_collision") is False,
            },
        ]
        if op_obj is not None:
            checks.extend(
                [
                    {
                        "name": "operations_group_membership",
                        "ok": op_obj in list(job.Operations.Group),
                    },
                    {
                        "name": "exact_tool_controller",
                        "ok": getattr(op_obj, "ToolController", None) is controller,
                    },
                ]
            )
        return {"ok": all(item["ok"] for item in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Add CAM operation: {clean_label}",
        create,
        verify,
    )
    result = domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": f"add_operation:{op_type}"},
    )
    native_result = transaction.get("result", {})
    if result.get("ok"):
        result["next_action"] = (
            "Verify the toolpath visually with a screenshot, then add the "
            "next operation. G-code postprocessing to a file is left to "
            "the user in the FreeCAD GUI."
        )
    elif native_result.get("error"):
        result["error"] = native_result["error"]
        result["retained_operation"] = native_result.get("operation_object")
        result["failure_stage"] = native_result.get("stage")
    return result


def _op_factory(op_type: str) -> Any:
    if op_type == "profile":
        import Path.Op.Profile as PathProfile

        return PathProfile.Create
    if op_type == "pocket":
        import Path.Op.PocketShape as PathPocket

        return PathPocket.Create
    if op_type == "drilling":
        import Path.Op.Drilling as PathDrilling

        return PathDrilling.Create
    import Path.Op.MillFace as PathMillFace

    return PathMillFace.Create


def _validate_faces(
    service: Any,
    doc: Any,
    job: Any,
    faces: Any,
    *,
    operation_type: str,
) -> tuple[list[tuple[str, str]], list[dict[str, Any]], str | None]:
    if not faces:
        return [], [], None
    if doc is None:
        return [], [], "No active document."
    if not isinstance(faces, list):
        return [], [], "faces must be an array of exact object/face references."
    model_names = {
        getattr(model, "Name", "")
        for model in getattr(getattr(job, "Model", None), "Group", []) or []
    }
    refs: list[tuple[str, str]] = []
    details: list[dict[str, Any]] = []
    for item in faces:
        if not isinstance(item, dict):
            return [], details, "Each face reference must be an object."
        object_name = str(item.get("object_name") or "").strip()
        face_name = str(item.get("face_name") or "").strip()
        obj = doc.getObject(object_name) if object_name else None
        if obj is None:
            return [], details, f"Object not found by exact internal name: {object_name}"
        if model_names and object_name not in model_names:
            return [], details, (
                f"Object {object_name} is not part of this job's model; "
                f"job models: {sorted(model_names)}. Reference the job's "
                "model clones, not the original objects."
            )
        selection = item.get("selection")
        linked_publication = reference_contracts.published_object(obj)
        if linked_publication is not None and not isinstance(selection, dict):
            return [], details, (
                f"{object_name} clones a regenerating scripted output. Use a "
                "published semantic interface instead of an exact FaceN name."
            )
        if isinstance(selection, dict):
            if selection.get("type") != "published_interface":
                return [], details, "CAM face selection.type must be published_interface."
            interface_name = str(selection.get("interface_name") or "").strip()
            try:
                interface = reference_contracts.resolve_interface(
                    service, obj, interface_name
                )
            except reference_contracts.ReferenceContractError as exc:
                return [], details, f"{object_name}: {exc}"
            names = list(interface.get("subelements") or [])
            geometry = list(interface.get("geometry") or [])
            if not names or len(names) != len(geometry) or any(
                not name.startswith("Face") for name in names
            ):
                return [], details, (
                    f"CAM interface {interface_name!r} must resolve only to one "
                    "or more faces."
                )
            managed_selection = {
                "type": "published_interface",
                "interface_name": interface_name,
                "model_id": interface["model_id"],
                "publication_name": interface["publication_name"],
                "output_key": interface["output_key"],
            }
            for resolved_name in names:
                try:
                    element = obj.Shape.getElement(resolved_name)
                except Exception as exc:
                    return [], details, (
                        f"The job clone does not expose {resolved_name} from "
                        f"published interface {interface_name!r}: {exc}"
                    )
                reference = (object_name, resolved_name)
                if reference in refs:
                    return [], details, f"Duplicate face reference: {object_name}.{resolved_name}"
                descriptor = _face_descriptor(obj, resolved_name, element)
                suitability = _face_suitability(operation_type, descriptor)
                descriptor["operation_suitability"] = suitability
                descriptor["managed_selection"] = managed_selection
                details.append(descriptor)
                if not suitability["ok"]:
                    return [], details, (
                        f"{object_name}.{resolved_name} is not valid for a "
                        f"{operation_type} operation: {suitability['message']}"
                    )
                refs.append(reference)
            continue
        if not face_name.startswith("Face"):
            return [], details, (
                f"Invalid face name: {face_name}. CAM base geometry must be "
                "an exact existing face name such as 'Face3'."
            )
        shape = getattr(obj, "Shape", None)
        try:
            element = shape.getElement(face_name) if shape is not None else None
        except Exception as exc:
            return [], details, (
                f"FreeCAD failed while resolving {object_name}.{face_name}: {exc}"
            )
        if element is None:
            return [], details, f"Face not found on {object_name}: {face_name}"
        reference = (object_name, face_name)
        if reference in refs:
            return [], details, f"Duplicate face reference: {object_name}.{face_name}"
        descriptor = _face_descriptor(obj, face_name, element)
        suitability = _face_suitability(operation_type, descriptor)
        descriptor["operation_suitability"] = suitability
        details.append(descriptor)
        if not suitability["ok"]:
            return [], details, (
                f"{object_name}.{face_name} is not valid for a {operation_type} operation: "
                f"{suitability['message']}"
            )
        refs.append((object_name, face_name))
    return refs, details, None


def _contract_face_entries(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for item in details:
        selection = item.get("managed_selection")
        if isinstance(selection, dict):
            key = (
                str(item.get("object") or ""),
                str(selection.get("model_id") or ""),
                str(selection.get("interface_name") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append({"object_name": key[0], "selection": dict(selection)})
        else:
            key = (
                str(item.get("object") or ""),
                "exact",
                str(item.get("face") or ""),
            )
            if key in seen:
                continue
            seen.add(key)
            result.append({"object_name": key[0], "face_name": key[2]})
    return result


def rebind_scripted_reference(
    service: Any,
    operation_obj: Any,
    contract: dict[str, Any],
) -> dict[str, Any]:
    job_name = str(contract.get("job_name") or "")
    job = service._get_cam_job(job_name)
    if job is None:
        return _invalid("The CAM job recorded by this operation no longer exists.")
    faces = contract.get("faces")
    if not isinstance(faces, list) or not faces:
        return _invalid("The CAM reference contract contains no faces.")
    doc = service._active_document()
    refs, error = _resolve_rebind_faces(service, job, faces)
    if error:
        return _invalid(error)
    grouped: dict[str, list[str]] = {}
    for object_name, face_name in refs:
        grouped.setdefault(object_name, []).append(face_name)
    try:
        operation_obj.Base = [
            (doc.getObject(object_name), names)
            for object_name, names in grouped.items()
        ]
        operation_obj.touch()
    except Exception as exc:
        return _invalid(
            "FreeCAD could not rebind the CAM operation.",
            native_error=str(exc),
        )
    actual_refs: list[tuple[str, str]] = []
    for linked_object, linked_names in list(getattr(operation_obj, "Base", []) or []):
        for linked_name in list(linked_names or []):
            actual_refs.append(
                (
                    str(getattr(linked_object, "Name", "") or ""),
                    str(linked_name or ""),
                )
            )
    if sorted(actual_refs) != sorted(refs):
        return _invalid(
            "FreeCAD did not retain the rebound CAM base references exactly.",
            requested_references=refs,
            actual_references=actual_refs,
        )
    reference_contracts.mark_stale(
        operation_obj,
        str(contract.get("source_revision") or ""),
        "A referenced scripted model changed; regenerate and verify this CAM toolpath.",
    )
    return {
        "ok": True,
        "domain": "cam_reference",
        "object": operation_obj.Name,
        "resolved_faces": actual_refs,
        "toolpath_recompute_deferred": True,
    }


def _resolve_rebind_faces(
    service: Any,
    job: Any,
    faces: list[dict[str, Any]],
) -> tuple[list[tuple[str, str]], str | None]:
    """Resolve persisted CAM interfaces without inspecting live face geometry."""

    model_names = {
        str(getattr(item, "Name", "") or "")
        for item in list(
            getattr(getattr(job, "Model", None), "Group", []) or []
        )
    }
    doc = service._active_document()
    refs: list[tuple[str, str]] = []
    for item in faces:
        if not isinstance(item, dict):
            return [], "The CAM reference contract contains a non-object face entry."
        object_name = str(item.get("object_name") or "")
        if object_name not in model_names:
            return [], f"CAM job model clone no longer exists: {object_name}"
        obj = doc.getObject(object_name) if doc is not None else None
        if obj is None:
            return [], f"CAM job model object no longer exists: {object_name}"
        selection = item.get("selection")
        if isinstance(selection, dict):
            interface_name = str(selection.get("interface_name") or "")
            try:
                interface = reference_contracts.resolve_interface(
                    service, obj, interface_name
                )
            except reference_contracts.ReferenceContractError as exc:
                return [], f"{object_name}: {exc}"
            names = list(interface.get("subelements") or [])
            if not names or any(not name.startswith("Face") for name in names):
                return [], (
                    f"CAM interface {interface_name!r} must resolve only to one "
                    "or more faces."
                )
            refs.extend((object_name, name) for name in names)
            continue
        face_name = str(item.get("face_name") or "")
        if not face_name.startswith("Face"):
            return [], f"Invalid persisted CAM face name: {face_name!r}"
        refs.append((object_name, face_name))
    if len(refs) != len(set(refs)):
        return [], "The rebound CAM face contract resolves duplicate faces."
    return refs, None


def _face_descriptor(obj: Any, face_name: str, face: Any) -> dict[str, Any]:
    surface = getattr(face, "Surface", None)
    surface_type = type(surface).__name__ if surface is not None else None
    descriptor: dict[str, Any] = {
        "object": obj.Name,
        "face": face_name,
        "surface_type": surface_type,
        "area_mm2": float(face.Area),
        "bounds": domain_runtime.bound_box_summary(face.BoundBox),
        "edge_count": len(list(face.Edges)),
        "wire_count": len(list(face.Wires)),
    }
    if surface_type == "Plane":
        normal = face.normalAt(0.0, 0.0)
        descriptor["normal"] = domain_runtime.vector_values(normal)
    if surface_type == "Cylinder":
        descriptor["cylinder"] = {
            "radius_mm": float(surface.Radius),
            "axis": domain_runtime.vector_values(surface.Axis),
            "center": domain_runtime.vector_values(surface.Center),
        }
    circular_edges = []
    for index, edge in enumerate(face.Edges, start=1):
        curve = getattr(edge, "Curve", None)
        if type(curve).__name__ == "Circle":
            circular_edges.append(
                {
                    "edge_index": index,
                    "radius_mm": float(curve.Radius),
                    "center": domain_runtime.vector_values(curve.Center),
                    "axis": domain_runtime.vector_values(curve.Axis),
                }
            )
    descriptor["circular_edges"] = circular_edges
    return descriptor


def _face_suitability(operation_type: str, descriptor: dict[str, Any]) -> dict[str, Any]:
    if operation_type == "drilling":
        if descriptor["surface_type"] == "Cylinder":
            axis = descriptor["cylinder"]["axis"]
            axial = abs(float(axis[2])) >= 1.0 - 1.0e-7
            return {
                "ok": axial,
                "message": (
                    "cylindrical hole axis is parallel to the machining Z axis"
                    if axial
                    else "cylindrical hole axis is not parallel to the machining Z axis"
                ),
            }
        circles = descriptor["circular_edges"]
        if circles:
            first = circles[0]
            concentric = all(
                _distance(first["center"], circle["center"]) <= 1.0e-7
                for circle in circles[1:]
            )
            axial = all(abs(float(circle["axis"][2])) >= 1.0 - 1.0e-7 for circle in circles)
            ok = concentric and axial
            return {
                "ok": ok,
                "message": (
                    "face has concentric circular boundaries normal to machining Z"
                    if ok
                    else "circular boundaries are not concentric and normal to machining Z"
                ),
            }
        return {
            "ok": False,
            "message": "drilling requires a cylindrical face or concentric circular face boundaries",
        }
    if operation_type == "pocket":
        if descriptor["surface_type"] != "Plane":
            return {"ok": False, "message": "pocket bases must be planar faces"}
        normal = descriptor.get("normal") or [0.0, 0.0, 0.0]
        axial = abs(float(normal[2])) >= 1.0 - 1.0e-7
        return {
            "ok": axial,
            "message": (
                "planar pocket face is normal to machining Z"
                if axial
                else "pocket face is not normal to machining Z"
            ),
        }
    return {"ok": True, "message": "native face reference is supported"}


def _distance(first: list[float], second: list[float]) -> float:
    return math.sqrt(sum((float(first[index]) - float(second[index])) ** 2 for index in range(3)))


def _tool_compatibility(operation_type: str, shape_id: str) -> dict[str, Any]:
    allowed = {
        "profile": ["endmill", "ballend", "chamfer", "v-bit"],
        "pocket": ["endmill", "ballend"],
        "drilling": ["drill"],
        "face": ["endmill", "ballend"],
    }[operation_type]
    ok = shape_id in allowed
    return {
        "ok": ok,
        "allowed": allowed,
        "message": (
            f"Tool shape {shape_id or '<missing>'} is valid for {operation_type}."
            if ok
            else f"Tool shape {shape_id or '<missing>'} cannot run a {operation_type} operation."
        ),
    }


def _validate_operation_payload(operation_type: str, operation: dict[str, Any]) -> str | None:
    fields = {
        "profile": {"type", "side", "step_down_mm", "faces"},
        "pocket": {"type", "faces", "step_down_mm", "step_over_percent"},
        "drilling": {"type", "faces", "peck_depth_mm"},
        "face": {"type", "boundary", "step_down_mm", "step_over_percent"},
    }[operation_type]
    required = {
        "profile": {"type", "side", "step_down_mm"},
        "pocket": fields,
        "drilling": fields,
        "face": fields,
    }[operation_type]
    missing = sorted(required.difference(operation))
    extra = sorted(set(operation).difference(fields))
    if missing:
        return "operation is missing: " + ", ".join(missing)
    if extra:
        return "operation has unsupported fields: " + ", ".join(extra)
    if operation_type == "profile" and operation["side"] not in {"inside", "outside"}:
        return "profile side must be inside or outside."
    if operation_type == "face" and operation["boundary"] not in _BOUNDARY_MAP:
        return "face boundary must be boundbox, stock, or perimeter."
    if operation_type in {"profile", "pocket", "face"}:
        step_down = float(operation["step_down_mm"])
        if not math.isfinite(step_down) or step_down <= 0.0:
            return "step_down_mm must be finite and positive."
    if operation_type in {"pocket", "face"}:
        step_over = int(operation["step_over_percent"])
        if not 1 <= step_over <= 100:
            return "step_over_percent must be from 1 through 100."
    if operation_type == "drilling":
        peck = float(operation["peck_depth_mm"])
        if not math.isfinite(peck) or peck < 0.0:
            return "peck_depth_mm must be finite and nonnegative."
    return None


def _generation_diagnostics(operation: Any) -> dict[str, Any]:
    proxy = getattr(operation, "Proxy", None)
    if proxy is None or not hasattr(proxy, "getGenerationDiagnostics"):
        return {
            "status": "unavailable",
            "stage": "native_diagnostic_contract",
            "error": {
                "code": "native_diagnostics_unavailable",
                "message": "The native CAM operation does not expose generation diagnostics.",
            },
        }
    diagnostics = proxy.getGenerationDiagnostics(operation)
    return diagnostics if isinstance(diagnostics, dict) else {
        "status": "invalid",
        "stage": "native_diagnostic_contract",
        "error": {
            "code": "native_diagnostics_invalid",
            "message": "The native CAM diagnostic payload is not structured data.",
        },
    }


def _read_operation_properties(operation: Any, assignments: dict[str, Any]) -> dict[str, Any]:
    actual = {}
    for name in assignments:
        value = getattr(operation, name)
        if isinstance(value, bool):
            actual[name] = value
        elif hasattr(value, "Value"):
            actual[name] = float(value.Value)
        elif isinstance(value, (int, float)):
            actual[name] = value
        else:
            actual[name] = str(value)
    return actual


def _operation_properties_match(actual: dict[str, Any], assignments: dict[str, Any]) -> bool:
    for name, requested in assignments.items():
        current = actual.get(name)
        if isinstance(requested, str) and requested.split()[-1] in {"mm", "deg"}:
            expected = float(requested.split()[0])
            if current is None or abs(float(current) - expected) > 1.0e-9:
                return False
        elif isinstance(requested, (int, float)) and not isinstance(requested, bool):
            if current is None or abs(float(current) - float(requested)) > 1.0e-9:
                return False
        elif current != requested:
            return False
    return True


def _numeric(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _expression_for(obj: Any, property_name: str) -> str | None:
    for name, expression in list(getattr(obj, "ExpressionEngine", []) or []):
        if str(name) == property_name:
            return str(expression)
    return None


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}
