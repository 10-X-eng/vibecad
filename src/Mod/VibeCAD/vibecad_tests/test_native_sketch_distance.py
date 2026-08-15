# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from VibeCADNativeSketchDistance import (
    create_sketch_distance,
    preflight_sketch_distance,
    prepare_sketch_distance,
    verify_sketch_distance,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeArc,
    FakeCircle,
    FakeConstraint,
    FakeEllipse,
    FakeExternalLine,
    FakeLine,
    FakePoint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "selection": [_element(0, "whole")],
            "dimension": {"value": 8.0, "unit": "mm"},
            "driving": True,
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_distance(
        context,
        prepare_sketch_distance(document.Uid, values),
    )


def _rejected_feasibility(index: int = 0) -> dict[str, object]:
    return {
        "accepted": False,
        "degrees_of_freedom": -1,
        "solver_status": -2,
        "first_proposed_constraint_index": index,
        "proposed_constraint_count": 1,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [index],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
    }


def test_distance_creates_exact_driving_line_length(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())

    result = verify_sketch_distance(
        document,
        create_sketch_distance(document, prepared),
    )

    assert result["operation"] == "constrain_distance"
    assert result["target_form"] == "line_length"
    assert result["measured_before"] == {"value": 5.0, "unit": "mm"}
    assert result["measured_after"] == {"value": 8.0, "unit": "mm"}
    assert result["constraint"] == {
        "index": 0,
        "type": "Distance",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [{"slot": 1, "geometry_index": 0}],
        "value": 8.0,
        "label_distance": 10.0,
        "label_position": 0.0,
    }
    assert sketch.Geometry[0].EndPoint.x == 8.0


def test_distance_creates_exact_point_to_point_distance(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = sketch.addGeometry(FakePoint(_point(0.0, 0.0)), False)
    second = sketch.addGeometry(FakePoint(_point(3.0, 4.0)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=3,
            selection=[_element(first, "start"), _element(second, "start")],
            dimension={"value": 10.0, "unit": "mm"},
        ),
    )

    result = verify_sketch_distance(
        document,
        create_sketch_distance(document, prepared),
    )

    assert result["target_form"] == "point_to_point"
    assert result["measured_before"] == {"value": 5.0, "unit": "mm"}
    assert result["measured_after"] == {"value": 10.0, "unit": "mm"}
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": first, "position": 1},
        {"slot": 2, "geometry_index": second, "position": 1},
    ]
    assert sketch.Geometry[second].X == 6.0
    assert sketch.Geometry[second].Y == 8.0


@pytest.mark.parametrize(
    ("axis", "coordinate", "initial", "requested", "target_form", "kind"),
    (
        (-1, "y", -4.0, -9.0, "horizontal_axis_to_point", "DistanceY"),
        (-2, "x", 3.0, 11.0, "vertical_axis_to_point", "DistanceX"),
    ),
)
def test_distance_preserves_signed_axis_to_point_forms(
    monkeypatch,
    axis,
    coordinate,
    initial,
    requested,
    target_form,
    kind,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    position = _point(3.0, -4.0)
    setattr(position, coordinate, initial)
    point = sketch.addGeometry(FakePoint(position), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(point, "start"), _element(axis, "whole")],
            dimension={"value": requested, "unit": "mm"},
        ),
    )

    result = verify_sketch_distance(
        document,
        create_sketch_distance(document, prepared),
    )

    assert result["target_form"] == target_form
    assert result["constraint"]["type"] == kind
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": axis, "position": 1},
        {"slot": 2, "geometry_index": point, "position": 1},
    ]
    assert result["measured_before"] == {"value": initial, "unit": "mm"}
    assert result["measured_after"] == {"value": requested, "unit": "mm"}


def test_distance_creates_exact_circular_arc_length(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 2.0)
    arc = sketch.addGeometry(FakeArc(circle, 0.0, math.pi / 2.0), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(arc, "whole")],
            dimension={"value": 2.0 * math.pi, "unit": "mm"},
        ),
    )

    result = verify_sketch_distance(
        document,
        create_sketch_distance(document, prepared),
    )

    assert result["target_form"] == "circular_arc_length"
    assert math.isclose(result["measured_before"]["value"], math.pi)
    assert math.isclose(result["measured_after"]["value"], 2.0 * math.pi)
    assert math.isclose(sketch.Geometry[arc].Radius, 4.0)


def test_distance_creates_exact_point_to_line_distance(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(0, "whole"), _element(point, "start")],
            dimension={"value": 6.0, "unit": "mm"},
        ),
    )

    result = verify_sketch_distance(
        document,
        create_sketch_distance(document, prepared),
    )

    assert result["target_form"] == "point_to_line"
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": point, "position": 1},
        {"slot": 2, "geometry_index": 0},
    ]
    assert sketch.Geometry[point].Y == 6.0


def test_distance_creates_exact_point_to_circle_distance(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = sketch.addGeometry(
        FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 4.0),
        False,
    )
    point = sketch.addGeometry(FakePoint(_point(10.0, 0.0)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=3,
            selection=[_element(circle, "whole"), _element(point, "start")],
            dimension={"value": 3.0, "unit": "mm"},
        ),
    )

    result = verify_sketch_distance(
        document,
        create_sketch_distance(document, prepared),
    )

    assert result["target_form"] == "point_to_circle"
    assert result["measured_before"] == {"value": 6.0, "unit": "mm"}
    assert result["measured_after"] == {"value": 3.0, "unit": "mm"}
    assert sketch.Geometry[point].X == 7.0


@pytest.mark.parametrize("curve_kind", ("line", "circle"))
def test_distance_reference_supports_exact_curve_pairs(monkeypatch, curve_kind) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = sketch.addGeometry(
        FakeCircle(_point(0.0, 10.0), _point(0.0, 0.0), 2.0),
        False,
    )
    if curve_kind == "line":
        selection = [_element(0, "whole"), _element(first, "whole")]
        expected_form = "circle_to_line"
        measured = 8.0
    else:
        second = sketch.addGeometry(
            FakeCircle(_point(0.0, 20.0), _point(0.0, 0.0), 3.0),
            False,
        )
        selection = [_element(first, "whole"), _element(second, "whole")]
        expected_form = "circle_to_circle"
        measured = 5.0
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=int(sketch.GeometryCount),
            selection=selection,
            dimension={"value": measured, "unit": "mm"},
            driving=False,
        ),
    )

    result = verify_sketch_distance(
        document,
        create_sketch_distance(document, prepared),
    )

    assert result["target_form"] == expected_form
    assert result["constraint"]["type"] == "Distance"
    assert result["constraint"]["driving"] is False
    assert result["measured_before"] == result["measured_after"]


def test_distance_accepts_exact_external_line_length_reference(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.ExternalGeo.append(FakeExternalLine("Support.Edge1"))
    prepared = _prepared(
        document,
        context,
        _values(
            expected_external_geometry_count=1,
            selection=[_element(-3, "whole")],
            dimension={"value": 5.0, "unit": "mm"},
            driving=False,
        ),
    )

    assert prepared.resolved.target_form == "line_length"
    assert prepared.spec.driving is False


@pytest.mark.parametrize(
    ("selection", "geometry", "message"),
    (
        ([_element(1, "start")], FakePoint(_point(2.0, 3.0)), "requires two points"),
        (
            [_element(1, "whole")],
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 3.0),
            "needs a second curve.*Radius or Diameter",
        ),
        ([_element(-1, "whole")], None, "axis length"),
        (
            [_element(1, "whole")],
            FakeEllipse(_point(0.0, 0.0), 4.0, 2.0),
            "does not support whole",
        ),
        (
            [_element(0, "whole"), _element(1, "whole")],
            FakeLine(_point(0.0, 4.0), _point(5.0, 4.0)),
            "does not constrain two whole lines",
        ),
        (
            [_element(1, "start"), _element(2, "start")],
            (FakePoint(_point(1.0, 1.0)), FakePoint(_point(1.0, 1.0))),
            "points are coincident",
        ),
        (
            [_element(1, "start"), _element(0, "whole")],
            FakePoint(_point(2.0, 0.0)),
            "point lies on the line",
        ),
    ),
)
def test_distance_refuses_incomplete_degenerate_and_unsupported_targets(
    monkeypatch,
    selection,
    geometry,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    geometries = geometry if isinstance(geometry, tuple) else (() if geometry is None else (geometry,))
    for item in geometries:
        sketch.addGeometry(item, False)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=int(sketch.GeometryCount),
                selection=selection,
            ),
        )


def test_distance_refuses_point_on_circle_and_tangent_curve_pairs(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = sketch.addGeometry(
        FakeCircle(_point(0.0, 5.0), _point(0.0, 0.0), 5.0),
        False,
    )
    point = sketch.addGeometry(FakePoint(_point(0.0, 10.0)), False)
    with pytest.raises(NativeSketchError, match="point lies on the circle"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                selection=[_element(point, "start"), _element(circle, "whole")],
            ),
        )
    with pytest.raises(NativeSketchError, match="circle and line are tangent"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                selection=[_element(circle, "whole"), _element(0, "whole")],
            ),
        )
    second = sketch.addGeometry(
        FakeCircle(_point(0.0, 12.0), _point(0.0, 0.0), 2.0),
        False,
    )
    with pytest.raises(NativeSketchError, match="circles are tangent"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=4,
                selection=[_element(circle, "whole"), _element(second, "whole")],
            ),
        )


def test_distance_refuses_unsupported_intersecting_curve_pairs(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = sketch.addGeometry(
        FakeCircle(_point(0.0, 1.0), _point(0.0, 0.0), 2.0),
        False,
    )
    with pytest.raises(NativeSketchError, match="circle and line intersect"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[_element(circle, "whole"), _element(0, "whole")],
            ),
        )

    second = sketch.addGeometry(
        FakeCircle(_point(3.0, 1.0), _point(0.0, 0.0), 2.0),
        False,
    )
    with pytest.raises(NativeSketchError, match="circles intersect"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                selection=[_element(circle, "whole"), _element(second, "whole")],
            ),
        )


def test_distance_refuses_stale_reference_and_nonpositive_nonaxis_value(
    monkeypatch,
) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="measurement changed"):
        _prepared(
            document,
            context,
            _values(dimension={"value": 4.0, "unit": "mm"}, driving=False),
        )
    with pytest.raises(NativeSketchError, match="must be greater than zero"):
        _prepared(
            document,
            context,
            _values(dimension={"value": 0.0, "unit": "mm"}),
        )
    with pytest.raises(NativeSketchError, match="must be greater than zero"):
        _prepared(
            document,
            context,
            _values(dimension={"value": -3.0, "unit": "mm"}),
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"selection": []},
        {"selection": [_element(0, "whole")] * 2},
        {"selection": [_element(-2000, "whole")]},
        {"selection": [_element(0, "bad")]},
        {"dimension": {"value": 5.0, "unit": "deg"}},
        {"dimension": {"value": 1_000_001.0, "unit": "mm"}},
        {"driving": 1},
        {"unexpected": True},
    ),
)
def test_distance_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_distance(document.Uid, _values(**updates))


def test_distance_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = _rejected_feasibility()

    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(document, context, _values())

    assert sketch.ConstraintCount == 0
    assert sketch.Geometry[0].EndPoint.x == 5.0


def test_distance_refuses_group_member_and_lifecycle_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    member = sketch.addGeometry(FakeLine(_point(0.0, 2.0), _point(5.0, 2.0)), False)
    sketch.addConstraint(FakeConstraint("Text", [0, 0, member, 0], "A", "Font", True))
    with pytest.raises(NativeSketchError, match="group handle 0"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                expected_constraint_count=1,
                selection=[_element(member, "whole")],
            ),
        )

    sketch.Constraints.clear()
    sketch.ConstraintCount = 0
    values = _values(expected_geometry_count=2)
    prepared = _prepared(document, context, values)
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Distance preflight"):
        create_sketch_distance(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, values)
    draft = create_sketch_distance(document, prepared)
    sketch.RedundantConstraints.append(0)
    with pytest.raises(NativeSketchError, match="solver conflict or redundancy"):
        verify_sketch_distance(document, draft)
