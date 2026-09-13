# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Engrave operation."""

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


_ENGRAVE_FIELDS = frozenset({"start_vertex"})
_DEPTH_FIELDS = frozenset({"start_depth_mm", "final_depth_mm", "step_down_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_LINKING_FIELDS = frozenset({"strategy", "collision_clearance_mm"})
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}


@dataclass(frozen=True, slots=True)
class EngraveCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    engrave: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    linking: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class EngraveParameters:
    start_vertex: int
    start_depth_mm: float
    final_depth_mm: float
    step_down_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    linking_strategy: str
    collision_clearance_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class PreparedEngraveCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: EngraveParameters
    wire_count: int
    closed_wire_edge_counts: tuple[int, ...]
    whole_model_resources: tuple[Any, ...]
    tool_diameter_mm: float


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
    if not isinstance(raw, Mapping):
        _error("Engrave geometry must be one closed geometry request.")
    kind = str(raw.get("kind") or "")
    if kind == "entire_job":
        if set(raw) != {"kind"}:
            _error("Engrave entire_job geometry contains only kind.")
        return {"kind": "entire_job"}
    if kind == "whole_models":
        if set(raw) != {"kind", "models"}:
            _error("Engrave whole_models geometry requires exactly kind and models.")
        models = raw["models"]
        if not isinstance(models, list) or not 1 <= len(models) <= 32:
            _error("Engrave whole_models geometry requires 1 through 32 models.")
        targets = [
            _exact_target(value, f"Engrave whole model {index}")
            for index, value in enumerate(models)
        ]
        names = [str(value["object_name"]) for value in targets]
        if len(names) != len(set(names)):
            _error("Engrave whole model targets must be distinct.")
        return {"kind": "whole_models", "models": targets}
    if kind == "edges":
        if set(raw) != {"kind", "items"}:
            _error("Engrave edges geometry requires exactly kind and items.")
        raw_items = raw["items"]
        if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 32:
            _error("Engrave edges geometry requires 1 through 32 model items.")
        items = []
        seen_models: set[str] = set()
        total = 0
        for index, raw_item in enumerate(raw_items):
            if not isinstance(raw_item, Mapping) or set(raw_item) != {
                "model",
                "edges",
            }:
                _error(f"Engrave edge item {index} requires model and edges.")
            model = _exact_target(raw_item["model"], f"Engrave edge item {index} model")
            name = str(model["object_name"])
            if name in seen_models:
                _error("Engrave edge items must target distinct models.")
            edges = raw_item["edges"]
            if not isinstance(edges, list) or not 1 <= len(edges) <= 64:
                _error(f"Engrave edge item {index} requires 1 through 64 edges.")
            names = [str(value or "") for value in edges]
            if len(names) != len(set(names)):
                _error("Engrave edge names must be unique per model.")
            total += len(names)
            if total > 64:
                _error("Engrave accepts at most 64 exact edges in total.")
            items.append({"model": model, "subelements": names})
            seen_models.add(name)
        return {"kind": "subelements", "items": items}
    _error("Engrave geometry kind must be entire_job, whole_models, or edges.")


def _normalize_parameters(spec: EngraveCreateSpec) -> EngraveParameters:
    engrave = exact_fields(spec.engrave, _ENGRAVE_FIELDS, "Engrave settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Engrave depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Engrave heights")
    linking = exact_fields(spec.linking, _LINKING_FIELDS, "Engrave linking")
    start_vertex = engrave["start_vertex"]
    if (
        isinstance(start_vertex, bool)
        or not isinstance(start_vertex, int)
        or not 0 <= start_vertex <= 999_999
    ):
        _error("Engrave start_vertex must be an integer from 0 through 999999.")
    start = finite_number(depths["start_depth_mm"], "Engrave start depth")
    final = finite_number(depths["final_depth_mm"], "Engrave final depth")
    if final >= start:
        _error("Engrave final_depth_mm must be below start_depth_mm.")
    step_down = finite_number(
        depths["step_down_mm"],
        "Engrave step down",
        minimum=0.0,
    )
    if step_down <= 0.0:
        _error("Engrave step_down_mm must be greater than zero.")
    safe = finite_number(heights["safe_height_mm"], "Engrave safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Engrave clearance height",
    )
    if safe < start:
        _error("Engrave safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Engrave clearance_height_mm must be at or above safe_height_mm.")
    linking_strategy = str(linking["strategy"] or "")
    if linking_strategy not in LINKING_STRATEGIES:
        _error(
            "Engrave linking strategy must be clearance_height, retract_height, "
            "line_of_sight, tool_diameter, or tool_shape."
        )
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Engrave coolant must be none, flood, or mist.")
    return EngraveParameters(
        start_vertex=start_vertex,
        start_depth_mm=start,
        final_depth_mm=final,
        step_down_mm=step_down,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        linking_strategy=linking_strategy,
        collision_clearance_mm=finite_number(
            linking["collision_clearance_mm"],
            "Engrave collision clearance",
            minimum=0.0,
        ),
        coolant=coolant,
    )


def _whole_model_wires(source: Any) -> tuple[Any, ...]:
    import Part

    shape = source.Shape
    if str(getattr(shape, "ShapeType", "")) == "Edge":
        return (Part.Wire(shape),)
    try:
        volume = float(shape.Volume)
    except (AttributeError, TypeError, ValueError):
        volume = math.inf
    wires = tuple(shape.Wires)
    if not math.isfinite(volume) or abs(volume) >= 1.0e-9 or not wires:
        _error(
            f"Engrave whole model {source.Name!r} must be a zero-volume Part "
            "shape containing at least one wire.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return wires


def _edge_wires(item: Any) -> tuple[Any, ...]:
    import Part

    try:
        edges = [item.public_source.Shape.getElement(name) for name in item.subelements]
        groups = Part.sortEdges(edges)
        wires = tuple(Part.Wire(group) for group in groups if group)
    except Exception as exc:
        raise NativeManufactureError(
            f"Selected Engrave edges on {item.public_source.Name!r} cannot form wires.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if not wires:
        _error(
            f"Selected Engrave edges on {item.public_source.Name!r} form no usable wire.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return wires


def _prepared_wires(boundary: PreparedOperationBoundary) -> tuple[Any, ...]:
    if boundary.geometry_kind == "subelements":
        return tuple(wire for item in boundary.geometry for wire in _edge_wires(item))
    if boundary.geometry_kind == "whole_models":
        return tuple(
            wire
            for item in boundary.geometry
            for wire in _whole_model_wires(item.public_source)
        )
    wires = []
    for item in boundary.geometry:
        source = item.public_source
        shape = source.Shape
        if str(getattr(shape, "ShapeType", "")) == "Edge":
            wires.extend(_whole_model_wires(source))
            continue
        try:
            volume = float(shape.Volume)
        except (AttributeError, TypeError, ValueError):
            continue
        if math.isfinite(volume) and abs(volume) < 1.0e-9 and tuple(shape.Wires):
            wires.extend(tuple(shape.Wires))
    if not wires:
        _error(
            "Engrave entire_job geometry requires at least one zero-volume wire "
            "model in the exact Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return tuple(wires)


def _validate_start_vertex(
    wires: tuple[Any, ...], start_vertex: int
) -> tuple[int, ...]:
    closed_counts = tuple(len(wire.Edges) for wire in wires if wire.isClosed())
    if start_vertex and not closed_counts:
        _error("Engrave start_vertex must be 0 when every selected wire is open.")
    invalid = tuple(count for count in closed_counts if start_vertex >= count)
    if invalid:
        _error(
            f"Engrave start_vertex {start_vertex} is outside at least one closed "
            f"wire; the smallest selected closed wire has {min(closed_counts)} edges."
        )
    return closed_counts


def preflight_engrave_create(
    document: Any,
    spec: EngraveCreateSpec,
) -> PreparedEngraveCreate:
    """Freeze exact Engrave geometry, controller, and visible task values."""

    if not isinstance(spec, EngraveCreateSpec):
        raise TypeError("spec must be an EngraveCreateSpec")
    parameters = _normalize_parameters(spec)
    geometry = _normalize_geometry(spec.geometry)
    boundary = preflight_operation_boundary(
        document,
        noun="Engrave",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=geometry,
        allowed_subelement_types=frozenset({"Edge"}),
        allow_entire_job=True,
    )
    wires = _prepared_wires(boundary)
    closed_counts = _validate_start_vertex(wires, parameters.start_vertex)
    diameter = validate_operation_tool_linking(
        boundary,
        parameters.linking_strategy,
    )
    return PreparedEngraveCreate(
        label=clean_operation_label(spec.label, "Engrave"),
        boundary=boundary,
        parameters=parameters,
        wire_count=len(wires),
        closed_wire_edge_counts=closed_counts,
        whole_model_resources=(
            tuple(item.job_resource for item in boundary.geometry)
            if boundary.geometry_kind == "whole_models"
            else ()
        ),
        tool_diameter_mm=diameter,
    )


def _parameter_payload(prepared: PreparedEngraveCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    return {
        "engrave": {"start_vertex": parameters.start_vertex},
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


def _apply_settings(operation: Any, prepared: PreparedEngraveCreate) -> None:
    import FreeCAD as App

    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        (
            "StartVertex",
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
            "CollisionClearance",
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.BaseShapes = list(prepared.whole_model_resources)
    operation.StartVertex = parameters.start_vertex
    operation.Reverse = False
    operation.CutPattern = "Bidirectional"
    operation.Approximation = False
    operation.SortingMode = "Automatic"
    operation.StartPoint = App.Vector(0.0, 0.0, 0.0)
    operation.UseEndPoint = False
    operation.EndPoint = App.Vector(0.0, 0.0, 0.0)
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


def create_engrave(
    document: Any,
    *,
    prepared: PreparedEngraveCreate,
) -> NativeMutationDraft:
    """Create one native Engrave operation inside the owned transaction."""

    if not isinstance(prepared, PreparedEngraveCreate):
        raise TypeError("prepared must be a PreparedEngraveCreate")
    import Path.Op.Engrave as PathEngrave

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Engrave"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Engrave",
        operation_factory=PathEngrave.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, engrave_prepared=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _assert_engrave_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedEngraveCreate,
) -> None:
    parameters = prepared.parameters
    actual = {
        "base_shapes": tuple(operation.BaseShapes),
        "start_vertex": int(operation.StartVertex),
        "reverse": bool(operation.Reverse),
        "cut_pattern": str(operation.CutPattern),
        "approximation": bool(operation.Approximation),
        "sorting": str(operation.SortingMode),
        "start_point": _vector_tuple(operation.StartPoint),
        "use_end_point": bool(operation.UseEndPoint),
        "end_point": _vector_tuple(operation.EndPoint),
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
        "base_shapes": prepared.whole_model_resources,
        "start_vertex": parameters.start_vertex,
        "reverse": False,
        "cut_pattern": "Bidirectional",
        "approximation": False,
        "sorting": "Automatic",
        "start_point": (0.0, 0.0, 0.0),
        "use_end_point": False,
        "end_point": (0.0, 0.0, 0.0),
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
        name: {"expected": str(expected_value), "actual": str(actual.get(name))}
        for name, expected_value in expected.items()
        if actual.get(name) != expected_value
    }
    for property_name in (
        "StartVertex",
        "StartDepth",
        "FinalDepth",
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
            "The created Engrave operation did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_ENGRAVE_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _engrave_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedEngraveCreate,
) -> Mapping[str, Any]:
    actual_wire_count = len(tuple(getattr(operation.Proxy, "wires", ()) or ()))
    if actual_wire_count != prepared.wire_count:
        raise NativeManufactureError(
            "The created Engrave operation did not process every frozen wire.",
            error_code="NATIVE_MANUFACTURE_ENGRAVE_POSTCONDITION_FAILED",
            repair={
                "expected_wire_count": prepared.wire_count,
                "actual_wire_count": actual_wire_count,
            },
        )
    return {
        "wire_count": prepared.wire_count,
        "closed_wire_edge_counts": list(prepared.closed_wire_edge_counts),
        "start_vertex": prepared.parameters.start_vertex,
        "tool_diameter_mm": prepared.tool_diameter_mm,
    }


def verify_created_engrave(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedEngraveCreate = draft.value["engrave_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="engrave",
        assert_settings=partial(_assert_engrave_settings, prepared=prepared),
        additional_verify=partial(_engrave_result, prepared=prepared),
    )
