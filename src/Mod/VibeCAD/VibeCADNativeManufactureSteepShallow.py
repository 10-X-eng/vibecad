# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded, task-free Native CAM Steep/Shallow creation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOCL import OCLToolFacts, validate_ocl_tool
from VibeCADNativeManufactureOperationSupport import (
    PreparedOperationBoundary,
    clean_operation_label,
    clear_operation_expressions,
    create_native_operation,
    exact_fields,
    extend_native_operation_draft,
    finite_number,
    native_operation_presentation,
    preflight_operation_boundary,
    quantity_mm,
    verify_native_operation,
)
from VibeCADNativeMutation import NativeMutationDraft


_BASE_FIELDS = frozenset(
    {
        "slope_threshold_degrees",
        "stepover_mm",
        "boundary_overlap_mm",
        "sample_interval_mm",
        "cut_mode",
        "use_rest_machining",
        "mesh",
    }
)
_REST_FIELDS = _BASE_FIELDS | frozenset({"rest_reference_tool_diameter_mm"})
_MESH_FIELDS = frozenset({"linear_deflection_mm", "angular_deflection_radians"})
_DEPTH_FIELDS = frozenset({"start_depth_mm", "final_depth_mm", "step_down_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_CUT_MODES = {"climb": "Climb", "conventional": "Conventional"}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_MAX_ESTIMATED_PROCESSING_CELLS = 250_000
_SETTING_TOLERANCE = 1.0e-7


@dataclass(frozen=True, slots=True)
class SteepShallowCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    steep_shallow: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class SteepShallowParameters:
    slope_threshold_degrees: float
    stepover_mm: float
    boundary_overlap_mm: float
    sample_interval_mm: float
    cut_mode: str
    use_rest_machining: bool
    rest_reference_tool_diameter_mm: float
    linear_deflection_mm: float
    angular_deflection_radians: float
    start_depth_mm: float
    final_depth_mm: float
    step_down_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class SteepShallowGeometryFacts:
    model_name: str
    x_span_mm: float
    y_span_mm: float
    model_top_mm: float
    model_bottom_mm: float
    layer_ceiling: int
    estimated_processing_cells: int


@dataclass(frozen=True, slots=True)
class PreparedSteepShallowCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: SteepShallowParameters
    geometry: SteepShallowGeometryFacts
    tool: OCLToolFacts


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _boolean(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{noun} must be true or false.")
    return value


def _positive(
    value: Any,
    noun: str,
    *,
    minimum: float = 0.0,
    maximum: float = 1_000_000.0,
) -> float:
    result = finite_number(value, noun, minimum=minimum, maximum=maximum)
    if result <= 0.0:
        _error(f"{noun} must be greater than zero.")
    return result


def _normalize_parameters(spec: SteepShallowCreateSpec) -> SteepShallowParameters:
    if not isinstance(spec.steep_shallow, Mapping):
        _error("Steep/Shallow settings must be one closed settings object.")
    if "rest_reference_tool_diameter_mm" in set(spec.steep_shallow):
        settings = exact_fields(
            spec.steep_shallow,
            _REST_FIELDS,
            "Steep/Shallow rest-machining settings",
        )
        use_rest = _boolean(
            settings["use_rest_machining"],
            "Steep/Shallow use_rest_machining",
        )
        if not use_rest:
            _error(
                "Steep/Shallow rest_reference_tool_diameter_mm requires "
                "use_rest_machining to be true."
            )
        rest_diameter = _positive(
            settings["rest_reference_tool_diameter_mm"],
            "Steep/Shallow rest reference tool diameter",
            minimum=0.001,
        )
    else:
        settings = exact_fields(
            spec.steep_shallow,
            _BASE_FIELDS,
            "Steep/Shallow settings",
        )
        use_rest = _boolean(
            settings["use_rest_machining"],
            "Steep/Shallow use_rest_machining",
        )
        if use_rest:
            _error(
                "Steep/Shallow use_rest_machining requires "
                "rest_reference_tool_diameter_mm."
            )
        rest_diameter = 0.0
    slope = finite_number(
        settings["slope_threshold_degrees"],
        "Steep/Shallow slope threshold",
        minimum=0.0,
        maximum=90.0,
    )
    stepover = _positive(
        settings["stepover_mm"],
        "Steep/Shallow stepover",
        minimum=0.001,
    )
    overlap = finite_number(
        settings["boundary_overlap_mm"],
        "Steep/Shallow boundary overlap",
        minimum=0.0,
    )
    sample_interval = _positive(
        settings["sample_interval_mm"],
        "Steep/Shallow sample interval",
        minimum=0.001,
    )
    cut_mode = str(settings["cut_mode"] or "")
    if cut_mode not in _CUT_MODES:
        _error("Steep/Shallow cut_mode must be climb or conventional.")
    mesh = exact_fields(settings["mesh"], _MESH_FIELDS, "Steep/Shallow mesh settings")
    linear_deflection = _positive(
        mesh["linear_deflection_mm"],
        "Steep/Shallow linear deflection",
        minimum=0.001,
        maximum=25.4,
    )
    angular_deflection = _positive(
        mesh["angular_deflection_radians"],
        "Steep/Shallow angular deflection",
        minimum=0.001,
        maximum=1.570796327,
    )
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Steep/Shallow depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Steep/Shallow heights")
    start_depth = finite_number(depths["start_depth_mm"], "Steep/Shallow start depth")
    final_depth = finite_number(depths["final_depth_mm"], "Steep/Shallow final depth")
    if final_depth >= start_depth:
        _error("Steep/Shallow final_depth_mm must be below start_depth_mm.")
    step_down = _positive(depths["step_down_mm"], "Steep/Shallow step down")
    safe = finite_number(heights["safe_height_mm"], "Steep/Shallow safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Steep/Shallow clearance height",
    )
    if safe < start_depth:
        _error("Steep/Shallow safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Steep/Shallow clearance_height_mm must be at or above safe_height_mm.")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Steep/Shallow coolant must be none, flood, or mist.")
    return SteepShallowParameters(
        slope_threshold_degrees=slope,
        stepover_mm=stepover,
        boundary_overlap_mm=overlap,
        sample_interval_mm=sample_interval,
        cut_mode=cut_mode,
        use_rest_machining=use_rest,
        rest_reference_tool_diameter_mm=rest_diameter,
        linear_deflection_mm=linear_deflection,
        angular_deflection_radians=angular_deflection,
        start_depth_mm=start_depth,
        final_depth_mm=final_depth,
        step_down_mm=step_down,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        coolant=coolant,
    )


def _valid_shape(shape: Any) -> bool:
    return bool(
        shape is not None
        and not bool(getattr(shape, "isNull", lambda: True)())
        and bool(shape.isValid())
    )


def _inspect_geometry(
    boundary: PreparedOperationBoundary,
    parameters: SteepShallowParameters,
    tool: OCLToolFacts,
) -> SteepShallowGeometryFacts:
    models = boundary.geometry
    if len(models) != 1:
        _error(
            "Steep/Shallow requires an exact CAM Job with one model; the "
            "shipped operation machines only the first model and silently "
            "ignores every model after it.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    item = models[0]
    shape = getattr(item.job_resource, "Shape", None)
    if not _valid_shape(shape):
        _error(
            "Steep/Shallow requires one valid Job model shape.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    bounds = shape.BoundBox
    x_span = float(bounds.XLength)
    y_span = float(bounds.YLength)
    if math.hypot(x_span, y_span) <= _SETTING_TOLERANCE:
        _error(
            "The Steep/Shallow Job model has no usable XY projection.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    model_top = float(bounds.ZMax)
    model_bottom = float(bounds.ZMin)
    if parameters.start_depth_mm < model_top:
        _error(
            "Steep/Shallow start_depth_mm must be at or above the exact model "
            f"top of {model_top:g} mm."
        )
    if parameters.final_depth_mm < model_bottom:
        _error(
            "Steep/Shallow final_depth_mm cannot be below the exact model "
            f"bottom of {model_bottom:g} mm."
        )
    if (
        parameters.use_rest_machining
        and parameters.rest_reference_tool_diameter_mm
        <= tool.diameter_mm + _SETTING_TOLERANCE
    ):
        _error(
            "Steep/Shallow rest_reference_tool_diameter_mm must exceed the "
            f"exact cutter diameter of {tool.diameter_mm:g} mm; a reference "
            "tool at or below the current tool leaves no rest material."
        )
    sampled_x = x_span + tool.diameter_mm
    sampled_y = y_span + tool.diameter_mm
    n_x = max(2, math.ceil(sampled_x / parameters.sample_interval_mm) + 1)
    n_y = max(2, math.ceil(sampled_y / parameters.sample_interval_mm) + 1)
    cells = n_x * n_y
    if cells > _MAX_ESTIMATED_PROCESSING_CELLS:
        _error(
            "Steep/Shallow would require approximately "
            f"{cells:,} OpenCamLib sampling cells, above the synchronous "
            f"safety limit of {_MAX_ESTIMATED_PROCESSING_CELLS:,}. Increase "
            "the sample interval or machine a smaller model so the VibeCAD "
            "UI remains responsive.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    layers = max(
        1,
        math.ceil(
            (parameters.start_depth_mm - parameters.final_depth_mm)
            / parameters.step_down_mm
        ),
    )
    return SteepShallowGeometryFacts(
        model_name=str(item.public_source.Name),
        x_span_mm=round(x_span, 9),
        y_span_mm=round(y_span, 9),
        model_top_mm=round(model_top, 9),
        model_bottom_mm=round(model_bottom, 9),
        layer_ceiling=layers,
        estimated_processing_cells=cells,
    )


def preflight_steep_shallow_create(
    document: Any,
    spec: SteepShallowCreateSpec,
) -> PreparedSteepShallowCreate:
    """Freeze the exact Job, model, cutter, parameters, and bounded work."""

    if not isinstance(spec, SteepShallowCreateSpec):
        raise TypeError("spec must be a SteepShallowCreateSpec")
    parameters = _normalize_parameters(spec)
    boundary = preflight_operation_boundary(
        document,
        noun="Steep/Shallow",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry={"kind": "entire_job"},
        allowed_subelement_types=frozenset({"Face"}),
        allow_entire_job=True,
    )
    tool = validate_ocl_tool(boundary, noun="Steep/Shallow")
    geometry = _inspect_geometry(boundary, parameters, tool)
    return PreparedSteepShallowCreate(
        label=clean_operation_label(spec.label, "Steep Shallow"),
        boundary=boundary,
        parameters=parameters,
        geometry=geometry,
        tool=tool,
    )


def _parameter_payload(prepared: PreparedSteepShallowCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    settings: dict[str, Any] = {
        "slope_threshold_degrees": parameters.slope_threshold_degrees,
        "stepover_mm": parameters.stepover_mm,
        "boundary_overlap_mm": parameters.boundary_overlap_mm,
        "sample_interval_mm": parameters.sample_interval_mm,
        "cut_mode": parameters.cut_mode,
        "use_rest_machining": parameters.use_rest_machining,
        "mesh": {
            "linear_deflection_mm": parameters.linear_deflection_mm,
            "angular_deflection_radians": parameters.angular_deflection_radians,
        },
    }
    if parameters.use_rest_machining:
        settings["rest_reference_tool_diameter_mm"] = (
            parameters.rest_reference_tool_diameter_mm
        )
    return {
        "steep_shallow": settings,
        "depths": {
            "start_depth_mm": parameters.start_depth_mm,
            "final_depth_mm": parameters.final_depth_mm,
            "step_down_mm": parameters.step_down_mm,
        },
        "heights": {
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
        "coolant": parameters.coolant,
    }


def _apply_settings(
    operation: Any,
    *,
    prepared: PreparedSteepShallowCreate,
) -> None:
    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        ("StartDepth", "FinalDepth", "StepDown", "SafeHeight", "ClearanceHeight"),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.SlopeThreshold = parameters.slope_threshold_degrees
    operation.StepOver = f"{parameters.stepover_mm} mm"
    operation.BoundaryOverlap = f"{parameters.boundary_overlap_mm} mm"
    operation.CutMode = _CUT_MODES[parameters.cut_mode]
    operation.SampleInterval = f"{parameters.sample_interval_mm} mm"
    operation.UseRestMachining = parameters.use_rest_machining
    operation.RestReferenceToolDiameter = (
        f"{parameters.rest_reference_tool_diameter_mm} mm"
    )
    operation.LinearDeflection = f"{parameters.linear_deflection_mm} mm"
    operation.AngularDeflection = parameters.angular_deflection_radians
    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]


def create_steep_shallow(
    document: Any,
    *,
    prepared: PreparedSteepShallowCreate,
) -> NativeMutationDraft:
    """Create one shipped Steep/Shallow operation in the owned transaction."""

    if not isinstance(prepared, PreparedSteepShallowCreate):
        raise TypeError("prepared must be a PreparedSteepShallowCreate")
    import Path.Op.SteepShallow as PathSteepShallow

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.SteepShallow"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="SteepShallow",
        operation_factory=PathSteepShallow.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, steep_shallow_prepared=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    return next(
        (
            expression
            for path, expression in tuple(
                getattr(operation, "ExpressionEngine", ()) or ()
            )
            if str(path).lstrip(".") == property_name
        ),
        None,
    )


def _same_number(actual: float, expected: float) -> bool:
    return abs(float(actual) - float(expected)) <= _SETTING_TOLERANCE


def _assert_steep_shallow_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedSteepShallowCreate,
) -> None:
    parameters = prepared.parameters
    expected = {
        "slope_threshold_degrees": parameters.slope_threshold_degrees,
        "stepover_mm": parameters.stepover_mm,
        "boundary_overlap_mm": parameters.boundary_overlap_mm,
        "cut_mode": _CUT_MODES[parameters.cut_mode],
        "sample_interval_mm": parameters.sample_interval_mm,
        "use_rest_machining": parameters.use_rest_machining,
        "rest_reference_tool_diameter_mm": (parameters.rest_reference_tool_diameter_mm),
        "linear_deflection_mm": parameters.linear_deflection_mm,
        "angular_deflection_radians": parameters.angular_deflection_radians,
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
    }
    actual = {
        "slope_threshold_degrees": round(float(operation.SlopeThreshold.Value), 9),
        "stepover_mm": quantity_mm(operation, "StepOver"),
        "boundary_overlap_mm": quantity_mm(operation, "BoundaryOverlap"),
        "cut_mode": str(operation.CutMode),
        "sample_interval_mm": quantity_mm(operation, "SampleInterval"),
        "use_rest_machining": bool(operation.UseRestMachining),
        "rest_reference_tool_diameter_mm": quantity_mm(
            operation,
            "RestReferenceToolDiameter",
        ),
        "linear_deflection_mm": quantity_mm(operation, "LinearDeflection"),
        "angular_deflection_radians": quantity_mm(operation, "AngularDeflection"),
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "coolant": str(operation.CoolantMode),
    }
    mismatches = {}
    for name, expected_value in expected.items():
        actual_value = actual[name]
        if isinstance(expected_value, bool) or isinstance(actual_value, bool):
            matches = actual_value is expected_value
        elif isinstance(expected_value, float) and isinstance(
            actual_value, (float, int)
        ):
            matches = _same_number(actual_value, expected_value)
        else:
            matches = actual_value == expected_value
        if not matches:
            mismatches[name] = {"expected": expected_value, "actual": actual_value}
    for property_name in (
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "SafeHeight",
        "ClearanceHeight",
    ):
        expression = _expression(operation, property_name)
        if expression:
            mismatches[f"{property_name}_expression"] = {
                "expected": None,
                "actual": str(expression),
            }
    if mismatches:
        raise NativeManufactureError(
            "The created Steep/Shallow did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_STEEP_SHALLOW_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _steep_shallow_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedSteepShallowCreate,
) -> Mapping[str, Any]:
    cutting_z = []
    current_z = None
    cutting_commands = 0
    for command in tuple(operation.Path.Commands):
        values = command.Parameters
        if "Z" in values:
            current_z = float(values["Z"])
        if str(command.Name) in {"G1", "G2", "G3"} and current_z is not None:
            cutting_z.append(current_z)
            cutting_commands += 1
    if not cutting_z:
        _error(
            "The created Steep/Shallow has no depth-bearing cutting moves.",
            "NATIVE_MANUFACTURE_STEEP_SHALLOW_POSTCONDITION_FAILED",
        )
    minimum_z = min(cutting_z)
    if minimum_z < prepared.parameters.final_depth_mm - _SETTING_TOLERANCE:
        _error(
            "The created Steep/Shallow cut below its exact final depth of "
            f"{prepared.parameters.final_depth_mm:g} mm.",
            "NATIVE_MANUFACTURE_STEEP_SHALLOW_POSTCONDITION_FAILED",
        )
    return {
        "target_mode": "entire_job",
        "model_name": prepared.geometry.model_name,
        "tool_shape_type": prepared.tool.shape_type,
        "ocl_cutter": prepared.tool.ocl_cutter,
        "tool_diameter_mm": prepared.tool.diameter_mm,
        "use_rest_machining": prepared.parameters.use_rest_machining,
        "rest_reference_tool_diameter_mm": (
            prepared.parameters.rest_reference_tool_diameter_mm
        ),
        "layer_ceiling": prepared.geometry.layer_ceiling,
        "estimated_processing_cells": (prepared.geometry.estimated_processing_cells),
        "cutting_command_count": cutting_commands,
        "minimum_cutting_z_mm": round(minimum_z, 9),
        "maximum_cutting_z_mm": round(max(cutting_z), 9),
    }


def verify_created_steep_shallow(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSteepShallowCreate = draft.value["steep_shallow_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="steep_shallow",
        assert_settings=partial(_assert_steep_shallow_settings, prepared=prepared),
        additional_verify=partial(_steep_shallow_result, prepared=prepared),
    )
