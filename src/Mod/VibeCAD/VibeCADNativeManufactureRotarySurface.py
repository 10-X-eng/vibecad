# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded, task-free Native CAM Rotary Surface creation."""

from __future__ import annotations

from dataclasses import dataclass
from functools import partial
import math
import re
from typing import Any, Mapping

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureOCL import OCLToolFacts, validate_ocl_tool
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
from VibeCADNativeManufactureState import configured_machine_state
from VibeCADNativeMutation import NativeMutationDraft


_ROTARY_FIELDS = frozenset(
    {
        "pattern",
        "cut_mode",
        "axial_window",
        "angular_resolution_degrees",
        "radial_stock_to_leave_mm",
        "layers",
        "feed_mode",
        "maximum_effective_feed_mm_per_min",
        "mesh",
    }
)
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_MESH_FIELDS = frozenset(
    {"linear_deflection_mm", "angular_deflection_radians"}
)
_CUT_MODES = {"climb": "Climb", "conventional": "Conventional"}
_PATTERNS = {"spiral": "Spiral", "parallel": "Parallel", "rings": "Rings"}
_FEED_MODES = {"axial_only": "AxialOnly", "surface_speed": "SurfaceSpeed"}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_ROTARY_LETTER = re.compile(r"^[ABC]$")
_MAX_PROCESSING_CELLS = 150_000
_MAX_ESTIMATED_COMMANDS = 500_000
_SETTING_TOLERANCE = 1.0e-7


@dataclass(frozen=True, slots=True)
class RotarySurfaceCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    geometry: Mapping[str, Any]
    rotary_surface: Mapping[str, Any]
    heights: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class RotaryGeometryRequest:
    shared_request: Mapping[str, Any]
    requested_kind: str


@dataclass(frozen=True, slots=True)
class RotaryParameters:
    pattern: str
    stepover_mm: float
    requested_start_angle_degrees: float
    requested_sweep_degrees: float
    cut_mode: str
    axial_window_kind: str
    requested_axial_start_mm: float
    requested_axial_stop_mm: float
    angular_resolution_degrees: float
    radial_stock_to_leave_mm: float
    layer_mode: str
    step_down_mm: float
    feed_mode: str
    maximum_effective_feed_mm_per_min: float
    linear_deflection_mm: float
    angular_deflection_radians: float
    safe_height_mm: float
    clearance_height_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class RotaryMachineFacts:
    state_sha256: str
    configuration_name: str
    axis_name: str
    world_axis: str
    direction: tuple[float, float, float]
    minimum_degrees: float
    maximum_degrees: float
    wrap_strategy: str


@dataclass(frozen=True, slots=True)
class RotaryGeometryFacts:
    axial_start_mm: float
    axial_stop_mm: float
    start_angle_degrees: float
    sweep_degrees: float
    stock_radius_mm: float
    face_count: int
    estimated_processing_cells: int
    estimated_command_ceiling: int
    radial_layer_ceiling: int
    generated_axis_minimum_degrees: float
    generated_axis_maximum_degrees: float


@dataclass(frozen=True, slots=True)
class PreparedRotarySurfaceCreate:
    label: str
    boundary: PreparedOperationBoundary
    geometry_request: RotaryGeometryRequest
    parameters: RotaryParameters
    machine: RotaryMachineFacts
    geometry: RotaryGeometryFacts
    tool: OCLToolFacts


def _error(message: str, code: str = "NATIVE_ARGUMENTS_INVALID") -> None:
    raise NativeManufactureError(message, error_code=code)


def _positive(
    value: Any,
    noun: str,
    *,
    minimum: float = 0.0,
    maximum: float = 1_000_000.0,
) -> float:
    result = finite_number(value, noun, minimum=minimum, maximum=maximum)
    if result <= 0.0:
        _error(f"{noun} must be greater than zero.")
    return result


def _normalize_geometry(raw: Any) -> RotaryGeometryRequest:
    if not isinstance(raw, Mapping):
        _error("Rotary Surface geometry must be one closed geometry request.")
    kind = str(raw.get("kind") or "")
    if kind == "entire_job":
        exact_fields(raw, frozenset({"kind"}), "Rotary Surface entire Job geometry")
        return RotaryGeometryRequest({"kind": "entire_job"}, kind)
    if kind != "faces":
        _error("Rotary Surface geometry kind must be entire_job or faces.")
    value = exact_fields(
        raw,
        frozenset({"kind", "items"}),
        "Rotary Surface face geometry",
    )
    items = value["items"]
    if not isinstance(items, list) or not 1 <= len(items) <= 32:
        _error("Rotary Surface face geometry requires 1 through 32 model items.")
    total = 0
    shared_items = []
    for item in items:
        exact = exact_fields(
            item,
            frozenset({"model", "faces"}),
            "Rotary Surface face item",
        )
        faces = exact["faces"]
        if not isinstance(faces, list) or not faces:
            _error("Each Rotary Surface face item requires at least one exact Face name.")
        names = [str(face) for face in faces]
        if len(names) != len(set(names)):
            _error("Rotary Surface Face names must be unique within each model item.")
        total += len(names)
        if total > 64:
            _error("A Rotary Surface request accepts at most 64 total Faces.")
        shared_items.append({"model": exact["model"], "subelements": names})
    return RotaryGeometryRequest(
        {"kind": "subelements", "items": shared_items},
        kind,
    )


def _normalize_pattern(raw: Any) -> tuple[str, float, float, float]:
    if not isinstance(raw, Mapping):
        _error("Rotary Surface pattern must be one closed pattern request.")
    kind = str(raw.get("kind") or "")
    if kind == "spiral":
        value = exact_fields(
            raw,
            frozenset({"kind", "axial_pitch_mm", "start_angle_degrees"}),
            "Rotary Surface spiral pattern",
        )
        start = finite_number(
            value["start_angle_degrees"],
            "Rotary Surface spiral start angle",
            minimum=0.0,
            maximum=359.999999,
        )
        return (
            kind,
            _positive(value["axial_pitch_mm"], "Rotary Surface axial pitch", minimum=0.001),
            start,
            360.0 - start,
        )
    if kind not in {"parallel", "rings"}:
        _error("Rotary Surface pattern kind must be spiral, parallel, or rings.")
    spacing_name = "surface_stepover_mm" if kind == "parallel" else "axial_spacing_mm"
    value = exact_fields(
        raw,
        frozenset({"kind", spacing_name, "start_angle_degrees", "sweep_degrees"}),
        f"Rotary Surface {kind} pattern",
    )
    start = finite_number(
        value["start_angle_degrees"],
        "Rotary Surface start angle",
        minimum=-360.0,
        maximum=360.0,
    )
    sweep = _positive(
        value["sweep_degrees"],
        "Rotary Surface angular sweep",
        maximum=360.0,
    )
    if start + sweep > 360.0:
        _error("Rotary Surface start_angle_degrees plus sweep_degrees cannot exceed 360.")
    return (
        kind,
        _positive(value[spacing_name], f"Rotary Surface {spacing_name}", minimum=0.001),
        start,
        sweep,
    )


def _normalize_axial_window(raw: Any) -> tuple[str, float, float]:
    if not isinstance(raw, Mapping):
        _error("Rotary Surface axial_window must be one closed request.")
    kind = str(raw.get("kind") or "")
    if kind == "stock":
        exact_fields(raw, frozenset({"kind"}), "Rotary Surface stock axial window")
        return kind, 0.0, 0.0
    if kind != "explicit":
        _error("Rotary Surface axial_window kind must be stock or explicit.")
    value = exact_fields(
        raw,
        frozenset({"kind", "start_mm", "stop_mm"}),
        "Rotary Surface explicit axial window",
    )
    start = finite_number(value["start_mm"], "Rotary Surface axial start")
    stop = finite_number(value["stop_mm"], "Rotary Surface axial stop")
    if stop <= start:
        _error("Rotary Surface axial stop_mm must exceed start_mm.")
    return kind, start, stop


def _normalize_layers(raw: Any) -> tuple[str, float]:
    if not isinstance(raw, Mapping):
        _error("Rotary Surface layers must be one closed layer request.")
    kind = str(raw.get("kind") or "")
    if kind == "single_pass":
        exact_fields(raw, frozenset({"kind"}), "Rotary Surface single-pass layers")
        return kind, 0.0
    if kind != "multi_pass":
        _error("Rotary Surface layers kind must be single_pass or multi_pass.")
    value = exact_fields(
        raw,
        frozenset({"kind", "step_down_mm"}),
        "Rotary Surface multi-pass layers",
    )
    return kind, _positive(
        value["step_down_mm"],
        "Rotary Surface radial step down",
        minimum=0.001,
    )


def _normalize_parameters(spec: RotarySurfaceCreateSpec) -> RotaryParameters:
    values = exact_fields(spec.rotary_surface, _ROTARY_FIELDS, "Rotary Surface settings")
    pattern, stepover, start_angle, sweep = _normalize_pattern(values["pattern"])
    cut_mode = str(values["cut_mode"] or "")
    if cut_mode not in _CUT_MODES:
        _error("Rotary Surface cut_mode must be climb or conventional.")
    axial_kind, axial_start, axial_stop = _normalize_axial_window(
        values["axial_window"]
    )
    angular_resolution = _positive(
        values["angular_resolution_degrees"],
        "Rotary Surface angular resolution",
        minimum=0.05,
        maximum=45.0,
    )
    radial_stock = finite_number(
        values["radial_stock_to_leave_mm"],
        "Rotary Surface radial stock to leave",
        minimum=0.0,
    )
    layer_mode, step_down = _normalize_layers(values["layers"])
    feed_mode = str(values["feed_mode"] or "")
    if feed_mode not in _FEED_MODES:
        _error("Rotary Surface feed_mode must be axial_only or surface_speed.")
    maximum_feed = _positive(
        values["maximum_effective_feed_mm_per_min"],
        "Rotary Surface maximum effective feed",
        minimum=0.001,
        maximum=10_000_000.0,
    )
    mesh = exact_fields(values["mesh"], _MESH_FIELDS, "Rotary Surface mesh settings")
    linear_deflection = _positive(
        mesh["linear_deflection_mm"],
        "Rotary Surface linear deflection",
        minimum=0.001,
        maximum=25.4,
    )
    angular_deflection = _positive(
        mesh["angular_deflection_radians"],
        "Rotary Surface angular deflection",
        minimum=0.001,
        maximum=1.570796327,
    )
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Rotary Surface heights")
    safe = finite_number(heights["safe_height_mm"], "Rotary Surface safe height")
    clearance = finite_number(
        heights["clearance_height_mm"],
        "Rotary Surface clearance height",
    )
    if safe < clearance:
        _error("Rotary Surface safe_height_mm must be at or above clearance_height_mm.")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Rotary Surface coolant must be none, flood, or mist.")
    return RotaryParameters(
        pattern=pattern,
        stepover_mm=stepover,
        requested_start_angle_degrees=start_angle,
        requested_sweep_degrees=sweep,
        cut_mode=cut_mode,
        axial_window_kind=axial_kind,
        requested_axial_start_mm=axial_start,
        requested_axial_stop_mm=axial_stop,
        angular_resolution_degrees=angular_resolution,
        radial_stock_to_leave_mm=radial_stock,
        layer_mode=layer_mode,
        step_down_mm=step_down,
        feed_mode=feed_mode,
        maximum_effective_feed_mm_per_min=maximum_feed,
        linear_deflection_mm=linear_deflection,
        angular_deflection_radians=angular_deflection,
        safe_height_mm=safe,
        clearance_height_mm=clearance,
        coolant=coolant,
    )


def _axis_label(vector: Any) -> tuple[str, tuple[float, float, float]]:
    values = tuple(
        finite_number(getattr(vector, name, None), f"Rotary machine axis {name}", minimum=-1.0, maximum=1.0)
        for name in ("x", "y", "z")
    )
    length = math.sqrt(sum(value * value for value in values))
    if length <= 1.0e-9:
        _error(
            "The configured rotary axis has no usable direction.",
            "NATIVE_MANUFACTURE_MACHINE_INVALID",
        )
    normalized = tuple(value / length for value in values)
    x, y, z = (abs(value) for value in normalized)
    if x > 0.99 and y < 0.05 and z < 0.05:
        return "X", normalized
    if y > 0.99 and x < 0.05 and z < 0.05:
        return "Y", normalized
    if z > 0.99 and x < 0.05 and y < 0.05:
        _error(
            "Rotary Surface does not support a world-Z rotary axis; configure an X/A or Y/B rotary machine.",
            "NATIVE_MANUFACTURE_MACHINE_INVALID",
        )
    _error(
        "Rotary Surface requires a rotary axis aligned with world X or Y.",
        "NATIVE_MANUFACTURE_MACHINE_INVALID",
    )


def _resolve_machine(boundary: PreparedOperationBoundary) -> RotaryMachineFacts:
    current = configured_machine_state(boundary.job)
    expected = boundary.job_before.get("machine")
    if current != expected:
        _error(
            "The exact CAM machine changed after turn start.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )
    proxy = getattr(boundary.job, "Proxy", None)
    reader = getattr(proxy, "getMachine", None)
    machine = reader() if callable(reader) else None
    axes = getattr(machine, "rotary_axes", None) if machine is not None else None
    if not axes:
        _error(
            "Rotary Surface requires the exact Job to use a configured machine with a rotary axis.",
            "NATIVE_MANUFACTURE_MACHINE_INVALID",
        )
    _key, axis = next(iter(axes.items()))
    command_letter = str(getattr(axis, "name", "") or "").upper()
    if not _ROTARY_LETTER.fullmatch(command_letter):
        _error(
            "Rotary Surface requires its selected machine rotary axis to emit A, B, or C.",
            "NATIVE_MANUFACTURE_MACHINE_INVALID",
        )
    world_axis, direction = _axis_label(getattr(axis, "rotation_vector", None))
    joint_origin = tuple(getattr(axis, "joint_origin", ()) or ())
    try:
        origin = tuple(float(value) for value in joint_origin)
    except (TypeError, ValueError):
        origin = ()
    if len(origin) != 3 or any(not math.isfinite(value) for value in origin):
        _error(
            "The selected rotary axis has an invalid joint origin.",
            "NATIVE_MANUFACTURE_MACHINE_INVALID",
        )
    if math.sqrt(sum(value * value for value in origin)) > 1.0e-6:
        _error(
            "Rotary Surface currently requires the machine rotary axis to pass through the world origin.",
            "NATIVE_MANUFACTURE_MACHINE_INVALID",
        )
    minimum = finite_number(
        getattr(axis, "min_limit", None),
        "Rotary machine minimum angle",
        minimum=-1_000_000.0,
        maximum=1_000_000.0,
    )
    maximum = finite_number(
        getattr(axis, "max_limit", None),
        "Rotary machine maximum angle",
        minimum=-1_000_000.0,
        maximum=1_000_000.0,
    )
    if maximum <= minimum:
        _error(
            "The selected rotary axis maximum limit must exceed its minimum limit.",
            "NATIVE_MANUFACTURE_MACHINE_INVALID",
        )
    wrap = getattr(axis, "wrap_strategy", "unwound")
    wrap_strategy = str(getattr(wrap, "value", wrap))
    if wrap_strategy not in {"unwound", "modulo", "rezero"}:
        _error(
            "The selected rotary axis has an unsupported wrap strategy.",
            "NATIVE_MANUFACTURE_MACHINE_INVALID",
        )
    return RotaryMachineFacts(
        state_sha256=str(current.get("state_sha256") or ""),
        configuration_name=str(current.get("configuration_name") or ""),
        axis_name=command_letter,
        world_axis=world_axis,
        direction=direction,
        minimum_degrees=minimum,
        maximum_degrees=maximum,
        wrap_strategy=wrap_strategy,
    )


def _validate_stock(
    boundary: PreparedOperationBoundary,
    machine: RotaryMachineFacts,
) -> tuple[float, float, float, float]:
    import FreeCAD as App
    import Path.Main.Stock as PathStock

    stock = getattr(boundary.job, "Stock", None)
    if stock is None:
        _error(
            "Rotary Surface requires a Cylinder stock on the exact Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    if PathStock.StockType.FromStock(stock) != PathStock.StockType.CreateCylinder:
        _error(
            "Rotary Surface requires a CreateCylinder Job stock.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    shape = getattr(stock, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        _error(
            "Rotary Surface requires a valid cylindrical stock shape.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    radius = finite_number(
        getattr(getattr(stock, "Radius", None), "Value", None),
        "Rotary Surface stock radius",
        minimum=0.001,
    )
    placement = stock.Placement
    cylinder_axis = placement.Rotation.multVec(App.Vector(0.0, 0.0, 1.0))
    cylinder_axis.normalize()
    target = App.Vector(1.0, 0.0, 0.0) if machine.world_axis == "X" else App.Vector(0.0, 1.0, 0.0)
    if abs(float(cylinder_axis.dot(target))) < 0.9999985:
        _error(
            f"The Cylinder stock axis must align with world {machine.world_axis}.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    base = placement.Base
    radial_offset = (
        math.hypot(float(base.y), float(base.z))
        if machine.world_axis == "X"
        else math.hypot(float(base.x), float(base.z))
    )
    if radial_offset > 1.0e-6:
        _error(
            "The Cylinder stock centerline must coincide with the world-origin rotary axis.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    bounds = shape.BoundBox
    if machine.world_axis == "X":
        axial_start, axial_stop = float(bounds.XMin), float(bounds.XMax)
    else:
        axial_start, axial_stop = float(bounds.YMin), float(bounds.YMax)
    return (
        round(radius, 9),
        round(axial_start, 9),
        round(axial_stop, 9),
        round(float(bounds.ZMax), 9),
    )


def _face_bounds(
    boundary: PreparedOperationBoundary,
    machine: RotaryMachineFacts,
    linear_deflection_mm: float,
) -> tuple[float, float, float, float]:
    axial = []
    angles = []
    for item in boundary.geometry:
        for name in item.subelements:
            try:
                face = item.job_resource.Shape.getElement(name).copy()
                vertices, _facets = face.tessellate(linear_deflection_mm)
            except Exception as exc:
                raise NativeManufactureError(
                    f"Rotary Surface could not sample exact Face {item.public_source.Name}.{name}.",
                    error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                ) from exc
            for vertex in vertices:
                if machine.world_axis == "X":
                    axial.append(float(vertex.x))
                    angle = math.degrees(math.atan2(-float(vertex.y), float(vertex.z)))
                else:
                    axial.append(float(vertex.y))
                    angle = math.degrees(math.atan2(float(vertex.x), float(vertex.z)))
                angles.append(angle + 360.0 if angle < 0.0 else angle)
    if not axial or not angles:
        _error(
            "Rotary Surface selected Faces produced no usable boundary samples.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return min(axial), max(axial), min(angles), max(angles)


def _validate_model_alignment(
    boundary: PreparedOperationBoundary,
    machine: RotaryMachineFacts,
    tolerance_mm: float,
) -> None:
    models = tuple(getattr(getattr(boundary.job, "Model", None), "Group", ()) or ())
    if len(models) != 1:
        _error(
            "Rotary Surface requires an exact CAM Job with one model; the shipped operation otherwise ignores every model after the first.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    model = models[0]
    shape = getattr(model, "Shape", None)
    if shape is None or shape.isNull() or not shape.isValid():
        _error(
            "Rotary Surface requires one valid Job model shape.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    center = shape.BoundBox.Center
    placement = getattr(model, "Placement", None)
    if placement is not None and hasattr(placement, "multVec"):
        center = placement.multVec(center)
    offset = (
        math.hypot(float(center.y), float(center.z))
        if machine.world_axis == "X"
        else math.hypot(float(center.x), float(center.z))
    )
    if offset > tolerance_mm:
        _error(
            "The exact Job model bounding center is "
            f"{offset:g} mm off the rotary axis, beyond the requested "
            f"linear deflection of {tolerance_mm:g} mm. Re-center the model.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )


def _axis_output_bounds(
    parameters: RotaryParameters,
    *,
    axial_span_mm: float,
    start_angle_degrees: float,
    sweep_degrees: float,
    stock_radius_mm: float,
) -> tuple[float, float, int]:
    direction = 1.0 if parameters.cut_mode == "climb" else -1.0
    if parameters.pattern == "spiral":
        repetitions = axial_span_mm / parameters.stepover_mm
        end = start_angle_degrees + direction * repetitions * 360.0
        commands = math.ceil(repetitions * 360.0 / parameters.angular_resolution_degrees) + 6
    elif parameters.pattern == "parallel":
        angular_step_degrees = math.degrees(parameters.stepover_mm / stock_radius_mm)
        passes = max(1, math.ceil(sweep_degrees / angular_step_degrees) + 1)
        axial_samples = max(2, math.ceil(axial_span_mm / (parameters.stepover_mm * 0.25)) + 1)
        commands = passes * (axial_samples + 4) + 2
        end = start_angle_degrees + direction * sweep_degrees
    else:
        rings = max(2, math.ceil(axial_span_mm / parameters.stepover_mm) + 1)
        angular_samples = max(
            1,
            math.ceil(sweep_degrees / parameters.angular_resolution_degrees),
        )
        commands = rings * (angular_samples + 2) + 5
        end = start_angle_degrees + direction * rings * sweep_degrees
    return min(start_angle_degrees, end), max(start_angle_degrees, end), commands


def _inspect_geometry(
    boundary: PreparedOperationBoundary,
    request: RotaryGeometryRequest,
    parameters: RotaryParameters,
    machine: RotaryMachineFacts,
    tool: OCLToolFacts,
) -> RotaryGeometryFacts:
    stock_radius, stock_start, stock_stop, stock_top = _validate_stock(
        boundary, machine
    )
    _validate_model_alignment(boundary, machine, parameters.linear_deflection_mm)
    if parameters.radial_stock_to_leave_mm >= stock_radius:
        _error("Rotary Surface radial stock to leave must be below the stock radius.")
    required_clearance = stock_top + tool.diameter_mm * 0.5
    if parameters.clearance_height_mm < required_clearance:
        _error(
            "Rotary Surface clearance_height_mm must clear the cylindrical stock and cutter radius; "
            f"use at least {required_clearance:g} mm."
        )
    if parameters.safe_height_mm < required_clearance:
        _error(
            "Rotary Surface safe_height_mm must clear the cylindrical stock and cutter radius; "
            f"use at least {required_clearance:g} mm."
        )
    if parameters.axial_window_kind == "stock":
        axial_start, axial_stop = stock_start, stock_stop
    else:
        axial_start = parameters.requested_axial_start_mm
        axial_stop = parameters.requested_axial_stop_mm
        if axial_start < stock_start - _SETTING_TOLERANCE or axial_stop > stock_stop + _SETTING_TOLERANCE:
            _error(
                "Rotary Surface explicit axial window must remain within the exact Cylinder stock bounds "
                f"[{stock_start:g}, {stock_stop:g}] mm."
            )
    start_angle = parameters.requested_start_angle_degrees
    sweep = parameters.requested_sweep_degrees
    face_count = sum(len(item.subelements) for item in boundary.geometry)
    if request.requested_kind == "faces":
        if parameters.pattern == "spiral":
            _error(
                "Rotary Surface selected Faces are not supported by the spiral pattern because its continuous pitch cannot honor a bounded angular sector. Use parallel or rings, or geometry.kind=entire_job."
            )
        if parameters.cut_mode == "conventional":
            _error(
                "Rotary Surface selected Faces currently require cut_mode=climb because the shipped conventional generator sweeps away from the selected angular interval."
            )
        face_axial_start, face_axial_stop, face_angle_start, face_angle_stop = _face_bounds(
            boundary,
            machine,
            parameters.linear_deflection_mm,
        )
        axial_start = max(axial_start, face_axial_start)
        axial_stop = min(axial_stop, face_axial_stop)
        if face_angle_stop - face_angle_start < 350.0:
            requested_stop = start_angle + sweep
            start_angle = max(start_angle, face_angle_start)
            requested_stop = min(requested_stop, face_angle_stop)
            sweep = requested_stop - start_angle
        if axial_stop <= axial_start:
            _error("Rotary Surface selected Faces produce an empty axial machining range.")
        if sweep <= 0.0:
            _error("Rotary Surface selected Faces produce an empty angular machining range.")
    axial_span = axial_stop - axial_start
    if axial_span <= 0.0:
        _error("Rotary Surface effective axial machining span must be positive.")
    x_step = max(min(parameters.stepover_mm * 0.25, axial_span / 8.0), 0.001)
    x_samples = max(2, math.ceil(axial_span / x_step) + 1)
    angular_samples = max(
        8,
        math.ceil(360.0 / parameters.angular_resolution_degrees) + 1,
    )
    cells = x_samples * angular_samples
    if cells > _MAX_PROCESSING_CELLS:
        _error(
            "Rotary Surface would require approximately "
            f"{cells:,} OpenCamLib sampling cells, above the synchronous safety limit of "
            f"{_MAX_PROCESSING_CELLS:,}. Increase the stepover or angular resolution, or narrow the axial range so the VibeCAD UI remains responsive.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    layers = (
        1
        if parameters.layer_mode == "single_pass"
        else max(1, math.ceil(stock_radius / parameters.step_down_mm))
    )
    axis_minimum, axis_maximum, per_layer_commands = _axis_output_bounds(
        parameters,
        axial_span_mm=axial_span,
        start_angle_degrees=start_angle,
        sweep_degrees=sweep,
        stock_radius_mm=stock_radius,
    )
    command_ceiling = layers * per_layer_commands
    if command_ceiling > _MAX_ESTIMATED_COMMANDS:
        _error(
            "Rotary Surface would generate approximately "
            f"{command_ceiling:,} toolpath commands, above the synchronous safety limit of "
            f"{_MAX_ESTIMATED_COMMANDS:,}. Increase stepover, angular resolution, or radial step down, or narrow the machining range.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
        )
    property_stop = start_angle + sweep
    if not (
        machine.minimum_degrees <= start_angle <= machine.maximum_degrees
        and machine.minimum_degrees <= property_stop <= machine.maximum_degrees
    ):
        _error(
            "Rotary Surface requested angular properties exceed the selected machine axis limits "
            f"[{machine.minimum_degrees:g}, {machine.maximum_degrees:g}] degrees.",
            "NATIVE_MANUFACTURE_MACHINE_LIMIT_EXCEEDED",
        )
    if machine.wrap_strategy == "unwound":
        if axis_minimum < machine.minimum_degrees or axis_maximum > machine.maximum_degrees:
            _error(
                "Rotary Surface would emit unwound rotary travel "
                f"[{axis_minimum:g}, {axis_maximum:g}] degrees outside the selected machine limits "
                f"[{machine.minimum_degrees:g}, {machine.maximum_degrees:g}]. Increase the machine travel, select a wrapping strategy, or reduce the machining span.",
                "NATIVE_MANUFACTURE_MACHINE_LIMIT_EXCEEDED",
            )
    elif machine.minimum_degrees > 0.0 or machine.maximum_degrees < 360.0:
        _error(
            "Rotary Surface modulo and rezero wrapping require the selected machine axis to accept the full [0, 360] degree range.",
            "NATIVE_MANUFACTURE_MACHINE_LIMIT_EXCEEDED",
        )
    return RotaryGeometryFacts(
        axial_start_mm=round(axial_start, 9),
        axial_stop_mm=round(axial_stop, 9),
        start_angle_degrees=round(start_angle, 9),
        sweep_degrees=round(sweep, 9),
        stock_radius_mm=stock_radius,
        face_count=face_count,
        estimated_processing_cells=cells,
        estimated_command_ceiling=command_ceiling,
        radial_layer_ceiling=layers,
        generated_axis_minimum_degrees=round(axis_minimum, 9),
        generated_axis_maximum_degrees=round(axis_maximum, 9),
    )


def preflight_rotary_surface_create(
    document: Any,
    spec: RotarySurfaceCreateSpec,
) -> PreparedRotarySurfaceCreate:
    """Freeze exact machine, stock, model, cutter, parameters, and work."""

    if not isinstance(spec, RotarySurfaceCreateSpec):
        raise TypeError("spec must be a RotarySurfaceCreateSpec")
    parameters = _normalize_parameters(spec)
    geometry_request = _normalize_geometry(spec.geometry)
    boundary = preflight_operation_boundary(
        document,
        noun="Rotary Surface",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry=geometry_request.shared_request,
        allowed_subelement_types=frozenset({"Face"}),
        allow_entire_job=True,
    )
    machine = _resolve_machine(boundary)
    tool = validate_ocl_tool(boundary, noun="Rotary Surface")
    geometry = _inspect_geometry(
        boundary,
        geometry_request,
        parameters,
        machine,
        tool,
    )
    return PreparedRotarySurfaceCreate(
        label=clean_operation_label(spec.label, "Rotary Surface"),
        boundary=boundary,
        geometry_request=geometry_request,
        parameters=parameters,
        machine=machine,
        geometry=geometry,
        tool=tool,
    )


def _pattern_payload(prepared: PreparedRotarySurfaceCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    geometry = prepared.geometry
    if parameters.pattern == "spiral":
        return {
            "kind": "spiral",
            "axial_pitch_mm": parameters.stepover_mm,
            "start_angle_degrees": geometry.start_angle_degrees,
        }
    spacing_name = (
        "surface_stepover_mm" if parameters.pattern == "parallel" else "axial_spacing_mm"
    )
    return {
        "kind": parameters.pattern,
        spacing_name: parameters.stepover_mm,
        "start_angle_degrees": geometry.start_angle_degrees,
        "sweep_degrees": geometry.sweep_degrees,
    }


def _parameter_payload(prepared: PreparedRotarySurfaceCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    geometry = prepared.geometry
    axial_window: dict[str, Any] = {"kind": parameters.axial_window_kind}
    if parameters.axial_window_kind == "explicit":
        axial_window.update(
            {
                "start_mm": parameters.requested_axial_start_mm,
                "stop_mm": parameters.requested_axial_stop_mm,
            }
        )
    layers: dict[str, Any] = {"kind": parameters.layer_mode}
    if parameters.layer_mode == "multi_pass":
        layers["step_down_mm"] = parameters.step_down_mm
    return {
        "rotary_surface": {
            "pattern": _pattern_payload(prepared),
            "cut_mode": parameters.cut_mode,
            "axial_window": axial_window,
            "effective_axial_window_mm": {
                "start_mm": geometry.axial_start_mm,
                "stop_mm": geometry.axial_stop_mm,
            },
            "angular_resolution_degrees": parameters.angular_resolution_degrees,
            "radial_stock_to_leave_mm": parameters.radial_stock_to_leave_mm,
            "layers": layers,
            "feed_mode": parameters.feed_mode,
            "maximum_effective_feed_mm_per_min": (
                parameters.maximum_effective_feed_mm_per_min
            ),
            "mesh": {
                "linear_deflection_mm": parameters.linear_deflection_mm,
                "angular_deflection_radians": parameters.angular_deflection_radians,
            },
        },
        "heights": {
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
        "coolant": parameters.coolant,
    }


def _apply_settings(
    operation: Any,
    *,
    prepared: PreparedRotarySurfaceCreate,
) -> None:
    import FreeCAD as App

    parameters = prepared.parameters
    geometry = prepared.geometry
    clear_operation_expressions(operation, ("StepDown", "SafeHeight", "ClearanceHeight"))
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.StartX = f"{geometry.axial_start_mm} mm"
    operation.StopX = f"{geometry.axial_stop_mm} mm"
    operation.StartAngle = geometry.start_angle_degrees
    operation.StopAngle = geometry.start_angle_degrees + geometry.sweep_degrees
    operation.StepOver = f"{parameters.stepover_mm} mm"
    operation.AngularResolution = parameters.angular_resolution_degrees
    operation.RadialStockToLeave = f"{parameters.radial_stock_to_leave_mm} mm"
    operation.CutMode = _CUT_MODES[parameters.cut_mode]
    operation.CutPattern = _PATTERNS[parameters.pattern]
    operation.FeedMode = _FEED_MODES[parameters.feed_mode]
    operation.MaxFeed = parameters.maximum_effective_feed_mm_per_min
    operation.BoundaryFromFaces = prepared.geometry_request.requested_kind == "faces"
    operation.LinearDeflection = f"{parameters.linear_deflection_mm} mm"
    operation.AngularDeflection = parameters.angular_deflection_radians
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]
    operation.Workplane = App.Vector(0.0, 0.0, 1.0)


def create_rotary_surface(
    document: Any,
    *,
    prepared: PreparedRotarySurfaceCreate,
) -> NativeMutationDraft:
    """Create one shipped Rotary Surface operation in the owned transaction."""

    if not isinstance(prepared, PreparedRotarySurfaceCreate):
        raise TypeError("prepared must be a PreparedRotarySurfaceCreate")
    import Path.Op.RotarySurface as PathRotarySurface

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.RotarySurface"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="RotarySurface",
        operation_factory=PathRotarySurface.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, rotary_surface_prepared=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    return next(
        (
            expression
            for path, expression in tuple(getattr(operation, "ExpressionEngine", ()) or ())
            if str(path).lstrip(".") == property_name
        ),
        None,
    )


def _same_number(actual: float, expected: float) -> bool:
    return abs(float(actual) - float(expected)) <= _SETTING_TOLERANCE


def _assert_rotary_surface_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedRotarySurfaceCreate,
) -> None:
    parameters = prepared.parameters
    geometry = prepared.geometry
    expected = {
        "axial_start_mm": geometry.axial_start_mm,
        "axial_stop_mm": geometry.axial_stop_mm,
        "start_angle_degrees": geometry.start_angle_degrees,
        "stop_angle_degrees": geometry.start_angle_degrees + geometry.sweep_degrees,
        "stepover_mm": parameters.stepover_mm,
        "angular_resolution_degrees": parameters.angular_resolution_degrees,
        "radial_stock_to_leave_mm": parameters.radial_stock_to_leave_mm,
        "cut_mode": _CUT_MODES[parameters.cut_mode],
        "pattern": _PATTERNS[parameters.pattern],
        "feed_mode": _FEED_MODES[parameters.feed_mode],
        "maximum_effective_feed_mm_per_min": parameters.maximum_effective_feed_mm_per_min,
        "boundary_from_faces": prepared.geometry_request.requested_kind == "faces",
        "linear_deflection_mm": parameters.linear_deflection_mm,
        "angular_deflection_radians": parameters.angular_deflection_radians,
        "step_down_mm": parameters.step_down_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
    }
    actual = {
        "axial_start_mm": quantity_mm(operation, "StartX"),
        "axial_stop_mm": quantity_mm(operation, "StopX"),
        "start_angle_degrees": round(float(operation.StartAngle.Value), 9),
        "stop_angle_degrees": round(float(operation.StopAngle.Value), 9),
        "stepover_mm": quantity_mm(operation, "StepOver"),
        "angular_resolution_degrees": round(float(operation.AngularResolution.Value), 9),
        "radial_stock_to_leave_mm": quantity_mm(operation, "RadialStockToLeave"),
        "cut_mode": str(operation.CutMode),
        "pattern": str(operation.CutPattern),
        "feed_mode": str(operation.FeedMode),
        "maximum_effective_feed_mm_per_min": round(float(operation.MaxFeed), 9),
        "boundary_from_faces": bool(operation.BoundaryFromFaces),
        "linear_deflection_mm": quantity_mm(operation, "LinearDeflection"),
        "angular_deflection_radians": quantity_mm(operation, "AngularDeflection"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "coolant": str(operation.CoolantMode),
    }
    mismatches = {}
    for name, expected_value in expected.items():
        actual_value = actual[name]
        if isinstance(expected_value, float) and isinstance(actual_value, (float, int)):
            matches = _same_number(actual_value, expected_value)
        else:
            matches = actual_value == expected_value
        if not matches:
            mismatches[name] = {"expected": expected_value, "actual": actual_value}
    for property_name in ("StepDown", "SafeHeight", "ClearanceHeight"):
        expression = _expression(operation, property_name)
        if expression:
            mismatches[f"{property_name}_expression"] = {
                "expected": None,
                "actual": str(expression),
            }
    if configured_machine_state(prepared.boundary.job).get("state_sha256") != prepared.machine.state_sha256:
        mismatches["machine_state_sha256"] = {
            "expected": prepared.machine.state_sha256,
            "actual": configured_machine_state(prepared.boundary.job).get("state_sha256"),
        }
    if mismatches:
        raise NativeManufactureError(
            "The created Rotary Surface did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_ROTARY_SURFACE_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _rotary_surface_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedRotarySurfaceCreate,
) -> Mapping[str, Any]:
    letter = prepared.machine.axis_name
    rotary_values = []
    fully_qualified_cutting = 0
    for command in tuple(operation.Path.Commands):
        parameters = command.Parameters
        if letter in parameters:
            rotary_values.append(float(parameters[letter]))
        if str(command.Name) in {"G1", "G2", "G3"} and all(
            name in parameters for name in ("X", "Y", "Z", letter)
        ):
            fully_qualified_cutting += 1
    if not rotary_values or fully_qualified_cutting == 0:
        _error(
            f"The created Rotary Surface has no fully qualified XYZ{letter} cutting motion.",
            "NATIVE_MANUFACTURE_ROTARY_SURFACE_POSTCONDITION_FAILED",
        )
    if prepared.machine.wrap_strategy in {"modulo", "rezero"} and (
        min(rotary_values) < -_SETTING_TOLERANCE
        or max(rotary_values) >= 360.0 + _SETTING_TOLERANCE
    ):
        _error(
            "The created Rotary Surface did not honor the exact machine wrap strategy.",
            "NATIVE_MANUFACTURE_ROTARY_SURFACE_POSTCONDITION_FAILED",
        )
    return {
        "target_mode": prepared.geometry_request.requested_kind,
        "face_count": prepared.geometry.face_count,
        "pattern": prepared.parameters.pattern,
        "rotary_axis": {
            "command_letter": letter,
            "world_axis": prepared.machine.world_axis,
            "wrap_strategy": prepared.machine.wrap_strategy,
            "minimum_degrees": prepared.machine.minimum_degrees,
            "maximum_degrees": prepared.machine.maximum_degrees,
        },
        "tool_shape_type": prepared.tool.shape_type,
        "ocl_cutter": prepared.tool.ocl_cutter,
        "tool_diameter_mm": prepared.tool.diameter_mm,
        "stock_radius_mm": prepared.geometry.stock_radius_mm,
        "estimated_processing_cells": prepared.geometry.estimated_processing_cells,
        "estimated_command_ceiling": prepared.geometry.estimated_command_ceiling,
        "radial_layer_ceiling": prepared.geometry.radial_layer_ceiling,
        "fully_qualified_cutting_command_count": fully_qualified_cutting,
        "minimum_output_rotary_degrees": round(min(rotary_values), 9),
        "maximum_output_rotary_degrees": round(max(rotary_values), 9),
    }


def verify_created_rotary_surface(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared: PreparedRotarySurfaceCreate = draft.value["rotary_surface_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="rotary_surface",
        assert_settings=partial(
            _assert_rotary_surface_settings,
            prepared=prepared,
        ),
        additional_verify=partial(_rotary_surface_result, prepared=prepared),
    )
