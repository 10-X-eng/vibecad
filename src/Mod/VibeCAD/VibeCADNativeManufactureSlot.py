# SPDX-License-Identifier: LGPL-2.1-or-later

"""Exact, task-free creation of the shipped CAM Slot operation."""

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
    preflight_operation_without_geometry,
    quantity_mm,
    shape_sha256,
    validate_operation_tool,
    verify_native_operation,
)
from VibeCADNativeMutation import NativeMutationDraft


_SLOT_FIELDS = frozenset(
    {"path", "extend_start_mm", "extend_end_mm", "layer_mode", "reverse_direction"}
)
_DEPTH_FIELDS = frozenset({"start_depth_mm", "final_depth_mm", "step_down_mm"})
_HEIGHT_FIELDS = frozenset({"safe_height_mm", "clearance_height_mm"})
_REFERENCE_NAMES = {
    "center_of_mass": "Center of Mass",
    "bounding_box_center": "Center of BoundBox",
    "lowest_point": "Lowest Point",
    "highest_point": "Highest Point",
}
_REFERENCE_PUBLIC = {value: key for key, value in _REFERENCE_NAMES.items()}
_ORIENTATIONS = {
    "start_to_end": "Start to End",
    "perpendicular": "Perpendicular",
}
_ORIENTATION_PUBLIC = {value: key for key, value in _ORIENTATIONS.items()}
_LAYER_MODES = {
    "directional": "Directional",
    "bidirectional": "Bidirectional",
}
_COOLANT_MODES = {"none": "None", "flood": "Flood", "mist": "Mist"}
_POINT_FIELDS = frozenset({"x_mm", "y_mm", "z_mm"})
_POINT_TOLERANCE_MM = 1.0e-7


@dataclass(frozen=True, slots=True)
class SlotCreateSpec:
    label: Any
    job: Mapping[str, Any]
    tool_controller: Mapping[str, Any]
    slot: Mapping[str, Any]
    depths: Mapping[str, Any]
    heights: Mapping[str, Any]
    coolant: Any


@dataclass(frozen=True, slots=True)
class SlotParameters:
    path_kind: str
    requested_start_mm: tuple[float, float, float] | None
    requested_end_mm: tuple[float, float, float] | None
    reference1: str
    reference2: str
    orientation: str
    extend_start_mm: float
    extend_end_mm: float
    layer_mode: str
    reverse_direction: bool
    start_depth_mm: float
    final_depth_mm: float
    step_down_mm: float
    safe_height_mm: float
    clearance_height_mm: float
    coolant: str


@dataclass(frozen=True, slots=True)
class PreparedSlotCreate:
    label: str
    boundary: PreparedOperationBoundary
    parameters: SlotParameters
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


def _point(value: Any, noun: str) -> tuple[float, float, float]:
    point = exact_fields(value, _POINT_FIELDS, noun)
    return tuple(
        finite_number(point[f"{axis}_mm"], f"{noun} {axis}")
        for axis in ("x", "y", "z")
    )


def _distinct_xy(
    first: tuple[float, float, float],
    second: tuple[float, float, float],
    noun: str,
) -> None:
    if math.hypot(first[0] - second[0], first[1] - second[1]) <= _POINT_TOLERANCE_MM:
        _error(f"{noun} must resolve to two distinct XY points.")


def _model_target(path: Mapping[str, Any], expected_fields: frozenset[str], noun: str):
    request = exact_fields(path, expected_fields, noun)
    model = request["model"]
    if not isinstance(model, Mapping):
        _error(f"{noun} model must be one exact state target.")
    return request, model


def _path_request(
    raw: Any,
) -> tuple[
    str,
    Mapping[str, Any] | None,
    tuple[str, ...],
    tuple[float, float, float] | None,
    tuple[float, float, float] | None,
    str,
    str,
    str,
]:
    if not isinstance(raw, Mapping):
        _error("Slot path must be one closed path request.")
    kind = str(raw.get("kind") or "")
    if kind == "custom_points":
        path = exact_fields(
            raw,
            frozenset({"kind", "start_point_mm", "end_point_mm"}),
            "Custom-points Slot path",
        )
        first = _point(path["start_point_mm"], "Slot start point")
        second = _point(path["end_point_mm"], "Slot end point")
        if abs(first[2] - second[2]) > _POINT_TOLERANCE_MM:
            _error("Slot custom points must have the same Z coordinate.")
        _distinct_xy(first, second, "Slot custom points")
        return (
            kind,
            None,
            (),
            first,
            second,
            "Center of Mass",
            "Center of Mass",
            "Start to End",
        )

    if kind == "single_edge":
        path, model = _model_target(
            raw,
            frozenset({"kind", "model", "edge", "orientation"}),
            "Single-edge Slot path",
        )
        names = (str(path["edge"] or ""),)
        reference1 = "Long Edge"
        reference2 = "Center of Mass"
    elif kind == "single_horizontal_face":
        path, model = _model_target(
            raw,
            frozenset({"kind", "model", "face", "span", "orientation"}),
            "Single-horizontal-face Slot path",
        )
        names = (str(path["face"] or ""),)
        span = str(path["span"] or "")
        if span not in {"long_edge", "short_edge"}:
            _error("Slot horizontal-face span must be long_edge or short_edge.")
        reference1 = "Long Edge" if span == "long_edge" else "Short Edge"
        reference2 = "Center of Mass"
    elif kind == "single_vertical_face":
        path, model = _model_target(
            raw,
            frozenset({"kind", "model", "face", "orientation"}),
            "Single-vertical-face Slot path",
        )
        names = (str(path["face"] or ""),)
        reference1 = "Long Edge"
        reference2 = "Center of Mass"
    elif kind == "two_vertices":
        path, model = _model_target(
            raw,
            frozenset(
                {"kind", "model", "start_vertex", "end_vertex", "orientation"}
            ),
            "Two-vertex Slot path",
        )
        names = (
            str(path["start_vertex"] or ""),
            str(path["end_vertex"] or ""),
        )
        reference1 = reference2 = "Vertex"
    elif kind == "two_edges":
        path, model = _model_target(
            raw,
            frozenset(
                {
                    "kind",
                    "model",
                    "start_edge",
                    "start_reference",
                    "end_edge",
                    "end_reference",
                    "orientation",
                }
            ),
            "Two-edge Slot path",
        )
        names = (str(path["start_edge"] or ""), str(path["end_edge"] or ""))
        reference1 = _reference(path["start_reference"], "Slot start edge")
        reference2 = _reference(path["end_reference"], "Slot end edge")
    elif kind == "two_vertical_faces":
        path, model = _model_target(
            raw,
            frozenset(
                {
                    "kind",
                    "model",
                    "start_face",
                    "start_reference",
                    "end_face",
                    "end_reference",
                    "orientation",
                }
            ),
            "Two-vertical-face Slot path",
        )
        names = (str(path["start_face"] or ""), str(path["end_face"] or ""))
        reference1 = _reference(path["start_reference"], "Slot start face")
        reference2 = _reference(path["end_reference"], "Slot end face")
    else:
        _error(
            "Slot path kind must be custom_points, single_edge, "
            "single_horizontal_face, single_vertical_face, two_vertices, "
            "two_edges, or two_vertical_faces."
        )
    if len(names) == 2 and names[0] == names[1]:
        _error("A two-feature Slot path requires two distinct subelements.")
    orientation = str(path["orientation"] or "")
    if orientation not in _ORIENTATIONS:
        _error("Slot orientation must be start_to_end or perpendicular.")
    return (
        kind,
        model,
        names,
        None,
        None,
        reference1,
        reference2,
        _ORIENTATIONS[orientation],
    )


def _reference(value: Any, noun: str) -> str:
    key = str(value or "")
    if key not in _REFERENCE_NAMES:
        _error(
            f"{noun} reference must be center_of_mass, bounding_box_center, "
            "lowest_point, or highest_point."
        )
    return _REFERENCE_NAMES[key]


def _normalize_parameters(
    spec: SlotCreateSpec,
) -> tuple[SlotParameters, Mapping[str, Any] | None, tuple[str, ...]]:
    slot = exact_fields(spec.slot, _SLOT_FIELDS, "Slot settings")
    depths = exact_fields(spec.depths, _DEPTH_FIELDS, "Slot depths")
    heights = exact_fields(spec.heights, _HEIGHT_FIELDS, "Slot heights")
    (
        path_kind,
        model,
        names,
        requested_start,
        requested_end,
        reference1,
        reference2,
        orientation,
    ) = _path_request(slot["path"])
    layer_mode = str(slot["layer_mode"] or "")
    if layer_mode not in _LAYER_MODES:
        _error("Slot layer_mode must be directional or bidirectional.")
    start = finite_number(depths["start_depth_mm"], "Slot start depth")
    final = finite_number(depths["final_depth_mm"], "Slot final depth")
    if final >= start:
        _error("Slot final_depth_mm must be below start_depth_mm.")
    safe = finite_number(heights["safe_height_mm"], "Slot safe height")
    clearance = finite_number(heights["clearance_height_mm"], "Slot clearance height")
    if safe < start:
        _error("Slot safe_height_mm must be at or above start_depth_mm.")
    if clearance < safe:
        _error("Slot clearance_height_mm must be at or above safe_height_mm.")
    coolant = str(spec.coolant or "")
    if coolant not in _COOLANT_MODES:
        _error("Slot coolant must be none, flood, or mist.")
    return (
        SlotParameters(
            path_kind=path_kind,
            requested_start_mm=requested_start,
            requested_end_mm=requested_end,
            reference1=reference1,
            reference2=reference2,
            orientation=orientation,
            extend_start_mm=finite_number(slot["extend_start_mm"], "Slot start extension"),
            extend_end_mm=finite_number(slot["extend_end_mm"], "Slot end extension"),
            layer_mode=layer_mode,
            reverse_direction=_boolean(slot["reverse_direction"], "Slot reverse_direction"),
            start_depth_mm=start,
            final_depth_mm=final,
            step_down_mm=_positive(depths["step_down_mm"], "Slot step down"),
            safe_height_mm=safe,
            clearance_height_mm=clearance,
            coolant=coolant,
        ),
        model,
        names,
    )


def _normal(face: Any) -> Any:
    try:
        u_min, u_max, v_min, v_max = face.ParameterRange
        return face.normalAt((u_min + u_max) / 2.0, (v_min + v_max) / 2.0)
    except Exception as exc:
        raise NativeManufactureError(
            "Slot face orientation could not be evaluated.",
            error_code="NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        ) from exc


def _is_straight_edge(edge: Any) -> bool:
    import Part

    if isinstance(edge.Curve, (Part.Line, Part.LineSegment)):
        return True
    if len(edge.Vertexes) != 2:
        return False
    return abs(
        float(edge.Vertexes[0].Point.distanceToPoint(edge.Vertexes[1].Point))
        - float(edge.Length)
    ) <= 1.0e-7


def _reference_point(shape: Any, reference: str) -> tuple[float, float, float]:
    if reference == "Center of Mass":
        point = shape.CenterOfMass
    elif reference == "Center of BoundBox":
        point = shape.BoundBox.Center
    elif reference in {"Lowest Point", "Highest Point"}:
        vertices = tuple(shape.Vertexes)
        if not vertices:
            _error("Slot reference geometry has no vertices.")
        selector = min if reference == "Lowest Point" else max
        height = selector(float(vertex.Point.z) for vertex in vertices)
        selected = [
            vertex.Point
            for vertex in vertices
            if abs(float(vertex.Point.z) - height) <= _POINT_TOLERANCE_MM
        ]
        point = type(selected[0])(
            sum(float(value.x) for value in selected) / len(selected),
            sum(float(value.y) for value in selected) / len(selected),
            height,
        )
    elif reference == "Vertex":
        vertices = tuple(shape.Vertexes)
        if len(vertices) != 1:
            _error("Slot Vertex reference does not resolve to one vertex.")
        point = vertices[0].Point
    else:
        _error("Slot feature reference is invalid.")
    return (float(point.x), float(point.y), float(point.z))


def _validate_selected_path(
    boundary: PreparedOperationBoundary,
    parameters: SlotParameters,
    tool_diameter_mm: float,
) -> None:
    import Part
    import Path

    if boundary.geometry_kind != "subelements" or len(boundary.geometry) != 1:
        _error("A geometry-based Slot path must use one exact Job model.")
    item = boundary.geometry[0]
    shapes = tuple(
        item.public_source.Shape.getElement(name) for name in item.subelements
    )
    kind = parameters.path_kind
    if kind == "single_edge":
        edge = shapes[0]
        if not Path.Geom.isHorizontal(edge):
            _error("A single-edge Slot requires a horizontal Edge.")
        if isinstance(edge.Curve, Part.Circle):
            diameter = 2.0 * float(edge.Curve.Radius)
            if tool_diameter_mm > diameter + _POINT_TOLERANCE_MM:
                _error(
                    "The Slot cutter is larger than the selected circular Edge.",
                    "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
                )
            if len(edge.Vertexes) == 1 and (
                parameters.extend_start_mm != 0.0 or parameters.extend_end_mm != 0.0
            ):
                _error("A full-circle Slot does not accept start or end extensions.")
        elif not _is_straight_edge(edge):
            _error("A single-edge Slot requires a line, circular arc, or straight Edge.")
        if len(edge.Vertexes) == 2:
            first = edge.Vertexes[0].Point
            second = edge.Vertexes[1].Point
            _distinct_xy(
                (float(first.x), float(first.y), float(first.z)),
                (float(second.x), float(second.y), float(second.z)),
                "The selected Slot Edge",
            )
    elif kind == "single_horizontal_face":
        face = shapes[0]
        if abs(abs(float(_normal(face).z)) - 1.0) > 1.0e-7:
            _error("single_horizontal_face requires a horizontal Face.")
        if len(face.Edges) != 4 or not all(_is_straight_edge(edge) for edge in face.Edges):
            _error("A horizontal-face Slot requires one four-sided straight Face.")
        directions = []
        for edge in face.Edges:
            delta = edge.Vertexes[-1].Point - edge.Vertexes[0].Point
            length = math.hypot(float(delta.x), float(delta.y))
            if length <= _POINT_TOLERANCE_MM:
                _error("A horizontal-face Slot has a degenerate boundary Edge.")
            directions.append((float(delta.x) / length, float(delta.y) / length))
        parallel_pairs = sum(
            1
            for index, first in enumerate(directions)
            for second in directions[index + 1 :]
            if abs(first[0] * second[1] - first[1] * second[0]) <= 1.0e-7
        )
        if parallel_pairs < 2:
            _error("A horizontal-face Slot requires two opposing parallel edge pairs.")
    elif kind == "single_vertical_face":
        if abs(float(_normal(shapes[0]).z)) > 1.0e-7:
            _error("single_vertical_face requires a vertical Face.")
    elif kind == "two_vertices":
        points = tuple(_reference_point(shape, "Vertex") for shape in shapes)
        if abs(points[0][2] - points[1][2]) > _POINT_TOLERANCE_MM:
            _error("A two-vertex Slot requires vertices at the same Z coordinate.")
        _distinct_xy(points[0], points[1], "The selected Slot vertices")
    elif kind == "two_edges":
        if any(len(shape.Vertexes) < 2 for shape in shapes):
            _error("A two-edge Slot does not accept a closed Edge with one vertex.")
        points = (
            _reference_point(shapes[0], parameters.reference1),
            _reference_point(shapes[1], parameters.reference2),
        )
        _distinct_xy(points[0], points[1], "The selected Slot edge references")
    elif kind == "two_vertical_faces":
        if any(abs(float(_normal(face).z)) > 1.0e-7 for face in shapes):
            _error("A two-face Slot requires two vertical Faces.")
        points = (
            _reference_point(shapes[0], parameters.reference1),
            _reference_point(shapes[1], parameters.reference2),
        )
        _distinct_xy(points[0], points[1], "The selected Slot face references")
    else:
        raise RuntimeError("Unexpected selected Slot path kind")


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
            "Slot requires valid solid stock owned by the exact CAM Job.",
            "NATIVE_MANUFACTURE_TARGET_TYPE_INVALID",
        )
    return stock, shape_sha256(shape, "CAM Job stock")


def preflight_slot_create(document: Any, spec: SlotCreateSpec) -> PreparedSlotCreate:
    """Freeze the exact Job, stock, cutter, path definition, and Slot settings."""

    if not isinstance(spec, SlotCreateSpec):
        raise TypeError("spec must be a SlotCreateSpec")
    parameters, model, names = _normalize_parameters(spec)
    if parameters.path_kind == "custom_points":
        boundary = preflight_operation_without_geometry(
            document,
            noun="Slot",
            job_target=spec.job,
            tool_controller_target=spec.tool_controller,
        )
    else:
        boundary = preflight_operation_boundary(
            document,
            noun="Slot",
            job_target=spec.job,
            tool_controller_target=spec.tool_controller,
            geometry={
                "kind": "subelements",
                "items": [{"model": model, "subelements": list(names)}],
            },
            allowed_subelement_types=frozenset({"Face", "Edge", "Vertex"}),
            allow_entire_job=False,
        )
    tool_diameter = validate_operation_tool(boundary)
    if parameters.path_kind != "custom_points":
        _validate_selected_path(boundary, parameters, tool_diameter)
    stock, stock_hash = _prepare_stock(document, boundary)
    return PreparedSlotCreate(
        label=clean_operation_label(spec.label, "Slot"),
        boundary=boundary,
        parameters=parameters,
        stock=stock,
        stock_shape_sha256=stock_hash,
        tool_diameter_mm=tool_diameter,
    )


def _assert_stock_current(prepared: PreparedSlotCreate) -> None:
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
        _error("CAM Job stock changed before Slot creation.", "NATIVE_MANUFACTURE_STATE_STALE")


def _parameter_payload(prepared: PreparedSlotCreate) -> dict[str, Any]:
    parameters = prepared.parameters
    path: dict[str, Any] = {
        "kind": parameters.path_kind,
    }
    if parameters.path_kind == "custom_points":
        path.update(
            start_point_mm=dict(
                zip(("x_mm", "y_mm", "z_mm"), parameters.requested_start_mm)
            ),
            end_point_mm=dict(
                zip(("x_mm", "y_mm", "z_mm"), parameters.requested_end_mm)
            ),
        )
    else:
        path["orientation"] = _ORIENTATION_PUBLIC[parameters.orientation]
        if parameters.path_kind == "single_horizontal_face":
            path["span"] = (
                "long_edge" if parameters.reference1 == "Long Edge" else "short_edge"
            )
        elif parameters.path_kind in {"two_edges", "two_vertical_faces"}:
            path["start_reference"] = _REFERENCE_PUBLIC[parameters.reference1]
            path["end_reference"] = _REFERENCE_PUBLIC[parameters.reference2]
    return {
        "slot": {
            "path": path,
            "extend_start_mm": parameters.extend_start_mm,
            "extend_end_mm": parameters.extend_end_mm,
            "layer_mode": parameters.layer_mode,
            "reverse_direction": parameters.reverse_direction,
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
        "coolant": parameters.coolant,
    }


def _apply_settings(operation: Any, prepared: PreparedSlotCreate) -> None:
    import FreeCAD as App

    _assert_stock_current(prepared)
    parameters = prepared.parameters
    clear_operation_expressions(
        operation,
        (
            "CustomPoint1",
            "CustomPoint2",
            "ExtendPathStart",
            "ExtendPathEnd",
            "ExtendRadius",
            "StartDepth",
            "FinalDepth",
            "StepDown",
            "SafeHeight",
            "ClearanceHeight",
            "StartPoint",
        ),
    )
    operation.Label = prepared.label
    operation.ToolController = prepared.boundary.controller
    operation.OpToolDiameter = prepared.boundary.controller.Tool.Diameter
    if parameters.requested_start_mm is None:
        operation.CustomPoint1 = App.Vector(0.0, 0.0, 0.0)
        operation.CustomPoint2 = App.Vector(0.0, 0.0, 0.0)
    else:
        operation.CustomPoint1 = App.Vector(*parameters.requested_start_mm)
        operation.CustomPoint2 = App.Vector(*parameters.requested_end_mm)
    operation.Reference1 = parameters.reference1
    operation.Reference2 = parameters.reference2
    operation.ExtendPathStart = f"{parameters.extend_start_mm} mm"
    operation.ExtendPathEnd = f"{parameters.extend_end_mm} mm"
    operation.CutPattern = _LAYER_MODES[parameters.layer_mode]
    operation.PathOrientation = parameters.orientation
    operation.ReverseDirection = parameters.reverse_direction
    operation.StartDepth = f"{parameters.start_depth_mm} mm"
    operation.FinalDepth = f"{parameters.final_depth_mm} mm"
    operation.StepDown = f"{parameters.step_down_mm} mm"
    operation.SafeHeight = f"{parameters.safe_height_mm} mm"
    operation.ClearanceHeight = f"{parameters.clearance_height_mm} mm"
    operation.CoolantMode = _COOLANT_MODES[parameters.coolant]

    # Current human task-panel defaults not represented by Slot creation.
    operation.ExtendRadius = "0 mm"
    operation.ShowTempObjects = False
    operation.UseStartPoint = False
    operation.StartPoint = App.Vector(0.0, 0.0, 0.0)


def create_slot(
    document: Any,
    *,
    prepared: PreparedSlotCreate,
) -> NativeMutationDraft:
    """Create one native Slot operation inside the owned transaction."""

    if not isinstance(prepared, PreparedSlotCreate):
        raise TypeError("prepared must be a PreparedSlotCreate")
    import Path.Op.Slot as PathSlot

    provider_factory, provider_resource = native_operation_presentation(
        "Path.Op.Gui.Slot"
    )

    draft = create_native_operation(
        document,
        prepared=prepared.boundary,
        internal_name="Slot",
        operation_factory=PathSlot.Create,
        provider_factory=provider_factory,
        provider_resource=provider_resource,
        configure=partial(_apply_settings, prepared=prepared),
        payload={"parameters": _parameter_payload(prepared)},
    )
    return extend_native_operation_draft(draft, slot_prepared=prepared)


def _expression(operation: Any, property_name: str) -> Any:
    getter = getattr(operation, "getExpression", None)
    return getter(property_name) if callable(getter) else None


def _vector_tuple(value: Any) -> tuple[float, float, float]:
    return tuple(round(float(getattr(value, axis)), 9) for axis in ("x", "y", "z"))


def _assert_slot_settings(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedSlotCreate,
) -> None:
    parameters = prepared.parameters
    actual = {
        "reference1": str(operation.Reference1),
        "reference2": str(operation.Reference2),
        "extend_start_mm": quantity_mm(operation, "ExtendPathStart"),
        "extend_end_mm": quantity_mm(operation, "ExtendPathEnd"),
        "layer_mode": str(operation.CutPattern),
        "orientation": str(operation.PathOrientation),
        "reverse_direction": bool(operation.ReverseDirection),
        "start_depth_mm": quantity_mm(operation, "StartDepth"),
        "final_depth_mm": quantity_mm(operation, "FinalDepth"),
        "step_down_mm": quantity_mm(operation, "StepDown"),
        "safe_height_mm": quantity_mm(operation, "SafeHeight"),
        "clearance_height_mm": quantity_mm(operation, "ClearanceHeight"),
        "coolant": str(operation.CoolantMode),
        "extend_radius_mm": quantity_mm(operation, "ExtendRadius"),
        "show_temp_objects": bool(operation.ShowTempObjects),
        "use_start_point": bool(operation.UseStartPoint),
        "start_point_mm": _vector_tuple(operation.StartPoint),
    }
    expected = {
        "reference1": parameters.reference1,
        "reference2": parameters.reference2,
        "extend_start_mm": parameters.extend_start_mm,
        "extend_end_mm": parameters.extend_end_mm,
        "layer_mode": _LAYER_MODES[parameters.layer_mode],
        "orientation": parameters.orientation,
        "reverse_direction": parameters.reverse_direction,
        "start_depth_mm": parameters.start_depth_mm,
        "final_depth_mm": parameters.final_depth_mm,
        "step_down_mm": parameters.step_down_mm,
        "safe_height_mm": parameters.safe_height_mm,
        "clearance_height_mm": parameters.clearance_height_mm,
        "coolant": _COOLANT_MODES[parameters.coolant],
        "extend_radius_mm": 0.0,
        "show_temp_objects": False,
        "use_start_point": False,
        "start_point_mm": (0.0, 0.0, 0.0),
    }
    mismatches = {
        name: {"expected": value, "actual": actual.get(name)}
        for name, value in expected.items()
        if actual.get(name) != value
    }
    resolved_start = _vector_tuple(operation.CustomPoint1)
    resolved_end = _vector_tuple(operation.CustomPoint2)
    if not all(math.isfinite(value) for value in (*resolved_start, *resolved_end)):
        mismatches["resolved_points"] = {
            "expected": "two finite points",
            "actual": (resolved_start, resolved_end),
        }
    elif math.hypot(
        resolved_start[0] - resolved_end[0],
        resolved_start[1] - resolved_end[1],
    ) <= _POINT_TOLERANCE_MM:
        mismatches["resolved_points"] = {
            "expected": "two distinct XY points",
            "actual": (resolved_start, resolved_end),
        }
    for property_name in (
        "CustomPoint1",
        "CustomPoint2",
        "ExtendPathStart",
        "ExtendPathEnd",
        "ExtendRadius",
        "StartDepth",
        "FinalDepth",
        "StepDown",
        "SafeHeight",
        "ClearanceHeight",
        "StartPoint",
    ):
        expression = _expression(operation, property_name)
        if expression:
            mismatches[f"{property_name}_expression"] = {
                "expected": None,
                "actual": str(expression),
            }
    if mismatches:
        raise NativeManufactureError(
            "The created Slot operation did not retain: "
            + ", ".join(sorted(mismatches))
            + ".",
            error_code="NATIVE_MANUFACTURE_SLOT_POSTCONDITION_FAILED",
            repair={"parameter_mismatches": mismatches},
        )


def _slot_result(
    operation: Any,
    _payload: Mapping[str, Any],
    *,
    prepared: PreparedSlotCreate,
) -> Mapping[str, Any]:
    _assert_stock_current(prepared)
    return {
        "path_kind": prepared.parameters.path_kind,
        "resolved_start_mm": list(_vector_tuple(operation.CustomPoint1)),
        "resolved_end_mm": list(_vector_tuple(operation.CustomPoint2)),
        "tool_diameter_mm": prepared.tool_diameter_mm,
        "stock": {
            "object_name": str(prepared.stock.Name),
            "shape_sha256": prepared.stock_shape_sha256,
        },
    }


def verify_created_slot(document: Any, draft: NativeMutationDraft) -> dict[str, Any]:
    prepared: PreparedSlotCreate = draft.value["slot_prepared"]
    return verify_native_operation(
        document,
        draft,
        result_key="slot",
        assert_settings=partial(_assert_slot_settings, prepared=prepared),
        additional_verify=partial(_slot_result, prepared=prepared),
    )
