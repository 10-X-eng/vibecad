# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchLock import (
    create_sketch_lock,
    preflight_sketch_lock,
    prepare_sketch_lock,
    verify_sketch_lock,
)
from vibecad_tests.native_sketch_test_support import (
    FakeConstraint,
    FakeExternalLine,
    FakePoint,
    geometry_target_values,
    install_fake_sketch_host,
)


def _element(index: int, position: str) -> dict[str, object]:
    return {"geometry_index": index, "position": position}


def _point(x: float, y: float) -> SimpleNamespace:
    return SimpleNamespace(x=x, y=y, z=0.0)


def _absolute(
    index: int = 0,
    position: str = "end",
    *,
    x: float = 5.0,
    y: float = 0.0,
) -> dict[str, object]:
    return {
        "form": "absolute",
        "point": _element(index, position),
        "expected_position_mm": {"x": x, "y": y},
    }


def _relative(
    point_index: int = 0,
    point_position: str = "end",
    reference_index: int = 0,
    reference_position: str = "start",
    *,
    x: float = -5.0,
    y: float = 0.0,
) -> dict[str, object]:
    return {
        "form": "relative",
        "point": _element(point_index, point_position),
        "reference": _element(reference_index, reference_position),
        "expected_offset_mm": {"x": x, "y": y},
    }


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "expected_external_geometry_count": 0,
            "target": _absolute(),
            "driving": True,
            **updates,
        }
    )


def _prepared(document, context, values):
    return preflight_sketch_lock(
        context,
        prepare_sketch_lock(document.Uid, values),
    )


def _apply(document, prepared):
    return verify_sketch_lock(
        document,
        create_sketch_lock(document, prepared),
    )


def _rejected_feasibility(index: int = 0) -> dict[str, object]:
    return {
        "accepted": False,
        "degrees_of_freedom": -1,
        "solver_status": -2,
        "first_proposed_constraint_index": index,
        "proposed_constraint_count": 2,
        "conflicting_constraint_indices": [],
        "redundant_constraint_indices": [index],
        "partially_redundant_constraint_indices": [],
        "malformed_constraint_indices": [],
    }


def test_lock_creates_exact_absolute_driving_constraint_pair(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)

    result = _apply(document, _prepared(document, context, _values()))

    assert result["operation"] == "constrain_lock"
    assert result["target_form"] == "absolute"
    assert result["measured_before"] == {"x": 5.0, "y": 0.0, "unit": "mm"}
    assert result["measured_after"] == result["measured_before"]
    assert [constraint["index"] for constraint in result["constraints"]] == [0, 1]
    assert [constraint["type"] for constraint in result["constraints"]] == [
        "DistanceX",
        "DistanceY",
    ]
    assert [constraint["value"] for constraint in result["constraints"]] == [
        5.0,
        0.0,
    ]
    assert all(
        constraint["references"]
        == [{"slot": 1, "geometry_index": 0, "position": 2}]
        for constraint in result["constraints"]
    )
    assert all(constraint["driving"] is True for constraint in result["constraints"])
    assert sketch.Geometry[0].EndPoint.x == 5.0
    assert sketch.Geometry[0].EndPoint.y == 0.0


def test_lock_absolute_preserves_signed_standalone_point_position(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(-7.5, 12.25)), False)
    prepared = _prepared(
        document,
        context,
        _values(
            expected_geometry_count=2,
            target=_absolute(point, "start", x=-7.5, y=12.25),
        ),
    )

    result = _apply(document, prepared)

    assert result["measured_after"] == {"x": -7.5, "y": 12.25, "unit": "mm"}
    assert [constraint["value"] for constraint in result["constraints"]] == [
        -7.5,
        12.25,
    ]


def test_lock_absolute_creates_exact_reference_pair(monkeypatch) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    result = _apply(
        document,
        _prepared(document, context, _values(driving=False)),
    )

    assert all(constraint["driving"] is False for constraint in result["constraints"])
    assert result["measured_before"] == result["measured_after"]


def test_lock_creates_exact_relative_driving_constraint_pair(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    result = _apply(
        document,
        _prepared(document, context, _values(target=_relative())),
    )

    assert result["target_form"] == "relative"
    assert result["measured_before"] == {"x": -5.0, "y": 0.0, "unit": "mm"}
    assert result["measured_after"] == result["measured_before"]
    assert [constraint["type"] for constraint in result["constraints"]] == [
        "DistanceX",
        "DistanceY",
    ]
    assert [constraint["value"] for constraint in result["constraints"]] == [
        -5.0,
        0.0,
    ]
    assert all(
        constraint["references"]
        == [
            {"slot": 1, "geometry_index": 0, "position": 2},
            {"slot": 2, "geometry_index": 0, "position": 1},
        ]
        for constraint in result["constraints"]
    )
    assert sketch.Geometry[0].StartPoint.x == 0.0
    assert sketch.Geometry[0].EndPoint.x == 5.0


def test_lock_relative_accepts_origin_as_exact_reference(monkeypatch) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                target=_relative(reference_index=-1, reference_position="start")
            ),
        ),
    )

    assert result["constraints"][0]["references"][1] == {
        "slot": 2,
        "geometry_index": -1,
        "position": 1,
    }
    assert result["measured_after"] == {"x": -5.0, "y": 0.0, "unit": "mm"}


def test_lock_relative_preserves_two_point_signed_offset(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    target = sketch.addGeometry(FakePoint(_point(4.0, -3.0)), False)
    reference = sketch.addGeometry(FakePoint(_point(-2.0, 8.0)), False)
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=3,
                target=_relative(
                    target,
                    "start",
                    reference,
                    "start",
                    x=-6.0,
                    y=11.0,
                ),
            ),
        ),
    )

    assert result["measured_after"] == {"x": -6.0, "y": 11.0, "unit": "mm"}


def test_lock_accepts_exact_external_point_reference(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.ExternalGeo.append(FakeExternalLine("Support.Edge1"))
    result = _apply(
        document,
        _prepared(
            document,
            context,
            _values(
                expected_external_geometry_count=1,
                target=_relative(
                    reference_index=-3,
                    reference_position="start",
                ),
                driving=False,
            ),
        ),
    )

    assert result["constraints"][0]["references"][1]["geometry_index"] == -3
    assert all(constraint["driving"] is False for constraint in result["constraints"])


@pytest.mark.parametrize(
    ("target", "message"),
    (
        (_absolute(-1, "start", x=0.0, y=0.0), "cannot lock the origin"),
        (_absolute(0, "whole"), "must be one exact point"),
        (
            _relative(-1, "start", 0, "end", x=5.0, y=0.0),
            "cannot lock the origin",
        ),
        (_relative(reference_position="whole"), "must be one exact point"),
    ),
)
def test_lock_refuses_invalid_target_or_reference_points(
    monkeypatch,
    target,
    message,
) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match=message):
        _prepared(document, context, _values(target=target))


@pytest.mark.parametrize(
    "target",
    (
        _absolute(x=4.0),
        _absolute(y=1.0),
        _relative(x=-4.0),
        _relative(y=1.0),
    ),
)
def test_lock_refuses_stale_expected_measurements(monkeypatch, target) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError, match="expected measurement changed"):
        _prepared(document, context, _values(target=target))


def test_lock_refuses_duplicate_relative_points(monkeypatch) -> None:
    document, _sketch, context = install_fake_sketch_host(monkeypatch)
    target = _relative(reference_position="end", x=0.0, y=0.0)
    with pytest.raises(NativeSketchError, match="must be distinct"):
        _prepared(document, context, _values(target=target))


def test_lock_refuses_internal_group_member(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    member = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
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
                target=_absolute(member, "start", x=2.0, y=3.0),
            ),
        )


def test_lock_refuses_internal_alignment_geometry(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    point = sketch.addGeometry(FakePoint(_point(2.0, 3.0)), False)
    sketch.GeometryFacadeList[point].InternalType = "BSplineControlPoint"
    with pytest.raises(NativeSketchError, match="internal-alignment geometry"):
        _prepared(
            document,
            context,
            _values(
                expected_geometry_count=2,
                target=_absolute(point, "start", x=2.0, y=3.0),
            ),
        )


def test_lock_refuses_missing_point_lookup(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    monkeypatch.setattr(sketch, "getPoint", None)
    with pytest.raises(NativeSketchError, match="point lookup is unavailable"):
        _prepared(document, context, _values())


@pytest.mark.parametrize(
    "updates",
    (
        {"target": {"form": "absolute"}},
        {
            "target": {
                **_absolute(),
                "reference": _element(0, "start"),
            }
        },
        {
            "target": {
                **_relative(),
                "expected_position_mm": {"x": 5.0, "y": 0.0},
            }
        },
        {"target": {**_absolute(), "form": "multiple"}},
        {"target": _absolute(x=True)},
        {"target": _absolute(x=float("inf"))},
        {"target": _absolute(x=1_000_001.0)},
        {"driving": 1},
        {"unexpected": True},
    ),
)
def test_lock_rejects_invalid_exact_contract(monkeypatch, updates) -> None:
    document, _sketch, _context = install_fake_sketch_host(monkeypatch)
    with pytest.raises(NativeSketchError):
        prepare_sketch_lock(document.Uid, _values(**updates))


def test_lock_refuses_solver_rejection_without_mutation(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = _rejected_feasibility()

    with pytest.raises(NativeSketchError, match="would be redundant"):
        _prepared(document, context, _values())

    assert sketch.ConstraintCount == 0
    assert sketch.Geometry[0].EndPoint.x == 5.0
    assert sketch.Geometry[0].EndPoint.y == 0.0


def test_lock_refuses_inexact_pair_feasibility_diagnostic(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    sketch.FeasibilityOverride = {
        **_rejected_feasibility(),
        "accepted": True,
        "degrees_of_freedom": 2,
        "solver_status": 0,
        "proposed_constraint_count": 1,
        "redundant_constraint_indices": [],
    }
    with pytest.raises(NativeSketchError, match="exact append"):
        _prepared(document, context, _values())
    assert sketch.ConstraintCount == 0


def test_lock_refuses_preflight_and_postcondition_drift(monkeypatch) -> None:
    document, sketch, context = install_fake_sketch_host(monkeypatch)
    prepared = _prepared(document, context, _values())
    sketch.GeometryFacadeList[0].Blocked = True
    with pytest.raises(NativeSketchError, match="after Lock preflight"):
        create_sketch_lock(document, prepared)

    sketch.GeometryFacadeList[0].Blocked = False
    prepared = _prepared(document, context, _values())
    draft = create_sketch_lock(document, prepared)
    sketch.RedundantConstraints.append(1)
    with pytest.raises(NativeSketchError, match="solver conflict or redundancy"):
        verify_sketch_lock(document, draft)
