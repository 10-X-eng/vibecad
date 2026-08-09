# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchVertical import (
    create_sketch_vertical,
    preflight_sketch_vertical,
    prepare_sketch_vertical,
    verify_sketch_vertical,
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


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "selection": [_element(0, "whole")],
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_vertical(
        context,
        prepare_sketch_vertical(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_vertical(
        document,
        create_sketch_vertical(document, prepared),
    )


def _replace_line(sketch, end: tuple[float, float]) -> None:
    sketch.Geometry[0] = FakeLine(_point(0.0, 0.0), _point(*end))
    sketch.GeometryFacadeList[0].Geometry = sketch.Geometry[0]


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


def test_vertical_constrains_one_exact_line_without_inference(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _replace_line(sketch, (5.0, 2.0))

    result = _apply(document, _prepared(document, context, _values()))

    assert result["operation"] == "constrain_vertical"
    assert result["target_form"] == "line"
    assert result["alignment"] == "vertical"
    assert result["measured_before"] == {
        "delta_x": 5.0,
        "delta_y": 2.0,
        "unit": "mm",
    }
    assert result["measured_after"] == {
        "delta_x": 0.0,
        "delta_y": 2.0,
        "unit": "mm",
    }
    assert result["constraint"] == {
        "index": 0,
        "type": "Vertical",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [{"slot": 1, "geometry_index": 0}],
    }


def test_vertical_preserves_signed_line_direction(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _replace_line(sketch, (-5.0, -2.0))

    result = _apply(document, _prepared(document, context, _values()))

    assert result["measured_before"]["delta_x"] == -5.0
    assert result["measured_before"]["delta_y"] == -2.0
    assert result["measured_after"]["delta_x"] == 0.0
    assert result["measured_after"]["delta_y"] == -2.0


def test_vertical_constrains_one_ordered_point_pair(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    second = sketch.addGeometry(FakePoint(_point(10.0, 7.0)), False)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                selection=[
                    _element(first, "start"),
                    _element(second, "start"),
                ],
            ),
        ),
    )

    assert result["target_form"] == "point_pair"
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": first, "position": 1},
        {"slot": 2, "geometry_index": second, "position": 1},
    ]
    assert result["measured_after"] == {
        "delta_x": 0.0,
        "delta_y": 4.0,
        "unit": "mm",
    }


def test_vertical_accepts_origin_as_one_point(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(2.0, 5.0)), False)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[_element(point, "start"), _element(-1, "start")],
            ),
        ),
    )

    assert result["constraint"]["references"][1] == {
        "slot": 2,
        "geometry_index": -1,
        "position": 1,
    }
    assert sketch.getPoint(point, 1).x == 0.0


def test_vertical_accepts_external_point_reference(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(2.0, 5.0)), False)
    external = FakeExternalLine("Support.Edge1")
    external.EndPoint = _point(6.0, 0.0)
    sketch.ExternalGeo.append(external)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                expected_external_geometry_count=1,
                selection=[_element(point, "start"), _element(-3, "end")],
            ),
        ),
    )

    assert result["constraint"]["references"][1]["geometry_index"] == -3
    assert sketch.getPoint(point, 1).x == 6.0


def test_vertical_accepts_curve_center_point(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = sketch.addGeometry(
        FakeCircle(_point(2.0, 3.0), _point(0.0, 0.0), 4.0),
        False,
    )
    point = sketch.addGeometry(FakePoint(_point(7.0, 8.0)), False)

    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                selection=[_element(circle, "center"), _element(point, "start")],
            ),
        ),
    )

    assert result["constraint"]["references"][0]["position"] == 3
    assert sketch.getPoint(point, 1).x == 2.0


def test_vertical_refuses_zero_length_line_and_coincident_points(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _replace_line(sketch, (0.0, 0.0))
    with pytest.raises(NativeSketchError, match="zero-length"):
        _prepared(document, context, _values())

    first = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    second = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    with pytest.raises(NativeSketchError, match="coincident points"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                selection=[
                    _element(first, "start"),
                    _element(second, "start"),
                ],
            ),
        )


@pytest.mark.parametrize(
    ("selection", "geometry", "message"),
    (
        ([_element(0, "start")], None, "one whole line"),
        ([_element(-1, "whole")], None, "fixed axis or edge"),
        (
            [_element(1, "whole")],
            FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 3.0),
            "straight line",
        ),
        (
            [_element(0, "whole"), _element(1, "start")],
            FakePoint(_point(4.0, 2.0)),
            "must be one exact point",
        ),
    ),
)
def test_vertical_refuses_wrong_exact_form(
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


@pytest.mark.parametrize("constraint_type", ("Horizontal", "Vertical", "Block"))
def test_vertical_refuses_existing_line_axis_or_block_constraint(
    monkeypatch,
    constraint_type,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _replace_line(sketch, (5.0, 2.0))
    sketch.addConstraint(FakeConstraint(constraint_type, 0))
    with pytest.raises(NativeSketchError, match="already has"):
        _prepared(
            document,
            context,
            _values(expected_constraint_count=1),
        )


def test_vertical_refuses_group_and_internal_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    member = sketch.addGeometry(FakePoint(_point(7.0, 5.0)), False)
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
                selection=[_element(0, "end"), _element(member, "start")],
            ),
        )

    sketch.Constraints.clear()
    sketch.ConstraintCount = 0
    sketch.GeometryFacadeList[member].InternalType = "BSplineControlPoint"
    with pytest.raises(NativeSketchError, match="internal-alignment geometry"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[_element(0, "end"), _element(member, "start")],
            ),
        )


def test_vertical_refuses_missing_point_lookup(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _replace_line(sketch, (5.0, 2.0))
    monkeypatch.setattr(sketch, "getPoint", None)
    with pytest.raises(NativeSketchError, match="point lookup is unavailable"):
        _prepared(document, context, _values())


@pytest.mark.parametrize(
    "updates",
    (
        {"selection": []},
        {"selection": [_element(0, "whole")] * 2},
        {
            "selection": [
                _element(0, "start"),
                _element(0, "end"),
                _element(-1, "start"),
            ]
        },
        {"selection": [_element(-2000, "whole")]},
        {"selection": [_element(0, "bad")]},
        {"expected_inference": "vertical"},
        {"unexpected": True},
    ),
)
def test_vertical_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_vertical(document.Uid, _values(**updates))


def test_vertical_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _replace_line(sketch, (5.0, 2.0))
    sketch.FeasibilityOverride = _rejected_feasibility()
    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(document, context, _values())
    assert sketch.ConstraintCount == 0
    assert sketch.Geometry[0].EndPoint.x == 5.0
    assert sketch.Geometry[0].EndPoint.y == 2.0


def test_vertical_refuses_feasibility_side_effect(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _replace_line(sketch, (5.0, 2.0))
    diagnose = sketch.diagnoseAdditionalConstraints

    def mutating_diagnosis(constraint):
        result = diagnose(constraint)
        sketch.GeometryFacadeList[0].Blocked = True
        return result

    monkeypatch.setattr(sketch, "diagnoseAdditionalConstraints", mutating_diagnosis)
    with pytest.raises(NativeSketchError, match="feasibility check changed"):
        _prepared(document, context, _values())
    assert sketch.ConstraintCount == 0


def test_vertical_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    _replace_line(sketch, (5.0, 2.0))
    prepared = _prepared(document, context, _values())
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Vertical"):
        create_sketch_vertical(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, _values())
    monkeypatch.setattr(sketch, "_solve_horizontal_vertical", lambda _constraint: None)
    draft = create_sketch_vertical(document, prepared)
    with pytest.raises(NativeSketchError, match="does not satisfy"):
        verify_sketch_vertical(document, draft)
