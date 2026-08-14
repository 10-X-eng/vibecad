# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contract for exact Native Drawing active-view capture."""

from __future__ import annotations

import json

from VibeCADNativeDrawingActiveView import (
    DEFAULT_CAPTURE_HEIGHT_PX,
    DEFAULT_CAPTURE_WIDTH_PX,
    MAX_CAPTURE_DIMENSION_PX,
    MAX_CAPTURE_PIXELS,
)
from VibeCADNativeDrawingActiveViewSchema import (
    drawing_active_view_capability_definition,
)


def _branch(schema: dict, operation: str) -> dict:
    return next(
        branch
        for branch in schema["parameters"]["oneOf"]
        if branch["properties"]["operation"]["const"] == operation
    )


def test_active_view_is_one_closed_path_private_operation() -> None:
    definition = drawing_active_view_capability_definition()
    assert definition.primary_classification == "mutation"
    assert len(definition.variants) == 1
    variant = definition.variants[0]
    assert variant.operation == "create_active_view"
    assert variant.action_ids == frozenset({"TechDraw_ActiveView"})
    assert variant.surface_ids == frozenset({"drawing"})
    assert variant.exact_target_type == (
        "ExactDrawingPageActive3DViewportAndCaptureSettings"
    )
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False

    schema = definition.provider_schema((variant.operation,))
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":")).casefold()
    assert "unknown" not in encoded
    assert "path" not in encoded
    assert "data_url" not in encoded
    branch = _branch(schema, variant.operation)
    assert branch["additionalProperties"] is False
    assert branch["required"] == [
        "operation",
        "label",
        "page",
        "viewport",
        "position",
        "scale",
        "crop",
        "background",
    ]
    assert branch["properties"]["page"]["required"] == [
        "object_name",
        "expected_state_sha256",
    ]
    assert branch["properties"]["viewport"]["required"] == [
        "expected_state_sha256"
    ]


def test_active_view_publishes_all_capture_choices_and_runtime_bounds() -> None:
    definition = drawing_active_view_capability_definition()
    schema = definition.provider_schema(("create_active_view",))
    branch = _branch(schema, "create_active_view")
    crop = branch["properties"]["crop"]["oneOf"]
    background = branch["properties"]["background"]["oneOf"]

    assert tuple(item["properties"]["kind"]["const"] for item in crop) == (
        "full",
        "rectangle",
    )
    assert crop[1]["required"] == ["kind", "width_mm", "height_mm"]
    assert tuple(item["properties"]["kind"]["const"] for item in background) == (
        "transparent",
        "viewport",
        "solid",
    )
    assert background[2]["properties"]["rgb"]["additionalProperties"] is False
    assert DEFAULT_CAPTURE_WIDTH_PX == 1280
    assert DEFAULT_CAPTURE_HEIGHT_PX == 1024
    assert MAX_CAPTURE_DIMENSION_PX == 4096
    assert MAX_CAPTURE_PIXELS == 16 * 1024 * 1024
