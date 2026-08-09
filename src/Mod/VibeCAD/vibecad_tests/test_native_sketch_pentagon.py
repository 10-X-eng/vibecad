# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchPentagon import (
    create_sketch_pentagon,
    preflight_sketch_pentagon,
    prepare_sketch_pentagon,
    verify_sketch_pentagon,
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
def pentagon_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_pentagon(
        context,
        prepare_sketch_pentagon(document.Uid, values),
    )


def test_pentagon_matches_human_geometry_and_constraints(pentagon_host) -> None:
    document, sketch, context = pentagon_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_pentagon(document, prepared)
    result = verify_sketch_pentagon(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (7, 14)
    assert result["side_count"] == 5
    assert result["radius_mm"] == 10.0
    assert result["vertices_mm"][0] == [10.0, 0.0, 0.0]
    assert result["vertices_mm"][1] == pytest.approx(
        [10.0 * math.cos(math.tau / 5.0), 10.0 * math.sin(math.tau / 5.0), 0.0]
    )
    assert [item["index"] for item in result["geometries"]] == [1, 2, 3, 4, 5]
    assert result["construction_circle"]["index"] == 6
    assert result["construction_circle"]["construction"] is True
    assert [item["type"] for item in result["constraints"]] == [
        *(["Coincident"] * 5),
        *(["Equal"] * 4),
        *(["PointOnObject"] * 5),
    ]
    assert result["constraints"][-1]["references"] == [
        {"slot": 1, "geometry_index": 5, "position": 2},
        {"slot": 2, "geometry_index": 6},
    ]


def test_pentagon_rejects_coincident_center_and_corner(pentagon_host) -> None:
    document, _sketch, _context = pentagon_host

    with pytest.raises(NativeSketchError, match="must be distinct"):
        prepare_sketch_pentagon(
            document.Uid,
            _values(corner_mm={"x": 0.0, "y": 0.0}),
        )


def test_pentagon_verifier_rejects_side_constraint_drift(pentagon_host) -> None:
    document, sketch, context = pentagon_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_pentagon(document, prepared)
    sketch.Constraints[5].Second = 5

    with pytest.raises(NativeSketchError, match="side equality constraint changed"):
        verify_sketch_pentagon(document, draft)
