# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchHexagon import (
    create_sketch_hexagon,
    preflight_sketch_hexagon,
    prepare_sketch_hexagon,
    verify_sketch_hexagon,
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
def hexagon_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_hexagon(
        context,
        prepare_sketch_hexagon(document.Uid, values),
    )


def test_hexagon_matches_human_geometry_and_constraints(hexagon_host) -> None:
    document, sketch, context = hexagon_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_hexagon(document, prepared)
    result = verify_sketch_hexagon(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (8, 17)
    assert result["side_count"] == 6
    assert result["radius_mm"] == 10.0
    assert result["vertices_mm"][1] == pytest.approx(
        [5.0, 5.0 * math.sqrt(3.0), 0.0]
    )
    assert [item["index"] for item in result["geometries"]] == list(range(1, 7))
    assert result["construction_circle"]["index"] == 7
    assert result["construction_circle"]["construction"] is True
    assert [item["type"] for item in result["constraints"]] == [
        *(["Coincident"] * 6),
        *(["Equal"] * 5),
        *(["PointOnObject"] * 6),
    ]
    assert result["constraints"][-1]["references"] == [
        {"slot": 1, "geometry_index": 6, "position": 2},
        {"slot": 2, "geometry_index": 7},
    ]


def test_hexagon_rejects_coincident_center_and_corner(hexagon_host) -> None:
    document, _sketch, _context = hexagon_host

    with pytest.raises(NativeSketchError, match="must be distinct"):
        prepare_sketch_hexagon(
            document.Uid,
            _values(corner_mm={"x": 0.0, "y": 0.0}),
        )


def test_hexagon_verifier_rejects_corner_constraint_drift(hexagon_host) -> None:
    document, sketch, context = hexagon_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_hexagon(document, prepared)
    sketch.Constraints[0].SecondPos = 2

    with pytest.raises(NativeSketchError, match="corner constraint changed"):
        verify_sketch_hexagon(document, draft)
