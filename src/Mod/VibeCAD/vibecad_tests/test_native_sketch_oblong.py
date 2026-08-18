# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchOblong import (
    create_sketch_oblong,
    preflight_sketch_oblong,
    prepare_sketch_oblong,
    verify_sketch_oblong,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "first_corner_mm": {"x": -10.0, "y": -6.0},
            "opposite_corner_mm": {"x": 10.0, "y": 6.0},
            "corner_radius_mm": 2.0,
            **updates,
        }
    )


@pytest.fixture
def oblong_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_oblong(
        context,
        prepare_sketch_oblong(document.Uid, values),
    )


def test_oblong_matches_human_geometry_and_constraints(oblong_host) -> None:
    document, sketch, context = oblong_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_oblong(document, prepared)
    result = verify_sketch_oblong(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert result["geometry_count"] == 11
    assert result["constraint_count"] == 19
    assert result["corner_radius_mm"] == 2.0
    assert result["segment_count"] == 8
    assert result["closed"] is True
    assert result["corners_mm"] == [
        [-10.0, -6.0, 0.0],
        [10.0, -6.0, 0.0],
        [10.0, 6.0, 0.0],
        [-10.0, 6.0, 0.0],
    ]
    assert [item["geometry_index"] for item in result["geometry_refs"]] == list(range(1, 9))
    assert [item["kind"] for item in result["geometry_refs"]] == [
        *(["line"] * 4),
        *(["circular_arc"] * 4),
    ]
    assert [item["geometry_index"] for item in result["construction_geometry_refs"]] == [9, 10]
    assert all(item["construction"] is True for item in result["construction_geometry_refs"])
    assert [item["type"] for item in result["constraint_refs"]] == [
        *(["Tangent"] * 8),
        "Horizontal",
        "Vertical",
        "Horizontal",
        "Vertical",
        "Equal",
        "Equal",
        "Equal",
        *(["PointOnObject"] * 4),
    ]
    assert set(result) == {
        "sketch",
        "geometry_refs",
        "construction_geometry_refs",
        "constraint_refs",
        "corners_mm",
        "corner_radius_mm",
        "segment_count",
        "closed",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
    }


def test_oblong_supports_negative_diagonal_human_order(oblong_host) -> None:
    document, _sketch, context = oblong_host
    prepared = _prepared(
        document,
        context,
        _values(
            first_corner_mm={"x": -10.0, "y": 6.0},
            opposite_corner_mm={"x": 10.0, "y": -6.0},
        ),
    )

    result = verify_sketch_oblong(
        document,
        create_sketch_oblong(document, prepared),
    )

    assert result["corners_mm"] == [
        [-10.0, 6.0, 0.0],
        [-10.0, -6.0, 0.0],
        [10.0, -6.0, 0.0],
        [10.0, 6.0, 0.0],
    ]
    assert [item["type"] for item in result["constraint_refs"][8:12]] == [
        "Vertical",
        "Horizontal",
        "Vertical",
        "Horizontal",
    ]


@pytest.mark.parametrize("radius", (0.0, 6.0, 8.0))
def test_oblong_rejects_zero_or_oversized_radius(oblong_host, radius) -> None:
    document, _sketch, _context = oblong_host

    with pytest.raises(NativeSketchError):
        prepare_sketch_oblong(document.Uid, _values(corner_radius_mm=radius))


def test_oblong_verifier_rejects_arc_drift(oblong_host) -> None:
    document, sketch, context = oblong_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_oblong(document, prepared)
    sketch.Geometry[5].Radius = 3.0

    with pytest.raises(NativeSketchError, match="corner Arc"):
        verify_sketch_oblong(document, draft)


def test_oblong_verifier_rejects_tangent_drift(oblong_host) -> None:
    document, sketch, context = oblong_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_oblong(document, prepared)
    sketch.Constraints[0].SecondPos = 1

    with pytest.raises(NativeSketchError, match="tangent constraint changed"):
        verify_sketch_oblong(document, draft)
