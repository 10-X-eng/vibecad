# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchSlot import (
    create_sketch_slot,
    preflight_sketch_slot,
    prepare_sketch_slot,
    verify_sketch_slot,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "start_center_mm": {"x": -10.0, "y": 0.0},
            "end_center_mm": {"x": 10.0, "y": 0.0},
            "radius_mm": 3.0,
            **updates,
        }
    )


@pytest.fixture
def slot_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_slot(
        context,
        prepare_sketch_slot(document.Uid, values),
    )


def test_slot_matches_human_geometry_and_constraints(slot_host) -> None:
    document, sketch, context = slot_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_slot(document, prepared)
    result = verify_sketch_slot(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (5, 5)
    assert result["start_center_mm"] == [-10.0, 0.0, 0.0]
    assert result["end_center_mm"] == [10.0, 0.0, 0.0]
    assert result["centerline_length_mm"] == 20.0
    assert result["radius_mm"] == 3.0
    assert result["closed"] is True
    assert [item["index"] for item in result["arcs"]] == [1, 2]
    assert [item["start_mm"] for item in result["arcs"]] == [
        [-10.0, 3.0, 0.0],
        [10.0, -3.0, 0.0],
    ]
    assert [item["end_mm"] for item in result["arcs"]] == [
        [-10.0, -3.0, 0.0],
        [10.0, 3.0, 0.0],
    ]
    assert [item["index"] for item in result["lines"]] == [3, 4]
    assert [item["start_mm"] for item in result["lines"]] == [
        [-10.0, 3.0, 0.0],
        [-10.0, -3.0, 0.0],
    ]
    assert [item["end_mm"] for item in result["lines"]] == [
        [10.0, 3.0, 0.0],
        [10.0, -3.0, 0.0],
    ]
    assert [item["type"] for item in result["constraints"]] == [
        "Tangent",
        "Tangent",
        "Tangent",
        "Tangent",
        "Equal",
    ]
    assert result["constraints"][0]["references"] == [
        {"slot": 1, "geometry_index": 1, "position": 1},
        {"slot": 2, "geometry_index": 3, "position": 1},
    ]
    assert result["constraints"][-1]["references"] == [
        {"slot": 1, "geometry_index": 1},
        {"slot": 2, "geometry_index": 2},
    ]


def test_slot_supports_rotated_centerline(slot_host) -> None:
    document, _sketch, context = slot_host
    prepared = _prepared(
        document,
        context,
        _values(
            start_center_mm={"x": 0.0, "y": 0.0},
            end_center_mm={"x": 0.0, "y": 10.0},
            radius_mm=2.0,
        ),
    )

    result = verify_sketch_slot(document, create_sketch_slot(document, prepared))

    assert [item["start_mm"] for item in result["lines"]] == [
        [-2.0, 0.0, 0.0],
        [2.0, 0.0, 0.0],
    ]
    assert [item["end_mm"] for item in result["lines"]] == [
        [-2.0, 10.0, 0.0],
        [2.0, 10.0, 0.0],
    ]
    assert math.isclose(
        result["arcs"][0]["last_parameter"],
        2.0 * math.pi,
        abs_tol=1.0e-10,
    )
    assert math.isclose(
        result["arcs"][1]["last_parameter"],
        math.pi,
        abs_tol=1.0e-10,
    )


@pytest.mark.parametrize(
    "updates",
    (
        {"end_center_mm": {"x": -10.0, "y": 0.0}},
        {"radius_mm": 0.0},
        {
            "start_center_mm": {"x": 999_999.0, "y": 0.0},
            "end_center_mm": {"x": 999_999.0, "y": 1.0},
            "radius_mm": 2.0,
        },
    ),
)
def test_slot_rejects_degenerate_or_unbounded_geometry(slot_host, updates) -> None:
    document, _sketch, _context = slot_host

    with pytest.raises(NativeSketchError):
        prepare_sketch_slot(document.Uid, _values(**updates))


def test_slot_verifier_rejects_tangent_constraint_drift(slot_host) -> None:
    document, sketch, context = slot_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_slot(document, prepared)
    sketch.Constraints[2].FirstPos = 1

    with pytest.raises(NativeSketchError, match="tangent constraint changed"):
        verify_sketch_slot(document, draft)
