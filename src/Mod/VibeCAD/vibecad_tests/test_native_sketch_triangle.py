# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTriangle import (
    create_sketch_triangle,
    preflight_sketch_triangle,
    prepare_sketch_triangle,
    verify_sketch_triangle,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "center_mm": {"x": 0.0, "y": 0.0},
            "corner_mm": {"x": 10.0, "y": 0.0},
            **updates,
        }
    )


@pytest.fixture
def triangle_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_triangle(
        context,
        prepare_sketch_triangle(document.Uid, values),
    )


def test_triangle_matches_human_geometry_and_constraints(triangle_host) -> None:
    document, sketch, context = triangle_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_triangle(document, prepared)
    result = verify_sketch_triangle(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert result["geometry_count"] == 5
    assert result["constraint_count"] == 8
    assert result["center_mm"] == [0.0, 0.0, 0.0]
    assert result["corner_mm"] == [10.0, 0.0, 0.0]
    assert result["radius_mm"] == 10.0
    assert result["side_count"] == 3
    assert result["closed"] is True
    expected_vertices = (
        (10.0, 0.0, 0.0),
        (-5.0, 5.0 * math.sqrt(3.0), 0.0),
        (-5.0, -5.0 * math.sqrt(3.0), 0.0),
    )
    assert all(
        actual == pytest.approx(expected)
        for actual, expected in zip(
            result["vertices_mm"],
            expected_vertices,
            strict=True,
        )
    )
    assert [item["index"] for item in result["geometries"]] == [1, 2, 3]
    assert [item["type_id"] for item in result["geometries"]] == [
        "Part::GeomLineSegment",
        "Part::GeomLineSegment",
        "Part::GeomLineSegment",
    ]
    assert all(item["construction"] is False for item in result["geometries"])
    assert result["construction_circle"]["index"] == 4
    assert result["construction_circle"]["type_id"] == "Part::GeomCircle"
    assert result["construction_circle"]["construction"] is True
    assert result["construction_circle"]["radius_mm"] == 10.0
    assert [item["type"] for item in result["constraints"]] == [
        "Coincident",
        "Coincident",
        "Coincident",
        "Equal",
        "Equal",
        "PointOnObject",
        "PointOnObject",
        "PointOnObject",
    ]
    assert result["constraints"][0]["references"] == [
        {"slot": 1, "geometry_index": 1, "position": 2},
        {"slot": 2, "geometry_index": 2, "position": 1},
    ]
    assert result["constraints"][-1]["references"] == [
        {"slot": 1, "geometry_index": 3, "position": 2},
        {"slot": 2, "geometry_index": 4},
    ]
    assert set(result) == {
        "sketch",
        "geometries",
        "construction_circle",
        "constraints",
        "center_mm",
        "corner_mm",
        "vertices_mm",
        "radius_mm",
        "side_count",
        "closed",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
    }


@pytest.mark.parametrize(
    ("center", "corner", "match"),
    (
        ((1.0, 2.0), (1.0, 2.0), "must be distinct"),
        ((-600_000.0, 0.0), (600_000.0, 0.0), "radius must not exceed"),
        ((900_000.0, 0.0), (900_000.0, 500_000.0), "vertices must remain"),
    ),
)
def test_triangle_rejects_degenerate_or_unbounded_geometry(
    triangle_host,
    center,
    corner,
    match,
) -> None:
    document, _sketch, _context = triangle_host

    with pytest.raises(NativeSketchError, match=match):
        prepare_sketch_triangle(
            document.Uid,
            _values(
                center_mm={"x": center[0], "y": center[1]},
                corner_mm={"x": corner[0], "y": corner[1]},
            ),
        )


def test_triangle_verifier_rejects_construction_circle_drift(triangle_host) -> None:
    document, sketch, context = triangle_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_triangle(document, prepared)
    sketch.GeometryFacadeList[4].Construction = False

    with pytest.raises(NativeSketchError, match="construction Circle changed"):
        verify_sketch_triangle(document, draft)


def test_triangle_verifier_rejects_equality_constraint_drift(triangle_host) -> None:
    document, sketch, context = triangle_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_triangle(document, prepared)
    sketch.Constraints[3].Second = 3

    with pytest.raises(NativeSketchError, match="side equality constraint changed"):
        verify_sketch_triangle(document, draft)
