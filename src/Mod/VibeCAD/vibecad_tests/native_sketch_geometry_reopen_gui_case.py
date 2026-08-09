# SPDX-License-Identifier: LGPL-2.1-or-later

"""FCStd reopen verification for the rolling Native Sketch geometry gate."""

from __future__ import annotations

import math
from typing import Any, Mapping

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_basic_geometry_gui_case import (
    verify_reopened_basic_geometry,
)
from vibecad_tests.native_sketch_angle_gui_case import verify_reopened_angle
from vibecad_tests.native_sketch_coincident_gui_case import (
    verify_reopened_coincident,
)
from vibecad_tests.native_sketch_horizontal_vertical_gui_case import (
    verify_reopened_horizontal_vertical,
)
from vibecad_tests.native_sketch_horizontal_gui_case import (
    verify_reopened_horizontal,
)
from vibecad_tests.native_sketch_vertical_gui_case import verify_reopened_vertical
from vibecad_tests.native_sketch_parallel_gui_case import verify_reopened_parallel
from vibecad_tests.native_sketch_perpendicular_gui_case import (
    verify_reopened_perpendicular,
)
from vibecad_tests.native_sketch_tangent_gui_case import verify_reopened_tangent
from vibecad_tests.native_sketch_lock_gui_case import verify_reopened_lock
from vibecad_tests.native_sketch_bspline_gui_case import (
    verify_reopened_bspline,
    verify_reopened_interpolated_bspline,
    verify_reopened_periodic_bspline,
    verify_reopened_periodic_interpolated_bspline,
)
from vibecad_tests.native_sketch_circle_gui_case import (
    verify_reopened_circle,
    verify_reopened_three_point_circle,
)
from vibecad_tests.native_sketch_construction_gui_case import (
    verify_reopened_construction,
)
from vibecad_tests.native_sketch_dimension_gui_case import verify_reopened_dimension
from vibecad_tests.native_sketch_diameter_gui_case import verify_reopened_diameter
from vibecad_tests.native_sketch_distance_gui_case import verify_reopened_distance
from vibecad_tests.native_sketch_distance_x_gui_case import (
    verify_reopened_horizontal_distance,
)
from vibecad_tests.native_sketch_distance_y_gui_case import (
    verify_reopened_vertical_distance,
)
from vibecad_tests.native_sketch_ellipse_gui_case import (
    verify_reopened_ellipse,
    verify_reopened_three_point_ellipse,
)
from vibecad_tests.native_sketch_oblong_gui_case import verify_reopened_oblong
from vibecad_tests.native_sketch_rectangle_gui_case import (
    verify_reopened_center_rectangle,
    verify_reopened_rectangle,
)
from vibecad_tests.native_sketch_radiam_gui_case import verify_reopened_radiam
from vibecad_tests.native_sketch_radius_gui_case import verify_reopened_radius
from vibecad_tests.native_sketch_regular_polygon_gui_case import (
    verify_reopened_arbitrary_regular_polygon,
    verify_reopened_heptagon,
    verify_reopened_hexagon,
    verify_reopened_octagon,
    verify_reopened_pentagon,
    verify_reopened_square,
    verify_reopened_triangle,
)
from vibecad_tests.native_sketch_slot_gui_case import (
    verify_reopened_arc_slot,
    verify_reopened_slot,
)
from vibecad_tests.native_sketch_text_gui_case import verify_reopened_text


def _verify_inline_conics(sketch: Any, state: Mapping[str, Any]) -> None:
    reopened_arc = serialize_sketch_geometry(sketch, 5)
    assert reopened_arc["type_id"] == "Part::GeomArcOfCircle"
    assert reopened_arc["construction"] is False
    assert reopened_arc["center_mm"] == [18.0, 12.0, 0.0]
    assert reopened_arc["radius_mm"] == 6.0
    assert math.isclose(
        reopened_arc["first_parameter"],
        math.radians(30.0),
        abs_tol=1.0e-10,
    )
    assert math.isclose(
        reopened_arc["last_parameter"],
        math.radians(150.0),
        abs_tol=1.0e-10,
    )
    reopened_three_point = serialize_sketch_geometry(sketch, 6)
    assert reopened_three_point["type_id"] == "Part::GeomArcOfCircle"
    assert reopened_three_point["construction"] is False
    assert reopened_three_point["center_mm"] == [-10.0, 10.0, 0.0]
    assert reopened_three_point["radius_mm"] == 5.0
    assert math.isclose(
        reopened_three_point["first_parameter"],
        math.pi,
        abs_tol=1.0e-10,
    )
    assert math.isclose(
        reopened_three_point["last_parameter"],
        math.tau,
        abs_tol=1.0e-10,
    )
    reopened_elliptical = serialize_sketch_geometry(sketch, 7)
    assert reopened_elliptical["type_id"] == "Part::GeomArcOfEllipse"
    assert reopened_elliptical["construction"] is False
    assert reopened_elliptical["center_mm"] == [0.0, -15.0, 0.0]
    assert reopened_elliptical["major_radius_mm"] == 8.0
    assert reopened_elliptical["minor_radius_mm"] == 3.0
    assert [
        serialize_sketch_geometry(sketch, index)["internal_type"]
        for index in (8, 9, 10, 11)
    ] == [
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ]
    assert [
        serialize_sketch_constraint(sketch, index)["references"]
        for index in (2, 3, 4, 5)
    ] == [item["references"] for item in state["elliptical_constraints"]]
    reopened_hyperbolic = serialize_sketch_geometry(sketch, 12)
    assert reopened_hyperbolic["type_id"] == "Part::GeomArcOfHyperbola"
    assert reopened_hyperbolic["construction"] is False
    assert reopened_hyperbolic["center_mm"] == [15.0, -12.0, 0.0]
    assert reopened_hyperbolic["major_radius_mm"] == 5.0
    assert reopened_hyperbolic["minor_radius_mm"] == 3.0
    assert reopened_hyperbolic["first_parameter"] == -1.0
    assert reopened_hyperbolic["last_parameter"] == 1.0
    assert [
        serialize_sketch_geometry(sketch, index)["internal_type"]
        for index in (13, 14, 15)
    ] == ["HyperbolaMajor", "HyperbolaMinor", "HyperbolaFocus"]
    assert [
        serialize_sketch_constraint(sketch, index)["references"] for index in (6, 7, 8)
    ] == [item["references"] for item in state["hyperbolic_constraints"]]
    reopened_parabolic = serialize_sketch_geometry(sketch, 16)
    assert reopened_parabolic["type_id"] == "Part::GeomArcOfParabola"
    assert reopened_parabolic["center_mm"] == [-18.0, -10.0, 0.0]
    assert reopened_parabolic["focal_length_mm"] == 4.0
    assert reopened_parabolic["first_parameter"] == -5.0
    assert reopened_parabolic["last_parameter"] == 6.0
    assert [
        serialize_sketch_geometry(sketch, index)["internal_type"] for index in (17, 18)
    ] == ["ParabolaFocus", "ParabolaFocalAxis"]
    assert [
        serialize_sketch_constraint(sketch, index)["references"] for index in (9, 10)
    ] == [item["references"] for item in state["parabolic_constraints"]]


def verify_reopened_geometry_cases(sketch: Any, state: Mapping[str, Any]) -> None:
    verify_reopened_basic_geometry(sketch, line_construction=True)
    _verify_inline_conics(sketch, state)
    verify_reopened_circle(sketch, state["circle"])
    verify_reopened_three_point_circle(sketch, state["three_point_circle"])
    verify_reopened_ellipse(sketch, state["ellipse"])
    verify_reopened_three_point_ellipse(sketch, state["three_point_ellipse"])
    verify_reopened_rectangle(sketch, state["rectangle"])
    verify_reopened_center_rectangle(sketch, state["center_rectangle"])
    verify_reopened_oblong(sketch, state["oblong"])
    verify_reopened_triangle(sketch, state["triangle"])
    verify_reopened_square(sketch, state["square"])
    verify_reopened_pentagon(sketch, state["pentagon"])
    verify_reopened_hexagon(sketch, state["hexagon"])
    verify_reopened_heptagon(sketch, state["heptagon"])
    verify_reopened_octagon(sketch, state["octagon"])
    verify_reopened_arbitrary_regular_polygon(sketch, state["regular_polygon"])
    verify_reopened_slot(sketch, state["slot"])
    verify_reopened_arc_slot(sketch, state["arc_slot"])
    verify_reopened_bspline(sketch, state["bspline"])
    verify_reopened_periodic_bspline(sketch, state["periodic_bspline"])
    verify_reopened_interpolated_bspline(sketch, state["interpolated_bspline"])
    verify_reopened_periodic_interpolated_bspline(
        sketch,
        state["periodic_interpolated_bspline"],
    )
    verify_reopened_text(sketch, state["text"])
    verify_reopened_construction(sketch, state["construction"])
    verify_reopened_dimension(sketch, state["dimension"])
    verify_reopened_horizontal_distance(sketch, state["horizontal_distance"])
    verify_reopened_vertical_distance(sketch, state["vertical_distance"])
    verify_reopened_distance(sketch, state["distance"])
    verify_reopened_radiam(sketch, state["radiam"])
    verify_reopened_radius(sketch, state["radius"])
    verify_reopened_diameter(sketch, state["diameter"])
    verify_reopened_angle(sketch, state["angle"])
    verify_reopened_lock(sketch, state["lock"])
    verify_reopened_coincident(sketch, state["coincident"])
    verify_reopened_horizontal_vertical(sketch, state["horizontal_vertical"])
    verify_reopened_horizontal(sketch, state["horizontal"])
    verify_reopened_vertical(sketch, state["vertical"])
    verify_reopened_parallel(sketch, state["parallel"])
    verify_reopened_perpendicular(sketch, state["perpendicular"])
    verify_reopened_tangent(sketch, state["tangent"])
