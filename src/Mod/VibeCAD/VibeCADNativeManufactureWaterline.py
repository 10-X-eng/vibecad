# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded, task-free Native CAM Waterline creation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOCL import (
    OCLGeometryFacts,
    OCLGeometryRequest,
    OCLToolFacts,
    inspect_ocl_geometry,
    normalize_ocl_geometry,
    validate_ocl_tool,
)
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


_WATERLINE_FIELDS = frozenset(
    {
        "algorithm",
        "cut_mode",
        "layers",
        "depth_offset_mm",
        "geometry_handling",
        "reverse_pass_order",
        "optimization",
        "start",
    }
)
_DEPTH_FIELDS = frozenset({"start_depth_mm", "final_depth_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_POINT_FIELDS = frozenset({"x_mm", "y_mm", "z_mm"})
_POINT_XY_FIELDS = frozenset({"x_mm", "y_mm"})
_GEOMETRY_HANDLING_FIELDS = frozenset(
    {"boundary_enforcement", "internal_features", "multiple_features"}
)
_INTERNAL_FEATURE_FIELDS = frozenset({"cut", "adjustment_mm"})
_OPTIMIZATION_FIELDS = frozenset({"stepover_transitions", "gap_threshold_mm"})
_ALGORITHM_NAMES = {
    "drop_cutter": "OCL Dropcutter",
    "adaptive": "OCL Adaptive",
    "experimental": "Experimental",
}
_BOUND_NAMES = {"model": "BaseBoundBox", "stock": "Stock"}
_CUT_MODES = {"climb": "Climb", "conventional": "Conventional"}
_PATTERN_NAMES = {
    "offset": "Offset",
    "line": "Line",
    "zigzag": "ZigZag",
    "circular": "Circular",
    "circular_zigzag": "CircularZigZag",
    "spiral": "Spiral",
}
_PATTERN_CENTER_NAMES = {
    "center_of_mass": "CenterOfMass",
    "bounding_box_center": "CenterOfBoundBox",
    "minimum_xy": "XminYmin",
    "point": "Custom",
}
_MULTIPLE_FEATURES = {
    "collectively": "Collectively",
    "individually": "Individually",
}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_MAX_ESTIMATED_PROCESSING_CELLS = 250_000
_SETTING_TOLERANCE = 1.0e-7


@dataclass(frozen=True, slots=True)
class WaterlineCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    waterline: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class WaterlineParameters:
    algorithm: str
    bounds: str
    sample_interval_mm: float
    minimum_sample_interval_mm: float
    mesh_deflection_mm: float
    optimize_linear_paths: bool
    clearing_mode: str
    pattern: str
    angle_degrees: float
    pattern_center: str
    pattern_center_mm: tuple[float, float]
    stepover_percent: int
    boundary_adjustment_mm: float
    ignore_outer_above_mm: float
    cut_mode: str
    layer_mode: str
    step_down_mm: float
    depth_offset_mm: float
    boundary_enforcement: bool
    cut_internal_features: bool
    internal_features_adjustment_mm: float
    multiple_features: str
    reverse_pass_order: bool
    optimize_stepover_transitions: bool
    gap_threshold_mm: float
    use_start_point: bool
    start_point_mm: tuple[float, float, float]
    start_depth_mm: float
    final_depth_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class WaterlineGeometryFacts:
    common: OCLGeometryFacts
    layer_count: int
    estimated_processing_cells: int


@dataclass(frozen=True, slots=True)
class PreparedWaterlineCreate:
    label: str
    boundary: PreparedOperationBoundary
    geometry_request: OCLGeometryRequest
    parameters: WaterlineParameters
    geometry: WaterlineGeometryFacts
    tool: OCLToolFacts
    stepover_mm: float


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _boolean(value: Any, noun: str) -> bool:
    if not isinstance(value, bool):
        _error(f"{noun} must be true or false.")
    return value


def _positive(value: Any, noun: str, *, minimum: float = 0.0) -> float:
    result = finite_number(value, noun, minimum=minimum)
    if result <= 0.0:
        _error(f"{noun} must be greater than zero.")
    return result


def _normalize_center(raw: Any) -> tuple[str, tuple[float, float]]:
    if not isinstance(raw, Mapping):
        _error("Waterline radial clearing center must be one closed center request.")
    kind = str(raw.get("kind") or "")
    if kind in {"center_of_mass", "bounding_box_center", "minimum_xy"}:
        exact_fields(raw, frozenset({"kind"}), "Waterline automatic clearing center")
        return kind, (0.0, 0.0)
    if kind != "point":
        _error(
            "Waterline clearing center kind must be center_of_mass, "
            "bounding_box_center, minimum_xy, or point."
        )
    exact_fields(raw, frozenset({"kind", "point_mm"}), "Waterline point center")
    point = exact_fields(
        raw["point_mm"],
        _POINT_XY_FIELDS,
        "Waterline clearing center point",
    )
    return kind, tuple(
        finite_number(point[field], f"Waterline clearing center {field}")
        for field in ("x_mm", "y_mm")
    )


def _normalize_pattern(
    raw: Any,
) -> tuple[str, float, str, tuple[float, float]]:
    if not isinstance(raw, Mapping):
        _error("Waterline clearing pattern must be one closed pattern request.")
    kind = str(raw.get("kind") or "")
    if kind == "offset":
        exact_fields(raw, frozenset({"kind"}), "Waterline offset clearing pattern")
        return kind, 0.0, "center_of_mass", (0.0, 0.0)
    if kind in {"line", "zigzag"}:
        exact_fields(
            raw,
            frozenset({"kind", "angle_degrees"}),
            f"Waterline {kind} clearing pattern",
        )
        angle = finite_number(
            raw["angle_degrees"],
            "Waterline clearing angle",
            minimum=-360.0,
            maximum=360.0,
        )
        if angle >= 360.0:
            _error("Waterline clearing angle must be less than 360 degrees.")
        return kind, angle, "center_of_mass", (0.0, 0.0)
    if kind in {"circular", "circular_zigzag", "spiral"}:
        exact_fields(
            raw,
            frozenset({"kind", "center"}),
            f"Waterline {kind} clearing pattern",
        )
        center, point = _normalize_center(raw["center"])
        return kind, 0.0, center, point
    _error(
        "Waterline clearing pattern kind must be offset, line, zigzag, "
        "circular, circular_zigzag, or spiral."
    )


def _normalize_clearing(
    raw: Any,
) -> tuple[str, str, float, str, tuple[float, float], int]:
    if not isinstance(raw, Mapping):
        _error("Waterline clearing must be one closed clearing request.")
    kind = str(raw.get("kind") or "")
    if kind == "waterline_only":
        exact_fields(raw, frozenset({"kind"}), "Waterline-only clearing")
        return kind, "none", 0.0, "center_of_mass", (0.0, 0.0), 100
    if kind not in {"every_layer", "final_layer"}:
        _error("Waterline clearing kind must be waterline_only, every_layer, or final_layer.")
    value = exact_fields(
        raw,
        frozenset({"kind", "pattern", "stepover_percent"}),
        f"Waterline {kind} clearing",
    )
    stepover = value["stepover_percent"]
    if isinstance(stepover, bool) or not isinstance(stepover, int) or not 1 <= stepover <= 100:
        _error("Waterline stepover_percent must be an integer from 1 through 100.")
    pattern, angle, center, point = _normalize_pattern(value["pattern"])
    return kind, pattern, angle, center, point, stepover


def _normalize_algorithm(
    raw: Any,
    *,
    start_depth_mm: float,
) -> tuple[
    str,
    str,
    float,
    float,
    float,
    bool,
    str,
    str,
    float,
    str,
    tuple[float, float],
    int,
    float,
    float,
]:
    if not isinstance(raw, Mapping):
        _error("Waterline algorithm must be one closed algorithm request.")
    kind = str(raw.get("kind") or "")
    if kind == "drop_cutter":
        value = exact_fields(
            raw,
            frozenset(
                {"kind", "bounds", "sample_interval_mm", "mesh_deflection_mm"}
            ),
            "Waterline drop-cutter algorithm",
        )
        bounds = str(value["bounds"] or "")
        if bounds not in _BOUND_NAMES:
            _error("Waterline drop-cutter bounds must be model or stock.")
        return (
            kind,
            bounds,
            _positive(
                value["sample_interval_mm"],
                "Waterline sample interval",
                minimum=0.001,
            ),
            0.005,
            _positive(
                value["mesh_deflection_mm"],
                "Waterline mesh deflection",
                minimum=0.001,
            ),
            True,
            "waterline_only",
            "none",
            0.0,
            "center_of_mass",
            (0.0, 0.0),
            100,
            0.0,
            start_depth_mm + 0.00001,
        )
    if kind == "adaptive":
        value = exact_fields(
            raw,
            frozenset(
                {
                    "kind",
                    "sample_interval_mm",
                    "minimum_sample_interval_mm",
                    "optimize_linear_paths",
                    "mesh_deflection_mm",
                }
            ),
            "Waterline adaptive algorithm",
        )
        sample = _positive(
            value["sample_interval_mm"],
            "Waterline sample interval",
            minimum=0.001,
        )
        minimum_sample = _positive(
            value["minimum_sample_interval_mm"],
            "Waterline minimum sample interval",
            minimum=0.001,
        )
        if minimum_sample > sample:
            _error(
                "Waterline minimum_sample_interval_mm cannot exceed sample_interval_mm."
            )
        return (
            kind,
            "model",
            sample,
            minimum_sample,
            _positive(
                value["mesh_deflection_mm"],
                "Waterline mesh deflection",
                minimum=0.001,
            ),
            _boolean(
                value["optimize_linear_paths"],
                "Waterline optimize_linear_paths",
            ),
            "waterline_only",
            "none",
            0.0,
            "center_of_mass",
            (0.0, 0.0),
            100,
            0.0,
            start_depth_mm + 0.00001,
        )
    if kind != "experimental":
        _error("Waterline algorithm kind must be drop_cutter, adaptive, or experimental.")
    value = exact_fields(
        raw,
        frozenset(
            {
                "kind",
                "bounds",
                "clearing",
                "boundary_adjustment_mm",
                "ignore_outer_above_mm",
            }
        ),
        "Waterline experimental algorithm",
    )
    bounds = str(value["bounds"] or "")
    if bounds not in _BOUND_NAMES:
        _error("Waterline experimental bounds must be model or stock.")
    clearing = _normalize_clearing(value["clearing"])
    return (
        kind,
        bounds,
        1.0,
        0.005,
        0.001,
        True,
        *clearing,
        finite_number(
            value["boundary_adjustment_mm"],
            "Waterline boundary adjustment",
        ),
        finite_number(
            value["ignore_outer_above_mm"],
            "Waterline ignore-outer-above height",
        ),
    )


def _normalize_layers(raw: Any, depth_span_mm: float) -> tuple[str, float]:
    if not isinstance(raw, Mapping):
        _error("Waterline layers must be one closed layer request.")
    kind = str(raw.get("kind") or "")
    if kind == "single_pass":
        exact_fields(raw, frozenset({"kind"}), "Waterline single-pass layers")
        return kind, max(round(depth_span_mm, 9), 0.001)
    if kind == "multi_pass":
        exact_fields(raw, frozenset({"kind", "step_down_mm"}), "Waterline multi-pass layers")
        step_down = _positive(raw["step_down_mm"], "Waterline step_down_mm")
        if step_down > depth_span_mm:
            _error("Waterline step_down_mm cannot exceed the requested depth span.")
        return kind, step_down
    _error("Waterline layer kind must be single_pass or multi_pass.")


def _normalize_start(raw: Any) -> tuple[bool, tuple[float, float, float]]:
    if not isinstance(raw, Mapping):
        _error("Waterline start must be one closed start request.")
    kind = str(raw.get("kind") or "")
    if kind == "automatic":
        exact_fields(raw, frozenset({"kind"}), "Waterline automatic start")
        return False, (0.0, 0.0, 0.0)
    if kind != "point":
        _error("Waterline start kind must be automatic or point.")
    exact_fields(raw, frozenset({"kind", "point_mm"}), "Waterline point start")
    point = exact_fields(raw["point_mm"], _POINT_FIELDS, "Waterline start point")
    return True, tuple(
        finite_number(point[field], f"Waterline start point {field}")
        for field in ("x_mm", "y_mm", "z_mm")
    )


def _normalize_parameters(spec: WaterlineCreateSpec) -> WaterlineParameters:
    settings = exact_fields(spec.waterline, _WATERLINE_FIELDS, "Waterline settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Waterline depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Waterline heights")
    start_depth = finite_number(depths["start_depth_mm"], "Waterline start depth")
    final_depth = finite_number(depths["final_depth_mm"], "Waterline final depth")
    if final_depth >= start_depth:
        _error("Waterline final_depth_mm must be below start_depth_mm.")
    layer_mode, step_down = _normalize_layers(
        settings["layers"],
        start_depth - final_depth,
    )
    algorithm = _normalize_algorithm(
        settings["algorithm"],
        start_depth_mm=start_depth,
    )
    cut_mode = str(settings["cut_mode"] or "")
    if cut_mode not in _CUT_MODES:
        _error("Waterline cut_mode must be climb or conventional.")
    handling = exact_fields(
        settings["geometry_handling"],
        _GEOMETRY_HANDLING_FIELDS,
        "Waterline geometry handling",
    )
    internal = exact_fields(
        handling["internal_features"],
        _INTERNAL_FEATURE_FIELDS,
        "Waterline internal features",
    )
    multiple = str(handling["multiple_features"] or "")
    if multiple not in _MULTIPLE_FEATURES:
        _error("Waterline multiple_features must be collectively or individually.")
    optimization = exact_fields(
        settings["optimization"],
        _OPTIMIZATION_FIELDS,
        "Waterline optimization",
    )
    use_start, start_point = _normalize_start(settings["start"])
    safe = finite_number(heights["safe_height_mm"], "Waterline safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Waterline clearance height",
    )
    if safe < start_depth:
        _error("Waterline safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Waterline clearance_height_mm must be at or above safe_height_mm.")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Waterline coolant must be none, flood, or mist.")
    (
        algorithm_kind,
        bounds,
        sample_interval,
        minimum_sample_interval,
        mesh_deflection,
        optimize_linear,
        clearing_mode,
        pattern,
        angle,
        pattern_center,
        pattern_center_mm,
        stepover,
        boundary_adjustment,
        ignore_outer_above,
    ) = algorithm
    if algorithm_kind == "experimental" and not (
        final_depth <= ignore_outer_above <= start_depth
    ):
        _error(
            "Waterline experimental ignore_outer_above_mm must lie between "
            "final_depth_mm and start_depth_mm."
        )
    return WaterlineParameters(
        algorithm=algorithm_kind,
        bounds=bounds,
        sample_interval_mm=sample_interval,
        minimum_sample_interval_mm=minimum_sample_interval,
        mesh_deflection_mm=mesh_deflection,
        optimize_linear_paths=optimize_linear,
        clearing_mode=clearing_mode,
        pattern=pattern,
        angle_degrees=angle,
        pattern_center=pattern_center,
        pattern_center_mm=pattern_center_mm,
        stepover_percent=stepover,
        boundary_adjustment_mm=boundary_adjustment,
        ignore_outer_above_mm=ignore_outer_above,
        cut_mode=cut_mode,
        layer_mode=layer_mode,
        step_down_mm=step_down,
        depth_offset_mm=finite_number(
            settings["depth_offset_mm"],
            "Waterline depth offset",
        ),
        boundary_enforcement=_boolean(
            handling["boundary_enforcement"],
            "Waterline boundary enforcement",
        ),
        cut_internal_features=_boolean(
            internal["cut"],
            "Waterline internal feature cutting",
        ),
        internal_features_adjustment_mm=finite_number(
            internal["adjustment_mm"],
            "Waterline internal feature adjustment",
        ),
        multiple_features=multiple,
        reverse_pass_order=_boolean(
            settings["reverse_pass_order"],
            "Waterline reverse pass order",
        ),
        optimize_stepover_transitions=_boolean(
            optimization["stepover_transitions"],
            "Waterline stepover-transition optimization",
        ),
        gap_threshold_mm=finite_number(
            optimization["gap_threshold_mm"],
            "Waterline gap threshold",
            minimum=0.0,
        ),
        use_start_point=use_start,
        start_point_mm=start_point,
        start_depth_mm=start_depth,
        final_depth_mm=final_depth,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        coolant=coolant,
    )


def _estimate_workload(
    common: OCLGeometryFacts,
    parameters: WaterlineParameters,
    tool: OCLToolFacts,
) -> WaterlineGeometryFacts:
    layer_count = (
        1
        if parameters.layer_mode == "single_pass"
        else int(
            math.ceil(
                (parameters.start_depth_mm - parameters.final_depth_mm)
                / parameters.step_down_mm
            )
        )
    )
    if parameters.algorithm == "drop_cutter":
        estimate = sum(
            (max(2, math.ceil(x_span / parameters.sample_interval_mm) + 1))
            * (max(2, math.ceil(y_span / parameters.sample_interval_mm) + 1))
            * layer_count
            for x_span, y_span in common.spans_xy_mm
        )
    elif parameters.algorithm == "adaptive":
        refinement = min(
            max(parameters.sample_interval_mm / parameters.minimum_sample_interval_mm, 1.0),
            8.0,
        )
        estimate = math.ceil(
            sum(
                max(2, math.ceil(x_span / parameters.sample_interval_mm) + 1)
                * max(2, math.ceil(y_span / parameters.sample_interval_mm) + 1)
                for x_span, y_span in common.spans_xy_mm
            )
            * refinement
            * refinement
            * layer_count
        )
    else:
        stepover = (
            tool.diameter_mm
            if parameters.clearing_mode == "waterline_only"
            else tool.diameter_mm * parameters.stepover_percent / 100.0
        )
        padding = abs(parameters.boundary_adjustment_mm) * 2.0
        estimate = sum(
            max(2, math.ceil((x_span + padding) / stepover) + 1)
            * max(2, math.ceil((y_span + padding) / stepover) + 1)
            * layer_count
            for x_span, y_span in common.spans_xy_mm
        )
    if estimate > _MAX_ESTIMATED_PROCESSING_CELLS:
        _error(
            "Waterline would require approximately "
            f"{estimate:,} processing cells, above the synchronous safety limit "
            f"of {_MAX_ESTIMATED_PROCESSING_CELLS:,}. Select fewer Faces, increase "
            "the sample interval or stepover, increase step_down_mm, or split the "
            "machining region so the VibeCAD UI remains responsive.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    return WaterlineGeometryFacts(
        common=common,
        layer_count=layer_count,
        estimated_processing_cells=estimate,
    )


def preflight_waterline_create(
    document: Any,
    spec: WaterlineCreateSpec,
) -> PreparedWaterlineCreate:
    """Freeze exact geometry, algorithm inputs, cutter, and bounded work."""

    if not isinstance(spec, WaterlineCreateSpec):
        raise TypeError("spec must be a WaterlineCreateSpec")
    parameters = _normalize_parameters(spec)
    geometry_request = normalize_ocl_geometry(spec.geometry, noun="Waterline")
    if (
        geometry_request.requested_kind == "faces"
        and parameters.algorithm != "adaptive"
    ):
        _error(
            "Selected Faces are supported only by the Waterline adaptive algorithm. "
            f"The {parameters.algorithm} algorithm requires geometry.kind=entire_job; "
            "the shipped Waterline engine otherwise ignores the requested Faces."
        )
    boundary = preflight_operation_boundary(
        document,
        noun="Waterline",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=geometry_request.shared_request,
        allowed_subelement_types=frozenset({"Face"}),
        allow_entire_job=True,
    )
    tool = validate_ocl_tool(boundary, noun="Waterline")
    common = inspect_ocl_geometry(
        boundary,
        geometry_request,
        bounds=parameters.bounds,
        noun="Waterline",
    )
    if parameters.start_depth_mm < common.derived_source_top_mm:
        _error(
            "Waterline start_depth_mm must be at or above the derived source top "
            f"of {common.derived_source_top_mm:g} mm."
        )
    effective_final = parameters.final_depth_mm + parameters.depth_offset_mm
    if effective_final < common.stock_bottom_mm:
        _error(
            "Waterline final_depth_mm plus depth_offset_mm cannot be below the "
            f"exact Job stock bottom of {common.stock_bottom_mm:g} mm."
        )
    geometry = _estimate_workload(common, parameters, tool)
    return PreparedWaterlineCreate(
        label=clean_operation_label(spec.label, "Waterline"),
        boundary=boundary,
        geometry_request=geometry_request,
        parameters=parameters,
        geometry=geometry,
        tool=tool,
        stepover_mm=round(
            tool.diameter_mm * parameters.stepover_percent / 100.0,
            9,
        ),
    )


def _pattern_payload(parameters: WaterlineParameters) -> dict[str, Any]:
    result: dict[str, Any] = {"kind": parameters.pattern}
    if parameters.pattern in {"line", "zigzag"}:
        result["angle_degrees"] = parameters.angle_degrees
    elif parameters.pattern in {"circular", "circular_zigzag", "spiral"}:
        center: dict[str, Any] = {"kind": parameters.pattern_center}
        if parameters.pattern_center == "point":
            center["point_mm"] = {
                "x_mm": parameters.pattern_center_mm[0],
                "y_mm": parameters.pattern_center_mm[1],
            }
        result["center"] = center
    return result


def _algorithm_payload(parameters: WaterlineParameters) -> dict[str, Any]:
    if parameters.algorithm == "drop_cutter":
        return {
            "kind": "drop_cutter",
            "bounds": parameters.bounds,
            "sample_interval_mm": parameters.sample_interval_mm,
            "mesh_deflection_mm": parameters.mesh_deflection_mm,
        }
    if parameters.algorithm == "adaptive":
        return {
            "kind": "adaptive",
            "sample_interval_mm": parameters.sample_interval_mm,
            "minimum_sample_interval_mm": parameters.minimum_sample_interval_mm,
            "optimize_linear_paths": parameters.optimize_linear_paths,
            "mesh_deflection_mm": parameters.mesh_deflection_mm,
        }
    clearing: dict[str, Any] = {"kind": parameters.clearing_mode}
    if parameters.clearing_mode != "waterline_only":
        clearing.update(
            {
                "pattern": _pattern_payload(parameters),
                "stepover_percent": parameters.stepover_percent,
            }
        )
    return {
        "kind": "experimental",
        "bounds": parameters.bounds,
        "clearing": clearing,
        "boundary_adjustment_mm": parameters.boundary_adjustment_mm,
        "ignore_outer_above_mm": parameters.ignore_outer_above_mm,
    }


def _parameter_payload(prepared: PreparedWaterlineCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    layers: dict[str, Any] = {"kind": parameters.layer_mode}
    if parameters.layer_mode == "multi_pass":
        layers["step_down_mm"] = parameters.step_down_mm
    start: dict[str, Any] = {"kind": "automatic"}
    if parameters.use_start_point:
        start = {
            "kind": "point",
            "point_mm": dict(
                zip(("x_mm", "y_mm", "z_mm"), parameters.start_point_mm, strict=True)
            ),
        }
    return {
        "waterline": {
            "algorithm": _algorithm_payload(parameters),
            "cut_mode": parameters.cut_mode,
            "layers": layers,
            "depth_offset_mm": parameters.depth_offset_mm,
            "geometry_handling": {
                "boundary_enforcement": parameters.boundary_enforcement,
                "internal_features": {
                    "cut": parameters.cut_internal_features,
                    "adjustment_mm": parameters.internal_features_adjustment_mm,
                },
                "multiple_features": parameters.multiple_features,
            },
            "reverse_pass_order": parameters.reverse_pass_order,
            "optimization": {
                "stepover_transitions": parameters.optimize_stepover_transitions,
                "gap_threshold_mm": parameters.gap_threshold_mm,
            },
            "start": start,
        },
        "depths": {
            "start_depth_mm": parameters.start_depth_mm,
            "final_depth_mm": parameters.final_depth_mm,
            "effective_step_down_mm": parameters.step_down_mm,
            "derived_operation_floor_mm": (
                prepared.geometry.common.derived_operation_floor_mm
            ),
        },
        "heights": {
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
        "coolant": parameters.coolant,
    }


def _operation_patterns(parameters: WaterlineParameters) -> tuple[str, str]:
    if parameters.clearing_mode == "waterline_only":
        return "None", "Off"
    pattern = _PATTERN_NAMES[parameters.pattern]
    if parameters.clearing_mode == "every_layer":
        return pattern, "Off"
    return "None", pattern


def _apply_settings(operation: Any, prepared: PreparedWaterlineCreate) -> None:
    import FreeCAD as App

    parameters = prepared.parameters
    request = prepared.geometry_request
    clear_operation_expressions(
        operation,
        ("StartDepth", "FinalDepth", "StepDown", "SafeHeight", "ClearanceHeight"),
    )
    cut_pattern, clear_last_layer = _operation_patterns(parameters)
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.Algorithm = _ALGORITHM_NAMES[parameters.algorithm]
    operation.BoundBox = _BOUND_NAMES[parameters.bounds]
    operation.LayerMode = (
        "Single-pass" if parameters.layer_mode == "single_pass" else "Multi-pass"
    )
    operation.CutMode = _CUT_MODES[parameters.cut_mode]
    operation.CutPattern = cut_pattern
    operation.ClearLastLayer = clear_last_layer
    operation.CutPatternAngle = parameters.angle_degrees
    operation.PatternCenterAt = _PATTERN_CENTER_NAMES[parameters.pattern_center]
    operation.PatternCenterCustom = App.Vector(*parameters.pattern_center_mm, 0.0)
    operation.StepOver = parameters.stepover_percent
    operation.SampleInterval = f"{parameters.sample_interval_mm} mm"
    operation.MinSampleInterval = f"{parameters.minimum_sample_interval_mm} mm"
    operation.LinearDeflection = f"{parameters.mesh_deflection_mm} mm"
    operation.AngularDeflection = "0.25 mm"
    operation.OptimizeLinearPaths = parameters.optimize_linear_paths
    operation.BoundaryAdjustment = f"{parameters.boundary_adjustment_mm} mm"
    operation.IgnoreOuterAbove = f"{parameters.ignore_outer_above_mm} mm"
    operation.DepthOffset = f"{parameters.depth_offset_mm} mm"
    operation.AvoidLastX_Faces = request.avoid_last_face_count
    operation.AvoidLastX_InternalFeatures = request.avoid_internal_features
    operation.BoundaryEnforcement = parameters.boundary_enforcement
    operation.InternalFeaturesCut = parameters.cut_internal_features
    operation.InternalFeaturesAdjustment = (
        f"{parameters.internal_features_adjustment_mm} mm"
    )
    operation.HandleMultipleFeatures = _MULTIPLE_FEATURES[parameters.multiple_features]
    operation.CutPatternReversed = parameters.reverse_pass_order
    operation.OptimizeStepOverTransitions = parameters.optimize_stepover_transitions
    operation.GapThreshold = f"{parameters.gap_threshold_mm} mm"
    operation.UseStartPoint = parameters.use_start_point
    operation.StartPoint = App.Vector(*parameters.start_point_mm)
    operation.OpStartDepth = f"{prepared.geometry.common.derived_source_top_mm} mm"
    operation.OpFinalDepth = f"{prepared.geometry.common.derived_operation_floor_mm} mm"
    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]
    operation.Workplane = App.Vector(0.0, 0.0, 1.0)
    operation.ShowTempObjects = False


def create_waterline(
    document: Any,
    *,
    prepared: PreparedWaterlineCreate,
) -> NativeMutationDraft:
    """Create one shipped Waterline operation inside the owned transaction."""

    if not isinstance(prepared, PreparedWaterlineCreate):
        raise TypeError("prepared must be a PreparedWaterlineCreate")
    import Path.Op.Waterline as PathWaterline

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Waterline"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Waterline",
        operation_factory=PathWaterline.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, waterline_prepared=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    return next(
        (
            expression
            for path, expression in tuple(getattr(operation, "ExpressionEngine", ()) or ())
            if str(path).lstrip(".") == property_name
        ),
        None,
    )


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _same_number(actual: float, expected: float) -> bool:
    return abs(float(actual) - float(expected)) <= _SETTING_TOLERANCE


def _assert_waterline_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedWaterlineCreate,
) -> None:
    parameters = prepared.parameters
    request = prepared.geometry_request
    cut_pattern, clear_last_layer = _operation_patterns(parameters)
    expected = {
        "algorithm": _ALGORITHM_NAMES[parameters.algorithm],
        "bounds": _BOUND_NAMES[parameters.bounds],
        "layer_mode": (
            "Single-pass" if parameters.layer_mode == "single_pass" else "Multi-pass"
        ),
        "cut_mode": _CUT_MODES[parameters.cut_mode],
        "cut_pattern": cut_pattern,
        "clear_last_layer": clear_last_layer,
        "angle_degrees": parameters.angle_degrees,
        "pattern_center": _PATTERN_CENTER_NAMES[parameters.pattern_center],
        "stepover_percent": parameters.stepover_percent,
        "sample_interval_mm": parameters.sample_interval_mm,
        "minimum_sample_interval_mm": parameters.minimum_sample_interval_mm,
        "mesh_deflection_mm": parameters.mesh_deflection_mm,
        "optimize_linear_paths": parameters.optimize_linear_paths,
        "boundary_adjustment_mm": parameters.boundary_adjustment_mm,
        "ignore_outer_above_mm": parameters.ignore_outer_above_mm,
        "depth_offset_mm": parameters.depth_offset_mm,
        "avoid_last_face_count": request.avoid_last_face_count,
        "avoid_internal_features": request.avoid_internal_features,
        "boundary_enforcement": parameters.boundary_enforcement,
        "cut_internal_features": parameters.cut_internal_features,
        "internal_features_adjustment_mm": parameters.internal_features_adjustment_mm,
        "multiple_features": _MULTIPLE_FEATURES[parameters.multiple_features],
        "reverse_pass_order": parameters.reverse_pass_order,
        "optimize_stepover_transitions": parameters.optimize_stepover_transitions,
        "gap_threshold_mm": parameters.gap_threshold_mm,
        "use_start_point": parameters.use_start_point,
        "start_point_mm": parameters.start_point_mm,
        "derived_source_top_mm": prepared.geometry.common.derived_source_top_mm,
        "derived_operation_floor_mm": prepared.geometry.common.derived_operation_floor_mm,
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
        "workplane": (0.0, 0.0, 1.0),
        "show_temporary_objects": False,
    }
    actual = {
        "algorithm": str(operation.Algorithm),
        "bounds": str(operation.BoundBox),
        "layer_mode": str(operation.LayerMode),
        "cut_mode": str(operation.CutMode),
        "cut_pattern": str(operation.CutPattern),
        "clear_last_layer": str(operation.ClearLastLayer),
        "angle_degrees": round(float(operation.CutPatternAngle), 9),
        "pattern_center": str(operation.PatternCenterAt),
        "stepover_percent": int(operation.StepOver),
        "sample_interval_mm": quantity_mm(operation, "SampleInterval"),
        "minimum_sample_interval_mm": quantity_mm(operation, "MinSampleInterval"),
        "mesh_deflection_mm": quantity_mm(operation, "LinearDeflection"),
        "optimize_linear_paths": bool(operation.OptimizeLinearPaths),
        "boundary_adjustment_mm": quantity_mm(operation, "BoundaryAdjustment"),
        "ignore_outer_above_mm": quantity_mm(operation, "IgnoreOuterAbove"),
        "depth_offset_mm": quantity_mm(operation, "DepthOffset"),
        "avoid_last_face_count": int(operation.AvoidLastX_Faces),
        "avoid_internal_features": bool(operation.AvoidLastX_InternalFeatures),
        "boundary_enforcement": bool(operation.BoundaryEnforcement),
        "cut_internal_features": bool(operation.InternalFeaturesCut),
        "internal_features_adjustment_mm": quantity_mm(
            operation,
            "InternalFeaturesAdjustment",
        ),
        "multiple_features": str(operation.HandleMultipleFeatures),
        "reverse_pass_order": bool(operation.CutPatternReversed),
        "optimize_stepover_transitions": bool(operation.OptimizeStepOverTransitions),
        "gap_threshold_mm": quantity_mm(operation, "GapThreshold"),
        "use_start_point": bool(operation.UseStartPoint),
        "start_point_mm": _vector_tuple(operation.StartPoint),
        "derived_source_top_mm": quantity_mm(operation, "OpStartDepth"),
        "derived_operation_floor_mm": quantity_mm(operation, "OpFinalDepth"),
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "coolant": str(operation.CoolantMode),
        "workplane": _vector_tuple(operation.Workplane),
        "show_temporary_objects": bool(operation.ShowTempObjects),
    }
    mismatches = {}
    for name, expected_value in expected.items():
        actual_value = actual.get(name)
        if isinstance(expected_value, float) and isinstance(actual_value, (int, float)):
            matches = _same_number(actual_value, expected_value)
        elif isinstance(expected_value, tuple) and isinstance(actual_value, tuple):
            matches = len(actual_value) == len(expected_value) and all(
                _same_number(a, e)
                for a, e in zip(actual_value, expected_value, strict=True)
            )
        else:
            matches = actual_value == expected_value
        if not matches:
            mismatches[name] = {"expected": expected_value, "actual": actual_value}
    if parameters.pattern_center == "point":
        expected_center = (*parameters.pattern_center_mm, 0.0)
        actual_center = _vector_tuple(operation.PatternCenterCustom)
        if any(
            not _same_number(actual_value, expected_value)
            for actual_value, expected_value in zip(
                actual_center,
                expected_center,
                strict=True,
            )
        ):
            mismatches["pattern_center_mm"] = {
                "expected": expected_center,
                "actual": actual_center,
            }
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
            "The created Waterline did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_WATERLINE_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _waterline_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedWaterlineCreate,
) -> Mapping[str, Any]:
    cutting_z = []
    current_z = None
    for command in tuple(operation.Path.Commands):
        values = command.Parameters
        if "Z" in values:
            current_z = float(values["Z"])
        if str(command.Name) in {"G1", "G2", "G3"} and current_z is not None:
            cutting_z.append(current_z)
    if not cutting_z:
        _error(
            "The created Waterline has no depth-bearing cutting moves.",
            "NATIVE_MANUFACTURE_WATERLINE_POSTCONDITION_FAILED",
        )
    common = prepared.geometry.common
    return {
        "target_mode": prepared.geometry_request.requested_kind,
        "face_count": common.face_count,
        "cutting_face_count": common.cutting_face_count,
        "avoided_face_count": common.avoided_face_count,
        "derived_source_top_mm": common.derived_source_top_mm,
        "derived_operation_floor_mm": common.derived_operation_floor_mm,
        "algorithm": prepared.parameters.algorithm,
        "layer_count": prepared.geometry.layer_count,
        "tool_shape_type": prepared.tool.shape_type,
        "ocl_cutter": prepared.tool.ocl_cutter,
        "tool_diameter_mm": prepared.tool.diameter_mm,
        "stepover_mm": prepared.stepover_mm,
        "estimated_processing_cells": prepared.geometry.estimated_processing_cells,
        "minimum_cutting_z_mm": round(min(cutting_z), 9),
        "maximum_cutting_z_mm": round(max(cutting_z), 9),
        "gap_summary": str(operation.GapSizes),
    }


def verify_created_waterline(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedWaterlineCreate = draft.value["waterline_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="waterline",
        assert_settings=partial(_assert_waterline_settings, prepared=prepared),
        additional_verify=partial(_waterline_result, prepared=prepared),
    )
