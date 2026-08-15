# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchArcSlot import (
    create_sketch_arc_slot,
    preflight_sketch_arc_slot,
    prepare_sketch_arc_slot,
    verify_sketch_arc_slot,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "center_mm": {"x": 0.0, "y": 0.0},
            "centerline_radius_mm": 10.0,
            "start_angle_degrees": 0.0,
            "sweep_angle_degrees": 90.0,
            "slot_radius_mm": 2.0,
            **updates,
        }
    )


@pytest.fixture
def arc_slot_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_arc_slot(
        context,
        prepare_sketch_arc_slot(document.Uid, values),
    )


def test_arc_slot_matches_human_positive_sweep_topology(arc_slot_host) -> None:
    document, sketch, context = arc_slot_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_arc_slot(document, prepared)
    result = verify_sketch_arc_slot(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert (result["geometry_count"], result["constraint_count"]) == (5, 5)
    assert result["center_mm"] == [0.0, 0.0, 0.0]
    assert result["centerline_radius_mm"] == 10.0
    assert result["start_angle_degrees"] == 0.0
    assert result["sweep_angle_degrees"] == 90.0
    assert result["slot_radius_mm"] == 2.0
    assert result["clockwise"] is False
    assert result["inner_boundary_present"] is True
    assert result["closed"] is True
    assert result["arc_roles"] == {
        "outer_boundary": 1,
        "initial_end": 2,
        "terminal_end": 3,
        "inner_boundary": 4,
    }
    arcs = result["arcs"]
    assert [item["radius_mm"] for item in arcs] == [12.0, 2.0, 2.0, 8.0]
    assert [item["center_mm"] for item in arcs] == [
        [0.0, 0.0, 0.0],
        [10.0, 0.0, 0.0],
        [0.0, 10.0, 0.0],
        [0.0, 0.0, 0.0],
    ]
    assert [item["start_mm"] for item in arcs] == [
        [12.0, 0.0, 0.0],
        [8.0, 0.0, 0.0],
        [0.0, 12.0, 0.0],
        [8.0, 0.0, 0.0],
    ]
    assert [item["end_mm"] for item in arcs] == [
        [0.0, 12.0, 0.0],
        [12.0, 0.0, 0.0],
        [0.0, 8.0, 0.0],
        [0.0, 8.0, 0.0],
    ]
    constraints = result["constraints"]
    assert [item["type"] for item in constraints] == [
        "Coincident",
        "Tangent",
        "Tangent",
        "Tangent",
        "Tangent",
    ]
    assert constraints[0]["references"] == [
        {"slot": 1, "geometry_index": 1, "position": 3},
        {"slot": 2, "geometry_index": 4, "position": 3},
    ]
    assert constraints[-1]["references"] == [
        {"slot": 1, "geometry_index": 1, "position": 1},
        {"slot": 2, "geometry_index": 2, "position": 2},
    ]


def test_arc_slot_preserves_human_clockwise_geometry_order(arc_slot_host) -> None:
    document, _sketch, context = arc_slot_host
    prepared = _prepared(
        document,
        context,
        _values(sweep_angle_degrees=-90.0),
    )

    result = verify_sketch_arc_slot(
        document,
        create_sketch_arc_slot(document, prepared),
    )

    assert result["clockwise"] is True
    arcs = result["arcs"]
    assert math.isclose(arcs[0]["first_parameter"], 1.5 * math.pi)
    assert math.isclose(arcs[0]["last_parameter"], math.tau)
    assert [item["start_mm"] for item in arcs] == [
        [0.0, -12.0, 0.0],
        [12.0, 0.0, 0.0],
        [0.0, -8.0, 0.0],
        [0.0, -8.0, 0.0],
    ]
    assert [item["end_mm"] for item in arcs] == [
        [12.0, 0.0, 0.0],
        [8.0, 0.0, 0.0],
        [0.0, -12.0, 0.0],
        [8.0, 0.0, 0.0],
    ]
    assert result["constraints"][1]["references"] == [
        {"slot": 1, "geometry_index": 4, "position": 1},
        {"slot": 2, "geometry_index": 3, "position": 1},
    ]


def test_arc_slot_supports_human_collapsed_inner_boundary(arc_slot_host) -> None:
    document, _sketch, context = arc_slot_host
    prepared = _prepared(
        document,
        context,
        _values(slot_radius_mm=10.0),
    )

    result = verify_sketch_arc_slot(
        document,
        create_sketch_arc_slot(document, prepared),
    )

    assert (result["geometry_count"], result["constraint_count"]) == (4, 4)
    assert result["inner_boundary_present"] is False
    assert result["arc_roles"] == {
        "outer_boundary": 1,
        "initial_end": 2,
        "terminal_end": 3,
    }
    assert [item["type"] for item in result["constraints"]] == [
        "Coincident",
        "Coincident",
        "Tangent",
        "Tangent",
    ]
    assert result["constraints"][0]["references"] == [
        {"slot": 1, "geometry_index": 1, "position": 3},
        {"slot": 2, "geometry_index": 2, "position": 1},
    ]


@pytest.mark.parametrize(
    "updates",
    (
        {"sweep_angle_degrees": 0.0},
        {"sweep_angle_degrees": 360.0},
        {"slot_radius_mm": 11.0},
        {"slot_radius_mm": 9.999_999_95},
        {
            "center_mm": {"x": 999_990.0, "y": 0.0},
            "centerline_radius_mm": 8.0,
            "slot_radius_mm": 3.0,
        },
    ),
)
def test_arc_slot_rejects_degenerate_or_unbounded_geometry(
    arc_slot_host,
    updates,
) -> None:
    document, _sketch, _context = arc_slot_host

    with pytest.raises(NativeSketchError):
        prepare_sketch_arc_slot(document.Uid, _values(**updates))


def test_arc_slot_verifier_rejects_inherent_constraint_drift(arc_slot_host) -> None:
    document, sketch, context = arc_slot_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_arc_slot(document, prepared)
    sketch.Constraints[3].SecondPos = 2

    with pytest.raises(NativeSketchError, match="inherent constraint changed"):
        verify_sketch_arc_slot(document, draft)
