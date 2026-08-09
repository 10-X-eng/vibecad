# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchPeriodicInterpolatedBSpline import (
    create_sketch_periodic_interpolated_bspline,
    preflight_sketch_periodic_interpolated_bspline,
    prepare_sketch_periodic_interpolated_bspline,
    verify_sketch_periodic_interpolated_bspline,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


_POINTS = (
    (0.0, 0.0),
    (8.0, 1.0),
    (12.0, 7.0),
    (1.0, 10.0),
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "interpolation_points_mm": [
                {"x": point[0], "y": point[1]} for point in _POINTS
            ],
            **updates,
        }
    )


@pytest.fixture
def periodic_interpolated_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_periodic_interpolated_bspline(
        context,
        prepare_sketch_periodic_interpolated_bspline(document.Uid, values),
    )


def test_periodic_interpolation_matches_human_knot_topology(
    periodic_interpolated_host,
) -> None:
    document, sketch, context = periodic_interpolated_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_periodic_interpolated_bspline(document, prepared)
    result = verify_sketch_periodic_interpolated_bspline(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (12, 15)
    assert result["interpolation_points_mm"] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]
    assert result["effective_degree"] == 3
    assert result["periodic"] is True

    spline = result["spline"]
    assert spline["index"] == 5
    assert spline["degree"] == 3
    assert spline["pole_count"] == 5
    assert spline["knot_count"] == 5
    assert spline["multiplicities"] == [2, 1, 1, 1, 2]
    assert spline["rational"] is False
    assert spline["periodic"] is True
    assert spline["closed"] is True
    assert spline["start_mm"] == spline["end_mm"]

    inputs = result["interpolation_point_handles"]
    assert [item["index"] for item in inputs] == list(range(1, 5))
    assert all(item["internal_type"] == "BSplineKnotPoint" for item in inputs)
    controls = result["control_point_handles"]
    assert [item["index"] for item in controls] == list(range(6, 11))
    assert all(item["internal_type"] == "BSplineControlPoint" for item in controls)
    exposed = result["exposed_knot_points"]
    assert [item["index"] for item in exposed] == [11]
    assert exposed[0]["internal_type"] == "BSplineKnotPoint"
    assert exposed[0]["position_mm"] == spline["end_mm"]

    constraints = result["constraints"]
    assert [item["index"] for item in constraints] == list(range(15))
    assert [item["type"] for item in constraints] == [
        *("InternalAlignment" for _index in range(4)),
        "InternalAlignment",
        "Weight",
        *(value for _index in range(4) for value in ("InternalAlignment", "Equal")),
        "InternalAlignment",
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 11, "position": 1},
        {"slot": 2, "geometry_index": 5},
    ]


def test_two_point_periodic_interpolation_preserves_host_special_cardinality(
    periodic_interpolated_host,
) -> None:
    document, _sketch, context = periodic_interpolated_host
    points = [{"x": 0.0, "y": 0.0}, {"x": 8.0, "y": 2.0}]
    prepared = _prepared(
        document,
        context,
        _values(interpolation_points_mm=points),
    )

    result = verify_sketch_periodic_interpolated_bspline(
        document,
        create_sketch_periodic_interpolated_bspline(document, prepared),
    )

    assert (result["geometry_count"], result["constraint_count"]) == (11, 15)
    assert result["spline"]["pole_count"] == 6
    assert result["spline"]["knot_count"] == 3
    assert result["spline"]["multiplicities"] == [3, 3, 3]


@pytest.mark.parametrize(
    "points",
    (
        [{"x": 0.0, "y": 0.0}],
        [{"x": 0.0, "y": 0.0}, {"x": 0.0, "y": 0.0}],
        [
            {"x": 0.0, "y": 0.0},
            {"x": 5.0, "y": 8.0},
            {"x": 0.0, "y": 0.0},
        ],
    ),
)
def test_periodic_interpolation_rejects_invalid_points(
    periodic_interpolated_host,
    points,
) -> None:
    document, _sketch, _context = periodic_interpolated_host

    with pytest.raises(NativeSketchError):
        prepare_sketch_periodic_interpolated_bspline(
            document.Uid,
            _values(interpolation_points_mm=points),
        )


def test_periodic_interpolation_verifier_rejects_terminal_knot_drift(
    periodic_interpolated_host,
) -> None:
    document, sketch, context = periodic_interpolated_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_periodic_interpolated_bspline(document, prepared)
    sketch.Constraints[-1].Second = 0

    with pytest.raises(NativeSketchError, match="exposed knot alignment changed"):
        verify_sketch_periodic_interpolated_bspline(document, draft)
