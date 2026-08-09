# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchEllipse import (
    create_sketch_ellipse,
    preflight_sketch_ellipse,
    prepare_sketch_ellipse,
    verify_sketch_ellipse,
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
            "major_radius_mm": 8.0,
            "minor_radius_mm": 3.0,
            "rotation_degrees": 30.0,
            **updates,
        }
    )


@pytest.fixture
def host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_ellipse_creates_closed_curve_and_all_human_internal_geometry(host) -> None:
    document, sketch, context = host
    prepared = preflight_sketch_ellipse(
        context,
        prepare_sketch_ellipse(document.Uid, _values()),
    )
    draft = create_sketch_ellipse(document, prepared)
    result = verify_sketch_ellipse(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert result["geometry_count"] == 6
    assert result["constraint_count"] == 4
    geometry = result["geometry"]
    assert geometry["type_id"] == "Part::GeomEllipse"
    assert geometry["kind"] == "ellipse"
    assert geometry["center_mm"] == [2.0, -1.0, 0.0]
    assert geometry["major_radius_mm"] == 8.0
    assert geometry["minor_radius_mm"] == 3.0
    assert geometry["closed"] is True
    assert geometry["construction"] is False
    internal = result["internal_geometries"]
    assert [item["index"] for item in internal] == [2, 3, 4, 5]
    assert [item["internal_type"] for item in internal] == [
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ]
    assert all(item["construction"] is True for item in internal)
    assert [item["references"] for item in result["internal_constraints"]] == [
        [
            {"slot": 1, "geometry_index": 2},
            {"slot": 2, "geometry_index": 1},
        ],
        [
            {"slot": 1, "geometry_index": 3},
            {"slot": 2, "geometry_index": 1},
        ],
        [
            {"slot": 1, "geometry_index": 4, "position": 1},
            {"slot": 2, "geometry_index": 1},
        ],
        [
            {"slot": 1, "geometry_index": 5, "position": 1},
            {"slot": 2, "geometry_index": 1},
        ],
    ]


@pytest.mark.parametrize(
    "updates",
    (
        {"major_radius_mm": 0.0},
        {"minor_radius_mm": 0.0},
        {"minor_radius_mm": 8.0},
        {"minor_radius_mm": 9.0},
        {"rotation_degrees": 360.0},
    ),
)
def test_ellipse_rejects_invalid_definition(host, updates) -> None:
    document, _sketch, _context = host
    with pytest.raises(NativeSketchError):
        prepare_sketch_ellipse(document.Uid, _values(**updates))


def test_ellipse_verifier_rejects_internal_geometry_drift(host) -> None:
    document, sketch, context = host
    prepared = preflight_sketch_ellipse(
        context,
        prepare_sketch_ellipse(document.Uid, _values()),
    )
    draft = create_sketch_ellipse(document, prepared)
    sketch.Geometry[2].StartPoint.x += 0.25
    with pytest.raises(NativeSketchError, match="internal geometry changed"):
        verify_sketch_ellipse(document, draft)


def test_ellipse_verifier_rejects_radius_drift(host) -> None:
    document, sketch, context = host
    prepared = preflight_sketch_ellipse(
        context,
        prepare_sketch_ellipse(document.Uid, _values()),
    )
    draft = create_sketch_ellipse(document, prepared)
    sketch.Geometry[1].MajorRadius += 0.25
    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_ellipse(document, draft)
