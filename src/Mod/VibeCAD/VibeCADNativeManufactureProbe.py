# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, bounded creation of the shipped CAM stock-probing grid."""

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
    preflight_operation_boundary,
    quantity_mm,
    shape_sha256,
    verify_native_operation,
)
from VibeCADNativeManufactureState import tool_controller_state
from VibeCADNativeMutation import NativeMutationDraft


MAX_PROBE_AXIS_POINTS = 64
MAX_PROBE_GRID_POINTS = 1024
_GRID_FIELDS = frozenset(
    {"point_count_x", "point_count_y", "x_offset_mm", "y_offset_mm"}
)
_MOTION_FIELDS = frozenset(
    {"probe_depth_mm", "safe_height_mm", "clearance_height_mm"}
)
_PROBE_COMMANDS = frozenset({"G38.2"})


@dataclass(frozen=True, slots=True)
class ProbeCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    grid: Mapping[str, Any]
    motion: Mapping[str, Any]


@dataclass(frozen=True, slots=True)
class ProbeParameters:
    point_count_x: int
    point_count_y: int
    x_offset_mm: float
    y_offset_mm: float
    probe_depth_mm: float
    safe_height_mm: float
    clearance_height_mm: float

    @property
    def point_count(self) -> int:
        return self.point_count_x * self.point_count_y


@dataclass(frozen=True, slots=True)
class PreparedProbeCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: ProbeParameters
    stock: Any
    stock_shape_sha256: str
    stock_bounds: tuple[float, float, float, float, float, float]
    vertical_feed: float


def _error(
    message: str,
    code: str = "NATIVE_ARGUMENTS_INVALID",
    *,
    repair: Mapping[str, Any] | None = None,
) -> None:
    raise NativeManufactureError(message, error_code=code, repair=repair)


def _point_count(value: Any, axis: str) -> int:
    if (
        isinstance(value, bool)
        or not isinstance(value, int)
        or not 3 <= value <= MAX_PROBE_AXIS_POINTS
    ):
        _error(
            f"Probe point_count_{axis} must be an integer from 3 through 64.",
            repair={
                "field": f"grid.point_count_{axis}",
                "minimum": 3,
                "maximum": MAX_PROBE_AXIS_POINTS,
            },
        )
    return value


def _normalize_parameters(spec: ProbeCreateSpec) -> ProbeParameters:
    grid = exact_fields(spec.grid, _GRID_FIELDS, "Probe grid")
    motion = exact_fields(spec.motion, _MOTION_FIELDS, "Probe motion")
    count_x = _point_count(grid["point_count_x"], "x")
    count_y = _point_count(grid["point_count_y"], "y")
    point_count = count_x * count_y
    if point_count > MAX_PROBE_GRID_POINTS:
        _error(
            "Probe grid point_count_x multiplied by point_count_y may not exceed 1024.",
            "NATIVE_MANUFACTURE_WORKLOAD_TOO_LARGE",
            repair={
                "point_count_x": count_x,
                "point_count_y": count_y,
                "point_count": point_count,
                "maximum_point_count": MAX_PROBE_GRID_POINTS,
            },
        )
    return ProbeParameters(
        point_count_x=count_x,
        point_count_y=count_y,
        x_offset_mm=finite_number(grid["x_offset_mm"], "Probe X offset"),
        y_offset_mm=finite_number(grid["y_offset_mm"], "Probe Y offset"),
        probe_depth_mm=finite_number(
            motion["probe_depth_mm"],
            "Probe depth",
        ),
        safe_height_mm=finite_number(
            motion["safe_height_mm"],
            "Probe safe height",
        ),
        clearance_height_mm=finite_number(
            motion["clearance_height_mm"],
            "Probe clearance height",
        ),
    )


def _prepare_stock(
    document: Any,
    boundary: PreparedOperationBoundary,
) -> tuple[Any, str, tuple[float, float, float, float, float, float]]:
    stock = getattr(boundary.job, "Stock", None)
    shape = getattr(stock, "Shape", None)
    if (
        stock is None
        or getattr(stock, "Document", None) is not document
        or document.getObject(str(getattr(stock, "Name", ""))) is not stock
        or shape is None
        or bool(getattr(shape, "isNull", lambda: True)())
        or not bool(getattr(shape, "isValid", lambda: False)())
    ):
        _error(
            "Probe requires valid current stock owned by the exact CAM Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    bounds = shape.BoundBox
    values = tuple(
        round(float(getattr(bounds, name)), 9)
        for name in ("XMin", "YMin", "ZMin", "XMax", "YMax", "ZMax")
    )
    if (
        not all(math.isfinite(value) for value in values)
        or values[3] <= values[0]
        or values[4] <= values[1]
        or values[5] <= values[2]
    ):
        _error(
            "Probe requires stock with finite nonzero X, Y, and Z extents.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return stock, shape_sha256(shape, "CAM Job stock"), values


def _vertical_feed(boundary: PreparedOperationBoundary) -> float:
    controller = boundary.controller
    if int(getattr(controller, "ToolNumber", 0) or 0) <= 0:
        _error(
            "Probe requires a nonzero tool-controller number.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    tool = getattr(controller, "Tool", None)
    try:
        import PathScripts.PathUtils as PathUtils

        shape_name = str(PathUtils.getToolShapeName(tool) or "").casefold()
    except Exception as exc:
        raise NativeManufactureError(
            "The exact Probe tool type could not be resolved.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc
    if shape_name != "probe":
        _error(
            "Probe requires a tool controller whose ToolBit shape is exactly probe.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "tool_controller_object_name": str(controller.Name),
                "actual_tool_shape": shape_name or None,
                "required_tool_shape": "probe",
            },
        )
    value = getattr(getattr(controller, "VertFeed", None), "Value", None)
    try:
        feed = float(value)
    except (TypeError, ValueError):
        feed = 0.0
    if not math.isfinite(feed) or feed <= 0.0:
        _error(
            "Probe requires a positive finite vertical feed on its exact controller.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
            repair={
                "tool_controller_object_name": str(controller.Name),
                "vertical_feed": feed if math.isfinite(feed) else None,
            },
        )
    return round(feed, 9)


def _validate_motion(
    parameters: ProbeParameters,
    stock_bounds: tuple[float, float, float, float, float, float],
) -> None:
    stock_bottom = stock_bounds[2]
    stock_top = stock_bounds[5]
    if parameters.probe_depth_mm < stock_bottom:
        _error(
            "Probe probe_depth_mm must be at or above the exact stock bottom.",
            repair={
                "field": "motion.probe_depth_mm",
                "stock_bottom_z_mm": stock_bottom,
            },
        )
    if parameters.probe_depth_mm >= stock_top:
        _error(
            "Probe probe_depth_mm must be below the exact stock top.",
            repair={
                "field": "motion.probe_depth_mm",
                "stock_top_z_mm": stock_top,
            },
        )
    if parameters.safe_height_mm <= stock_top:
        _error(
            "Probe safe_height_mm must be above the exact stock top.",
            repair={
                "field": "motion.safe_height_mm",
                "stock_top_z_mm": stock_top,
            },
        )
    if parameters.clearance_height_mm < parameters.safe_height_mm:
        _error(
            "Probe clearance_height_mm must be at or above safe_height_mm.",
            repair={
                "field": "motion.clearance_height_mm",
                "minimum": parameters.safe_height_mm,
            },
        )


def preflight_probe_create(
    document: Any,
    spec: ProbeCreateSpec,
) -> PreparedProbeCreate:
    if not isinstance(spec, ProbeCreateSpec):
        raise TypeError("spec must be a ProbeCreateSpec")
    parameters = _normalize_parameters(spec)
    boundary = preflight_operation_boundary(
        document,
        noun="Probe grid",
        job_target=spec.job,
        tool_controller_target=spec.tool_controller,
        geometry={"kind": "entire_job"},
        allowed_subelement_types=frozenset({"Face", "Edge"}),
        allow_entire_job=True,
    )
    stock, stock_hash, stock_bounds = _prepare_stock(document, boundary)
    _validate_motion(parameters, stock_bounds)
    return PreparedProbeCreate(
        label=clean_operation_label(spec.label, "Probe grid"),
        boundary=boundary,
        parameters=parameters,
        stock=stock,
        stock_shape_sha256=stock_hash,
        stock_bounds=stock_bounds,
        vertical_feed=_vertical_feed(boundary),
    )


def _assert_stock_current(prepared: PreparedProbeCreate) -> None:
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
            "Exact CAM Job stock changed before Probe creation.",
            "NATIVE_MANUFACTURE_STATE_STALE",
        )


def _payload(prepared: PreparedProbeCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    return {
        "grid": {
            "point_count_x": parameters.point_count_x,
            "point_count_y": parameters.point_count_y,
            "x_offset_mm": parameters.x_offset_mm,
            "y_offset_mm": parameters.y_offset_mm,
        },
        "motion": {
            "probe_depth_mm": parameters.probe_depth_mm,
            "safe_height_mm": parameters.safe_height_mm,
            "clearance_height_mm": parameters.clearance_height_mm,
        },
    }


def _apply_settings(operation: Any, prepared: PreparedProbeCreate) -> None:
    _assert_stock_current(prepared)
    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        ("StartDepth", "FinalDepth", "SafeHeight", "ClearanceHeight"),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    operation.StartDepth = f"{prepared.stock_bounds[5]} mm"
    operation.FinalDepth = f"{parameters.probe_depth_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.Xoffset = f"{parameters.x_offset_mm} mm"
    operation.Yoffset = f"{parameters.y_offset_mm} mm"
    operation.PointCountX = parameters.point_count_x
    operation.PointCountY = parameters.point_count_y
    operation.OutputFileName = ""


def create_probe(
    document: Any,
    *,
    prepared: PreparedProbeCreate,
) -> NativeMutationDraft:
    if not isinstance(prepared, PreparedProbeCreate):
        raise TypeError("prepared must be a PreparedProbeCreate")

    import Path.Op.Gui.Probe as PathProbeGui
    import Path.Op.Probe as PathProbe

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Probe",
        operation_factory=PathProbe.Create,
        provider_factory=PathProbeGui.PathOpGui.ViewProvider,
        provider_resource=PathProbeGui.Command.res,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _payload(prepared)},
    )
    return extend_native_operation_draft(draft, probe_prepared=prepared)


def _numeric_label_matches(actual: str, requested: str) -> bool:
    if actual == requested:
        return True
    suffix = actual[len(requested) :] if actual.startswith(requested) else ""
    return len(suffix) >= 3 and suffix.isdigit()


def _grid_values(prepared: PreparedProbeCreate) -> tuple[tuple[float, float], ...]:
    parameters = prepared.parameters
    x_min, y_min, _z_min, x_max, y_max, _z_max = prepared.stock_bounds
    x_step = (x_max - x_min) / (parameters.point_count_x - 1)
    y_step = (y_max - y_min) / (parameters.point_count_y - 1)
    return tuple(
        (
            round(x_min + x_index * x_step + parameters.x_offset_mm, 9),
            round(y_min + y_index * y_step + parameters.y_offset_mm, 9),
        )
        for y_index in range(parameters.point_count_y)
        for x_index in range(parameters.point_count_x)
    )


def _same_parameters(actual: Mapping[str, Any], expected: Mapping[str, float]) -> bool:
    return set(actual) == set(expected) and all(
        math.isclose(
            float(actual[name]),
            float(value),
            rel_tol=0.0,
            abs_tol=1.0e-8,
        )
        for name, value in expected.items()
    )


def _assert_probe_settings(
    operation: Any,
    _payload_value: Mapping[str, Any],
    *,
    prepared: PreparedProbeCreate,
) -> None:
    parameters = prepared.parameters
    property_failures = []
    actual_label = str(operation.Label)
    if not _numeric_label_matches(actual_label, prepared.label):
        property_failures.append("label")
    expected_properties = {
        "StartDepth": prepared.stock_bounds[5],
        "FinalDepth": parameters.probe_depth_mm,
        "SafeHeight": parameters.safe_height_mm,
        "ClearanceHeight": parameters.clearance_height_mm,
        "Xoffset": parameters.x_offset_mm,
        "Yoffset": parameters.y_offset_mm,
    }
    for name, expected in expected_properties.items():
        if quantity_mm(operation, name) != expected:
            property_failures.append(name)
    if int(operation.PointCountX) != parameters.point_count_x:
        property_failures.append("PointCountX")
    if int(operation.PointCountY) != parameters.point_count_y:
        property_failures.append("PointCountY")
    if str(operation.OutputFileName):
        property_failures.append("OutputFileName")
    for name in ("StartDepth", "FinalDepth", "SafeHeight", "ClearanceHeight"):
        getter = getattr(operation, "getExpression", None)
        if callable(getter) and getter(name):
            property_failures.append(f"{name}_expression")
    if property_failures:
        _error(
            "The created Probe operation did not retain exact settings: "
            + ", ".join(property_failures)
            + ".",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={"failed_settings": property_failures},
        )

    commands = tuple(operation.Path.Commands or ())
    expected_count = 5 + 3 * parameters.point_count
    if len(commands) != expected_count:
        _error(
            "The created Probe operation produced the wrong bounded command count.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
            repair={
                "expected_command_count": expected_count,
                "actual_command_count": len(commands),
            },
        )
    if (
        str(commands[0].Name) != f"({actual_label})"
        or str(commands[1].Name) != "(Begin Probing )"
        or dict(commands[1].Annotations) != {"probe_open": ""}
        or str(commands[-2].Name) != "(PROBECLOSE)"
        or dict(commands[-2].Annotations) != {"probe_close": ""}
        or str(commands[-1].Name) != "G0"
        or not _same_parameters(
            commands[-1].Parameters,
            {"Z": parameters.clearance_height_mm},
        )
    ):
        _error(
            "The created Probe operation lost its automatic-output boundary commands.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    if str(commands[2].Name) != "G0" or not _same_parameters(
        commands[2].Parameters,
        {"Z": parameters.clearance_height_mm},
    ):
        _error(
            "The created Probe operation did not begin at clearance height.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    for index, (x_value, y_value) in enumerate(_grid_values(prepared)):
        start = 3 + 3 * index
        position, probe, retract = commands[start : start + 3]
        if (
            str(position.Name) != "G0"
            or not _same_parameters(
                position.Parameters,
                {
                    "X": x_value,
                    "Y": y_value,
                    "Z": parameters.safe_height_mm,
                },
            )
            or str(probe.Name) != "G38.2"
            or not _same_parameters(
                probe.Parameters,
                {"Z": parameters.probe_depth_mm, "F": prepared.vertical_feed},
            )
            or str(retract.Name) != "G0"
            or not _same_parameters(
                retract.Parameters,
                {"Z": parameters.safe_height_mm},
            )
        ):
            _error(
                f"The created Probe operation lost exact grid point {index} motion.",
                "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
                repair={"grid_point_index": index},
            )


def _verify_stock(
    _operation: Any,
    _payload_value: Mapping[str, Any],
    *,
    prepared: PreparedProbeCreate,
) -> Mapping[str, Any]:
    _assert_stock_current(prepared)
    return {
        "stock": {
            "object_name": str(prepared.stock.Name),
            "shape_sha256": prepared.stock_shape_sha256,
        }
    }


def verify_created_probe(
    document: Any,
    draft: NativeMutationDraft,
) -> dict[str, Any]:
    prepared = draft.value.get("probe_prepared")
    operation = draft.value.get("operation")
    if not isinstance(prepared, PreparedProbeCreate) or operation is None:
        raise TypeError("draft must contain one exact prepared Probe operation")
    verified = verify_native_operation(
        document,
        draft,
        result_key="probe",
        assert_settings=partial(_assert_probe_settings, prepared=prepared),
        additional_verify=partial(_verify_stock, prepared=prepared),
        minimum_cutting_commands=prepared.parameters.point_count,
        cutting_command_names=_PROBE_COMMANDS,
    )
    state = verified["probe"]
    job = verified["job"]
    controller_after = tool_controller_state(prepared.boundary.controller)
    if controller_after.get("state_sha256") != prepared.boundary.controller_before.get(
        "state_sha256"
    ):
        _error(
            "Probe creation changed its exact tool controller.",
            "NATIVE_MANUFACTURE_OPERATION_POSTCONDITION_FAILED",
        )
    parameters = prepared.parameters
    return {
        "operation": "create_grid",
        "object_name": str(operation.Name),
        "label": str(operation.Label)[:160],
        "job_object_name": str(prepared.boundary.job.Name),
        "tool_controller_object_name": str(prepared.boundary.controller.Name),
        "stock_object_name": str(prepared.stock.Name),
        "point_count_x": parameters.point_count_x,
        "point_count_y": parameters.point_count_y,
        "point_count": parameters.point_count,
        "path_command_count": 5 + 3 * parameters.point_count,
        "output_naming": "automatic_at_postprocess",
        "commanded_bounds_xy_mm": {
            "x_min": round(
                prepared.stock_bounds[0] + parameters.x_offset_mm,
                9,
            ),
            "y_min": round(
                prepared.stock_bounds[1] + parameters.y_offset_mm,
                9,
            ),
            "x_max": round(
                prepared.stock_bounds[3] + parameters.x_offset_mm,
                9,
            ),
            "y_max": round(
                prepared.stock_bounds[4] + parameters.y_offset_mm,
                9,
            ),
        },
        "stock_shape_sha256": prepared.stock_shape_sha256,
        "path_sha256": state.get("path_sha256"),
        "state_sha256": state.get("state_sha256"),
        "job_state_sha256": job.get("state_sha256"),
        "tool_controller_state_sha256": controller_after.get("state_sha256"),
    }
