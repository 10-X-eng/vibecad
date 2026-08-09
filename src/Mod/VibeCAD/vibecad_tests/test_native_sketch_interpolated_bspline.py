# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchInterpolatedBSpline import (
    create_sketch_interpolated_bspline,
    preflight_sketch_interpolated_bspline,
    prepare_sketch_interpolated_bspline,
    verify_sketch_interpolated_bspline,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


_POINTS = (
    (0.0, 0.0),
    (5.0, 8.0),
    (12.0, 7.0),
    (18.0, 0.0),
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
def interpolated_bspline_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_interpolated_bspline(
        context,
        prepare_sketch_interpolated_bspline(document.Uid, values),
    )


def test_interpolated_bspline_matches_human_knot_topology(
    interpolated_bspline_host,
) -> None:
    document, sketch, context = interpolated_bspline_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_interpolated_bspline(document, prepared)
    result = verify_sketch_interpolated_bspline(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (12, 16)
    assert result["interpolation_points_mm"] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]
    assert result["effective_degree"] == 3
    assert result["periodic"] is False
    assert result["construction"] is False

    spline = result["spline"]
    assert spline["index"] == 5
    assert spline["type_id"] == "Part::GeomBSplineCurve"
    assert spline["kind"] == "b_spline"
    assert spline["construction"] is False
    assert spline["degree"] == 3
    assert spline["pole_count"] == 6
    assert spline["knot_count"] == 4
    assert spline["multiplicities"] == [4, 1, 1, 4]
    assert spline["rational"] is False
    assert spline["periodic"] is False

    inputs = result["interpolation_point_handles"]
    assert [item["index"] for item in inputs] == list(range(1, 5))
    assert [item["position_mm"] for item in inputs] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]
    assert all(item["construction"] is True for item in inputs)
    assert all(item["internal_type"] == "BSplineKnotPoint" for item in inputs)

    controls = result["control_point_handles"]
    assert [item["index"] for item in controls] == list(range(6, 12))
    assert all(item["construction"] is True for item in controls)
    assert all(item["internal_type"] == "BSplineControlPoint" for item in controls)

    constraints = result["constraints"]
    assert [item["index"] for item in constraints] == list(range(16))
    assert [item["type"] for item in constraints] == [
        *("InternalAlignment" for _index in range(4)),
        "InternalAlignment",
        "Weight",
        *(value for _index in range(5) for value in ("InternalAlignment", "Equal")),
    ]
    assert constraints[0]["references"] == [
        {"slot": 1, "geometry_index": 1, "position": 1},
        {"slot": 2, "geometry_index": 5},
    ]
    assert constraints[4]["references"] == [
        {"slot": 1, "geometry_index": 6, "position": 3},
        {"slot": 2, "geometry_index": 5},
    ]
    assert constraints[5]["type"] == "Weight"
    assert constraints[7]["references"] == [
        {"slot": 1, "geometry_index": 7},
        {"slot": 2, "geometry_index": 6},
    ]


def test_three_point_interpolation_uses_human_point_on_curve_special_case(
    interpolated_bspline_host,
) -> None:
    document, _sketch, context = interpolated_bspline_host
    points = [{"x": 0.0, "y": 0.0}, {"x": 5.0, "y": 8.0}, {"x": 12.0, "y": 0.0}]
    prepared = _prepared(
        document,
        context,
        _values(interpolation_points_mm=points),
    )

    result = verify_sketch_interpolated_bspline(
        document,
        create_sketch_interpolated_bspline(document, prepared),
    )

    assert (result["geometry_count"], result["constraint_count"]) == (9, 11)
    assert result["spline"]["pole_count"] == 4
    assert result["spline"]["knot_count"] == 2
    assert result["spline"]["multiplicities"] == [4, 4]
    assert [item["type"] for item in result["constraints"][:3]] == [
        "InternalAlignment",
        "PointOnObject",
        "InternalAlignment",
    ]
    assert "internal_type" not in result["interpolation_point_handles"][1]


@pytest.mark.parametrize(
    "points",
    (
        [{"x": 0.0, "y": 0.0}],
        [{"x": 0.0, "y": 0.0}, {"x": 0.0, "y": 0.0}],
    ),
)
def test_interpolated_bspline_rejects_invalid_points(
    interpolated_bspline_host,
    points,
) -> None:
    document, _sketch, _context = interpolated_bspline_host

    with pytest.raises(NativeSketchError):
        prepare_sketch_interpolated_bspline(
            document.Uid,
            _values(interpolation_points_mm=points),
        )


def test_interpolated_bspline_verifier_rejects_control_alignment_drift(
    interpolated_bspline_host,
) -> None:
    document, sketch, context = interpolated_bspline_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_interpolated_bspline(document, prepared)
    sketch.Constraints[4].Second = 0

    with pytest.raises(NativeSketchError, match="control alignment changed"):
        verify_sketch_interpolated_bspline(document, draft)
