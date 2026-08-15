# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchPerpendicular import (
    create_sketch_perpendicular,
    preflight_sketch_perpendicular,
    prepare_sketch_perpendicular,
    verify_sketch_perpendicular,
)
from vibecad_tests.native_sketch_test_support import (
    FakeCircle,
    FakeConstraint,
    FakeEllipse,
    FakeLine,
    FakePoint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _target(form: str, **values) -> dict[str, object]:
    return {"form": form, **values}


def _values(target: dict[str, object], **updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "target": target,
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_perpendicular(
        context,
        prepare_sketch_perpendicular(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_perpendicular(
        document,
        create_sketch_perpendicular(document, prepared),
    )


def _add_line(sketch, start, end) -> int:
    return sketch.addGeometry(FakeLine(_point(*start), _point(*end)), False)


def _curve_curve(first: int = 0, second: int = 1) -> dict[str, object]:
    return _target(
        "curve_curve",
        first_curve=_element(first, "whole"),
        second_curve=_element(second, "whole"),
    )


def test_perpendicular_constrains_exact_curve_pair(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (1.0, 2.0), (4.0, 7.0))

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(_curve_curve(), expected_geometry_count=2),
        ),
    )

    assert result["operation"] == "constrain_perpendicular"
    assert result["form"] == "curve_curve"
    assert result["support_constraints"] == []
    assert result["measured_before"]["angular_error"] > 30.0
    assert result["measured_after"]["unit"] == "deg"
    assert math.isclose(result["measured_after"]["angular_error"], 0.0, abs_tol=1.0e-12)
    assert result["constraint"] == {
        "index": 0,
        "type": "Perpendicular",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": 0},
            {"slot": 2, "geometry_index": 1},
        ],
    }


def test_perpendicular_constrains_line_and_circle_without_helper_geometry(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = FakeCircle(_point(2.0, 3.0), _point(0.0, 0.0), 2.0)
    sketch.addGeometry(circle, False)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(_curve_curve(), expected_geometry_count=2),
        ),
    )

    assert result["measured_before"] == {
        "center_line_distance": 3.0,
        "unit": "mm",
    }
    assert result["measured_after"] == {
        "center_line_distance": 0.0,
        "unit": "mm",
    }
    assert int(sketch.GeometryCount) == 2


def test_perpendicular_constrains_endpoint_to_curve(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch, (8.0, 4.0), (11.0, 9.0))
    target = _target(
        "endpoint_curve",
        endpoint=_element(0, "end"),
        curve=_element(second, "whole"),
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        ),
    )

    assert result["form"] == "endpoint_curve"
    assert result["measured_after"] == {"angular_error": 0.0, "unit": "deg"}
    assert math.isclose(result["constraint"]["value"], math.pi / 2.0)
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0, "position": 2},
        {"slot": 2, "geometry_index": second},
    ]


def test_perpendicular_constrains_two_curve_endpoints(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch, (8.0, 4.0), (11.0, 9.0))
    target = _target(
        "endpoint_endpoint",
        first_endpoint=_element(0, "end"),
        second_endpoint=_element(second, "start"),
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        ),
    )

    assert result["form"] == "endpoint_endpoint"
    assert result["measured_after"] == {"angular_error": 0.0, "unit": "deg"}
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0, "position": 2},
        {"slot": 2, "geometry_index": second, "position": 1},
    ]


def test_perpendicular_constrains_exact_point_pair_to_line(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    line = _add_line(sketch, (0.0, 4.0), (5.0, 6.0))
    target = _target(
        "point_pair_line",
        first_point=_element(0, "start"),
        second_point=_element(0, "end"),
        line=_element(line, "whole"),
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        ),
    )

    assert result["form"] == "point_pair_line"
    assert result["measured_after"] == {"angular_error": 0.0, "unit": "deg"}
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0},
        {"slot": 2, "geometry_index": line},
    ]


def test_point_pair_line_compiles_to_safe_two_line_host_calls(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    line = _add_line(sketch, (0.0, 4.0), (5.0, 6.0))
    target = _target(
        "point_pair_line",
        first_point=_element(0, "start"),
        second_point=_element(0, "end"),
        line=_element(line, "whole"),
    )
    diagnose = sketch.diagnoseAdditionalConstraints

    def checked_diagnosis(proposed):
        constraint = proposed[0] if isinstance(proposed, list) else proposed
        assert (constraint.First, constraint.FirstPos) == (0, 0)
        assert (constraint.Second, constraint.SecondPos) == (line, 0)
        assert constraint.Third == -2000
        return diagnose(proposed)

    monkeypatch.setattr(sketch, "diagnoseAdditionalConstraints", checked_diagnosis)

    prepared = _prepared(
        document,
        context,
        _values(target, expected_geometry_count=2),
    )
    add_constraint = sketch.addConstraint

    def checked_append(proposed):
        constraint = proposed[0]
        assert (constraint.First, constraint.Second, constraint.Third) == (0, line, -2000)
        return [add_constraint(constraint)]

    monkeypatch.setattr(sketch, "addConstraint", checked_append)
    result = _apply(document, prepared)

    assert prepared.resolved.target_form == "point_pair_line"
    assert result["constraint"]["type"] == "Perpendicular"


def test_perpendicular_via_point_reports_exact_support_constraint(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch, (5.0, 2.0), (9.0, 5.0))
    target = _target(
        "curves_via_point",
        first_curve=_element(0, "whole"),
        second_curve=_element(second, "whole"),
        point=_element(0, "end"),
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        ),
    )

    assert result["form"] == "curves_via_point"
    assert result["support_constraints"] == [
        {
            "index": 0,
            "type": "PointOnObject",
            "driving": True,
            "active": True,
            "virtual": False,
            "references": [
                {"slot": 1, "geometry_index": 0, "position": 2},
                {"slot": 2, "geometry_index": second},
            ],
        }
    ]
    assert result["constraint"]["index"] == 1
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0},
        {"slot": 2, "geometry_index": second},
        {"slot": 3, "geometry_index": 0, "position": 2},
    ]
    assert result["measured_after"]["unit"] == "deg"
    assert math.isclose(
        result["measured_after"]["angular_error"],
        0.0,
        abs_tol=1.0e-12,
    )


def test_perpendicular_via_point_reuses_existing_direct_support(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch, (5.0, 0.0), (8.0, 4.0))
    sketch.addConstraint(FakeConstraint("PointOnObject", 0, 2, second))
    target = _target(
        "curves_via_point",
        first_curve=_element(0, "whole"),
        second_curve=_element(second, "whole"),
        point=_element(0, "end"),
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                target,
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        ),
    )

    assert result["support_constraints"] == []
    assert result["constraint"]["index"] == 1


def test_perpendicular_refuses_implicit_conic_helper_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(FakeEllipse(_point(2.0, 2.0), 4.0, 2.0), False)
    with pytest.raises(NativeSketchError, match="will not infer construction geometry"):
        _prepared(
            document,
            context,
            _values(_curve_curve(), expected_geometry_count=2),
        )
    assert int(sketch.GeometryCount) == 2
    assert int(sketch.ConstraintCount) == 0


@pytest.mark.parametrize(
    ("target", "message"),
    (
        (
            _target(
                "curve_curve",
                first_curve=_element(0, "start"),
                second_curve=_element(1, "whole"),
            ),
            "whole curve",
        ),
        (
            _curve_curve(0, 0),
            "must be distinct",
        ),
        (
            _target(
                "endpoint_curve",
                endpoint=_element(0, "whole"),
                curve=_element(1, "whole"),
            ),
            "curve endpoint",
        ),
        (
            _target(
                "endpoint_endpoint",
                first_endpoint=_element(0, "end"),
                second_endpoint=_element(0, "start"),
            ),
            "distinct curves",
        ),
        (
            _target(
                "point_pair_line",
                first_point=_element(0, "start"),
                second_point=_element(1, "start"),
                line=_element(2, "whole"),
            ),
            "start and end of one explicit straight line",
        ),
    ),
)
def test_perpendicular_refuses_wrong_exact_targets(monkeypatch, target, message) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (1.0, 1.0), (3.0, 5.0))
    sketch.addGeometry(FakePoint(_point(3.0, 2.0)), False)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=3),
        )


def test_perpendicular_refuses_standalone_endpoint_and_fixed_targets(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(3.0, 2.0)), False)
    target = _target(
        "endpoint_curve",
        endpoint=_element(point, "start"),
        curve=_element(-1, "whole"),
    )
    with pytest.raises(NativeSketchError, match="standalone point"):
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        )

    target = _curve_curve(-1, -2)
    with pytest.raises(NativeSketchError, match="editable internal target"):
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"target": {}},
        {"target": {"form": "curve_curve"}},
        {"target": {**_curve_curve(), "unexpected": True}},
        {"target": {**_curve_curve(), "form": "tangent"}},
        {"unexpected": True},
    ),
)
def test_perpendicular_rejects_invalid_closed_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    values = _values(_curve_curve())
    values.update(updates)
    with pytest.raises(NativeSketchError):
        prepare_sketch_perpendicular(document.Uid, values)


def test_perpendicular_refuses_existing_constraint_in_reverse_order(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (1.0, 1.0), (3.0, 5.0))
    sketch.addConstraint(FakeConstraint("Perpendicular", 1, 0))
    with pytest.raises(NativeSketchError, match="already have"):
        _prepared(
            document,
            context,
            _values(
                _curve_curve(),
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )


def test_perpendicular_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (1.0, 1.0), (3.0, 5.0))
    sketch.FeasibilityOverride = {
        "accepted": False,
        "degrees_of_freedom": -1,
        "solver_status": -2,
        "first_proposed_constraint_index": 0,
        "proposed_constraint_count": 1,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [0],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
    }
    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(
            document,
            context,
            _values(_curve_curve(), expected_geometry_count=2),
        )
    assert int(sketch.ConstraintCount) == 0


def test_perpendicular_refuses_feasibility_side_effect(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (1.0, 1.0), (3.0, 5.0))
    diagnose = sketch.diagnoseAdditionalConstraints

    def mutating_diagnosis(constraints):
        result = diagnose(constraints)
        sketch.GeometryFacadeList[0].Blocked = True
        return result

    monkeypatch.setattr(sketch, "diagnoseAdditionalConstraints", mutating_diagnosis)
    with pytest.raises(NativeSketchError, match="feasibility check changed"):
        _prepared(
            document,
            context,
            _values(_curve_curve(), expected_geometry_count=2),
        )


def test_perpendicular_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (1.0, 1.0), (3.0, 5.0))
    values = _values(_curve_curve(), expected_geometry_count=2)
    prepared = _prepared(document, context, values)
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Perpendicular"):
        create_sketch_perpendicular(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, values)
    monkeypatch.setattr(sketch, "_solve_perpendicular", lambda _constraint: None)
    draft = create_sketch_perpendicular(document, prepared)
    with pytest.raises(NativeSketchError, match="does not satisfy"):
        verify_sketch_perpendicular(document, draft)
