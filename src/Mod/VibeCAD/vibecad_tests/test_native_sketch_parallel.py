# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchParallel import (
    create_sketch_parallel,
    preflight_sketch_parallel,
    prepare_sketch_parallel,
    verify_sketch_parallel,
)
from vibecad_tests.native_sketch_test_support import (
    FakeCircle,
    FakeConstraint,
    FakeExternalLine,
    FakeLine,
    FakePoint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


def _element(index: int, position: str = "whole") -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "selection": [_element(0), _element(1)],
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_parallel(
        context,
        prepare_sketch_parallel(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_parallel(
        document,
        create_sketch_parallel(document, prepared),
    )


def _add_line(sketch, start=(0.0, 3.0), end=(3.0, 7.0)) -> int:
    return sketch.addGeometry(FakeLine(_point(*start), _point(*end)), False)


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


def test_parallel_constrains_one_ordered_internal_line_pair(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        ),
    )

    assert result["operation"] == "constrain_parallel"
    assert result["measured_before"]["angular_error"] > 50.0
    assert result["measured_before"]["unit"] == "deg"
    assert result["measured_after"] == {"angular_error": 0.0, "unit": "deg"}
    assert result["constraint"] == {
        "index": 0,
        "type": "Parallel",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": 0},
            {"slot": 2, "geometry_index": second},
        ],
    }
    assert sketch.Geometry[second].EndPoint.y == 3.0


@pytest.mark.parametrize(
    "selection",
    (
        [_element(0), _element(-1)],
        [_element(-1), _element(0)],
        [_element(0), _element(-3)],
        [_element(-3), _element(0)],
    ),
)
def test_parallel_accepts_one_axis_or_external_line(
    monkeypatch,
    selection,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.Geometry[0] = FakeLine(_point(0.0, 0.0), _point(3.0, 4.0))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]
    external = FakeExternalLine("Support.Edge1")
    external.StartPoint = _point(0.0, 10.0)
    external.EndPoint = _point(5.0, 10.0)
    sketch.ExternalGeo.append(external)
    external_count = 1

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_external_geometry_count=external_count,
                selection=selection,
            ),
        ),
    )

    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": selection[0]["geometry_index"]},
        {"slot": 2, "geometry_index": selection[1]["geometry_index"]},
    ]
    assert result["measured_after"]["angular_error"] == 0.0


def test_parallel_accepts_antiparallel_geometry_without_existing_constraint(
    monkeypatch,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = _add_line(sketch, start=(5.0, 3.0), end=(0.0, 3.0))

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        ),
    )

    assert second == 1
    assert result["measured_before"]["angular_error"] == 0.0
    assert result["constraint"]["type"] == "Parallel"


@pytest.mark.parametrize(
    ("selection", "geometry", "message"),
    (
        ([_element(0)], None, "exactly two lines"),
        ([_element(0), _element(1), _element(2)], None, "exactly two lines"),
        ([_element(0), _element(0)], None, "must be distinct"),
        ([_element(-1), _element(-2)], None, "editable internal line"),
        ([_element(0, "start"), _element(1)], FakeLine(), "whole lines"),
        (
            [_element(0), _element(1)],
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 2.0),
            "straight lines",
        ),
        (
            [_element(0), _element(1)],
            FakePoint(_point(2.0, 3.0)),
            "straight lines",
        ),
    ),
)
def test_parallel_refuses_wrong_exact_targets(
    monkeypatch,
    selection,
    geometry,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    if geometry is not None:
        sketch.addGeometry(geometry, False)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=int(sketch.GeometryCount),
                selection=selection,
            ),
        )


def test_parallel_refuses_zero_length_line(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch, start=(3.0, 3.0), end=(3.0, 3.0))
    with pytest.raises(NativeSketchError, match="zero-length"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )


def test_parallel_refuses_existing_constraint_in_either_order(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
    sketch.addConstraint(FakeConstraint("Parallel", 1, 0))
    with pytest.raises(NativeSketchError, match="already have"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2, expected_constraint_count=1),
        )


def test_parallel_refuses_group_and_internal_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    member = _add_line(sketch)
    sketch.addConstraint(
        FakeConstraint("Text", [0, 0, member, 0], "A", "Font", True)
    )
    with pytest.raises(NativeSketchError, match="group handle"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                expected_constraint_count=1,
            ),
        )

    sketch.Constraints.clear()
    sketch.ConstraintCount = 0
    sketch.GeometryFacadeList[member].InternalType = "BSplineControlPoint"
    with pytest.raises(NativeSketchError, match="internal-alignment geometry"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )


def test_parallel_refuses_missing_host_queries(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
    monkeypatch.setattr(sketch, "getPoint", None)
    with pytest.raises(NativeSketchError, match="point lookup is unavailable"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )

    monkeypatch.undo()
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
    del sketch.Constraints
    with pytest.raises(NativeSketchError, match="constraints are unavailable"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"selection": []},
        {"selection": [_element(0)]},
        {"selection": [_element(0), _element(1), _element(2)]},
        {"selection": [_element(-2000), _element(0)]},
        {"selection": [_element(0, "bad"), _element(1)]},
        {"expected_inference": "parallel"},
        {"unexpected": True},
    ),
)
def test_parallel_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_parallel(document.Uid, _values(**updates))


def test_parallel_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
    sketch.FeasibilityOverride = _rejected_feasibility()
    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )
    assert sketch.ConstraintCount == 0


def test_parallel_refuses_feasibility_side_effect(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
    diagnose = sketch.diagnoseAdditionalConstraints

    def mutating_diagnosis(constraint):
        result = diagnose(constraint)
        sketch.GeometryFacadeList[0].Blocked = True
        return result

    monkeypatch.setattr(sketch, "diagnoseAdditionalConstraints", mutating_diagnosis)
    with pytest.raises(NativeSketchError, match="feasibility check changed"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )
    assert sketch.ConstraintCount == 0


def test_parallel_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _add_line(sketch)
    prepared = _prepared(
        document,
        context,
        _values(expected_geometry_count=2),
    )
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Parallel"):
        create_sketch_parallel(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(
        document,
        context,
        _values(expected_geometry_count=2),
    )
    monkeypatch.setattr(sketch, "_solve_parallel", lambda _constraint: None)
    draft = create_sketch_parallel(document, prepared)
    with pytest.raises(NativeSketchError, match="does not satisfy"):
        verify_sketch_parallel(document, draft)
