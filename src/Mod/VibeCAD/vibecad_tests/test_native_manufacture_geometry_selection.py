# SPDX-License-Identifier: LGPL-2.1-or-later

"""Canonical Native CAM geometry selections."""

from __future__ import annotations

import pytest

from VibeCADNativeManufactureErrors import NativeManufactureError
from VibeCADNativeManufactureAdaptive import AdaptiveCreateSpec, _normalize_parameters
from VibeCADNativeManufactureOperationSupport import (
    merge_subelement_geometry_items,
)
from VibeCADNativeManufactureFocusedOperationBindings import (
    _lower_focused_operation_arguments,
)


def _item(model: str, state: str, *subelements: str) -> dict:
    return {
        "model": {
            "object_name": model,
            "expected_state_sha256": state,
        },
        "subelements": list(subelements),
    }


def test_repeated_exact_model_selections_are_merged_in_request_order() -> None:
    body_state = "a" * 64
    fixture_state = "b" * 64

    assert merge_subelement_geometry_items(
        [
            _item("Body", body_state, "Face3"),
            _item("Fixture", fixture_state, "Edge2"),
            _item("Body", body_state, "Face7", "Face3"),
        ],
        noun="Pocket",
        max_items=32,
        max_subelements=64,
    ) == (
        ("Body", body_state, ("Face3", "Face7")),
        ("Fixture", fixture_state, ("Edge2",)),
    )


def test_repeated_model_with_conflicting_revision_is_rejected() -> None:
    with pytest.raises(NativeManufactureError, match="conflicting exact states"):
        merge_subelement_geometry_items(
            [
                _item("Body", "a" * 64, "Face3"),
                _item("Body", "b" * 64, "Face7"),
            ],
            noun="Pocket",
            max_items=32,
            max_subelements=64,
        )


def test_adaptive_accepts_the_toolpath_engines_minimum_tolerance() -> None:
    parameters = _normalize_parameters(
        AdaptiveCreateSpec(
            label="Adaptive",
            job={},
            tool_controller={},
            geometry={},
            adaptive={
                "cut_region": "inside",
                "operation_type": "clearing",
                "tolerance_mm": 0.001,
                "stepover_percent": 20.0,
                "lift_distance_mm": 0.0,
                "keep_tool_down_ratio": 3.0,
                "xy_stock_to_leave_mm": 0.0,
                "force_inside_out": False,
                "finishing_profile": True,
                "use_outline": False,
                "rest_machining": False,
            },
            helix_entry={
                "max_pitch_mm": 0.0,
                "max_ramp_angle_degrees": 5.0,
                "cone_angle_degrees": 0.0,
                "max_diameter_percent": 100,
                "min_diameter_percent": 10,
            },
            depths={
                "start_depth_mm": 10.0,
                "final_depth_mm": 0.0,
                "step_down_mm": 2.0,
                "finish_step_mm": 0.0,
            },
            heights={
                "safe_height_mm": 12.0,
                "clearance_height_mm": 15.0,
            },
            extensions={},
            coolant="none",
        )
    )

    assert parameters.tolerance_mm == 0.001


def test_focused_adaptive_uses_the_default_operation_runtime() -> None:
    arguments = {
        "operation": "adaptive",
        "job": {"object_name": "Setup", "expected_state_sha256": "a" * 64},
        "tool_controller": {
            "object_name": "ToolController",
            "expected_state_sha256": "b" * 64,
        },
        "geometry": [],
    }

    assert _lower_focused_operation_arguments(arguments) == {
        **arguments,
        "operation": "adaptive_defaults",
    }
