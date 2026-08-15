# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchArbitraryRegularPolygon import (
    create_sketch_arbitrary_regular_polygon,
    preflight_sketch_arbitrary_regular_polygon,
    prepare_sketch_arbitrary_regular_polygon,
    verify_sketch_arbitrary_regular_polygon,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "center_mm": {"x": 0.0, "y": 0.0},
            "corner_mm": {"x": 10.0, "y": 0.0},
            "side_count": 9,
            **updates,
        }
    )


@pytest.fixture
def regular_polygon_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_arbitrary_regular_polygon(
        context,
        prepare_sketch_arbitrary_regular_polygon(document.Uid, values),
    )


def test_arbitrary_regular_polygon_matches_human_topology(
    regular_polygon_host,
) -> None:
    document, sketch, context = regular_polygon_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_arbitrary_regular_polygon(document, prepared)
    result = verify_sketch_arbitrary_regular_polygon(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (11, 26)
    assert result["side_count"] == 9
    assert result["radius_mm"] == 10.0
    assert result["vertices_mm"][1] == pytest.approx(
        [
            10.0 * math.cos(math.tau / 9.0),
            10.0 * math.sin(math.tau / 9.0),
            0.0,
        ]
    )
    assert [item["index"] for item in result["geometries"]] == list(range(1, 10))
    assert result["construction_circle"]["index"] == 10
    assert result["construction_circle"]["construction"] is True
    assert [item["type"] for item in result["constraints"]] == [
        *(["Coincident"] * 9),
        *(["Equal"] * 8),
        *(["PointOnObject"] * 9),
    ]


@pytest.mark.parametrize("side_count", (2, 10_000, 3.5, True))
def test_arbitrary_regular_polygon_rejects_invalid_side_count(
    regular_polygon_host,
    side_count,
) -> None:
    document, _sketch, _context = regular_polygon_host

    with pytest.raises(NativeSketchError, match="integer from 3 through 9999"):
        prepare_sketch_arbitrary_regular_polygon(
            document.Uid,
            _values(side_count=side_count),
        )


def test_arbitrary_regular_polygon_verifier_rejects_constraint_drift(
    regular_polygon_host,
) -> None:
    document, sketch, context = regular_polygon_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_arbitrary_regular_polygon(document, prepared)
    sketch.Constraints[-1].FirstPos = 1
    sketch.Constraints[-1].Second = 9

    with pytest.raises(
        NativeSketchError,
        match="circumcircle incidence constraint changed",
    ):
        verify_sketch_arbitrary_regular_polygon(document, draft)
