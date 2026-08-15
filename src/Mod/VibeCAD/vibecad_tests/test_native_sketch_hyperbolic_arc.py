# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchHyperbolicArc import (
    create_sketch_hyperbolic_arc,
    preflight_sketch_hyperbolic_arc,
    prepare_sketch_hyperbolic_arc,
    verify_sketch_hyperbolic_arc,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "center_mm": {"x": 2.0, "y": -1.0},
            "major_radius_mm": 5.0,
            "minor_radius_mm": 3.0,
            "rotation_degrees": 15.0,
            "start_parameter": -1.0,
            "end_parameter": 1.0,
            **updates,
        }
    )


@pytest.fixture
def hyperbolic_arc_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_hyperbolic_arc_creates_curve_and_all_human_internal_geometry(
    hyperbolic_arc_host,
) -> None:
    document, sketch, context = hyperbolic_arc_host
    prepared = preflight_sketch_hyperbolic_arc(
        context,
        prepare_sketch_hyperbolic_arc(document.Uid, _values()),
    )

    draft = create_sketch_hyperbolic_arc(document, prepared)
    result = verify_sketch_hyperbolic_arc(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert result["geometry_count"] == 5
    assert result["constraint_count"] == 3
    geometry = result["geometry"]
    assert geometry["index"] == 1
    assert geometry["type_id"] == "Part::GeomArcOfHyperbola"
    assert geometry["kind"] == "hyperbolic_arc"
    assert geometry["construction"] is False
    assert geometry["center_mm"] == [2.0, -1.0, 0.0]
    assert geometry["major_radius_mm"] == 5.0
    assert geometry["minor_radius_mm"] == 3.0
    assert geometry["first_parameter"] == -1.0
    assert geometry["last_parameter"] == 1.0
    internal = result["internal_geometries"]
    assert [item["index"] for item in internal] == [2, 3, 4]
    assert [item["internal_type"] for item in internal] == [
        "HyperbolaMajor",
        "HyperbolaMinor",
        "HyperbolaFocus",
    ]
    assert [item["kind"] for item in internal] == ["line", "line", "point"]
    assert all(item["construction"] is True for item in internal)
    constraints = result["internal_constraints"]
    assert [item["references"] for item in constraints] == [
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
    ]


def test_hyperbolic_arc_allows_major_coefficient_below_minor(
    hyperbolic_arc_host,
) -> None:
    document, _sketch, context = hyperbolic_arc_host
    prepared = preflight_sketch_hyperbolic_arc(
        context,
        prepare_sketch_hyperbolic_arc(
            document.Uid,
            _values(major_radius_mm=3.0, minor_radius_mm=5.0),
        ),
    )

    result = verify_sketch_hyperbolic_arc(
        document,
        create_sketch_hyperbolic_arc(document, prepared),
    )

    assert result["geometry"]["major_radius_mm"] == 3.0
    assert result["geometry"]["minor_radius_mm"] == 5.0


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        ({"major_radius_mm": 0.0}, "greater than"),
        ({"minor_radius_mm": 1.0e-10}, "greater than"),
        ({"rotation_degrees": 360.0}, "below 360"),
        ({"start_parameter": -20.1}, "within"),
        ({"end_parameter": 20.1}, "within"),
        ({"start_parameter": 1.0, "end_parameter": 1.0}, "must be greater"),
        ({"start_parameter": 2.0, "end_parameter": 1.0}, "must be greater"),
    ),
)
def test_hyperbolic_arc_rejects_degenerate_parameters(
    hyperbolic_arc_host,
    updates: dict[str, object],
    message: str,
) -> None:
    document, _sketch, _context = hyperbolic_arc_host

    with pytest.raises(NativeSketchError, match=message):
        prepare_sketch_hyperbolic_arc(document.Uid, _values(**updates))


def test_hyperbolic_arc_rejects_unbounded_endpoint(hyperbolic_arc_host) -> None:
    document, _sketch, _context = hyperbolic_arc_host

    with pytest.raises(NativeSketchError, match="within"):
        prepare_sketch_hyperbolic_arc(
            document.Uid,
            _values(major_radius_mm=1_000_000.0, end_parameter=20.0),
        )


def test_hyperbolic_arc_verifier_rejects_internal_geometry_drift(
    hyperbolic_arc_host,
) -> None:
    document, sketch, context = hyperbolic_arc_host
    prepared = preflight_sketch_hyperbolic_arc(
        context,
        prepare_sketch_hyperbolic_arc(document.Uid, _values()),
    )
    draft = create_sketch_hyperbolic_arc(document, prepared)
    sketch.Geometry[3].StartPoint.y += 1.0

    with pytest.raises(NativeSketchError, match="internal geometry changed"):
        verify_sketch_hyperbolic_arc(document, draft)


def test_hyperbolic_arc_verifier_rejects_parameter_drift(
    hyperbolic_arc_host,
) -> None:
    document, sketch, context = hyperbolic_arc_host
    prepared = preflight_sketch_hyperbolic_arc(
        context,
        prepare_sketch_hyperbolic_arc(document.Uid, _values()),
    )
    draft = create_sketch_hyperbolic_arc(document, prepared)
    sketch.Geometry[1].LastParameter = 1.5

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_hyperbolic_arc(document, draft)
