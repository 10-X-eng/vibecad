# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchArc import (
    create_sketch_arc,
    preflight_sketch_arc,
    prepare_sketch_arc,
    verify_sketch_arc,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "center_mm": {"x": 4.0, "y": -2.0},
            "radius_mm": 6.0,
            "start_angle_degrees": 30.0,
            "end_angle_degrees": 150.0,
            **updates,
        }
    )


@pytest.fixture
def arc_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_center_radius_arc_preflight_create_and_verify_exact_result(arc_host) -> None:
    document, sketch, context = arc_host
    prepared = preflight_sketch_arc(
        context,
        prepare_sketch_arc(document.Uid, _values()),
    )

    draft = create_sketch_arc(document, prepared)
    result = verify_sketch_arc(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert draft.created == ()
    assert result["geometry_count"] == 2
    assert result["constraint_count"] == 0
    geometry = result["geometry"]
    assert geometry["index"] == 1
    assert geometry["geometry_id"] == 101
    assert geometry["type_id"] == "Part::GeomArcOfCircle"
    assert geometry["kind"] == "circular_arc"
    assert geometry["construction"] is False
    assert geometry["blocked"] is False
    assert geometry["center_mm"] == [4.0, -2.0, 0.0]
    assert geometry["axis"] == [0.0, 0.0, 1.0]
    assert geometry["radius_mm"] == 6.0
    assert math.isclose(geometry["first_parameter"], math.radians(30.0))
    assert math.isclose(geometry["last_parameter"], math.radians(150.0))
    assert geometry["closed"] is False
    assert set(result) == {
        "sketch",
        "geometry",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
    }


def test_center_radius_arc_accepts_360_as_zero_without_losing_its_sweep(
    arc_host,
) -> None:
    document, _sketch, _context = arc_host

    prepared = prepare_sketch_arc(
        document.Uid,
        _values(start_angle_degrees=270.0, end_angle_degrees=360.0),
    )

    assert prepared.start_angle_degrees == 270.0
    assert prepared.end_angle_degrees == 0.0
    assert prepared.sweep_angle_degrees == 90.0
    assert math.isclose(prepared.last_parameter, math.radians(360.0))


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("radius_mm", 0.0, "greater than"),
        ("radius_mm", 1.0e-10, "greater than"),
        ("radius_mm", True, "finite number"),
        ("radius_mm", float("inf"), "within"),
        ("start_angle_degrees", -1.0, "between 0 and 360"),
        ("end_angle_degrees", -1.0, "between 0 and 360"),
        ("end_angle_degrees", 30.0, "must differ"),
    ),
)
def test_center_radius_arc_rejects_invalid_parameters(
    arc_host,
    field: str,
    value: object,
    message: str,
) -> None:
    document, _sketch, _context = arc_host

    with pytest.raises(NativeSketchError, match=message):
        prepare_sketch_arc(document.Uid, _values(**{field: value}))


@pytest.mark.parametrize(
    ("attribute", "value"),
    (
        ("Radius", 7.0),
        ("FirstParameter", 0.75),
        ("LastParameter", 2.75),
    ),
)
def test_center_radius_arc_verifier_rejects_parameter_drift(
    arc_host,
    attribute: str,
    value: float,
) -> None:
    document, sketch, context = arc_host
    prepared = preflight_sketch_arc(
        context,
        prepare_sketch_arc(document.Uid, _values()),
    )
    draft = create_sketch_arc(document, prepared)
    setattr(sketch.Geometry[1], attribute, value)

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_arc(document, draft)


def test_center_radius_arc_verifier_rejects_endpoint_drift(arc_host) -> None:
    document, sketch, context = arc_host
    prepared = preflight_sketch_arc(
        context,
        prepare_sketch_arc(document.Uid, _values()),
    )
    draft = create_sketch_arc(document, prepared)
    sketch.Geometry[1].EndPoint.x += 1.0

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_arc(document, draft)


def test_center_radius_arc_verifier_rejects_axis_drift(arc_host) -> None:
    document, sketch, context = arc_host
    prepared = preflight_sketch_arc(
        context,
        prepare_sketch_arc(document.Uid, _values()),
    )
    draft = create_sketch_arc(document, prepared)
    sketch.Geometry[1].Axis.z = -1.0

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_arc(document, draft)


def test_center_radius_arc_verifier_rejects_construction_state(arc_host) -> None:
    document, sketch, context = arc_host
    prepared = preflight_sketch_arc(
        context,
        prepare_sketch_arc(document.Uid, _values()),
    )
    draft = create_sketch_arc(document, prepared)
    sketch.GeometryFacadeList[1].Construction = True

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_arc(document, draft)
