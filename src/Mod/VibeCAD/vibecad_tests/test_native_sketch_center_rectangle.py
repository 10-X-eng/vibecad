# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADNativeSketchCenterRectangle import (
    create_sketch_center_rectangle,
    preflight_sketch_center_rectangle,
    prepare_sketch_center_rectangle,
    verify_sketch_center_rectangle,
)
from VibeCADNativeSketchErrors import NativeSketchError
from vibecad_tests.native_sketch_test_support import (
    geometry_target_values,
    install_fake_sketch_host,
)


def _values(**updates) -> dict[str, object]:
    return geometry_target_values(
        **{
            "center_mm": {"x": 2.0, "y": 1.0},
            "corner_mm": {"x": 8.0, "y": 5.0},
            **updates,
        }
    )


@pytest.fixture
def center_rectangle_host(monkeypatch):
    return install_fake_sketch_host(monkeypatch)


def _prepared(document, context, values):
    return preflight_sketch_center_rectangle(
        context,
        prepare_sketch_center_rectangle(document.Uid, values),
    )


def test_center_rectangle_matches_human_geometry(center_rectangle_host) -> None:
    document, sketch, context = center_rectangle_host
    prepared = _prepared(document, context, _values())

    draft = create_sketch_center_rectangle(document, prepared)
    result = verify_sketch_center_rectangle(document, draft)

    assert draft.recompute_targets == (sketch,)
    assert [item.object_name for item in draft.changed] == [sketch.Name]
    assert result["geometry_count"] == 6
    assert result["constraint_count"] == 9
    assert result["segment_count"] == 4
    assert result["closed"] is True
    assert result["corners_mm"] == [
        [-4.0, -3.0, 0.0],
        [8.0, -3.0, 0.0],
        [8.0, 5.0, 0.0],
        [-4.0, 5.0, 0.0],
    ]
    assert [item["index"] for item in result["geometries"]] == [1, 2, 3, 4]
    assert result["center_geometry"]["index"] == 5
    assert result["center_geometry"]["type_id"] == "Part::GeomPoint"
    assert result["center_geometry"]["construction"] is True
    assert result["center_geometry"]["position_mm"] == [2.0, 1.0, 0.0]
    assert [item["type"] for item in result["constraints"]] == [
        "Coincident",
        "Coincident",
        "Coincident",
        "Coincident",
        "Horizontal",
        "Vertical",
        "Horizontal",
        "Vertical",
        "Symmetric",
    ]
    assert result["constraints"][-1]["references"] == [
        {"slot": 1, "geometry_index": 3, "position": 1},
        {"slot": 2, "geometry_index": 1, "position": 1},
        {"slot": 3, "geometry_index": 5, "position": 1},
    ]
    assert set(result) == {
        "sketch",
        "geometries",
        "center_geometry",
        "constraints",
        "corners_mm",
        "segment_count",
        "closed",
        "geometry_count",
        "constraint_count",
        "profile",
        "solver",
    }


@pytest.mark.parametrize(
    "corner",
    (
        {"x": 2.0, "y": 5.0},
        {"x": 8.0, "y": 1.0},
        {"x": 2.0, "y": 1.0},
    ),
)
def test_center_rectangle_rejects_zero_half_span(
    center_rectangle_host,
    corner,
) -> None:
    document, _sketch, _context = center_rectangle_host

    with pytest.raises(NativeSketchError, match="non-zero width and height"):
        prepare_sketch_center_rectangle(
            document.Uid,
            _values(corner_mm=corner),
        )


def test_center_rectangle_rejects_unbounded_reflection(
    center_rectangle_host,
) -> None:
    document, _sketch, _context = center_rectangle_host

    with pytest.raises(NativeSketchError, match="within"):
        prepare_sketch_center_rectangle(
            document.Uid,
            _values(
                center_mm={"x": -900_000.0, "y": 0.0},
                corner_mm={"x": 900_000.0, "y": 1.0},
            ),
        )


def test_center_rectangle_verifier_rejects_center_drift(
    center_rectangle_host,
) -> None:
    document, sketch, context = center_rectangle_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_center_rectangle(document, prepared)
    sketch.Geometry[5].X = 3.0

    with pytest.raises(NativeSketchError, match="construction point changed"):
        verify_sketch_center_rectangle(document, draft)


def test_center_rectangle_verifier_rejects_symmetry_drift(
    center_rectangle_host,
) -> None:
    document, sketch, context = center_rectangle_host
    prepared = _prepared(document, context, _values())
    draft = create_sketch_center_rectangle(document, prepared)
    sketch.Constraints[8].ThirdPos = 2

    with pytest.raises(NativeSketchError, match="symmetry constraint changed"):
        verify_sketch_center_rectangle(document, draft)
