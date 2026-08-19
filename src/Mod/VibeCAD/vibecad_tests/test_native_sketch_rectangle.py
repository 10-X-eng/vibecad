# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchErrors import NativeSketchError
from VibeCADNativeSketchRectangle import (
    create_sketch_rectangle,
    preflight_sketch_rectangle,
    prepare_sketch_rectangle,
    verify_sketch_rectangle,
)
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "first_corner_mm": {"x": -8.0, "y": -3.0},
            "opposite_corner_mm": {"x": 6.0, "y": 5.0},
            **updates,
        }
    )


@pytest.fixture
def rectangle_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _create_result(document, context, values):
    prepared = preflight_sketch_rectangle(
        context,
        prepare_sketch_rectangle(document.Uid, values),
    )
    draft = create_sketch_rectangle(document, prepared)
    return draft, verify_sketch_rectangle(document, draft)


def test_rectangle_matches_positive_diagonal_human_geometry(rectangle_host) -> None:
    document, sketch, context = rectangle_host

    draft, result = _create_result(document, context, _values())

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert result["geometry_count"] == 5
    assert result["constraint_count"] == 8
    assert result["segment_count"] == 4
    assert result["closed"] is True
    assert result["corners_mm"] == [
        [-8.0, -3.0, 0.0],
        [6.0, -3.0, 0.0],
        [6.0, 5.0, 0.0],
        [-8.0, 5.0, 0.0],
    ]
    assert [item["geometry_index"] for item in result["geometry_refs"]] == [1, 2, 3, 4]
    assert [item["kind"] for item in result["geometry_refs"]] == ["line"] * 4
    assert [item["type"] for item in result["constraint_refs"]] == [
        "Coincident",
        "Coincident",
        "Coincident",
        "Coincident",
        "Horizontal",
        "Vertical",
        "Horizontal",
        "Vertical",
    ]
    assert set(result) == {
        "sketch",
        "geometry_refs",
        "constraint_refs",
        "corners_mm",
        "segment_count",
        "closed",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
    }


def test_rectangle_matches_negative_diagonal_human_order(rectangle_host) -> None:
    document, _sketch, context = rectangle_host

    _draft, result = _create_result(
        document,
        context,
        _values(
            first_corner_mm={"x": -8.0, "y": 5.0},
            opposite_corner_mm={"x": 6.0, "y": -3.0},
        ),
    )

    assert result["corners_mm"] == [
        [-8.0, 5.0, 0.0],
        [-8.0, -3.0, 0.0],
        [6.0, -3.0, 0.0],
        [6.0, 5.0, 0.0],
    ]
    assert [item["type"] for item in result["constraint_refs"][4:]] == [
        "Vertical",
        "Horizontal",
        "Vertical",
        "Horizontal",
    ]


@pytest.mark.parametrize(
    "opposite,message",
    (
        ({"x": -8.0, "y": 5.0}, "non-zero width and height"),
        ({"x": 6.0, "y": -3.0}, "non-zero width and height"),
        ({"x": -8.0, "y": -3.0}, "non-zero width and height"),
    ),
)
def test_rectangle_rejects_zero_span(rectangle_host, opposite, message) -> None:
    document, _sketch, _context = rectangle_host

    with pytest.raises(NativeSketchError, match=message):
        prepare_sketch_rectangle(
            document.Uid,
            _values(opposite_corner_mm=opposite),
        )


def test_rectangle_verifier_rejects_side_drift(rectangle_host) -> None:
    document, sketch, context = rectangle_host
    prepared = preflight_sketch_rectangle(
        context,
        prepare_sketch_rectangle(document.Uid, _values()),
    )
    draft = create_sketch_rectangle(document, prepared)
    sketch.Geometry[2].EndPoint.y = 9.0

    with pytest.raises(NativeSketchError, match="side differs"):
        verify_sketch_rectangle(document, draft)


def test_rectangle_verifier_rejects_alignment_drift(rectangle_host) -> None:
    document, sketch, context = rectangle_host
    prepared = preflight_sketch_rectangle(
        context,
        prepare_sketch_rectangle(document.Uid, _values()),
    )
    draft = create_sketch_rectangle(document, prepared)
    sketch.Constraints[4].Type = "Vertical"

    with pytest.raises(NativeSketchError, match="alignment constraint changed"):
        verify_sketch_rectangle(document, draft)
