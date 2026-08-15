# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from VibeCADNativeSketchDimension import (
    create_sketch_dimension,
    preflight_sketch_dimension,
    prepare_sketch_dimension,
    verify_sketch_dimension,
)
from VibeCADNativeSketchConstraintTargets import SketchConstraintElement
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeArc,
    FakeCircle,
    FakeConstraint,
    FakeEllipse,
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
            "expected_inference": "distance_x",
            "dimension": {"value": 10.0, "unit": "mm"},
            "driving": True,
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_dimension(
        context,
        prepare_sketch_dimension(document.Uid, values),
    )


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


def test_dimension_creates_exact_horizontal_line_dimension(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())

    draft = create_sketch_dimension(document, prepared)
    result = verify_sketch_dimension(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert result["operation"] == "infer_dimension"
    assert result["inference"] == "distance_x"
    assert result["measured_before"] == {"value": 5.0, "unit": "mm"}
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
        "value": 10.0,
        "label_distance": 10.0,
        "label_position": 0.0,
    }


def test_dimension_reference_requires_and_retains_current_measurement(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    values = _values(dimension={"value": 5.0, "unit": "mm"}, driving=False)
    prepared = _prepared(document, context, values)

    result = verify_sketch_dimension(
        document,
        create_sketch_dimension(document, prepared),
    )

    assert result["constraint"]["driving"] is False
    assert sketch.Constraints[0].Driving is False


@pytest.mark.parametrize(
    ("geometry", "selection", "expected", "measured"),
    (
        (
            FakeLine(_point(2.0, 8.0), _point(2.0, -3.0)),
            [_element(1, "whole")],
            "distance_y",
            11.0,
        ),
        (
            FakePoint(_point(7.0, 0.0)),
            [_element(1, "start")],
            "distance_x",
            7.0,
        ),
        (
            FakePoint(_point(0.0, -4.0)),
            [_element(1, "start")],
            "distance_y",
            4.0,
        ),
        (
            FakePoint(_point(2.0, 3.0)),
            [_element(1, "start"), _element(0, "whole")],
            "distance",
            3.0,
        ),
    ),
)
def test_dimension_infers_axis_and_point_line_forms(
    monkeypatch,
    geometry,
    selection,
    expected,
    measured,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(geometry, False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=selection,
            expected_inference=expected,
            dimension={"value": measured, "unit": "mm"},
            driving=False,
        ),
    )

    assert prepared.inferred.inference == expected
    assert math.isclose(prepared.inferred.measured_value, measured, abs_tol=1.0e-10)


def test_dimension_infers_line_angle_with_human_endpoint_orientation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(FakeLine(_point(0.0, 0.0), _point(0.0, 5.0)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(0, "whole"), _element(second, "whole")],
            expected_inference="angle",
            dimension={"value": 90.0, "unit": "deg"},
        ),
    )

    assert prepared.inferred.inference == "angle"
    assert prepared.inferred.references == (
        SketchConstraintElement(0, "start"),
        SketchConstraintElement(1, "start"),
    )
    result = verify_sketch_dimension(
        document,
        create_sketch_dimension(document, prepared),
    )
    assert result["constraint"]["type"] == "Angle"
    assert math.isclose(result["constraint"]["value"], math.pi / 2.0)


def test_dimension_infers_parallel_line_distance(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(FakeLine(_point(0.0, 4.0), _point(5.0, 4.0)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(0, "whole"), _element(second, "whole")],
            expected_inference="distance",
            dimension={"value": 4.0, "unit": "mm"},
            driving=False,
        ),
    )

    assert prepared.inferred.constructor_form == "point_curve"
    assert prepared.inferred.references[0].geometry_index == second
    assert prepared.inferred.references[0].position == "start"


def test_dimension_infers_line_to_axis_angle_but_refuses_axis_length(monkeypatch) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(
        document,
        context,
        _values(
            selection=[_element(0, "whole"), _element(-2, "whole")],
            expected_inference="angle",
            dimension={"value": 90.0, "unit": "deg"},
        ),
    )
    assert prepared.inferred.inference == "angle"
    assert math.isclose(prepared.inferred.measured_value, 90.0)

    with pytest.raises(NativeSketchError, match="length dimension on an axis"):
        _prepared(
            document,
            context,
            _values(selection=[_element(-1, "whole")]),
        )


@pytest.mark.parametrize("circle_kind", ("point", "line", "circle"))
def test_dimension_infers_circle_distance_forms(monkeypatch, circle_kind) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = sketch.addGeometry(
        FakeCircle(_point(0.0, 10.0), _point(0.0, 0.0), 2.0),
        False,
    )
    if circle_kind == "point":
        other = sketch.addGeometry(FakePoint(_point(0.0, 15.0)), False)
        selection = [_element(other, "start"), _element(circle, "whole")]
        measured = 3.0
    elif circle_kind == "line":
        selection = [_element(0, "whole"), _element(circle, "whole")]
        measured = 8.0
    else:
        other = sketch.addGeometry(
            FakeCircle(_point(0.0, 20.0), _point(0.0, 0.0), 3.0),
            False,
        )
        selection = [_element(circle, "whole"), _element(other, "whole")]
        measured = 5.0
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=int(sketch.GeometryCount),
            selection=selection,
            expected_inference="distance",
            dimension={"value": measured, "unit": "mm"},
            driving=False,
        ),
    )

    assert math.isclose(prepared.inferred.measured_value, measured)


@pytest.mark.parametrize(
    ("selection", "geometry", "message"),
    (
        ([_element(1, "whole")], FakeLine(_point(0.0, 0.0), _point(4.0, 3.0)), "explicit Distance"),
        ([_element(1, "whole")], FakeCircle(_point(2.0, 2.0), _point(0.0, 0.0), 2.0), "radius/diameter"),
        ([_element(1, "whole")], FakeEllipse(_point(0.0, 0.0), 4.0, 2.0), "does not infer"),
        ([_element(1, "start")], FakePoint(_point(0.0, 0.0)), "coincident"),
    ),
)
def test_dimension_refuses_ambiguous_or_unsupported_single_selection(
    monkeypatch,
    selection,
    geometry,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(geometry, False)

    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2, selection=selection),
        )


def test_dimension_refuses_collinear_lines_and_zero_curve_distance(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    collinear = sketch.addGeometry(
        FakeLine(_point(8.0, 0.0), _point(12.0, 0.0)),
        False,
    )

    with pytest.raises(NativeSketchError, match="point-to-curve distance is zero"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[_element(0, "whole"), _element(collinear, "whole")],
                expected_inference="distance",
            ),
        )


def test_dimension_refuses_stale_inference_and_reference_measurement(monkeypatch) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="inferred distance_x"):
        _prepared(
            document,
            context,
            _values(
                expected_inference="distance_y",
                dimension={"value": 5.0, "unit": "mm"},
            ),
        )
    with pytest.raises(NativeSketchError, match="measurement changed"):
        _prepared(
            document,
            context,
            _values(dimension={"value": 4.0, "unit": "mm"}, driving=False),
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"selection": []},
        {"selection": [_element(0, "whole")] * 2},
        {"selection": [_element(-2000, "whole")]},
        {"selection": [_element(0, "bad")]},
        {"expected_inference": "radius"},
        {"dimension": {"value": 5.0, "unit": "deg"}},
        {"dimension": {"value": 0.0, "unit": "mm"}},
        {"driving": 1},
        {"unexpected": True},
    ),
)
def test_dimension_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_dimension(document.Uid, _values(**updates))


def test_dimension_rejects_group_member_internal_alignment_and_external_drift(
    monkeypatch,
) -> None:
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
    sketch.Constraints.clear()
    sketch.ConstraintCount = 0
    sketch.GeometryFacadeList[member].InternalType = "BSplineControlPoint"
    with pytest.raises(NativeSketchError, match="internal-alignment"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                selection=[_element(member, "whole")],
            ),
        )
    sketch.ExternalGeo.append(FakeExternalLine("Support.Edge1"))
    with pytest.raises(NativeSketchError, match="external geometry count changed"):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=2),
        )


def test_dimension_rejects_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Dimension preflight"):
        create_sketch_dimension(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, _values())
    draft = create_sketch_dimension(document, prepared)
    sketch.RedundantConstraints.append(0)
    with pytest.raises(NativeSketchError, match="solver conflict or redundancy"):
        verify_sketch_dimension(document, draft)


def test_dimension_accepts_exact_external_line_target(monkeypatch) -> None:
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

    assert prepared.inferred.inference == "distance_x"
    assert prepared.spec.driving is False


def test_dimension_arc_endpoint_is_an_exact_point_target(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = FakeCircle(_point(0.0, 0.0), _point(0.0, 0.0), 5.0)
    arc = sketch.addGeometry(FakeArc(circle, 0.0, math.pi / 2.0), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            selection=[_element(arc, "start")],
            dimension={"value": 5.0, "unit": "mm"},
            driving=False,
        ),
    )

    assert prepared.inferred.inference == "distance_x"
