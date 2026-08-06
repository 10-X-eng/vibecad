# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import pytest

from VibeCADPlacementInspection import (
    PlacementInspectionError,
    read_placement,
)


@pytest.mark.parametrize(
    ("plane", "offset", "origin", "x_axis", "y_axis", "normal"),
    (
        ("XY", 2.0, [0.0, 0.0, 2.0], [1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]),
        (
            "XZ",
            2.0,
            [0.0, -2.0, 0.0],
            [1.0, 0.0, 0.0],
            [0.0, 0.0, 1.0],
            [0.0, -1.0, 0.0],
        ),
        ("YZ", 2.0, [2.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0], [1.0, 0.0, 0.0]),
    ),
)
def test_principal_sketch_frames_match_native_freecad_placement(
    plane: str,
    offset: float,
    origin: list[float],
    x_axis: list[float],
    y_axis: list[float],
    normal: list[float],
) -> None:
    result = read_placement(
        {
            "operation": "sketch",
            "plane": plane,
            "plane_offset_mm": offset,
        }
    )

    assert result["origin_mm"] == origin
    assert result["local_axes_in_global_coordinates"] == {
        "x": x_axis,
        "y": y_axis,
        "z": normal,
    }
    assert result["default_subtractive_direction"] == [-item for item in normal]
    assert result["linear_feature_directions"] == {
        "along_normal": normal,
        "opposite_normal": [-item for item in normal],
        "symmetric": [normal, [-item for item in normal]],
    }
    assert len(result["local_to_global_matrix_row_major"]) == 16


def test_explicit_primitive_frame_resolves_dimension_axes_and_roll() -> None:
    result = read_placement(
        {
            "operation": "box",
            "origin": [1, 2, 3],
            "direction": [0, 1, 0],
            "x_direction": [1, 0, 0],
        }
    )

    assert result["origin_mm"] == [1.0, 2.0, 3.0]
    assert result["local_axes_in_global_coordinates"] == {
        "x": [1.0, 0.0, 0.0],
        "y": [0.0, 0.0, -1.0],
        "z": [0.0, 1.0, 0.0],
    }
    assert result["dimension_mapping"] == {
        "length": "local +X",
        "width": "local +Y",
        "height": "local +Z",
    }


def test_nondefault_direction_requires_explicit_roll_for_exact_preview() -> None:
    with pytest.raises(PlacementInspectionError) as captured:
        read_placement({"operation": "wedge", "direction": [0, 1, 0]})

    assert captured.value.code == "PLACEMENT_UNDERSPECIFIED"
    assert "x_direction" in str(captured.value)


def test_explicit_sketch_placement_rejects_parallel_x_direction() -> None:
    with pytest.raises(PlacementInspectionError, match="must not be parallel"):
        read_placement(
            {
                "operation": "sketch",
                "placement": {
                    "origin": [0, 0, 0],
                    "normal": [0, 0, 1],
                    "x_direction": [0, 0, 2],
                },
            }
        )


def test_universal_vibescript_tool_routes_exact_placement_read() -> None:
    import VibeCADSession as session

    result = session._run_universal_vibescript_tool(
        object(),
        "PartDesignWorkbench",
        "vibescript.read_placement",
        {
            "operation": "sketch",
            "plane": "XZ",
            "plane_offset_mm": 7,
        },
        document_thread_dispatch=None,
        cancellation_check=None,
        progress_callback=None,
    )

    assert result["ok"] is True
    assert result["origin_mm"] == [0.0, -7.0, 0.0]
    assert result["local_axes_in_global_coordinates"]["z"] == [0.0, -1.0, 0.0]
