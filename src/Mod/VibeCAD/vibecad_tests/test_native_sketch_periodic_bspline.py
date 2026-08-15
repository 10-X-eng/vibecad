# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchPeriodicBSpline import (
    create_sketch_periodic_bspline,
    preflight_sketch_periodic_bspline,
    prepare_sketch_periodic_bspline,
    verify_sketch_periodic_bspline,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


_POINTS = (
    (0.0, 0.0),
    (8.0, 1.0),
    (12.0, 7.0),
    (5.0, 12.0),
    (-3.0, 7.0),
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "control_points_mm": [
                {"x": point[0], "y": point[1]} for point in _POINTS
            ],
            "degree": 3,
            **updates,
        }
    )


@pytest.fixture
def periodic_bspline_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_periodic_bspline(
        context,
        prepare_sketch_periodic_bspline(document.Uid, values),
    )


def test_periodic_bspline_matches_human_control_point_topology(
    periodic_bspline_host,
) -> None:
    document, sketch, context = periodic_bspline_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_periodic_bspline(document, prepared)
    result = verify_sketch_periodic_bspline(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (13, 16)
    assert result["control_points_mm"] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]
    assert result["requested_degree"] == 3
    assert result["effective_degree"] == 3
    assert result["periodic"] is True
    assert result["construction"] is False

    spline = result["spline"]
    assert spline["index"] == 6
    assert spline["type_id"] == "Part::GeomBSplineCurve"
    assert spline["kind"] == "b_spline"
    assert spline["construction"] is False
    assert spline["degree"] == 3
    assert spline["pole_count"] == 5
    assert spline["knot_count"] == 6
    assert spline["poles_mm"] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]
    assert spline["weights"] == [1.0] * 5
    assert spline["knots"] == [0.0, 1.0, 2.0, 3.0, 4.0, 5.0]
    assert spline["multiplicities"] == [1] * 6
    assert spline["rational"] is False
    assert spline["periodic"] is True
    assert spline["closed"] is True
    assert spline["start_mm"] == spline["end_mm"]

    controls = result["control_point_handles"]
    assert [item["index"] for item in controls] == list(range(1, 6))
    assert [item["center_mm"] for item in controls] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]
    assert all(item["internal_type"] == "BSplineControlPoint" for item in controls)
    knots = result["knot_points"]
    assert [item["index"] for item in knots] == list(range(7, 13))
    assert len(knots) == 6
    assert knots[0]["position_mm"] == knots[-1]["position_mm"]
    assert all(item["internal_type"] == "BSplineKnotPoint" for item in knots)

    constraints = result["constraints"]
    assert [item["index"] for item in constraints] == list(range(16))
    assert [item["type"] for item in constraints] == [
        "Weight",
        *("Equal" for _index in range(4)),
        *("InternalAlignment" for _index in range(11)),
    ]
    assert constraints[5]["references"] == [
        {"slot": 1, "geometry_index": 1, "position": 3},
        {"slot": 2, "geometry_index": 6},
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 12, "position": 1},
        {"slot": 2, "geometry_index": 6},
    ]


def test_periodic_bspline_accepts_two_poles_and_clamps_degree_to_pole_count(
    periodic_bspline_host,
) -> None:
    document, _sketch, context = periodic_bspline_host
    points = [{"x": 0.0, "y": 0.0}, {"x": 8.0, "y": 2.0}]
    prepared = _prepared(
        document,
        context,
        _values(control_points_mm=points, degree=25),
    )

    result = verify_sketch_periodic_bspline(
        document,
        create_sketch_periodic_bspline(document, prepared),
    )

    assert result["requested_degree"] == 25
    assert result["effective_degree"] == 2
    assert result["spline"]["degree"] == 2
    assert result["spline"]["knots"] == [0.0, 1.0, 2.0]
    assert result["spline"]["multiplicities"] == [1, 1, 1]


@pytest.mark.parametrize(
    "updates",
    (
        {"control_points_mm": [{"x": 0.0, "y": 0.0}]},
        {
            "control_points_mm": [
                {"x": 0.0, "y": 0.0},
                {"x": 4.0, "y": 5.0},
                {"x": 0.0, "y": 0.0},
            ]
        },
        {"degree": 0},
        {"degree": 26},
        {"degree": True},
    ),
)
def test_periodic_bspline_rejects_invalid_definition(
    periodic_bspline_host,
    updates,
) -> None:
    document, _sketch, _context = periodic_bspline_host

    with pytest.raises(NativeSketchError):
        prepare_sketch_periodic_bspline(document.Uid, _values(**updates))


def test_periodic_bspline_verifier_rejects_periodicity_drift(
    periodic_bspline_host,
) -> None:
    document, sketch, context = periodic_bspline_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_periodic_bspline(document, prepared)
    sketch.Geometry[6]._periodic = False

    with pytest.raises(NativeSketchError, match="geometry differs"):
        verify_sketch_periodic_bspline(document, draft)
