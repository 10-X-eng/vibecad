# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contract tests for exact Drawing bolt-circle centerlines."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingBoltCircleCenterLineSchema import (
    DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME,
    DRAWING_BOLT_CIRCLE_CENTER_LINE_OPERATIONS,
    drawing_bolt_circle_center_line_capability_definition,
)
from VibeCADNativeDrawingBoltCircleCenterLineState import (
    NativeDrawingBoltCircleCenterLineStateError,
    normalize_bolt_circle_center_line_host_plan,
)


MOD_ROOT = Path(__file__).resolve().parents[2]


def _point(x: float, y: float) -> dict[str, float]:
    return {"x_mm": x, "y_mm": y}


def _plan(*, created: bool = False) -> dict:
    centers = ((10.0, 0.0), (0.0, 10.0), (-10.0, 0.0))
    directions = ((1.0, 0.0), (0.0, 1.0), (-1.0, 0.0))
    holes = []
    for index, ((x, y), (dx, dy)) in enumerate(
        zip(centers, directions, strict=True), start=1
    ):
        center_line = {
            "start_in_view_mm": _point(x + dx * 1.1, y + dy * 1.1),
            "end_in_view_mm": _point(x - dx * 1.1, y - dy * 1.1),
        }
        if created:
            center_line["tag"] = (
                f"{index:08x}-{index:04x}-{index:04x}-{index:04x}-{index:012x}"
            )
        holes.append(
            {
                "source_subelement": f"Edge{index}",
                "geometry_configuration": "circle",
                "center_in_view_mm": _point(x, y),
                "radius_mm": 1.0,
                "pattern_radius_at_center_mm": 10.0,
                "pattern_radius_deviation_mm": 0.0,
                "center_line": center_line,
            }
        )
    result = {
        "pattern_center_in_view_mm": _point(0.0, 0.0),
        "pattern_radius_mm": 10.0,
        "maximum_pattern_radius_deviation_mm": 0.0,
        "pattern_radius_tolerance_mm": 0.0000001,
        "all_centers_on_pattern": True,
        "hole_center_line_extension_factor": 1.1,
        "line_format": {
            "line_number": 1,
            "style_code": 1,
            "width_mm": 0.35,
            "color_rgb": {"red": 0.1, "green": 0.2, "blue": 0.3},
            "visible": True,
        },
        "holes": holes,
    }
    if created:
        result["pattern_circle_tag"] = (
            "ffffffff-ffff-ffff-ffff-ffffffffffff"
        )
    return result


def test_bolt_circle_schema_is_one_closed_exact_operation() -> None:
    definition = drawing_bolt_circle_center_line_capability_definition()
    schema = definition.provider_schema(
        DRAWING_BOLT_CIRCLE_CENTER_LINE_OPERATIONS
    )
    branches = schema["parameters"]["oneOf"]

    assert definition.name == DRAWING_BOLT_CIRCLE_CENTER_LINE_CAPABILITY_NAME
    assert DRAWING_BOLT_CIRCLE_CENTER_LINE_OPERATIONS == ("create",)
    assert len(branches) == 1
    create = branches[0]
    assert create["properties"]["operation"]["const"] == "create"
    assert create["required"] == ["operation", "page", "view", "holes"]
    assert create["additionalProperties"] is False
    holes = create["properties"]["holes"]
    assert holes["minItems"] == 3
    assert holes["maxItems"] == 32
    assert holes["items"]["additionalProperties"] is False
    assert holes["items"]["required"] == [
        "subelement",
        "expected_element_state_sha256",
    ]
    variant = definition.variants[0]
    assert variant.action_ids == frozenset({"TechDraw_ExtensionHoleCircle"})
    assert variant.surface_ids == frozenset({"drawing"})
    assert variant.exact_target_type == (
        "ExactOrderedDrawingHoleCirclesAndDerivedBoltCircle"
    )
    assert variant.transaction_behavior == "document"
    assert variant.background_required is False
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert "first three" in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 5 * 1024


def test_bolt_circle_host_state_requires_radial_geometry_and_unique_tags() -> None:
    planned = normalize_bolt_circle_center_line_host_plan(
        _plan(), created=False
    )
    created = normalize_bolt_circle_center_line_host_plan(
        _plan(created=True), created=True
    )
    assert planned["pattern_radius_mm"] == 10.0
    assert created["holes"][0]["center_line"]["tag"].startswith("00000001")

    malformed = _plan()
    malformed["holes"][0]["center_line"]["start_in_view_mm"]["y_mm"] = 1.0
    with pytest.raises(
        NativeDrawingBoltCircleCenterLineStateError,
        match="inconsistent bolt-hole centerline geometry",
    ):
        normalize_bolt_circle_center_line_host_plan(malformed, created=False)


def test_human_command_and_native_share_the_bolt_circle_builder() -> None:
    command = (
        MOD_ROOT / "TechDraw" / "Gui" / "CommandExtensionPack.cpp"
    ).read_text(encoding="utf-8")
    binding = (
        MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp"
    ).read_text(encoding="utf-8")
    builder = (
        MOD_ROOT / "TechDraw" / "Gui" / "CircleCenterLineBuilder.cpp"
    ).read_text(encoding="utf-8")

    assert "createDrawingBoltCircleCenterLines" in command
    assert "createDrawingBoltCircleCenterLines" in binding
    assert "validateDrawingBoltCircleCenterLines" in binding
    human = command[
        command.index("void execHoleCircle") : command.index(
            "DEF_STD_CMD_A(CmdTechDrawExtensionHoleCircle)"
        )
    ]
    assert "addCosmeticEdge" not in human
    assert "createDrawingBoltCircleCenterLines" in human
    assert "Part::Geom2dCircle::getCircleCenter" in builder
    assert "HoleCenterLineExtensionFactor = 1.1" in builder
