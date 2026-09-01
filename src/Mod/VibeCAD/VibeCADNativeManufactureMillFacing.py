# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Mill Facing operation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
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
    shape_sha256,
    validate_operation_tool_linking,
    verify_native_operation,
)
from VibeCADNativeMutation import NativeMutationDraft


_FACING_FIELDS = frozenset(
    {
        "cut_mode",
        "pattern",
        "angle_degrees",
        "reverse",
        "stepover_percent",
        "axial_stock_to_leave_mm",
        "pass_extension_mm",
        "stock_extension_mm",
    }
)
_DEPTH_FIELDS = frozenset({"start_depth_mm", "final_depth_mm", "step_down_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_LINKING_FIELDS = frozenset({"strategy", "collision_clearance_mm"})
_CUT_MODES = {"climb": "Climb", "conventional": "Conventional"}
_PATTERNS = {
    "zigzag": "ZigZag",
    "bidirectional": "Bidirectional",
    "directional": "Directional",
    "spiral": "Spiral",
}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}


@dataclass(frozen=True, slots=True)
class MillFacingCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    facing: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    linking: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class MillFacingParameters:
    cut_mode: str
    pattern: str
    angle_degrees: float
    reverse: bool
    stepover_percent: int
    axial_stock_to_leave_mm: float
    pass_extension_mm: float
    stock_extension_mm: float
    start_depth_mm: float
    final_depth_mm: float
    step_down_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    linking_strategy: str
    collision_clearance_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class PreparedMillFacingCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: MillFacingParameters
    stock: Any
    stock_shape_sha256: str
    stock_top_face_sha256: str


@dataclass(frozen=True, slots=True)
class MillFacingDefaultsSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class PreparedMillFacingDefaults:
    label: str
    boundary: PreparedOperationBoundary
    coolant: str
    stock: Any
    stock_shape_sha256: str
    stock_top_face_sha256: str


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


def _normalize_parameters(spec: MillFacingCreateSpec) -> MillFacingParameters:
    facing = exact_fields(spec.facing, _FACING_FIELDS, "Mill Facing settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Mill Facing depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Mill Facing heights")
    linking = exact_fields(spec.linking, _LINKING_FIELDS, "Mill Facing linking")
    cut_mode = str(facing["cut_mode"] or "")
    if cut_mode not in _CUT_MODES:
        _error("Mill Facing cut_mode must be climb or conventional.")
    pattern = str(facing["pattern"] or "")
    if pattern not in _PATTERNS:
        _error(
            "Mill Facing pattern must be zigzag, bidirectional, directional, or spiral."
        )
    angle = finite_number(
        facing["angle_degrees"],
        "Mill Facing angle",
        minimum=0.0,
        maximum=180.0,
    )
    stepover = facing["stepover_percent"]
    if (
        isinstance(stepover, bool)
        or not isinstance(stepover, int)
        or not 1 <= stepover <= 100
    ):
        _error("Mill Facing stepover_percent must be an integer from 1 through 100.")
    axial_leave = finite_number(
        facing["axial_stock_to_leave_mm"],
        "Mill Facing axial stock to leave",
        minimum=0.0,
    )
    start = finite_number(depths["start_depth_mm"], "Mill Facing start depth")
    final = finite_number(depths["final_depth_mm"], "Mill Facing final depth")
    step_down = _positive(depths["step_down_mm"], "Mill Facing step down")
    if final >= start:
        _error("Mill Facing final_depth_mm must be below start_depth_mm.")
    if final + axial_leave >= start:
        _error(
            "Mill Facing final_depth_mm plus axial_stock_to_leave_mm must remain below "
            "start_depth_mm."
        )
    safe = finite_number(heights["safe_height_mm"], "Mill Facing safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Mill Facing clearance height",
    )
    if safe < start:
        _error("Mill Facing safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Mill Facing clearance_height_mm must be at or above safe_height_mm.")
    strategy = str(linking["strategy"] or "")
    if strategy not in LINKING_STRATEGIES:
        _error(
            "Mill Facing linking strategy must be clearance_height, retract_height, "
            "line_of_sight, tool_diameter, or tool_shape."
        )
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Mill Facing coolant must be none, flood, or mist.")
    return MillFacingParameters(
        cut_mode=cut_mode,
        pattern=pattern,
        angle_degrees=angle,
        reverse=_boolean(facing["reverse"], "Mill Facing reverse"),
        stepover_percent=stepover,
        axial_stock_to_leave_mm=axial_leave,
        pass_extension_mm=finite_number(
            facing["pass_extension_mm"],
            "Mill Facing pass extension",
        ),
        stock_extension_mm=finite_number(
            facing["stock_extension_mm"],
            "Mill Facing stock extension",
        ),
        start_depth_mm=start,
        final_depth_mm=final,
        step_down_mm=step_down,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        linking_strategy=strategy,
        collision_clearance_mm=finite_number(
            linking["collision_clearance_mm"],
            "Mill Facing collision clearance",
            minimum=0.0,
        ),
        coolant=coolant,
    )


def _stock_top_face(stock: Any) -> Any:
    shape = getattr(stock, "Shape", None)
    if shape is None or bool(getattr(shape, "isNull", lambda: True)()):
        _error(
            "Mill Facing requires CAM Job stock with valid solid geometry.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    upward = []
    for face in tuple(getattr(shape, "Faces", ()) or ()):
        try:
            u_min, u_max, v_min, v_max = face.ParameterRange
            normal = face.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
        except Exception:
            continue
        if float(normal.z) > 0.9:
            upward.append(face)
    if not upward:
        _error(
            "Mill Facing requires Job stock with an upward-facing top face.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return max(upward, key=lambda face: float(face.BoundBox.ZMax))


def _validate_stock_boundary(stock: Any, parameters: MillFacingParameters) -> Any:
    import Path.Base.Generator.facing_common as facing_common

    top_face = _stock_top_face(stock)
    try:
        offset = top_face.OuterWire.makeOffset2D(parameters.stock_extension_mm, 2)
        polygon = facing_common.get_angled_polygon(offset, parameters.angle_degrees)
    except Exception as exc:
        raise NativeManufactureError(
            "Mill Facing stock_extension_mm and angle_degrees do not produce a valid "
            "stock boundary.",
            error_code="NATIVE_ARGUMENTS_INVALID",
            repair={
                "stock_extension_mm": parameters.stock_extension_mm,
                "angle_degrees": parameters.angle_degrees,
                "native_error": str(exc)[:240],
            },
        ) from exc
    if polygon is None or not tuple(getattr(polygon, "Edges", ()) or ()):
        _error("Mill Facing stock_extension_mm collapses the usable stock boundary.")
    return top_face


def preflight_mill_facing_create(
    document: Any,
    spec: MillFacingCreateSpec,
) -> PreparedMillFacingCreate:
    """Freeze the exact Job stock, model graph, controller, and Facing values."""

    if not isinstance(spec, MillFacingCreateSpec):
        raise TypeError("spec must be a MillFacingCreateSpec")
    parameters = _normalize_parameters(spec)
    boundary = preflight_operation_boundary(
        document,
        noun="Mill Facing",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry={"kind": "entire_job"},
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=True,
    )
    stock = getattr(boundary.job, "Stock", None)
    stock_shape = getattr(stock, "Shape", None)
    if (
        stock is None
        or getattr(stock, "Document", None) is not document
        or document.getObject(str(getattr(stock, "Name", ""))) is not stock
        or stock_shape is None
        or bool(getattr(stock_shape, "isNull", lambda: True)())
    ):
        _error(
            "Mill Facing requires valid current stock owned by the exact CAM Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    top_face = _validate_stock_boundary(stock, parameters)
    validate_operation_tool_linking(boundary, parameters.linking_strategy)
    return PreparedMillFacingCreate(
        label=clean_operation_label(spec.label, "Mill Facing"),
        boundary=boundary,
        parameters=parameters,
        stock=stock,
        stock_shape_sha256=shape_sha256(stock_shape, "CAM Job stock"),
        stock_top_face_sha256=shape_sha256(top_face, "CAM Job stock top face"),
    )


def preflight_mill_facing_defaults(
    document: Any,
    spec: MillFacingDefaultsSpec,
) -> PreparedMillFacingDefaults:
    """Freeze the stock and setup resources used by a default Facing operation."""

    if not isinstance(spec, MillFacingDefaultsSpec):
        raise TypeError("spec must be a MillFacingDefaultsSpec")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Mill Facing coolant must be none, flood, or mist.")
    boundary = preflight_operation_boundary(
        document,
        noun="Mill Facing",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry={"kind": "entire_job"},
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=True,
    )
    stock = getattr(boundary.job, "Stock", None)
    stock_shape = getattr(stock, "Shape", None)
    if (
        stock is None
        or getattr(stock, "Document", None) is not document
        or document.getObject(str(getattr(stock, "Name", ""))) is not stock
        or stock_shape is None
        or bool(getattr(stock_shape, "isNull", lambda: True)())
    ):
        _error(
            "Mill Facing requires valid current stock owned by the exact CAM Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    top_face = _stock_top_face(stock)
    return PreparedMillFacingDefaults(
        label=clean_operation_label(spec.label, "Mill Facing"),
        boundary=boundary,
        coolant=coolant,
        stock=stock,
        stock_shape_sha256=shape_sha256(stock_shape, "CAM Job stock"),
        stock_top_face_sha256=shape_sha256(top_face, "CAM Job stock top face"),
    )


def _assert_stock_current(prepared: PreparedMillFacingCreate) -> None:
    stock = prepared.stock
    document = prepared.boundary.job.Document
    stock_shape = getattr(stock, "Shape", None)
    if (
        getattr(stock, "Document", None) is not document
        or document.getObject(str(stock.Name)) is not stock
        or getattr(prepared.boundary.job, "Stock", None) is not stock
        or stock_shape is None
        or shape_sha256(stock_shape, "CAM Job stock") != prepared.stock_shape_sha256
        or shape_sha256(_stock_top_face(stock), "CAM Job stock top face")
        != prepared.stock_top_face_sha256
    ):
        _error(
            "CAM Job stock changed before Mill Facing creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def _parameter_payload(prepared: PreparedMillFacingCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    return {
        "facing": {
            "cut_mode": parameters.cut_mode,
            "pattern": parameters.pattern,
            "angle_degrees": parameters.angle_degrees,
            "reverse": parameters.reverse,
            "stepover_percent": parameters.stepover_percent,
            "axial_stock_to_leave_mm": parameters.axial_stock_to_leave_mm,
            "pass_extension_mm": parameters.pass_extension_mm,
            "stock_extension_mm": parameters.stock_extension_mm,
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


def _apply_settings(operation: Any, prepared: PreparedMillFacingCreate) -> None:
    _assert_stock_current(prepared)
    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        (
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
            "AxialStockToLeave",
            "PassExtension",
            "StockExtension",
            "CollisionClearance",
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.CutMode = _CUT_MODES[parameters.cut_mode]
    operation.ClearingPattern = _PATTERNS[parameters.pattern]
    operation.Angle = f"{parameters.angle_degrees} deg"
    operation.Reverse = parameters.reverse
    operation.StepOver = parameters.stepover_percent
    operation.AxialStockToLeave = f"{parameters.axial_stock_to_leave_mm} mm"
    operation.PassExtension = f"{parameters.pass_extension_mm} mm"
    operation.StockExtension = f"{parameters.stock_extension_mm} mm"
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


def create_mill_facing(
    document: Any,
    *,
    prepared: PreparedMillFacingCreate,
) -> NativeMutationDraft:
    """Create one native Mill Facing operation inside the owned transaction."""

    if not isinstance(prepared, PreparedMillFacingCreate):
        raise TypeError("prepared must be a PreparedMillFacingCreate")
    import Path.Op.MillFacing as PathMillFacing

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.MillFacing"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="MillFacing",
        operation_factory=PathMillFacing.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, facing_prepared=prepared)


def create_mill_facing_defaults(
    document: Any,
    *,
    prepared: PreparedMillFacingDefaults,
) -> NativeMutationDraft:
    """Create Facing with the same setup-owned defaults as the human command."""

    if not isinstance(prepared, PreparedMillFacingDefaults):
        raise TypeError("prepared must be a PreparedMillFacingDefaults")
    import Path.Op.MillFacing as PathMillFacing

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.MillFacing"
    )

    def configure(operation: Any) -> None:
        operation.Label = prepared.label
        operation.CoolantMode = _COOLANT_MODES[prepared.coolant]

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="MillFacing",
        operation_factory=PathMillFacing.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=configure,
        payload={"parameters": {"source": "setup_defaults"}},
    )
    return extend_native_operation_draft(draft, facing_defaults=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _assert_facing_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedMillFacingCreate,
) -> None:
    parameters = prepared.parameters
    actual = {
        "cut_mode": str(operation.CutMode),
        "pattern": str(operation.ClearingPattern),
        "angle_degrees": round(float(operation.Angle.Value), 9),
        "reverse": bool(operation.Reverse),
        "stepover_percent": int(operation.StepOver),
        "axial_stock_to_leave_mm": quantity_mm(operation, "AxialStockToLeave"),
        "pass_extension_mm": quantity_mm(operation, "PassExtension"),
        "stock_extension_mm": quantity_mm(operation, "StockExtension"),
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "linking_strategy": str(operation.CollisionAvoidanceStrategy),
        "collision_clearance_mm": quantity_mm(operation, "CollisionClearance"),
        "coolant": str(operation.CoolantMode),
    }
    expected = {
        "cut_mode": _CUT_MODES[parameters.cut_mode],
        "pattern": _PATTERNS[parameters.pattern],
        "angle_degrees": parameters.angle_degrees,
        "reverse": parameters.reverse,
        "stepover_percent": parameters.stepover_percent,
        "axial_stock_to_leave_mm": parameters.axial_stock_to_leave_mm,
        "pass_extension_mm": parameters.pass_extension_mm,
        "stock_extension_mm": parameters.stock_extension_mm,
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "linking_strategy": LINKING_STRATEGIES[parameters.linking_strategy],
        "collision_clearance_mm": parameters.collision_clearance_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
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
        "AxialStockToLeave",
        "PassExtension",
        "StockExtension",
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
            "The created Mill Facing operation did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_FACING_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _verify_stock(
    _operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedMillFacingCreate,
) -> Mapping[str, Any]:
    _assert_stock_current(prepared)
    return {
        "stock": {
            "object_name": str(prepared.stock.Name),
            "shape_sha256": prepared.stock_shape_sha256,
        }
    }


def verify_created_mill_facing(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedMillFacingCreate = draft.value["facing_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="mill_facing",
        assert_settings=partial(_assert_facing_settings, prepared=prepared),
        additional_verify=partial(_verify_stock, prepared=prepared),
    )


def _default_facing_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedMillFacingDefaults,
) -> Mapping[str, Any]:
    stock = prepared.stock
    stock_shape = getattr(stock, "Shape", None)
    if (
        getattr(stock, "Document", None) is not operation.Document
        or getattr(prepared.boundary.job, "Stock", None) is not stock
        or stock_shape is None
        or shape_sha256(stock_shape, "CAM Job stock") != prepared.stock_shape_sha256
        or shape_sha256(_stock_top_face(stock), "CAM Job stock top face")
        != prepared.stock_top_face_sha256
    ):
        _error(
            "CAM Job stock changed during Mill Facing creation.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    return {
        "stock": {
            "object_name": str(stock.Name),
            "shape_sha256": prepared.stock_shape_sha256,
        },
        "parameters": {
            "source": "setup_defaults",
            "cut_mode": str(operation.CutMode),
            "pattern": str(operation.ClearingPattern),
            "stepover_percent": int(operation.StepOver),
            "start_depth_mm": quantity_mm(operation, "StartDepth"),
            "final_depth_mm": quantity_mm(operation, "FinalDepth"),
            "step_down_mm": quantity_mm(operation, "StepDown"),
            "safe_height_mm": quantity_mm(operation, "SafeHeight"),
            "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
            "coolant": str(operation.CoolantMode),
        },
    }


def verify_created_mill_facing_defaults(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedMillFacingDefaults = draft.value["facing_defaults"]
    return verify_native_operation(
        document,
        draft,
        result_key="mill_facing",
        assert_settings=lambda _operation, _payload: None,
        additional_verify=partial(_default_facing_result, prepared=prepared),
    )
