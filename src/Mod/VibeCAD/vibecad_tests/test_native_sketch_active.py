# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import copy

import pytest

import VibeCADNativeSketchConstraintRuntime as runtime_module
import VibeCADNativeSketchActiveState as active_state_module
from VibeCADNativeSketchActive import (
    create_sketch_active,
    preflight_sketch_active,
    prepare_sketch_active,
    verify_sketch_active,
)
from VibeCADNativeSketchConstraintRuntime import NativeSketchConstraintRuntime
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _target(index: int, expected: bool) -> dict[str, object]:
    return {"constraint_index": index, "expected_active": expected}


def _values(targets, *, constraint_count=None, **updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_constraint_count": (
                len(targets) if constraint_count is None else constraint_count
            ),
            "expected_external_geometry_count": 0,
            "targets": targets,
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_active(
        context,
        prepare_sketch_active(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_active(
        document,
        create_sketch_active(document, prepared),
    )


def _constraint(constraint_type: str) -> FakeConstraint:
    if constraint_type == "Distance":
        return FakeConstraint("Distance", 0, 5.0)
    if constraint_type == "Coincident":
        return FakeConstraint("Coincident", 0, 1, 0, 2)
    if constraint_type == "Block":
        return FakeConstraint("Block", 0)
    if constraint_type == "Group":
        return FakeConstraint("Group", [0, 0, 0, 0, 0, 0])
    if constraint_type == "Text":
        return FakeConstraint("Text", [0, 0], "Label", "/tmp/font.ttf", True)
    if constraint_type == "InternalAlignment":
        return FakeConstraint("InternalAlignment::EllipseMajorDiameter", 0, 0, 0, 0)
    return FakeConstraint(constraint_type, 0)


def _add(sketch, constraint_type: str, *, active: bool = True) -> int:
    constraint = _constraint(constraint_type)
    constraint.IsActive = active
    return int(sketch.addConstraint(constraint))


def test_active_toggles_exact_mixed_states_and_preserves_expression(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", active=True)
    _add(sketch, "Horizontal", active=False)
    sketch.Constraints[0].Name = "Length"
    sketch.Constraints[0].Driving = False
    sketch.Constraints[1].InVirtualSpace = True
    sketch.ExpressionEngine = [(".Constraints.Length", "Spreadsheet.Length")]
    before_geometry = copy.deepcopy(sketch.Geometry)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values([_target(0, True), _target(1, False)]),
        ),
    )

    assert [constraint.IsActive for constraint in sketch.Constraints] == [False, True]
    assert sketch.Constraints[0].Driving is False
    assert sketch.Constraints[1].InVirtualSpace is True
    assert sketch.ExpressionEngine == [(".Constraints.Length", "Spreadsheet.Length")]
    assert vars(sketch.Geometry[0].StartPoint) == vars(before_geometry[0].StartPoint)
    assert vars(sketch.Geometry[0].EndPoint) == vars(before_geometry[0].EndPoint)
    assert result["operation"] == "toggle_active_inactive"
    assert result["diagnosed_degrees_of_freedom"] == 4
    assert result["changed_constraints"] == [
        {
            "constraint_index": 0,
            "constraint_type": "Distance",
            "previous_active": True,
            "current_active": False,
        },
        {
            "constraint_index": 1,
            "constraint_type": "Horizontal",
            "previous_active": False,
            "current_active": True,
        },
    ]


@pytest.mark.parametrize(
    "constraint_type",
    (
        "Distance",
        "Horizontal",
        "Coincident",
        "Block",
        "Group",
        "Text",
        "InternalAlignment",
    ),
)
def test_active_supports_every_constraint_semantic_category(
    monkeypatch,
    constraint_type,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, constraint_type)

    result = _apply(
        document,
        _prepared(document, context, _values([_target(0, True)])),
    )

    assert sketch.Constraints[0].IsActive is False
    assert result["changed_constraints"][0]["constraint_type"] == constraint_type


def test_active_accepts_full_sixteen_target_batch(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    for _index in range(16):
        _add(sketch, "Horizontal")

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values([_target(index, True) for index in range(16)]),
        ),
    )

    assert all(constraint.IsActive is False for constraint in sketch.Constraints)
    assert len(result["changed_constraints"]) == 16


@pytest.mark.parametrize(
    "updates",
    (
        {"targets": []},
        {"targets": [_target(index, True) for index in range(17)]},
        {"targets": [_target(0, True), _target(0, True)]},
        {"targets": [{"constraint_index": 0}]},
        {"targets": [{"constraint_index": True, "expected_active": True}]},
        {"targets": [{"constraint_index": 0, "expected_active": 1}]},
        {"expected_external_geometry_count": -1},
        {"unexpected": True},
    ),
)
def test_active_rejects_open_duplicate_or_unbounded_targets(
    monkeypatch,
    updates,
) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    values = _values([_target(0, True)])
    values.update(updates)
    with pytest.raises(NativeSketchError):
        prepare_sketch_active(document.Uid, values)


def test_active_rejects_stale_state_or_constraint_type(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Horizontal")
    with pytest.raises(NativeSketchError, match="active state changed"):
        _prepared(document, context, _values([_target(0, False)]))

    records_by_index = active_state_module.constraint_records_by_index

    def inconsistent_type(records):
        result = records_by_index(records)
        result[0] = {**result[0], "type": "Vertical"}
        return result

    monkeypatch.setattr(
        active_state_module,
        "constraint_records_by_index",
        inconsistent_type,
    )
    with pytest.raises(NativeSketchError, match="type is inconsistent"):
        _prepared(document, context, _values([_target(0, True)]))


@pytest.mark.parametrize(
    "updates",
    (
        {"expected_geometry_count": 0},
        {"expected_constraint_count": 2},
        {"expected_external_geometry_count": 1},
    ),
)
def test_active_rejects_stale_counts(monkeypatch, updates) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Horizontal")
    with pytest.raises(NativeSketchError, match="count changed"):
        _prepared(document, context, _values([_target(0, True)], **updates))


def test_active_rejects_existing_solver_issues_before_diagnosis(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Horizontal")
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
            "active_states": [False],
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
            "active_states": [False],
        },
        {
            "accepted": True,
            "degrees_of_freedom": 3,
            "solver_status": 0,
            "conflicting_constraint_indices": [],
            "redundant_constraint_indices": [0],
            "partially_redundant_constraint_indices": [],
            "malformed_constraint_indices": [],
            "constraint_indices": [0],
            "active_states": [False],
        },
    ),
)
def test_active_rejects_incomplete_refused_wrong_or_inconsistent_diagnostics(
    monkeypatch,
    diagnostic,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Horizontal")
    sketch.FeasibilityOverride = diagnostic

    with pytest.raises(NativeSketchError, match="feasibility|solver issue"):
        _prepared(document, context, _values([_target(0, True)]))

    assert sketch.Constraints[0].IsActive is True


def test_active_rejects_diagnostic_side_effect(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance")
    diagnose = sketch.diagnoseActiveChanges

    def mutating_diagnosis(changes):
        result = diagnose(changes)
        sketch.Constraints[0].LabelDistance = 99.0
        return result

    monkeypatch.setattr(sketch, "diagnoseActiveChanges", mutating_diagnosis)
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
def test_active_rejects_any_drift_after_preflight(monkeypatch, mutation) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance")
    prepared = _prepared(document, context, _values([_target(0, True)]))
    mutation(sketch)

    with pytest.raises(NativeSketchError, match="changed after"):
        create_sketch_active(document, prepared)

    assert sketch.Constraints[0].IsActive is True


@pytest.mark.parametrize(
    ("mutation", "message"),
    (
        (
            lambda sketch: setattr(sketch.GeometryFacadeList[0], "Construction", True),
            "geometry metadata",
        ),
        (
            lambda sketch: setattr(sketch.Constraints[0], "Driving", False),
            "constraints beyond",
        ),
        (
            lambda sketch: sketch.ExpressionEngine.append(("Other", "1 mm")),
            "expressions",
        ),
        (lambda sketch: sketch.MalformedConstraints.append(0), "solver issue"),
    ),
)
def test_active_verifier_rejects_unrelated_postcondition_changes(
    monkeypatch,
    mutation,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance")
    draft = create_sketch_active(
        document,
        _prepared(document, context, _values([_target(0, True)])),
    )
    mutation(sketch)

    with pytest.raises(NativeSketchError, match=message):
        verify_sketch_active(document, draft)


def test_active_verifier_allows_solver_shape_updates(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Distance", active=False)
    draft = create_sketch_active(
        document,
        _prepared(document, context, _values([_target(0, False)])),
    )
    sketch.Geometry[0].EndPoint.x += 3.0

    result = verify_sketch_active(document, draft)

    assert result["operation"] == "toggle_active_inactive"


def test_constraint_runtime_routes_active_through_one_exact_transaction(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add(sketch, "Horizontal")
    captured = {}

    def run_immediate(runtime_context, **values):
        assert runtime_context is context
        captured.update(values)
        draft = values["mutate"](document)
        return values["verify"](document, draft)

    monkeypatch.setattr(runtime_module, "run_immediate_mutation", run_immediate)
    result = NativeSketchConstraintRuntime(context).mutate_constraint(
        {
            "operation": "toggle_active_inactive",
            **_values([_target(0, True)]),
        },
        ticket=None,
    )

    assert captured["transaction_name"] == "Toggle Native Sketch Active/Inactive"
    assert result["operation"] == "toggle_active_inactive"
