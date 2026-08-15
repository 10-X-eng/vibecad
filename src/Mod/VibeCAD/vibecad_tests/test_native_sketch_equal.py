# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import VibeCADNativeSketchConstraintRuntime as runtime_module
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
from VibeCADNativeSketchEqual import (
    create_sketch_equal,
    preflight_sketch_equal,
    prepare_sketch_equal,
    verify_sketch_equal,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeArc,
    FakeBSpline,
    FakeCircle,
    FakeConstraint,
    FakeEllipse,
    FakeEllipticalArc,
    FakeExternalLine,
    FakeHyperbola,
    FakeHyperbolicArc,
    FakeLine,
    FakeParabola,
    FakeParabolicArc,
    FakePoint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


def _element(index: int, position: str = "whole") -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _values(selection=None, **updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "selection": selection or [_element(0), _element(1)],
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_equal(
        context,
        prepare_sketch_equal(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_equal(
        document,
        create_sketch_equal(document, prepared),
    )


def _line(start, end) -> FakeLine:
    return FakeLine(_point(*start), _point(*end))


def _circle(center, radius: float) -> FakeCircle:
    return FakeCircle(_point(*center), _point(0.0, 0.0), radius)


def _constraint(index: int, first: int, second: int) -> dict[str, object]:
    return {
        "index": index,
        "type": "Equal",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": first},
            {"slot": 2, "geometry_index": second},
        ],
    }


def test_equal_constrains_one_atomic_ordered_line_chain(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = _line((0.0, 0.0), (3.0, 0.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    first_added = sketch.addGeometry(_line((0.0, 5.0), (7.0, 5.0)), False)
    second_added = sketch.addGeometry(_line((0.0, 9.0), (11.0, 9.0)), False)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(0), _element(first_added), _element(second_added)],
                expected_geometry_count=3,
            ),
        ),
    )

    assert result["operation"] == "constrain_equal"
    assert result["family"] == "line_length"
    assert result["constraints"] == [
        _constraint(0, 0, first_added),
        _constraint(1, first_added, second_added),
    ]
    assert result["measured_before"] == {
        "maximum_error": 4.0,
        "unit": "mm",
        "pairs": [
            {
                "first_geometry_index": 0,
                "second_geometry_index": 1,
                "errors": {"length": 4.0},
            },
            {
                "first_geometry_index": 1,
                "second_geometry_index": 2,
                "errors": {"length": 4.0},
            },
        ],
    }
    assert result["measured_after"]["maximum_error"] == 0.0
    assert math.isclose(sketch.Geometry[1].EndPoint.x, 3.0)
    assert math.isclose(sketch.Geometry[2].EndPoint.x, 3.0)


def test_equal_constrains_mixed_circle_and_circular_arc_radii(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = sketch.addGeometry(_circle((0.0, 0.0), 2.0), False)
    arc = sketch.addGeometry(
        FakeArc(_circle((10.0, 0.0), 5.0), 0.2, 2.1),
        False,
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(circle), _element(arc)],
                expected_geometry_count=3,
            ),
        ),
    )

    assert result["family"] == "circular_radius"
    assert result["measured_before"]["pairs"][0]["errors"] == {"radius": 3.0}
    assert result["measured_after"]["maximum_error"] == 0.0
    assert sketch.Geometry[arc].Radius == 2.0


def test_equal_constrains_ellipse_and_elliptical_arc_radii(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    ellipse = sketch.addGeometry(FakeEllipse(_point(0.0, 0.0), 8.0, 3.0), False)
    arc = sketch.addGeometry(
        FakeEllipticalArc(FakeEllipse(_point(20.0, 0.0), 5.0, 2.0), 0.2, 2.1),
        False,
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(ellipse), _element(arc)],
                expected_geometry_count=3,
            ),
        ),
    )

    assert result["family"] == "elliptic_radii"
    assert result["measured_before"]["pairs"][0]["errors"] == {
        "major_radius": 3.0,
        "minor_radius": 1.0,
    }
    assert result["measured_after"]["maximum_error"] == 0.0
    assert sketch.Geometry[arc].MajorRadius == 8.0
    assert sketch.Geometry[arc].MinorRadius == 3.0


def test_equal_constrains_hyperbolic_arc_radii(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = sketch.addGeometry(
        FakeHyperbolicArc(FakeHyperbola(_point(0.0, 0.0), 7.0, 3.0), -1.0, 1.0),
        False,
    )
    second = sketch.addGeometry(
        FakeHyperbolicArc(FakeHyperbola(_point(20.0, 0.0), 4.0, 2.0), -0.8, 0.9),
        False,
    )
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(first), _element(second)],
                expected_geometry_count=3,
            ),
        ),
    )
    assert result["family"] == "hyperbolic_radii"
    assert result["measured_before"]["pairs"][0]["errors"] == {
        "major_radius": 3.0,
        "minor_radius": 1.0,
    }
    assert result["measured_after"]["maximum_error"] == 0.0


def test_equal_constrains_parabolic_focal_length(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = sketch.addGeometry(
        FakeParabolicArc(
            FakeParabola(_point(3.0, 0.0), _point(0.0, 0.0), None),
            -3.0,
            3.0,
        ),
        False,
    )
    second = sketch.addGeometry(
        FakeParabolicArc(
            FakeParabola(_point(15.0, 0.0), _point(10.0, 0.0), None),
            -4.0,
            4.0,
        ),
        False,
    )
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(first), _element(second)],
                expected_geometry_count=3,
            ),
        ),
    )
    assert result["family"] == "parabolic_focal_length"
    assert result["measured_before"]["pairs"][0]["errors"] == {"focal_length": 2.0}
    assert result["measured_after"]["maximum_error"] == 0.0


def _add_weight_handles(sketch) -> tuple[int, int, int]:
    first = sketch.addGeometry(_circle((0.0, 0.0), 1.0), True)
    second = sketch.addGeometry(_circle((15.0, 5.0), 2.0), True)
    spline = sketch.addGeometry(
        FakeBSpline(
            poles=[_point(0.0, 0.0), _point(15.0, 5.0)],
            multiplicities=[2, 2],
            knots=[0.0, 1.0],
            degree=1,
            weights=[1.0, 2.0],
        ),
        False,
    )
    sketch.addConstraint(
        FakeConstraint(
            "InternalAlignment:Sketcher::BSplineControlPoint",
            first,
            3,
            spline,
            0,
        )
    )
    sketch.addConstraint(
        FakeConstraint(
            "InternalAlignment:Sketcher::BSplineControlPoint",
            second,
            3,
            spline,
            1,
        )
    )
    return first, second, spline


def test_equal_constrains_exact_bspline_pole_weights(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first, second, spline = _add_weight_handles(sketch)
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(first), _element(second)],
                expected_geometry_count=4,
                expected_constraint_count=2,
            ),
        ),
    )
    assert result["family"] == "b_spline_weight"
    assert result["measured_before"] == {
        "maximum_error": 1.0,
        "unit": "unitless",
        "pairs": [
            {
                "first_geometry_index": first,
                "second_geometry_index": second,
                "errors": {"weight": 1.0},
            }
        ],
    }
    assert result["measured_after"]["maximum_error"] == 0.0
    assert sketch.Geometry[spline].getWeights() == [1.0, 1.0]


def test_equal_accepts_one_external_or_blocked_target(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = _line((0.0, 0.0), (3.0, 0.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    external = FakeExternalLine("Support.Edge1")
    external.StartPoint = _point(0.0, 5.0)
    external.EndPoint = _point(9.0, 5.0)
    sketch.ExternalGeo.append(external)
    external_result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(0), _element(-3)],
                expected_external_geometry_count=1,
            ),
        ),
    )
    assert external_result["constraints"][0]["references"][1] == {
        "slot": 2,
        "geometry_index": -3,
    }
    assert math.isclose(sketch.Geometry[0].EndPoint.x, 9.0)

    editable = sketch.addGeometry(_line((0.0, 10.0), (4.0, 10.0)), False)
    blocked = sketch.addGeometry(_line((0.0, 15.0), (7.0, 15.0)), False)
    sketch.GeometryFacadeList[blocked].Blocked = True
    blocked_result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(editable), _element(blocked)],
                expected_geometry_count=3,
                expected_constraint_count=1,
                expected_external_geometry_count=1,
            ),
        ),
    )
    assert blocked_result["measured_after"]["maximum_error"] == 0.0


@pytest.mark.parametrize(
    ("selection", "message"),
    (
        ([_element(0)], "two through 17"),
        ([_element(0)] * 18, "two through 17"),
        ([_element(0), _element(0)], "must be distinct"),
        ([_element(0, "start"), _element(1)], "whole edges"),
        ([_element(-1), _element(0)], "Sketch axes"),
        ([_element(-2), _element(0)], "Sketch axes"),
    ),
)
def test_equal_refuses_invalid_selection(monkeypatch, selection, message) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(_line((0.0, 3.0), (7.0, 3.0)), False)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(selection, expected_geometry_count=2),
        )


@pytest.mark.parametrize(
    "geometry",
    (
        FakePoint(_point(2.0, 3.0)),
        FakeBSpline(),
    ),
)
def test_equal_refuses_unsupported_whole_geometry(monkeypatch, geometry) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    index = sketch.addGeometry(geometry, False)
    with pytest.raises(NativeSketchError, match="support|whole B-spline"):
        _prepared(
            document,
            context,
            _values(
                [_element(0), _element(index)],
                expected_geometry_count=2,
            ),
        )


def test_equal_refuses_mixed_families_and_mixed_weight_circle(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = sketch.addGeometry(_circle((0.0, 0.0), 2.0), False)
    with pytest.raises(NativeSketchError, match="same compatible family"):
        _prepared(
            document,
            context,
            _values(
                [_element(0), _element(circle)],
                expected_geometry_count=2,
            ),
        )

    first, _second, _spline = _add_weight_handles(sketch)
    with pytest.raises(NativeSketchError, match="same compatible family"):
        _prepared(
            document,
            context,
            _values(
                [_element(circle), _element(first)],
                expected_geometry_count=5,
                expected_constraint_count=2,
            ),
        )


def test_equal_refuses_two_fixed_targets(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = FakeExternalLine("Support.Edge1")
    second = FakeExternalLine("Support.Edge2")
    sketch.ExternalGeo.extend((first, second))
    with pytest.raises(NativeSketchError, match="at most one fixed or external"):
        _prepared(
            document,
            context,
            _values(
                [_element(-3), _element(-4)],
                expected_external_geometry_count=2,
            ),
        )


def test_equal_refuses_existing_pair_in_either_order(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line((0.0, 3.0), (5.0, 3.0)), False)
    sketch.addConstraint(FakeConstraint("Equal", second, 0))
    with pytest.raises(NativeSketchError, match="already have"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2, expected_constraint_count=1),
        )


def test_equal_refuses_transitively_connected_pair(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line((0.0, 3.0), (5.0, 3.0)), False)
    third = sketch.addGeometry(_line((0.0, 6.0), (5.0, 6.0)), False)
    sketch.addConstraint(FakeConstraint("Equal", 0, second))
    sketch.addConstraint(FakeConstraint("Equal", second, third))
    with pytest.raises(NativeSketchError, match="already connected"):
        _prepared(
            document,
            context,
            _values(
                [_element(0), _element(third)],
                expected_geometry_count=3,
                expected_constraint_count=2,
            ),
        )


def test_equal_refuses_group_and_non_weight_internal_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    member = sketch.addGeometry(_line((0.0, 3.0), (5.0, 3.0)), False)
    sketch.addConstraint(FakeConstraint("Text", [0, 0, member, 0], "A", "Font", True))
    with pytest.raises(NativeSketchError, match="group handle"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2, expected_constraint_count=1),
        )

    sketch.Constraints.clear()
    sketch.ConstraintCount = 0
    sketch.GeometryFacadeList[member].InternalType = "EllipseMajorDiameter"
    with pytest.raises(NativeSketchError, match="internal-alignment"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )


@pytest.mark.parametrize("failure", ("missing_owner", "two_owners", "mismatch"))
def test_equal_refuses_malformed_bspline_weight_handles(monkeypatch, failure) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first, second, spline = _add_weight_handles(sketch)
    if failure == "missing_owner":
        del sketch.Constraints[0]
        sketch.ConstraintCount -= 1
    elif failure == "two_owners":
        sketch.addConstraint(
            FakeConstraint(
                "InternalAlignment:Sketcher::BSplineControlPoint",
                first,
                3,
                spline,
                0,
            )
        )
    else:
        sketch.Geometry[first].Radius = 3.0
    message = "one exact spline owner" if failure != "mismatch" else "disagree"
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(
                [_element(first), _element(second)],
                expected_geometry_count=4,
                expected_constraint_count=int(sketch.ConstraintCount),
            ),
        )


def test_equal_refuses_degenerate_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line((3.0, 3.0), (3.0, 3.0)), False)
    with pytest.raises(
        NativeSketchError, match="line length must be finite and positive"
    ):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )
    sketch.Geometry[second] = _circle((3.0, 3.0), 0.0)
    sketch.GeometryFacadeList[second].Geometry = sketch.Geometry[second]
    sketch.Geometry[0] = _circle((0.0, 0.0), 2.0)
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    with pytest.raises(NativeSketchError, match="radius must be finite and positive"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
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


def test_equal_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(_line((0.0, 3.0), (5.0, 3.0)), False)
    sketch.FeasibilityOverride = _rejected_feasibility()
    before = (int(sketch.GeometryCount), int(sketch.ConstraintCount))
    with pytest.raises(NativeSketchError, match="redundant; no constraint was added"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == before


def test_equal_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(_line((0.0, 3.0), (7.0, 3.0)), False)
    values = _values(expected_geometry_count=2)
    prepared = _prepared(document, context, values)
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Equal"):
        create_sketch_equal(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, values)
    monkeypatch.setattr(sketch, "_solve_equal", lambda _constraint: None)
    draft = create_sketch_equal(document, prepared)
    with pytest.raises(NativeSketchError, match="does not satisfy"):
        verify_sketch_equal(document, draft)


def test_constraint_runtime_routes_equal_through_exact_transaction(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(_line((0.0, 3.0), (5.0, 3.0)), False)
    captured = {}

    def run_immediate(runtime_context, **values):
        assert runtime_context is context
        captured.update(values)
        draft = values["mutate"](document)
        return values["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    runtime = NativeSketchConstraintRuntime(context)
    result = runtime.mutate_constraint(
        {
            "operation": "constrain_equal",
            **_values(expected_geometry_count=2),
        },
        ticket=None,
    )

    assert captured["transaction_name"] == "Create Native Sketch Equal"
    assert result["operation"] == "constrain_equal"
    assert result["constraints"][0]["type"] == "Equal"
