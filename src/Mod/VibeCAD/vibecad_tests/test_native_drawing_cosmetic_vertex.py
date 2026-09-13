# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing cosmetic-vertex creation."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingCosmeticVertexSchema import (
    DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME,
    DRAWING_COSMETIC_VERTEX_OPERATIONS,
    drawing_cosmetic_vertex_capability_definition,
)
from VibeCADNativeDrawingCosmeticVertexState import (
    NativeDrawingCosmeticVertexStateError,
    normalize_explicit_vertex_host_plan,
    normalize_midpoint_vertex_host_plan,
    normalize_offset_vertex_host_plan,
    normalize_quadrant_vertex_host_plan,
    normalize_vertex_intersection_host_plan,
)
from vibecad_tests.schema_test_helpers import exact_provider_branches


MOD_ROOT = Path(__file__).resolve().parents[2]


def _point(x: float, y: float) -> dict[str, float]:
    return {"x_mm": x, "y_mm": y}


def _format() -> dict:
    return {
        "color_rgb": {"red": 0.0, "green": 0.0, "blue": 0.0},
        "size_mm": 1.0,
        "style_code": 1,
        "visible": True,
    }


def _tag(index: int) -> str:
    return f"{index:08x}-{index:04x}-{index:04x}-{index:04x}-{index:012x}"


def _intersection_plan(*, created: bool = False) -> dict:
    vertices = []
    for index, point in enumerate((_point(2.0, 3.0), _point(8.0, -1.0)), start=1):
        vertex = {"point_in_view_mm": point, "vertex_format": _format()}
        if created:
            vertex["tag"] = _tag(index)
        vertices.append(vertex)
    return {
        "source_subelements": ["Edge1", "Edge2"],
        "vertices": vertices,
    }


def _offset_plan(*, created: bool = False) -> dict:
    vertex = {
        "point_in_view_mm": _point(12.5, -2.0),
        "vertex_format": _format(),
    }
    if created:
        vertex["tag"] = _tag(1)
    return {
        "source_subelement": "Vertex3",
        "source_point_in_view_mm": _point(10.0, 4.0),
        "offset_mm": _point(2.5, -6.0),
        "vertex": vertex,
    }


def _explicit_point_plan(*, created: bool = False) -> dict:
    result = {
        "point_in_view_mm": _point(-7.25, 9.5),
        "vertex_format": _format(),
    }
    if created:
        result["tag"] = _tag(1)
    return result


def _midpoint_plan(*, created: bool = False) -> dict:
    midpoints = []
    for index, (source, point) in enumerate(
        (("Edge1", _point(-4.0, 2.0)), ("Edge3", _point(8.5, -1.25))),
        start=1,
    ):
        vertex = {"point_in_view_mm": point, "vertex_format": _format()}
        if created:
            vertex["tag"] = _tag(index)
        midpoints.append({"source_subelement": source, "vertex": vertex})
    return {"midpoints": midpoints}


def _quadrant_plan(*, created: bool = False) -> dict:
    vertices = []
    for index, point in enumerate(
        (_point(7.0, 0.0), _point(0.0, 7.0), _point(-7.0, 0.0)),
        start=1,
    ):
        vertex = {"point_in_view_mm": point, "vertex_format": _format()}
        if created:
            vertex["tag"] = _tag(index)
        vertices.append(vertex)
    return {"sources": [{"source_subelement": "Edge4", "vertices": vertices}]}


def test_cosmetic_vertex_schema_has_five_closed_exact_branches() -> None:
    definition = drawing_cosmetic_vertex_capability_definition()
    schema = definition.provider_schema(DRAWING_COSMETIC_VERTEX_OPERATIONS)
    by_operation = exact_provider_branches(
        definition, DRAWING_COSMETIC_VERTEX_OPERATIONS
    )

    assert definition.name == DRAWING_COSMETIC_VERTEX_CAPABILITY_NAME
    assert "oneOf" not in schema["parameters"]
    assert schema["parameters"]["properties"]["operation"]["enum"] == list(
        DRAWING_COSMETIC_VERTEX_OPERATIONS
    )
    assert tuple(by_operation) == DRAWING_COSMETIC_VERTEX_OPERATIONS
    assert by_operation["create_intersections"]["required"] == [
        "operation",
        "page",
        "view",
        "edges",
    ]
    assert by_operation["create_intersections"]["properties"]["edges"]["minItems"] == 2
    assert by_operation["create_intersections"]["properties"]["edges"]["maxItems"] == 2
    assert by_operation["create_offset"]["required"] == [
        "operation",
        "page",
        "view",
        "source_vertex",
        "offset_mm",
    ]
    assert (
        by_operation["create_offset"]["properties"]["offset_mm"]["additionalProperties"]
        is False
    )
    assert by_operation["create_point"]["required"] == [
        "operation",
        "page",
        "view",
        "point_in_view_mm",
    ]
    point_schema = by_operation["create_point"]["properties"]["point_in_view_mm"]
    assert point_schema["required"] == ["x_mm", "y_mm"]
    assert point_schema["additionalProperties"] is False
    midpoint_edges = by_operation["create_midpoints"]["properties"]["edges"]
    assert midpoint_edges["minItems"] == 1
    assert midpoint_edges["maxItems"] == 64
    quadrant_edges = by_operation["create_quadrants"]["properties"]["edges"]
    assert quadrant_edges["minItems"] == 1
    assert quadrant_edges["maxItems"] == 64
    assert all(
        branch["additionalProperties"] is False
        for branch in by_operation.values()
    )
    assert {
        action for variant in definition.variants for action in variant.action_ids
    } == {
        "TechDraw_ExtensionVertexAtIntersection",
        "TechDraw_CommandAddOffsetVertex",
        "TechDraw_CosmeticVertex",
        "TechDraw_Midpoints",
        "TechDraw_Quadrants",
    }
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 14 * 1024


def test_intersection_state_preserves_every_host_point_and_requires_tags() -> None:
    planned = normalize_vertex_intersection_host_plan(
        _intersection_plan(), created=False
    )
    created = normalize_vertex_intersection_host_plan(
        _intersection_plan(created=True), created=True
    )
    assert [item["point_in_view_mm"] for item in planned["vertices"]] == [
        _point(2.0, 3.0),
        _point(8.0, -1.0),
    ]
    assert created["vertices"][1]["tag"] == _tag(2)

    malformed = _intersection_plan(created=True)
    malformed["vertices"][1]["tag"] = _tag(1)
    with pytest.raises(
        NativeDrawingCosmeticVertexStateError,
        match="duplicate intersection-vertex tags",
    ):
        normalize_vertex_intersection_host_plan(malformed, created=True)


def test_offset_state_requires_exact_host_vector_math_and_allows_zero() -> None:
    planned = normalize_offset_vertex_host_plan(_offset_plan(), created=False)
    created = normalize_offset_vertex_host_plan(
        _offset_plan(created=True), created=True
    )
    assert planned["offset_mm"] == _point(2.5, -6.0)
    assert created["vertex"]["tag"] == _tag(1)

    zero = _offset_plan()
    zero["source_point_in_view_mm"] = _point(10.0, 4.0)
    zero["offset_mm"] = _point(0.0, 0.0)
    zero["vertex"]["point_in_view_mm"] = _point(10.0, 4.0)
    assert normalize_offset_vertex_host_plan(zero, created=False)[
        "offset_mm"
    ] == _point(0.0, 0.0)

    malformed = _offset_plan()
    malformed["vertex"]["point_in_view_mm"]["x_mm"] += 1.0
    with pytest.raises(
        NativeDrawingCosmeticVertexStateError,
        match="inconsistent offset-vertex point",
    ):
        normalize_offset_vertex_host_plan(malformed, created=False)


def test_explicit_point_state_preserves_canonical_view_coordinates() -> None:
    planned = normalize_explicit_vertex_host_plan(
        _explicit_point_plan(), created=False
    )
    created = normalize_explicit_vertex_host_plan(
        _explicit_point_plan(created=True), created=True
    )
    assert planned["point_in_view_mm"] == _point(-7.25, 9.5)
    assert created["tag"] == _tag(1)

    malformed = _explicit_point_plan()
    malformed["unexpected"] = True
    with pytest.raises(
        NativeDrawingCosmeticVertexStateError,
        match="malformed cosmetic-vertex point",
    ):
        normalize_explicit_vertex_host_plan(malformed, created=False)


def test_midpoint_state_preserves_ordered_source_associations_and_tags() -> None:
    planned = normalize_midpoint_vertex_host_plan(_midpoint_plan(), created=False)
    created = normalize_midpoint_vertex_host_plan(
        _midpoint_plan(created=True), created=True
    )
    assert [item["source_subelement"] for item in planned["midpoints"]] == [
        "Edge1",
        "Edge3",
    ]
    assert created["midpoints"][1]["vertex"]["tag"] == _tag(2)

    duplicate = _midpoint_plan()
    duplicate["midpoints"][1]["source_subelement"] = "Edge1"
    with pytest.raises(
        NativeDrawingCosmeticVertexStateError,
        match="duplicate midpoint sources",
    ):
        normalize_midpoint_vertex_host_plan(duplicate, created=False)


def test_quadrant_state_preserves_three_ordered_points_per_source() -> None:
    planned = normalize_quadrant_vertex_host_plan(_quadrant_plan(), created=False)
    created = normalize_quadrant_vertex_host_plan(
        _quadrant_plan(created=True), created=True
    )
    assert planned["sources"][0]["source_subelement"] == "Edge4"
    assert len(planned["sources"][0]["vertices"]) == 3
    assert created["sources"][0]["vertices"][2]["tag"] == _tag(3)


def test_human_and_native_vertex_paths_share_one_compiled_builder() -> None:
    command = (MOD_ROOT / "TechDraw" / "Gui" / "CommandExtensionPack.cpp").read_text(
        encoding="utf-8"
    )
    offset_command = (
        MOD_ROOT / "TechDraw" / "TechDrawTools" / "CommandVertexCreations.py"
    ).read_text(encoding="utf-8")
    offset_task = (
        MOD_ROOT / "TechDraw" / "TechDrawTools" / "TaskAddOffsetVertex.py"
    ).read_text(encoding="utf-8")
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    builder = (MOD_ROOT / "TechDraw" / "Gui" / "CosmeticVertexBuilder.cpp").read_text(
        encoding="utf-8"
    )
    direct_task = (MOD_ROOT / "TechDraw" / "Gui" / "TaskCosVertex.cpp").read_text(
        encoding="utf-8"
    )
    annotate_command = (
        MOD_ROOT / "TechDraw" / "Gui" / "CommandAnnotate.cpp"
    ).read_text(encoding="utf-8")

    human = command[
        command.index(
            "void CmdTechDrawExtensionVertexAtIntersection::activated"
        ) : command.index("bool CmdTechDrawExtensionVertexAtIntersection::isActive")
    ]
    assert "createDrawingVertexIntersections" in human
    assert "addCosmeticVertex" not in human
    assert "selected.SubElementNames[0]" in offset_command
    assert "TechDrawGui.createDrawingOffsetVertex" in offset_task
    assert "validateDrawingVertexIntersections" in binding
    assert "createDrawingVertexIntersections" in binding
    assert "validateDrawingOffsetVertex" in binding
    assert "createDrawingOffsetVertex" in binding
    assert "validateDrawingCosmeticVertexPoint" in binding
    assert "createDrawingCosmeticVertexPoint" in binding
    assert "validateDrawingMidpointVertices" in binding
    assert "createDrawingMidpointVertices" in binding
    assert "validateDrawingQuadrantVertices" in binding
    assert "createDrawingQuadrantVertices" in binding
    add_direct = direct_task[
        direct_task.index("void TaskCosVertex::addCosVertex") : direct_task.index(
            "//********** Tracker routines"
        )
    ]
    assert "createDrawingCosmeticVertexPoint" in add_direct
    assert "addCosmeticVertex" not in add_direct
    midpoint_start = annotate_command.index("void execMidpoints(Gui::Command* cmd)\n{")
    midpoint_human = annotate_command[
        midpoint_start : annotate_command.index(
            "void execQuadrants(Gui::Command* cmd)\n{",
            midpoint_start,
        )
    ]
    assert "validateDrawingMidpointVertices" in midpoint_human
    assert "createDrawingMidpointVertices" in midpoint_human
    assert "addCosmeticVertex" not in midpoint_human
    quadrant_start = annotate_command.index("void execQuadrants(Gui::Command* cmd)\n{")
    quadrant_human = annotate_command[
        quadrant_start : annotate_command.index(
            "void execCenterLine", quadrant_start
        )
    ]
    assert "validateDrawingQuadrantVertices" in quadrant_human
    assert "createDrawingQuadrantVertices" in quadrant_human
    assert "addCosmeticVertex" not in quadrant_human
    assert "first->intersection(second)" in builder
    assert "sourcePoint + offsetInViewMm" in builder
    assert "validateDrawingCosmeticVertexPoint" in builder
    assert "edge->getMidPoint()" in builder
    assert "edge->getQuads()" in builder
    assert "defaultFormat()" in builder
