# SPDX-License-Identifier: LGPL-2.1-or-later

"""Focused contracts for exact Drawing frame presentation state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from VibeCADNativeDrawingPresentationSchema import (
    DRAWING_PRESENTATION_CAPABILITY_NAME,
    DRAWING_PRESENTATION_OPERATIONS,
    drawing_presentation_capability_definition,
)
from VibeCADNativeDrawingPresentationState import (
    NativeDrawingPresentationStateError,
    normalize_drawing_frame_visibility_plan,
    normalize_drawing_grid_visibility_plan,
    normalize_drawing_hidden_edge_visibility_plan,
)


MOD_ROOT = Path(__file__).resolve().parents[2]


def _plan(previous: bool, visible: bool, count: int = 3) -> dict:
    return {
        "page_name": "Page",
        "previous_visible": previous,
        "visible": visible,
        "changed": previous is not visible,
        "graphical_view_count": count,
    }


def test_drawing_presentation_schema_is_closed_explicit_and_transient() -> None:
    definition = drawing_presentation_capability_definition()
    schema = definition.provider_schema(DRAWING_PRESENTATION_OPERATIONS)
    branches = schema["parameters"]["oneOf"]

    assert definition.name == DRAWING_PRESENTATION_CAPABILITY_NAME
    assert DRAWING_PRESENTATION_OPERATIONS == (
        "set_frame_visibility",
        "set_grid_visibility",
        "set_hidden_edges_visible",
    )
    assert [branch["properties"]["operation"]["const"] for branch in branches] == list(
        DRAWING_PRESENTATION_OPERATIONS
    )
    assert all(branch["additionalProperties"] is False for branch in branches)
    assert all(branch["properties"]["visible"]["type"] == "boolean" for branch in branches)
    assert set(branches[0]["properties"]["page"]["properties"]) == {
        "object_name",
        "expected_state_sha256",
        "expected_frame_visibility_state_sha256",
    }
    assert set(branches[1]["properties"]["page"]["properties"]) == {
        "object_name",
        "expected_state_sha256",
        "expected_grid_visibility_state_sha256",
    }
    assert set(branches[2]["properties"]["view"]["properties"]) == {
        "object_name",
        "expected_state_sha256",
        "expected_hidden_edge_visibility_state_sha256",
    }
    encoded = json.dumps(schema, sort_keys=True, separators=(",", ":"))
    assert "toggle_frame" not in encoded.casefold()
    assert "unknown" not in encoded.casefold()
    assert "selection" not in encoded.casefold()
    assert len(encoded.encode("utf-8")) < 5 * 1024


def test_drawing_frame_visibility_state_validates_change_and_no_change() -> None:
    changed = normalize_drawing_frame_visibility_plan(_plan(False, True))
    unchanged = normalize_drawing_frame_visibility_plan(_plan(True, True))

    assert changed["changed"] is True
    assert changed["visible"] is True
    assert unchanged["changed"] is False
    assert unchanged["previous_visible"] is True


def test_drawing_grid_and_hidden_edge_state_validate_exact_host_plans() -> None:
    grid = normalize_drawing_grid_visibility_plan(
        {
            "page_name": "Page",
            "previous_visible": False,
            "visible": True,
            "changed": True,
        }
    )
    hidden = normalize_drawing_hidden_edge_visibility_plan(
        {
            "page_name": "Page",
            "view_name": "View",
            "previous_visible": True,
            "visible": False,
            "changed": True,
        }
    )

    assert grid["visible"] is True
    assert hidden["page_name"] == "Page"
    assert hidden["view_name"] == "View"
    assert hidden["visible"] is False


@pytest.mark.parametrize(
    "mutation",
    (
        {"changed": False},
        {"visible": 1},
        {"graphical_view_count": -1},
        {"page_name": "bad page"},
    ),
)
def test_drawing_frame_visibility_state_rejects_inconsistent_host_data(
    mutation: dict,
) -> None:
    raw = _plan(False, True)
    raw.update(mutation)
    with pytest.raises(NativeDrawingPresentationStateError):
        normalize_drawing_frame_visibility_plan(raw)


def test_human_and_native_presentation_paths_share_one_compiled_builder() -> None:
    command = (MOD_ROOT / "TechDraw" / "Gui" / "CommandDecorate.cpp").read_text(
        encoding="utf-8"
    )
    annotate = (MOD_ROOT / "TechDraw" / "Gui" / "CommandAnnotate.cpp").read_text(
        encoding="utf-8"
    )
    page_view = (MOD_ROOT / "TechDraw" / "Gui" / "MDIViewPage.cpp").read_text(
        encoding="utf-8"
    )
    binding = (MOD_ROOT / "TechDraw" / "Gui" / "AppTechDrawGuiPy.cpp").read_text(
        encoding="utf-8"
    )
    builder = (MOD_ROOT / "TechDraw" / "Gui" / "FrameVisibilityBuilder.cpp").read_text(
        encoding="utf-8"
    )

    command_section = command[
        command.index("void CmdTechDrawToggleFrame::activated") : command.index(
            "// TechDraw_ToggleGrid"
        )
    ]
    assert command_section.count("changeDrawingFrameVisibility(") == 1
    assert "toggleFrameState();" not in command_section
    assert "changeDrawingFrameVisibility(m_vpPage" in page_view
    assert "validateDrawingFrameVisibility" in binding
    assert "changeDrawingFrameVisibility" in binding
    assert "drawingFrameVisibilityAvailable" in binding
    assert "inspectDrawingFrameVisibility" in binding
    assert "ViewFrameMode::Manual" in builder
    assert "activeWindow() != pageProvider->getMDIViewPage()" in builder
    assert "pageProvider->toggleFrameState();" in builder
    assert "changeDrawingGridVisibility(m_vpPage" in page_view
    assert "changeDrawingGridVisibility" in binding
    assert "changeDrawingHiddenEdgeVisibility" in binding
    assert "changeDrawingGridVisibility(vpp" in command
    assert "changeDrawingHiddenEdgeVisibility(baseFeat" in annotate
    assert "pageProvider->ShowGrid.setValue" in builder
    assert "provider->ShowAllEdges.setValue" in builder
