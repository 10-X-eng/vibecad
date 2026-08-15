# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy
from types import SimpleNamespace

import pytest

import VibeCADNativeSketchConstraintRuntime as runtime_module
from VibeCADNativeSketchBlock import (
    create_sketch_block,
    preflight_sketch_block,
    prepare_sketch_block,
    verify_sketch_block,
)
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
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
            "selection": [_element(0)] if selection is None else selection,
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_block(
        context,
        prepare_sketch_block(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_block(
        document,
        create_sketch_block(document, prepared),
    )


def _line(start=(0.0, 0.0), end=(5.0, 0.0)) -> FakeLine:
    return FakeLine(_point(*start), _point(*end))


def _constraint(index: int, geometry_index: int) -> dict[str, object]:
    return {
        "index": index,
        "type": "Block",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [{"slot": 1, "geometry_index": geometry_index}],
    }


def test_block_freezes_exact_edge_without_moving_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = _line((1.25, -2.5), (8.75, 4.5))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    before = copy.deepcopy(sketch.Geometry[0])

    result = _apply(document, _prepared(document, context, _values()))

    assert result["operation"] == "constrain_block"
    assert result["constraints"] == [_constraint(0, 0)]
    assert result["frozen_geometry"] == [
        {
            "index": 0,
            "geometry_id": 100,
            "type_id": "Part::GeomLineSegment",
            "kind": "line",
            "construction": False,
            "blocked": True,
        }
    ]
    assert sketch.GeometryFacadeList[0].Blocked is True
    assert (
        sketch.Geometry[0].StartPoint.x,
        sketch.Geometry[0].StartPoint.y,
        sketch.Geometry[0].EndPoint.x,
        sketch.Geometry[0].EndPoint.y,
    ) == (
        before.StartPoint.x,
        before.StartPoint.y,
        before.EndPoint.x,
        before.EndPoint.y,
    )


def test_block_batches_ordered_distinct_edges_atomically(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line((2.0, 3.0), (4.0, 7.0)), False)
    third = sketch.addGeometry(
        FakeCircle(_point(10.0, 3.0), _point(0.0, 0.0), 2.5),
        True,
    )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(third), _element(0), _element(second)],
                expected_geometry_count=3,
            ),
        ),
    )

    assert result["constraints"] == [
        _constraint(0, third),
        _constraint(1, 0),
        _constraint(2, second),
    ]
    assert [item["index"] for item in result["frozen_geometry"]] == [0, second, third]
    assert all(
        sketch.GeometryFacadeList[index].Blocked
        for index in (third, 0, second)
    )


def _edge_families() -> tuple[object, ...]:
    return (
        _line(),
        FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 2.0),
        FakeArc(FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 2.0), 0.2, 1.4),
        FakeEllipse(_point(0.0, 0.0), 4.0, 2.0),
        FakeEllipticalArc(FakeEllipse(_point(0.0, 0.0), 4.0, 2.0), 0.2, 1.4),
        FakeHyperbolicArc(FakeHyperbola(_point(0.0, 0.0), 3.0, 2.0), -0.4, 0.8),
        FakeParabolicArc(
            FakeParabola(_point(2.0, 0.0), _point(0.0, 0.0), None),
            -2.0,
            3.0,
        ),
        FakeBSpline(),
    )


@pytest.mark.parametrize("geometry", _edge_families())
def test_block_supports_every_shipped_edge_family(monkeypatch, geometry) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = geometry
    sketch.GeometryFacadeList[0].Geometry = geometry

    result = _apply(document, _prepared(document, context, _values()))

    assert result["constraints"][0]["type"] == "Block"
    assert result["frozen_geometry"][0]["type_id"] == geometry.TypeId


@pytest.mark.parametrize(
    ("internal_type", "geometry"),
    (
        ("EllipseMajorDiameter", _line()),
        ("EllipseMinorDiameter", _line()),
        ("HyperbolaMajor", _line()),
        ("HyperbolaMinor", _line()),
        ("ParabolaFocalAxis", _line()),
        (
            "BSplineControlPoint",
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 1.0),
        ),
    ),
)
def test_block_supports_human_selectable_internal_edges(
    monkeypatch,
    internal_type,
    geometry,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = geometry
    sketch.GeometryFacadeList[0].Geometry = geometry
    sketch.GeometryFacadeList[0].InternalType = internal_type

    result = _apply(document, _prepared(document, context, _values()))

    assert result["frozen_geometry"][0]["internal_type"] == internal_type
    assert result["frozen_geometry"][0]["blocked"] is True


@pytest.mark.parametrize(
    "selection",
    (
        [_element(-1)],
        [_element(-2)],
        [_element(-3)],
        [_element(0, "start")],
    ),
)
def test_block_refuses_axes_external_geometry_and_vertices(monkeypatch, selection) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    expected_external = 0
    if selection[0]["geometry_index"] == -3:
        sketch.ExternalGeo.append(FakeExternalLine("Support.Edge1"))
        expected_external = 1
    with pytest.raises(NativeSketchError, match="internal whole edges|position"):
        _prepared(
            document,
            context,
            _values(
                selection,
                expected_external_geometry_count=expected_external,
            ),
        )


def test_block_refuses_standalone_and_internal_point_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = FakePoint(_point(1.0, 2.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    with pytest.raises(NativeSketchError, match="cannot target Sketch points"):
        _prepared(document, context, _values())

    sketch.GeometryFacadeList[0].InternalType = "EllipseFocus1"
    with pytest.raises(NativeSketchError, match="internal-alignment"):
        _prepared(document, context, _values())


def test_block_refuses_group_members_but_accepts_exact_group_handle(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    member = sketch.addGeometry(_line((0.0, 2.0), (5.0, 2.0)), False)
    sketch.addConstraint(FakeConstraint("Group", 0, member))
    with pytest.raises(NativeSketchError, match="group handle 0"):
        _prepared(
            document,
            context,
            _values(
                [_element(member)],
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                [_element(0)],
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        ),
    )
    assert result["constraints"] == [_constraint(1, 0)]


def test_block_refuses_duplicate_selection_existing_and_malformed_state(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="distinct"):
        _prepared(document, context, _values([_element(0), _element(0)]))

    sketch.addConstraint(FakeConstraint("Block", 0))
    with pytest.raises(NativeSketchError, match="already has"):
        _prepared(
            document,
            context,
            _values(expected_constraint_count=1),
        )

    sketch.delConstraint(0)
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="without a matching constraint"):
        _prepared(document, context, _values())


@pytest.mark.parametrize(
    "updates",
    (
        {"expected_geometry_count": 0},
        {"expected_constraint_count": 1},
        {"expected_external_geometry_count": 1},
    ),
)
def test_block_refuses_stale_sketch_counts(monkeypatch, updates) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="count changed|count does not match"):
        _prepared(document, context, _values(**updates))


@pytest.mark.parametrize(
    "selection",
    (
        [],
        [_element(index) for index in range(17)],
        [{"geometry_index": 0}],
        [_element(0) | {"extra": True}],
    ),
)
def test_block_refuses_open_or_unbounded_selection_shapes(monkeypatch, selection) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="one through|incorrect fields"):
        _prepared(document, context, _values(selection))


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


def test_block_uses_dedicated_copied_geometry_feasibility(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)

    def wrong_diagnostic(_constraints):
        raise AssertionError("generic diagnostic must not be used for Block")

    monkeypatch.setattr(sketch, "diagnoseAdditionalConstraints", wrong_diagnostic)
    prepared = _prepared(document, context, _values())

    assert prepared.resolved.references[0].geometry_index == 0
    assert sketch.GeometryFacadeList[0].Blocked is False
    assert sketch.ConstraintCount == 0


def test_block_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = _rejected_feasibility()
    before = copy.deepcopy(sketch.Geometry[0])
    with pytest.raises(NativeSketchError, match="redundant; no constraint was added"):
        _prepared(document, context, _values())
    assert sketch.ConstraintCount == 0
    assert sketch.GeometryFacadeList[0].Blocked is False
    assert sketch.Geometry[0].StartPoint.x == before.StartPoint.x


def test_block_refuses_incomplete_or_wrong_solver_diagnostics(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = {"accepted": True}
    with pytest.raises(NativeSketchError, match="incomplete diagnostics"):
        _prepared(document, context, _values())

    sketch.FeasibilityOverride = {
        **_rejected_feasibility(7),
        "accepted": True,
        "degrees_of_freedom": 2,
        "solver_status": 0,
        "redundant_constraint_indices": [],
    }
    with pytest.raises(NativeSketchError, match="exact append"):
        _prepared(document, context, _values())


def test_block_proves_diagnostic_has_no_geometry_side_effect(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    diagnose = sketch.diagnoseBlockConstraints

    def mutating_diagnosis(constraints):
        result = diagnose(constraints)
        sketch.Geometry[0].EndPoint.x += 1.0
        return result

    monkeypatch.setattr(sketch, "diagnoseBlockConstraints", mutating_diagnosis)
    with pytest.raises(NativeSketchError, match="feasibility check changed"):
        _prepared(document, context, _values())


def test_block_refuses_preflight_drift_and_wrong_postconditions(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())
    sketch.Geometry[0].EndPoint.x += 1.0
    with pytest.raises(NativeSketchError, match="after Block"):
        create_sketch_block(document, prepared)

    sketch.Geometry[0].EndPoint.x -= 1.0
    prepared = _prepared(document, context, _values())

    def moving_block(constraint):
        sketch.GeometryFacadeList[constraint.First].Blocked = True
        sketch.Geometry[constraint.First].EndPoint.y += 1.0

    monkeypatch.setattr(sketch, "_solve_block", moving_block)
    draft = create_sketch_block(document, prepared)
    with pytest.raises(NativeSketchError, match="beyond setting"):
        verify_sketch_block(document, draft)


def test_block_refuses_missing_blocked_flag_postcondition(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())
    monkeypatch.setattr(sketch, "_solve_block", lambda _constraint: None)
    draft = create_sketch_block(document, prepared)
    with pytest.raises(NativeSketchError, match="geometry metadata"):
        verify_sketch_block(document, draft)


def test_block_constructs_exact_whole_geometry_constraints(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(_line(), False)
    captured = []
    diagnose = sketch.diagnoseBlockConstraints

    def capture(constraints):
        captured.extend(constraints if isinstance(constraints, list) else [constraints])
        return diagnose(constraints)

    monkeypatch.setattr(sketch, "diagnoseBlockConstraints", capture)
    _prepared(
        document,
        context,
        _values(
            [_element(second), _element(0)],
            expected_geometry_count=2,
        ),
    )

    assert [
        (
            item.Type,
            item.First,
            item.FirstPos,
            item.Second,
            item.Third,
            item.Driving,
            item.IsActive,
        )
        for item in captured
    ] == [
        ("Block", second, 0, -2000, -2000, True, True),
        ("Block", 0, 0, -2000, -2000, True, True),
    ]


def test_constraint_runtime_routes_block_through_one_exact_transaction(
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
        {"operation": "constrain_block", **_values()},
        ticket=None,
    )

    assert captured["transaction_name"] == "Create Native Sketch Block"
    assert result["operation"] == "constrain_block"
    assert result["constraints"] == [_constraint(0, 0)]
