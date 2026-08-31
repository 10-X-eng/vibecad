# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused provider contract for exact Native Drawing balloons."""

from __future__ import annotations

import json

from VibeCADNativeDrawingBalloonSchema import (
    DRAWING_BALLOON_OPERATIONS,
    drawing_balloon_capability_definition,
)
from vibecad_tests.schema_test_helpers import exact_provider_branches


def test_balloon_schema_has_four_closed_exact_operations() -> None:
    definition = drawing_balloon_capability_definition()
    schema = definition.provider_schema(DRAWING_BALLOON_OPERATIONS)
    branches = exact_provider_branches(definition, DRAWING_BALLOON_OPERATIONS)

    assert definition.preserve_operation_branches is False
    assert "oneOf" not in schema["parameters"]
    assert schema["parameters"]["properties"]["operation"]["enum"] == list(
        DRAWING_BALLOON_OPERATIONS
    )
    assert DRAWING_BALLOON_OPERATIONS == (
        "create",
        "set_text",
        "set_style",
        "move_bubble",
    )
    assert set(branches) == set(DRAWING_BALLOON_OPERATIONS)
    assert all(branch["additionalProperties"] is False for branch in branches.values())

    create = branches["create"]
    assert set(create["required"]) == {
        "operation",
        "label",
        "text",
        "page",
        "view",
        "anchor",
        "bubble_offset_in_view_mm",
    }
    assert create["properties"]["text"]["maxLength"] == 512
    assert create["properties"]["label"]["maxLength"] == 160
    anchor = create["properties"]["anchor"]
    assert anchor["additionalProperties"] is False
    assert anchor["required"] == ["subelement"]
    assert set(anchor["properties"]) == {"subelement"}
    assert anchor["properties"]["subelement"]["pattern"] == (
        r"^(?:Edge|Vertex)(?:0|[1-9][0-9]*)$"
    )
    offset = create["properties"]["bubble_offset_in_view_mm"]
    assert offset["required"] == ["x_mm", "y_mm"]
    assert offset["additionalProperties"] is False
    assert offset["properties"]["x_mm"]["minimum"] == -1000.0
    assert offset["properties"]["x_mm"]["maximum"] == 1000.0

    for operation in ("set_text", "set_style", "move_bubble"):
        target = branches[operation]["properties"]["balloon"]
        assert target["required"] == ["object_name", "expected_state_sha256"]
        assert target["additionalProperties"] is False
    assert branches["set_text"]["required"] == ["operation", "balloon", "text"]
    assert branches["move_bubble"]["required"] == [
        "operation",
        "balloon",
        "bubble_offset_in_view_mm",
    ]
    style = branches["set_style"]["properties"]["style"]
    assert style["additionalProperties"] is False
    assert set(style["required"]) == {
        "bubble_shape",
        "leader_end",
        "bubble_scale",
        "leader_end_scale",
        "kink_length_mm",
        "font_size_mm",
        "line_width_mm",
        "line_visible",
        "color_rgb",
    }
    assert style["properties"]["bubble_shape"]["enum"] == [
        "Circular",
        "None",
        "Triangle",
        "Inspection",
        "Hexagon",
        "Square",
        "Rectangle",
        "Line",
    ]
    color = style["properties"]["color_rgb"]
    assert color["required"] == ["red", "green", "blue"]
    assert color["additionalProperties"] is False

    variants = {variant.operation: variant for variant in definition.variants}
    assert all(
        variant.action_ids == frozenset({"TechDraw_Balloon"})
        and variant.surface_ids == frozenset({"drawing"})
        and variant.transaction_behavior == "document"
        and variant.background_required is False
        for variant in variants.values()
    )
    assert variants["create"].provider_supplemental is False
    assert variants["create"].exact_target_type == (
        "ExactDrawingProjectedBalloonAnchorAndPlacement"
    )
    assert all(
        variants[operation].provider_supplemental is True
        for operation in ("set_text", "set_style", "move_bubble")
    )
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 8 * 1024
