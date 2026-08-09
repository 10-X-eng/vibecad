# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchCircle import (
    create_sketch_circle,
    preflight_sketch_circle,
    prepare_sketch_circle,
    verify_sketch_circle,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "center_mm": {"x": 2.0, "y": -1.0},
            "radius_mm": 5.0,
            **updates,
        }
    )


@pytest.fixture
def circle_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_circle_creates_exact_closed_geometry(circle_host) -> None:
    document, sketch, context = circle_host
    prepared = preflight_sketch_circle(
        context,
        prepare_sketch_circle(document.Uid, _values()),
    )

    draft = create_sketch_circle(document, prepared)
    result = verify_sketch_circle(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert result["geometry_count"] == 2
    assert result["constraint_count"] == 0
    geometry = result["geometry"]
    assert geometry["index"] == 1
    assert geometry["type_id"] == "Part::GeomCircle"
    assert geometry["kind"] == "circle"
    assert geometry["construction"] is False
    assert geometry["center_mm"] == [2.0, -1.0, 0.0]
    assert geometry["axis"] == [0.0, 0.0, 1.0]
    assert geometry["radius_mm"] == 5.0
    assert geometry["closed"] is True


@pytest.mark.parametrize(
    "updates",
    (
        {"radius_mm": 0.0},
        {"radius_mm": 1.0e-10},
        {"center_mm": {"x": 1_000_001.0, "y": 0.0}},
    ),
)
def test_circle_rejects_invalid_definition(circle_host, updates) -> None:
    document, _sketch, _context = circle_host

    with pytest.raises(NativeSketchError):
        prepare_sketch_circle(document.Uid, _values(**updates))


def test_circle_verifier_rejects_radius_drift(circle_host) -> None:
    document, sketch, context = circle_host
    prepared = preflight_sketch_circle(
        context,
        prepare_sketch_circle(document.Uid, _values()),
    )
    draft = create_sketch_circle(document, prepared)
    sketch.Geometry[1].Radius += 0.25

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_circle(document, draft)
