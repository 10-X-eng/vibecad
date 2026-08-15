# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchOctagon import (
    create_sketch_octagon,
    preflight_sketch_octagon,
    prepare_sketch_octagon,
    verify_sketch_octagon,
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
def octagon_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_octagon(
        context,
        prepare_sketch_octagon(document.Uid, values),
    )


def test_octagon_matches_human_geometry_and_constraints(octagon_host) -> None:
    document, sketch, context = octagon_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_octagon(document, prepared)
    result = verify_sketch_octagon(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (10, 23)
    assert result["side_count"] == 8
    assert result["radius_mm"] == 10.0
    diagonal = 5.0 * math.sqrt(2.0)
    assert result["vertices_mm"][1] == pytest.approx([diagonal, diagonal, 0.0])
    assert [item["index"] for item in result["geometries"]] == list(range(1, 9))
    assert result["construction_circle"]["index"] == 9
    assert result["construction_circle"]["construction"] is True
    assert [item["type"] for item in result["constraints"]] == [
        *(["Coincident"] * 8),
        *(["Equal"] * 7),
        *(["PointOnObject"] * 8),
    ]


def test_octagon_rejects_coincident_center_and_corner(octagon_host) -> None:
    document, _sketch, _context = octagon_host

    with pytest.raises(NativeSketchError, match="must be distinct"):
        prepare_sketch_octagon(
            document.Uid,
            _values(corner_mm={"x": 0.0, "y": 0.0}),
        )


def test_octagon_verifier_rejects_side_geometry_drift(octagon_host) -> None:
    document, sketch, context = octagon_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_octagon(document, prepared)
    sketch.Geometry[1].StartPoint.x = 9.0

    with pytest.raises(NativeSketchError, match="side differs"):
        verify_sketch_octagon(document, draft)
