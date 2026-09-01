# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM 3D Pocket operation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import (
    PreparedOperationBoundary,
    clean_operation_label,
    clear_operation_expressions,
    create_native_operation,
    exact_fields,
    extend_native_operation_draft,
    finite_number,
    has_prior_cutting_operation,
    native_operation_presentation,
    preflight_operation_boundary,
    quantity_mm,
    validate_operation_tool,
    verify_native_operation,
)
from VibeCADNativeManufacturePocketGeometry import (
    PocketFeatureFacts,
    validate_pocket_feature_geometry,
)
from VibeCADNativeMutation import NativeMutationDraft


_POCKET_FIELDS = frozenset(
    {
        "cut_mode",
        "pattern",
        "stepover_percent",
        "pass_extension_mm",
        "rest_machining",
        "start",
    }
)
_DEPTH_FIELDS = frozenset({"start_depth_mm", "step_down_mm", "finish_step_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_POINT_FIELDS = frozenset({"x_mm", "y_mm", "z_mm"})
_PATTERN_NAMES = {
    "offset": "Offset",
    "zigzag": "ZigZag",
    "zigzag_offset": "ZigZagOffset",
    "line": "Line",
    "grid": "Grid",
}
_CUT_MODES = {"climb": "Climb", "conventional": "Conventional"}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_CUTTING_DEPTH_TOLERANCE_MM = 1.0e-5


@dataclass(frozen=True, slots=True)
class Pocket3DCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    pocket: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class Pocket3DParameters:
    cut_mode: str
    pattern: str
    angle_degrees: float | None
    stepover_percent: int
    pass_extension_mm: float
    rest_machining: bool
    use_start_point: bool
    start_point_mm: tuple[float, float, float]
    minimize_travel: bool
    start_depth_mm: float
    step_down_mm: float
    finish_step_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class Pocket3DGeometryFacts:
    features: PocketFeatureFacts
    derived_source_top_mm: float
    derived_final_depth_mm: float


@dataclass(frozen=True, slots=True)
class PreparedPocket3DCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: Pocket3DParameters
    geometry: Pocket3DGeometryFacts
    tool_diameter_mm: float
    stepover_mm: float


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _boolean(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{noun} must be true or false.")
    return value


def _positive(value: Any, noun: str) -> float:
    result = finite_number(value, noun, minimum=0.0)
    if result <= 0.0:
        _error(f"{noun} must be greater than zero.")
    return result


def _normalize_pattern(raw: Any) -> tuple[str, float | None]:
    if not isinstance(raw, Mapping):
        _error("3D Pocket pattern must be one closed pattern request.")
    kind = str(raw.get("kind") or "")
    if kind == "offset":
        exact_fields(raw, frozenset({"kind"}), "3D Pocket offset pattern")
        return kind, None
    if kind in {"zigzag", "zigzag_offset", "line", "grid"}:
        exact_fields(
            raw,
            frozenset({"kind", "angle_degrees"}),
            f"3D Pocket {kind} pattern",
        )
        return kind, finite_number(
            raw["angle_degrees"],
            "3D Pocket pattern angle",
            minimum=-360_000.0,
            maximum=360_000.0,
        )
    _error(
        "3D Pocket pattern kind must be offset, zigzag, zigzag_offset, line, or grid."
    )


def _normalize_start(raw: Any) -> tuple[bool, tuple[float, float, float], bool]:
    if not isinstance(raw, Mapping):
        _error("3D Pocket start must be one closed start request.")
    kind = str(raw.get("kind") or "")
    if kind == "automatic":
        exact_fields(raw, frozenset({"kind"}), "3D Pocket automatic start")
        return False, (0.0, 0.0, 0.0), False
    if kind != "point":
        _error("3D Pocket start kind must be automatic or point.")
    exact_fields(
        raw,
        frozenset({"kind", "point_mm", "minimize_travel"}),
        "3D Pocket point start",
    )
    point = exact_fields(raw["point_mm"], _POINT_FIELDS, "3D Pocket start point")
    return (
        True,
        tuple(
            finite_number(point[field], f"3D Pocket start point {field}")
            for field in ("x_mm", "y_mm", "z_mm")
        ),
        _boolean(raw["minimize_travel"], "3D Pocket minimize_travel"),
    )


def _normalize_parameters(spec: Pocket3DCreateSpec) -> Pocket3DParameters:
    pocket = exact_fields(spec.pocket, _POCKET_FIELDS, "3D Pocket settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "3D Pocket depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "3D Pocket heights")
    cut_mode = str(pocket["cut_mode"] or "")
    if cut_mode not in _CUT_MODES:
        _error("3D Pocket cut_mode must be climb or conventional.")
    pattern, angle = _normalize_pattern(pocket["pattern"])
    stepover = pocket["stepover_percent"]
    if (
        isinstance(stepover, bool)
        or not isinstance(stepover, int)
        or not 1 <= stepover <= 100
    ):
        _error("3D Pocket stepover_percent must be an integer from 1 through 100.")
    use_start, start_point, minimize_travel = _normalize_start(pocket["start"])
    start_depth = finite_number(depths["start_depth_mm"], "3D Pocket start depth")
    step_down = _positive(depths["step_down_mm"], "3D Pocket step down")
    finish_step = finite_number(
        depths["finish_step_mm"],
        "3D Pocket finish step",
        minimum=0.0,
    )
    if finish_step > step_down:
        _error("3D Pocket finish_step_mm cannot exceed step_down_mm.")
    safe = finite_number(heights["safe_height_mm"], "3D Pocket safe height")
    clearance = finite_number(
        heights["clearance_height_mm"], "3D Pocket clearance height"
    )
    if safe < start_depth:
        _error("3D Pocket safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("3D Pocket clearance_height_mm must be at or above safe_height_mm.")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("3D Pocket coolant must be none, flood, or mist.")
    return Pocket3DParameters(
        cut_mode=cut_mode,
        pattern=pattern,
        angle_degrees=angle,
        stepover_percent=stepover,
        pass_extension_mm=finite_number(
            pocket["pass_extension_mm"], "3D Pocket pass extension"
        ),
        rest_machining=_boolean(pocket["rest_machining"], "3D Pocket rest_machining"),
        use_start_point=use_start,
        start_point_mm=start_point,
        minimize_travel=minimize_travel,
        start_depth_mm=start_depth,
        step_down_mm=step_down,
        finish_step_mm=finish_step,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        coolant=coolant,
    )


def _feature_final_depth(model_bounds: Any, feature_bounds: Any) -> float:
    if (
        feature_bounds.ZMax == feature_bounds.ZMin
        and feature_bounds.ZMax == model_bounds.ZMax
    ):
        return float(feature_bounds.ZMin)
    if (
        feature_bounds.ZMax > feature_bounds.ZMin
        and feature_bounds.ZMax == model_bounds.ZMax
    ):
        return float(feature_bounds.ZMin)
    if (
        feature_bounds.ZMax > feature_bounds.ZMin
        and feature_bounds.ZMin > model_bounds.ZMin
    ):
        return float(feature_bounds.ZMin)
    if (
        feature_bounds.ZMax == feature_bounds.ZMin
        and feature_bounds.ZMax > model_bounds.ZMin
    ):
        return float(feature_bounds.ZMin)
    return float(model_bounds.ZMin)


def _geometry_facts(
    boundary: PreparedOperationBoundary,
    parameters: Pocket3DParameters,
) -> Pocket3DGeometryFacts:
    features = validate_pocket_feature_geometry(boundary, noun="3D Pocket")
    stock_shape = getattr(getattr(boundary.job, "Stock", None), "Shape", None)
    if (
        stock_shape is None
        or bool(getattr(stock_shape, "isNull", lambda: True)())
        or not bool(stock_shape.isValid())
    ):
        _error(
            "3D Pocket requires a valid exact Job stock shape.",
            "NATIVE_MANUFACTURE_JOB_GRAPH_INVALID",
        )
    stock_bounds = stock_shape.BoundBox
    source_top = float(stock_bounds.ZMax)
    final_depth = float(stock_bounds.ZMin)
    for item in boundary.geometry:
        resource_shape = item.job_resource.Shape
        model_bounds = resource_shape.BoundBox
        source_top = max(source_top, float(model_bounds.ZMax))
        for name in item.subelements:
            bounds = resource_shape.getElement(name).BoundBox
            source_top = max(source_top, float(bounds.ZMax))
            final_depth = max(
                final_depth,
                _feature_final_depth(model_bounds, bounds),
            )
    source_top = round(source_top, 9)
    final_depth = round(final_depth, 9)
    if parameters.start_depth_mm < source_top:
        _error(
            "3D Pocket start_depth_mm must be at or above the derived source top "
            f"of {source_top:g} mm."
        )
    if parameters.start_depth_mm <= final_depth:
        _error(
            "3D Pocket start_depth_mm must be above the derived final depth "
            f"of {final_depth:g} mm."
        )
    return Pocket3DGeometryFacts(
        features=features,
        derived_source_top_mm=source_top,
        derived_final_depth_mm=final_depth,
    )


def preflight_pocket_3d_create(
    document: Any,
    spec: Pocket3DCreateSpec,
) -> PreparedPocket3DCreate:
    """Freeze exact features, derived final depth, controller, and task values."""

    if not isinstance(spec, Pocket3DCreateSpec):
        raise TypeError("spec must be a Pocket3DCreateSpec")
    parameters = _normalize_parameters(spec)
    boundary = preflight_operation_boundary(
        document,
        noun="3D Pocket",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=spec.geometry,
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=False,
    )
    geometry = _geometry_facts(boundary, parameters)
    if parameters.rest_machining and not has_prior_cutting_operation(boundary):
        _error(
            "3D Pocket rest_machining requires an earlier active cutting "
            "operation in the exact CAM Job."
        )
    tool_diameter = validate_operation_tool(boundary)
    stepover = round(tool_diameter * parameters.stepover_percent / 100.0, 9)
    if not math.isfinite(stepover) or stepover <= 0.0:
        _error(
            "The exact tool and stepover do not produce a usable lateral cut.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return PreparedPocket3DCreate(
        label=clean_operation_label(spec.label, "3D Pocket"),
        boundary=boundary,
        parameters=parameters,
        geometry=geometry,
        tool_diameter_mm=tool_diameter,
        stepover_mm=stepover,
    )


def _parameter_payload(prepared: PreparedPocket3DCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    pattern: dict[str, Any] = {"kind": parameters.pattern}
    if parameters.angle_degrees is not None:
        pattern["angle_degrees"] = parameters.angle_degrees
    start: dict[str, Any] = {"kind": "automatic"}
    if parameters.use_start_point:
        start = {
            "kind": "point",
            "point_mm": dict(zip(("x_mm", "y_mm", "z_mm"), parameters.start_point_mm)),
            "minimize_travel": parameters.minimize_travel,
        }
    return {
        "pocket": {
            "cut_mode": parameters.cut_mode,
            "pattern": pattern,
            "stepover_percent": parameters.stepover_percent,
            "pass_extension_mm": parameters.pass_extension_mm,
            "rest_machining": parameters.rest_machining,
            "start": start,
        },
        "depths": {
            "start_depth_mm": parameters.start_depth_mm,
            "derived_final_depth_mm": prepared.geometry.derived_final_depth_mm,
            "step_down_mm": parameters.step_down_mm,
            "finish_step_mm": parameters.finish_step_mm,
        },
        "heights": {
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
        "coolant": parameters.coolant,
    }


def _apply_settings(operation: Any, prepared: PreparedPocket3DCreate) -> None:
    import FreeCAD as App

    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        (
            "RetractThreshold",
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "FinishDepth",
            "SafeHeight",
            "ClearanceHeight",
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.CutMode = _CUT_MODES[parameters.cut_mode]
    operation.ClearingPattern = _PATTERN_NAMES[parameters.pattern]
    operation.Angle = (
        parameters.angle_degrees if parameters.angle_degrees is not None else 45.0
    )
    operation.StepOver = parameters.stepover_percent
    operation.ExtraOffset = f"{parameters.pass_extension_mm} mm"
    operation.UseRestMachining = parameters.rest_machining
    operation.UseStartPoint = parameters.use_start_point
    operation.StartPoint = App.Vector(*parameters.start_point_mm)
    operation.MinTravel = parameters.minimize_travel
    operation.StartAt = "Center"
    operation.SortingMode = "Automatic"
    operation.ForceMaxStepOver = False
    operation.SplitArcs = False
    operation.RetractThreshold = (
        f"{prepared.tool_diameter_mm} mm" if parameters.minimize_travel else "0 mm"
    )
    operation.HandleMultipleFeatures = "Collectively"
    operation.AdaptivePocketStart = False
    operation.AdaptivePocketFinish = False
    operation.ProcessStockArea = False
    operation.OpStartDepth = f"{prepared.geometry.derived_source_top_mm} mm"
    operation.OpFinalDepth = f"{prepared.geometry.derived_final_depth_mm} mm"
    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{prepared.geometry.derived_final_depth_mm} mm"
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.FinishDepth = f"{parameters.finish_step_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]
    operation.Workplane = App.Vector(0.0, 0.0, 1.0)


def create_pocket_3d(
    document: Any,
    *,
    prepared: PreparedPocket3DCreate,
) -> NativeMutationDraft:
    """Create one native 3D Pocket inside the owned document transaction."""

    if not isinstance(prepared, PreparedPocket3DCreate):
        raise TypeError("prepared must be a PreparedPocket3DCreate")
    import Path.Op.Pocket as PathPocket

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Pocket"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Pocket3D",
        operation_factory=PathPocket.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, pocket_3d_prepared=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    matches = tuple(
        expression
        for path, expression in tuple(getattr(operation, "ExpressionEngine", ()) or ())
        if str(path).lstrip(".") == property_name
    )
    return matches[0] if matches else None


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _assert_pocket_3d_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedPocket3DCreate,
) -> None:
    parameters = prepared.parameters
    actual = {
        "cut_mode": str(operation.CutMode),
        "pattern": str(operation.ClearingPattern),
        "angle_degrees": round(float(operation.Angle), 9),
        "stepover_percent": int(operation.StepOver),
        "pass_extension_mm": quantity_mm(operation, "ExtraOffset"),
        "rest_machining": bool(operation.UseRestMachining),
        "use_start_point": bool(operation.UseStartPoint),
        "start_point_mm": _vector_tuple(operation.StartPoint),
        "minimize_travel": bool(operation.MinTravel),
        "start_at": str(operation.StartAt),
        "sorting": str(operation.SortingMode),
        "force_max_stepover": bool(operation.ForceMaxStepOver),
        "split_arcs": bool(operation.SplitArcs),
        "retract_threshold_mm": quantity_mm(operation, "RetractThreshold"),
        "multiple_features": str(operation.HandleMultipleFeatures),
        "adaptive_start": bool(operation.AdaptivePocketStart),
        "adaptive_finish": bool(operation.AdaptivePocketFinish),
        "process_stock": bool(operation.ProcessStockArea),
        "derived_source_top_mm": quantity_mm(operation, "OpStartDepth"),
        "derived_final_depth_mm": quantity_mm(operation, "OpFinalDepth"),
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "finish_step_mm": quantity_mm(operation, "FinishDepth"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "coolant": str(operation.CoolantMode),
        "workplane": _vector_tuple(operation.Workplane),
    }
    expected = {
        "cut_mode": _CUT_MODES[parameters.cut_mode],
        "pattern": _PATTERN_NAMES[parameters.pattern],
        "angle_degrees": (
            parameters.angle_degrees if parameters.angle_degrees is not None else 45.0
        ),
        "stepover_percent": parameters.stepover_percent,
        "pass_extension_mm": parameters.pass_extension_mm,
        "rest_machining": parameters.rest_machining,
        "use_start_point": parameters.use_start_point,
        "start_point_mm": parameters.start_point_mm,
        "minimize_travel": parameters.minimize_travel,
        "start_at": "Center",
        "sorting": "Automatic",
        "force_max_stepover": False,
        "split_arcs": False,
        "retract_threshold_mm": (
            prepared.tool_diameter_mm if parameters.minimize_travel else 0.0
        ),
        "multiple_features": "Collectively",
        "adaptive_start": False,
        "adaptive_finish": False,
        "process_stock": False,
        "derived_source_top_mm": prepared.geometry.derived_source_top_mm,
        "derived_final_depth_mm": prepared.geometry.derived_final_depth_mm,
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": prepared.geometry.derived_final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "finish_step_mm": parameters.finish_step_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
        "workplane": (0.0, 0.0, 1.0),
    }
    mismatches = {
        name: {"expected": expected_value, "actual": actual.get(name)}
        for name, expected_value in expected.items()
        if actual.get(name) != expected_value
    }
    for property_name in (
        "RetractThreshold",
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "FinishDepth",
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
            "The created 3D Pocket did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_POCKET_3D_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _pocket_3d_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedPocket3DCreate,
) -> Mapping[str, Any]:
    removal = getattr(operation, "removalshape", None)
    removal_valid = bool(
        removal is not None
        and not bool(getattr(removal, "isNull", lambda: True)())
        and bool(removal.isValid())
        and float(removal.Volume) > 0.0
    )
    if not prepared.parameters.rest_machining and not removal_valid:
        _error(
            "The created 3D Pocket did not retain a valid removal volume.",
            "NATIVE_MANUFACTURE_POCKET_3D_POSTCONDITION_FAILED",
        )
    current_z = None
    effective_cutting_z = []
    for command in tuple(operation.Path.Commands):
        parameters = command.Parameters
        if "Z" in parameters:
            current_z = float(parameters["Z"])
        if str(command.Name) in {"G1", "G2", "G3"} and current_z is not None:
            effective_cutting_z.append(current_z)
    cutting_z = tuple(effective_cutting_z)
    if (
        cutting_z
        and min(cutting_z)
        > prepared.geometry.derived_final_depth_mm + _CUTTING_DEPTH_TOLERANCE_MM
    ):
        raise NativeManufactureError(
            "The created 3D Pocket did not reach its derived final depth.",
            error_code="NATIVE_MANUFACTURE_POCKET_3D_POSTCONDITION_FAILED",
            repair={
                "derived_final_depth_mm": prepared.geometry.derived_final_depth_mm,
                "minimum_effective_cutting_z_mm": round(min(cutting_z), 9),
            },
        )
    features = prepared.geometry.features
    return {
        "features": {
            "feature_count": features.feature_count,
            "face_count": features.face_count,
            "edge_count": features.edge_count,
            "closed_edge_wire_count": features.closed_edge_wire_count,
        },
        "derived_source_top_mm": prepared.geometry.derived_source_top_mm,
        "derived_final_depth_mm": prepared.geometry.derived_final_depth_mm,
        "tool_diameter_mm": prepared.tool_diameter_mm,
        "stepover_mm": prepared.stepover_mm,
        "removal_volume_mm3": (
            round(float(removal.Volume), 9) if removal_valid else 0.0
        ),
        "minimum_cutting_z_mm": round(min(cutting_z), 9) if cutting_z else None,
    }


def verify_created_pocket_3d(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedPocket3DCreate = draft.value["pocket_3d_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="pocket_3d",
        assert_settings=partial(_assert_pocket_3d_settings, prepared=prepared),
        additional_verify=partial(_pocket_3d_result, prepared=prepared),
        minimum_cutting_commands=(0 if prepared.parameters.rest_machining else 1),
    )
