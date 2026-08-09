# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchThreePointCircle import (
    create_sketch_three_point_circle,
    preflight_sketch_three_point_circle,
    prepare_sketch_three_point_circle,
    verify_sketch_three_point_circle,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "first_point_mm": {"x": -5.0, "y": 0.0},
            "second_point_mm": {"x": 5.0, "y": 0.0},
            "third_point_mm": {"x": 0.0, "y": 5.0},
            **updates,
        }
    )


@pytest.fixture
def host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_three_point_circle_creates_exact_circumcircle(host) -> None:
    document, sketch, context = host
    prepared = preflight_sketch_three_point_circle(
        context,
        prepare_sketch_three_point_circle(document.Uid, _values()),
    )
    draft = create_sketch_three_point_circle(document, prepared)
    result = verify_sketch_three_point_circle(document, draft)

    assert draft.recompute_targets == (sketch,)
    geometry = result["geometry"]
    assert geometry["type_id"] == "Part::GeomCircle"
    assert geometry["center_mm"] == [0.0, 0.0, 0.0]
    assert geometry["radius_mm"] == 5.0
    assert geometry["closed"] is True
    assert geometry["construction"] is False


@pytest.mark.parametrize(
    "updates",
    (
        {"second_point_mm": {"x": -5.0, "y": 0.0}},
        {"third_point_mm": {"x": 0.0, "y": 0.0}},
        {
            "first_point_mm": {"x": -5.0, "y": 0.0},
            "second_point_mm": {"x": 0.0, "y": 0.0},
            "third_point_mm": {"x": 5.0, "y": 0.0},
        },
    ),
)
def test_three_point_circle_rejects_degenerate_points(host, updates) -> None:
    document, _sketch, _context = host
    with pytest.raises(NativeSketchError):
        prepare_sketch_three_point_circle(document.Uid, _values(**updates))


def test_three_point_circle_verifier_rejects_radius_drift(host) -> None:
    document, sketch, context = host
    prepared = preflight_sketch_three_point_circle(
        context,
        prepare_sketch_three_point_circle(document.Uid, _values()),
    )
    draft = create_sketch_three_point_circle(document, prepared)
    sketch.Geometry[1].Radius += 0.25
    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_three_point_circle(document, draft)
