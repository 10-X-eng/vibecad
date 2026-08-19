# SPDX-License-Identifier: LGPL-2.1-or-later

"""Provider fixture and arguments for the Native Sketch geometry GUI gate."""

from __future__ import annotations

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

from VibeCADEditState import active_edit_object
from VibeCADRibbonSurface import read_active_ribbon_surface
from vibecad_tests.native_sketch_provider_turn import provider_turn as provider_turn


def process_events(rounds: int = 16) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def edit_boundary(document, sketch, controller) -> tuple:
    surface = read_active_ribbon_surface(controller)
    return (
        App.ActiveDocument is document,
        active_edit_object() is sketch,
        Gui.activeWorkbench().name(),
        surface.authorization_token,
        int(document.getBookedTransactionID()),
        bool(document.HasPendingTransaction),
    )


def dimension_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    expected_inference: str,
    value: float,
    unit: str,
    driving: bool = True,
) -> dict:
    return {
        "operation": "infer_dimension",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "selection": [
            {"geometry_index": index, "position": position}
            for index, position in selection
        ],
        "expected_inference": expected_inference,
        "dimension": {"value": value, "unit": unit},
        "driving": driving,
    }


def _axis_distance_arguments(
    operation: str,
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    value: float,
    unit: str = "mm",
    driving: bool = True,
) -> dict:
    return {
        "operation": operation,
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "selection": [
            {"geometry_index": index, "position": position}
            for index, position in selection
        ],
        "dimension": {"value": value, "unit": unit},
        "driving": driving,
    }


def horizontal_distance_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    value: float,
    unit: str = "mm",
    driving: bool = True,
) -> dict:
    return _axis_distance_arguments(
        "constrain_distance_x",
        sketch,
        geometry_count=geometry_count,
        external_geometry_count=external_geometry_count,
        selection=selection,
        value=value,
        unit=unit,
        driving=driving,
    )


def vertical_distance_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    value: float,
    unit: str = "mm",
    driving: bool = True,
) -> dict:
    return _axis_distance_arguments(
        "constrain_distance_y",
        sketch,
        geometry_count=geometry_count,
        external_geometry_count=external_geometry_count,
        selection=selection,
        value=value,
        unit=unit,
        driving=driving,
    )


def distance_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    value: float,
    unit: str = "mm",
    driving: bool = True,
) -> dict:
    return _axis_distance_arguments(
        "constrain_distance",
        sketch,
        geometry_count=geometry_count,
        external_geometry_count=external_geometry_count,
        selection=selection,
        value=value,
        unit=unit,
        driving=driving,
    )


def radiam_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    expected_constraint: str,
    value: float,
    unit: str = "mm",
    driving: bool = True,
) -> dict:
    return {
        "operation": "constrain_radius_diameter",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "selection": [
            {"geometry_index": index, "position": position}
            for index, position in selection
        ],
        "expected_constraint": expected_constraint,
        "dimension": {"value": value, "unit": unit},
        "driving": driving,
    }


def radius_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    value: float,
    unit: str = "mm",
    driving: bool = True,
) -> dict:
    return _axis_distance_arguments(
        "constrain_radius",
        sketch,
        geometry_count=geometry_count,
        external_geometry_count=external_geometry_count,
        selection=selection,
        value=value,
        unit=unit,
        driving=driving,
    )


def diameter_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    value: float,
    unit: str = "mm",
    driving: bool = True,
) -> dict:
    return _axis_distance_arguments(
        "constrain_diameter",
        sketch,
        geometry_count=geometry_count,
        external_geometry_count=external_geometry_count,
        selection=selection,
        value=value,
        unit=unit,
        driving=driving,
    )


def angle_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    expected_form: str,
    value: float,
    unit: str = "deg",
    driving: bool = True,
) -> dict:
    return {
        "operation": "constrain_angle",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "selection": [
            {"geometry_index": index, "position": position}
            for index, position in selection
        ],
        "expected_form": expected_form,
        "dimension": {"value": value, "unit": unit},
        "driving": driving,
    }


def lock_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    target: dict,
    driving: bool = True,
) -> dict:
    return {
        "operation": "constrain_lock",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "target": target,
        "driving": driving,
    }


def coincident_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    target: dict,
) -> dict:
    return {
        "operation": "constrain_coincident",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "target": target,
    }


def horizontal_vertical_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
    expected_inference: str,
) -> dict:
    return {
        "operation": "constrain_horizontal_vertical",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "selection": [
            {"geometry_index": index, "position": position}
            for index, position in selection
        ],
        "expected_inference": expected_inference,
    }


def horizontal_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
) -> dict:
    return {
        "operation": "constrain_horizontal",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "selection": [
            {"geometry_index": index, "position": position}
            for index, position in selection
        ],
    }


def vertical_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[tuple[int, str], ...],
) -> dict:
    return {
        "operation": "constrain_vertical",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "selection": [
            {"geometry_index": index, "position": position}
            for index, position in selection
        ],
    }


def parallel_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    selection: tuple[int, int],
) -> dict:
    return {
        "operation": "constrain_parallel",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "selection": [
            {"geometry_index": index, "position": "whole"} for index in selection
        ],
    }


def point_arguments(sketch, *, geometry_count: int, x: float, y: float) -> dict:
    return {
        "operation": "create_point",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "position_mm": {"x": x, "y": y},
    }


def line_arguments(
    sketch,
    *,
    geometry_count: int,
    start: tuple[float, float],
    end: tuple[float, float],
) -> dict:
    return {
        "operation": "create_line",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "start_mm": {"x": start[0], "y": start[1]},
        "end_mm": {"x": end[0], "y": end[1]},
    }


def polyline_arguments(
    sketch,
    *,
    geometry_count: int,
    vertices: tuple[tuple[float, float], ...],
    closed: bool,
) -> dict:
    return {
        "operation": "create_polyline",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "vertices_mm": [{"x": point[0], "y": point[1]} for point in vertices],
        "closed": closed,
    }


def arc_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    radius: float,
    start_degrees: float,
    sweep_degrees: float,
) -> dict:
    return {
        "operation": "create_arc",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "center_mm": {"x": center[0], "y": center[1]},
        "radius_mm": radius,
        "start_angle_degrees": start_degrees,
        "end_angle_degrees": (start_degrees + sweep_degrees) % 360.0,
    }


def three_point_arc_arguments(
    sketch,
    *,
    geometry_count: int,
    first: tuple[float, float],
    second: tuple[float, float],
    rim: tuple[float, float],
) -> dict:
    return {
        "operation": "create3_point_arc",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "first_endpoint_mm": {"x": first[0], "y": first[1]},
        "second_endpoint_mm": {"x": second[0], "y": second[1]},
        "rim_point_mm": {"x": rim[0], "y": rim[1]},
    }


def elliptical_arc_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    major_radius: float,
    minor_radius: float,
    rotation_degrees: float,
    start_degrees: float,
    sweep_degrees: float,
) -> dict:
    return {
        "operation": "create_arc_of_ellipse",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "center_mm": {"x": center[0], "y": center[1]},
        "major_radius_mm": major_radius,
        "minor_radius_mm": minor_radius,
        "rotation_degrees": rotation_degrees,
        "start_parameter_degrees": start_degrees,
        "sweep_parameter_degrees": sweep_degrees,
    }


def hyperbolic_arc_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    major_radius: float,
    minor_radius: float,
    rotation_degrees: float,
    start_parameter: float,
    end_parameter: float,
) -> dict:
    return {
        "operation": "create_arc_of_hyperbola",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "center_mm": {"x": center[0], "y": center[1]},
        "major_radius_mm": major_radius,
        "minor_radius_mm": minor_radius,
        "rotation_degrees": rotation_degrees,
        "start_parameter": start_parameter,
        "end_parameter": end_parameter,
    }


def parabolic_arc_arguments(
    sketch,
    *,
    geometry_count: int,
    vertex: tuple[float, float],
    focal_length: float,
    rotation_degrees: float,
    start_parameter: float,
    end_parameter: float,
) -> dict:
    return {
        "operation": "create_arc_of_parabola",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "vertex_mm": {"x": vertex[0], "y": vertex[1]},
        "focal_length_mm": focal_length,
        "rotation_degrees": rotation_degrees,
        "start_parameter_mm": start_parameter,
        "end_parameter_mm": end_parameter,
    }


def circle_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    radius: float,
) -> dict:
    return {
        "operation": "create_circle",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "center_mm": {"x": center[0], "y": center[1]},
        "radius_mm": radius,
    }


def three_point_circle_arguments(
    sketch,
    *,
    geometry_count: int,
    points: tuple[tuple[float, float], tuple[float, float], tuple[float, float]],
) -> dict:
    return {
        "operation": "create3_point_circle",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "first_point_mm": {"x": points[0][0], "y": points[0][1]},
        "second_point_mm": {"x": points[1][0], "y": points[1][1]},
        "third_point_mm": {"x": points[2][0], "y": points[2][1]},
    }


def ellipse_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    major_radius: float,
    minor_radius: float,
    rotation_degrees: float,
) -> dict:
    return {
        "operation": "create_ellipse",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "center_mm": {"x": center[0], "y": center[1]},
        "major_radius_mm": major_radius,
        "minor_radius_mm": minor_radius,
        "rotation_degrees": rotation_degrees,
    }


def three_point_ellipse_arguments(
    sketch,
    *,
    geometry_count: int,
    first_axis_endpoint: tuple[float, float],
    second_axis_endpoint: tuple[float, float],
    rim_point: tuple[float, float],
) -> dict:
    return {
        "operation": "create3_point_ellipse",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "first_axis_endpoint_mm": {
            "x": first_axis_endpoint[0],
            "y": first_axis_endpoint[1],
        },
        "second_axis_endpoint_mm": {
            "x": second_axis_endpoint[0],
            "y": second_axis_endpoint[1],
        },
        "rim_point_mm": {"x": rim_point[0], "y": rim_point[1]},
    }


def rectangle_arguments(
    sketch,
    *,
    geometry_count: int,
    first_corner: tuple[float, float],
    opposite_corner: tuple[float, float],
) -> dict:
    return {
        "operation": "create_rectangle",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "first_corner_mm": {"x": first_corner[0], "y": first_corner[1]},
        "opposite_corner_mm": {
            "x": opposite_corner[0],
            "y": opposite_corner[1],
        },
    }


def center_rectangle_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    corner: tuple[float, float],
) -> dict:
    return {
        "operation": "create_center_rectangle",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "center_mm": {"x": center[0], "y": center[1]},
        "corner_mm": {"x": corner[0], "y": corner[1]},
    }


def rounded_rectangle_arguments(
    sketch,
    *,
    geometry_count: int,
    first_corner: tuple[float, float],
    opposite_corner: tuple[float, float],
    radius: float,
) -> dict:
    return {
        "operation": "create_rounded_rectangle",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "first_corner_mm": {"x": first_corner[0], "y": first_corner[1]},
        "opposite_corner_mm": {
            "x": opposite_corner[0],
            "y": opposite_corner[1],
        },
        "corner_radius_mm": radius,
    }


def _fixed_polygon_arguments(
    sketch,
    *,
    operation: str,
    geometry_count: int,
    center: tuple[float, float],
    corner: tuple[float, float],
) -> dict:
    return {
        "operation": operation,
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "center_mm": {"x": center[0], "y": center[1]},
        "corner_mm": {"x": corner[0], "y": corner[1]},
    }


def square_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    corner: tuple[float, float],
) -> dict:
    return _fixed_polygon_arguments(
        sketch,
        operation="create_square",
        geometry_count=geometry_count,
        center=center,
        corner=corner,
    )


def triangle_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    corner: tuple[float, float],
) -> dict:
    return _fixed_polygon_arguments(
        sketch,
        operation="create_triangle",
        geometry_count=geometry_count,
        center=center,
        corner=corner,
    )


def pentagon_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    corner: tuple[float, float],
) -> dict:
    return _fixed_polygon_arguments(
        sketch,
        operation="create_pentagon",
        geometry_count=geometry_count,
        center=center,
        corner=corner,
    )


def hexagon_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    corner: tuple[float, float],
) -> dict:
    return _fixed_polygon_arguments(
        sketch,
        operation="create_hexagon",
        geometry_count=geometry_count,
        center=center,
        corner=corner,
    )


def heptagon_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    corner: tuple[float, float],
) -> dict:
    return _fixed_polygon_arguments(
        sketch,
        operation="create_heptagon",
        geometry_count=geometry_count,
        center=center,
        corner=corner,
    )


def octagon_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    corner: tuple[float, float],
) -> dict:
    return _fixed_polygon_arguments(
        sketch,
        operation="create_octagon",
        geometry_count=geometry_count,
        center=center,
        corner=corner,
    )


def regular_polygon_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    corner: tuple[float, float],
    side_count: int,
) -> dict:
    return {
        **_fixed_polygon_arguments(
            sketch,
            operation="create_regular_polygon",
            geometry_count=geometry_count,
            center=center,
            corner=corner,
        ),
        "side_count": side_count,
    }


def slot_arguments(
    sketch,
    *,
    geometry_count: int,
    start_center: tuple[float, float],
    end_center: tuple[float, float],
    radius: float,
) -> dict:
    return {
        "operation": "create_slot",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "start_center_mm": {"x": start_center[0], "y": start_center[1]},
        "end_center_mm": {"x": end_center[0], "y": end_center[1]},
        "radius_mm": radius,
    }


def arc_slot_arguments(
    sketch,
    *,
    geometry_count: int,
    center: tuple[float, float],
    centerline_radius: float,
    start_degrees: float,
    sweep_degrees: float,
    slot_radius: float,
) -> dict:
    return {
        "operation": "create_arc_slot",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "center_mm": {"x": center[0], "y": center[1]},
        "centerline_radius_mm": centerline_radius,
        "start_angle_degrees": start_degrees,
        "sweep_angle_degrees": sweep_degrees,
        "slot_radius_mm": slot_radius,
    }


def bspline_arguments(
    sketch,
    *,
    geometry_count: int,
    control_points: tuple[tuple[float, float], ...],
    degree: int,
) -> dict:
    return {
        "operation": "create_b_spline",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "control_points_mm": [
            {"x": point[0], "y": point[1]} for point in control_points
        ],
        "degree": degree,
    }


def periodic_bspline_arguments(
    sketch,
    *,
    geometry_count: int,
    control_points: tuple[tuple[float, float], ...],
    degree: int,
) -> dict:
    return {
        **bspline_arguments(
            sketch,
            geometry_count=geometry_count,
            control_points=control_points,
            degree=degree,
        ),
        "operation": "create_periodic_b_spline",
    }


def interpolated_bspline_arguments(
    sketch,
    *,
    geometry_count: int,
    interpolation_points: tuple[tuple[float, float], ...],
) -> dict:
    return {
        "operation": "create_b_spline_by_interpolation",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "interpolation_points_mm": [
            {"x": point[0], "y": point[1]} for point in interpolation_points
        ],
    }


def periodic_interpolated_bspline_arguments(
    sketch,
    *,
    geometry_count: int,
    interpolation_points: tuple[tuple[float, float], ...],
) -> dict:
    return {
        **interpolated_bspline_arguments(
            sketch,
            geometry_count=geometry_count,
            interpolation_points=interpolation_points,
        ),
        "operation": "create_periodic_b_spline_by_interpolation",
    }


def text_arguments(
    sketch,
    *,
    geometry_count: int,
    text: str,
    font_name: str,
    handle_start: tuple[float, float],
    handle_end: tuple[float, float],
    sizing_mode: str,
) -> dict:
    return {
        "operation": "create_text",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "text": text,
        "font_name": font_name,
        "handle_start_mm": {"x": handle_start[0], "y": handle_start[1]},
        "handle_end_mm": {"x": handle_end[0], "y": handle_end[1]},
        "sizing_mode": sizing_mode,
    }


def construction_arguments(
    sketch,
    *,
    geometry_count: int,
    external_geometry_count: int,
    targets: tuple[tuple[int, bool], ...],
) -> dict:
    return {
        "operation": "toggle_construction",
        "sketch": {"object_name": sketch.Name},
        "expected_geometry_count": geometry_count,
        "expected_constraint_count": int(sketch.ConstraintCount),
        "expected_external_geometry_count": external_geometry_count,
        "targets": [
            {"geometry_index": index, "expected_state": expected}
            for index, expected in targets
        ],
    }
