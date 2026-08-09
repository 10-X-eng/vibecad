# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import math

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchParabolicArc import (
    create_sketch_parabolic_arc,
    preflight_sketch_parabolic_arc,
    prepare_sketch_parabolic_arc,
    verify_sketch_parabolic_arc,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "vertex_mm": {"x": 2.0, "y": -1.0},
            "focal_length_mm": 5.0,
            "rotation_degrees": 15.0,
            "start_parameter_mm": -4.0,
            "end_parameter_mm": 6.0,
            **updates,
        }
    )


@pytest.fixture
def parabolic_arc_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def test_parabolic_arc_creates_curve_and_all_human_internal_geometry(
    parabolic_arc_host,
) -> None:
    document, sketch, context = parabolic_arc_host
    prepared = preflight_sketch_parabolic_arc(
        context,
        prepare_sketch_parabolic_arc(document.Uid, _values()),
    )

    draft = create_sketch_parabolic_arc(document, prepared)
    result = verify_sketch_parabolic_arc(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert result["geometry_count"] == 4
    assert result["constraint_count"] == 2
    geometry = result["geometry"]
    assert geometry["index"] == 1
    assert geometry["type_id"] == "Part::GeomArcOfParabola"
    assert geometry["kind"] == "parabolic_arc"
    assert geometry["construction"] is False
    assert geometry["center_mm"] == [2.0, -1.0, 0.0]
    assert geometry["focal_length_mm"] == 5.0
    assert geometry["first_parameter"] == -4.0
    assert geometry["last_parameter"] == 6.0
    internal = result["internal_geometries"]
    assert [item["index"] for item in internal] == [2, 3]
    assert [item["internal_type"] for item in internal] == [
        "ParabolaFocus",
        "ParabolaFocalAxis",
    ]
    assert [item["kind"] for item in internal] == ["point", "line"]
    assert all(item["construction"] is True for item in internal)
    focus = internal[0]["position_mm"]
    assert math.isclose(focus[0], 2.0 + 5.0 * math.cos(math.radians(15.0)))
    assert math.isclose(focus[1], -1.0 + 5.0 * math.sin(math.radians(15.0)))
    constraints = result["internal_constraints"]
    assert [item["references"] for item in constraints] == [
        [
            {"slot": 1, "geometry_index": 2, "position": 1},
            {"slot": 2, "geometry_index": 1},
        ],
        [
            {"slot": 1, "geometry_index": 3},
            {"slot": 2, "geometry_index": 1},
        ],
    ]


@pytest.mark.parametrize(
    ("updates", "message"),
    (
        ({"focal_length_mm": 0.0}, "greater than"),
        ({"rotation_degrees": 360.0}, "below 360"),
        ({"start_parameter_mm": -1_000_001.0}, "within"),
        ({"end_parameter_mm": 1_000_001.0}, "within"),
        (
            {"start_parameter_mm": 1.0, "end_parameter_mm": 1.0},
            "must be greater",
        ),
        (
            {"start_parameter_mm": 2.0, "end_parameter_mm": 1.0},
            "must be greater",
        ),
    ),
)
def test_parabolic_arc_rejects_degenerate_parameters(
    parabolic_arc_host,
    updates,
    message,
) -> None:
    document, _sketch, _context = parabolic_arc_host

    with pytest.raises(NativeSketchError, match=message):
        prepare_sketch_parabolic_arc(document.Uid, _values(**updates))


def test_parabolic_arc_rejects_unbounded_analytic_endpoint(
    parabolic_arc_host,
) -> None:
    document, _sketch, _context = parabolic_arc_host

    with pytest.raises(NativeSketchError, match="within"):
        prepare_sketch_parabolic_arc(
            document.Uid,
            _values(focal_length_mm=0.001, end_parameter_mm=1000.0),
        )


def test_parabolic_arc_verifier_rejects_internal_geometry_drift(
    parabolic_arc_host,
) -> None:
    document, sketch, context = parabolic_arc_host
    prepared = preflight_sketch_parabolic_arc(
        context,
        prepare_sketch_parabolic_arc(document.Uid, _values()),
    )
    draft = create_sketch_parabolic_arc(document, prepared)
    sketch.Geometry[2].X += 0.25

    with pytest.raises(NativeSketchError, match="internal geometry changed"):
        verify_sketch_parabolic_arc(document, draft)


def test_parabolic_arc_verifier_rejects_parameter_drift(
    parabolic_arc_host,
) -> None:
    document, sketch, context = parabolic_arc_host
    prepared = preflight_sketch_parabolic_arc(
        context,
        prepare_sketch_parabolic_arc(document.Uid, _values()),
    )
    draft = create_sketch_parabolic_arc(document, prepared)
    sketch.Geometry[1].LastParameter += 0.25

    with pytest.raises(NativeSketchError, match="exact definition"):
        verify_sketch_parabolic_arc(document, draft)
