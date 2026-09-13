# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM V-carve operation."""

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
    validate_operation_tool,
    verify_native_operation,
)
from VibeCADNativeMutation import NativeMutationDraft


_VCARVE_FIELDS = frozenset(
    {
        "discretization_deflection_mm",
        "colinear_filter_degrees",
        "optimize_movements",
        "finishing",
    }
)
_DEPTH_FIELDS = frozenset({"final_depth_mm", "step_down_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_PLANAR_TOLERANCE_MM = 1.0e-7


@dataclass(frozen=True, slots=True)
class VCarveCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    v_carve: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class VCarveParameters:
    discretization_deflection_mm: float
    colinear_filter_degrees: float
    optimize_movements: bool
    finishing_pass: bool
    finishing_z_offset_mm: float
    final_depth_mm: float
    step_down_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class VCarveGeometryFacts:
    face_count: int
    boundary_wire_count: int
    surface_z_mm: float


@dataclass(frozen=True, slots=True)
class VCarveToolFacts:
    diameter_mm: float
    tip_diameter_mm: float
    cutting_edge_angle_degrees: float
    maximum_carve_depth_mm: float
    effective_final_depth_mm: float


@dataclass(frozen=True, slots=True)
class PreparedVCarveCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: VCarveParameters
    geometry: VCarveGeometryFacts
    tool: VCarveToolFacts
    whole_model_resources: tuple[Any, ...]
    geometry_tolerance_mm: float


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
        _error("V-carve geometry must be one closed geometry request.")
    kind = str(raw.get("kind") or "")
    if kind == "whole_models":
        if set(raw) != {"kind", "models"}:
            _error("V-carve whole_models geometry requires exactly kind and models.")
        models = raw.get("models")
        if not isinstance(models, list) or not 1 <= len(models) <= 32:
            _error("V-carve whole_models geometry requires 1 through 32 models.")
        targets = [
            _exact_target(value, f"V-carve whole model {index}")
            for index, value in enumerate(models)
        ]
        names = [str(value["object_name"]) for value in targets]
        if len(names) != len(set(names)):
            _error("V-carve whole model targets must be distinct.")
        return {"kind": "whole_models", "models": targets}
    if kind != "faces" or set(raw) != {"kind", "items"}:
        _error("V-carve geometry kind must be faces or whole_models.")
    raw_items = raw.get("items")
    if not isinstance(raw_items, list) or not 1 <= len(raw_items) <= 32:
        _error("V-carve faces geometry requires 1 through 32 model items.")
    items = []
    seen_models: set[str] = set()
    total = 0
    for index, raw_item in enumerate(raw_items):
        if not isinstance(raw_item, Mapping) or set(raw_item) != {"model", "faces"}:
            _error(f"V-carve face item {index} requires model and faces.")
        model = _exact_target(raw_item["model"], f"V-carve face item {index} model")
        model_name = str(model["object_name"])
        if model_name in seen_models:
            _error("V-carve face items must target distinct models.")
        raw_faces = raw_item["faces"]
        if not isinstance(raw_faces, list) or not 1 <= len(raw_faces) <= 64:
            _error(f"V-carve face item {index} requires 1 through 64 Faces.")
        faces = [str(value or "") for value in raw_faces]
        if len(faces) != len(set(faces)):
            _error("V-carve Face names must be unique per model.")
        total += len(faces)
        if total > 64:
            _error("V-carve accepts at most 64 exact Faces in total.")
        items.append({"model": model, "subelements": faces})
        seen_models.add(model_name)
    return {"kind": "subelements", "items": items}


def _normalize_finishing(raw: Any) -> tuple[bool, float]:
    if not isinstance(raw, Mapping):
        _error("V-carve finishing must be one closed finishing request.")
    enabled = raw.get("enabled")
    if enabled is False and set(raw) == {"enabled"}:
        return False, 0.0
    if enabled is True and set(raw) == {"enabled", "z_offset_mm"}:
        return True, finite_number(raw["z_offset_mm"], "V-carve finishing Z offset")
    _error(
        "V-carve finishing must be {enabled:false} or "
        "{enabled:true,z_offset_mm:number}."
    )


def _normalize_parameters(spec: VCarveCreateSpec) -> VCarveParameters:
    settings = exact_fields(spec.v_carve, _VCARVE_FIELDS, "V-carve settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "V-carve depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "V-carve heights")
    optimize = settings["optimize_movements"]
    if not isinstance(optimize, bool):
        _error("V-carve optimize_movements must be a boolean.")
    finishing, finishing_offset = _normalize_finishing(settings["finishing"])
    safe = finite_number(heights["safe_height_mm"], "V-carve safe height")
    clearance = finite_number(
        heights["clearance_height_mm"], "V-carve clearance height"
    )
    if clearance < safe:
        _error("V-carve clearance_height_mm must be at or above safe_height_mm.")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("V-carve coolant must be none, flood, or mist.")
    return VCarveParameters(
        discretization_deflection_mm=finite_number(
            settings["discretization_deflection_mm"],
            "V-carve discretization deflection",
            minimum=0.001,
            maximum=1.0,
        ),
        colinear_filter_degrees=finite_number(
            settings["colinear_filter_degrees"],
            "V-carve colinear filter",
            minimum=0.0,
            maximum=90.0,
        ),
        optimize_movements=optimize,
        finishing_pass=finishing,
        finishing_z_offset_mm=finishing_offset,
        final_depth_mm=finite_number(depths["final_depth_mm"], "V-carve final depth"),
        step_down_mm=finite_number(
            depths["step_down_mm"],
            "V-carve step down",
            minimum=0.0,
            maximum=1_000_000.0,
        ),
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        coolant=coolant,
    )


def _prepared_faces(boundary: PreparedOperationBoundary) -> tuple[Any, ...]:
    faces = []
    if boundary.geometry_kind == "whole_models":
        for item in boundary.geometry:
            shape = item.public_source.Shape
            try:
                volume = float(shape.Volume)
            except (AttributeError, TypeError, ValueError):
                volume = math.inf
            model_faces = tuple(face.copy() for face in shape.Faces)
            if not math.isfinite(volume) or abs(volume) >= 1.0e-9 or not model_faces:
                _error(
                    f"V-carve whole model {item.public_source.Name!r} must be a "
                    "zero-volume Part shape containing at least one Face.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
            faces.extend(model_faces)
        return tuple(faces)
    for item in boundary.geometry:
        faces.extend(
            item.public_source.Shape.getElement(name).copy()
            for name in item.subelements
        )
    return tuple(faces)


def _validate_voronoi_face(face: Any, deflection: float, index: int) -> int:
    import FreeCAD as App
    import Path

    if (
        not bool(face.isValid())
        or float(face.Area) <= _PLANAR_TOLERANCE_MM
        or float(face.BoundBox.ZLength) > _PLANAR_TOLERANCE_MM
        or not tuple(face.Wires)
    ):
        _error(
            f"V-carve Face {index} must be a valid nonempty planar XY Face.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    try:
        normal = face.normalAt(0.0, 0.0)
    except Exception as exc:
        raise NativeManufactureError(
            f"V-carve Face {index} has no usable surface normal.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if abs(abs(float(normal.z)) - 1.0) > 1.0e-7:
        _error(
            f"V-carve Face {index} must be parallel to the XY plane.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    diagram = Path.Voronoi.Diagram()
    boundary_count = 0
    try:
        for wire in face.Wires:
            if not wire.isClosed():
                _error(
                    f"V-carve Face {index} contains an open boundary.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
            points = [
                App.Vector(point.x, point.y)
                for point in wire.discretize(QuasiDeflection=deflection)
            ]
            if (
                len(points) > 1
                and points[-1].distanceToPoint(points[0])
                < App.Base.Precision.confusion()
            ):
                points.pop()
            if len(points) < 3:
                _error(
                    f"V-carve Face {index} has a boundary with fewer than three "
                    "usable points.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
            points.append(points[0])
            for first, second in zip(points, points[1:]):
                if first.distanceToPoint(second) <= _PLANAR_TOLERANCE_MM:
                    _error(
                        f"V-carve Face {index} has a degenerate boundary segment.",
                        "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                    )
                diagram.addSegment(first, second)
            boundary_count += 1
        diagram.construct()
    except NativeManufactureError:
        raise
    except Exception as exc:
        raise NativeManufactureError(
            f"V-carve Face {index} cannot form a Voronoi medial region.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if not tuple(diagram.Edges):
        _error(
            f"V-carve Face {index} produced no Voronoi medial geometry.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return boundary_count


def _geometry_facts(
    boundary: PreparedOperationBoundary,
    parameters: VCarveParameters,
) -> VCarveGeometryFacts:
    faces = _prepared_faces(boundary)
    if not faces:
        _error(
            "V-carve requires at least one exact planar Face.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    surface_z = tuple(round(float(face.BoundBox.ZMax), 7) for face in faces)
    if len(set(surface_z)) != 1:
        _error(
            "V-carve Faces must be coplanar at one exact Z height.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    wire_count = sum(
        _validate_voronoi_face(
            face,
            parameters.discretization_deflection_mm,
            index,
        )
        for index, face in enumerate(faces, start=1)
    )
    return VCarveGeometryFacts(
        face_count=len(faces),
        boundary_wire_count=wire_count,
        surface_z_mm=surface_z[0],
    )


def _tool_number(tool: Any, property_name: str) -> float:
    value = getattr(tool, property_name, None)
    if hasattr(value, "Value"):
        value = value.Value
    try:
        result = float(value)
    except (TypeError, ValueError):
        _error(
            f"V-carve requires a numeric {property_name} on the exact ToolBit.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if not math.isfinite(result):
        _error(
            f"V-carve requires a finite {property_name} on the exact ToolBit.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return result


def _tool_facts(
    boundary: PreparedOperationBoundary,
    parameters: VCarveParameters,
    geometry: VCarveGeometryFacts,
) -> VCarveToolFacts:
    diameter = validate_operation_tool(boundary)
    tool = boundary.controller.Tool
    if not hasattr(tool, "CuttingEdgeAngle") or not hasattr(tool, "TipDiameter"):
        _error(
            "V-carve requires a V-bit ToolBit with CuttingEdgeAngle and TipDiameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    angle = _tool_number(tool, "CuttingEdgeAngle")
    tip = _tool_number(tool, "TipDiameter")
    if angle <= 0.0 or angle >= 180.0:
        _error(
            "V-carve ToolBit CuttingEdgeAngle must be greater than zero and below "
            "180 degrees.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if tip < 0.0 or tip >= diameter:
        _error(
            "V-carve ToolBit TipDiameter must be nonnegative and below Diameter.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    tangent = math.tan(math.radians(angle / 2.0))
    maximum_depth = (diameter - tip) / (2.0 * tangent)
    if not math.isfinite(maximum_depth) or maximum_depth <= 0.0:
        _error(
            "The exact V-bit has no usable radial cutting depth.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if parameters.final_depth_mm >= geometry.surface_z_mm:
        _error("V-carve final_depth_mm must be below the selected Face plane.")
    effective_final = max(
        geometry.surface_z_mm - maximum_depth,
        parameters.final_depth_mm,
    )
    if (
        parameters.finishing_pass
        and effective_final + parameters.finishing_z_offset_mm >= geometry.surface_z_mm
    ):
        _error(
            "V-carve finishing z_offset_mm places the finishing path at or above "
            "the selected Face plane."
        )
    return VCarveToolFacts(
        diameter_mm=round(diameter, 9),
        tip_diameter_mm=round(tip, 9),
        cutting_edge_angle_degrees=round(angle, 9),
        maximum_carve_depth_mm=round(maximum_depth, 9),
        effective_final_depth_mm=round(effective_final, 9),
    )


def preflight_v_carve_create(
    document: Any,
    spec: VCarveCreateSpec,
) -> PreparedVCarveCreate:
    """Freeze exact V-carve faces, V-bit, and visible task values."""

    if not isinstance(spec, VCarveCreateSpec):
        raise TypeError("spec must be a VCarveCreateSpec")
    parameters = _normalize_parameters(spec)
    boundary = preflight_operation_boundary(
        document,
        noun="V-carve",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=_normalize_geometry(spec.geometry),
        allowed_subelement_types=frozenset({"Face"}),
        allow_entire_job=False,
    )
    geometry = _geometry_facts(boundary, parameters)
    if parameters.safe_height_mm < geometry.surface_z_mm:
        _error("V-carve safe_height_mm must be at or above the selected Face plane.")
    tool = _tool_facts(boundary, parameters, geometry)
    import Path

    tolerance = float(Path.Preferences.defaultGeometryTolerance())
    if not math.isfinite(tolerance) or tolerance <= 0.0:
        _error(
            "V-carve requires a positive CAM geometry tolerance.",
            "NATIVE_MANUFACTURE_STATE_INVALID",
        )
    return PreparedVCarveCreate(
        label=clean_operation_label(spec.label, "V-carve"),
        boundary=boundary,
        parameters=parameters,
        geometry=geometry,
        tool=tool,
        whole_model_resources=(
            tuple(item.job_resource for item in boundary.geometry)
            if boundary.geometry_kind == "whole_models"
            else ()
        ),
        geometry_tolerance_mm=round(tolerance, 12),
    )


def _parameter_payload(prepared: PreparedVCarveCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    finishing: dict[str, Any] = {"enabled": parameters.finishing_pass}
    if parameters.finishing_pass:
        finishing["z_offset_mm"] = parameters.finishing_z_offset_mm
    return {
        "v_carve": {
            "discretization_deflection_mm": (parameters.discretization_deflection_mm),
            "colinear_filter_degrees": parameters.colinear_filter_degrees,
            "optimize_movements": parameters.optimize_movements,
            "finishing": finishing,
        },
        "depths": {
            "final_depth_mm": parameters.final_depth_mm,
            "step_down_mm": parameters.step_down_mm,
        },
        "heights": {
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
        "coolant": parameters.coolant,
    }


def _apply_settings(operation: Any, prepared: PreparedVCarveCreate) -> None:
    import FreeCAD as App

    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        (
            "Discretize",
            "Colinear",
            "FinishingPassZOffset",
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
    operation.BaseShapes = list(prepared.whole_model_resources)
    operation.Discretize = parameters.discretization_deflection_mm
    operation.Colinear = parameters.colinear_filter_degrees
    operation.Tolerance = prepared.geometry_tolerance_mm
    operation.OptimizeMovements = parameters.optimize_movements
    operation.FinishingPass = parameters.finishing_pass
    operation.FinishingPassZOffset = f"{parameters.finishing_z_offset_mm} mm"
    operation.StartDepth = f"{prepared.geometry.surface_z_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]
    operation.Workplane = App.Vector(0.0, 0.0, 1.0)


def create_v_carve(
    document: Any,
    *,
    prepared: PreparedVCarveCreate,
) -> NativeMutationDraft:
    """Create one native V-carve operation inside the owned transaction."""

    if not isinstance(prepared, PreparedVCarveCreate):
        raise TypeError("prepared must be a PreparedVCarveCreate")
    import Path.Op.Vcarve as PathVCarve

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Vcarve"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Vcarve",
        operation_factory=PathVCarve.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, v_carve_prepared=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _assert_v_carve_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedVCarveCreate,
) -> None:
    parameters = prepared.parameters
    actual = {
        "base_shapes": tuple(operation.BaseShapes),
        "discretization": round(float(operation.Discretize), 9),
        "colinear": round(float(operation.Colinear), 9),
        "tolerance": round(float(operation.Tolerance), 12),
        "optimize": bool(operation.OptimizeMovements),
        "finishing": bool(operation.FinishingPass),
        "finishing_offset_mm": quantity_mm(operation, "FinishingPassZOffset"),
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "coolant": str(operation.CoolantMode),
        "workplane": _vector_tuple(operation.Workplane),
    }
    expected = {
        "base_shapes": prepared.whole_model_resources,
        "discretization": parameters.discretization_deflection_mm,
        "colinear": parameters.colinear_filter_degrees,
        "tolerance": prepared.geometry_tolerance_mm,
        "optimize": parameters.optimize_movements,
        "finishing": parameters.finishing_pass,
        "finishing_offset_mm": parameters.finishing_z_offset_mm,
        "start_depth_mm": prepared.geometry.surface_z_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
        "workplane": (0.0, 0.0, 1.0),
    }
    mismatches = {
        name: {"expected": str(expected_value), "actual": str(actual.get(name))}
        for name, expected_value in expected.items()
        if actual.get(name) != expected_value
    }
    for property_name in (
        "Discretize",
        "Colinear",
        "FinishingPassZOffset",
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
            "The created V-carve operation did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_VCARVE_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _v_carve_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedVCarveCreate,
) -> Mapping[str, Any]:
    medial = getattr(operation.Proxy, "voronoiDebugMedialCache", None)
    raw_edges = getattr(operation.Proxy, "voronoiDebugEdgeCache", None)
    if not isinstance(medial, dict) or len(medial) != prepared.geometry.face_count:
        raise NativeManufactureError(
            "The created V-carve operation did not retain one medial result per "
            "frozen Face.",
            error_code="NATIVE_MANUFACTURE_VCARVE_POSTCONDITION_FAILED",
            repair={
                "expected_face_count": prepared.geometry.face_count,
                "actual_face_count": len(medial) if isinstance(medial, dict) else 0,
            },
        )
    medial_wire_count = sum(len(wires) for wires in medial.values())
    medial_edge_count = sum(len(wire) for wires in medial.values() for wire in wires)
    if (
        medial_wire_count < prepared.geometry.face_count
        or medial_edge_count < medial_wire_count
        or not isinstance(raw_edges, dict)
        or len(raw_edges) != prepared.geometry.face_count
    ):
        raise NativeManufactureError(
            "The created V-carve operation produced incomplete Voronoi medial geometry.",
            error_code="NATIVE_MANUFACTURE_VCARVE_POSTCONDITION_FAILED",
            repair={
                "medial_wire_count": medial_wire_count,
                "medial_edge_count": medial_edge_count,
                "raw_face_count": len(raw_edges) if isinstance(raw_edges, dict) else 0,
            },
        )
    cutting_z = tuple(
        float(command.Parameters["Z"])
        for command in tuple(operation.Path.Commands)
        if str(command.Name) in {"G1", "G2", "G3"} and "Z" in command.Parameters
    )
    if not cutting_z:
        _error(
            "The created V-carve operation produced no depth-bearing cutting move.",
            "NATIVE_MANUFACTURE_VCARVE_POSTCONDITION_FAILED",
        )
    return {
        "face_count": prepared.geometry.face_count,
        "boundary_wire_count": prepared.geometry.boundary_wire_count,
        "medial_wire_count": medial_wire_count,
        "medial_edge_count": medial_edge_count,
        "surface_z_mm": prepared.geometry.surface_z_mm,
        "minimum_cutting_z_mm": round(min(cutting_z), 9),
        "tool": {
            "diameter_mm": prepared.tool.diameter_mm,
            "tip_diameter_mm": prepared.tool.tip_diameter_mm,
            "cutting_edge_angle_degrees": (prepared.tool.cutting_edge_angle_degrees),
            "maximum_carve_depth_mm": prepared.tool.maximum_carve_depth_mm,
            "effective_final_depth_mm": prepared.tool.effective_final_depth_mm,
        },
    }


def verify_created_v_carve(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedVCarveCreate = draft.value["v_carve_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="v_carve",
        assert_settings=partial(_assert_v_carve_settings, prepared=prepared),
        additional_verify=partial(_v_carve_result, prepared=prepared),
    )
