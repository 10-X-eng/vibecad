# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing parallel and perpendicular lines."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingCosmeticLineSchema import (
    DRAWING_COSMETIC_LINE_CAPABILITY_NAME,
    DRAWING_COSMETIC_LINE_OPERATIONS,
    drawing_cosmetic_line_capability_definition,
)
from VibeCADNativeDrawingCosmeticLineState import (
    NativeDrawingCosmeticLineStateError,
    normalize_cosmetic_line_host_plan,
    normalize_two_point_cosmetic_line_host_plan,
)
from vibecad_tests.schema_test_helpers import exact_provider_branches


MOD_ROOT = Path(__file__).resolve().parents[2]


def _point(x: float, y: float) -> dict[str, float]:
    return {"x_mm": x, "y_mm": y}


def _format() -> dict:
    return {
        "line_number": 3,
        "style_code": 2,
        "width_mm": 0.5,
        "color_rgb": {"red": 0.1, "green": 0.2, "blue": 0.3},
        "visible": True,
    }


def _line(start: dict[str, float], end: dict[str, float]) -> dict:
    return {
        "start_in_view_mm": start,
        "end_in_view_mm": end,
        "length_mm": 10.0,
    }


def _plan(construction: str, line: dict) -> dict:
    return {
        "construction": construction,
        "reference_edge_subelement": "Edge7",
        "through_vertex_subelement": "Vertex4",
        "reference_start_in_view_mm": _point(0.0, 0.0),
        "reference_end_in_view_mm": _point(6.0, 8.0),
        "through_point_in_view_mm": _point(20.0, 30.0),
        "line": line,
        "line_format": _format(),
    }


def test_cosmetic_line_schema_has_three_closed_exact_role_branches() -> None:
    definition = drawing_cosmetic_line_capability_definition()
    schema = definition.provider_schema(DRAWING_COSMETIC_LINE_OPERATIONS)
    by_operation = exact_provider_branches(
        definition, DRAWING_COSMETIC_LINE_OPERATIONS
    )

    assert definition.name == DRAWING_COSMETIC_LINE_CAPABILITY_NAME
    assert "oneOf" not in schema["parameters"]
    assert schema["parameters"]["properties"]["operation"]["enum"] == list(
        DRAWING_COSMETIC_LINE_OPERATIONS
    )
    assert tuple(by_operation) == DRAWING_COSMETIC_LINE_OPERATIONS
    named_role_branches = [
        by_operation["create_parallel"],
        by_operation["create_perpendicular"],
    ]
    assert all(
        branch["required"]
        == ["operation", "page", "view", "reference_edge", "through_vertex"]
        for branch in named_role_branches
    )
    assert all(
        branch["additionalProperties"] is False
        for branch in by_operation.values()
    )
    assert all(
        branch["properties"]["reference_edge"]["properties"]["subelement"][
            "pattern"
        ].startswith("^Edge")
        for branch in named_role_branches
    )
    assert all(
        branch["properties"]["through_vertex"]["properties"]["subelement"][
            "pattern"
        ].startswith("^Vertex")
        for branch in named_role_branches
    )
    between_vertices = by_operation["create_between_vertices"]
    assert between_vertices["required"] == ["operation", "page", "view", "vertices"]
    assert between_vertices["properties"]["vertices"]["minItems"] == 2
    assert between_vertices["properties"]["vertices"]["maxItems"] == 2
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert "start_in_view_mm" not in encoded
    assert "line_format" not in encoded
    assert len(encoded.encode("utf-8")) < 16 * 1024


def test_cosmetic_line_state_validates_both_exact_constructions() -> None:
    parallel = normalize_cosmetic_line_host_plan(
        _plan("parallel", _line(_point(17.0, 26.0), _point(23.0, 34.0))),
        created=False,
    )
    perpendicular = normalize_cosmetic_line_host_plan(
        _plan("perpendicular", _line(_point(16.0, 33.0), _point(24.0, 27.0))),
        created=False,
    )

    assert parallel["construction"] == "parallel"
    assert perpendicular["construction"] == "perpendicular"
    assert parallel["line"]["length_mm"] == 10.0
    assert perpendicular["through_point_in_view_mm"] == _point(20.0, 30.0)

    malformed = _plan(
        "perpendicular",
        _line(_point(17.0, 26.0), _point(23.0, 34.0)),
    )
    with pytest.raises(
        NativeDrawingCosmeticLineStateError,
        match="not perpendicular",
    ):
        normalize_cosmetic_line_host_plan(malformed, created=False)


def test_cosmetic_line_state_canonicalizes_unoriented_endpoints_and_tags() -> None:
    raw = _plan("parallel", _line(_point(23.0, 34.0), _point(17.0, 26.0)))
    raw["line_tag"] = "01234567-89ab-cdef-0123-456789abcdef"

    normalized = normalize_cosmetic_line_host_plan(raw, created=True)

    assert normalized["line"]["start_in_view_mm"] == _point(17.0, 26.0)
    assert normalized["line"]["end_in_view_mm"] == _point(23.0, 34.0)
    assert normalized["line_tag"] == raw["line_tag"]


def test_two_point_cosmetic_line_state_preserves_ordered_sources() -> None:
    raw = {
        "construction": "between_vertices",
        "source_vertex_subelements": ["Vertex2", "Vertex9"],
        "line": _line(_point(0.0, 0.0), _point(6.0, 8.0)),
        "line_format": _format(),
    }
    planned = normalize_two_point_cosmetic_line_host_plan(raw, created=False)
    assert planned["source_vertex_subelements"] == ["Vertex2", "Vertex9"]

    raw["line_tag"] = "01234567-89ab-cdef-0123-456789abcdef"
    created = normalize_two_point_cosmetic_line_host_plan(raw, created=True)
    assert created["line_tag"] == raw["line_tag"]


def test_human_and_native_cosmetic_line_paths_share_one_compiled_builder() -> None:
    command = (MOD_ROOT / "TechDraw" / "Gui" / "CommandExtensionPack.cpp").read_text(
        encoding="utf-8"
    )
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    builder = (MOD_ROOT / "TechDraw" / "Gui" / "CosmeticLineBuilder.cpp").read_text(
        encoding="utf-8"
    )
    task = (MOD_ROOT / "TechDraw" / "Gui" / "TaskCosmeticLine.cpp").read_text(
        encoding="utf-8"
    )

    assert command.count("createDrawingCosmeticLine(") == 1
    assert (
        "CosmeticEdge::makeLineFromCanonicalPoints"
        not in command[
            command.index("void execLineParallelPerpendicular") : command.index(
                "DEF_STD_CMD_A(CmdTechDrawExtensionLineParallel)"
            )
        ]
    )
    assert "validateDrawingCosmeticLine" in binding
    assert "createDrawingCosmeticLine" in binding
    assert "drawingPersistentCosmeticLine" in binding
    assert "drawingCosmeticLines" in binding
    assert "validateDrawingTwoPointCosmeticLine" in binding
    assert "createDrawingTwoPointCosmeticLine" in binding
    assert "LineFormat::getCurrentLineFormat" in builder
    assert "DrawingCosmeticLineConstruction::Perpendicular" in builder
    assert "createDrawingCosmeticLineSegment" in task
    assert "addCosmeticEdge" not in task
