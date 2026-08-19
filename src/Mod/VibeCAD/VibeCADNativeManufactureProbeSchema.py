# SPDX-License-Identifier: LGPL-2.1-or-later

"""Closed provider contract for bounded stock-probing grids."""

from __future__ import annotations

from VibeCADNativeCapabilityRegistry import (
    NativeCapabilityDefinition,
    NativeCapabilityRegistry,
    NativeCapabilityVariant,
)
from VibeCADNativeManufactureContract import PATH_OPERATION_LABEL_SCHEMA


MANUFACTURE_PROBE_CAPABILITY_NAME = "manufacture.probe"

_OBJECT_NAME = {
    "type": "string",
    "pattern": r"^[A-Za-z_][A-Za-z0-9_]*$",
    "maxLength": 128,
}
_SHA256 = {
    "type": "string",
    "pattern": r"^[0-9a-f]{64}$",
    "minLength": 64,
    "maxLength": 64,
}
_DISTANCE_MM = {
    "type": "number",
    "minimum": -1_000_000.0,
    "maximum": 1_000_000.0,
}


def _closed(properties: dict, required: tuple[str, ...]) -> dict:
    return {
        "type": "object",
        "properties": properties,
        "required": list(required),
        "additionalProperties": False,
    }


_EXACT_TARGET = _closed(
    {
        "object_name": _OBJECT_NAME,
        "expected_state_sha256": _SHA256,
    },
    ("object_name", "expected_state_sha256"),
)
_POINT_COUNT = {
    "type": "integer",
    "minimum": 3,
    "maximum": 64,
    "description": (
        "Three through 64 points on this axis. The two axis counts multiplied "
        "together may not exceed 1024 probe points."
    ),
}
_GRID = _closed(
    {
        "point_count_x": _POINT_COUNT,
        "point_count_y": _POINT_COUNT,
        "x_offset_mm": _DISTANCE_MM,
        "y_offset_mm": _DISTANCE_MM,
    },
    ("point_count_x", "point_count_y", "x_offset_mm", "y_offset_mm"),
)
_MOTION = _closed(
    {
        "probe_depth_mm": {
            **_DISTANCE_MM,
            "description": (
                "Absolute Z depth at or above the exact stock bottom and strictly "
                "below its top."
            ),
        },
        "safe_height_mm": {
            **_DISTANCE_MM,
            "description": "Absolute Z height strictly above the exact stock top.",
        },
        "clearance_height_mm": {
            **_DISTANCE_MM,
            "description": "Absolute Z height at or above safe_height_mm.",
        },
    },
    ("probe_depth_mm", "safe_height_mm", "clearance_height_mm"),
)


def manufacture_probe_capability_definition() -> NativeCapabilityDefinition:
    return NativeCapabilityDefinition(
        name=MANUFACTURE_PROBE_CAPABILITY_NAME,
        description="Create a bounded stock-probing grid for one exact CAM Job.",
        primary_classification="mutation",
        variants=(
            NativeCapabilityVariant(
                operation="create_grid",
                description=(
                    "Create and verify an ordered 3-by-3 through 1024-point probing "
                    "grid across the exact current Job stock."
                ),
                action_ids=frozenset({"CAM_Probe"}),
                surface_ids=frozenset({"manufacture"}),
                exact_target_type="ExactCamJobProbeControllerAndBoundedStockGrid",
                transaction_behavior="document",
                background_required=False,
                parameters=_closed(
                    {
                        "label": PATH_OPERATION_LABEL_SCHEMA,
                        "job": _EXACT_TARGET,
                        "tool_controller": _EXACT_TARGET,
                        "grid": _GRID,
                        "motion": _MOTION,
                    },
                    ("label", "job", "tool_controller", "grid", "motion"),
                ),
            ),
        ),
    )


def register_manufacture_probe_capability_definition(
    registry: NativeCapabilityRegistry,
) -> None:
    if not isinstance(registry, NativeCapabilityRegistry):
        raise TypeError("registry must be a NativeCapabilityRegistry")
    registry.register_definition(manufacture_probe_capability_definition())
