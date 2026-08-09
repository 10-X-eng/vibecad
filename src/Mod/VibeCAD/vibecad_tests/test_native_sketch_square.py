# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchSquare import (
    create_sketch_square,
    preflight_sketch_square,
    prepare_sketch_square,
    verify_sketch_square,
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
def square_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_square(
        context,
        prepare_sketch_square(document.Uid, values),
    )


def test_square_matches_human_geometry_and_constraints(square_host) -> None:
    document, sketch, context = square_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_square(document, prepared)
    result = verify_sketch_square(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (6, 11)
    assert result["side_count"] == 4
    assert result["radius_mm"] == 10.0
    expected_vertices = (
        (10.0, 0.0, 0.0),
        (0.0, 10.0, 0.0),
        (-10.0, 0.0, 0.0),
        (0.0, -10.0, 0.0),
    )
    assert all(
        actual == pytest.approx(expected, abs=1.0e-9)
        for actual, expected in zip(
            result["vertices_mm"],
            expected_vertices,
            strict=True,
        )
    )
    assert [item["index"] for item in result["geometries"]] == [1, 2, 3, 4]
    assert result["construction_circle"]["index"] == 5
    assert result["construction_circle"]["construction"] is True
    assert [item["type"] for item in result["constraints"]] == [
        *(["Coincident"] * 4),
        *(["Equal"] * 3),
        *(["PointOnObject"] * 4),
    ]
    assert result["constraints"][-1]["references"] == [
        {"slot": 1, "geometry_index": 4, "position": 2},
        {"slot": 2, "geometry_index": 5},
    ]


def test_square_rejects_coincident_center_and_corner(square_host) -> None:
    document, _sketch, _context = square_host

    with pytest.raises(NativeSketchError, match="must be distinct"):
        prepare_sketch_square(
            document.Uid,
            _values(corner_mm={"x": 0.0, "y": 0.0}),
        )


def test_square_verifier_rejects_circumcircle_constraint_drift(square_host) -> None:
    document, sketch, context = square_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_square(document, prepared)
    sketch.Constraints[-1].Second = 4

    with pytest.raises(
        NativeSketchError,
        match="circumcircle incidence constraint changed",
    ):
        verify_sketch_square(document, draft)
