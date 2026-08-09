# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeSketchDistanceAxis import SketchAxisDistanceDefinition
from VibeCADNativeSketchDistanceX import (
    create_sketch_horizontal_distance,
    preflight_sketch_horizontal_distance,
    prepare_sketch_horizontal_distance,
)
from VibeCADNativeSketchDistanceY import (
    create_sketch_vertical_distance,
    preflight_sketch_vertical_distance,
    prepare_sketch_vertical_distance,
    verify_sketch_vertical_distance,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeCircle,
    FakeLine,
    FakePoint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


def _vertical_line() -> FakeLine:
    return FakeLine(_point(2.0, -1.0), _point(2.0, 5.0))


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_geometry_count": 2,
            "expected_external_geometry_count": 0,
            "selection": [_element(1, "whole")],
            "dimension": {"value": 12.0, "unit": "mm"},
            "driving": True,
            **updates,
        }
    )


def _host(monkeypatch):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(_vertical_line(), False)
    return document, sketch, context


def _prepared(document, context, values):
    return preflight_sketch_vertical_distance(
        context,
        prepare_sketch_vertical_distance(document.Uid, values),
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


def test_vertical_distance_constrains_exact_whole_line(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, context, _values())

    result = verify_sketch_vertical_distance(
        document,
        create_sketch_vertical_distance(document, prepared),
    )

    assert result["operation"] == "constrain_distance_y"
    assert result["target_form"] == "point_to_point"
    assert result["measured_before"] == {"value": 6.0, "unit": "mm"}
    assert result["measured_after"] == {"value": 12.0, "unit": "mm"}
    assert result["constraint"] == {
        "index": 0,
        "type": "DistanceY",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": 1, "position": 1},
            {"slot": 2, "geometry_index": 1, "position": 2},
        ],
        "value": 12.0,
        "label_distance": 10.0,
        "label_position": 0.0,
    }
    assert sketch.Geometry[1].EndPoint.y == 11.0


def test_vertical_distance_normalizes_reversed_point_order(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    first = sketch.addGeometry(FakePoint(_point(1.0, 10.0)), False)
    second = sketch.addGeometry(FakePoint(_point(4.0, 2.0)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=4,
            selection=[_element(first, "start"), _element(second, "start")],
            dimension={"value": 14.0, "unit": "mm"},
        ),
    )

    assert prepared.resolved.measured_value == 8.0
    assert [item.geometry_index for item in prepared.resolved.references] == [second, first]
    result = verify_sketch_vertical_distance(
        document,
        create_sketch_vertical_distance(document, prepared),
    )
    assert result["measured_after"] == {"value": 14.0, "unit": "mm"}
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": second, "position": 1},
        {"slot": 2, "geometry_index": first, "position": 1},
    ]


@pytest.mark.parametrize("coordinate", (-9.0, 0.0, 18.5))
def test_vertical_distance_supports_signed_point_coordinate(
    monkeypatch,
    coordinate,
) -> None:
    document, sketch, context = _host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(3.0, 4.0)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=3,
            selection=[_element(point, "start")],
            dimension={"value": coordinate, "unit": "mm"},
        ),
    )

    result = verify_sketch_vertical_distance(
        document,
        create_sketch_vertical_distance(document, prepared),
    )

    assert result["target_form"] == "point_coordinate"
    assert result["measured_before"] == {"value": 4.0, "unit": "mm"}
    assert result["measured_after"] == {"value": coordinate, "unit": "mm"}
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": point, "position": 1}
    ]


def test_vertical_reference_requires_and_retains_current_measurement(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(
        document,
        context,
        _values(dimension={"value": 6.0, "unit": "mm"}, driving=False),
    )

    result = verify_sketch_vertical_distance(
        document,
        create_sketch_vertical_distance(document, prepared),
    )

    assert result["constraint"]["driving"] is False
    assert result["measured_before"] == result["measured_after"]
    assert sketch.Geometry[1].EndPoint.y == 5.0


def test_vertical_reference_refuses_stale_measurement(monkeypatch) -> None:
    document, _sketch, context = _host(monkeypatch)
    with pytest.raises(NativeSketchError, match="measurement changed"):
        _prepared(document, context, _values(driving=False))


@pytest.mark.parametrize(
    ("selection", "geometry", "message"),
    (
        ([_element(-2, "whole")], None, "axis as a line"),
        ([_element(-1, "start")], None, "origin to itself"),
        (
            [_element(2, "whole")],
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 5.0),
            "requires one line segment",
        ),
        (
            [_element(1, "whole"), _element(2, "start")],
            FakePoint(_point(8.0, 2.0)),
            "two exact points",
        ),
        (
            [_element(0, "start"), _element(2, "start")],
            FakePoint(_point(3.0, 0.0)),
            "same Y coordinate.*Horizontal geometric",
        ),
    ),
)
def test_vertical_distance_refuses_unsupported_exact_targets(
    monkeypatch,
    selection,
    geometry,
    message,
) -> None:
    document, sketch, context = _host(monkeypatch)
    expected_count = 2
    if geometry is not None:
        sketch.addGeometry(geometry, False)
        expected_count = 3
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=expected_count, selection=selection),
        )


def test_vertical_distance_refuses_nonpositive_two_point_value(monkeypatch) -> None:
    document, _sketch, context = _host(monkeypatch)
    with pytest.raises(NativeSketchError, match="greater than zero"):
        _prepared(document, context, _values(dimension={"value": 0.0, "unit": "mm"}))


@pytest.mark.parametrize(
    "updates",
    (
        {"selection": []},
        {"selection": [_element(1, "whole")] * 2},
        {"selection": [_element(-2000, "whole")]},
        {"selection": [_element(1, "bad")]},
        {"dimension": {"value": 5.0, "unit": "deg"}},
        {"dimension": {"value": -1_000_001.0, "unit": "mm"}},
        {"driving": 1},
        {"unexpected": True},
    ),
)
def test_vertical_distance_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = _host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_vertical_distance(document.Uid, _values(**updates))


def test_vertical_distance_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    sketch.FeasibilityOverride = _rejected_feasibility()

    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(document, context, _values())

    assert sketch.ConstraintCount == 0
    assert sketch.Geometry[1].EndPoint.y == 5.0


def test_vertical_binding_rejects_horizontal_spec(monkeypatch) -> None:
    document, _sketch, context = _host(monkeypatch)
    vertical = prepare_sketch_vertical_distance(document.Uid, _values())
    with pytest.raises(TypeError, match="horizontal Sketch axis distance"):
        preflight_sketch_horizontal_distance(context, vertical)


def test_axis_definition_rejects_crosswired_identity() -> None:
    with pytest.raises(ValueError, match="Invalid Sketch axis-distance definition"):
        SketchAxisDistanceDefinition(
            axis="x",
            title="Crosswired Distance",
            constraint_type="DistanceY",
            operation="constrain_distance_x",
            equal_coordinate_constraint="Vertical",
        )


def test_vertical_binding_rejects_horizontal_preflight_create_and_verify(
    monkeypatch,
) -> None:
    document, _sketch, context = _host(monkeypatch)
    horizontal = prepare_sketch_horizontal_distance(
        document.Uid,
        _values(selection=[_element(0, "start")]),
    )
    with pytest.raises(TypeError, match="vertical Sketch axis distance"):
        preflight_sketch_vertical_distance(context, horizontal)

    prepared = preflight_sketch_horizontal_distance(context, horizontal)
    with pytest.raises(TypeError, match="vertical Sketch axis distance"):
        create_sketch_vertical_distance(document, prepared)

    draft = create_sketch_horizontal_distance(document, prepared)
    with pytest.raises(TypeError, match="vertical Sketch axis distance"):
        verify_sketch_vertical_distance(document, draft)
