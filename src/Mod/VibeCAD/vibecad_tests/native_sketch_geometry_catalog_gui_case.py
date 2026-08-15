# SPDX-License-Identifier: LGPL-2.1-or-later

"""Ordered catalog-geometry cases for the rolling Native Sketch GUI gate."""

from __future__ import annotations

from typing import Any, Callable

from vibecad_tests.native_sketch_bspline_gui_case import (
    exercise_bspline_case,
    exercise_interpolated_bspline_case,
    exercise_periodic_bspline_case,
    exercise_periodic_interpolated_bspline_case,
)
from vibecad_tests.native_sketch_circle_gui_case import (
    exercise_circle_case,
    exercise_three_point_circle_case,
)
from vibecad_tests.native_sketch_construction_gui_case import (
    exercise_construction_case,
)
from vibecad_tests.native_sketch_ellipse_gui_case import (
    exercise_ellipse_case,
    exercise_three_point_ellipse_case,
)
from vibecad_tests.native_sketch_oblong_gui_case import exercise_oblong_case
from vibecad_tests.native_sketch_rectangle_gui_case import (
    exercise_center_rectangle_case,
    exercise_rectangle_case,
)
from vibecad_tests.native_sketch_regular_polygon_gui_case import (
    exercise_arbitrary_regular_polygon_case,
    exercise_heptagon_case,
    exercise_hexagon_case,
    exercise_octagon_case,
    exercise_pentagon_case,
    exercise_square_case,
    exercise_triangle_case,
)
from vibecad_tests.native_sketch_slot_gui_case import (
    exercise_arc_slot_case,
    exercise_slot_case,
)
from vibecad_tests.native_sketch_text_gui_case import exercise_text_case


_ORDERED_CASES = (
    ("circle", exercise_circle_case),
    ("three_point_circle", exercise_three_point_circle_case),
    ("ellipse", exercise_ellipse_case),
    ("three_point_ellipse", exercise_three_point_ellipse_case),
    ("rectangle", exercise_rectangle_case),
    ("center_rectangle", exercise_center_rectangle_case),
    ("oblong", exercise_oblong_case),
    ("triangle", exercise_triangle_case),
    ("square", exercise_square_case),
    ("pentagon", exercise_pentagon_case),
    ("hexagon", exercise_hexagon_case),
    ("heptagon", exercise_heptagon_case),
    ("octagon", exercise_octagon_case),
    ("regular_polygon", exercise_arbitrary_regular_polygon_case),
    ("slot", exercise_slot_case),
    ("arc_slot", exercise_arc_slot_case),
    ("bspline", exercise_bspline_case),
    ("periodic_bspline", exercise_periodic_bspline_case),
    ("interpolated_bspline", exercise_interpolated_bspline_case),
    (
        "periodic_interpolated_bspline",
        exercise_periodic_interpolated_bspline_case,
    ),
    ("text", exercise_text_case),
    ("construction", exercise_construction_case),
)


def exercise_catalog_geometry_cases(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict[str, Any]:
    common = {
        "sketch": sketch,
        "document": document,
        "native_call": native_call,
        "process_events": process_events,
        "edit_boundary": edit_boundary,
        "boundary": boundary,
        "controller": controller,
    }
    return {name: exercise(**common) for name, exercise in _ORDERED_CASES}
