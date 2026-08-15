# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from types import SimpleNamespace

import pytest

import VibeCADNativeSketchTargets as target_module
from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchPoint import (
    create_sketch_point,
    preflight_sketch_point,
    prepare_sketch_point,
    verify_sketch_point,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{"position_mm": {"x": 12.5, "y": -4.0}, **updates}
    )


@pytest.fixture
def point_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_point_preflight_create_and_verify_exact_result(point_host) -> None:
    document, sketch, context = point_host
    spec = prepare_sketch_point(document.Uid, _values())
    prepared = preflight_sketch_point(context, spec)

    draft = create_sketch_point(document, prepared)
    result = verify_sketch_point(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert draft.created == ()
    assert result["sketch"]["object_name"] == sketch.Name
    assert result["geometry_count"] == 2
    assert result["constraint_count"] == 0
    assert result["geometry"] == {
        "index": 1,
        "geometry_id": 101,
        "type_id": "Part::GeomPoint",
        "kind": "point",
        "construction": False,
        "blocked": False,
        "position_mm": [12.5, -4.0, 0.0],
    }
    assert set(result) == {
        "sketch",
        "geometry",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
    }


@pytest.mark.parametrize(
    "updates",
    (
        {"sketch": {"object_name": "Other"}},
        {"expected_geometry_count": 2},
        {"expected_constraint_count": 1},
        {"position_mm": {"x": float("inf"), "y": 0.0}},
        {"position_mm": {"x": True, "y": 0.0}},
    ),
)
def test_point_rejects_wrong_target_state_or_coordinates(point_host, updates) -> None:
    document, _sketch, context = point_host
    with pytest.raises((NativeSketchError, RuntimeError)):
        spec = prepare_sketch_point(document.Uid, _values(**updates))
        preflight_sketch_point(context, spec)


def test_point_requires_the_exact_human_opened_sketch(point_host, monkeypatch) -> None:
    document, _sketch, context = point_host
    monkeypatch.setattr(target_module, "active_edit_object", lambda: None)
    spec = prepare_sketch_point(document.Uid, _values())

    with pytest.raises(NativeSketchError, match="human-opened"):
        preflight_sketch_point(context, spec)


def test_point_rejects_preexisting_geometry_drift_after_preflight(point_host) -> None:
    document, sketch, context = point_host
    prepared = preflight_sketch_point(
        context,
        prepare_sketch_point(document.Uid, _values()),
    )
    sketch.Geometry[0].EndPoint.x = 9.0

    with pytest.raises(NativeSketchError, match="changed after Point preflight"):
        create_sketch_point(document, prepared)


def test_point_rejects_preexisting_constraint_drift_after_preflight(point_host) -> None:
    document, sketch, context = point_host
    constraint = SimpleNamespace(
        Type="Distance",
        First=0,
        FirstPos=1,
        Second=-2000,
        SecondPos=0,
        Third=-2000,
        ThirdPos=0,
        Value=5.0,
        Name="Length",
        Driving=True,
        IsActive=True,
        InVirtualSpace=False,
    )
    sketch.Constraints = [constraint]
    sketch.ConstraintCount = 1
    prepared = preflight_sketch_point(
        context,
        prepare_sketch_point(
            document.Uid,
            _values(expected_constraint_count=1),
        ),
    )
    constraint.Value = 7.0

    with pytest.raises(NativeSketchError, match="constraints changed"):
        create_sketch_point(document, prepared)


def test_point_verifier_rejects_unexpected_construction_state(point_host) -> None:
    document, sketch, context = point_host
    prepared = preflight_sketch_point(
        context,
        prepare_sketch_point(document.Uid, _values()),
    )
    draft = create_sketch_point(document, prepared)
    sketch.GeometryFacadeList[1].Construction = True

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_point(document, draft)
