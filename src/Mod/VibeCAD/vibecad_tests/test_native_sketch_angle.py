# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from VibeCADNativeSketchAngle import (
    create_sketch_angle,
    preflight_sketch_angle,
    prepare_sketch_angle,
    verify_sketch_angle,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeArc,
    FakeBSpline,
    FakeCircle,
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


def _line(angle_degrees: float, length: float = 5.0) -> FakeLine:
    angle = math.radians(angle_degrees)
    return FakeLine(
        _point(0.0, 0.0),
        _point(length * math.cos(angle), length * math.sin(angle)),
    )


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "selection": [_element(0, "whole")],
            "expected_form": "line_orientation",
            "dimension": {"value": 30.0, "unit": "deg"},
            "driving": True,
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_angle(
        context,
        prepare_sketch_angle(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_angle(
        document,
        create_sketch_angle(document, prepared),
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


def test_angle_creates_exact_driving_line_orientation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)

    result = _apply(document, _prepared(document, context, _values()))

    assert result["operation"] == "constrain_angle"
    assert result["target_form"] == "line_orientation"
    assert result["measured_before"] == {"value": 0.0, "unit": "deg"}
    assert math.isclose(result["measured_after"]["value"], 30.0)
    constraint = dict(result["constraint"])
    value = constraint.pop("value")
    assert math.isclose(value, math.pi / 6.0)
    assert constraint == {
        "index": 0,
        "type": "Angle",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [{"slot": 1, "geometry_index": 0}],
        "label_distance": 10.0,
        "label_position": 0.0,
    }
    assert math.isclose(sketch.Geometry[0].EndPoint.x, 5.0 * math.sqrt(3.0) / 2.0)
    assert math.isclose(sketch.Geometry[0].EndPoint.y, 2.5)


def test_angle_preserves_signed_line_orientation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = _line(20.0)
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(dimension={"value": -45.0, "unit": "deg"}),
        ),
    )

    assert math.isclose(result["measured_before"]["value"], 20.0)
    assert math.isclose(result["measured_after"]["value"], -45.0)
    assert math.isclose(sketch.Geometry[0].EndPoint.y, -5.0 / math.sqrt(2.0))


def test_angle_creates_exact_driving_circular_arc_span(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = FakeCircle(_point(2.0, 3.0), _point(0.0, 0.0), 4.0)
    arc = sketch.addGeometry(FakeArc(circle, 0.25, 0.25 + math.pi / 2.0), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(arc, "whole")],
            expected_form="circular_arc_span",
            dimension={"value": 120.0, "unit": "deg"},
        ),
    )

    result = _apply(document, prepared)

    assert result["target_form"] == "circular_arc_span"
    assert math.isclose(result["measured_before"]["value"], 90.0)
    assert math.isclose(result["measured_after"]["value"], 120.0)
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": arc}
    ]
    assert math.isclose(
        sketch.Geometry[arc].LastParameter - sketch.Geometry[arc].FirstParameter,
        math.radians(120.0),
    )


def test_angle_creates_exact_directed_line_line_angle(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line(60.0), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(0, "start"), _element(second, "start")],
            expected_form="line_line",
            dimension={"value": 45.0, "unit": "deg"},
        ),
    )

    result = _apply(document, prepared)

    assert result["target_form"] == "line_line"
    assert math.isclose(result["measured_before"]["value"], 60.0)
    assert math.isclose(result["measured_after"]["value"], 45.0)
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0, "position": 1},
        {"slot": 2, "geometry_index": second, "position": 1},
    ]


def test_angle_line_line_normalizes_negative_branch_by_swapping_rays(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = sketch.addGeometry(_line(60.0), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(first, "start"), _element(0, "start")],
            expected_form="line_line",
            dimension={"value": 60.0, "unit": "deg"},
            driving=False,
        ),
    )

    result = _apply(document, prepared)

    assert result["constraint"]["driving"] is False
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0, "position": 1},
        {"slot": 2, "geometry_index": first, "position": 1},
    ]
    assert result["measured_before"] == result["measured_after"]


def test_angle_line_line_accepts_explicit_whole_axis_ray(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    line = sketch.addGeometry(_line(60.0), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(-1, "whole"), _element(line, "start")],
            expected_form="line_line",
            dimension={"value": 30.0, "unit": "deg"},
        ),
    )

    result = _apply(document, prepared)

    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": -1, "position": 1},
        {"slot": 2, "geometry_index": line, "position": 1},
    ]
    assert math.isclose(result["measured_before"]["value"], 60.0)
    assert math.isclose(result["measured_after"]["value"], 30.0)


def test_angle_creates_exact_driving_angle_via_existing_point(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line(60.0), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[
                _element(0, "whole"),
                _element(second, "whole"),
                _element(0, "start"),
            ],
            expected_form="via_point",
            dimension={"value": 40.0, "unit": "deg"},
        ),
    )

    result = _apply(document, prepared)

    assert result["target_form"] == "via_point"
    assert math.isclose(result["measured_before"]["value"], 60.0)
    assert math.isclose(result["measured_after"]["value"], 40.0)
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0},
        {"slot": 2, "geometry_index": second},
        {"slot": 3, "geometry_index": 0, "position": 1},
    ]


def test_angle_via_point_normalizes_negative_branch(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = sketch.addGeometry(_line(60.0), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[
                _element(first, "whole"),
                _element(0, "whole"),
                _element(first, "start"),
            ],
            expected_form="via_point",
            dimension={"value": 60.0, "unit": "deg"},
            driving=False,
        ),
    )

    result = _apply(document, prepared)

    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0},
        {"slot": 2, "geometry_index": first},
        {"slot": 3, "geometry_index": first, "position": 1},
    ]
    assert result["measured_before"] == result["measured_after"]


def test_angle_accepts_exact_external_line_orientation_reference(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.ExternalGeo.append(FakeExternalLine("Support.Edge1"))

    prepared = _prepared(
        document,
        context,
        _values(
            expected_external_geometry_count=1,
            selection=[_element(-3, "whole")],
            dimension={"value": 0.0, "unit": "deg"},
            driving=False,
        ),
    )

    result = _apply(document, prepared)
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": -3}
    ]
    assert result["measured_before"] == result["measured_after"]


@pytest.mark.parametrize(
    ("expected_form", "selection", "geometry", "message"),
    (
        ("line_orientation", [_element(-1, "whole")], None, "non-axis line"),
        ("line_orientation", [_element(0, "start")], None, "one whole"),
        (
            "line_orientation",
            [_element(1, "whole")],
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 3.0),
            "straight line",
        ),
        ("circular_arc_span", [_element(0, "whole")], None, "circular arc"),
        (
            "circular_arc_span",
            [_element(1, "whole")],
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 3.0),
            "circular arc",
        ),
        (
            "line_line",
            [_element(0, "whole"), _element(1, "start")],
            _line(60.0),
            "start or end",
        ),
        (
            "line_line",
            [_element(0, "start"), _element(1, "center")],
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 3.0),
            "start or end",
        ),
        (
            "line_line",
            [_element(0, "start"), _element(1, "start")],
            _line(0.0),
            "parallel or collinear",
        ),
    ),
)
def test_angle_refuses_wrong_geometry_or_position(
    monkeypatch,
    expected_form,
    selection,
    geometry,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    if geometry is not None:
        sketch.addGeometry(geometry, False)
    values = _values(
        expected_geometry_count=int(sketch.GeometryCount),
        selection=selection,
        expected_form=expected_form,
        dimension={"value": 60.0, "unit": "deg"},
    )
    with pytest.raises(NativeSketchError, match=message):
        _prepared(document, context, values)


def test_angle_refuses_zero_length_line(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = FakeLine(_point(1.0, 1.0), _point(1.0, 1.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    with pytest.raises(NativeSketchError, match="zero-length"):
        _prepared(document, context, _values())


def test_angle_refuses_degenerate_circular_arc(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 3.0)
    arc = sketch.addGeometry(FakeArc(circle, 0.5, 0.5), False)
    with pytest.raises(NativeSketchError, match="degenerate"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[_element(arc, "whole")],
                expected_form="circular_arc_span",
                dimension={"value": 90.0, "unit": "deg"},
            ),
        )


@pytest.mark.parametrize(
    ("selection", "message"),
    (
        (
            [_element(0, "whole"), _element(1, "whole"), _element(2, "start")],
            "point to lie on both curves",
        ),
        (
            [_element(0, "start"), _element(1, "whole"), _element(0, "end")],
            "curves must use whole",
        ),
        (
            [_element(0, "whole"), _element(1, "whole"), _element(2, "whole")],
            "exact curve point",
        ),
    ),
)
def test_angle_via_point_refuses_implicit_or_inexact_topology(
    monkeypatch,
    selection,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(_line(60.0), False)
    sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                selection=selection,
                expected_form="via_point",
                dimension={"value": 40.0, "unit": "deg"},
            ),
        )


def test_angle_via_point_refuses_point_geometry_as_curve(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(0.0, 0.0)), False)
    with pytest.raises(NativeSketchError, match="does not support"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[
                    _element(point, "whole"),
                    _element(0, "whole"),
                    _element(point, "start"),
                ],
                expected_form="via_point",
                dimension={"value": 40.0, "unit": "deg"},
            ),
        )


@pytest.mark.parametrize(
    ("attribute", "message"),
    (
        ("isPointOnCurve", "point-on-curve query is unavailable"),
        ("calculateAngleViaPoint", "via-point measurement is unavailable"),
    ),
)
def test_angle_via_point_refuses_missing_host_queries(
    monkeypatch,
    attribute,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line(60.0), False)
    monkeypatch.setattr(sketch, attribute, None)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[
                    _element(0, "whole"),
                    _element(second, "whole"),
                    _element(0, "start"),
                ],
                expected_form="via_point",
                dimension={"value": 40.0, "unit": "deg"},
            ),
        )


@pytest.mark.parametrize(
    ("expected_form", "value"),
    (
        ("line_orientation", -180.000001),
        ("line_orientation", 180.000001),
        ("circular_arc_span", 0.0),
        ("circular_arc_span", 360.0),
        ("line_line", 0.0),
        ("line_line", 180.0),
        ("via_point", 0.0),
        ("via_point", 180.0),
    ),
)
def test_angle_refuses_form_specific_value_ranges(
    monkeypatch,
    expected_form,
    value,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line(60.0), False)
    if expected_form == "line_orientation":
        selection = [_element(0, "whole")]
    elif expected_form == "circular_arc_span":
        circle = FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 3.0)
        arc = sketch.addGeometry(FakeArc(circle, 0.0, math.pi / 2.0), False)
        selection = [_element(arc, "whole")]
    elif expected_form == "line_line":
        selection = [_element(0, "start"), _element(second, "start")]
    else:
        selection = [
            _element(0, "whole"),
            _element(second, "whole"),
            _element(0, "start"),
        ]
    with pytest.raises(NativeSketchError, match="must be"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=int(sketch.GeometryCount),
                selection=selection,
                expected_form=expected_form,
                dimension={"value": value, "unit": "deg"},
            ),
        )


def test_angle_reference_requires_exact_current_measurement(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line(60.0), False)
    with pytest.raises(NativeSketchError, match="reference measurement changed"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[_element(0, "start"), _element(second, "start")],
                expected_form="line_line",
                dimension={"value": 50.0, "unit": "deg"},
                driving=False,
            ),
        )


def test_angle_refuses_bspline_internal_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    geometry = sketch.addGeometry(FakeBSpline(), False)
    sketch.GeometryFacadeList[geometry].InternalType = "BSplineControlPoint"
    with pytest.raises(NativeSketchError, match="internal-alignment geometry"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[_element(geometry, "whole")],
            ),
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"selection": []},
        {"selection": [_element(0, "whole"), _element(1, "whole")]},
        {"selection": [_element(-2000, "whole")]},
        {"selection": [_element(0, "bad")]},
        {"expected_form": "supplementary"},
        {"dimension": {"value": 30.0, "unit": "rad"}},
        {"dimension": {"value": float("inf"), "unit": "deg"}},
        {"driving": 1},
        {"unexpected": True},
    ),
)
def test_angle_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_angle(document.Uid, _values(**updates))


def test_angle_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = _rejected_feasibility()

    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(document, context, _values())

    assert sketch.ConstraintCount == 0
    assert sketch.Geometry[0].EndPoint.x == 5.0
    assert sketch.Geometry[0].EndPoint.y == 0.0


def test_angle_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Angle preflight"):
        create_sketch_angle(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, _values())
    draft = create_sketch_angle(document, prepared)
    sketch.RedundantConstraints.append(0)
    with pytest.raises(NativeSketchError, match="solver conflict or redundancy"):
        verify_sketch_angle(document, draft)
