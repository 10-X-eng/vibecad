# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing Leader Lines."""

from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingErrors import NativeDrawingError
from VibeCADNativeDrawingLeader import _normalize_host_plan
from VibeCADNativeDrawingLeaderSchema import (
    DRAWING_ANNOTATION_CAPABILITY_NAME,
    DRAWING_LEADER_OPERATIONS,
    drawing_leader_capability_definition,
)
from VibeCADNativeDrawingLeaderState import MAX_DRAWING_LEADER_POINTS
from VibeCADNativeRegistry import build_native_capability_registry


MOD_ROOT = Path(__file__).resolve().parents[2]
_HOST_ERROR_CODE = "NATIVE_DRAWING_LEADER_RUNTIME_UNAVAILABLE"


def _host_plan() -> dict:
    return {
        "page_name": "Page",
        "owner_name": "FrontView",
        "object_name": "LeaderLine",
        "label": "Inspection Leader",
        "requested_points_on_page_mm": [
            {"x_mm": 72.0, "y_mm": 64.0},
            {"x_mm": 94.0, "y_mm": 80.0},
            {"x_mm": 116.0, "y_mm": 80.0},
        ],
        "owner_transform": {
            "position_on_page_mm": {"x_mm": 100.0, "y_mm": 75.0},
            "scale": 1.5,
            "rotation_degrees": 18.0,
        },
        "stored": {
            "anchor_in_owner_mm": {"x_mm": -19.998, "y_mm": -1.45},
            "waypoints_in_owner_mm": [
                {"x_mm": 0.0, "y_mm": 0.0},
                {"x_mm": 17.25, "y_mm": -5.6},
                {"x_mm": 31.0, "y_mm": -1.1},
            ],
        },
        "rendered_points_on_page_mm": [
            {"x_mm": 72.0, "y_mm": 64.0},
            {"x_mm": 94.0, "y_mm": 80.0},
            {"x_mm": 116.0, "y_mm": 80.0},
        ],
        "symbols": {"start": "filled_arrow", "end": "none"},
        "behavior": {
            "scalable": False,
            "auto_horizontal": True,
            "rotates_with_owner": True,
        },
        "line": {
            "line_width_mm": 0.35,
            "line_style": "continuous",
            "color_rgb": {"red": 0.1, "green": 0.2, "blue": 0.3},
        },
    }


def test_leader_schema_has_two_sharp_closed_branches() -> None:
    definition = drawing_leader_capability_definition()
    schema = definition.provider_schema(DRAWING_LEADER_OPERATIONS)
    branches = schema["parameters"]["oneOf"]
    by_operation = {
        branch["properties"]["operation"]["const"]: branch
        for branch in branches
    }

    assert definition.name == DRAWING_ANNOTATION_CAPABILITY_NAME
    assert tuple(by_operation) == DRAWING_LEADER_OPERATIONS
    assert by_operation["read_leader_defaults"]["required"] == ["operation"]
    assert by_operation["read_leader_defaults"]["additionalProperties"] is False

    create = by_operation["leader_line"]
    assert create["required"] == [
        "operation",
        "page",
        "owner",
        "points_on_page_mm",
        "label",
        "symbols",
        "behavior",
        "line",
    ]
    assert create["additionalProperties"] is False
    points = create["properties"]["points_on_page_mm"]
    assert (points["minItems"], points["maxItems"]) == (
        2,
        MAX_DRAWING_LEADER_POINTS,
    )
    for field in ("page", "owner", "symbols", "behavior", "line"):
        assert create["properties"][field]["additionalProperties"] is False
    assert create["properties"]["line"]["properties"]["color_rgb"][
        "additionalProperties"
    ] is False

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "file_path" not in encoded.casefold()
    assert "data_url" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 10 * 1024


def test_leader_host_plan_preserves_complete_typed_state() -> None:
    raw = _host_plan()
    normalized = _normalize_host_plan(raw)
    assert normalized["requested_points_on_page_mm"] == tuple(
        raw["requested_points_on_page_mm"]
    )
    assert normalized["stored"]["waypoints_in_owner_mm"] == tuple(
        raw["stored"]["waypoints_in_owner_mm"]
    )
    assert normalized["rendered_points_on_page_mm"] == tuple(
        raw["rendered_points_on_page_mm"]
    )
    for field in (
        "page_name",
        "owner_name",
        "object_name",
        "label",
        "owner_transform",
        "symbols",
        "behavior",
        "line",
    ):
        assert normalized[field] == raw[field]


@pytest.mark.parametrize(
    ("path", "value"),
    (
        (("rendered_points_on_page_mm",), None),
        (("behavior", "scalable"), 1),
        (("symbols", "start"), "arrowish"),
        (("owner_transform", "scale"), 0.0),
        (("line", "color_rgb", "blue"), 2.0),
    ),
)
def test_leader_host_plan_rejects_malformed_nested_state(
    path: tuple[str, ...],
    value: object,
) -> None:
    raw = deepcopy(_host_plan())
    target = raw
    for name in path[:-1]:
        target = target[name]
    target[path[-1]] = value

    with pytest.raises(NativeDrawingError) as caught:
        _normalize_host_plan(raw)
    assert caught.value.error_code == _HOST_ERROR_CODE


def test_leader_registry_has_one_definition_and_implementation() -> None:
    registry = build_native_capability_registry()

    definition = registry.definition(DRAWING_ANNOTATION_CAPABILITY_NAME)
    implementation = registry.implementation(DRAWING_ANNOTATION_CAPABILITY_NAME)
    assert definition is not None
    assert implementation is not None
    assert tuple(item.operation for item in definition.variants) == (
        DRAWING_LEADER_OPERATIONS
    )


def test_human_and_native_paths_share_one_compiled_builder() -> None:
    task = (MOD_ROOT / "TechDraw" / "Gui" / "TaskLeaderLine.cpp").read_text(
        encoding="utf-8"
    )
    builder = (MOD_ROOT / "TechDraw" / "Gui" / "LeaderLineBuilder.cpp").read_text(
        encoding="utf-8"
    )
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    scene = (MOD_ROOT / "TechDraw" / "Gui" / "QGIView.cpp").read_text(
        encoding="utf-8"
    )

    create_task = task[task.index("void TaskLeaderLine::createLeaderFeature") :]
    assert create_task.count("createDrawingLeaderLine(") == 1
    assert "drawingLeaderDefaults()" in create_task
    assert builder.count('QStringLiteral("TechDraw::DrawLeaderLine")') == 1
    assert "findAllParentPages()" in builder
    assert "DrawView::isProjGroupItem" in builder
    assert "publishProvisionalTimelineOperationBlock" in builder
    assert "leader->requestPaint();" in builder
    for function in (
        "drawingLeaderDefaults",
        "validateDrawingLeaderLine",
        "createDrawingLeaderLine",
    ):
        assert function in builder
        assert function in binding
    assert "UserType::QGILeaderLine" in scene
