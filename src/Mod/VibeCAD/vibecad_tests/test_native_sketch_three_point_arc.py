# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchThreePointArc import (
    create_sketch_three_point_arc,
    preflight_sketch_three_point_arc,
    prepare_sketch_three_point_arc,
    verify_sketch_three_point_arc,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "first_endpoint_mm": {"x": 0.0, "y": 0.0},
            "second_endpoint_mm": {"x": 10.0, "y": 0.0},
            "rim_point_mm": {"x": 5.0, "y": 5.0},
            **updates,
        }
    )


@pytest.fixture
def three_point_arc_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_three_point_arc_creates_exact_arc_through_upper_rim(
    three_point_arc_host,
) -> None:
    document, sketch, context = three_point_arc_host
    spec = prepare_sketch_three_point_arc(document.Uid, _values())
    prepared = preflight_sketch_three_point_arc(context, spec)

    draft = create_sketch_three_point_arc(document, prepared)
    result = verify_sketch_three_point_arc(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert spec.center_mm == (5.0, 0.0)
    assert spec.radius_mm == 5.0
    assert math.isclose(spec.first_parameter, 0.0)
    assert math.isclose(spec.rim_parameter, math.pi / 2.0)
    assert math.isclose(spec.last_parameter, math.pi)
    geometry = result["geometry"]
    assert geometry["index"] == 1
    assert geometry["type_id"] == "Part::GeomArcOfCircle"
    assert geometry["kind"] == "circular_arc"
    assert geometry["center_mm"] == [5.0, 0.0, 0.0]
    assert geometry["radius_mm"] == 5.0
    assert geometry["start_mm"] == [10.0, 0.0, 0.0]
    assert math.isclose(geometry["end_mm"][0], 0.0, abs_tol=1.0e-12)
    assert math.isclose(geometry["end_mm"][1], 0.0, abs_tol=1.0e-12)
    assert geometry["closed"] is False
    assert result["geometry_count"] == 2
    assert result["constraint_count"] == 0


def test_three_point_arc_selects_wrapped_arc_through_lower_rim(
    three_point_arc_host,
) -> None:
    document, _sketch, context = three_point_arc_host
    spec = prepare_sketch_three_point_arc(
        document.Uid,
        _values(rim_point_mm={"x": 5.0, "y": -5.0}),
    )

    result = verify_sketch_three_point_arc(
        document,
        create_sketch_three_point_arc(
            document,
            preflight_sketch_three_point_arc(context, spec),
        ),
    )

    assert math.isclose(spec.first_parameter, math.pi)
    assert math.isclose(spec.rim_parameter, 3.0 * math.pi / 2.0)
    assert math.isclose(spec.last_parameter, math.tau)
    assert result["geometry"]["start_mm"] == [0.0, 0.0, 0.0]
    assert result["geometry"]["end_mm"] == [10.0, 0.0, 0.0]


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        (
            {"second_endpoint_mm": {"x": 0.0, "y": 0.0}},
            "endpoints must be distinct",
        ),
        (
            {"rim_point_mm": {"x": 0.0, "y": 0.0}},
            "first endpoint and rim point endpoints must be distinct",
        ),
        (
            {"rim_point_mm": {"x": 5.0, "y": 0.0}},
            "must not be collinear",
        ),
        (
            {"rim_point_mm": {"x": 5.0, "y": 1.0e-10}},
            "must not be collinear",
        ),
    ),
)
def test_three_point_arc_rejects_degenerate_definitions(
    three_point_arc_host,
    updates: dict[str, object],
    message: str,
) -> None:
    document, _sketch, _context = three_point_arc_host

    with pytest.raises(NativeSketchError, match=message):
        prepare_sketch_three_point_arc(document.Uid, _values(**updates))


def test_three_point_arc_rejects_unbounded_circumcircle(
    three_point_arc_host,
) -> None:
    document, _sketch, _context = three_point_arc_host

    with pytest.raises(NativeSketchError, match="within"):
        prepare_sketch_three_point_arc(
            document.Uid,
            _values(rim_point_mm={"x": 5.0, "y": 1.0e-6}),
        )


def test_three_point_arc_verifier_rejects_parameter_drift(
    three_point_arc_host,
) -> None:
    document, sketch, context = three_point_arc_host
    prepared = preflight_sketch_three_point_arc(
        context,
        prepare_sketch_three_point_arc(document.Uid, _values()),
    )
    draft = create_sketch_three_point_arc(document, prepared)
    sketch.Geometry[1].LastParameter = 2.0

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_three_point_arc(document, draft)
