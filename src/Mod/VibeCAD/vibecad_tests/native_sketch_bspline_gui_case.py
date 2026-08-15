# SPDX-License-Identifier: LGPL-2.1-or-later

"""Non-periodic B-spline lifecycle case for the rolling Native Sketch gate."""

from __future__ import annotations

from typing import Any, Callable

from VibeCADNativeSketchState import (
    serialize_sketch_constraint,
    serialize_sketch_geometry,
)
from vibecad_tests.native_sketch_geometry_gui_support import (
    bspline_arguments,
    interpolated_bspline_arguments,
    periodic_bspline_arguments,
    periodic_interpolated_bspline_arguments,
)


_CONTROL_POINTS = (
    (-45.0, -60.0),
    (-38.0, -48.0),
    (-28.0, -48.0),
    (-20.0, -60.0),
)


def exercise_bspline_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict:
    undo_before = int(document.UndoCount)
    invalid = bspline_arguments(
        sketch,
        geometry_count=107,
        control_points=((-45.0, -60.0), (-45.0, -60.0)),
        degree=3,
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (107, 184)

    response = native_call(
        bspline_arguments(
            sketch,
            geometry_count=107,
            control_points=_CONTROL_POINTS,
            degree=3,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (114, 194)
    assert response["control_points_mm"] == [
        [x, y, 0.0] for x, y in _CONTROL_POINTS
    ]
    assert response["requested_degree"] == 3
    assert response["effective_degree"] == 3
    assert response["periodic"] is False
    assert response["construction"] is False

    spline = response["spline"]
    assert spline["index"] == 111
    assert spline["type_id"] == "Part::GeomBSplineCurve"
    assert spline["kind"] == "b_spline"
    assert spline["construction"] is False
    assert spline["degree"] == 3
    assert spline["pole_count"] == 4
    assert spline["knot_count"] == 2
    assert spline["poles_mm"] == [[x, y, 0.0] for x, y in _CONTROL_POINTS]
    assert spline["weights"] == [1.0, 1.0, 1.0, 1.0]
    assert spline["knots"] == [0.0, 1.0]
    assert spline["multiplicities"] == [4, 4]
    assert spline["rational"] is False
    assert spline["periodic"] is False

    controls = response["control_point_handles"]
    assert [item["index"] for item in controls] == list(range(107, 111))
    assert [item["center_mm"] for item in controls] == [
        [x, y, 0.0] for x, y in _CONTROL_POINTS
    ]
    assert all(item["construction"] is True for item in controls)
    assert all(item["internal_type"] == "BSplineControlPoint" for item in controls)

    knots = response["knot_points"]
    assert [item["index"] for item in knots] == [112, 113]
    assert [item["position_mm"] for item in knots] == [
        [-45.0, -60.0, 0.0],
        [-20.0, -60.0, 0.0],
    ]
    assert all(item["construction"] is True for item in knots)
    assert all(item["internal_type"] == "BSplineKnotPoint" for item in knots)

    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(184, 194))
    assert [item["type"] for item in constraints] == [
        "Weight",
        "Equal",
        "Equal",
        "Equal",
        *("InternalAlignment" for _index in range(6)),
    ]
    assert constraints[0]["value"] == 1.0
    assert constraints[0]["references"] == [
        {"slot": 1, "geometry_index": 107}
    ]
    assert constraints[4]["references"] == [
        {"slot": 1, "geometry_index": 107, "position": 3},
        {"slot": 2, "geometry_index": 111},
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 113, "position": 1},
        {"slot": 2, "geometry_index": 111},
    ]
    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == "Create Native Sketch B-Spline"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (107, 184)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (114, 194)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "spline": spline,
        "controls": controls,
        "knots": knots,
        "constraints": constraints,
    }


def verify_reopened_bspline(sketch: Any, expected: dict) -> None:
    spline = serialize_sketch_geometry(sketch, 111)
    for key in (
        "type_id",
        "kind",
        "construction",
        "degree",
        "pole_count",
        "knot_count",
        "poles_mm",
        "weights",
        "knots",
        "multiplicities",
        "rational",
        "periodic",
        "start_mm",
        "end_mm",
    ):
        assert spline[key] == expected["spline"][key]

    controls = [serialize_sketch_geometry(sketch, index) for index in range(107, 111)]
    for actual, saved in zip(controls, expected["controls"], strict=True):
        for key in (
            "type_id",
            "kind",
            "construction",
            "internal_type",
            "center_mm",
            "axis",
            "radius_mm",
        ):
            assert actual[key] == saved[key]

    knots = [serialize_sketch_geometry(sketch, index) for index in (112, 113)]
    for actual, saved in zip(knots, expected["knots"], strict=True):
        for key in (
            "type_id",
            "kind",
            "construction",
            "internal_type",
            "position_mm",
        ):
            assert actual[key] == saved[key]

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(184, 194)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_periodic_bspline_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict:
    control_points = (
        (20.0, -75.0),
        (32.0, -72.0),
        (38.0, -62.0),
        (28.0, -55.0),
        (17.0, -62.0),
    )
    undo_before = int(document.UndoCount)
    invalid = periodic_bspline_arguments(
        sketch,
        geometry_count=114,
        control_points=(
            (20.0, -75.0),
            (32.0, -72.0),
            (20.0, -75.0),
        ),
        degree=3,
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (114, 194)

    response = native_call(
        periodic_bspline_arguments(
            sketch,
            geometry_count=114,
            control_points=control_points,
            degree=3,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (126, 210)
    assert response["control_points_mm"] == [
        [x, y, 0.0] for x, y in control_points
    ]
    assert response["requested_degree"] == 3
    assert response["effective_degree"] == 3
    assert response["periodic"] is True
    assert response["construction"] is False

    spline = response["spline"]
    assert spline["index"] == 119
    assert spline["type_id"] == "Part::GeomBSplineCurve"
    assert spline["kind"] == "b_spline"
    assert spline["construction"] is False
    assert spline["degree"] == 3
    assert spline["pole_count"] == 5
    assert spline["knot_count"] == 6
    assert spline["poles_mm"] == [[x, y, 0.0] for x, y in control_points]
    assert spline["weights"] == [1.0] * 5
    assert spline["knots"] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert spline["multiplicities"] == [1] * 6
    assert spline["rational"] is False
    assert spline["periodic"] is True
    assert spline["closed"] is True
    assert spline["start_mm"] == spline["end_mm"]

    controls = response["control_point_handles"]
    assert [item["index"] for item in controls] == list(range(114, 119))
    assert [item["center_mm"] for item in controls] == [
        [x, y, 0.0] for x, y in control_points
    ]
    assert all(item["construction"] is True for item in controls)
    assert all(item["internal_type"] == "BSplineControlPoint" for item in controls)

    knots = response["knot_points"]
    assert [item["index"] for item in knots] == list(range(120, 126))
    assert knots[0]["position_mm"] == knots[-1]["position_mm"]
    assert all(item["construction"] is True for item in knots)
    assert all(item["internal_type"] == "BSplineKnotPoint" for item in knots)

    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(194, 210))
    assert [item["type"] for item in constraints] == [
        "Weight",
        *("Equal" for _index in range(4)),
        *("InternalAlignment" for _index in range(11)),
    ]
    assert constraints[0]["value"] == 1.0
    assert constraints[5]["references"] == [
        {"slot": 1, "geometry_index": 114, "position": 3},
        {"slot": 2, "geometry_index": 119},
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 125, "position": 1},
        {"slot": 2, "geometry_index": 119},
    ]
    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == "Create Native Sketch Periodic B-Spline"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (114, 194)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (126, 210)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "spline": spline,
        "controls": controls,
        "knots": knots,
        "constraints": constraints,
    }


def verify_reopened_periodic_bspline(sketch: Any, expected: dict) -> None:
    spline = serialize_sketch_geometry(sketch, 119)
    for key in (
        "type_id",
        "kind",
        "construction",
        "degree",
        "pole_count",
        "knot_count",
        "poles_mm",
        "weights",
        "knots",
        "multiplicities",
        "rational",
        "periodic",
        "closed",
        "start_mm",
        "end_mm",
    ):
        assert spline[key] == expected["spline"][key]

    controls = [serialize_sketch_geometry(sketch, index) for index in range(114, 119)]
    for actual, saved in zip(controls, expected["controls"], strict=True):
        for key in (
            "type_id",
            "kind",
            "construction",
            "internal_type",
            "center_mm",
            "axis",
            "radius_mm",
        ):
            assert actual[key] == saved[key]

    knots = [serialize_sketch_geometry(sketch, index) for index in range(120, 126)]
    for actual, saved in zip(knots, expected["knots"], strict=True):
        for key in (
            "type_id",
            "kind",
            "construction",
            "internal_type",
            "position_mm",
        ):
            assert actual[key] == saved[key]

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(194, 210)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_interpolated_bspline_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict:
    interpolation_points = (
        (-48.0, -82.0),
        (-38.0, -70.0),
        (-25.0, -73.0),
        (-15.0, -84.0),
    )
    undo_before = int(document.UndoCount)
    invalid = interpolated_bspline_arguments(
        sketch,
        geometry_count=126,
        interpolation_points=((-48.0, -82.0), (-48.0, -82.0)),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (126, 210)

    response = native_call(
        interpolated_bspline_arguments(
            sketch,
            geometry_count=126,
            interpolation_points=interpolation_points,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (137, 226)
    assert response["interpolation_points_mm"] == [
        [x, y, 0.0] for x, y in interpolation_points
    ]
    assert response["effective_degree"] == 3
    assert response["periodic"] is False
    assert response["construction"] is False

    spline = response["spline"]
    assert spline["index"] == 130
    assert spline["type_id"] == "Part::GeomBSplineCurve"
    assert spline["kind"] == "b_spline"
    assert spline["construction"] is False
    assert spline["degree"] == 3
    assert spline["pole_count"] == 6
    assert spline["knot_count"] == 4
    assert spline["multiplicities"] == [4, 1, 1, 4]
    assert spline["rational"] is False
    assert spline["periodic"] is False
    assert spline["poles_mm"][0] == [-48.0, -82.0, 0.0]
    assert spline["poles_mm"][-1] == [-15.0, -84.0, 0.0]
    assert spline["start_mm"] == [-48.0, -82.0, 0.0]
    assert spline["end_mm"] == [-15.0, -84.0, 0.0]
    assert len(spline["weights"]) == 6
    assert spline["weights"] == [1.0] * 6
    assert spline["knots"][0] == 0.0
    assert len(spline["knots"]) == 4

    inputs = response["interpolation_point_handles"]
    assert [item["index"] for item in inputs] == list(range(126, 130))
    assert [item["position_mm"] for item in inputs] == [
        [x, y, 0.0] for x, y in interpolation_points
    ]
    assert all(item["construction"] is True for item in inputs)
    assert all(item["internal_type"] == "BSplineKnotPoint" for item in inputs)

    controls = response["control_point_handles"]
    assert [item["index"] for item in controls] == list(range(131, 137))
    assert all(item["construction"] is True for item in controls)
    assert all(item["internal_type"] == "BSplineControlPoint" for item in controls)

    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(210, 226))
    assert [item["type"] for item in constraints] == [
        *("InternalAlignment" for _index in range(4)),
        "InternalAlignment",
        "Weight",
        *(value for _index in range(5) for value in ("InternalAlignment", "Equal")),
    ]
    assert constraints[0]["references"] == [
        {"slot": 1, "geometry_index": 126, "position": 1},
        {"slot": 2, "geometry_index": 130},
    ]
    assert constraints[4]["references"] == [
        {"slot": 1, "geometry_index": 131, "position": 3},
        {"slot": 2, "geometry_index": 130},
    ]
    assert constraints[5]["type"] == "Weight"
    assert constraints[7]["references"] == [
        {"slot": 1, "geometry_index": 132},
        {"slot": 2, "geometry_index": 131},
    ]
    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == "Create Native Sketch Interpolated B-Spline"

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (126, 210)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (137, 226)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "spline": spline,
        "inputs": inputs,
        "controls": controls,
        "constraints": constraints,
    }


def verify_reopened_interpolated_bspline(sketch: Any, expected: dict) -> None:
    spline = serialize_sketch_geometry(sketch, 130)
    for key in (
        "type_id",
        "kind",
        "construction",
        "degree",
        "pole_count",
        "knot_count",
        "poles_mm",
        "weights",
        "knots",
        "multiplicities",
        "rational",
        "periodic",
        "closed",
        "start_mm",
        "end_mm",
    ):
        assert spline[key] == expected["spline"][key]

    inputs = [serialize_sketch_geometry(sketch, index) for index in range(126, 130)]
    for actual, saved in zip(inputs, expected["inputs"], strict=True):
        for key in (
            "type_id",
            "kind",
            "construction",
            "internal_type",
            "position_mm",
        ):
            assert actual[key] == saved[key]

    controls = [serialize_sketch_geometry(sketch, index) for index in range(131, 137)]
    for actual, saved in zip(controls, expected["controls"], strict=True):
        for key in (
            "type_id",
            "kind",
            "construction",
            "internal_type",
            "center_mm",
            "axis",
            "radius_mm",
        ):
            assert actual[key] == saved[key]

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(210, 226)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]


def exercise_periodic_interpolated_bspline_case(
    *,
    sketch: Any,
    document: Any,
    native_call: Callable[..., dict],
    process_events: Callable[[int], None],
    edit_boundary: Callable[..., tuple],
    boundary: tuple,
    controller: Any,
) -> dict:
    interpolation_points = (
        (10.0, -92.0),
        (24.0, -89.0),
        (31.0, -78.0),
        (15.0, -72.0),
    )
    undo_before = int(document.UndoCount)
    invalid = periodic_interpolated_bspline_arguments(
        sketch,
        geometry_count=137,
        interpolation_points=(
            (10.0, -92.0),
            (24.0, -89.0),
            (10.0, -92.0),
        ),
    )
    failure = native_call(invalid, succeeds=False)
    assert failure["error_code"] == "NATIVE_SKETCH_INVALID"
    assert int(document.UndoCount) == undo_before
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (137, 226)

    response = native_call(
        periodic_interpolated_bspline_arguments(
            sketch,
            geometry_count=137,
            interpolation_points=interpolation_points,
        )
    )
    assert (response["geometry_count"], response["constraint_count"]) == (148, 241)
    assert response["interpolation_points_mm"] == [
        [x, y, 0.0] for x, y in interpolation_points
    ]
    assert response["effective_degree"] == 3
    assert response["periodic"] is True
    assert response["construction"] is False

    spline = response["spline"]
    assert spline["index"] == 141
    assert spline["type_id"] == "Part::GeomBSplineCurve"
    assert spline["kind"] == "b_spline"
    assert spline["construction"] is False
    assert spline["degree"] == 3
    assert spline["pole_count"] == 5
    assert spline["knot_count"] == 5
    assert spline["multiplicities"] == [2, 1, 1, 1, 2]
    assert spline["rational"] is False
    assert spline["periodic"] is True
    assert spline["closed"] is True
    assert spline["start_mm"] == spline["end_mm"]
    assert spline["weights"] == [1.0] * 5

    inputs = response["interpolation_point_handles"]
    assert [item["index"] for item in inputs] == list(range(137, 141))
    assert [item["position_mm"] for item in inputs] == [
        [x, y, 0.0] for x, y in interpolation_points
    ]
    assert all(item["construction"] is True for item in inputs)
    assert all(item["internal_type"] == "BSplineKnotPoint" for item in inputs)

    controls = response["control_point_handles"]
    assert [item["index"] for item in controls] == list(range(142, 147))
    assert all(item["construction"] is True for item in controls)
    assert all(item["internal_type"] == "BSplineControlPoint" for item in controls)

    exposed = response["exposed_knot_points"]
    assert [item["index"] for item in exposed] == [147]
    assert exposed[0]["construction"] is True
    assert exposed[0]["internal_type"] == "BSplineKnotPoint"
    assert exposed[0]["position_mm"] == spline["end_mm"]

    constraints = response["constraints"]
    assert [item["index"] for item in constraints] == list(range(226, 241))
    assert [item["type"] for item in constraints] == [
        *("InternalAlignment" for _index in range(4)),
        "InternalAlignment",
        "Weight",
        *(value for _index in range(4) for value in ("InternalAlignment", "Equal")),
        "InternalAlignment",
    ]
    assert constraints[0]["references"] == [
        {"slot": 1, "geometry_index": 137, "position": 1},
        {"slot": 2, "geometry_index": 141},
    ]
    assert constraints[4]["references"] == [
        {"slot": 1, "geometry_index": 142, "position": 3},
        {"slot": 2, "geometry_index": 141},
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 147, "position": 1},
        {"slot": 2, "geometry_index": 141},
    ]
    assert undo_before == 20
    assert int(document.UndoCount) == 20
    assert document.UndoNames[0] == (
        "Create Native Sketch Periodic Interpolated B-Spline"
    )

    document.undo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (137, 226)
    document.redo()
    process_events(16)
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == (148, 241)
    assert edit_boundary(document, sketch, controller) == boundary
    return {
        "spline": spline,
        "inputs": inputs,
        "controls": controls,
        "exposed": exposed,
        "constraints": constraints,
    }


def verify_reopened_periodic_interpolated_bspline(
    sketch: Any,
    expected: dict,
) -> None:
    spline = serialize_sketch_geometry(sketch, 141)
    for key in (
        "type_id",
        "kind",
        "construction",
        "degree",
        "pole_count",
        "knot_count",
        "poles_mm",
        "weights",
        "knots",
        "multiplicities",
        "rational",
        "periodic",
        "closed",
        "start_mm",
        "end_mm",
    ):
        assert spline[key] == expected["spline"][key]

    inputs = [serialize_sketch_geometry(sketch, index) for index in range(137, 141)]
    for actual, saved in zip(inputs, expected["inputs"], strict=True):
        for key in (
            "type_id",
            "kind",
            "construction",
            "internal_type",
            "position_mm",
        ):
            assert actual[key] == saved[key]

    controls = [serialize_sketch_geometry(sketch, index) for index in range(142, 147)]
    for actual, saved in zip(controls, expected["controls"], strict=True):
        for key in (
            "type_id",
            "kind",
            "construction",
            "internal_type",
            "center_mm",
            "axis",
            "radius_mm",
        ):
            assert actual[key] == saved[key]

    exposed = serialize_sketch_geometry(sketch, 147)
    for key in (
        "type_id",
        "kind",
        "construction",
        "internal_type",
        "position_mm",
    ):
        assert exposed[key] == expected["exposed"][0][key]

    constraints = [
        serialize_sketch_constraint(sketch, index) for index in range(226, 241)
    ]
    for actual, saved in zip(constraints, expected["constraints"], strict=True):
        for key in ("type", "driving", "active", "virtual", "references"):
            assert actual[key] == saved[key]
