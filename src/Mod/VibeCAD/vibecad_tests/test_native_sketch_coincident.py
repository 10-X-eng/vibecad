# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math
from types import SimpleNamespace

import pytest

from VibeCADNativeSketchCoincident import (
    create_sketch_coincident,
    preflight_sketch_coincident,
    prepare_sketch_coincident,
    verify_sketch_coincident,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    FakeArc,
    FakeBSpline,
    FakeCircle,
    FakeConstraint,
    FakeEllipse,
    FakeEllipticalArc,
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


def _point_point(
    first_index: int = 0,
    first_position: str = "end",
    second_index: int = 1,
    second_position: str = "start",
) -> dict[str, object]:
    return {
        "form": "point_point",
        "first_point": _element(first_index, first_position),
        "second_point": _element(second_index, second_position),
    }


def _point_on_object(
    point_index: int = 1,
    point_position: str = "start",
    curve_index: int = 0,
    curve_position: str = "whole",
) -> dict[str, object]:
    return {
        "form": "point_on_object",
        "point": _element(point_index, point_position),
        "curve": _element(curve_index, curve_position),
    }


def _concentric(
    first_index: int = 1,
    second_index: int = 2,
    first_position: str = "whole",
    second_position: str = "whole",
) -> dict[str, object]:
    return {
        "form": "concentric",
        "first_curve": _element(first_index, first_position),
        "second_curve": _element(second_index, second_position),
    }


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_geometry_count": 2,
            "expected_external_geometry_count": 0,
            "target": _point_point(),
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_coincident(
        context,
        prepare_sketch_coincident(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_coincident(
        document,
        create_sketch_coincident(document, prepared),
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


def test_coincident_creates_one_exact_point_pair(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(8.0, 3.0)), False)

    result = _apply(document, _prepared(document, context, _values()))

    assert result["operation"] == "constrain_coincident"
    assert result["target_form"] == "point_point"
    assert result["measured_before"] == {
        "satisfied": False,
        "separation": math.sqrt(18.0),
        "unit": "mm",
    }
    assert result["measured_after"] == {
        "satisfied": True,
        "separation": 0.0,
        "unit": "mm",
    }
    assert result["constraint"] == {
        "index": 0,
        "type": "Coincident",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": 0, "position": 2},
            {"slot": 2, "geometry_index": point, "position": 1},
        ],
    }
    assert sketch.getPoint(point, 1).x == 5.0
    assert sketch.getPoint(point, 1).y == 0.0


def test_coincident_point_pair_accepts_origin_as_exact_second_point(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(4.0, -3.0)), False)
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(target=_point_point(point, "start", -1, "start")),
        ),
    )

    assert result["constraint"]["references"][1] == {
        "slot": 2,
        "geometry_index": -1,
        "position": 1,
    }
    assert sketch.getPoint(point, 1).x == 0.0
    assert sketch.getPoint(point, 1).y == 0.0


def test_coincident_accepts_two_endpoints_of_one_bspline(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    spline = sketch.addGeometry(FakeBSpline(), False)
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                target=_point_point(spline, "start", spline, "end"),
            ),
        ),
    )

    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": spline, "position": 1},
        {"slot": 2, "geometry_index": spline, "position": 2},
    ]
    assert result["measured_after"]["satisfied"] is True


def test_coincident_creates_exact_point_on_line(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    result = _apply(
        document,
        _prepared(document, context, _values(target=_point_on_object(point))),
    )

    assert result["target_form"] == "point_on_object"
    assert result["measured_before"] == {"point_on_curve": False}
    assert result["measured_after"] == {"point_on_curve": True}
    assert result["constraint"] == {
        "index": 0,
        "type": "PointOnObject",
        "driving": True,
        "active": True,
        "virtual": False,
        "references": [
            {"slot": 1, "geometry_index": point, "position": 1},
            {"slot": 2, "geometry_index": 0},
        ],
    }
    assert sketch.getPoint(point, 1).x == 2.0
    assert sketch.getPoint(point, 1).y == 0.0


def test_coincident_places_exact_point_on_circle(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    circle = sketch.addGeometry(
        FakeCircle(_point(2.0, 3.0), _point(0.0, 0.0), 4.0),
        False,
    )
    point = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                target=_point_on_object(point, "start", circle),
            ),
        ),
    )

    assert result["measured_after"] == {"point_on_curve": True}
    assert sketch.getPoint(point, 1).x == 6.0
    assert sketch.getPoint(point, 1).y == 3.0


def test_coincident_places_exact_point_on_vertical_axis(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(3.0, 2.0)), False)
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(target=_point_on_object(point, "start", -2)),
        ),
    )

    assert result["constraint"]["references"][1] == {
        "slot": 2,
        "geometry_index": -2,
    }
    assert sketch.getPoint(point, 1).x == 0.0
    assert sketch.getPoint(point, 1).y == 2.0


def test_coincident_places_exact_point_on_external_curve(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    sketch.ExternalGeo.append(FakeExternalLine("Support.Edge1"))
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_external_geometry_count=1,
                target=_point_on_object(point, "start", -3),
            ),
        ),
    )

    assert result["constraint"]["references"][1]["geometry_index"] == -3
    assert result["measured_after"] == {"point_on_curve": True}


@pytest.mark.parametrize("second_kind", ("circle", "arc", "ellipse", "ellipse_arc"))
def test_coincident_creates_exact_concentric_conics(monkeypatch, second_kind) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    first = sketch.addGeometry(
        FakeCircle(_point(1.0, 2.0), _point(0.0, 0.0), 5.0),
        False,
    )
    if second_kind in {"circle", "arc"}:
        base = FakeCircle(_point(7.0, -4.0), _point(0.0, 0.0), 3.0)
        geometry = base if second_kind == "circle" else FakeArc(base, 0.2, 1.8)
    else:
        base = FakeEllipse(_point(7.0, -4.0), 6.0, 2.0)
        geometry = (
            base
            if second_kind == "ellipse"
            else FakeEllipticalArc(base, 0.2, 1.8)
        )
    second = sketch.addGeometry(geometry, False)
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                target=_concentric(first, second),
            ),
        ),
    )

    assert result["target_form"] == "concentric"
    assert result["constraint"]["references"] == [
        {"slot": 1, "geometry_index": first, "position": 3},
        {"slot": 2, "geometry_index": second, "position": 3},
    ]
    assert result["measured_before"] == {
        "satisfied": False,
        "separation": math.sqrt(72.0),
        "unit": "mm",
    }
    assert result["measured_after"] == {
        "satisfied": True,
        "separation": 0.0,
        "unit": "mm",
    }
    assert sketch.getPoint(second, 3).x == 1.0
    assert sketch.getPoint(second, 3).y == 2.0


@pytest.mark.parametrize(
    ("target", "setup", "message"),
    (
        (_point_point(0, "start", 1, "start"), "point_at_origin", "already coincident"),
        (_point_on_object(), "point_on_line", "already lies"),
        (_concentric(), "same_centers", "already concentric"),
    ),
)
def test_coincident_refuses_already_satisfied_target(
    monkeypatch,
    target,
    setup,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    if setup == "point_at_origin":
        sketch.addGeometry(FakePoint(_point(0.0, 0.0)), False)
        expected = 2
    elif setup == "point_on_line":
        sketch.addGeometry(FakePoint(_point(2.0, 0.0)), False)
        expected = 2
    else:
        sketch.addGeometry(
            FakeCircle(_point(1.0, 2.0), _point(0.0, 0.0), 3.0),
            False,
        )
        sketch.addGeometry(FakeEllipse(_point(1.0, 2.0), 5.0, 2.0), False)
        expected = 3
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(expected_geometry_count=expected, target=target),
        )
    assert sketch.ConstraintCount == 0


@pytest.mark.parametrize(
    ("target", "setup", "message"),
    (
        (_point_point(0, "start", 0, "end"), None, "same non-B-spline"),
        (_point_point(0, "whole", 1, "start"), "point", "must be one exact point"),
        (_point_on_object(1, "start", 0, "end"), "point", "whole position"),
        (_point_on_object(0, "start", 0), None, "its own curve"),
        (_point_on_object(1, "start", 2), "two_points", "does not support"),
        (_concentric(0, 1), "point", "only circles"),
        (_concentric(1, 2, "center", "whole"), "two_conics", "whole conic"),
    ),
)
def test_coincident_refuses_wrong_exact_geometry_or_position(
    monkeypatch,
    target,
    setup,
    message,
) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    if setup in {"point", "two_points"}:
        sketch.addGeometry(FakePoint(_point(8.0, 3.0)), False)
    if setup == "two_points":
        sketch.addGeometry(FakePoint(_point(4.0, 6.0)), False)
    if setup == "two_conics":
        sketch.addGeometry(
            FakeCircle(_point(1.0, 2.0), _point(0.0, 0.0), 3.0),
            False,
        )
        sketch.addGeometry(FakeEllipse(_point(5.0, 6.0), 5.0, 2.0), False)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=int(sketch.GeometryCount),
                target=target,
            ),
        )


def test_coincident_refuses_hidden_tangent_replacement(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    second = sketch.addGeometry(
        FakeLine(_point(8.0, 3.0), _point(10.0, 3.0)),
        False,
    )
    sketch.addConstraint(FakeConstraint("Tangent", 0, second))

    with pytest.raises(NativeSketchError, match="replace an existing Tangent"):
        _prepared(
            document,
            context,
            _values(
                expected_constraint_count=1,
                target=_point_point(0, "end", second, "start"),
            ),
        )
    assert sketch.ConstraintCount == 1
    assert sketch.Constraints[0].Type == "Tangent"


def test_coincident_refuses_group_member_and_internal_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    member = sketch.addGeometry(FakePoint(_point(8.0, 3.0)), False)
    sketch.addConstraint(
        FakeConstraint("Text", [0, 0, member, 0], "A", "Font", True)
    )
    with pytest.raises(NativeSketchError, match="group handle"):
        _prepared(
            document,
            context,
            _values(
                expected_constraint_count=1,
                target=_point_point(0, "end", member, "start"),
            ),
        )

    sketch.Constraints.clear()
    sketch.ConstraintCount = 0
    sketch.GeometryFacadeList[member].InternalType = "BSplineControlPoint"
    with pytest.raises(NativeSketchError, match="internal-alignment geometry"):
        _prepared(
            document,
            context,
            _values(target=_point_point(0, "end", member, "start")),
        )


@pytest.mark.parametrize("flag", ("Missing", "Detached"))
def test_coincident_refuses_unavailable_external_geometry(monkeypatch, flag) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(8.0, 3.0)), False)
    external = FakeExternalLine("Support.Edge1")
    external.extension.setFlag(flag, True)
    sketch.ExternalGeo.append(external)
    with pytest.raises(NativeSketchError, match="missing or detached"):
        _prepared(
            document,
            context,
            _values(
                expected_external_geometry_count=1,
                target=_point_point(-3, "start", point, "start"),
            ),
        )


def test_coincident_refuses_missing_point_on_curve_query(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    monkeypatch.setattr(sketch, "isPointOnCurve", None)
    with pytest.raises(NativeSketchError, match="query is unavailable"):
        _prepared(
            document,
            context,
            _values(target=_point_on_object(point)),
        )


@pytest.mark.parametrize(
    "updates",
    (
        {"target": {"form": "point_point"}},
        {"target": {**_point_point(), "curve": _element(0, "whole")}},
        {"target": {**_point_on_object(), "first_point": _element(0, "end")}},
        {"target": {**_concentric(), "form": "multiple"}},
        {"target": {"form": "point_on_object", "point": {}, "curve": {}}},
        {"unexpected": True},
    ),
)
def test_coincident_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_coincident(document.Uid, _values(**updates))


def test_coincident_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(FakePoint(_point(8.0, 3.0)), False)
    sketch.FeasibilityOverride = _rejected_feasibility()

    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(document, context, _values())

    assert sketch.ConstraintCount == 0
    assert sketch.getPoint(1, 1).x == 8.0
    assert sketch.getPoint(1, 1).y == 3.0


def test_coincident_refuses_feasibility_side_effect(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(FakePoint(_point(8.0, 3.0)), False)
    diagnose = sketch.diagnoseAdditionalConstraints

    def mutating_diagnosis(constraint):
        result = diagnose(constraint)
        sketch.GeometryFacadeList[0].Blocked = True
        return result

    monkeypatch.setattr(sketch, "diagnoseAdditionalConstraints", mutating_diagnosis)
    with pytest.raises(NativeSketchError, match="feasibility check changed"):
        _prepared(document, context, _values())
    assert sketch.ConstraintCount == 0


def test_coincident_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.addGeometry(FakePoint(_point(8.0, 3.0)), False)
    prepared = _prepared(document, context, _values())
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Coincident preflight"):
        create_sketch_coincident(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, _values())
    draft = create_sketch_coincident(document, prepared)
    sketch.Constraints[0].SecondPos = 2
    with pytest.raises(NativeSketchError, match="differs from its exact definition"):
        verify_sketch_coincident(document, draft)
