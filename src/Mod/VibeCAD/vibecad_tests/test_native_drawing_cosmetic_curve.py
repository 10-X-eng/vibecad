# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing cosmetic circles and arcs."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingCosmeticCurveSchema import (
    DRAWING_COSMETIC_CURVE_CAPABILITY_NAME,
    DRAWING_COSMETIC_CURVE_OPERATIONS,
    drawing_cosmetic_curve_capability_definition,
)
from VibeCADNativeDrawingCosmeticCurveState import (
    NativeDrawingCosmeticCurveStateError,
    normalize_cosmetic_curve_host_plan,
)
from vibecad_tests.schema_test_helpers import exact_provider_branches


MOD_ROOT = Path(__file__).resolve().parents[2]


def _point(x: float, y: float) -> dict[str, float]:
    return {"x_mm": x, "y_mm": y}


def _format() -> dict:
    return {
        "line_number": 1,
        "style_code": 1,
        "width_mm": 0.35,
        "color_rgb": {"red": 0.1, "green": 0.2, "blue": 0.3},
        "visible": True,
    }


def _geometry(
    center: dict[str, float],
    radius: float,
    *,
    arc: bool = False,
    start: float | None = None,
    end: float | None = None,
) -> dict:
    return {
        "geometry_configuration": "circular_arc" if arc else "circle",
        "center_in_view_mm": center,
        "radius_mm": radius,
        "start_angle_degrees": start,
        "end_angle_degrees": end,
        "clockwise": False,
    }


def _plan(kind: str, points: list[dict[str, float]], geometry: dict) -> dict:
    return {
        "kind": kind,
        "source_subelements": [f"Vertex{index}" for index in range(1, len(points) + 1)],
        "source_points_in_view_mm": points,
        "geometry": geometry,
        "line_format": _format(),
    }


def test_cosmetic_curve_schema_has_four_closed_named_role_branches() -> None:
    definition = drawing_cosmetic_curve_capability_definition()
    schema = definition.provider_schema(DRAWING_COSMETIC_CURVE_OPERATIONS)
    by_operation = exact_provider_branches(
        definition, DRAWING_COSMETIC_CURVE_OPERATIONS
    )

    assert definition.name == DRAWING_COSMETIC_CURVE_CAPABILITY_NAME
    assert "oneOf" not in schema["parameters"]
    assert schema["parameters"]["properties"]["operation"]["enum"] == list(
        DRAWING_COSMETIC_CURVE_OPERATIONS
    )
    assert tuple(by_operation) == DRAWING_COSMETIC_CURVE_OPERATIONS
    assert by_operation["create_one_point_circle"]["required"] == [
        "operation",
        "page",
        "view",
        "center_vertex",
        "radius_mm",
    ]
    assert by_operation["create_two_point_circle"]["required"][-2:] == [
        "center_vertex",
        "radius_vertex",
    ]
    assert by_operation["create_three_point_circle"]["required"][-3:] == [
        "first_perimeter_vertex",
        "second_perimeter_vertex",
        "third_perimeter_vertex",
    ]
    assert by_operation["create_center_start_end_arc"]["required"][-3:] == [
        "center_vertex",
        "start_vertex",
        "end_vertex",
    ]
    assert all(
        branch["additionalProperties"] is False
        for branch in by_operation.values()
    )
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 24 * 1024


def test_cosmetic_curve_state_validates_all_four_host_constructions() -> None:
    one = _plan(
        "one_point_circle",
        [_point(2.0, 3.0)],
        _geometry(_point(2.0, 3.0), 4.5),
    )
    two = _plan(
        "two_point_circle",
        [_point(2.0, 3.0), _point(5.0, 7.0)],
        _geometry(_point(2.0, 3.0), 5.0),
    )
    three = _plan(
        "three_point_circle",
        [_point(5.0, 0.0), _point(0.0, 5.0), _point(-5.0, 0.0)],
        _geometry(_point(0.0, 0.0), 5.0),
    )
    arc = _plan(
        "center_start_end_arc",
        [_point(0.0, 0.0), _point(5.0, 0.0), _point(0.0, 8.0)],
        _geometry(_point(0.0, 0.0), 5.0, arc=True, start=0.0, end=90.0),
    )

    assert (
        normalize_cosmetic_curve_host_plan(one, created=False)["geometry"]["radius_mm"]
        == 4.5
    )
    assert (
        normalize_cosmetic_curve_host_plan(two, created=False)["geometry"]["radius_mm"]
        == 5.0
    )
    assert normalize_cosmetic_curve_host_plan(three, created=False)["geometry"][
        "center_in_view_mm"
    ] == _point(0.0, 0.0)
    assert (
        normalize_cosmetic_curve_host_plan(arc, created=False)["geometry"][
            "end_angle_degrees"
        ]
        == 90.0
    )

    malformed = dict(two)
    malformed["geometry"] = _geometry(_point(2.0, 3.0), 6.0)
    with pytest.raises(
        NativeDrawingCosmeticCurveStateError,
        match="two-point circle radius",
    ):
        normalize_cosmetic_curve_host_plan(malformed, created=False)


def test_human_and_native_curve_paths_share_one_compiled_builder() -> None:
    command = (MOD_ROOT / "TechDraw" / "Gui" / "CommandExtensionPack.cpp").read_text(
        encoding="utf-8"
    )
    task = (MOD_ROOT / "TechDraw" / "Gui" / "TaskCosmeticCircle.cpp").read_text(
        encoding="utf-8"
    )
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    builder = (MOD_ROOT / "TechDraw" / "Gui" / "CosmeticCurveBuilder.cpp").read_text(
        encoding="utf-8"
    )
    builder_header = (
        MOD_ROOT / "TechDraw" / "Gui" / "CosmeticCurveBuilder.h"
    ).read_text(encoding="utf-8")

    assert command.count("createDrawingCosmeticCurve(") >= 3
    assert "createDrawingCosmeticCircleAtCenter" in task
    assert "validateDrawingCosmeticCurve" in binding
    assert "createDrawingCosmeticCurve" in binding
    assert "drawingPersistentCosmeticCurve" in binding
    assert "drawingCosmeticCurves" in binding
    assert "Part::Geom2dCircle::getCircleCenter" in builder
    assert "LineFormat::getCurrentLineFormat" in builder
    assert "sourcePointsInViewMm" in builder_header
