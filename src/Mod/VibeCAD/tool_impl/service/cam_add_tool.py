# SPDX-License-Identifier: LGPL-2.1-or-later

"""Add one cutting tool (tool bit + controller) to an exact CAM job."""

from __future__ import annotations

from typing import Any
import math
import pathlib

from VibeCADTransactions import run_freecad_transaction

from . import domain_runtime


_SHAPE_IDS = {
    "endmill": "endmill",
    "ballend": "ballend",
    "drill": "drill",
    "chamfer": "chamfer",
    "vbit": "v-bit",
}

_COMMON_GEOMETRY = {
    "diameter_mm": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Cutting diameter in mm.",
    },
    "length_mm": {
        "type": "number",
        "exclusiveMinimum": 0,
        "description": "Overall tool length in mm.",
    },
    "flutes": {
        "type": "integer",
        "minimum": 1,
        "description": "Number of cutting flutes.",
    },
}


def _mill_geometry_schema(shape: str) -> dict[str, Any]:
    properties = {
        "shape": {"const": shape},
        **_COMMON_GEOMETRY,
        "cutting_edge_height_mm": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Axial flute/cutting-edge height in mm.",
        },
        "shank_diameter_mm": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Shank diameter in mm.",
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _conical_geometry_schema(shape: str) -> dict[str, Any]:
    properties = {
        "shape": {"const": shape},
        **_COMMON_GEOMETRY,
        "cutting_edge_height_mm": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Axial cutting-edge height in mm.",
        },
        "shank_diameter_mm": {
            "type": "number",
            "exclusiveMinimum": 0,
            "description": "Shank diameter in mm.",
        },
        "cutting_edge_angle_deg": {
            "type": "number",
            "exclusiveMinimum": 0,
            "exclusiveMaximum": 180,
            "description": "Included cutting-cone angle in degrees.",
        },
        "tip_diameter_mm": {
            "type": "number",
            "minimum": 0,
            "description": "Flat tip diameter in mm; must be below cutting diameter.",
        },
    }
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


TOOL_SPEC = {
    "name": "cam.add_tool",
    "description": (
        "Add one cutting tool to an exact CAM job: a native tool bit of the "
        "chosen shape plus a tool controller holding its number, feeds, and "
        "spindle speed. Every machining operation needs a tool controller, "
        "so add at least one tool before cam.add_operation."
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
                "description": (
                    "Visible label for the tool controller, e.g. '6mm Endmill'."
                ),
            },
            "tool_geometry": {
                "description": (
                    "Complete geometry for exactly one native tool shape. "
                    "Every shape-defining dimension is explicit; no native "
                    "template dimension is accepted implicitly."
                ),
                "oneOf": [
                    _mill_geometry_schema("endmill"),
                    _mill_geometry_schema("ballend"),
                    {
                        "type": "object",
                        "properties": {
                            "shape": {"const": "drill"},
                            **_COMMON_GEOMETRY,
                            "tip_angle_deg": {
                                "type": "number",
                                "exclusiveMinimum": 0,
                                "exclusiveMaximum": 180,
                                "description": "Included drill-point angle in degrees.",
                            },
                        },
                        "required": [
                            "shape",
                            "diameter_mm",
                            "length_mm",
                            "flutes",
                            "tip_angle_deg",
                        ],
                        "additionalProperties": False,
                    },
                    _conical_geometry_schema("chamfer"),
                    _conical_geometry_schema("vbit"),
                ],
            },
            "tool_number": {
                "type": "integer",
                "minimum": 1,
                "description": (
                    "Tool number for tool changes in the machine's tool "
                    "table; must be unique within the job."
                ),
            },
            "spindle_rpm": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": "Spindle speed in revolutions per minute.",
            },
            "horizontal_feed_mm_per_min": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": ("Feed rate in mm/min for horizontal cutting moves."),
            },
            "vertical_feed_mm_per_min": {
                "type": "number",
                "exclusiveMinimum": 0,
                "description": (
                    "Feed rate in mm/min for vertical plunge moves; "
                    "typically a third to half of the horizontal feed."
                ),
            },
        },
        "required": [
            "job_name",
            "label",
            "tool_geometry",
            "tool_number",
            "spindle_rpm",
            "horizontal_feed_mm_per_min",
            "vertical_feed_mm_per_min",
        ],
        "additionalProperties": False,
    },
}


def run(
    service: Any,
    job_name: str,
    label: str,
    tool_geometry: dict[str, Any],
    tool_number: int,
    spindle_rpm: float,
    horizontal_feed_mm_per_min: float,
    vertical_feed_mm_per_min: float,
) -> dict[str, Any]:
    clean_label = str(label or "").strip()
    if not clean_label:
        return _invalid("label is required.")
    cutting_values = {
        "spindle_rpm": float(spindle_rpm),
        "horizontal_feed_mm_per_min": float(horizontal_feed_mm_per_min),
        "vertical_feed_mm_per_min": float(vertical_feed_mm_per_min),
    }
    if any(
        not math.isfinite(value) or value <= 0.0 for value in cutting_values.values()
    ):
        return _invalid("Spindle speed and both feed rates must be finite and positive.")
    if int(tool_number) < 1:
        return _invalid("tool_number must be at least 1.")
    if not isinstance(tool_geometry, dict):
        return _invalid("tool_geometry must be one complete shape variant.")
    shape = str(tool_geometry.get("shape") or "").strip()
    shape_id = _SHAPE_IDS.get(shape)
    if shape_id is None:
        return _invalid(
            f"Unknown shape: {shape}. Choose one of: {', '.join(sorted(_SHAPE_IDS))}."
        )
    job = service._get_cam_job(str(job_name or "").strip() or None)
    if job is None:
        return _invalid(
            f"CAM job not found: {job_name}. Call cam.list_jobs for exact names."
        )
    existing_numbers = [
        int(getattr(tc, "ToolNumber", 0))
        for tc in getattr(getattr(job, "Tools", None), "Group", []) or []
    ]
    if int(tool_number) in existing_numbers:
        return _invalid(
            f"Tool number {tool_number} is already used in this job; "
            f"existing numbers: {sorted(existing_numbers)}."
        )
    geometry_error = _validate_geometry(shape, tool_geometry)
    if geometry_error:
        return _invalid(geometry_error, tool_geometry=tool_geometry)
    try:
        import Path.Tool.Controller as PathController
        from Path.Tool.toolbit import ToolBit
    except ImportError:
        return _invalid(
            "The CAM workbench is not available in this FreeCAD build; "
            "tools cannot be added."
        )
    try:
        toolbit = ToolBit.from_shape_id(shape_id, f"{clean_label} Tool Bit")
    except Exception as exc:
        return _invalid(
            f"Native tool-bit shape {shape_id} could not be loaded: {exc}",
            stage="tool_shape_load",
        )
    detached_tool = toolbit.obj
    loaded_shape_id = pathlib.Path(
        str(getattr(detached_tool, "ShapeID", "") or "")
    ).stem.casefold()
    if loaded_shape_id != shape_id:
        return _invalid(
            f"Native tool shape mismatch: requested {shape_id}, loaded {loaded_shape_id or '<missing>'}.",
            stage="tool_shape_load",
        )
    expected_values = _native_geometry_values(shape, tool_geometry)
    supported_properties = {
        name: detached_tool.getTypeIdOfProperty(name)
        for name in detached_tool.PropertiesList
    }
    missing_properties = [name for name in expected_values if name not in supported_properties]
    if missing_properties:
        return _invalid(
            "Native tool shape lacks required properties: " + ", ".join(missing_properties),
            stage="tool_shape_schema",
            native_supported_properties=supported_properties,
            required_properties=sorted(expected_values),
        )

    def create() -> dict[str, Any]:
        import FreeCAD as App
        import Path.Base.Util as PathUtil

        active = App.ActiveDocument
        if active is None:
            raise RuntimeError("No active document.")
        tool_obj = toolbit.attach_to_doc(doc=active)
        if tool_obj.ViewObject:
            tool_obj.ViewObject.Visibility = False

        def finalize_partial_tool_bit() -> None:
            """Retain a failed tool bit as one exact inspectable operation."""

            PathUtil.publishProvisionalToolBit(tool_obj)

        result = {
            "document": active.Name,
            "job": job.Name,
            "tool_bit": tool_obj.Name,
            "tool_controller": None,
            "stage": "tool_geometry_assignment",
            "shape": shape,
            "required_geometry": expected_values,
            "native_supported_properties": supported_properties,
            "created_objects": {"tool_bit": tool_obj.Name, "tool_controller": None},
            "retained_partial_state": True,
        }
        try:
            for property_name, property_value in expected_values.items():
                setattr(tool_obj, property_name, property_value)
            active.recompute()
        except Exception as exc:
            result["error"] = {
                "code": "tool_geometry_assignment_failed",
                "message": str(exc),
            }
            result["actual_geometry"] = _read_geometry(tool_obj, expected_values)
            finalize_partial_tool_bit()
            return result
        result["actual_geometry"] = _read_geometry(tool_obj, expected_values)
        if not _geometry_matches(result["actual_geometry"], expected_values):
            result["error"] = {
                "code": "tool_geometry_readback_mismatch",
                "message": "Native tool geometry did not read back exactly as requested.",
            }
            finalize_partial_tool_bit()
            return result
        result["stage"] = "controller_creation"
        try:
            resource_extension = PathUtil.stageTimelineResourceGraphExtension(
                job,
            )
        except Exception:
            finalize_partial_tool_bit()
            raise
        controller = None
        try:
            controller = PathController.Create(
                clean_label,
                tool=tool_obj,
                toolNumber=int(tool_number),
                assignViewProvider=bool(App.GuiUp),
                document=active,
                timelineOwner=job,
            )
            controller.Label = clean_label
            controller.SpindleSpeed = cutting_values["spindle_rpm"]
            controller.HorizFeed = f"{cutting_values['horizontal_feed_mm_per_min']} mm/min"
            controller.VertFeed = f"{cutting_values['vertical_feed_mm_per_min']} mm/min"
            job.Proxy.addToolController(controller)
            controller.Label = clean_label
            active.recompute()
        except Exception as exc:
            result["error"] = {
                "code": "tool_controller_creation_failed",
                "message": str(exc),
            }
            if (
                controller is not None
                and getattr(controller, "Document", None) is active
                and active.getObject(controller.Name) is controller
            ):
                job.Proxy.markToolControllerResource(controller)
                new_resources = PathUtil.toolControllerResourceGraph(
                    controller,
                )
            else:
                PathUtil.markTimelineResourceTree(tool_obj, job)
                new_resources = (
                    tool_obj,
                    *PathUtil.timelineVisualResourceGraph(tool_obj),
                )
            PathUtil.finalizeTimelineResourceGraphExtension(
                job,
                resource_extension,
                new_resources,
            )
            return result
        result.update(
            {
                "stage": "complete",
                "error": None,
                "tool_controller": controller.Name,
                "tool_controller_label": controller.Label,
                "created_objects": {
                    "tool_bit": tool_obj.Name,
                    "tool_controller": controller.Name,
                },
                "controller": {
                    "tool_number": int(controller.ToolNumber),
                    "spindle_rpm": _quantity_in(controller.SpindleSpeed, "rpm"),
                    "horizontal_feed_mm_per_min": _quantity_in(controller.HorizFeed, "mm/min"),
                    "vertical_feed_mm_per_min": _quantity_in(controller.VertFeed, "mm/min"),
                    "tool": getattr(getattr(controller, "Tool", None), "Name", None),
                },
                "job_membership": {
                    "tools_group": job.Tools.Name,
                    "members": [item.Name for item in job.Tools.Group],
                    "controller_is_member": controller in list(job.Tools.Group),
                },
                "retained_partial_state": False,
            }
        )
        PathUtil.finalizeTimelineResourceGraphExtension(
            job,
            resource_extension,
            PathUtil.toolControllerResourceGraph(controller),
        )
        return result

    def verify(result: dict[str, Any]) -> dict[str, Any]:
        tool_obj = service._active_document().getObject(str(result.get("tool_bit") or ""))
        controller = service._active_document().getObject(
            str(result.get("tool_controller") or "")
        )
        checks = [
            {"name": "native_stage_complete", "ok": result.get("stage") == "complete"},
            {"name": "tool_bit_retained", "ok": tool_obj is not None},
            {"name": "tool_controller_retained", "ok": controller is not None},
        ]
        if tool_obj is not None:
            checks.append(
                {
                    "name": "tool_geometry_readback",
                    "ok": _geometry_matches(_read_geometry(tool_obj, expected_values), expected_values),
                }
            )
        if controller is not None:
            checks.extend(
                [
                    {
                        "name": "controller_job_membership",
                        "ok": controller in list(job.Tools.Group),
                    },
                    {
                        "name": "controller_tool_link",
                        "ok": getattr(controller, "Tool", None) is tool_obj,
                    },
                    {
                        "name": "controller_number",
                        "ok": int(controller.ToolNumber) == int(tool_number),
                    },
                    {
                        "name": "controller_cutting_parameters",
                        "ok": all(
                            abs(actual - requested) <= 1.0e-9
                            for actual, requested in (
                                (_quantity_in(controller.SpindleSpeed, "rpm"), float(spindle_rpm)),
                                (_quantity_in(controller.HorizFeed, "mm/min"), float(horizontal_feed_mm_per_min)),
                                (_quantity_in(controller.VertFeed, "mm/min"), float(vertical_feed_mm_per_min)),
                            )
                        ),
                    },
                ]
            )
        return {"ok": all(item["ok"] for item in checks), "checks": checks}

    transaction = run_freecad_transaction(
        f"Add CAM tool: {clean_label}",
        create,
        verify,
    )
    return domain_runtime.build_mutation_result(
        transaction,
        extra={"operation": "add_tool"},
        next_action=(
            "Add machining operations with cam.add_operation and name this "
            "exact tool controller explicitly."
        ),
    )


def _invalid(message: str, **details: Any) -> dict[str, Any]:
    return {"ok": False, "error": message, "retry_same_call": False, **details}


def _validate_geometry(shape: str, geometry: dict[str, Any]) -> str | None:
    if shape not in _SHAPE_IDS:
        return f"Unknown shape: {shape}. Choose one of: {', '.join(sorted(_SHAPE_IDS))}."
    required = {
        "endmill": {"diameter_mm", "length_mm", "flutes", "cutting_edge_height_mm", "shank_diameter_mm"},
        "ballend": {"diameter_mm", "length_mm", "flutes", "cutting_edge_height_mm", "shank_diameter_mm"},
        "drill": {"diameter_mm", "length_mm", "flutes", "tip_angle_deg"},
        "chamfer": {"diameter_mm", "length_mm", "flutes", "cutting_edge_height_mm", "shank_diameter_mm", "cutting_edge_angle_deg", "tip_diameter_mm"},
        "vbit": {"diameter_mm", "length_mm", "flutes", "cutting_edge_height_mm", "shank_diameter_mm", "cutting_edge_angle_deg", "tip_diameter_mm"},
    }[shape]
    missing = sorted(required.difference(geometry))
    extra = sorted(set(geometry).difference(required | {"shape"}))
    if missing:
        return "tool_geometry is missing: " + ", ".join(missing)
    if extra:
        return "tool_geometry has unsupported fields: " + ", ".join(extra)
    numeric = {name: float(geometry[name]) for name in required if name != "flutes"}
    if any(not math.isfinite(value) for value in numeric.values()):
        return "Every tool dimension must be finite."
    if any(value <= 0.0 for name, value in numeric.items() if name != "tip_diameter_mm"):
        return "Tool dimensions must be positive except tip_diameter_mm, which may be zero."
    if int(geometry["flutes"]) < 1:
        return "flutes must be at least 1."
    if shape in {"endmill", "ballend", "chamfer", "vbit"}:
        if numeric["cutting_edge_height_mm"] > numeric["length_mm"]:
            return "cutting_edge_height_mm cannot exceed length_mm."
    if shape == "ballend" and numeric["cutting_edge_height_mm"] <= numeric["diameter_mm"] / 2.0:
        return "A ball-end tool needs cutting_edge_height_mm greater than its radius."
    if shape in {"drill", "chamfer", "vbit"}:
        angle_name = "tip_angle_deg" if shape == "drill" else "cutting_edge_angle_deg"
        if not 0.0 < numeric[angle_name] < 180.0:
            return f"{angle_name} must be between 0 and 180 degrees."
    if shape in {"chamfer", "vbit"}:
        if numeric["tip_diameter_mm"] < 0.0 or numeric["tip_diameter_mm"] >= numeric["diameter_mm"]:
            return "tip_diameter_mm must be nonnegative and smaller than diameter_mm."
        cone_height = (
            (numeric["diameter_mm"] - numeric["tip_diameter_mm"]) / 2.0
        ) / math.tan(math.radians(numeric["cutting_edge_angle_deg"] / 2.0))
        if cone_height > numeric["cutting_edge_height_mm"]:
            return (
                "cutting_edge_height_mm is too short for the specified cutting cone; "
                f"requires at least {cone_height:.6g} mm."
            )
    return None


def _native_geometry_values(shape: str, geometry: dict[str, Any]) -> dict[str, Any]:
    values: dict[str, Any] = {
        "Diameter": f"{float(geometry['diameter_mm'])} mm",
        "Length": f"{float(geometry['length_mm'])} mm",
        "Flutes": int(geometry["flutes"]),
    }
    if shape in {"endmill", "ballend", "chamfer", "vbit"}:
        values["CuttingEdgeHeight"] = f"{float(geometry['cutting_edge_height_mm'])} mm"
        values["ShankDiameter"] = f"{float(geometry['shank_diameter_mm'])} mm"
    if shape == "drill":
        values["TipAngle"] = f"{float(geometry['tip_angle_deg'])} deg"
    if shape in {"chamfer", "vbit"}:
        values["CuttingEdgeAngle"] = f"{float(geometry['cutting_edge_angle_deg'])} deg"
        values["TipDiameter"] = f"{float(geometry['tip_diameter_mm'])} mm"
    return values


def _read_geometry(tool_obj: Any, expected: dict[str, Any]) -> dict[str, Any]:
    return {name: _numeric(getattr(tool_obj, name)) for name in expected}


def _numeric(value: Any) -> float:
    return float(getattr(value, "Value", value))


def _quantity_in(value: Any, unit: str) -> float:
    if not hasattr(value, "getValueAs"):
        return float(value)
    converted = value.getValueAs(unit)
    return float(getattr(converted, "Value", converted))


def _geometry_matches(actual: dict[str, Any], expected: dict[str, Any]) -> bool:
    for name, requested in expected.items():
        requested_value = float(str(requested).split()[0]) if isinstance(requested, str) else float(requested)
        if abs(float(actual.get(name, math.nan)) - requested_value) > 1.0e-9:
            return False
    return True
