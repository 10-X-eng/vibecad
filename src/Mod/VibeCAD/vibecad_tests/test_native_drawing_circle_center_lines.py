# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contract tests for exact Drawing circle centerlines."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingCircleCenterLineSchema import (
    DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
    DRAWING_CIRCLE_CENTER_LINE_OPERATIONS,
    drawing_circle_center_line_capability_definition,
)
from VibeCADNativeDrawingCircleCenterLineState import (
    NativeDrawingCircleCenterLineStateError,
    normalize_circle_center_line_host_pairs,
)


MOD_ROOT = Path(__file__).resolve().parents[2]


def _point(x: float, y: float) -> dict[str, float]:
    return {"x_mm": x, "y_mm": y}


def _pair(*, created: bool = False) -> dict:
    horizontal = {
        "start_in_view_mm": _point(14.0, 2.0),
        "end_in_view_mm": _point(-10.0, 2.0),
    }
    vertical = {
        "start_in_view_mm": _point(2.0, 14.0),
        "end_in_view_mm": _point(2.0, -10.0),
    }
    if created:
        horizontal["tag"] = "11111111-1111-1111-1111-111111111111"
        vertical["tag"] = "22222222-2222-2222-2222-222222222222"
    return {
        "source_subelement": "Edge3",
        "geometry_configuration": "circle",
        "center_in_view_mm": _point(2.0, 2.0),
        "radius_mm": 10.0,
        "outside_extension_mm": 2.0,
        "horizontal": horizontal,
        "vertical": vertical,
        "line_format": {
            "line_number": 5,
            "style_code": 1,
            "width_mm": 0.35,
            "color_rgb": {"red": 0.1, "green": 0.2, "blue": 0.3},
            "visible": True,
        },
    }


def test_circle_centerline_schema_is_one_closed_exact_operation() -> None:
    definition = drawing_circle_center_line_capability_definition()
    schema = definition.provider_schema(
        DRAWING_CIRCLE_CENTER_LINE_OPERATIONS
    )
    branches = schema["parameters"]["oneOf"]

    assert definition.name == DRAWING_CIRCLE_CENTER_LINE_CAPABILITY_NAME
    assert DRAWING_CIRCLE_CENTER_LINE_OPERATIONS == ("create",)
    assert len(branches) == 1
    create = branches[0]
    assert create["properties"]["operation"]["const"] == "create"
    assert create["required"] == ["operation", "page", "view", "circles"]
    assert create["additionalProperties"] is False
    circles = create["properties"]["circles"]
    assert circles["minItems"] == 1
    assert circles["maxItems"] == 32
    assert circles["items"]["additionalProperties"] is False
    assert circles["items"]["required"] == [
        "subelement",
        "expected_element_state_sha256",
    ]
    assert circles["items"]["properties"]["subelement"]["pattern"] == (
        r"^Edge(?:0|[1-9][0-9]*)$"
    )
    variant = definition.variants[0]
    assert variant.action_ids == frozenset(
        {"TechDraw_ExtensionCircleCenterLines"}
    )
    assert variant.surface_ids == frozenset({"drawing"})
    assert variant.exact_target_type == (
        "ExactDrawingCircularEdgesAndPersistentCrossCenterlines"
    )
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 5 * 1024


def test_circle_centerline_host_state_requires_exact_cross_and_tags() -> None:
    planned = normalize_circle_center_line_host_pairs(
        [_pair()],
        created=False,
    )
    created = normalize_circle_center_line_host_pairs(
        [_pair(created=True)],
        created=True,
    )
    assert planned[0]["source_subelement"] == "Edge3"
    assert created[0]["horizontal"]["tag"].startswith("11111111")

    malformed = _pair()
    malformed["vertical"]["end_in_view_mm"]["x_mm"] = 3.0
    with pytest.raises(
        NativeDrawingCircleCenterLineStateError,
        match="inconsistent circle centerline cross",
    ):
        normalize_circle_center_line_host_pairs([malformed], created=False)


def test_human_command_and_native_share_the_compiled_builder() -> None:
    command = (
        MOD_ROOT / "TechDraw" / "Gui" / "CommandExtensionPack.cpp"
    ).read_text(encoding="utf-8")
    binding = (
        MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp"
    ).read_text(encoding="utf-8")
    builder = (
        MOD_ROOT / "TechDraw" / "Gui" / "CircleCenterLineBuilder.cpp"
    ).read_text(encoding="utf-8")

    assert "createDrawingCircleCenterLines" in command
    assert "createDrawingCircleCenterLines" in binding
    assert "validateDrawingCircleCenterLines" in binding
    assert "addCosmeticEdge" not in command[
        command.index("void execCircleCenterLines") : command.index(
            "DEF_STD_CMD_A(CmdTechDrawExtensionCircleCenterLines)"
        )
    ]
    assert "addCosmeticEdge" in builder
    assert "OutsideCircleMm = 2.0" in builder
    assert "CenterLineStyle" in builder
