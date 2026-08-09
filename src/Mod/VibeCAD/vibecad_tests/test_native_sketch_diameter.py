# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from VibeCADNativeSketchDiameter import (
    create_sketch_diameter,
    preflight_sketch_diameter,
    prepare_sketch_diameter,
    verify_sketch_diameter,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeArc,
    FakeCircle,
    FakeEllipse,
    FakePoint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


def _circle(radius: float = 4.0) -> FakeCircle:
    return FakeCircle(_point(3.0, 2.0), _point(0.0, 0.0), radius)


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


def _host(monkeypatch, geometry=None):
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(geometry or _circle(), False)
    return document, sketch, context


def _prepared(document, context, values):
    return preflight_sketch_diameter(
        context,
        prepare_sketch_diameter(document.Uid, values),
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


@pytest.mark.parametrize(
    ("geometry", "target_form", "measured_before"),
    (
        (_circle(4.0), "circle_diameter", 8.0),
        (
            FakeArc(_circle(5.0), 0.0, math.pi / 2.0),
            "circular_arc_diameter",
            10.0,
        ),
    ),
)
def test_diameter_creates_diameter_for_exact_circle_or_circular_arc(
    monkeypatch,
    geometry,
    target_form,
    measured_before,
) -> None:
    document, sketch, context = _host(monkeypatch, geometry)
    prepared = _prepared(document, context, _values())

    result = verify_sketch_diameter(
        document,
        create_sketch_diameter(document, prepared),
    )

    assert result["operation"] == "constrain_diameter"
    assert result["target_form"] == target_form
    assert result["measured_before"] == {
        "value": measured_before,
        "unit": "mm",
    }
    assert result["measured_after"] == {"value": 12.0, "unit": "mm"}
    assert result["constraint"] == {
        "index": 0,
        "type": "Diameter",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [{"slot": 1, "geometry_index": 1}],
        "value": 12.0,
        "label_distance": 10.0,
        "label_position": 0.0,
    }
    assert sketch.Geometry[1].Radius == 6.0


@pytest.mark.parametrize(
    "geometry",
    (
        _circle(4.0),
        FakeArc(_circle(4.0), 0.0, math.pi),
    ),
)
def test_diameter_reference_requires_and_retains_exact_measurement(
    monkeypatch,
    geometry,
) -> None:
    document, sketch, context = _host(monkeypatch, geometry)
    prepared = _prepared(
        document,
        context,
        _values(dimension={"value": 8.0, "unit": "mm"}, driving=False),
    )

    result = verify_sketch_diameter(
        document,
        create_sketch_diameter(document, prepared),
    )

    assert result["constraint"]["type"] == "Diameter"
    assert result["constraint"]["driving"] is False
    assert result["measured_before"] == result["measured_after"]
    assert sketch.Geometry[1].Radius == 4.0


def test_diameter_refuses_stale_reference_measurement(monkeypatch) -> None:
    document, _sketch, context = _host(monkeypatch)
    with pytest.raises(NativeSketchError, match="measurement changed"):
        _prepared(
            document,
            context,
            _values(dimension={"value": 9.0, "unit": "mm"}, driving=False),
        )


@pytest.mark.parametrize(
    ("geometry", "selection", "message"),
    (
        (None, [_element(0, "whole")], "does not support whole"),
        (_circle(), [_element(1, "center")], "one exact whole circle"),
        (FakePoint(_point(2.0, 2.0)), [_element(1, "whole")], "does not support whole"),
        (
            FakeEllipse(_point(0.0, 0.0), 4.0, 2.0),
            [_element(1, "whole")],
            "does not support whole",
        ),
        (_circle(), [_element(-2, "whole")], "one exact whole circle"),
    ),
)
def test_diameter_refuses_unsupported_exact_target(
    monkeypatch,
    geometry,
    selection,
    message,
) -> None:
    if geometry is None:
        document, _sketch, context = install_fake_sketch_host(monkeypatch)
        values = _values(expected_geometry_count=1, selection=selection)
    else:
        document, _sketch, context = _host(monkeypatch, geometry)
        values = _values(selection=selection)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(document, context, values)


def test_diameter_refuses_bspline_pole_owned_by_group(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    sketch.GeometryFacadeList[1].InternalType = "BSplineControlPoint"
    with pytest.raises(NativeSketchError, match="internal-alignment geometry"):
        _prepared(document, context, _values())


@pytest.mark.parametrize(
    "updates",
    (
        {"selection": []},
        {"selection": [_element(1, "whole"), _element(0, "whole")]},
        {"selection": [_element(-2000, "whole")]},
        {"selection": [_element(1, "bad")]},
        {"expected_constraint": "diameter"},
        {"dimension": {"value": 5.0, "unit": "deg"}},
        {"dimension": {"value": 0.0, "unit": "mm"}},
        {"dimension": {"value": 1_000_001.0, "unit": "mm"}},
        {"driving": 1},
        {"unexpected": True},
    ),
)
def test_diameter_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = _host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_diameter(document.Uid, _values(**updates))


def test_diameter_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    sketch.FeasibilityOverride = _rejected_feasibility()

    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(document, context, _values())

    assert sketch.ConstraintCount == 0
    assert sketch.Geometry[1].Radius == 4.0


def test_diameter_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = _host(monkeypatch)
    prepared = _prepared(document, context, _values())
    sketch.GeometryFacadeList[1].Blocked = True
    with pytest.raises(NativeSketchError, match="after Diameter preflight"):
        create_sketch_diameter(document, prepared)

    sketch.GeometryFacadeList[1].Blocked = False
    prepared = _prepared(document, context, _values())
    draft = create_sketch_diameter(document, prepared)
    sketch.RedundantConstraints.append(0)
    with pytest.raises(NativeSketchError, match="solver conflict or redundancy"):
        verify_sketch_diameter(document, draft)
