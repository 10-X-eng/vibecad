# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchHeptagon import (
    create_sketch_heptagon,
    preflight_sketch_heptagon,
    prepare_sketch_heptagon,
    verify_sketch_heptagon,
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
def heptagon_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_heptagon(
        context,
        prepare_sketch_heptagon(document.Uid, values),
    )


def test_heptagon_matches_human_geometry_and_constraints(heptagon_host) -> None:
    document, sketch, context = heptagon_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_heptagon(document, prepared)
    result = verify_sketch_heptagon(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (9, 20)
    assert result["side_count"] == 7
    assert result["radius_mm"] == 10.0
    assert result["vertices_mm"][1] == pytest.approx(
        [
            10.0 * math.cos(math.tau / 7.0),
            10.0 * math.sin(math.tau / 7.0),
            0.0,
        ]
    )
    assert [item["index"] for item in result["geometries"]] == list(range(1, 8))
    assert result["construction_circle"]["index"] == 8
    assert result["construction_circle"]["construction"] is True
    assert [item["type"] for item in result["constraints"]] == [
        *(["Coincident"] * 7),
        *(["Equal"] * 6),
        *(["PointOnObject"] * 7),
    ]


def test_heptagon_rejects_coincident_center_and_corner(heptagon_host) -> None:
    document, _sketch, _context = heptagon_host

    with pytest.raises(NativeSketchError, match="must be distinct"):
        prepare_sketch_heptagon(
            document.Uid,
            _values(corner_mm={"x": 0.0, "y": 0.0}),
        )


def test_heptagon_verifier_rejects_circle_geometry_drift(heptagon_host) -> None:
    document, sketch, context = heptagon_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_heptagon(document, prepared)
    sketch.Geometry[8].Radius = 11.0

    with pytest.raises(NativeSketchError, match="construction Circle changed"):
        verify_sketch_heptagon(document, draft)
