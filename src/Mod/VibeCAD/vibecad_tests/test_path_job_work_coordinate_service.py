# SPDX-License-Identifier: LGPL-2.1-or-later

"""CAM workpiece frames have one exact setup-scoped meaning."""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest


_MODULE_PATH = (
    Path(__file__).resolve().parents[2]
    / "CAM"
    / "Path"
    / "Main"
    / "JobWorkCoordinate.py"
)


def _service():
    spec = importlib.util.spec_from_file_location(
        "vibecad_test_job_work_coordinate",
        _MODULE_PATH,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("CAM work-coordinate service could not be loaded")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_workpiece_frame_normalizes_authored_coordinates() -> None:
    assert _service().normalize_workpiece_frame(
        {
            "origin_mm": {"x": 10, "y": -4, "z": 2},
            "x_direction_hint": {"x": 0, "y": 2, "z": 0},
            "z_direction": {"x": 0, "y": 0, "z": 3},
        }
    ) == {
        "origin_mm": {"x": 10.0, "y": -4.0, "z": 2.0},
        "x_direction_hint": {"x": 0.0, "y": 1.0, "z": 0.0},
        "z_direction": {"x": 0.0, "y": 0.0, "z": 1.0},
    }


@pytest.mark.parametrize(
    "frame",
    (
        {},
        {
            "origin_mm": {"x": 0, "y": 0, "z": 0},
            "x_direction_hint": {"x": 0, "y": 0, "z": 0},
            "z_direction": {"x": 0, "y": 0, "z": 1},
        },
        {
            "origin_mm": {"x": 0, "y": 0, "z": 0},
            "x_direction_hint": {"x": 0, "y": 0, "z": 1},
            "z_direction": {"x": 0, "y": 0, "z": 1},
        },
    ),
)
def test_workpiece_frame_rejects_incomplete_or_degenerate_axes(frame: dict) -> None:
    with pytest.raises(ValueError):
        _service().normalize_workpiece_frame(frame)
