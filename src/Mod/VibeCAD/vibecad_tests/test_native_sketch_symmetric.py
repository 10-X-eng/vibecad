# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

import VibeCADNativeSketchConstraintRuntime as runtime_module
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchSymmetric import (
    create_sketch_symmetric,
    preflight_sketch_symmetric,
    prepare_sketch_symmetric,
    verify_sketch_symmetric,
)
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
    return preflight_sketch_symmetric(
        context,
        prepare_sketch_symmetric(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_symmetric(
        document,
        create_sketch_symmetric(document, prepared),
    )


def _line(start, end) -> FakeLine:
    return FakeLine(_point(*start), _point(*end))


def _points_about_line(
    first: dict[str, object],
    second: dict[str, object],
    line: dict[str, object],
) -> dict[str, object]:
    return _target(
        "points_about_line",
        first_point=first,
        second_point=second,
        symmetry_line=line,
    )


def _points_about_point(
    first: dict[str, object],
    second: dict[str, object],
    point: dict[str, object],
) -> dict[str, object]:
    return _target(
        "points_about_point",
        first_point=first,
        second_point=second,
        symmetry_point=point,
    )


def _curve_about_line(curve: int, line: int = -1) -> dict[str, object]:
    return _target(
        "curve_about_line",
        curve=_element(curve, "whole"),
        symmetry_line=_element(line, "whole"),
    )


def _curve_about_point(curve: int, point: dict[str, object]) -> dict[str, object]:
    return _target(
        "curve_about_point",
        curve=_element(curve, "whole"),
        symmetry_point=point,
    )


def _constraint(
    index: int,
    first: tuple[int, int],
    second: tuple[int, int],
    reference: tuple[int, int | None],
) -> dict[str, object]:
    references = [
        {"slot": 1, "geometry_index": first[0], "position": first[1]},
        {"slot": 2, "geometry_index": second[0], "position": second[1]},
        {"slot": 3, "geometry_index": reference[0]},
    ]
    if reference[1] is not None:
        references[-1]["position"] = reference[1]
    return {
        "index": index,
        "type": "Symmetric",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": references,
    }


def test_symmetric_constrains_exact_points_about_internal_line(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = _line((1.0, 2.0), (5.0, 3.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    symmetry_line = sketch.addGeometry(_line((-2.0, 0.0), (8.0, 0.0)), False)
    target = _points_about_line(
        _element(0, "start"),
        _element(0, "end"),
        _element(symmetry_line, "whole"),
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        ),
    )

    assert result["operation"] == "constrain_symmetric"
    assert result["form"] == "points_about_line"
    assert result["measured_before"] == {
        "reference_kind": "line",
        "reflection_error": math.hypot(4.0, 5.0),
        "midpoint_error": 2.5,
        "unit": "mm",
    }
    assert result["measured_after"] == {
        "reference_kind": "line",
        "reflection_error": 0.0,
        "midpoint_error": 0.0,
        "unit": "mm",
    }
    assert result["constraint"] == _constraint(
        0,
        (0, 1),
        (0, 2),
        (symmetry_line, None),
    )
    assert (sketch.Geometry[0].EndPoint.x, sketch.Geometry[0].EndPoint.y) == (
        1.0,
        -2.0,
    )


def test_symmetric_constrains_exact_points_about_root(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = _line((2.0, 3.0), (7.0, 8.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    target = _points_about_point(
        _element(0, "start"),
        _element(0, "end"),
        _element(-1, "start"),
    )

    result = _apply(document, _prepared(document, context, _values(target)))

    assert result["form"] == "points_about_point"
    assert result["measured_after"] == {
        "reference_kind": "point",
        "reflection_error": 0.0,
        "midpoint_error": 0.0,
        "unit": "mm",
    }
    assert result["constraint"] == _constraint(0, (0, 1), (0, 2), (-1, 1))
    assert (sketch.Geometry[0].EndPoint.x, sketch.Geometry[0].EndPoint.y) == (
        -2.0,
        -3.0,
    )


def test_symmetric_constrains_one_open_curve_about_axis(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = _line((2.0, 3.0), (8.0, 9.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]

    result = _apply(
        document,
        _prepared(document, context, _values(_curve_about_line(0))),
    )

    assert result["form"] == "curve_about_line"
    assert result["constraint"] == _constraint(0, (0, 1), (0, 2), (-1, None))
    assert (sketch.Geometry[0].EndPoint.x, sketch.Geometry[0].EndPoint.y) == (
        2.0,
        -3.0,
    )


def test_symmetric_constrains_one_open_curve_about_exact_point(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = _line((2.0, 3.0), (8.0, 9.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    reference = sketch.addGeometry(FakePoint(_point(5.0, 5.0)), False)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                _curve_about_point(0, _element(reference, "start")),
                expected_geometry_count=2,
            ),
        ),
    )

    assert result["form"] == "curve_about_point"
    assert result["constraint"] == _constraint(
        0,
        (0, 1),
        (0, 2),
        (reference, 1),
    )
    assert (sketch.Geometry[0].EndPoint.x, sketch.Geometry[0].EndPoint.y) == (
        8.0,
        7.0,
    )


def _open_curves() -> tuple[object, ...]:
    return (
        _line((1.0, 2.0), (5.0, 3.0)),
        FakeArc(FakeCircle(_point(0.0, 2.0), _point(0.0, 0.0), 2.0), 0.2, 1.4),
        FakeEllipticalArc(FakeEllipse(_point(0.0, 2.0), 5.0, 2.0), 0.2, 1.4),
        FakeHyperbolicArc(FakeHyperbola(_point(0.0, 2.0), 3.0, 2.0), -0.4, 0.8),
        FakeParabolicArc(
            FakeParabola(_point(2.0, 2.0), _point(0.0, 2.0), None),
            -2.0,
            3.0,
        ),
        FakeBSpline(
            poles=[_point(1.0, 2.0), _point(3.0, 5.0), _point(6.0, 3.0)],
            multiplicities=[3, 3],
            knots=[0.0, 1.0],
            degree=2,
            weights=[1.0, 1.0, 1.0],
        ),
    )


@pytest.mark.parametrize("geometry", _open_curves())
def test_symmetric_supports_every_shipped_open_curve_family(
    monkeypatch,
    geometry,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    index = sketch.addGeometry(geometry, False)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(_curve_about_line(index), expected_geometry_count=2),
        ),
    )

    assert result["measured_after"]["reflection_error"] == 0.0
    assert result["measured_after"]["midpoint_error"] == 0.0
    assert result["constraint"]["references"][:2] == [
        {"slot": 1, "geometry_index": index, "position": 1},
        {"slot": 2, "geometry_index": index, "position": 2},
    ]


def test_symmetric_accepts_vertical_axis_and_external_references(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = _line((2.0, 3.0), (8.0, 9.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    vertical = _points_about_line(
        _element(0, "start"),
        _element(0, "end"),
        _element(-2, "whole"),
    )
    vertical_result = _apply(
        document,
        _prepared(document, context, _values(vertical)),
    )
    assert vertical_result["constraint"]["references"][-1] == {
        "slot": 3,
        "geometry_index": -2,
    }
    assert (sketch.Geometry[0].EndPoint.x, sketch.Geometry[0].EndPoint.y) == (
        -2.0,
        3.0,
    )

    second = sketch.addGeometry(_line((3.0, 4.0), (10.0, 12.0)), False)
    external_line = FakeExternalLine("Support.Edge1")
    external_line.StartPoint = _point(-5.0, 0.0)
    external_line.EndPoint = _point(5.0, 0.0)
    sketch.ExternalGeo.append(external_line)
    external_line_result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                _curve_about_line(second, -3),
                expected_geometry_count=2,
                expected_constraint_count=1,
                expected_external_geometry_count=1,
            ),
        ),
    )
    assert external_line_result["constraint"]["references"][-1] == {
        "slot": 3,
        "geometry_index": -3,
    }

    third = sketch.addGeometry(_line((4.0, 2.0), (9.0, 7.0)), False)
    external_point_result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                _curve_about_point(third, _element(-3, "start")),
                expected_geometry_count=3,
                expected_constraint_count=2,
                expected_external_geometry_count=1,
            ),
        ),
    )
    assert external_point_result["constraint"]["references"][-1] == {
        "slot": 3,
        "geometry_index": -3,
        "position": 1,
    }


def test_symmetric_can_move_editable_reference_when_subjects_are_external(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first_external = FakeExternalLine("First.Edge1")
    first_external.StartPoint = _point(-3.0, 2.0)
    second_external = FakeExternalLine("Second.Edge1")
    second_external.StartPoint = _point(5.0, -4.0)
    sketch.ExternalGeo.extend((first_external, second_external))
    symmetry_line = sketch.addGeometry(_line((0.0, 0.0), (7.0, 0.0)), False)
    target = _points_about_line(
        _element(-3, "start"),
        _element(-4, "start"),
        _element(symmetry_line, "whole"),
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                target,
                expected_geometry_count=2,
                expected_external_geometry_count=2,
            ),
        ),
    )

    assert result["measured_after"]["reflection_error"] < 1.0e-12
    assert result["measured_after"]["midpoint_error"] < 1.0e-12
    midpoint = (1.0, -1.0)
    line = sketch.Geometry[symmetry_line]
    delta = (line.EndPoint.x - line.StartPoint.x, line.EndPoint.y - line.StartPoint.y)
    assert math.isclose(
        delta[0] * (midpoint[1] - line.StartPoint.y)
        - delta[1] * (midpoint[0] - line.StartPoint.x),
        0.0,
        abs_tol=1.0e-12,
    )


def test_symmetric_can_move_editable_point_reference(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first_external = FakeExternalLine("First.Edge1")
    first_external.StartPoint = _point(-3.0, 2.0)
    second_external = FakeExternalLine("Second.Edge1")
    second_external.StartPoint = _point(5.0, -4.0)
    sketch.ExternalGeo.extend((first_external, second_external))
    reference = sketch.addGeometry(FakePoint(_point(20.0, 20.0)), False)
    target = _points_about_point(
        _element(-3, "start"),
        _element(-4, "start"),
        _element(reference, "start"),
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                target,
                expected_geometry_count=2,
                expected_external_geometry_count=2,
            ),
        ),
    )

    assert result["measured_after"]["reflection_error"] == 0.0
    assert (sketch.Geometry[reference].X, sketch.Geometry[reference].Y) == (1.0, -1.0)


def test_symmetric_duplicate_matching_is_subject_order_independent(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    symmetry_line = sketch.addGeometry(_line((-2.0, 0.0), (8.0, 0.0)), False)
    sketch.addConstraint(FakeConstraint("Symmetric", 0, 2, 0, 1, symmetry_line))
    reversed_target = _points_about_line(
        _element(0, "start"),
        _element(0, "end"),
        _element(symmetry_line, "whole"),
    )
    with pytest.raises(NativeSketchError, match="already have"):
        _prepared(
            document,
            context,
            _values(
                reversed_target,
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )


def test_symmetric_curve_form_matches_existing_endpoint_pair(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addConstraint(FakeConstraint("Symmetric", 0, 1, 0, 2, -1))
    with pytest.raises(NativeSketchError, match="already have"):
        _prepared(
            document,
            context,
            _values(_curve_about_line(0), expected_constraint_count=1),
        )


@pytest.mark.parametrize(
    ("target", "message"),
    (
        (
            _curve_about_line(0, 0),
            "own symmetry line",
        ),
        (
            _curve_about_point(0, _element(0, "start")),
            "own endpoints",
        ),
        (
            _points_about_line(
                _element(0, "start"),
                _element(0, "end"),
                _element(0, "whole"),
            ),
            "one line's endpoints",
        ),
    ),
)
def test_symmetric_refuses_self_reference(monkeypatch, target, message) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(document, context, _values(target))


@pytest.mark.parametrize(
    "geometry",
    (
        FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 2.0),
        FakeEllipse(_point(0.0, 0.0), 4.0, 2.0),
        FakeBSpline(periodic=True),
    ),
)
def test_symmetric_refuses_closed_or_periodic_curve(monkeypatch, geometry) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    index = sketch.addGeometry(geometry, False)
    with pytest.raises(NativeSketchError, match="open conic arc|periodic"):
        _prepared(
            document,
            context,
            _values(_curve_about_line(index), expected_geometry_count=2),
        )


def test_symmetric_refuses_non_line_symmetry_reference(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = sketch.addGeometry(
        FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 2.0),
        False,
    )
    target = _points_about_line(
        _element(0, "start"),
        _element(0, "end"),
        _element(circle, "whole"),
    )
    with pytest.raises(NativeSketchError, match="whole straight line"):
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"expected_geometry_count": 0},
        {"expected_constraint_count": 1},
        {"expected_external_geometry_count": 1},
    ),
)
def test_symmetric_refuses_stale_sketch_counts(monkeypatch, updates) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="count changed|count does not match"):
        _prepared(
            document,
            context,
            _values(_curve_about_line(0), **updates),
        )


def test_symmetric_refuses_degenerate_symmetry_line(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    line = sketch.addGeometry(_line((3.0, 4.0), (3.0, 4.0)), False)
    target = _points_about_line(
        _element(0, "start"),
        _element(0, "end"),
        _element(line, "whole"),
    )
    with pytest.raises(NativeSketchError, match="zero-length|degenerate"):
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
        )


@pytest.mark.parametrize(
    "target",
    (
        {"form": "points_about_line"},
        {
            "form": "points_about_line",
            "first_point": _element(0, "start"),
            "second_point": _element(0, "end"),
            "symmetry_line": _element(-1, "whole"),
            "extra": True,
        },
        {
            "form": "unknown",
            "curve": _element(0, "whole"),
            "symmetry_line": _element(-1, "whole"),
        },
    ),
)
def test_symmetric_refuses_ambiguous_or_open_target_shapes(monkeypatch, target) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="unsupported|incorrect fields"):
        _prepared(document, context, _values(target))


@pytest.mark.parametrize(
    "target",
    (
        _points_about_line(
            _element(0, "whole"),
            _element(0, "end"),
            _element(-1, "whole"),
        ),
        _points_about_point(
            _element(0, "start"),
            _element(0, "end"),
            _element(-1, "whole"),
        ),
        _target(
            "curve_about_line",
            curve=_element(0, "start"),
            symmetry_line=_element(-1, "whole"),
        ),
    ),
)
def test_symmetric_refuses_wrong_exact_positions(monkeypatch, target) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        _prepared(document, context, _values(target))


def test_symmetric_refuses_all_fixed_or_blocked_targets(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addConstraint(FakeConstraint("Block", 0))
    target = _curve_about_line(0)
    with pytest.raises(NativeSketchError, match="all fixed or external"):
        _prepared(
            document,
            context,
            _values(target, expected_constraint_count=1),
        )


def test_symmetric_refuses_group_and_internal_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    member = sketch.addGeometry(_line((0.0, 3.0), (5.0, 3.0)), False)
    sketch.addConstraint(FakeConstraint("Text", [0, 0, member, 0], "A", "Font", True))
    target = _curve_about_line(member)
    with pytest.raises(NativeSketchError, match="group handle"):
        _prepared(
            document,
            context,
            _values(
                target,
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )

    sketch.Constraints.clear()
    sketch.ConstraintCount = 0
    sketch.GeometryFacadeList[member].InternalType = "EllipseMajorDiameter"
    with pytest.raises(NativeSketchError, match="internal-alignment"):
        _prepared(
            document,
            context,
            _values(target, expected_geometry_count=2),
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


def test_symmetric_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = _rejected_feasibility()
    before = (int(sketch.GeometryCount), int(sketch.ConstraintCount))
    with pytest.raises(NativeSketchError, match="redundant; no constraint was added"):
        _prepared(document, context, _values(_curve_about_line(0)))
    assert (int(sketch.GeometryCount), int(sketch.ConstraintCount)) == before


def test_symmetric_refuses_incomplete_solver_diagnostics(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = {"accepted": True}
    with pytest.raises(NativeSketchError, match="incomplete diagnostics"):
        _prepared(document, context, _values(_curve_about_line(0)))


def test_symmetric_proves_feasibility_has_no_side_effect(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    diagnose = sketch.diagnoseAdditionalConstraints

    def mutating_diagnosis(constraint):
        result = diagnose(constraint)
        sketch.GeometryFacadeList[0].Blocked = True
        return result

    monkeypatch.setattr(sketch, "diagnoseAdditionalConstraints", mutating_diagnosis)
    with pytest.raises(NativeSketchError, match="feasibility check changed"):
        _prepared(document, context, _values(_curve_about_line(0)))


def test_symmetric_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    values = _values(_curve_about_line(0))
    prepared = _prepared(document, context, values)
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Symmetric"):
        create_sketch_symmetric(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, values)
    monkeypatch.setattr(sketch, "_solve_symmetric", lambda _constraint: None)
    draft = create_sketch_symmetric(document, prepared)
    with pytest.raises(NativeSketchError, match="does not satisfy"):
        verify_sketch_symmetric(document, draft)


def test_symmetric_exact_constructors_distinguish_line_and_point(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    captured = []
    original = sketch.diagnoseAdditionalConstraints

    def diagnose(constraint):
        captured.append(constraint)
        return original(constraint)

    monkeypatch.setattr(sketch, "diagnoseAdditionalConstraints", diagnose)
    _prepared(document, context, _values(_curve_about_line(0)))
    assert (
        captured[-1].First,
        captured[-1].FirstPos,
        captured[-1].Second,
        captured[-1].SecondPos,
        captured[-1].Third,
        captured[-1].ThirdPos,
    ) == (0, 1, 0, 2, -1, 0)

    reference = sketch.addGeometry(FakePoint(_point(0.0, 0.0)), False)
    _prepared(
        document,
        context,
        _values(
            _curve_about_point(0, _element(reference, "start")),
            expected_geometry_count=2,
        ),
    )
    assert (
        captured[-1].First,
        captured[-1].FirstPos,
        captured[-1].Second,
        captured[-1].SecondPos,
        captured[-1].Third,
        captured[-1].ThirdPos,
    ) == (0, 1, 0, 2, reference, 1)


def test_constraint_runtime_routes_symmetric_through_exact_transaction(
    monkeypatch,
) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
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
            "operation": "constrain_symmetric",
            **_values(_curve_about_line(0)),
        },
        ticket=None,
    )

    assert captured["transaction_name"] == "Create Native Sketch Symmetric"
    assert result["operation"] == "constrain_symmetric"
    assert result["constraint"]["type"] == "Symmetric"
