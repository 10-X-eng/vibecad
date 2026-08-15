# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchThreePointEllipse import (
    create_sketch_three_point_ellipse,
    preflight_sketch_three_point_ellipse,
    prepare_sketch_three_point_ellipse,
    verify_sketch_three_point_ellipse,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "first_axis_endpoint_mm": {"x": -8.0, "y": 0.0},
            "second_axis_endpoint_mm": {"x": 8.0, "y": 0.0},
            "rim_point_mm": {"x": 0.0, "y": 3.0},
            **updates,
        }
    )


@pytest.fixture
def host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_three_point_ellipse_creates_exact_human_geometry(host) -> None:
    document, sketch, context = host
    prepared = preflight_sketch_three_point_ellipse(
        context,
        prepare_sketch_three_point_ellipse(document.Uid, _values()),
    )
    draft = create_sketch_three_point_ellipse(document, prepared)
    result = verify_sketch_three_point_ellipse(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert result["geometry_count"] == 6
    assert result["constraint_count"] == 4
    geometry = result["geometry"]
    assert geometry["type_id"] == "Part::GeomEllipse"
    assert geometry["center_mm"] == [0.0, 0.0, 0.0]
    assert geometry["major_radius_mm"] == 8.0
    assert geometry["minor_radius_mm"] == 3.0
    assert geometry["x_axis"] == [1.0, 0.0, 0.0]
    assert [item["internal_type"] for item in result["internal_geometries"]] == [
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ]


def test_three_point_ellipse_promotes_derived_axis_to_major(host) -> None:
    document, _sketch, _context = host
    spec = prepare_sketch_three_point_ellipse(
        document.Uid,
        _values(rim_point_mm={"x": 0.0, "y": 12.0}),
    )
    assert spec.major_radius_mm == 12.0
    assert spec.minor_radius_mm == 8.0
    assert spec.major_axis == (0.0, 1.0)


@pytest.mark.parametrize(
    "updates",
    (
        {"second_axis_endpoint_mm": {"x": -8.0, "y": 0.0}},
        {"rim_point_mm": {"x": 0.0, "y": 0.0}},
        {"rim_point_mm": {"x": 8.0, "y": 3.0}},
        {"rim_point_mm": {"x": 0.0, "y": 8.0}},
    ),
)
def test_three_point_ellipse_rejects_degenerate_or_circle_result(host, updates) -> None:
    document, _sketch, _context = host
    with pytest.raises(NativeSketchError):
        prepare_sketch_three_point_ellipse(document.Uid, _values(**updates))


def test_three_point_ellipse_verifier_rejects_internal_drift(host) -> None:
    document, sketch, context = host
    prepared = preflight_sketch_three_point_ellipse(
        context,
        prepare_sketch_three_point_ellipse(document.Uid, _values()),
    )
    draft = create_sketch_three_point_ellipse(document, prepared)
    sketch.Geometry[2].EndPoint.x += 0.25
    with pytest.raises(NativeSketchError, match="internal geometry changed"):
        verify_sketch_three_point_ellipse(document, draft)
