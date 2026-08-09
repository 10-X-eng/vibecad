# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import VibeCADNativeSketchConstraintRuntime as runtime_module
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchTangent import (
    create_sketch_tangent,
    preflight_sketch_tangent,
    prepare_sketch_tangent,
    verify_sketch_tangent,
)
from vibecad_tests.native_sketch_test_support import (
    FakeBSpline,
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


def _element(index: int, position: str = "whole") -> dict[str, object]:
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
    return preflight_sketch_tangent(
        context,
        prepare_sketch_tangent(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_tangent(
        document,
        create_sketch_tangent(document, prepared),
    )


def _add_line(sketch, start=(7.0, 3.0), end=(10.0, 8.0)) -> int:
    return sketch.addGeometry(FakeLine(_point(*start), _point(*end)), False)


def _curve_curve(first: int = 0, second: int = 1) -> dict[str, object]:
    return _target(
        "curve_curve",
        first_curve=_element(first),
        second_curve=_element(second),
    )


def _endpoint_curve(first: int = 0, second: int = 1) -> dict[str, object]:
    return _target(
        "endpoint_curve",
        endpoint=_element(first, "end"),
        curve=_element(second),
    )


def _endpoint_endpoint(first: int = 0, second: int = 1) -> dict[str, object]:
    return _target(
        "endpoint_endpoint",
        first_endpoint=_element(first, "end"),
        second_endpoint=_element(second, "start"),
    )


def _via_point(first: int = 0, second: int = 1) -> dict[str, object]:
    return _target(
        "curves_via_point",
        first_curve=_element(first),
        second_curve=_element(second),
        point=_element(first, "end"),
    )


def _replace_endpoint_curve(
    index: int,
    first: int = 0,
    second: int = 1,
) -> dict[str, object]:
    return _target(
        "replace_with_endpoint_curve",
        constraint_index=index,
        endpoint=_element(first, "end"),
        curve=_element(second),
    )


def _replace_endpoint_endpoint(
    index: int,
    first: int = 0,
    second: int = 1,
) -> dict[str, object]:
    return _target(
        "replace_with_endpoint_endpoint",
        constraint_index=index,
        first_endpoint=_element(first, "end"),
        second_endpoint=_element(second, "start"),
    )


def _rejected_feasibility(index: int, count: int = 1) -> dict[str, object]:
    return {
        "accepted": False,
        "degrees_of_freedom": -1,
        "solver_status": -2,
        "first_proposed_constraint_index": index,
        "proposed_constraint_count": count,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [index],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
    }


def test_tangent_constrains_exact_line_pair(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(_curve_curve(), expected_geometry_count=2),
        ),
    )

    assert result["operation"] == "constrain_tangent"
    assert result["form"] == "curve_curve"
    assert result["support_constraints"] == []
    assert result["measured_before"]["angular_error"] > 45.0
    assert result["measured_after"]["unit"] == "deg"
    assert math.isclose(
        result["measured_after"]["angular_error"],
        0.0,
        abs_tol=1.0e-12,
    )
    assert result["constraint"] == {
        "index": 0,
        "type": "Tangent",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": 0},
            {"slot": 2, "geometry_index": second},
        ],
    }


@pytest.mark.parametrize("circular_pair", (False, True))
def test_tangent_constrains_circular_pairs_without_helper_geometry(
    monkeypatch,
    circular_pair,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    if circular_pair:
        sketch.Geometry[0] = FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 2.0)
        sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
        sketch.addGeometry(
            FakeCircle(_point(8.0, 1.0), _point(0.0, 0.0), 3.0),
            False,
        )
    else:
        sketch.addGeometry(
            FakeCircle(_point(2.0, 4.0), _point(0.0, 0.0), 2.0),
            False,
        )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(_curve_curve(), expected_geometry_count=2),
        ),
    )

    assert result["measured_before"]["tangency_error"] > 0.5
    assert result["measured_after"] == {"tangency_error": 0.0, "unit": "mm"}
    assert int(sketch.GeometryCount) == 2


def test_tangent_constrains_endpoint_to_curve(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(_endpoint_curve(), expected_geometry_count=2),
        ),
    )

    assert result["form"] == "endpoint_curve"
    assert result["measured_after"]["unit"] == "deg"
    assert math.isclose(
        result["measured_after"]["angular_error"],
        0.0,
        abs_tol=1.0e-12,
    )
    assert math.isclose(result["constraint"]["value"], -math.pi / 2.0)
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0, "position": 2},
        {"slot": 2, "geometry_index": second},
    ]


def test_tangent_constrains_two_curve_endpoints(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(_endpoint_endpoint(), expected_geometry_count=2),
        ),
    )

    assert result["form"] == "endpoint_endpoint"
    assert result["measured_after"] == {"angular_error": 0.0, "unit": "deg"}
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": 0, "position": 2},
        {"slot": 2, "geometry_index": second, "position": 1},
    ]


def test_tangent_via_point_reports_exact_support_constraint(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch, (5.0, 2.0), (9.0, 5.0))

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(_via_point(), expected_geometry_count=2),
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


def test_tangent_via_point_reuses_existing_support(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch, (5.0, 0.0), (9.0, 4.0))
    sketch.addConstraint(FakeConstraint("PointOnObject", 0, 2, second))

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                _via_point(),
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        ),
    )

    assert result["support_constraints"] == []
    assert result["constraint"]["index"] == 1
    assert int(sketch.ConstraintCount) == 2


def test_tangent_accepts_explicit_bspline_endpoint(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = FakeBSpline(
        poles=[_point(0.0, 0.0), _point(3.0, 0.0), _point(6.0, 0.0)],
        multiplicities=[3, 3],
        knots=[0.0, 1.0],
        degree=2,
        weights=[1.0, 1.0, 1.0],
    )
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    _add_line(sketch)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(_endpoint_curve(), expected_geometry_count=2),
        ),
    )

    assert result["constraint"]["type"] == "Tangent"
    assert result["measured_after"]["angular_error"] == 0.0


@pytest.mark.parametrize("geometry", (FakeEllipse(_point(2.0, 2.0), 4.0, 2.0), FakeBSpline()))
def test_tangent_refuses_implicit_curve_curve_helpers(monkeypatch, geometry) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(geometry, False)
    message = "explicit point" if isinstance(geometry, FakeEllipse) else "B-splines"

    with pytest.raises(NativeSketchError, match=message):
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
                second_curve=_element(1),
            ),
            "whole curve",
        ),
        (_curve_curve(0, 0), "distinct"),
        (
            _target(
                "endpoint_curve",
                endpoint=_element(0),
                curve=_element(1),
            ),
            "curve endpoint",
        ),
        (_endpoint_curve(0, 0), "against itself"),
        (_endpoint_endpoint(0, 0), "distinct curves"),
        (
            _target(
                "curves_via_point",
                first_curve=_element(0),
                second_curve=_element(1),
                point=_element(1, "center"),
            ),
            "start or end position",
        ),
    ),
)
def test_tangent_refuses_wrong_exact_targets(monkeypatch, target, message) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        )


def test_tangent_refuses_standalone_endpoint_and_two_fixed_curves(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(3.0, 2.0)), False)
    with pytest.raises(NativeSketchError, match="standalone point"):
        _prepared(
            document,
            context,
            _values(
                _target(
                    "endpoint_curve",
                    endpoint=_element(point, "start"),
                    curve=_element(-1),
                ),
                expected_geometry_count=2,
            ),
        )
    with pytest.raises(NativeSketchError, match="editable internal target"):
        _prepared(
            document,
            context,
            _values(_curve_curve(-1, -2), expected_geometry_count=2),
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"target": {}},
        {"target": {"form": "curve_curve"}},
        {"target": {**_curve_curve(), "unexpected": True}},
        {"target": {**_curve_curve(), "form": "tangent"}},
        {"target": {**_replace_endpoint_curve(0), "constraint_index": True}},
        {"unexpected": True},
    ),
)
def test_tangent_rejects_invalid_closed_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    values = _values(_curve_curve())
    values.update(updates)
    with pytest.raises(NativeSketchError):
        prepare_sketch_tangent(document.Uid, values)


@pytest.mark.parametrize(
    "constraint",
    (
        FakeConstraint("Tangent", 1, 0),
        FakeConstraint("Tangent", 0, 2, 1),
        FakeConstraint("Tangent", 1, 1, 0, 2),
        FakeConstraint("TangentViaPoint", 1, 0, 0, 2),
    ),
)
def test_tangent_refuses_existing_constraint_in_reverse_order(
    monkeypatch,
    constraint,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (5.0, 0.0), (9.0, 0.0))
    sketch.addConstraint(constraint)
    form = (
        _via_point()
        if constraint.Third > -2000
        else _endpoint_endpoint()
        if constraint.FirstPos and constraint.SecondPos
        else _endpoint_curve()
        if constraint.FirstPos or constraint.SecondPos
        else _curve_curve()
    )
    with pytest.raises(NativeSketchError, match="already have"):
        _prepared(
            document,
            context,
            _values(
                form,
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )


@pytest.mark.parametrize(
    ("support", "target", "replacement_form"),
    (
        (
            FakeConstraint("Coincident", 0, 2, 1, 1),
            _endpoint_endpoint(),
            "replace_with_endpoint_endpoint",
        ),
        (
            FakeConstraint("PointOnObject", 0, 2, 1),
            _endpoint_curve(),
            "replace_with_endpoint_curve",
        ),
        (
            FakeConstraint("Tangent", 0, 1),
            _endpoint_endpoint(),
            "replace_with_endpoint_endpoint",
        ),
        (
            FakeConstraint("Tangent", 0, 1),
            _endpoint_curve(),
            "replace_with_endpoint_curve",
        ),
    ),
)
def test_tangent_direct_forms_require_explicit_exact_replacement(
    monkeypatch,
    support,
    target,
    replacement_form,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (5.0, 0.0), (9.0, 2.0))
    sketch.addConstraint(support)

    with pytest.raises(NativeSketchError, match=replacement_form):
        _prepared(
            document,
            context,
            _values(
                target,
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )

    assert int(sketch.ConstraintCount) == 1
    assert sketch.Constraints[0].Type == support.Type


def test_tangent_does_not_mistake_unrelated_point_support_for_replacement(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (5.0, 0.0), (9.0, 2.0))
    sketch.addConstraint(FakeConstraint("PointOnObject", 0, 1, 1))

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                _endpoint_curve(),
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        ),
    )

    assert result["constraint"]["index"] == 1
    assert sketch.Constraints[0].Type == "PointOnObject"


@pytest.mark.parametrize(
    ("existing", "target"),
    (
        (
            FakeConstraint("Coincident", 0, 2, 1, 1),
            _replace_endpoint_endpoint(0),
        ),
        (FakeConstraint("Tangent", 0, 1), _replace_endpoint_endpoint(0)),
        (FakeConstraint("PointOnObject", 0, 2, 1), _replace_endpoint_curve(0)),
        (FakeConstraint("Tangent", 0, 1), _replace_endpoint_curve(0)),
    ),
)
def test_tangent_performs_one_explicit_exact_replacement(
    monkeypatch,
    existing,
    target,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (5.0, 0.0), (9.0, 2.0))
    sketch.addConstraint(existing)

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

    assert result["replaced_constraint"]["index"] == 0
    assert result["replaced_constraint"]["type"] == existing.Type
    assert result["constraint"]["index"] == 0
    assert result["constraint"]["type"] == "Tangent"
    assert int(sketch.ConstraintCount) == 1


def test_tangent_replacement_preserves_and_reindexes_other_constraints(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (5.0, 0.0), (9.0, 2.0))
    sketch.addConstraint(FakeConstraint("Coincident", 0, 2, 1, 1))
    sketch.addConstraint(FakeConstraint("Horizontal", 0))

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                _replace_endpoint_endpoint(0),
                expected_geometry_count=2,
                expected_constraint_count=2,
            ),
        ),
    )

    assert [constraint.Type for constraint in sketch.Constraints] == [
        "Horizontal",
        "Tangent",
    ]
    assert result["constraint"]["index"] == 1
    assert int(sketch.ConstraintCount) == 2


@pytest.mark.parametrize(
    ("target", "setup", "message"),
    (
        (_replace_endpoint_endpoint(-1), "coincident", "outside"),
        (_replace_endpoint_endpoint(3), "coincident", "outside"),
        (_replace_endpoint_curve(0), "coincident", "does not name"),
        (_replace_endpoint_endpoint(0), "point_on_object", "does not name"),
        (_replace_endpoint_endpoint(0), "wrong_coincident", "does not name"),
    ),
)
def test_tangent_refuses_wrong_replacement_record(
    monkeypatch,
    target,
    setup,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (5.0, 0.0), (9.0, 2.0))
    constraint = {
        "coincident": FakeConstraint("Coincident", 0, 2, 1, 1),
        "point_on_object": FakeConstraint("PointOnObject", 0, 2, 1),
        "wrong_coincident": FakeConstraint("Coincident", 0, 1, 1, 2),
    }[setup]
    sketch.addConstraint(constraint)

    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(
                target,
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )


@pytest.mark.parametrize("attribute", ("Driving", "IsActive", "InVirtualSpace"))
def test_tangent_refuses_non_durable_replacement_constraint(
    monkeypatch,
    attribute,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (5.0, 0.0), (9.0, 2.0))
    index = sketch.addConstraint(FakeConstraint("Coincident", 0, 2, 1, 1))
    setattr(sketch.Constraints[index], attribute, attribute == "InVirtualSpace")

    with pytest.raises(NativeSketchError, match="active driving non-virtual"):
        _prepared(
            document,
            context,
            _values(
                _replace_endpoint_endpoint(index),
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )


def test_tangent_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
    sketch.FeasibilityOverride = _rejected_feasibility(0)

    with pytest.raises(NativeSketchError, match="no constraint was added"):
        _prepared(
            document,
            context,
            _values(_curve_curve(), expected_geometry_count=2),
        )

    assert int(sketch.ConstraintCount) == 0


def test_tangent_refuses_replacement_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (5.0, 0.0), (9.0, 2.0))
    sketch.addConstraint(FakeConstraint("Coincident", 0, 2, 1, 1))
    sketch.FeasibilityOverride = _rejected_feasibility(0)

    with pytest.raises(NativeSketchError, match="no constraint was changed"):
        _prepared(
            document,
            context,
            _values(
                _replace_endpoint_endpoint(0),
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )

    assert int(sketch.ConstraintCount) == 1
    assert sketch.Constraints[0].Type == "Coincident"


def test_tangent_requires_exact_replacement_diagnostic(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, (5.0, 0.0), (9.0, 2.0))
    sketch.addConstraint(FakeConstraint("Coincident", 0, 2, 1, 1))
    monkeypatch.setattr(sketch, "diagnoseConstraintReplacement", None)

    with pytest.raises(NativeSketchError, match="replacement feasibility is unavailable"):
        _prepared(
            document,
            context,
            _values(
                _replace_endpoint_endpoint(0),
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )


def test_tangent_refuses_feasibility_side_effect(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
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


def test_tangent_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
    values = _values(_curve_curve(), expected_geometry_count=2)
    prepared = _prepared(document, context, values)
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Tangent"):
        create_sketch_tangent(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, values)
    monkeypatch.setattr(sketch, "_solve_tangent", lambda _constraint: None)
    draft = create_sketch_tangent(document, prepared)
    with pytest.raises(NativeSketchError, match="does not satisfy"):
        verify_sketch_tangent(document, draft)


def test_constraint_runtime_routes_tangent_through_exact_transaction(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
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
            "operation": "constrain_tangent",
            **_values(_curve_curve(), expected_geometry_count=2),
        },
        ticket=None,
    )

    assert captured["transaction_name"] == "Create Native Sketch Tangent"
    assert result["operation"] == "constrain_tangent"
    assert result["constraint"]["type"] == "Tangent"
