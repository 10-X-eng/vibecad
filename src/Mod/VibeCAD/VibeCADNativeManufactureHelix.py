# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped internal CAM Helix operation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import math
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOperationSupport import (
    LINKING_STRATEGIES,
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
    validate_operation_tool_linking,
    verify_native_operation,
)
from VibeCADNativeMutation import NativeMutationDraft


_HELIX_FIELDS = frozenset(
    {
        "start_at",
        "cut_mode",
        "max_pitch_mm",
        "max_ramp_angle_degrees",
        "stepover_percent",
        "radial_stock_to_leave_outer_mm",
        "sorting",
    }
)
_DEPTH_FIELDS = frozenset({"start_depth_mm", "final_depth_mm", "step_down_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_LINKING_FIELDS = frozenset({"strategy", "collision_clearance_mm"})
_START_AT = {"inside": "Inside", "outside": "Outside"}
_CUT_MODES = {"climb": "Climb", "conventional": "Conventional"}
_SORTING_MODES = {"automatic": "Automatic", "manual": "Manual"}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_CENTER_TOLERANCE_MM = 1.0e-7


@dataclass(frozen=True, slots=True)
class HelixCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    helix: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    linking: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class HelixParameters:
    start_at: str
    cut_mode: str
    max_pitch_mm: float
    max_ramp_angle_degrees: float
    stepover_percent: int
    radial_stock_to_leave_outer_mm: float
    sorting: str
    start_depth_mm: float
    final_depth_mm: float
    step_down_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    linking_strategy: str
    collision_clearance_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class PreparedHelixFeature:
    object_name: str
    subelement: str
    center_x_mm: float
    center_y_mm: float
    diameter_mm: float


@dataclass(frozen=True, slots=True)
class PreparedHelixCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: HelixParameters
    features: tuple[PreparedHelixFeature, ...]
    tool_diameter_mm: float


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _positive(value: Any, noun: str) -> float:
    result = finite_number(value, noun, minimum=0.0)
    if result <= 0.0:
        _error(f"{noun} must be greater than zero.")
    return result


def _normalize_parameters(spec: HelixCreateSpec) -> HelixParameters:
    helix = exact_fields(spec.helix, _HELIX_FIELDS, "Helix settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Helix depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Helix heights")
    linking = exact_fields(spec.linking, _LINKING_FIELDS, "Helix linking")

    start_at = str(helix["start_at"] or "")
    if start_at not in _START_AT:
        _error("Helix start_at must be inside or outside.")
    cut_mode = str(helix["cut_mode"] or "")
    if cut_mode not in _CUT_MODES:
        _error("Helix cut_mode must be climb or conventional.")
    sorting = str(helix["sorting"] or "")
    if sorting not in _SORTING_MODES:
        _error("Helix sorting must be automatic or manual.")
    stepover = helix["stepover_percent"]
    if (
        isinstance(stepover, bool)
        or not isinstance(stepover, int)
        or not 1 <= stepover <= 100
    ):
        _error("Helix stepover_percent must be an integer from 1 through 100.")

    start = finite_number(depths["start_depth_mm"], "Helix start depth")
    final = finite_number(depths["final_depth_mm"], "Helix final depth")
    if final >= start:
        _error("Helix final_depth_mm must be below start_depth_mm.")
    safe = finite_number(heights["safe_height_mm"], "Helix safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Helix clearance height",
    )
    if safe < start:
        _error("Helix safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Helix clearance_height_mm must be at or above safe_height_mm.")
    strategy = str(linking["strategy"] or "")
    if strategy not in LINKING_STRATEGIES:
        _error(
            "Helix linking strategy must be clearance_height, retract_height, "
            "line_of_sight, tool_diameter, or tool_shape."
        )
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Helix coolant must be none, flood, or mist.")

    return HelixParameters(
        start_at=start_at,
        cut_mode=cut_mode,
        max_pitch_mm=finite_number(
            helix["max_pitch_mm"],
            "Helix maximum pitch",
            minimum=0.0,
        ),
        max_ramp_angle_degrees=finite_number(
            helix["max_ramp_angle_degrees"],
            "Helix maximum ramp angle",
            minimum=0.0,
            maximum=90.0,
        ),
        stepover_percent=stepover,
        radial_stock_to_leave_outer_mm=finite_number(
            helix["radial_stock_to_leave_outer_mm"],
            "Helix outer radial stock to leave",
        ),
        sorting=sorting,
        start_depth_mm=start,
        final_depth_mm=final,
        step_down_mm=_positive(depths["step_down_mm"], "Helix step down"),
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        linking_strategy=strategy,
        collision_clearance_mm=finite_number(
            linking["collision_clearance_mm"],
            "Helix collision clearance",
            minimum=0.0,
        ),
        coolant=coolant,
    )


def _feature_facts(source: Any, subelement: str) -> PreparedHelixFeature:
    import Part
    import Path.Base.Drillable as Drillable

    try:
        shape = source.Shape
        feature = shape.getElement(subelement)
    except Exception as exc:
        raise NativeManufactureError(
            f"Helix feature {source.Name}.{subelement} changed after turn start.",
            error_code="NATIVE_MANUFACTURE_TARGET_STALE",
        ) from exc
    try:
        drillable = bool(
            Drillable.isDrillable(
                shape,
                feature,
                vector=None,
                allowPartial=True,
            )
        )
    except Exception:
        drillable = False
    if not drillable:
        _error(
            f"Helix feature {source.Name}.{subelement} is not a circular Face or Edge "
            "accepted by the shipped Helix selection gate.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )

    center = None
    diameter = None
    if isinstance(feature, Part.Edge) and isinstance(feature.Curve, Part.Circle):
        center = feature.Curve.Center
        diameter = 2.0 * float(feature.Curve.Radius)
    elif isinstance(feature, Part.Face):
        if isinstance(feature.Surface, Part.Cylinder):
            center = feature.Surface.Center
        circular_edges = [
            edge for edge in feature.Edges if isinstance(edge.Curve, Part.Circle)
        ]
        if circular_edges:
            if center is None:
                center = circular_edges[0].Curve.Center
            diameter = 2.0 * float(circular_edges[0].Curve.Radius)
    if (
        center is None
        or diameter is None
        or not math.isfinite(diameter)
        or diameter <= 0.0
    ):
        _error(
            f"Helix feature {source.Name}.{subelement} has no usable circular center and "
            "diameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    coordinates = (float(center.x), float(center.y))
    if not all(math.isfinite(value) for value in coordinates):
        _error(
            f"Helix feature {source.Name}.{subelement} has no finite XY center.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return PreparedHelixFeature(
        object_name=str(source.Name),
        subelement=subelement,
        center_x_mm=round(coordinates[0], 9),
        center_y_mm=round(coordinates[1], 9),
        diameter_mm=round(diameter, 9),
    )


def _prepare_features(
    boundary: PreparedOperationBoundary,
    parameters: HelixParameters,
    tool_diameter_mm: float,
) -> tuple[PreparedHelixFeature, ...]:
    result = tuple(
        _feature_facts(item.public_source, subelement)
        for item in boundary.geometry
        for subelement in item.subelements
    )
    if not result:
        _error("Helix requires at least one exact circular Face or Edge.")
    for index, feature in enumerate(result):
        for prior in result[:index]:
            if (
                math.hypot(
                    feature.center_x_mm - prior.center_x_mm,
                    feature.center_y_mm - prior.center_y_mm,
                )
                <= _CENTER_TOLERANCE_MM
            ):
                _error(
                    "Helix features must have distinct XY centers; choose one exact "
                    "feature for each hole."
                )
        outer_radius = (
            feature.diameter_mm / 2.0
            - tool_diameter_mm / 2.0
            - parameters.radial_stock_to_leave_outer_mm
        )
        if outer_radius < -_CENTER_TOLERANCE_MM:
            _error(
                f"Helix feature {feature.object_name}.{feature.subelement} is too small "
                "for the selected cutter and radial_stock_to_leave_outer_mm.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
    return result


def preflight_helix_create(
    document: Any,
    spec: HelixCreateSpec,
) -> PreparedHelixCreate:
    """Freeze the exact Job, controller, circular features, and Helix values."""

    if not isinstance(spec, HelixCreateSpec):
        raise TypeError("spec must be a HelixCreateSpec")
    parameters = _normalize_parameters(spec)
    boundary = preflight_operation_boundary(
        document,
        noun="Helix",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=spec.geometry,
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=False,
    )
    tool_diameter = validate_operation_tool_linking(
        boundary,
        parameters.linking_strategy,
    )
    features = _prepare_features(boundary, parameters, tool_diameter)
    return PreparedHelixCreate(
        label=clean_operation_label(spec.label, "Helix"),
        boundary=boundary,
        parameters=parameters,
        features=features,
        tool_diameter_mm=tool_diameter,
    )


def _parameter_payload(prepared: PreparedHelixCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    return {
        "helix": {
            "start_at": parameters.start_at,
            "cut_mode": parameters.cut_mode,
            "max_pitch_mm": parameters.max_pitch_mm,
            "max_ramp_angle_degrees": parameters.max_ramp_angle_degrees,
            "stepover_percent": parameters.stepover_percent,
            "radial_stock_to_leave_outer_mm": (
                parameters.radial_stock_to_leave_outer_mm
            ),
            "sorting": parameters.sorting,
        },
        "depths": {
            "start_depth_mm": parameters.start_depth_mm,
            "final_depth_mm": parameters.final_depth_mm,
            "step_down_mm": parameters.step_down_mm,
        },
        "heights": {
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
        "linking": {
            "strategy": parameters.linking_strategy,
            "collision_clearance_mm": parameters.collision_clearance_mm,
        },
        "coolant": parameters.coolant,
    }


def _apply_settings(operation: Any, prepared: PreparedHelixCreate) -> None:
    import FreeCAD as App

    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        (
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
            "HelixMaxPitch",
            "HelixMaxRampAngle",
            "RadialStockToLeaveInner",
            "RadialStockToLeaveOuter",
            "HelixConeAngle",
            "OverrideProfileDiameter",
            "RotationAngle",
            "CollisionClearance",
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.StartAt = _START_AT[parameters.start_at]
    operation.CutMode = _CUT_MODES[parameters.cut_mode]
    operation.HelixMaxPitch = f"{parameters.max_pitch_mm} mm"
    operation.HelixMaxRampAngle = f"{parameters.max_ramp_angle_degrees} deg"
    operation.StepOver = parameters.stepover_percent
    operation.RadialStockToLeaveOuter = (
        f"{parameters.radial_stock_to_leave_outer_mm} mm"
    )
    operation.SortingMode = _SORTING_MODES[parameters.sorting]
    operation.StartPoint = App.Vector(0.0, 0.0, 0.0)
    operation.UseEndPoint = False
    operation.EndPoint = App.Vector(0.0, 0.0, 0.0)
    operation.Disabled = []

    # These properties exist in the current implementation but are hidden from
    # the shipped task panel. Freeze the same fresh-operation behavior instead
    # of leaking unsupported expert controls into the provider schema.
    operation.Side = "Inside"
    operation.RadialStockToLeaveInner = "0 mm"
    operation.HelixConeAngle = "0 deg"
    operation.SingleHelix = False
    operation.SpiralMill = False
    operation.FinishHelixCircle = True
    operation.FinishSpiralCircle = True
    operation.RetractFromWall = True
    operation.OverrideArcFeedRate = True
    operation.OverrideProfileDiameter = "0 mm"
    operation.RotationAngle = -1.0

    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CollisionAvoidanceStrategy = LINKING_STRATEGIES[
        parameters.linking_strategy
    ]
    operation.CollisionClearance = f"{parameters.collision_clearance_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]


def create_helix(
    document: Any,
    *,
    prepared: PreparedHelixCreate,
) -> NativeMutationDraft:
    """Create one native Helix operation inside the owned transaction."""

    if not isinstance(prepared, PreparedHelixCreate):
        raise TypeError("prepared must be a PreparedHelixCreate")
    import Path.Op.Helix as PathHelix

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Helix"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Helix",
        operation_factory=PathHelix.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, helix_prepared=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _assert_helix_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedHelixCreate,
) -> None:
    import Path.Op.Helix as PathHelix

    parameters = prepared.parameters
    actual = {
        "start_at": str(operation.StartAt),
        "cut_mode": str(operation.CutMode),
        "max_pitch_mm": quantity_mm(operation, "HelixMaxPitch"),
        "max_ramp_angle_degrees": round(float(operation.HelixMaxRampAngle.Value), 9),
        "stepover_percent": int(operation.StepOver),
        "radial_stock_to_leave_outer_mm": quantity_mm(
            operation,
            "RadialStockToLeaveOuter",
        ),
        "sorting": str(operation.SortingMode),
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "linking_strategy": str(operation.CollisionAvoidanceStrategy),
        "collision_clearance_mm": quantity_mm(operation, "CollisionClearance"),
        "coolant": str(operation.CoolantMode),
        "direction": str(operation.Direction),
        "side": str(operation.Side),
        "radial_stock_to_leave_inner_mm": quantity_mm(
            operation,
            "RadialStockToLeaveInner",
        ),
        "cone_angle_degrees": round(float(operation.HelixConeAngle.Value), 9),
        "single_helix": bool(operation.SingleHelix),
        "spiral_mill": bool(operation.SpiralMill),
        "finish_helix_circle": bool(operation.FinishHelixCircle),
        "finish_spiral_circle": bool(operation.FinishSpiralCircle),
        "retract_from_wall": bool(operation.RetractFromWall),
        "override_arc_feed_rate": bool(operation.OverrideArcFeedRate),
        "override_profile_diameter_mm": quantity_mm(
            operation,
            "OverrideProfileDiameter",
        ),
        "rotation_angle_degrees": round(float(operation.RotationAngle.Value), 9),
        "disabled": tuple(operation.Disabled),
        "start_point_mm": _vector_tuple(operation.StartPoint),
        "use_end_point": bool(operation.UseEndPoint),
        "end_point_mm": _vector_tuple(operation.EndPoint),
    }
    expected = {
        "start_at": _START_AT[parameters.start_at],
        "cut_mode": _CUT_MODES[parameters.cut_mode],
        "max_pitch_mm": parameters.max_pitch_mm,
        "max_ramp_angle_degrees": parameters.max_ramp_angle_degrees,
        "stepover_percent": parameters.stepover_percent,
        "radial_stock_to_leave_outer_mm": (parameters.radial_stock_to_leave_outer_mm),
        "sorting": _SORTING_MODES[parameters.sorting],
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "linking_strategy": LINKING_STRATEGIES[parameters.linking_strategy],
        "collision_clearance_mm": parameters.collision_clearance_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
        "direction": str(PathHelix._caclulatePathDirection(operation)),
        "side": "Inside",
        "radial_stock_to_leave_inner_mm": 0.0,
        "cone_angle_degrees": 0.0,
        "single_helix": False,
        "spiral_mill": False,
        "finish_helix_circle": True,
        "finish_spiral_circle": True,
        "retract_from_wall": True,
        "override_arc_feed_rate": True,
        "override_profile_diameter_mm": 0.0,
        "rotation_angle_degrees": -1.0,
        "disabled": (),
        "start_point_mm": (0.0, 0.0, 0.0),
        "use_end_point": False,
        "end_point_mm": (0.0, 0.0, 0.0),
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
        "SafeHeight",
        "ClearanceHeight",
        "HelixMaxPitch",
        "HelixMaxRampAngle",
        "RadialStockToLeaveInner",
        "RadialStockToLeaveOuter",
        "HelixConeAngle",
        "OverrideProfileDiameter",
        "RotationAngle",
        "CollisionClearance",
    ):
        expression = _expression(operation, property_name)
        if expression:
            mismatches[f"{property_name}_expression"] = {
                "expected": None,
                "actual": str(expression),
            }
    if mismatches:
        raise NativeManufactureError(
            "The created Helix operation did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_HELIX_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _feature_result(
    _operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedHelixCreate,
) -> Mapping[str, Any]:
    return {
        "feature_count": len(prepared.features),
        "tool_diameter_mm": prepared.tool_diameter_mm,
    }


def verify_created_helix(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedHelixCreate = draft.value["helix_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="helix",
        assert_settings=partial(_assert_helix_settings, prepared=prepared),
        additional_verify=partial(_feature_result, prepared=prepared),
    )
