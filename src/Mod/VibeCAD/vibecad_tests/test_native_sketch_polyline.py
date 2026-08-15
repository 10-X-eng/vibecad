# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchPolyline import (
    MAX_POLYLINE_VERTICES,
    create_sketch_polyline,
    preflight_sketch_polyline,
    prepare_sketch_polyline,
    verify_sketch_polyline,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


_OPEN_VERTICES = [
    {"x": -4.0, "y": 1.0},
    {"x": 2.0, "y": 5.0},
    {"x": 7.0, "y": 3.0},
    {"x": 10.0, "y": -2.0},
]


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{"vertices_mm": _OPEN_VERTICES, "closed": False, **updates}
    )


@pytest.fixture
def polyline_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_open_polyline_is_one_connected_atomic_result(polyline_host) -> None:
    document, sketch, context = polyline_host
    spec = prepare_sketch_polyline(document.Uid, _values())
    prepared = preflight_sketch_polyline(context, spec)

    draft = create_sketch_polyline(document, prepared)
    result = verify_sketch_polyline(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert draft.created == ()
    assert result["segment_count"] == 3
    assert result["closed"] is False
    assert result["geometry_count"] == 4
    assert result["constraint_count"] == 2
    assert [item["index"] for item in result["geometries"]] == [1, 2, 3]
    assert [item["start_mm"] for item in result["geometries"]] == [
        [-4.0, 1.0, 0.0],
        [2.0, 5.0, 0.0],
        [7.0, 3.0, 0.0],
    ]
    assert [item["end_mm"] for item in result["geometries"]] == [
        [2.0, 5.0, 0.0],
        [7.0, 3.0, 0.0],
        [10.0, -2.0, 0.0],
    ]
    assert [item["references"] for item in result["constraints"]] == [
        [
            {"slot": 1, "geometry_index": 1, "position": 2},
            {"slot": 2, "geometry_index": 2, "position": 1},
        ],
        [
            {"slot": 1, "geometry_index": 2, "position": 2},
            {"slot": 2, "geometry_index": 3, "position": 1},
        ],
    ]
    assert all(item["type"] == "Coincident" for item in result["constraints"])
    assert set(result) == {
        "sketch",
        "geometries",
        "constraints",
        "segment_count",
        "closed",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
    }


def test_closed_polyline_adds_the_closing_segment_and_joint(polyline_host) -> None:
    document, _sketch, context = polyline_host
    vertices = [
        {"x": 0.0, "y": 0.0},
        {"x": 8.0, "y": 0.0},
        {"x": 3.0, "y": 6.0},
    ]
    spec = prepare_sketch_polyline(
        document.Uid,
        _values(vertices_mm=vertices, closed=True),
    )
    prepared = preflight_sketch_polyline(context, spec)

    result = verify_sketch_polyline(
        document,
        create_sketch_polyline(document, prepared),
    )

    assert result["closed"] is True
    assert result["segment_count"] == 3
    assert result["geometry_count"] == 4
    assert result["constraint_count"] == 3
    assert result["geometries"][-1]["start_mm"] == [3.0, 6.0, 0.0]
    assert result["geometries"][-1]["end_mm"] == [0.0, 0.0, 0.0]
    assert result["constraints"][-1]["references"] == [
        {"slot": 1, "geometry_index": 3, "position": 2},
        {"slot": 2, "geometry_index": 1, "position": 1},
    ]


@pytest.mark.parametrize(
    "updates,message",
    (
        (
            {
                "vertices_mm": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 0.0, "y": 0.0},
                ]
            },
            "endpoints must be distinct",
        ),
        (
            {
                "vertices_mm": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 5.0, "y": 0.0},
                ],
                "closed": True,
            },
            "at least 3",
        ),
        (
            {
                "vertices_mm": [
                    {"x": 0.0, "y": 0.0},
                    {"x": 5.0, "y": 0.0},
                    {"x": 0.0, "y": 0.0},
                ]
            },
            "closed=true",
        ),
    ),
)
def test_polyline_rejects_ambiguous_or_degenerate_paths(
    polyline_host,
    updates,
    message,
) -> None:
    document, _sketch, _context = polyline_host

    with pytest.raises(NativeSketchError, match=message):
        prepare_sketch_polyline(document.Uid, _values(**updates))


def test_polyline_rejects_unbounded_vertex_count(polyline_host) -> None:
    document, _sketch, _context = polyline_host
    vertices = [{"x": float(index), "y": 0.0} for index in range(66)]

    with pytest.raises(NativeSketchError, match=str(MAX_POLYLINE_VERTICES)):
        prepare_sketch_polyline(document.Uid, _values(vertices_mm=vertices))


def test_polyline_verifier_rejects_joint_constraint_drift(polyline_host) -> None:
    document, sketch, context = polyline_host
    prepared = preflight_sketch_polyline(
        context,
        prepare_sketch_polyline(document.Uid, _values()),
    )
    draft = create_sketch_polyline(document, prepared)
    sketch.Constraints[0].SecondPos = 2

    with pytest.raises(NativeSketchError, match="joint constraint changed"):
        verify_sketch_polyline(document, draft)


def test_polyline_verifier_rejects_segment_drift(polyline_host) -> None:
    document, sketch, context = polyline_host
    prepared = preflight_sketch_polyline(
        context,
        prepare_sketch_polyline(document.Uid, _values()),
    )
    draft = create_sketch_polyline(document, prepared)
    sketch.Geometry[2].EndPoint.y = 9.0

    with pytest.raises(NativeSketchError, match="segment differs"):
        verify_sketch_polyline(document, draft)
