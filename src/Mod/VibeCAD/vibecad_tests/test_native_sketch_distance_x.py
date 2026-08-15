# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeSketchDistanceX import (
    create_sketch_horizontal_distance,
    preflight_sketch_horizontal_distance,
    prepare_sketch_horizontal_distance,
    verify_sketch_horizontal_distance,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeCircle,
    FakeConstraint,
    FakeExternalLine,
    FakeLine,
    FakePoint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "selection": [_element(0, "whole")],
            "dimension": {"value": 12.0, "unit": "mm"},
            "driving": True,
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_horizontal_distance(
        context,
        prepare_sketch_horizontal_distance(document.Uid, values),
    )


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


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


def test_horizontal_distance_constrains_exact_whole_line(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())

    result = verify_sketch_horizontal_distance(
        document,
        create_sketch_horizontal_distance(document, prepared),
    )

    assert result["operation"] == "constrain_distance_x"
    assert result["target_form"] == "point_to_point"
    assert result["measured_before"] == {"value": 5.0, "unit": "mm"}
    assert result["measured_after"] == {"value": 12.0, "unit": "mm"}
    assert result["constraint"] == {
        "index": 0,
        "type": "DistanceX",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": 0, "position": 1},
            {"slot": 2, "geometry_index": 0, "position": 2},
        ],
        "value": 12.0,
        "label_distance": 10.0,
        "label_position": 0.0,
    }
    assert sketch.Geometry[0].EndPoint.x == 12.0


def test_horizontal_distance_normalizes_reversed_point_order(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = sketch.addGeometry(FakePoint(_point(10.0, 1.0)), False)
    second = sketch.addGeometry(FakePoint(_point(2.0, 5.0)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=3,
            selection=[_element(first, "start"), _element(second, "start")],
            dimension={"value": 14.0, "unit": "mm"},
        ),
    )

    assert prepared.resolved.measured_value == 8.0
    assert [item.geometry_index for item in prepared.resolved.references] == [second, first]
    result = verify_sketch_horizontal_distance(
        document,
        create_sketch_horizontal_distance(document, prepared),
    )

    assert result["measured_after"] == {"value": 14.0, "unit": "mm"}
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": second, "position": 1},
        {"slot": 2, "geometry_index": first, "position": 1},
    ]


@pytest.mark.parametrize("coordinate", (-8.0, 0.0, 17.5))
def test_horizontal_distance_supports_signed_point_coordinate(
    monkeypatch,
    coordinate,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(3.0, 2.0)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(point, "start")],
            dimension={"value": coordinate, "unit": "mm"},
        ),
    )

    result = verify_sketch_horizontal_distance(
        document,
        create_sketch_horizontal_distance(document, prepared),
    )

    assert result["target_form"] == "point_coordinate"
    assert result["measured_before"] == {"value": 3.0, "unit": "mm"}
    assert result["measured_after"] == {"value": coordinate, "unit": "mm"}
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": point, "position": 1}
    ]


def test_horizontal_reference_requires_and_retains_current_measurement(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(
        document,
        context,
        _values(dimension={"value": 5.0, "unit": "mm"}, driving=False),
    )

    result = verify_sketch_horizontal_distance(
        document,
        create_sketch_horizontal_distance(document, prepared),
    )

    assert result["constraint"]["driving"] is False
    assert result["measured_before"] == result["measured_after"]
    assert sketch.Geometry[0].EndPoint.x == 5.0


def test_horizontal_reference_refuses_stale_measurement(monkeypatch) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="measurement changed"):
        _prepared(document, context, _values(driving=False))


def test_horizontal_distance_accepts_exact_external_line_as_reference(monkeypatch) -> None:
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

    result = verify_sketch_horizontal_distance(
        document,
        create_sketch_horizontal_distance(document, prepared),
    )
    assert result["constraint"]["driving"] is False
    assert result["constraint"]["references"][0]["geometry_index"] == -3


@pytest.mark.parametrize(
    ("selection", "geometry", "message"),
    (
        ([_element(-1, "whole")], None, "axis as a line"),
        ([_element(-1, "start")], None, "origin to itself"),
        (
            [_element(1, "whole")],
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 5.0),
            "requires one line segment",
        ),
        (
            [_element(0, "whole"), _element(1, "start")],
            FakePoint(_point(8.0, 2.0)),
            "two exact points",
        ),
        (
            [_element(0, "start"), _element(1, "start")],
            FakePoint(_point(0.0, 2.0)),
            "same X coordinate",
        ),
    ),
)
def test_horizontal_distance_refuses_unsupported_exact_targets(
    monkeypatch,
    selection,
    geometry,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    expected_count = 1
    if geometry is not None:
        sketch.addGeometry(geometry, False)
        expected_count = 2
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=expected_count, selection=selection),
        )


def test_horizontal_distance_refuses_nonpositive_two_point_value(monkeypatch) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="greater than zero"):
        _prepared(document, context, _values(dimension={"value": 0.0, "unit": "mm"}))


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
def test_horizontal_distance_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_horizontal_distance(document.Uid, _values(**updates))


def test_horizontal_distance_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = _rejected_feasibility()

    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(document, context, _values())

    assert sketch.ConstraintCount == 0
    assert sketch.Constraints == []
    assert sketch.Geometry[0].EndPoint.x == 5.0


def test_horizontal_distance_refuses_invalid_feasibility_result(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = {"accepted": True}
    with pytest.raises(NativeSketchError, match="incomplete diagnostics"):
        _prepared(document, context, _values())


def test_horizontal_distance_refuses_feasibility_state_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    original = sketch.diagnoseAdditionalConstraints

    def drift(constraint):
        result = original(constraint)
        sketch.GeometryFacadeList[0].Blocked = True
        return result

    sketch.diagnoseAdditionalConstraints = drift
    with pytest.raises(NativeSketchError, match="feasibility check changed"):
        _prepared(document, context, _values())


def test_horizontal_distance_refuses_mutation_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Horizontal Distance preflight"):
        create_sketch_horizontal_distance(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, _values())
    draft = create_sketch_horizontal_distance(document, prepared)
    sketch.Geometry[0].EndPoint.x = 7.0
    with pytest.raises(NativeSketchError, match="does not satisfy"):
        verify_sketch_horizontal_distance(document, draft)


def test_horizontal_distance_refuses_new_solver_issue(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())
    draft = create_sketch_horizontal_distance(document, prepared)
    sketch.RedundantConstraints.append(0)
    with pytest.raises(NativeSketchError, match="solver conflict or redundancy"):
        verify_sketch_horizontal_distance(document, draft)


def test_horizontal_distance_refuses_group_member(monkeypatch) -> None:
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
