# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Pocket Shape operation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
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
    verify_native_operation,
)
from VibeCADNativeManufacturePocketGeometry import (
    validate_pocket_feature_geometry,
)
from VibeCADNativeMutation import NativeMutationDraft


_POCKET_FIELDS = frozenset(
    {
        "cut_mode",
        "pattern",
        "stepover_percent",
        "material_allowance_mm",
        "ignore_holes",
        "minimize_travel",
        "rest_machining",
    }
)
_DEPTH_FIELDS = frozenset(
    {"start_depth_mm", "final_depth_mm", "step_down_mm", "finish_step_mm"}
)
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_PATTERN_NAMES = {
    "offset": "Offset",
    "zigzag": "ZigZag",
    "zigzag_offset": "ZigZagOffset",
    "line": "Line",
    "grid": "Grid",
}
_CUT_MODES = {"climb": "Climb", "conventional": "Conventional"}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}


@dataclass(frozen=True, slots=True)
class PocketShapeCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    pocket: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    extensions: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class PocketShapeParameters:
    cut_mode: str
    pattern: str
    angle_degrees: float | None
    stepover_percent: int
    material_allowance_mm: float
    ignore_holes: bool
    minimize_travel: bool
    rest_machining: bool
    start_depth_mm: float
    final_depth_mm: float
    step_down_mm: float
    finish_step_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class PreparedPocketShapeCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: PocketShapeParameters
    extensions: PreparedFeatureExtensions


@dataclass(frozen=True, slots=True)
class PocketShapeDefaultsSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: tuple[Mapping[str, Any], ...]
    coolant: Any


@dataclass(frozen=True, slots=True)
class PreparedPocketShapeDefaults:
    label: str
    boundary: PreparedOperationBoundary
    coolant: str


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


def _normalize_parameters(spec: PocketShapeCreateSpec) -> PocketShapeParameters:
    pocket = exact_fields(spec.pocket, _POCKET_FIELDS, "Pocket Shape settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Pocket Shape depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Pocket Shape heights")

    cut_mode = str(pocket["cut_mode"] or "")
    if cut_mode not in _CUT_MODES:
        _error("Pocket Shape cut_mode must be climb or conventional.")
    pattern = pocket["pattern"]
    if not isinstance(pattern, Mapping):
        _error("Pocket Shape pattern must be one closed pattern request.")
    pattern_kind = str(pattern.get("kind") or "")
    if pattern_kind == "offset":
        exact_fields(pattern, frozenset({"kind"}), "Offset pattern")
        angle = None
    elif pattern_kind in {"zigzag", "zigzag_offset", "line", "grid"}:
        exact_fields(
            pattern,
            frozenset({"kind", "angle_degrees"}),
            f"{pattern_kind} pattern",
        )
        angle = finite_number(
            pattern["angle_degrees"],
            "Pocket Shape pattern angle",
            minimum=-360_000.0,
            maximum=360_000.0,
        )
    else:
        _error(
            "Pocket Shape pattern kind must be offset, zigzag, zigzag_offset, line, or grid."
        )

    stepover = pocket["stepover_percent"]
    if (
        isinstance(stepover, bool)
        or not isinstance(stepover, int)
        or not 1 <= stepover <= 100
    ):
        _error("Pocket Shape stepover_percent must be an integer from 1 through 100.")
    start = finite_number(depths["start_depth_mm"], "Pocket Shape start depth")
    final = finite_number(depths["final_depth_mm"], "Pocket Shape final depth")
    step_down = _positive(depths["step_down_mm"], "Pocket Shape step down")
    finish_step = finite_number(
        depths["finish_step_mm"],
        "Pocket Shape finish step",
        minimum=0.0,
    )
    if final >= start:
        _error("Pocket Shape final_depth_mm must be below start_depth_mm.")
    if finish_step > step_down:
        _error("Pocket Shape finish_step_mm cannot exceed step_down_mm.")
    safe = finite_number(heights["safe_height_mm"], "Pocket Shape safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Pocket Shape clearance height",
    )
    if safe < start:
        _error("Pocket Shape safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Pocket Shape clearance_height_mm must be at or above safe_height_mm.")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Pocket Shape coolant must be none, flood, or mist.")
    minimize_travel = _boolean(
        pocket["minimize_travel"],
        "Pocket Shape minimize_travel",
    )
    if minimize_travel:
        _error(
            "Pocket Shape minimize_travel requires an explicit start point, which is not "
            "part of creation. Create this operation with minimize_travel=false, then use "
            "the Start Point operation before enabling minimum travel."
        )
    return PocketShapeParameters(
        cut_mode=cut_mode,
        pattern=pattern_kind,
        angle_degrees=angle,
        stepover_percent=stepover,
        material_allowance_mm=finite_number(
            pocket["material_allowance_mm"],
            "Pocket Shape material allowance",
        ),
        ignore_holes=_boolean(pocket["ignore_holes"], "Pocket Shape ignore_holes"),
        minimize_travel=minimize_travel,
        rest_machining=_boolean(
            pocket["rest_machining"],
            "Pocket Shape rest_machining",
        ),
        start_depth_mm=start,
        final_depth_mm=final,
        step_down_mm=step_down,
        finish_step_mm=finish_step,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        coolant=coolant,
    )


def preflight_pocket_shape_create(
    document: Any,
    spec: PocketShapeCreateSpec,
) -> PreparedPocketShapeCreate:
    """Freeze the exact Job, controller, model geometry, and Pocket parameters."""

    if not isinstance(spec, PocketShapeCreateSpec):
        raise TypeError("spec must be a PocketShapeCreateSpec")
    parameters = _normalize_parameters(spec)
    boundary = preflight_operation_boundary(
        document,
        noun="Pocket Shape",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=spec.geometry,
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=False,
    )
    validate_pocket_feature_geometry(boundary, noun="Pocket Shape")
    if parameters.ignore_holes and "Face" not in boundary.selected_types:
        _error("Pocket Shape ignore_holes requires at least one selected Face.")
    if parameters.rest_machining and not has_prior_cutting_operation(boundary):
        _error(
            "Pocket Shape rest_machining requires an earlier active cutting operation "
            "in the exact CAM Job."
        )
    extensions = prepare_feature_extensions(
        boundary,
        spec.extensions,
        noun="Pocket Shape",
        use_outline=parameters.ignore_holes,
    )
    return PreparedPocketShapeCreate(
        label=clean_operation_label(spec.label, "Pocket Shape"),
        boundary=boundary,
        parameters=parameters,
        extensions=extensions,
    )


def preflight_pocket_shape_defaults(
    document: Any,
    spec: PocketShapeDefaultsSpec,
) -> PreparedPocketShapeDefaults:
    """Freeze exact pocket geometry while retaining setup-owned process defaults."""

    if not isinstance(spec, PocketShapeDefaultsSpec):
        raise TypeError("spec must be a PocketShapeDefaultsSpec")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Pocket Shape coolant must be none, flood, or mist.")
    boundary = preflight_operation_boundary(
        document,
        noun="Pocket Shape",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry={"kind": "subelements", "items": list(spec.geometry)},
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=False,
    )
    validate_pocket_feature_geometry(boundary, noun="Pocket Shape")
    return PreparedPocketShapeDefaults(
        label=clean_operation_label(spec.label, "Pocket Shape"),
        boundary=boundary,
        coolant=coolant,
    )


def _parameter_payload(prepared: PreparedPocketShapeCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    pattern: dict[str, Any] = {"kind": parameters.pattern}
    if parameters.angle_degrees is not None:
        pattern["angle_degrees"] = parameters.angle_degrees
    return {
        "pocket": {
            "cut_mode": parameters.cut_mode,
            "pattern": pattern,
            "stepover_percent": parameters.stepover_percent,
            "material_allowance_mm": parameters.material_allowance_mm,
            "ignore_holes": parameters.ignore_holes,
            "minimize_travel": parameters.minimize_travel,
            "rest_machining": parameters.rest_machining,
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


def _apply_settings(operation: Any, prepared: PreparedPocketShapeCreate) -> None:
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
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.CutMode = _CUT_MODES[parameters.cut_mode]
    operation.ClearingPattern = _PATTERN_NAMES[parameters.pattern]
    if parameters.angle_degrees is not None:
        operation.Angle = parameters.angle_degrees
    operation.StepOver = parameters.stepover_percent
    operation.ExtraOffset = f"{parameters.material_allowance_mm} mm"
    operation.UseOutline = parameters.ignore_holes
    operation.MinTravel = parameters.minimize_travel
    operation.UseRestMachining = parameters.rest_machining
    operation.UseStartPoint = False
    operation.StartAt = "Center"
    operation.SortingMode = "Automatic"
    operation.ForceMaxStepOver = False
    operation.SplitArcs = False
    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.FinishDepth = f"{parameters.finish_step_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]
    apply_feature_extensions(operation, prepared.extensions)


def create_pocket_shape(
    document: Any,
    *,
    prepared: PreparedPocketShapeCreate,
) -> NativeMutationDraft:
    """Create one native Pocket Shape inside the caller's document transaction."""

    if not isinstance(prepared, PreparedPocketShapeCreate):
        raise TypeError("prepared must be a PreparedPocketShapeCreate")
    import Path.Op.PocketShape as PathPocketShape

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.PocketShape"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Pocket Shape",
        operation_factory=PathPocketShape.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, pocket_prepared=prepared)


def create_pocket_shape_defaults(
    document: Any,
    *,
    prepared: PreparedPocketShapeDefaults,
) -> NativeMutationDraft:
    """Create Pocket Shape with the same defaults as the human command."""

    if not isinstance(prepared, PreparedPocketShapeDefaults):
        raise TypeError("prepared must be a PreparedPocketShapeDefaults")
    import Path.Op.PocketShape as PathPocketShape

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.PocketShape"
    )

    def configure(operation: Any) -> None:
        operation.Label = prepared.label
        operation.CoolantMode = _COOLANT_MODES[prepared.coolant]

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Pocket Shape",
        operation_factory=PathPocketShape.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=configure,
        payload={"parameters": {"source": "setup_defaults"}},
    )
    return extend_native_operation_draft(draft, pocket_defaults=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _assert_pocket_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedPocketShapeCreate,
) -> None:
    parameters = prepared.parameters
    actual = {
        "cut_mode": str(operation.CutMode),
        "pattern": str(operation.ClearingPattern),
        "stepover_percent": int(operation.StepOver),
        "material_allowance_mm": quantity_mm(operation, "ExtraOffset"),
        "ignore_holes": bool(operation.UseOutline),
        "minimize_travel": bool(operation.MinTravel),
        "rest_machining": bool(operation.UseRestMachining),
        "use_start_point": bool(operation.UseStartPoint),
        "start_at": str(operation.StartAt),
        "sorting_mode": str(operation.SortingMode),
        "force_max_stepover": bool(operation.ForceMaxStepOver),
        "split_arcs": bool(operation.SplitArcs),
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "finish_step_mm": quantity_mm(operation, "FinishDepth"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "coolant": str(operation.CoolantMode),
    }
    expected = {
        "cut_mode": _CUT_MODES[parameters.cut_mode],
        "pattern": _PATTERN_NAMES[parameters.pattern],
        "stepover_percent": parameters.stepover_percent,
        "material_allowance_mm": parameters.material_allowance_mm,
        "ignore_holes": parameters.ignore_holes,
        "minimize_travel": parameters.minimize_travel,
        "rest_machining": parameters.rest_machining,
        "use_start_point": False,
        "start_at": "Center",
        "sorting_mode": "Automatic",
        "force_max_stepover": False,
        "split_arcs": False,
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "finish_step_mm": parameters.finish_step_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
    }
    if parameters.angle_degrees is not None:
        expected["angle_degrees"] = parameters.angle_degrees
        actual["angle_degrees"] = round(float(operation.Angle), 9)
    mismatches = {
        name: {"expected": value, "actual": actual.get(name)}
        for name, value in expected.items()
        if actual.get(name) != value
    }
    for property_name in (
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
    assert_feature_extension_settings(operation, prepared.extensions, mismatches)
    if mismatches:
        raise NativeManufactureError(
            "The created Pocket Shape did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_POCKET_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _extension_result(
    _operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedPocketShapeCreate,
) -> Mapping[str, Any]:
    return {"extensions": feature_extension_result(prepared.extensions)}


def verify_created_pocket_shape(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedPocketShapeCreate = draft.value["pocket_prepared"]
    minimum_cutting = 0 if prepared.parameters.rest_machining else 1
    return verify_native_operation(
        document,
        draft,
        result_key="pocket_shape",
        assert_settings=partial(_assert_pocket_settings, prepared=prepared),
        additional_verify=partial(_extension_result, prepared=prepared),
        minimum_cutting_commands=minimum_cutting,
    )


def _default_pocket_result(
    operation: Any,
    _payload: Mapping[str, Any],
) -> Mapping[str, Any]:
    return {
        "parameters": {
            "source": "setup_defaults",
            "cut_mode": str(operation.CutMode),
            "pattern": str(operation.ClearingPattern),
            "stepover_percent": int(operation.StepOver),
            "material_allowance_mm": quantity_mm(operation, "ExtraOffset"),
            "start_depth_mm": quantity_mm(operation, "StartDepth"),
            "final_depth_mm": quantity_mm(operation, "FinalDepth"),
            "step_down_mm": quantity_mm(operation, "StepDown"),
            "safe_height_mm": quantity_mm(operation, "SafeHeight"),
            "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
            "coolant": str(operation.CoolantMode),
        }
    }


def verify_created_pocket_shape_defaults(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    return verify_native_operation(
        document,
        draft,
        result_key="pocket_shape",
        assert_settings=lambda _operation, _payload: None,
        additional_verify=_default_pocket_result,
    )
