# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Adaptive operation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureExtensions import (
    PreparedFeatureExtensions,
    apply_feature_extensions,
    assert_feature_extension_settings,
    feature_extension_result,
    prepare_feature_extensions,
)
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
    shape_sha256,
    validate_operation_tool,
    verify_native_operation,
)
from VibeCADNativeMutation import NativeMutationDraft


_ADAPTIVE_FIELDS = frozenset(
    {
        "cut_region",
        "operation_type",
        "tolerance_mm",
        "stepover_percent",
        "lift_distance_mm",
        "keep_tool_down_ratio",
        "xy_stock_to_leave_mm",
        "force_inside_out",
        "finishing_profile",
        "use_outline",
        "rest_machining",
    }
)
_HELIX_ENTRY_FIELDS = frozenset(
    {
        "max_pitch_mm",
        "max_ramp_angle_degrees",
        "cone_angle_degrees",
        "max_diameter_percent",
        "min_diameter_percent",
    }
)
_DEPTH_FIELDS = frozenset(
    {"start_depth_mm", "final_depth_mm", "step_down_mm", "finish_step_mm"}
)
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_CUT_REGIONS = {"inside": "Inside", "outside": "Outside"}
_OPERATION_TYPES = {"clearing": "Clearing", "profiling": "Profiling"}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}


@dataclass(frozen=True, slots=True)
class AdaptiveCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    adaptive: Mapping[str, Any]
    helix_entry: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    extensions: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class AdaptiveParameters:
    cut_region: str
    operation_type: str
    tolerance_mm: float
    stepover_percent: float
    lift_distance_mm: float
    keep_tool_down_ratio: float
    xy_stock_to_leave_mm: float
    force_inside_out: bool
    finishing_profile: bool
    use_outline: bool
    rest_machining: bool
    max_pitch_mm: float
    max_ramp_angle_degrees: float
    cone_angle_degrees: float
    max_diameter_percent: int
    min_diameter_percent: int
    start_depth_mm: float
    final_depth_mm: float
    step_down_mm: float
    finish_step_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class PreparedAdaptiveCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: AdaptiveParameters
    extensions: PreparedFeatureExtensions
    stock: Any
    stock_shape_sha256: str
    tool_diameter_mm: float


@dataclass(frozen=True, slots=True)
class AdaptiveDefaultsSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: tuple[Mapping[str, Any], ...]
    coolant: Any


@dataclass(frozen=True, slots=True)
class PreparedAdaptiveDefaults:
    label: str
    boundary: PreparedOperationBoundary
    coolant: str
    stock: Any
    stock_shape_sha256: str
    tool_diameter_mm: float


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


def _percent_integer(value: Any, noun: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 10 <= value <= 100:
        _error(f"{noun} must be an integer from 10 through 100.")
    return value


def _normalize_parameters(spec: AdaptiveCreateSpec) -> AdaptiveParameters:
    adaptive = exact_fields(spec.adaptive, _ADAPTIVE_FIELDS, "Adaptive settings")
    helix = exact_fields(
        spec.helix_entry,
        _HELIX_ENTRY_FIELDS,
        "Adaptive helix entry",
    )
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Adaptive depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Adaptive heights")

    cut_region = str(adaptive["cut_region"] or "")
    if cut_region not in _CUT_REGIONS:
        _error("Adaptive cut_region must be inside or outside.")
    operation_type = str(adaptive["operation_type"] or "")
    if operation_type not in _OPERATION_TYPES:
        _error("Adaptive operation_type must be clearing or profiling.")
    tolerance = finite_number(
        adaptive["tolerance_mm"],
        "Adaptive tolerance",
        minimum=0.001,
        maximum=0.15,
    )
    stepover = finite_number(
        adaptive["stepover_percent"],
        "Adaptive stepover percent",
        minimum=0.1,
        maximum=100.0,
    )
    keep_down = _positive(
        adaptive["keep_tool_down_ratio"],
        "Adaptive keep-tool-down ratio",
    )
    maximum_diameter = _percent_integer(
        helix["max_diameter_percent"],
        "Adaptive maximum helix diameter percent",
    )
    minimum_diameter = _percent_integer(
        helix["min_diameter_percent"],
        "Adaptive minimum helix diameter percent",
    )
    if minimum_diameter > maximum_diameter:
        _error(
            "Adaptive min_diameter_percent cannot exceed max_diameter_percent."
        )
    cone = finite_number(
        helix["cone_angle_degrees"],
        "Adaptive helix cone angle",
        minimum=0.0,
        maximum=90.0,
    )
    if cone >= 90.0:
        _error("Adaptive cone_angle_degrees must be less than 90 degrees.")

    start = finite_number(depths["start_depth_mm"], "Adaptive start depth")
    final = finite_number(depths["final_depth_mm"], "Adaptive final depth")
    step_down = _positive(depths["step_down_mm"], "Adaptive step down")
    finish_step = finite_number(
        depths["finish_step_mm"],
        "Adaptive finish step",
        minimum=0.0,
    )
    if final >= start:
        _error("Adaptive final_depth_mm must be below start_depth_mm.")
    if finish_step > step_down:
        _error("Adaptive finish_step_mm cannot exceed step_down_mm.")
    safe = finite_number(heights["safe_height_mm"], "Adaptive safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Adaptive clearance height",
    )
    if safe < start:
        _error("Adaptive safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Adaptive clearance_height_mm must be at or above safe_height_mm.")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Adaptive coolant must be none, flood, or mist.")

    return AdaptiveParameters(
        cut_region=cut_region,
        operation_type=operation_type,
        tolerance_mm=tolerance,
        stepover_percent=stepover,
        lift_distance_mm=finite_number(
            adaptive["lift_distance_mm"],
            "Adaptive lift distance",
            minimum=0.0,
        ),
        keep_tool_down_ratio=keep_down,
        xy_stock_to_leave_mm=finite_number(
            adaptive["xy_stock_to_leave_mm"],
            "Adaptive XY stock to leave",
        ),
        force_inside_out=_boolean(
            adaptive["force_inside_out"],
            "Adaptive force_inside_out",
        ),
        finishing_profile=_boolean(
            adaptive["finishing_profile"],
            "Adaptive finishing_profile",
        ),
        use_outline=_boolean(adaptive["use_outline"], "Adaptive use_outline"),
        rest_machining=_boolean(
            adaptive["rest_machining"],
            "Adaptive rest_machining",
        ),
        max_pitch_mm=finite_number(
            helix["max_pitch_mm"],
            "Adaptive maximum helix pitch",
            minimum=0.0,
        ),
        max_ramp_angle_degrees=finite_number(
            helix["max_ramp_angle_degrees"],
            "Adaptive maximum helix ramp angle",
            minimum=0.0,
            maximum=90.0,
        ),
        cone_angle_degrees=cone,
        max_diameter_percent=maximum_diameter,
        min_diameter_percent=minimum_diameter,
        start_depth_mm=start,
        final_depth_mm=final,
        step_down_mm=step_down,
        finish_step_mm=finish_step,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        coolant=coolant,
    )


def _require_adaptive_engine() -> None:
    try:
        import area
    except Exception as exc:
        raise NativeManufactureError(
            "Adaptive is unavailable because the libarea toolpath engine is not loaded.",
            error_code="NATIVE_MANUFACTURE_ENGINE_UNAVAILABLE",
            repair={"native_error_type": type(exc).__name__},
        ) from exc
    if not callable(getattr(area, "Adaptive2d", None)):
        _error(
            "Adaptive is unavailable because libarea has no Adaptive2d engine.",
            "NATIVE_MANUFACTURE_ENGINE_UNAVAILABLE",
        )


def _validate_adaptive_geometry(
    boundary: PreparedOperationBoundary,
    *,
    use_outline: bool,
) -> None:
    import Part
    import Path
    import Path.Op.Adaptive as PathAdaptive

    if boundary.geometry_kind != "subelements":
        _error("Adaptive requires exact Face or closed horizontal Edge geometry.")
    selected_edges = []
    for item in boundary.geometry:
        for name in item.subelements:
            element = item.public_source.Shape.getElement(name)
            if name.startswith("Face"):
                try:
                    projected_source = (
                        Part.Face(element.OuterWire)
                        if use_outline
                        else element
                    )
                    projection = PathAdaptive.projectFacesToXY([projected_source])
                except Exception as exc:
                    raise NativeManufactureError(
                        f"Adaptive cannot project {item.public_source.Name}.{name} "
                        "onto the XY machining plane.",
                        error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                        repair={"native_error": str(exc)[:240]},
                    ) from exc
                if not tuple(getattr(projection, "Wires", ()) or ()):
                    _error(
                        f"Adaptive face {item.public_source.Name}.{name} has no usable "
                        "XY machining region.",
                        "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                    )
            else:
                if not Path.Geom.isHorizontal(element):
                    _error(
                        f"Adaptive edge {item.public_source.Name}.{name} is not horizontal."
                    )
                selected_edges.append(element)
    if selected_edges:
        try:
            groups = Part.sortEdges(selected_edges)
            closed = all(Part.Wire(group).isClosed() for group in groups)
        except Exception:
            groups = ()
            closed = False
        if not groups or not closed:
            _error(
                "Adaptive Edge geometry must form one or more closed horizontal wires; "
                "add the missing connected edges or select the bounded Face instead."
            )
    if use_outline and "Face" not in boundary.selected_types:
        _error("Adaptive use_outline requires at least one selected Face.")


def _prepare_stock(document: Any, boundary: PreparedOperationBoundary) -> tuple[Any, str]:
    stock = getattr(boundary.job, "Stock", None)
    shape = getattr(stock, "Shape", None)
    if (
        stock is None
        or getattr(stock, "Document", None) is not document
        or document.getObject(str(getattr(stock, "Name", ""))) is not stock
        or shape is None
        or bool(getattr(shape, "isNull", lambda: True)())
        or not tuple(getattr(shape, "Solids", ()) or ())
    ):
        _error(
            "Adaptive requires valid solid stock owned by the exact CAM Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    bounds = shape.BoundBox
    if not all(
        math.isfinite(float(value))
        for value in (
            bounds.XMin,
            bounds.XMax,
            bounds.YMin,
            bounds.YMax,
            bounds.ZMin,
            bounds.ZMax,
        )
    ):
        _error(
            "Adaptive CAM Job stock has invalid bounds.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return stock, shape_sha256(shape, "CAM Job stock")


def preflight_adaptive_create(
    document: Any,
    spec: AdaptiveCreateSpec,
) -> PreparedAdaptiveCreate:
    """Freeze the exact Job, stock, cutter, regions, extensions, and settings."""

    if not isinstance(spec, AdaptiveCreateSpec):
        raise TypeError("spec must be an AdaptiveCreateSpec")
    _require_adaptive_engine()
    parameters = _normalize_parameters(spec)
    boundary = preflight_operation_boundary(
        document,
        noun="Adaptive",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=spec.geometry,
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=False,
    )
    tool_diameter = validate_operation_tool(boundary)
    _validate_adaptive_geometry(boundary, use_outline=parameters.use_outline)
    stock, stock_hash = _prepare_stock(document, boundary)
    if parameters.rest_machining and not has_prior_cutting_operation(boundary):
        _error(
            "Adaptive rest_machining requires an earlier active cutting operation "
            "in the exact CAM Job."
        )
    extensions = prepare_feature_extensions(
        boundary,
        spec.extensions,
        noun="Adaptive",
        use_outline=parameters.use_outline,
    )
    return PreparedAdaptiveCreate(
        label=clean_operation_label(spec.label, "Adaptive"),
        boundary=boundary,
        parameters=parameters,
        extensions=extensions,
        stock=stock,
        stock_shape_sha256=stock_hash,
        tool_diameter_mm=tool_diameter,
    )


def preflight_adaptive_defaults(
    document: Any,
    spec: AdaptiveDefaultsSpec,
) -> PreparedAdaptiveDefaults:
    """Freeze exact Adaptive regions while retaining the human-operation defaults."""
    if not isinstance(spec, AdaptiveDefaultsSpec):
        raise TypeError("spec must be an AdaptiveDefaultsSpec")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Adaptive coolant must be none, flood, or mist.")
    _require_adaptive_engine()
    boundary = preflight_operation_boundary(
        document,
        noun="Adaptive",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry={"kind": "subelements", "items": list(spec.geometry)},
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=False,
    )
    tool_diameter = validate_operation_tool(boundary)
    _validate_adaptive_geometry(boundary, use_outline=False)
    stock, stock_hash = _prepare_stock(document, boundary)
    return PreparedAdaptiveDefaults(
        label=clean_operation_label(spec.label, "Adaptive"),
        boundary=boundary,
        coolant=coolant,
        stock=stock,
        stock_shape_sha256=stock_hash,
        tool_diameter_mm=tool_diameter,
    )


def _assert_stock_current(prepared: PreparedAdaptiveCreate) -> None:
    stock = prepared.stock
    document = prepared.boundary.job.Document
    shape = getattr(stock, "Shape", None)
    if (
        getattr(stock, "Document", None) is not document
        or document.getObject(str(getattr(stock, "Name", ""))) is not stock
        or getattr(prepared.boundary.job, "Stock", None) is not stock
        or shape is None
        or shape_sha256(shape, "CAM Job stock") != prepared.stock_shape_sha256
    ):
        _error(
            "CAM Job stock changed before Adaptive creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def _parameter_payload(prepared: PreparedAdaptiveCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    return {
        "adaptive": {
            "cut_region": parameters.cut_region,
            "operation_type": parameters.operation_type,
            "tolerance_mm": parameters.tolerance_mm,
            "stepover_percent": parameters.stepover_percent,
            "lift_distance_mm": parameters.lift_distance_mm,
            "keep_tool_down_ratio": parameters.keep_tool_down_ratio,
            "xy_stock_to_leave_mm": parameters.xy_stock_to_leave_mm,
            "force_inside_out": parameters.force_inside_out,
            "finishing_profile": parameters.finishing_profile,
            "use_outline": parameters.use_outline,
            "rest_machining": parameters.rest_machining,
        },
        "helix_entry": {
            "max_pitch_mm": parameters.max_pitch_mm,
            "max_ramp_angle_degrees": parameters.max_ramp_angle_degrees,
            "cone_angle_degrees": parameters.cone_angle_degrees,
            "max_diameter_percent": parameters.max_diameter_percent,
            "min_diameter_percent": parameters.min_diameter_percent,
        },
        "depths": {
            "start_depth_mm": parameters.start_depth_mm,
            "final_depth_mm": parameters.final_depth_mm,
            "step_down_mm": parameters.step_down_mm,
            "finish_step_mm": parameters.finish_step_mm,
        },
        "heights": {
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
        "coolant": parameters.coolant,
    }


def _apply_settings(operation: Any, prepared: PreparedAdaptiveCreate) -> None:
    import FreeCAD as App

    _assert_stock_current(prepared)
    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        (
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "FinishDepth",
            "SafeHeight",
            "ClearanceHeight",
            "LiftDistance",
            "KeepToolDownRatio",
            "StockToLeave",
            "ZStockToLeave",
            "HelixMaxPitch",
            "HelixMaxRampAngle",
            "HelixConeAngle",
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.Side = _CUT_REGIONS[parameters.cut_region]
    operation.OperationType = _OPERATION_TYPES[parameters.operation_type]
    operation.Tolerance = parameters.tolerance_mm
    operation.StepOverPercent = parameters.stepover_percent
    operation.LiftDistance = f"{parameters.lift_distance_mm} mm"
    operation.KeepToolDownRatio = parameters.keep_tool_down_ratio
    operation.StockToLeave = f"{parameters.xy_stock_to_leave_mm} mm"
    operation.ForceInsideOut = parameters.force_inside_out
    operation.FinishingProfile = parameters.finishing_profile
    operation.UseOutline = parameters.use_outline
    operation.UseRestMachining = parameters.rest_machining
    operation.HelixMaxPitch = f"{parameters.max_pitch_mm} mm"
    operation.HelixMaxRampAngle = f"{parameters.max_ramp_angle_degrees} deg"
    operation.HelixConeAngle = f"{parameters.cone_angle_degrees} deg"
    operation.HelixMaxDiameterPercent = parameters.max_diameter_percent
    operation.HelixMinDiameterPercent = parameters.min_diameter_percent
    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.FinishDepth = f"{parameters.finish_step_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]

    # These properties exist on the current object but have no controls on the
    # shipped task panel. Preserve deterministic fresh-operation behavior.
    operation.ModelAwareExperiment = False
    operation.OrderCutsByRegion = False
    operation.ZStockToLeave = "0 mm"
    operation.Locations = []
    operation.Workplane = App.Vector(0.0, 0.0, 1.0)
    operation.Stopped = False
    operation.StopProcessing = False
    operation.AdaptiveInputState = ""
    operation.AdaptiveOutputState = ""
    apply_feature_extensions(operation, prepared.extensions)


def create_adaptive(
    document: Any,
    *,
    prepared: PreparedAdaptiveCreate,
) -> NativeMutationDraft:
    """Create one native Adaptive operation inside the owned transaction."""

    if not isinstance(prepared, PreparedAdaptiveCreate):
        raise TypeError("prepared must be a PreparedAdaptiveCreate")
    import Path.Op.Adaptive as PathAdaptive

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Adaptive"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Adaptive",
        operation_factory=PathAdaptive.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, adaptive_prepared=prepared)


def _apply_adaptive_defaults(
    operation: Any,
    *,
    prepared: PreparedAdaptiveDefaults,
) -> None:
    operation.Label = prepared.label
    operation.CoolantMode = _COOLANT_MODES[prepared.coolant]


def create_adaptive_defaults(
    document: Any,
    *,
    prepared: PreparedAdaptiveDefaults,
) -> NativeMutationDraft:
    """Create Adaptive with the same defaults as the human command."""
    if not isinstance(prepared, PreparedAdaptiveDefaults):
        raise TypeError("prepared must be a PreparedAdaptiveDefaults")
    import Path.Op.Adaptive as PathAdaptive

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Adaptive"
    )
    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Adaptive",
        operation_factory=PathAdaptive.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_adaptive_defaults, prepared=prepared),
        payload={"parameters": {"source": "setup_defaults"}},
    )
    return extend_native_operation_draft(draft, adaptive_defaults=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _assert_adaptive_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedAdaptiveCreate,
) -> None:
    parameters = prepared.parameters
    actual = {
        "cut_region": str(operation.Side),
        "operation_type": str(operation.OperationType),
        "tolerance_mm": round(float(operation.Tolerance), 9),
        "stepover_percent": round(float(operation.StepOverPercent), 9),
        "lift_distance_mm": quantity_mm(operation, "LiftDistance"),
        "keep_tool_down_ratio": round(float(operation.KeepToolDownRatio.Value), 9),
        "xy_stock_to_leave_mm": quantity_mm(operation, "StockToLeave"),
        "force_inside_out": bool(operation.ForceInsideOut),
        "finishing_profile": bool(operation.FinishingProfile),
        "use_outline": bool(operation.UseOutline),
        "rest_machining": bool(operation.UseRestMachining),
        "max_pitch_mm": quantity_mm(operation, "HelixMaxPitch"),
        "max_ramp_angle_degrees": round(float(operation.HelixMaxRampAngle.Value), 9),
        "cone_angle_degrees": round(float(operation.HelixConeAngle.Value), 9),
        "max_diameter_percent": int(operation.HelixMaxDiameterPercent),
        "min_diameter_percent": int(operation.HelixMinDiameterPercent),
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "finish_step_mm": quantity_mm(operation, "FinishDepth"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "coolant": str(operation.CoolantMode),
        "model_aware_experiment": bool(operation.ModelAwareExperiment),
        "order_cuts_by_region": bool(operation.OrderCutsByRegion),
        "z_stock_to_leave_mm": quantity_mm(operation, "ZStockToLeave"),
        "locations": tuple(operation.Locations),
        "workplane": _vector_tuple(operation.Workplane),
        "stopped": bool(operation.Stopped),
        "stop_processing": bool(operation.StopProcessing),
    }
    expected = {
        "cut_region": _CUT_REGIONS[parameters.cut_region],
        "operation_type": _OPERATION_TYPES[parameters.operation_type],
        "tolerance_mm": parameters.tolerance_mm,
        "stepover_percent": parameters.stepover_percent,
        "lift_distance_mm": parameters.lift_distance_mm,
        "keep_tool_down_ratio": parameters.keep_tool_down_ratio,
        "xy_stock_to_leave_mm": parameters.xy_stock_to_leave_mm,
        "force_inside_out": parameters.force_inside_out,
        "finishing_profile": parameters.finishing_profile,
        "use_outline": parameters.use_outline,
        "rest_machining": parameters.rest_machining,
        "max_pitch_mm": parameters.max_pitch_mm,
        "max_ramp_angle_degrees": parameters.max_ramp_angle_degrees,
        "cone_angle_degrees": parameters.cone_angle_degrees,
        "max_diameter_percent": parameters.max_diameter_percent,
        "min_diameter_percent": parameters.min_diameter_percent,
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "finish_step_mm": parameters.finish_step_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
        "model_aware_experiment": False,
        "order_cuts_by_region": False,
        "z_stock_to_leave_mm": 0.0,
        "locations": (),
        "workplane": (0.0, 0.0, 1.0),
        "stopped": False,
        "stop_processing": False,
    }
    mismatches = {
        name: {"expected": expected_value, "actual": actual.get(name)}
        for name, expected_value in expected.items()
        if actual.get(name) != expected_value
    }
    for property_name in (
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "FinishDepth",
        "SafeHeight",
        "ClearanceHeight",
        "LiftDistance",
        "KeepToolDownRatio",
        "StockToLeave",
        "ZStockToLeave",
        "HelixMaxPitch",
        "HelixMaxRampAngle",
        "HelixConeAngle",
    ):
        expression = _expression(operation, property_name)
        if expression:
            mismatches[f"{property_name}_expression"] = {
                "expected": None,
                "actual": str(expression),
            }
    assert_feature_extension_settings(operation, prepared.extensions, mismatches)
    if not isinstance(operation.AdaptiveInputState, dict):
        mismatches["adaptive_input_state"] = {
            "expected": "generated input state",
            "actual": type(operation.AdaptiveInputState).__name__,
        }
    if not isinstance(operation.AdaptiveOutputState, list):
        mismatches["adaptive_output_state"] = {
            "expected": "generated result list",
            "actual": type(operation.AdaptiveOutputState).__name__,
        }
    if mismatches:
        raise NativeManufactureError(
            "The created Adaptive operation did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_ADAPTIVE_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _adaptive_result(
    _operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedAdaptiveCreate,
) -> Mapping[str, Any]:
    _assert_stock_current(prepared)
    return {
        "engine": "libarea.Adaptive2d",
        "tool_diameter_mm": prepared.tool_diameter_mm,
        "stock": {
            "object_name": str(prepared.stock.Name),
            "shape_sha256": prepared.stock_shape_sha256,
        },
        "extensions": feature_extension_result(prepared.extensions),
    }


def verify_created_adaptive(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedAdaptiveCreate = draft.value["adaptive_prepared"]
    minimum_cutting = 0 if prepared.parameters.rest_machining else 1
    return verify_native_operation(
        document,
        draft,
        result_key="adaptive",
        assert_settings=partial(_assert_adaptive_settings, prepared=prepared),
        additional_verify=partial(_adaptive_result, prepared=prepared),
        minimum_cutting_commands=minimum_cutting,
    )


def _adaptive_defaults_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedAdaptiveDefaults,
) -> Mapping[str, Any]:
    stock = prepared.stock
    document = prepared.boundary.job.Document
    shape = getattr(stock, "Shape", None)
    if (
        getattr(stock, "Document", None) is not document
        or document.getObject(str(getattr(stock, "Name", ""))) is not stock
        or getattr(prepared.boundary.job, "Stock", None) is not stock
        or shape is None
        or shape_sha256(shape, "CAM Job stock") != prepared.stock_shape_sha256
    ):
        _error(
            "CAM Job stock changed during Adaptive creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    return {
        "engine": "libarea.Adaptive2d",
        "tool_diameter_mm": prepared.tool_diameter_mm,
        "stock": {
            "object_name": str(stock.Name),
            "shape_sha256": prepared.stock_shape_sha256,
        },
        "parameters": {
            "source": "setup_defaults",
            "cut_region": str(operation.Side),
            "operation_type": str(operation.OperationType),
            "tolerance_mm": round(float(operation.Tolerance), 9),
            "stepover_percent": round(float(operation.StepOverPercent), 9),
            "start_depth_mm": quantity_mm(operation, "StartDepth"),
            "final_depth_mm": quantity_mm(operation, "FinalDepth"),
            "step_down_mm": quantity_mm(operation, "StepDown"),
            "safe_height_mm": quantity_mm(operation, "SafeHeight"),
            "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
            "coolant": str(operation.CoolantMode),
        },
    }


def verify_created_adaptive_defaults(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedAdaptiveDefaults = draft.value["adaptive_defaults"]
    return verify_native_operation(
        document,
        draft,
        result_key="adaptive",
        assert_settings=lambda _operation, _payload: None,
        additional_verify=partial(
            _adaptive_defaults_result,
            prepared=prepared,
        ),
    )
