# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchEllipticalArc import (
    create_sketch_elliptical_arc,
    preflight_sketch_elliptical_arc,
    prepare_sketch_elliptical_arc,
    verify_sketch_elliptical_arc,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "center_mm": {"x": 3.0, "y": -2.0},
            "major_radius_mm": 10.0,
            "minor_radius_mm": 4.0,
            "rotation_degrees": 30.0,
            "start_parameter_degrees": 20.0,
            "sweep_parameter_degrees": 130.0,
            **updates,
        }
    )


@pytest.fixture
def elliptical_arc_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_elliptical_arc_creates_curve_and_all_human_internal_geometry(
    elliptical_arc_host,
) -> None:
    document, sketch, context = elliptical_arc_host
    spec = prepare_sketch_elliptical_arc(document.Uid, _values())
    prepared = preflight_sketch_elliptical_arc(context, spec)

    draft = create_sketch_elliptical_arc(document, prepared)
    result = verify_sketch_elliptical_arc(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert result["geometry_count"] == 6
    assert result["constraint_count"] == 4
    geometry = result["geometry"]
    assert geometry["index"] == 1
    assert geometry["type_id"] == "Part::GeomArcOfEllipse"
    assert geometry["kind"] == "elliptical_arc"
    assert geometry["construction"] is False
    assert geometry["center_mm"] == [3.0, -2.0, 0.0]
    assert geometry["major_radius_mm"] == 10.0
    assert geometry["minor_radius_mm"] == 4.0
    assert math.isclose(geometry["first_parameter"], math.radians(20.0))
    assert math.isclose(geometry["last_parameter"], math.radians(150.0))
    internal = result["internal_geometries"]
    assert [item["index"] for item in internal] == [2, 3, 4, 5]
    assert [item["internal_type"] for item in internal] == [
        "EllipseMajorDiameter",
        "EllipseMinorDiameter",
        "EllipseFocus1",
        "EllipseFocus2",
    ]
    assert [item["kind"] for item in internal] == ["line", "line", "point", "point"]
    assert all(item["construction"] is True for item in internal)
    constraints = result["internal_constraints"]
    assert [item["index"] for item in constraints] == [0, 1, 2, 3]
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
        [
            {"slot": 1, "geometry_index": 5, "position": 1},
            {"slot": 2, "geometry_index": 1},
        ],
    ]
    assert set(result) == {
        "sketch",
        "geometry",
        "internal_geometries",
        "internal_constraints",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
    }


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        ({"major_radius_mm": 0.0}, "greater than"),
        ({"minor_radius_mm": 1.0e-10}, "greater than"),
        ({"minor_radius_mm": 10.0}, "must be smaller"),
        ({"minor_radius_mm": 12.0}, "must be smaller"),
        ({"start_parameter_degrees": -1.0}, "between 0 and 360"),
        ({"sweep_parameter_degrees": 0.0}, "greater than 0"),
        ({"sweep_parameter_degrees": 360.0}, "below 360"),
    ),
)
def test_elliptical_arc_rejects_degenerate_parameters(
    elliptical_arc_host,
    updates: dict[str, object],
    message: str,
) -> None:
    document, _sketch, _context = elliptical_arc_host

    with pytest.raises(NativeSketchError, match=message):
        prepare_sketch_elliptical_arc(document.Uid, _values(**updates))


def test_elliptical_arc_rejects_exposed_role_drift(
    elliptical_arc_host,
    monkeypatch,
) -> None:
    document, sketch, context = elliptical_arc_host
    prepared = preflight_sketch_elliptical_arc(
        context,
        prepare_sketch_elliptical_arc(document.Uid, _values()),
    )
    original = sketch.exposeInternalGeometry

    def wrong_roles(index: int):
        result = original(index)
        result["created"][0]["role"] = "WrongRole"
        return result

    monkeypatch.setattr(sketch, "exposeInternalGeometry", wrong_roles)
    with pytest.raises(NativeSketchError, match="unexpected ellipse geometry"):
        create_sketch_elliptical_arc(document, prepared)


def test_elliptical_arc_verifier_rejects_internal_geometry_drift(
    elliptical_arc_host,
) -> None:
    document, sketch, context = elliptical_arc_host
    prepared = preflight_sketch_elliptical_arc(
        context,
        prepare_sketch_elliptical_arc(document.Uid, _values()),
    )
    draft = create_sketch_elliptical_arc(document, prepared)
    sketch.Geometry[2].EndPoint.x += 1.0

    with pytest.raises(NativeSketchError, match="internal geometry changed"):
        verify_sketch_elliptical_arc(document, draft)


def test_elliptical_arc_verifier_rejects_internal_constraint_drift(
    elliptical_arc_host,
) -> None:
    document, sketch, context = elliptical_arc_host
    prepared = preflight_sketch_elliptical_arc(
        context,
        prepare_sketch_elliptical_arc(document.Uid, _values()),
    )
    draft = create_sketch_elliptical_arc(document, prepared)
    sketch.Constraints[3].Second = 0

    with pytest.raises(NativeSketchError, match="internal alignment changed"):
        verify_sketch_elliptical_arc(document, draft)
