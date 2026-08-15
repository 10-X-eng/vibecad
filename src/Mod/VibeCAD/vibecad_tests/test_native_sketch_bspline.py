# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchBSpline import (
    create_sketch_bspline,
    preflight_sketch_bspline,
    prepare_sketch_bspline,
    verify_sketch_bspline,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


_POINTS = (
    (0.0, 0.0),
    (5.0, 8.0),
    (12.0, 8.0),
    (18.0, 0.0),
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
def bspline_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_bspline(
        context,
        prepare_sketch_bspline(document.Uid, values),
    )


def test_nonperiodic_bspline_matches_human_control_point_topology(
    bspline_host,
) -> None:
    document, sketch, context = bspline_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_bspline(document, prepared)
    result = verify_sketch_bspline(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (8, 10)
    assert result["control_points_mm"] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]
    assert result["requested_degree"] == 3
    assert result["effective_degree"] == 3
    assert result["periodic"] is False
    assert result["construction"] is False

    spline = result["spline"]
    assert spline["index"] == 5
    assert spline["type_id"] == "Part::GeomBSplineCurve"
    assert spline["kind"] == "b_spline"
    assert spline["construction"] is False
    assert spline["degree"] == 3
    assert spline["pole_count"] == 4
    assert spline["knot_count"] == 2
    assert spline["poles_mm"] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]
    assert spline["weights"] == [1.0, 1.0, 1.0, 1.0]
    assert spline["knots"] == [0.0, 1.0]
    assert spline["multiplicities"] == [4, 4]
    assert spline["rational"] is False
    assert spline["periodic"] is False

    controls = result["control_point_handles"]
    assert [item["index"] for item in controls] == [1, 2, 3, 4]
    assert [item["center_mm"] for item in controls] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]
    assert all(item["construction"] is True for item in controls)
    assert all(item["internal_type"] == "BSplineControlPoint" for item in controls)
    knots = result["knot_points"]
    assert [item["index"] for item in knots] == [6, 7]
    assert [item["position_mm"] for item in knots] == [
        [0.0, 0.0, 0.0],
        [18.0, 0.0, 0.0],
    ]
    assert all(item["internal_type"] == "BSplineKnotPoint" for item in knots)

    constraints = result["constraints"]
    assert [item["index"] for item in constraints] == list(range(10))
    assert [item["type"] for item in constraints] == [
        "Weight",
        "Equal",
        "Equal",
        "Equal",
        *("InternalAlignment" for _index in range(6)),
    ]
    assert constraints[0]["value"] == 1.0
    assert constraints[4]["references"] == [
        {"slot": 1, "geometry_index": 1, "position": 3},
        {"slot": 2, "geometry_index": 5},
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 7, "position": 1},
        {"slot": 2, "geometry_index": 5},
    ]


def test_nonperiodic_bspline_clamps_requested_degree_like_human_tool(
    bspline_host,
) -> None:
    document, _sketch, context = bspline_host
    points = [{"x": 0.0, "y": 0.0}, {"x": 4.0, "y": 6.0}, {"x": 9.0, "y": 0.0}]
    prepared = _prepared(
        document,
        context,
        _values(control_points_mm=points, degree=25),
    )

    result = verify_sketch_bspline(
        document,
        create_sketch_bspline(document, prepared),
    )

    assert result["requested_degree"] == 25
    assert result["effective_degree"] == 2
    assert result["spline"]["degree"] == 2
    assert result["spline"]["multiplicities"] == [3, 3]


def test_nonperiodic_linear_bspline_exposes_every_generated_knot(
    bspline_host,
) -> None:
    document, _sketch, context = bspline_host
    prepared = _prepared(document, context, _values(degree=1))

    result = verify_sketch_bspline(
        document,
        create_sketch_bspline(document, prepared),
    )

    assert result["effective_degree"] == 1
    assert result["spline"]["knots"] == [0.0, 1.0, 2.0, 3.0]
    assert result["spline"]["multiplicities"] == [2, 1, 1, 2]
    assert [item["position_mm"] for item in result["knot_points"]] == [
        [point[0], point[1], 0.0] for point in _POINTS
    ]


@pytest.mark.parametrize(
    "updates",
    (
        {"control_points_mm": [{"x": 0.0, "y": 0.0}]},
        {
            "control_points_mm": [
                {"x": 0.0, "y": 0.0},
                {"x": 0.0, "y": 0.0},
            ]
        },
        {"degree": 0},
        {"degree": 26},
        {"degree": True},
    ),
)
def test_nonperiodic_bspline_rejects_invalid_definition(
    bspline_host,
    updates,
) -> None:
    document, _sketch, _context = bspline_host

    with pytest.raises(NativeSketchError):
        prepare_sketch_bspline(document.Uid, _values(**updates))


def test_nonperiodic_bspline_verifier_rejects_alignment_drift(
    bspline_host,
) -> None:
    document, sketch, context = bspline_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_bspline(document, prepared)
    sketch.Constraints[5].Second = 0

    with pytest.raises(NativeSketchError, match="control-point alignment changed"):
        verify_sketch_bspline(document, draft)
