# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing thread representations."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingThreadRepresentationSchema import (
    DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME,
    DRAWING_THREAD_REPRESENTATION_OPERATIONS,
    drawing_thread_representation_capability_definition,
)
from VibeCADNativeDrawingThreadRepresentationState import (
    NativeDrawingThreadRepresentationStateError,
    normalize_thread_bottom_host_plans,
    normalize_thread_side_host_plan,
)
from vibecad_tests.schema_test_helpers import exact_provider_branches


MOD_ROOT = Path(__file__).resolve().parents[2]


def _point(x: float, y: float) -> dict[str, float]:
    return {"x_mm": x, "y_mm": y}


def _format(width: float = 0.18) -> dict:
    return {
        "line_number": 1,
        "style_code": 1,
        "width_mm": width,
        "color_rgb": {"red": 0.1, "green": 0.2, "blue": 0.3},
        "visible": True,
    }


def _tag(index: int) -> str:
    return f"{index:08x}-{index:04x}-{index:04x}-{index:04x}-{index:012x}"


def _side_plan(kind: str, *, created: bool = False) -> dict:
    factor = 1.176 if kind == "hole_side" else 0.85
    delta_y = 4.0 * (factor - 1.0) / 2.0
    roles = ["first_thread_boundary", "second_thread_boundary"]
    segments = [
        (_point(0.0, -delta_y), _point(10.0, -delta_y)),
        (_point(0.0, 4.0 + delta_y), _point(10.0, 4.0 + delta_y)),
    ]
    if kind == "hole_side":
        roles.append("thread_end")
        segments.append((_point(10.0, -delta_y), _point(10.0, 4.0 + delta_y)))
    lines = []
    for index, (role, (start, end)) in enumerate(
        zip(roles, segments, strict=True), start=1
    ):
        segment = {
            "start_in_view_mm": start,
            "end_in_view_mm": end,
        }
        if created:
            segment["tag"] = _tag(index)
        lines.append(
            {
                "role": role,
                "segment": segment,
                "line_format": _format(0.35 if role == "thread_end" else 0.18),
            }
        )
    return {
        "kind": kind,
        "thread_factor": factor,
        "source_diameter_mm": 4.0,
        "source_subelements": ["Edge1", "Edge2"],
        "source_lines": {
            "first": {
                "start_in_view_mm": _point(0.0, 0.0),
                "end_in_view_mm": _point(10.0, 0.0),
            },
            "second": {
                "start_in_view_mm": _point(0.0, 4.0),
                "end_in_view_mm": _point(10.0, 4.0),
            },
        },
        "lines": lines,
    }


def _bottom_plans(kind: str, *, created: bool = False) -> list[dict]:
    factor = 1.176 if kind == "hole_bottom" else 0.85
    result = []
    for index, radius in enumerate((2.0, 3.0), start=1):
        plan = {
            "kind": kind,
            "source_subelement": f"Edge{index}",
            "center_in_view_mm": _point(index * 10.0, 0.0),
            "source_radius_mm": radius,
            "thread_factor": factor,
            "thread_radius_mm": radius * factor,
            "start_angle_degrees": 15.0,
            "end_angle_degrees": 285.0,
            "line_format": _format(),
        }
        if created:
            plan["arc_tag"] = _tag(index)
        result.append(plan)
    return result


def test_thread_schema_has_four_closed_action_specific_branches() -> None:
    definition = drawing_thread_representation_capability_definition()
    schema = definition.provider_schema(DRAWING_THREAD_REPRESENTATION_OPERATIONS)
    by_operation = exact_provider_branches(
        definition, DRAWING_THREAD_REPRESENTATION_OPERATIONS
    )

    assert definition.name == DRAWING_THREAD_REPRESENTATION_CAPABILITY_NAME
    assert "oneOf" not in schema["parameters"]
    assert schema["parameters"]["properties"]["operation"]["enum"] == list(
        DRAWING_THREAD_REPRESENTATION_OPERATIONS
    )
    assert tuple(by_operation) == DRAWING_THREAD_REPRESENTATION_OPERATIONS
    for operation in ("create_hole_side", "create_bolt_side"):
        branch = by_operation[operation]
        assert branch["required"] == [
            "operation",
            "page",
            "view",
            "boundary_edges",
        ]
        assert branch["properties"]["boundary_edges"]["minItems"] == 2
        assert branch["properties"]["boundary_edges"]["maxItems"] == 2
        assert branch["additionalProperties"] is False
    for operation in ("create_hole_bottom", "create_bolt_bottom"):
        branch = by_operation[operation]
        assert branch["required"] == [
            "operation",
            "page",
            "view",
            "circles",
        ]
        assert branch["properties"]["circles"]["minItems"] == 1
        assert branch["properties"]["circles"]["maxItems"] == 32
        assert branch["additionalProperties"] is False
    actions = {
        action for variant in definition.variants for action in variant.action_ids
    }
    assert actions == {
        "TechDraw_ExtensionThreadHoleSide",
        "TechDraw_ExtensionThreadHoleBottom",
        "TechDraw_ExtensionThreadBoltSide",
        "TechDraw_ExtensionThreadBoltBottom",
    }
    assert all(
        variant.surface_ids == frozenset({"drawing"}) for variant in definition.variants
    )
    assert all(
        variant.transaction_behavior == "document" for variant in definition.variants
    )
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "path" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 14 * 1024


@pytest.mark.parametrize("kind", ["hole_side", "bolt_side"])
def test_thread_side_state_requires_exact_host_geometry(kind: str) -> None:
    planned = normalize_thread_side_host_plan(_side_plan(kind), created=False)
    created = normalize_thread_side_host_plan(
        _side_plan(kind, created=True), created=True
    )
    assert planned["thread_factor"] == (1.176 if kind == "hole_side" else 0.85)
    assert len(created["lines"]) == (3 if kind == "hole_side" else 2)
    assert created["lines"][0]["segment"]["tag"] == _tag(1)

    malformed = _side_plan(kind)
    malformed["lines"][0]["segment"]["start_in_view_mm"]["y_mm"] += 1.0
    with pytest.raises(
        NativeDrawingThreadRepresentationStateError,
        match="inconsistent thread-side geometry",
    ):
        normalize_thread_side_host_plan(malformed, created=False)


@pytest.mark.parametrize("kind", ["hole_bottom", "bolt_bottom"])
def test_thread_bottom_state_requires_factor_span_and_unique_tags(kind: str) -> None:
    planned = normalize_thread_bottom_host_plans(_bottom_plans(kind), created=False)
    created = normalize_thread_bottom_host_plans(
        _bottom_plans(kind, created=True), created=True
    )
    assert planned[0]["thread_factor"] == (1.176 if kind == "hole_bottom" else 0.85)
    assert created[1]["arc_tag"] == _tag(2)

    malformed = _bottom_plans(kind)
    malformed[0]["end_angle_degrees"] = 280.0
    with pytest.raises(
        NativeDrawingThreadRepresentationStateError,
        match="inconsistent thread-bottom geometry",
    ):
        normalize_thread_bottom_host_plans(malformed, created=False)


def test_human_and_native_thread_paths_share_one_compiled_builder() -> None:
    command = (MOD_ROOT / "TechDraw" / "Gui" / "CommandExtensionPack.cpp").read_text(
        encoding="utf-8"
    )
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    builder = (
        MOD_ROOT / "TechDraw" / "Gui" / "ThreadRepresentationBuilder.cpp"
    ).read_text(encoding="utf-8")

    human = command[
        command.index("void execThreadHoleSide") : command.index(
            "DEF_STD_CMD_ACL(CmdTechDrawExtensionThreadsGroup)"
        )
    ]
    assert "createDrawingThreadSide" in human
    assert "createDrawingThreadBottom" in command
    assert "addCosmeticEdge" not in human
    assert "validateDrawingThreadSide" in binding
    assert "createDrawingThreadSide" in binding
    assert "validateDrawingThreadBottom" in binding
    assert "createDrawingThreadBottom" in binding
    assert "HoleThreadFactor = 1.176" in builder
    assert "BoltThreadFactor = 0.85" in builder
    assert "ThreadArcStartDegrees = 15.0" in builder
    assert "ThreadArcEndDegrees = 285.0" in builder
