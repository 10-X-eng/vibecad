# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchLine import (
    create_sketch_line,
    preflight_sketch_line,
    prepare_sketch_line,
    verify_sketch_line,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "start_mm": {"x": -2.0, "y": 3.5},
            "end_mm": {"x": 8.0, "y": -1.5},
            **updates,
        }
    )


@pytest.fixture
def line_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_line_preflight_create_and_verify_exact_result(line_host) -> None:
    document, sketch, context = line_host
    spec = prepare_sketch_line(document.Uid, _values())
    prepared = preflight_sketch_line(context, spec)

    draft = create_sketch_line(document, prepared)
    result = verify_sketch_line(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert draft.created == ()
    assert result["sketch"]["object_name"] == sketch.Name
    assert result["geometry_count"] == 2
    assert result["constraint_count"] == 0
    geometry = result["geometry"]
    assert geometry["index"] == 1
    assert geometry["geometry_id"] == 101
    assert geometry["type_id"] == "Part::GeomLineSegment"
    assert geometry["kind"] == "line"
    assert geometry["construction"] is False
    assert geometry["blocked"] is False
    assert geometry["start_mm"] == [-2.0, 3.5, 0.0]
    assert geometry["end_mm"] == [8.0, -1.5, 0.0]
    assert geometry["first_parameter"] == 0.0
    assert geometry["last_parameter"] == 1.0
    assert set(result) == {
        "sketch",
        "geometry",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
    }


@pytest.mark.parametrize(
    "start,end",
    (
        ({"x": 1.0, "y": 2.0}, {"x": 1.0, "y": 2.0}),
        ({"x": 0.0, "y": 0.0}, {"x": 1.0e-10, "y": 0.0}),
    ),
)
def test_line_rejects_degenerate_endpoints(line_host, start, end) -> None:
    document, _sketch, _context = line_host

    with pytest.raises(NativeSketchError, match="endpoints must be distinct"):
        prepare_sketch_line(
            document.Uid,
            _values(start_mm=start, end_mm=end),
        )


def test_line_rejects_nonfinite_or_malformed_points(line_host) -> None:
    document, _sketch, _context = line_host

    for value in (
        {"x": float("nan"), "y": 0.0},
        {"x": True, "y": 0.0},
        {"x": 0.0, "y": 0.0, "z": 0.0},
    ):
        with pytest.raises(NativeSketchError):
            prepare_sketch_line(document.Uid, _values(start_mm=value))


def test_line_verifier_rejects_endpoint_drift(line_host) -> None:
    document, sketch, context = line_host
    prepared = preflight_sketch_line(
        context,
        prepare_sketch_line(document.Uid, _values()),
    )
    draft = create_sketch_line(document, prepared)
    sketch.Geometry[1].EndPoint.x = 9.0

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_line(document, draft)


def test_line_verifier_rejects_construction_state(line_host) -> None:
    document, sketch, context = line_host
    prepared = preflight_sketch_line(
        context,
        prepare_sketch_line(document.Uid, _values()),
    )
    draft = create_sketch_line(document, prepared)
    sketch.GeometryFacadeList[1].Construction = True

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_line(document, draft)
