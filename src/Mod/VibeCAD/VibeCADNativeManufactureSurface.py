# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded, task-free Native CAM Surface creation."""

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
    native_operation_presentation,
    preflight_operation_boundary,
    quantity_mm,
    verify_native_operation,
)
from VibeCADNativeManufactureOCL import (
    OCLGeometryRequest,
    OCLToolFacts,
    inspect_ocl_geometry,
    normalize_ocl_geometry,
    validate_ocl_tool,
)
from VibeCADNativeMutation import NativeMutationDraft


_SURFACE_FIELDS = frozenset(
    {
        "bounds",
        "cut_mode",
        "pattern",
        "layers",
        "stepover_percent",
        "depth_offset_mm",
        "sample_interval_mm",
        "profile_edges",
        "boundary",
        "internal_features",
        "multiple_features",
        "reverse_pass_order",
        "optimization",
        "start",
        "mesh_deflection_mm",
    }
)
_DEPTH_FIELDS = frozenset({"start_depth_mm", "final_depth_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_POINT_FIELDS = frozenset({"x_mm", "y_mm", "z_mm"})
_POINT_XY_FIELDS = frozenset({"x_mm", "y_mm"})
_BOUNDARY_FIELDS = frozenset({"enforce", "adjustment_mm"})
_INTERNAL_FEATURE_FIELDS = frozenset({"cut", "adjustment_mm"})
_OPTIMIZATION_FIELDS = frozenset(
    {"linear_paths", "stepover_transitions", "gap_threshold_mm"}
)
_PATTERN_NAMES = {
    "line": "Line",
    "zigzag": "ZigZag",
    "offset": "Offset",
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
_BOUND_NAMES = {"model": "BaseBoundBox", "stock": "Stock"}
_CUT_MODES = {"climb": "Climb", "conventional": "Conventional"}
_PROFILE_EDGES = {
    "none": "None",
    "only": "Only",
    "first": "First",
    "last": "Last",
}
_MULTIPLE_FEATURES = {
    "collectively": "Collectively",
    "individually": "Individually",
}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_MAX_ESTIMATED_DROP_CUTTER_POINTS = 250_000
_SETTING_TOLERANCE = 1.0e-7


@dataclass(frozen=True, slots=True)
class SurfaceCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    surface: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class SurfaceParameters:
    bounds: str
    cut_mode: str
    pattern: str
    angle_degrees: float
    pattern_center: str
    pattern_center_mm: tuple[float, float]
    emit_arcs: bool
    layer_mode: str
    step_down_mm: float
    stepover_percent: int
    depth_offset_mm: float
    sample_interval_mm: float
    profile_edges: str
    boundary_enforcement: bool
    boundary_adjustment_mm: float
    cut_internal_features: bool
    internal_features_adjustment_mm: float
    multiple_features: str
    reverse_pass_order: bool
    optimize_linear_paths: bool
    optimize_stepover_transitions: bool
    gap_threshold_mm: float
    use_start_point: bool
    start_point_mm: tuple[float, float, float]
    mesh_deflection_mm: float
    start_depth_mm: float
    final_depth_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class SurfaceGeometryFacts:
    face_count: int
    cutting_face_count: int
    avoided_face_count: int
    derived_source_top_mm: float
    derived_operation_floor_mm: float
    stock_bottom_mm: float
    estimated_drop_cutter_points: int


@dataclass(frozen=True, slots=True)
class PreparedSurfaceCreate:
    label: str
    boundary: PreparedOperationBoundary
    geometry_request: OCLGeometryRequest
    parameters: SurfaceParameters
    geometry: SurfaceGeometryFacts
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
        _error("Surface radial pattern center must be one closed center request.")
    kind = str(raw.get("kind") or "")
    if kind in {"center_of_mass", "bounding_box_center", "minimum_xy"}:
        exact_fields(raw, frozenset({"kind"}), "Surface automatic pattern center")
        return kind, (0.0, 0.0)
    if kind != "point":
        _error(
            "Surface pattern center kind must be center_of_mass, "
            "bounding_box_center, minimum_xy, or point."
        )
    exact_fields(raw, frozenset({"kind", "point_mm"}), "Surface point center")
    point = exact_fields(raw["point_mm"], _POINT_XY_FIELDS, "Surface pattern center point")
    return kind, tuple(
        finite_number(point[field], f"Surface pattern center {field}")
        for field in ("x_mm", "y_mm")
    )


def _normalize_pattern(raw: Any) -> tuple[str, float, str, tuple[float, float], bool]:
    if not isinstance(raw, Mapping):
        _error("Surface pattern must be one closed pattern request.")
    kind = str(raw.get("kind") or "")
    if kind in {"line", "zigzag"}:
        exact_fields(raw, frozenset({"kind", "angle_degrees"}), f"Surface {kind} pattern")
        angle = finite_number(
            raw["angle_degrees"],
            "Surface pattern angle",
            minimum=-360.0,
            maximum=360.0,
        )
        if angle >= 360.0:
            _error("Surface pattern angle must be less than 360 degrees.")
        return kind, angle, "center_of_mass", (0.0, 0.0), False
    if kind == "offset":
        exact_fields(raw, frozenset({"kind"}), "Surface offset pattern")
        return kind, 0.0, "center_of_mass", (0.0, 0.0), False
    if kind in {"circular", "circular_zigzag"}:
        exact_fields(
            raw,
            frozenset({"kind", "center", "emit_arcs"}),
            f"Surface {kind} pattern",
        )
        center, point = _normalize_center(raw["center"])
        return kind, 0.0, center, point, _boolean(
            raw["emit_arcs"], "Surface emit_arcs"
        )
    if kind == "spiral":
        exact_fields(raw, frozenset({"kind", "center"}), "Surface spiral pattern")
        center, point = _normalize_center(raw["center"])
        return kind, 0.0, center, point, False
    _error(
        "Surface pattern kind must be line, zigzag, offset, circular, "
        "circular_zigzag, or spiral."
    )


def _normalize_layers(raw: Any, depth_span_mm: float) -> tuple[str, float]:
    if not isinstance(raw, Mapping):
        _error("Surface layers must be one closed layer request.")
    kind = str(raw.get("kind") or "")
    if kind == "single_pass":
        exact_fields(raw, frozenset({"kind"}), "Surface single-pass layers")
        return kind, max(round(depth_span_mm, 9), 0.001)
    if kind == "multi_pass":
        exact_fields(
            raw,
            frozenset({"kind", "step_down_mm"}),
            "Surface multi-pass layers",
        )
        step_down = _positive(raw["step_down_mm"], "Surface step_down_mm")
        if step_down > depth_span_mm:
            _error("Surface step_down_mm cannot exceed the requested depth span.")
        return kind, step_down
    _error("Surface layer kind must be single_pass or multi_pass.")


def _normalize_start(raw: Any) -> tuple[bool, tuple[float, float, float]]:
    if not isinstance(raw, Mapping):
        _error("Surface start must be one closed start request.")
    kind = str(raw.get("kind") or "")
    if kind == "automatic":
        exact_fields(raw, frozenset({"kind"}), "Surface automatic start")
        return False, (0.0, 0.0, 0.0)
    if kind != "point":
        _error("Surface start kind must be automatic or point.")
    exact_fields(raw, frozenset({"kind", "point_mm"}), "Surface point start")
    point = exact_fields(raw["point_mm"], _POINT_FIELDS, "Surface start point")
    return True, tuple(
        finite_number(point[field], f"Surface start point {field}")
        for field in ("x_mm", "y_mm", "z_mm")
    )


def _normalize_parameters(spec: SurfaceCreateSpec) -> SurfaceParameters:
    surface = exact_fields(spec.surface, _SURFACE_FIELDS, "Surface settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Surface depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Surface heights")
    start_depth = finite_number(depths["start_depth_mm"], "Surface start depth")
    final_depth = finite_number(depths["final_depth_mm"], "Surface final depth")
    if final_depth >= start_depth:
        _error("Surface final_depth_mm must be below start_depth_mm.")
    layer_mode, step_down = _normalize_layers(
        surface["layers"], start_depth - final_depth
    )
    bounds = str(surface["bounds"] or "")
    if bounds not in _BOUND_NAMES:
        _error("Surface bounds must be model or stock.")
    cut_mode = str(surface["cut_mode"] or "")
    if cut_mode not in _CUT_MODES:
        _error("Surface cut_mode must be climb or conventional.")
    pattern, angle, center, center_point, emit_arcs = _normalize_pattern(
        surface["pattern"]
    )
    stepover = surface["stepover_percent"]
    if isinstance(stepover, bool) or not isinstance(stepover, int) or not 1 <= stepover <= 100:
        _error("Surface stepover_percent must be an integer from 1 through 100.")
    profile_edges = str(surface["profile_edges"] or "")
    if profile_edges not in _PROFILE_EDGES:
        _error("Surface profile_edges must be none, only, first, or last.")
    boundary = exact_fields(surface["boundary"], _BOUNDARY_FIELDS, "Surface boundary")
    internal = exact_fields(
        surface["internal_features"],
        _INTERNAL_FEATURE_FIELDS,
        "Surface internal features",
    )
    multiple = str(surface["multiple_features"] or "")
    if multiple not in _MULTIPLE_FEATURES:
        _error("Surface multiple_features must be collectively or individually.")
    optimization = exact_fields(
        surface["optimization"],
        _OPTIMIZATION_FIELDS,
        "Surface optimization",
    )
    use_start, start_point = _normalize_start(surface["start"])
    safe = finite_number(heights["safe_height_mm"], "Surface safe height")
    clearance = finite_number(
        heights["clearance_height_mm"], "Surface clearance height"
    )
    if safe < start_depth:
        _error("Surface safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Surface clearance_height_mm must be at or above safe_height_mm.")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Surface coolant must be none, flood, or mist.")
    return SurfaceParameters(
        bounds=bounds,
        cut_mode=cut_mode,
        pattern=pattern,
        angle_degrees=angle,
        pattern_center=center,
        pattern_center_mm=center_point,
        emit_arcs=emit_arcs,
        layer_mode=layer_mode,
        step_down_mm=step_down,
        stepover_percent=stepover,
        depth_offset_mm=finite_number(
            surface["depth_offset_mm"], "Surface depth offset"
        ),
        sample_interval_mm=_positive(
            surface["sample_interval_mm"],
            "Surface sample interval",
            minimum=0.001,
        ),
        profile_edges=profile_edges,
        boundary_enforcement=_boolean(boundary["enforce"], "Surface boundary enforce"),
        boundary_adjustment_mm=finite_number(
            boundary["adjustment_mm"], "Surface boundary adjustment"
        ),
        cut_internal_features=_boolean(
            internal["cut"], "Surface internal feature cut"
        ),
        internal_features_adjustment_mm=finite_number(
            internal["adjustment_mm"], "Surface internal feature adjustment"
        ),
        multiple_features=multiple,
        reverse_pass_order=_boolean(
            surface["reverse_pass_order"], "Surface reverse pass order"
        ),
        optimize_linear_paths=_boolean(
            optimization["linear_paths"], "Surface linear-path optimization"
        ),
        optimize_stepover_transitions=_boolean(
            optimization["stepover_transitions"],
            "Surface stepover-transition optimization",
        ),
        gap_threshold_mm=finite_number(
            optimization["gap_threshold_mm"],
            "Surface gap threshold",
            minimum=0.0,
        ),
        use_start_point=use_start,
        start_point_mm=start_point,
        mesh_deflection_mm=_positive(
            surface["mesh_deflection_mm"],
            "Surface mesh deflection",
            minimum=0.001,
        ),
        start_depth_mm=start_depth,
        final_depth_mm=final_depth,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        coolant=coolant,
    )


def _geometry_facts(
    boundary: PreparedOperationBoundary,
    request: OCLGeometryRequest,
    parameters: SurfaceParameters,
    tool: OCLToolFacts,
) -> SurfaceGeometryFacts:
    geometry = inspect_ocl_geometry(
        boundary,
        request,
        bounds=parameters.bounds,
        noun="Surface",
    )
    if parameters.start_depth_mm < geometry.derived_source_top_mm:
        _error(
            "Surface start_depth_mm must be at or above the derived source top "
            f"of {geometry.derived_source_top_mm:g} mm."
        )
    if parameters.final_depth_mm < geometry.stock_bottom_mm:
        _error(
            "Surface final_depth_mm cannot be below the exact Job stock bottom "
            f"of {geometry.stock_bottom_mm:g} mm."
        )
    stepover = tool.diameter_mm * parameters.stepover_percent / 100.0
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
    estimate = sum(
        max(1, math.ceil(span / stepover) + 1)
        * max(2, math.ceil(span / parameters.sample_interval_mm) + 1)
        * layer_count
        for x_span, y_span in geometry.spans_xy_mm
        for span in (math.hypot(x_span, y_span),)
    )
    if estimate > _MAX_ESTIMATED_DROP_CUTTER_POINTS:
        _error(
            "Surface would require approximately "
            f"{estimate:,} drop-cutter samples, above the synchronous safety "
            f"limit of {_MAX_ESTIMATED_DROP_CUTTER_POINTS:,}. Select fewer Faces, "
            "increase sample_interval_mm or stepover_percent, or split the machining "
            "region so the VibeCAD UI remains responsive.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    return SurfaceGeometryFacts(
        face_count=geometry.face_count,
        cutting_face_count=geometry.cutting_face_count,
        avoided_face_count=geometry.avoided_face_count,
        derived_source_top_mm=geometry.derived_source_top_mm,
        derived_operation_floor_mm=geometry.derived_operation_floor_mm,
        stock_bottom_mm=geometry.stock_bottom_mm,
        estimated_drop_cutter_points=estimate,
    )


def preflight_surface_create(
    document: Any,
    spec: SurfaceCreateSpec,
) -> PreparedSurfaceCreate:
    """Freeze exact geometry, resources, settings, and a bounded workload."""

    if not isinstance(spec, SurfaceCreateSpec):
        raise TypeError("spec must be a SurfaceCreateSpec")
    parameters = _normalize_parameters(spec)
    geometry_request = normalize_ocl_geometry(spec.geometry, noun="Surface")
    boundary = preflight_operation_boundary(
        document,
        noun="Surface",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=geometry_request.shared_request,
        allowed_subelement_types=frozenset({"Face"}),
        allow_entire_job=True,
    )
    tool = validate_ocl_tool(boundary, noun="Surface")
    geometry = _geometry_facts(boundary, geometry_request, parameters, tool)
    return PreparedSurfaceCreate(
        label=clean_operation_label(spec.label, "Surface"),
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


def _pattern_payload(parameters: SurfaceParameters) -> dict[str, Any]:
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
        if parameters.pattern in {"circular", "circular_zigzag"}:
            result["emit_arcs"] = parameters.emit_arcs
    return result


def _parameter_payload(prepared: PreparedSurfaceCreate) -> dict[str, Any]:
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
        "surface": {
            "bounds": parameters.bounds,
            "cut_mode": parameters.cut_mode,
            "pattern": _pattern_payload(parameters),
            "layers": layers,
            "stepover_percent": parameters.stepover_percent,
            "depth_offset_mm": parameters.depth_offset_mm,
            "sample_interval_mm": parameters.sample_interval_mm,
            "profile_edges": parameters.profile_edges,
            "boundary": {
                "enforce": parameters.boundary_enforcement,
                "adjustment_mm": parameters.boundary_adjustment_mm,
            },
            "internal_features": {
                "cut": parameters.cut_internal_features,
                "adjustment_mm": parameters.internal_features_adjustment_mm,
            },
            "multiple_features": parameters.multiple_features,
            "reverse_pass_order": parameters.reverse_pass_order,
            "optimization": {
                "linear_paths": parameters.optimize_linear_paths,
                "stepover_transitions": parameters.optimize_stepover_transitions,
                "gap_threshold_mm": parameters.gap_threshold_mm,
            },
            "start": start,
            "mesh_deflection_mm": parameters.mesh_deflection_mm,
        },
        "depths": {
            "start_depth_mm": parameters.start_depth_mm,
            "final_depth_mm": parameters.final_depth_mm,
            "effective_step_down_mm": parameters.step_down_mm,
            "derived_operation_floor_mm": prepared.geometry.derived_operation_floor_mm,
        },
        "heights": {
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
        "coolant": parameters.coolant,
    }


def _apply_settings(operation: Any, prepared: PreparedSurfaceCreate) -> None:
    import FreeCAD as App

    parameters = prepared.parameters
    request = prepared.geometry_request
    clear_operation_expressions(
        operation,
        (
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.ScanType = "Planar"
    operation.BoundBox = _BOUND_NAMES[parameters.bounds]
    operation.CutMode = _CUT_MODES[parameters.cut_mode]
    operation.CutPattern = _PATTERN_NAMES[parameters.pattern]
    operation.CutPatternAngle = parameters.angle_degrees
    operation.PatternCenterAt = _PATTERN_CENTER_NAMES[parameters.pattern_center]
    operation.PatternCenterCustom = App.Vector(*parameters.pattern_center_mm, 0.0)
    operation.CircularUseG2G3 = parameters.emit_arcs
    operation.LayerMode = (
        "Single-pass" if parameters.layer_mode == "single_pass" else "Multi-pass"
    )
    operation.StepOver = parameters.stepover_percent
    operation.DepthOffset = f"{parameters.depth_offset_mm} mm"
    operation.SampleInterval = f"{parameters.sample_interval_mm} mm"
    operation.ProfileEdges = _PROFILE_EDGES[parameters.profile_edges]
    operation.AvoidLastX_Faces = request.avoid_last_face_count
    operation.AvoidLastX_InternalFeatures = request.avoid_internal_features
    operation.BoundaryEnforcement = parameters.boundary_enforcement
    operation.BoundaryAdjustment = f"{parameters.boundary_adjustment_mm} mm"
    operation.InternalFeaturesCut = parameters.cut_internal_features
    operation.InternalFeaturesAdjustment = (
        f"{parameters.internal_features_adjustment_mm} mm"
    )
    operation.HandleMultipleFeatures = _MULTIPLE_FEATURES[parameters.multiple_features]
    operation.CutPatternReversed = parameters.reverse_pass_order
    operation.OptimizeLinearPaths = parameters.optimize_linear_paths
    operation.OptimizeStepOverTransitions = parameters.optimize_stepover_transitions
    operation.GapThreshold = f"{parameters.gap_threshold_mm} mm"
    operation.LinearDeflection = f"{parameters.mesh_deflection_mm} mm"
    operation.AngularDeflection = "0.25 mm"
    operation.UseStartPoint = parameters.use_start_point
    operation.StartPoint = App.Vector(*parameters.start_point_mm)
    operation.OpStartDepth = f"{prepared.geometry.derived_source_top_mm} mm"
    operation.OpFinalDepth = f"{prepared.geometry.derived_operation_floor_mm} mm"
    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]
    operation.Workplane = App.Vector(0.0, 0.0, 1.0)
    operation.ShowTempObjects = False
    operation.DropCutterDir = "X"
    operation.DropCutterExtraOffset = App.Vector(0.0, 0.0, 0.0)
    operation.RotationAxis = "X"
    operation.StartIndex = 0.0
    operation.StopIndex = 360.0
    operation.CutterTilt = 0.0


def create_surface(
    document: Any,
    *,
    prepared: PreparedSurfaceCreate,
) -> NativeMutationDraft:
    """Create one shipped Surface operation inside the owned transaction."""

    if not isinstance(prepared, PreparedSurfaceCreate):
        raise TypeError("prepared must be a PreparedSurfaceCreate")
    import Path.Op.Surface as PathSurface

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Surface"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Surface",
        operation_factory=PathSurface.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, surface_prepared=prepared)


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


def _assert_surface_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedSurfaceCreate,
) -> None:
    parameters = prepared.parameters
    request = prepared.geometry_request
    expected = {
        "scan_type": "Planar",
        "bounds": _BOUND_NAMES[parameters.bounds],
        "cut_mode": _CUT_MODES[parameters.cut_mode],
        "pattern": _PATTERN_NAMES[parameters.pattern],
        "angle_degrees": parameters.angle_degrees,
        "pattern_center": _PATTERN_CENTER_NAMES[parameters.pattern_center],
        "emit_arcs": parameters.emit_arcs,
        "layer_mode": (
            "Single-pass" if parameters.layer_mode == "single_pass" else "Multi-pass"
        ),
        "stepover_percent": parameters.stepover_percent,
        "depth_offset_mm": parameters.depth_offset_mm,
        "sample_interval_mm": parameters.sample_interval_mm,
        "profile_edges": _PROFILE_EDGES[parameters.profile_edges],
        "avoid_last_face_count": request.avoid_last_face_count,
        "avoid_internal_features": request.avoid_internal_features,
        "boundary_enforcement": parameters.boundary_enforcement,
        "boundary_adjustment_mm": parameters.boundary_adjustment_mm,
        "cut_internal_features": parameters.cut_internal_features,
        "internal_features_adjustment_mm": parameters.internal_features_adjustment_mm,
        "multiple_features": _MULTIPLE_FEATURES[parameters.multiple_features],
        "reverse_pass_order": parameters.reverse_pass_order,
        "optimize_linear_paths": parameters.optimize_linear_paths,
        "optimize_stepover_transitions": parameters.optimize_stepover_transitions,
        "gap_threshold_mm": parameters.gap_threshold_mm,
        "mesh_deflection_mm": parameters.mesh_deflection_mm,
        "use_start_point": parameters.use_start_point,
        "start_point_mm": parameters.start_point_mm,
        "derived_source_top_mm": prepared.geometry.derived_source_top_mm,
        "derived_operation_floor_mm": prepared.geometry.derived_operation_floor_mm,
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
        "workplane": (0.0, 0.0, 1.0),
        "show_temporary_objects": False,
        "rotation_axis": "X",
        "rotation_start_degrees": 0.0,
        "rotation_stop_degrees": 360.0,
        "cutter_tilt_degrees": 0.0,
    }
    actual = {
        "scan_type": str(operation.ScanType),
        "bounds": str(operation.BoundBox),
        "cut_mode": str(operation.CutMode),
        "pattern": str(operation.CutPattern),
        "angle_degrees": round(float(operation.CutPatternAngle), 9),
        "pattern_center": str(operation.PatternCenterAt),
        "emit_arcs": bool(operation.CircularUseG2G3),
        "layer_mode": str(operation.LayerMode),
        "stepover_percent": int(operation.StepOver),
        "depth_offset_mm": quantity_mm(operation, "DepthOffset"),
        "sample_interval_mm": quantity_mm(operation, "SampleInterval"),
        "profile_edges": str(operation.ProfileEdges),
        "avoid_last_face_count": int(operation.AvoidLastX_Faces),
        "avoid_internal_features": bool(operation.AvoidLastX_InternalFeatures),
        "boundary_enforcement": bool(operation.BoundaryEnforcement),
        "boundary_adjustment_mm": quantity_mm(operation, "BoundaryAdjustment"),
        "cut_internal_features": bool(operation.InternalFeaturesCut),
        "internal_features_adjustment_mm": quantity_mm(
            operation, "InternalFeaturesAdjustment"
        ),
        "multiple_features": str(operation.HandleMultipleFeatures),
        "reverse_pass_order": bool(operation.CutPatternReversed),
        "optimize_linear_paths": bool(operation.OptimizeLinearPaths),
        "optimize_stepover_transitions": bool(operation.OptimizeStepOverTransitions),
        "gap_threshold_mm": quantity_mm(operation, "GapThreshold"),
        "mesh_deflection_mm": quantity_mm(operation, "LinearDeflection"),
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
        "rotation_axis": str(operation.RotationAxis),
        "rotation_start_degrees": round(float(operation.StartIndex), 9),
        "rotation_stop_degrees": round(float(operation.StopIndex), 9),
        "cutter_tilt_degrees": round(float(operation.CutterTilt), 9),
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
                actual_center, expected_center, strict=True
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
            "The created Surface did not retain: " + ", ".join(sorted(mismatches)) + ".",
            error_code="NATIVE_MANUFACTURE_SURFACE_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _surface_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedSurfaceCreate,
) -> Mapping[str, Any]:
    finite_center = _vector_tuple(operation.PatternCenterCustom)
    if not all(math.isfinite(value) for value in finite_center):
        _error(
            "The created Surface produced a non-finite pattern center.",
            "NATIVE_MANUFACTURE_SURFACE_POSTCONDITION_FAILED",
        )
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
            "The created Surface has no depth-bearing cutting moves.",
            "NATIVE_MANUFACTURE_SURFACE_POSTCONDITION_FAILED",
        )
    return {
        "target_mode": prepared.geometry_request.requested_kind,
        "face_count": prepared.geometry.face_count,
        "cutting_face_count": prepared.geometry.cutting_face_count,
        "avoided_face_count": prepared.geometry.avoided_face_count,
        "derived_source_top_mm": prepared.geometry.derived_source_top_mm,
        "derived_operation_floor_mm": prepared.geometry.derived_operation_floor_mm,
        "tool_shape_type": prepared.tool.shape_type,
        "ocl_cutter": prepared.tool.ocl_cutter,
        "tool_diameter_mm": prepared.tool.diameter_mm,
        "stepover_mm": prepared.stepover_mm,
        "estimated_drop_cutter_points": prepared.geometry.estimated_drop_cutter_points,
        "pattern_center_mm": list(finite_center),
        "minimum_cutting_z_mm": round(min(cutting_z), 9),
        "maximum_cutting_z_mm": round(max(cutting_z), 9),
        "gap_summary": str(operation.GapSizes),
    }


def verify_created_surface(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedSurfaceCreate = draft.value["surface_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="surface",
        assert_settings=partial(_assert_surface_settings, prepared=prepared),
        additional_verify=partial(_surface_result, prepared=prepared),
    )
