# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Deburr operation."""

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


_DEBURR_FIELDS = frozenset({"width_mm", "extra_depth_mm", "direction"})
_DEPTH_FIELDS = frozenset({"step_down_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_LINKING_FIELDS = frozenset({"strategy", "collision_clearance_mm"})
_DIRECTIONS = {"clockwise": "CW", "counterclockwise": "CCW"}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_GEOMETRY_TOLERANCE_MM = 1.0e-7


@dataclass(frozen=True, slots=True)
class DeburrCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    deburr: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    linking: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class DeburrParameters:
    width_mm: float
    extra_depth_mm: float
    direction: str
    step_down_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    linking_strategy: str
    collision_clearance_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class DeburrFeatureFacts:
    feature_count: int
    edge_count: int
    face_count: int
    highest_z_mm: float


@dataclass(frozen=True, slots=True)
class DeburrToolFacts:
    diameter_mm: float
    cutting_edge_angle_degrees: float
    tip_radius_mm: float
    required_radius_mm: float
    path_depth_mm: float
    path_offset_mm: float


@dataclass(frozen=True, slots=True)
class PreparedDeburrCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: DeburrParameters
    features: DeburrFeatureFacts
    tool: DeburrToolFacts


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _exact_target(value: Any, noun: str) -> dict[str, Any]:
    if not isinstance(value, Mapping) or set(value) != {
        "object_name",
        "expected_state_sha256",
    }:
        _error(f"{noun} must be one exact model state target.")
    name = str(value.get("object_name") or "")
    digest = str(value.get("expected_state_sha256") or "")
    if not name or len(digest) != 64:
        _error(f"{noun} must contain a model name and 64-character state hash.")
    return dict(value)


def _normalize_geometry(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping) or set(raw) != {"kind", "items"}:
        _error("Deburr geometry requires exactly kind and items.")
    if str(raw.get("kind") or "") != "features":
        _error("Deburr geometry kind must be features.")
    raw_items = raw.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 32:
        _error("Deburr geometry requires 1 through 32 exact model items.")
    items = []
    seen_models: set[str] = set()
    total = 0
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping) or set(raw_item) != {
            "model",
            "features",
        }:
            _error(f"Deburr geometry item {index} requires model and features.")
        model = _exact_target(raw_item["model"], f"Deburr item {index} model")
        model_name = str(model["object_name"])
        if model_name in seen_models:
            _error("Deburr geometry items must target distinct models.")
        raw_features = raw_item["features"]
        if not isinstance(raw_features, list) or not 1 <= len(raw_features) <= 64:
            _error(f"Deburr geometry item {index} requires 1 through 64 features.")
        features = [str(value or "") for value in raw_features]
        if len(features) != len(set(features)):
            _error("Deburr feature names must be unique per model.")
        total += len(features)
        if total > 64:
            _error("Deburr accepts at most 64 exact features in total.")
        items.append({"model": model, "subelements": features})
        seen_models.add(model_name)
    return {"kind": "subelements", "items": items}


def _normalize_parameters(spec: DeburrCreateSpec) -> DeburrParameters:
    deburr = exact_fields(spec.deburr, _DEBURR_FIELDS, "Deburr settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Deburr depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Deburr heights")
    linking = exact_fields(spec.linking, _LINKING_FIELDS, "Deburr linking")
    direction = str(deburr["direction"] or "")
    if direction not in _DIRECTIONS:
        _error("Deburr direction must be clockwise or counterclockwise.")
    linking_strategy = str(linking["strategy"] or "")
    if linking_strategy not in LINKING_STRATEGIES:
        _error(
            "Deburr linking strategy must be clearance_height, retract_height, "
            "line_of_sight, tool_diameter, or tool_shape."
        )
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Deburr coolant must be none, flood, or mist.")
    safe = finite_number(heights["safe_height_mm"], "Deburr safe height")
    clearance = finite_number(heights["clearance_height_mm"], "Deburr clearance height")
    if clearance < safe:
        _error("Deburr clearance_height_mm must be at or above safe_height_mm.")
    width = finite_number(
        deburr["width_mm"], "Deburr width", minimum=0.0, maximum=1_000_000.0
    )
    if width <= 0.0:
        _error("Deburr width_mm must be greater than zero.")
    return DeburrParameters(
        width_mm=width,
        extra_depth_mm=finite_number(
            deburr["extra_depth_mm"],
            "Deburr extra depth",
            minimum=0.0,
            maximum=1_000_000.0,
        ),
        direction=direction,
        step_down_mm=finite_number(
            depths["step_down_mm"],
            "Deburr step down",
            minimum=0.0,
            maximum=1_000_000.0,
        ),
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        linking_strategy=linking_strategy,
        collision_clearance_mm=finite_number(
            linking["collision_clearance_mm"],
            "Deburr collision clearance",
            minimum=0.0,
        ),
        coolant=coolant,
    )


def _feature_facts(boundary: PreparedOperationBoundary) -> DeburrFeatureFacts:
    import FreeCAD as App
    import Part

    feature_count = 0
    edge_count = 0
    face_count = 0
    highest_z = -math.inf
    up = App.Vector(0.0, 0.0, 1.0)
    for item in boundary.geometry:
        for name in item.subelements:
            feature = item.public_source.Shape.getElement(name)
            feature_count += 1
            highest_z = max(highest_z, float(feature.BoundBox.ZMax))
            if str(feature.ShapeType) == "Edge":
                edge_count += 1
                if float(feature.Length) <= _GEOMETRY_TOLERANCE_MM:
                    _error(
                        f"Deburr feature {item.public_source.Name}.{name} is degenerate.",
                        "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                    )
                curve = getattr(feature, "Curve", None)
                if isinstance(curve, Part.Circle):
                    horizontal = abs(abs(float(curve.Axis.z)) - 1.0) <= 1.0e-7
                else:
                    horizontal = float(feature.BoundBox.ZLength) <= 1.0e-7
                if not horizontal:
                    _error(
                        f"Deburr Edge {item.public_source.Name}.{name} must lie in "
                        "one horizontal XY plane.",
                        "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                    )
                continue

            face_count += 1
            if not tuple(feature.Wires):
                _error(
                    f"Deburr Face {item.public_source.Name}.{name} has no usable boundary.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
            try:
                normal = feature.normalAt(0.0, 0.0)
            except Exception as exc:
                raise NativeManufactureError(
                    f"Deburr Face {item.public_source.Name}.{name} has no usable normal.",
                    error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                ) from exc
            z_span = float(feature.BoundBox.ZLength)
            if z_span <= _GEOMETRY_TOLERANCE_MM:
                if normal.dot(up) < 1.0 - 1.0e-7:
                    _error(
                        f"Deburr Face {item.public_source.Name}.{name} is horizontal "
                        "but does not face upward.",
                        "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                    )
                continue
            top_z = float(feature.BoundBox.ZMax)
            supported_lower_edge = False
            for edge in feature.Edges:
                vertices = tuple(edge.Vertexes)
                if not vertices:
                    continue
                edge_z = tuple(float(vertex.Point.z) for vertex in vertices)
                if (
                    max(edge_z) - min(edge_z) <= _GEOMETRY_TOLERANCE_MM
                    and max(edge_z) < top_z - _GEOMETRY_TOLERANCE_MM
                ):
                    supported_lower_edge = True
                    break
            if not supported_lower_edge:
                _error(
                    f"Deburr Face {item.public_source.Name}.{name} has no supported "
                    "lower horizontal boundary to project to its upper edge.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
    if not feature_count or not math.isfinite(highest_z):
        _error(
            "Deburr requires at least one usable Edge or Face.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return DeburrFeatureFacts(
        feature_count=feature_count,
        edge_count=edge_count,
        face_count=face_count,
        highest_z_mm=round(highest_z, 9),
    )


def _tool_number(tool: Any, property_name: str, default: float | None = None) -> float:
    value = getattr(tool, property_name, None)
    if hasattr(value, "Value"):
        value = value.Value
    if value is None and default is not None:
        return default
    try:
        result = float(value)
    except (TypeError, ValueError):
        _error(
            f"Deburr requires a numeric {property_name} on the selected ToolBit.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if not math.isfinite(result):
        _error(
            f"Deburr requires a finite {property_name} on the selected ToolBit.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return result


def _tool_facts(
    boundary: PreparedOperationBoundary,
    parameters: DeburrParameters,
) -> DeburrToolFacts:
    import Path.Op.Deburr as PathDeburr

    diameter = validate_operation_tool_linking(boundary, parameters.linking_strategy)
    tool = boundary.controller.Tool
    angle = _tool_number(tool, "CuttingEdgeAngle", 180.0)
    if angle < 0.0 or angle > 180.0:
        _error(
            "Deburr requires CuttingEdgeAngle from 0 through 180 degrees.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    end_mill = abs(angle) <= 1.0e-7 or abs(angle - 180.0) <= 1.0e-7
    if hasattr(tool, "TipDiameter"):
        tip_radius = _tool_number(tool, "TipDiameter") / 2.0
    elif hasattr(tool, "FlatRadius"):
        tip_radius = _tool_number(tool, "FlatRadius")
    else:
        tip_radius = 0.0
    if tip_radius < 0.0 or tip_radius > diameter / 2.0:
        _error(
            "Deburr ToolBit tip radius must be between zero and half its diameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if end_mill:
        required_radius = parameters.width_mm
    else:
        tangent = math.tan(math.radians(angle / 2.0))
        if tangent <= 0.0 or not math.isfinite(tangent):
            _error(
                "Deburr ToolBit cutting angle cannot generate the requested chamfer.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
        required_radius = (
            tip_radius + parameters.width_mm + parameters.extra_depth_mm * tangent
        )
    if required_radius > diameter / 2.0 + 1.0e-7:
        _error(
            "The requested Deburr width and extra depth exceed the selected "
            "ToolBit cutting radius.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    try:
        depth, offset, _extra_offset, _suppress = PathDeburr.toolDepthAndOffset(
            parameters.width_mm,
            parameters.extra_depth_mm,
            tool,
            False,
        )
    except Exception as exc:
        raise NativeManufactureError(
            "The selected ToolBit cannot calculate a Deburr path.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    depth = float(depth)
    offset = float(offset)
    if not math.isfinite(depth) or depth <= 0.0 or not math.isfinite(offset):
        _error(
            "The requested Deburr settings produce no positive cutting depth.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if hasattr(tool, "CuttingEdgeHeight"):
        cutting_height = _tool_number(tool, "CuttingEdgeHeight")
        if cutting_height > 0.0 and depth > cutting_height + 1.0e-7:
            _error(
                "The requested Deburr depth exceeds the ToolBit cutting-edge height.",
                "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            )
    return DeburrToolFacts(
        diameter_mm=round(diameter, 9),
        cutting_edge_angle_degrees=round(angle, 9),
        tip_radius_mm=round(tip_radius, 9),
        required_radius_mm=round(required_radius, 9),
        path_depth_mm=round(depth, 9),
        path_offset_mm=round(offset, 9),
    )


def preflight_deburr_create(
    document: Any,
    spec: DeburrCreateSpec,
) -> PreparedDeburrCreate:
    """Freeze exact Deburr features, controller, and visible task values."""

    if not isinstance(spec, DeburrCreateSpec):
        raise TypeError("spec must be a DeburrCreateSpec")
    parameters = _normalize_parameters(spec)
    boundary = preflight_operation_boundary(
        document,
        noun="Deburr",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=_normalize_geometry(spec.geometry),
        allowed_subelement_types=frozenset({"Edge", "Face"}),
        allow_entire_job=False,
    )
    features = _feature_facts(boundary)
    if parameters.safe_height_mm < features.highest_z_mm:
        _error(
            "Deburr safe_height_mm must be at or above the highest selected feature."
        )
    tool = _tool_facts(boundary, parameters)
    return PreparedDeburrCreate(
        label=clean_operation_label(spec.label, "Deburr"),
        boundary=boundary,
        parameters=parameters,
        features=features,
        tool=tool,
    )


def _parameter_payload(prepared: PreparedDeburrCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    return {
        "deburr": {
            "width_mm": parameters.width_mm,
            "extra_depth_mm": parameters.extra_depth_mm,
            "direction": parameters.direction,
        },
        "depths": {"step_down_mm": parameters.step_down_mm},
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


def _apply_settings(operation: Any, prepared: PreparedDeburrCreate) -> None:
    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        (
            "Width",
            "ExtraDepth",
            "EntryPoint",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
            "CollisionClearance",
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.Width = f"{parameters.width_mm} mm"
    operation.ExtraDepth = f"{parameters.extra_depth_mm} mm"
    operation.Join = "Round"
    operation.Direction = _DIRECTIONS[parameters.direction]
    operation.Side = "Outside"
    operation.EntryPoint = 0
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CollisionAvoidanceStrategy = LINKING_STRATEGIES[
        parameters.linking_strategy
    ]
    operation.CollisionClearance = f"{parameters.collision_clearance_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]


def create_deburr(
    document: Any,
    *,
    prepared: PreparedDeburrCreate,
) -> NativeMutationDraft:
    """Create one native Deburr operation inside the owned transaction."""

    if not isinstance(prepared, PreparedDeburrCreate):
        raise TypeError("prepared must be a PreparedDeburrCreate")
    import Path.Op.Deburr as PathDeburr

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Deburr"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Deburr",
        operation_factory=PathDeburr.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, deburr_prepared=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _assert_deburr_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedDeburrCreate,
) -> None:
    parameters = prepared.parameters
    actual = {
        "width_mm": quantity_mm(operation, "Width"),
        "extra_depth_mm": quantity_mm(operation, "ExtraDepth"),
        "join": str(operation.Join),
        "direction": str(operation.Direction),
        "side": str(operation.Side),
        "entry_point": int(operation.EntryPoint),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "linking_strategy": str(operation.CollisionAvoidanceStrategy),
        "collision_clearance_mm": quantity_mm(operation, "CollisionClearance"),
        "coolant": str(operation.CoolantMode),
    }
    expected = {
        "width_mm": parameters.width_mm,
        "extra_depth_mm": parameters.extra_depth_mm,
        "join": "Round",
        "direction": _DIRECTIONS[parameters.direction],
        "entry_point": 0,
        "step_down_mm": parameters.step_down_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "linking_strategy": LINKING_STRATEGIES[parameters.linking_strategy],
        "collision_clearance_mm": parameters.collision_clearance_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
    }
    mismatches = {
        name: {"expected": str(expected_value), "actual": str(actual.get(name))}
        for name, expected_value in expected.items()
        if actual.get(name) != expected_value
    }
    if actual["side"] not in {"Outside", "Inside"}:
        mismatches["side"] = {
            "expected": "a derived Outside or Inside value",
            "actual": actual["side"],
        }
    for property_name in (
        "Width",
        "ExtraDepth",
        "EntryPoint",
        "StepDown",
        "SafeHeight",
        "ClearanceHeight",
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
            "The created Deburr operation did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_DEBURR_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _deburr_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedDeburrCreate,
) -> Mapping[str, Any]:
    base_wire_count = len(tuple(getattr(operation.Proxy, "basewires", ()) or ()))
    adjusted_wire_count = len(
        tuple(getattr(operation.Proxy, "adjusted_basewires", ()) or ())
    )
    path_wire_count = len(tuple(getattr(operation.Proxy, "wires", ()) or ()))
    if (
        base_wire_count < 1
        or adjusted_wire_count != base_wire_count
        or path_wire_count < 1
    ):
        raise NativeManufactureError(
            "The created Deburr operation could not convert every selected feature "
            "set into a usable chamfer path.",
            error_code="NATIVE_MANUFACTURE_DEBURR_POSTCONDITION_FAILED",
            repair={
                "base_wire_count": base_wire_count,
                "adjusted_wire_count": adjusted_wire_count,
                "path_wire_count": path_wire_count,
            },
        )
    cutting_z = tuple(
        float(command.Parameters["Z"])
        for command in tuple(operation.Path.Commands)
        if str(command.Name) in {"G1", "G2", "G3"} and "Z" in command.Parameters
    )
    if not cutting_z:
        _error(
            "The created Deburr operation produced no depth-bearing cutting move.",
            "NATIVE_MANUFACTURE_DEBURR_POSTCONDITION_FAILED",
        )
    return {
        "features": {
            "feature_count": prepared.features.feature_count,
            "edge_count": prepared.features.edge_count,
            "face_count": prepared.features.face_count,
        },
        "base_wire_count": base_wire_count,
        "path_wire_count": path_wire_count,
        "derived_side": str(operation.Side),
        "tool": {
            "diameter_mm": prepared.tool.diameter_mm,
            "cutting_edge_angle_degrees": (prepared.tool.cutting_edge_angle_degrees),
            "tip_radius_mm": prepared.tool.tip_radius_mm,
            "required_radius_mm": prepared.tool.required_radius_mm,
        },
        "path_depth_mm": prepared.tool.path_depth_mm,
        "path_offset_mm": prepared.tool.path_offset_mm,
        "minimum_cutting_z_mm": round(min(cutting_z), 9),
    }


def verify_created_deburr(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedDeburrCreate = draft.value["deburr_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="deburr",
        assert_settings=partial(_assert_deburr_settings, prepared=prepared),
        additional_verify=partial(_deburr_result, prepared=prepared),
    )
