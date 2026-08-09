# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy

import pytest

import VibeCADNativeSketchConstraintRuntime as runtime_module
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
from VibeCADNativeSketchDriving import (
    create_sketch_driving,
    preflight_sketch_driving,
    prepare_sketch_driving,
    verify_sketch_driving,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    FakeExternalLine,
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(targets, **updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_constraint_count": len(targets),
            "expected_external_geometry_count": 0,
            "targets": targets,
            **updates,
        }
    )


def _target(index: int, expected: bool) -> dict[str, object]:
    return {"constraint_index": index, "expected_driving": expected}


def _prepared(document, context, values):
    return preflight_sketch_driving(
        context,
        prepare_sketch_driving(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_driving(
        document,
        create_sketch_driving(document, prepared),
    )


def _add(sketch, constraint_type: str, *, driving: bool, name: str = "") -> int:
    if constraint_type == "Weight":
        constraint = FakeConstraint("Weight", 0, 1.25)
    elif constraint_type in {"DistanceX", "DistanceY"}:
        constraint = FakeConstraint(constraint_type, 0, 1, 2.5)
    elif constraint_type in {"Radius", "Diameter", "Angle"}:
        constraint = FakeConstraint(constraint_type, 0, 2.5)
    elif constraint_type == "SnellsLaw":
        constraint = FakeConstraint(constraint_type, 0)
    else:
        constraint = FakeConstraint(constraint_type, 0, 2.5)
    constraint.Driving = driving
    constraint.Name = name
    return int(sketch.addConstraint(constraint))


def test_driving_toggles_exact_mixed_states_and_removes_only_target_expression(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    assert _add(sketch, "Distance", driving=True) == 0
    assert _add(sketch, "Radius", driving=False, name="MeasuredRadius") == 1
    sketch.ExpressionEngine = [
        ("Constraints[0]", "Spreadsheet.Length"),
        ("Unrelated", "Spreadsheet.Other"),
    ]
    before_geometry = copy.deepcopy(sketch.Geometry)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values([_target(0, True), _target(1, False)]),
        ),
    )

    assert [constraint.Driving for constraint in sketch.Constraints] == [False, True]
    assert sketch.ExpressionEngine == [("Unrelated", "Spreadsheet.Other")]
    assert vars(sketch.Geometry[0].StartPoint) == vars(before_geometry[0].StartPoint)
    assert vars(sketch.Geometry[0].EndPoint) == vars(before_geometry[0].EndPoint)
    assert result["operation"] == "toggle_driving_reference"
    assert result["diagnosed_degrees_of_freedom"] == 4
    assert result["changed_constraints"] == [
        {
            "constraint_index": 0,
            "constraint_type": "Distance",
            "previous_driving": True,
            "current_driving": False,
            "expression_removed": True,
        },
        {
            "constraint_index": 1,
            "constraint_type": "Radius",
            "previous_driving": False,
            "current_driving": True,
            "expression_removed": False,
        },
    ]


@pytest.mark.parametrize(
    "constraint_type",
    (
        "Distance",
        "DistanceX",
        "DistanceY",
        "Radius",
        "Diameter",
        "Angle",
        "SnellsLaw",
        "Weight",
    ),
)
def test_driving_supports_every_host_dimensional_constraint_type(
    monkeypatch,
    constraint_type,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, constraint_type, driving=True)

    result = _apply(
        document,
        _prepared(document, context, _values([_target(0, True)])),
    )

    assert sketch.Constraints[0].Driving is False
    assert result["changed_constraints"][0]["constraint_type"] == constraint_type


def test_driving_supports_inactive_virtual_and_named_constraints(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "DistanceX", driving=True, name="Width")
    _add(sketch, "DistanceY", driving=False, name="Height")
    sketch.Constraints[0].IsActive = False
    sketch.Constraints[1].InVirtualSpace = True
    sketch.ExpressionEngine = [(".Constraints.Width", "Spreadsheet.Width")]

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values([_target(0, True), _target(1, False)]),
        ),
    )

    assert sketch.Constraints[0].IsActive is False
    assert sketch.Constraints[1].InVirtualSpace is True
    assert sketch.ExpressionEngine == []
    assert result["changed_constraints"][0]["expression_removed"] is True


@pytest.mark.parametrize(
    "updates",
    (
        {"targets": []},
        {"targets": [_target(index, True) for index in range(17)]},
        {"targets": [_target(0, True), _target(0, True)]},
        {"targets": [{"constraint_index": 0}]},
        {"targets": [{"constraint_index": True, "expected_driving": True}]},
        {"targets": [{"constraint_index": 0, "expected_driving": 1}]},
        {"expected_external_geometry_count": -1},
        {"unexpected": True},
    ),
)
def test_driving_rejects_open_duplicate_or_unbounded_targets(
    monkeypatch,
    updates,
) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    values = _values([_target(0, True)])
    values.update(updates)
    with pytest.raises(NativeSketchError):
        prepare_sketch_driving(document.Uid, values)


def test_driving_rejects_nondimensional_and_stale_state(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addConstraint(FakeConstraint("Horizontal", 0))
    with pytest.raises(NativeSketchError, match="not dimensional"):
        _prepared(document, context, _values([_target(0, True)]))

    sketch.Constraints.clear()
    sketch.ConstraintCount = 0
    _add(sketch, "Distance", driving=True)
    with pytest.raises(NativeSketchError, match="driving state changed"):
        _prepared(document, context, _values([_target(0, False)]))


def test_driving_rejects_external_only_reference_becoming_driving(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.ExternalGeo.append(FakeExternalLine("Support.Edge1"))
    constraint = FakeConstraint("Distance", -3, 5.0)
    constraint.Driving = False
    sketch.addConstraint(constraint)

    with pytest.raises(NativeSketchError, match="cannot become driving"):
        _prepared(
            document,
            context,
            _values(
                [_target(0, False)],
                expected_external_geometry_count=1,
            ),
        )

    assert sketch.Constraints[0].Driving is False


@pytest.mark.parametrize(
    "updates",
    (
        {"expected_geometry_count": 0},
        {"expected_constraint_count": 2},
        {"expected_external_geometry_count": 1},
    ),
)
def test_driving_rejects_stale_counts(monkeypatch, updates) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    with pytest.raises(NativeSketchError, match="count changed"):
        _prepared(document, context, _values([_target(0, True)], **updates))


def test_driving_rejects_existing_solver_issues_before_diagnosis(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    sketch.RedundantConstraints = [0]

    with pytest.raises(NativeSketchError, match="without current solver issues"):
        _prepared(document, context, _values([_target(0, True)]))


@pytest.mark.parametrize(
    "diagnostic",
    (
        {},
        {
            "accepted": False,
            "degrees_of_freedom": -1,
            "solver_status": 1,
            "conflicting_constraint_indices": [0],
            "redundant_constraint_indices": [],
            "partially_redundant_constraint_indices": [],
            "malformed_constraint_indices": [],
            "constraint_indices": [0],
            "driving_states": [False],
        },
        {
            "accepted": True,
            "degrees_of_freedom": 3,
            "solver_status": 0,
            "conflicting_constraint_indices": [],
            "redundant_constraint_indices": [],
            "partially_redundant_constraint_indices": [],
            "malformed_constraint_indices": [],
            "constraint_indices": [1],
            "driving_states": [False],
        },
        {
            "accepted": True,
            "degrees_of_freedom": 3,
            "solver_status": 0,
            "conflicting_constraint_indices": [0],
            "redundant_constraint_indices": [],
            "partially_redundant_constraint_indices": [],
            "malformed_constraint_indices": [],
            "constraint_indices": [0],
            "driving_states": [False],
        },
    ),
)
def test_driving_rejects_incomplete_refused_wrong_or_inconsistent_diagnostics(
    monkeypatch,
    diagnostic,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    sketch.FeasibilityOverride = diagnostic

    with pytest.raises(NativeSketchError, match="feasibility|solver issue"):
        _prepared(document, context, _values([_target(0, True)]))

    assert sketch.Constraints[0].Driving is True


def test_driving_rejects_diagnostic_side_effect(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    diagnose = sketch.diagnoseDrivingChanges

    def mutating_diagnosis(changes):
        result = diagnose(changes)
        sketch.Constraints[0].LabelDistance = 99.0
        return result

    monkeypatch.setattr(sketch, "diagnoseDrivingChanges", mutating_diagnosis)
    with pytest.raises(NativeSketchError, match="feasibility changed"):
        _prepared(document, context, _values([_target(0, True)]))


@pytest.mark.parametrize(
    "mutation",
    (
        lambda sketch: setattr(sketch.Geometry[0].EndPoint, "x", 99.0),
        lambda sketch: setattr(sketch.Constraints[0], "LabelDistance", 99.0),
        lambda sketch: sketch.ExpressionEngine.append(("Other", "1 mm")),
        lambda sketch: sketch.ConflictingConstraints.append(0),
    ),
)
def test_driving_rejects_any_drift_after_preflight(monkeypatch, mutation) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    prepared = _prepared(document, context, _values([_target(0, True)]))
    mutation(sketch)

    with pytest.raises(NativeSketchError, match="changed after"):
        create_sketch_driving(document, prepared)

    assert sketch.Constraints[0].Driving is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda sketch: setattr(
                sketch.GeometryFacadeList[0], "Construction", True
            ),
            "geometry metadata",
        ),
        (
            lambda sketch: setattr(sketch.Constraints[0], "IsActive", False),
            "constraints beyond",
        ),
        (lambda sketch: sketch.ExpressionEngine.append(("Other", "1 mm")), "expressions"),
        (lambda sketch: sketch.MalformedConstraints.append(0), "solver issue"),
    ),
)
def test_driving_verifier_rejects_unrelated_postcondition_changes(
    monkeypatch,
    mutation,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    draft = create_sketch_driving(
        document,
        _prepared(document, context, _values([_target(0, True)])),
    )
    mutation(sketch)

    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_driving(document, draft)


def test_driving_verifier_allows_solver_shape_updates(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    draft = create_sketch_driving(
        document,
        _prepared(document, context, _values([_target(0, True)])),
    )
    sketch.Geometry[0].EndPoint.x += 3.0

    result = verify_sketch_driving(document, draft)

    assert result["operation"] == "toggle_driving_reference"


def test_driving_rejects_unresolvable_constraint_expression(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    sketch.ExpressionEngine = [("Constraints.Unknown", "1 mm")]

    with pytest.raises(NativeSketchError, match="resolve a constraint expression"):
        _prepared(document, context, _values([_target(0, True)]))


@pytest.mark.parametrize(
    "expression_record",
    (
        ("Other",),
        ("Other", "1 mm", "unexpected"),
        "Other=1 mm",
    ),
)
def test_driving_rejects_malformed_expression_record(
    monkeypatch,
    expression_record,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    sketch.ExpressionEngine = [expression_record]

    with pytest.raises(NativeSketchError, match="malformed expression record"):
        _prepared(document, context, _values([_target(0, True)]))


def test_constraint_runtime_routes_driving_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", driving=True)
    captured = {}

    def run_immediate(runtime_context, **values):
        assert runtime_context is context
        captured.update(values)
        draft = values["mutate"](document)
        return values["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchConstraintRuntime(context).mutate_constraint(
        {
            "operation": "toggle_driving_reference",
            **_values([_target(0, True)]),
        },
        ticket=None,
    )

    assert captured["transaction_name"] == "Toggle Native Sketch Driving/Reference"
    assert result["operation"] == "toggle_driving_reference"
