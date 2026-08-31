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
    DRAWING_LEADER_CAPABILITY_NAME,
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


def test_leader_line_is_one_focused_tool_with_optional_human_style() -> None:
    definition = drawing_leader_capability_definition()
    schema = definition.provider_schema(("create",))
    create = schema["parameters"]["oneOf"][0]

    assert definition.name == DRAWING_LEADER_CAPABILITY_NAME == "drawing.leader_line"
    assert create["properties"]["operation"]["const"] == "create"
    assert create["required"] == [
        "page",
        "owner",
        "points_on_page_mm",
        "label",
    ]
    assert create["additionalProperties"] is False
    points = create["properties"]["points_on_page_mm"]
    assert (points["minItems"], points["maxItems"]) == (
        2,
        MAX_DRAWING_LEADER_POINTS,
    )
    for field in ("page", "owner", "symbols", "behavior", "line"):
        assert create["properties"][field]["additionalProperties"] is False
    assert create["properties"]["symbols"]["required"] == []
    assert create["properties"]["behavior"]["required"] == []
    assert create["properties"]["line"]["required"] == []
    assert create["properties"]["line"]["properties"]["color_rgb"][
        "additionalProperties"
    ] is False

    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "unknown" not in encoded.casefold()
    assert "file_path" not in encoded.casefold()
    assert "data_url" not in encoded.casefold()
    assert "read_leader_defaults" not in encoded
    assert '"kind"' not in encoded
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
    ):
        assert normalized[field] == raw[field]
    assert normalized["line"]["line_width_mm"] == raw["line"]["line_width_mm"]
    assert normalized["line"]["line_style"] == raw["line"]["line_style"]
    assert normalized["line"]["color_rgb"] == {
        channel: round(int(float(value) * 255.0 + 0.5) / 255.0, 12)
        for channel, value in raw["line"]["color_rgb"].items()
    }


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

    assert registry.definition("drawing.annotation") is None
    definition = registry.definition(DRAWING_LEADER_CAPABILITY_NAME)
    implementation = registry.implementation(DRAWING_LEADER_CAPABILITY_NAME)
    assert definition is not None
    assert implementation is not None
    assert tuple(item.operation for item in definition.variants) == ("create",)


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

    implementation = (
        MOD_ROOT / "VibeCAD" / "VibeCADNativeDrawingLeader.py"
    ).read_text(encoding="utf-8")
    verify_return = implementation[implementation.index("def verify_drawing_leader") :]
    assert '"page": {' in verify_return
