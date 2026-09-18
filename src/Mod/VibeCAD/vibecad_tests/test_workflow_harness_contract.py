# SPDX-License-Identifier: LGPL-2.1-or-later

"""Contracts for the workflow harness that reuses the visible-tour click path."""

from __future__ import annotations

from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
TOUR_SCRIPT = REPOSITORY_ROOT / "Invoke-VibeCAD-VisibleTour.ps1"
HARNESS = REPOSITORY_ROOT / "tools" / "vibecad_workflow_harness.py"
CHANNEL = REPOSITORY_ROOT / "tools" / "vibecad_workflow_channel.py"
WORKFLOWS = REPOSITORY_ROOT / "tools" / "vibecad_workflows.json"


def test_workflow_harness_reuses_the_tour_click_route() -> None:
    tour = TOUR_SCRIPT.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")
    channel = CHANNEL.read_text(encoding="utf-8")
    workflows = WORKFLOWS.read_text(encoding="utf-8")

    assert "/v1/ui/click" in tour
    assert "Invoke-VibeCADAgentPost" in tour
    assert "/v1/ui/click" in harness
    assert "/v1/ui/click" in channel
    assert "new_document" in workflows
    assert "sketch_then_pad" in workflows
    assert "\"export\"" in workflows
    assert '"kind": "menu"' not in workflows
    assert "PartDesign_NewBody" in workflows
    assert "Sketcher_NewSketch" in workflows
    assert "leave_active_sketch" in workflows
    assert "leaveActiveSketch" in channel
    assert "PartDesign_DesignExtrude" in workflows
    assert "place_closed_circle" in workflows
    assert "export_step" in workflows
    assert workflows.count("Std_New") == 1
    assert "InternalFace1" in channel
    assert "Document Recovery" in channel
    assert "Start Recovery" in channel
    assert "Cancel" in channel
    assert "PartDesign_Pad" not in workflows
    assert "Part.Circle" in channel
    assert "Import.export" in channel
    assert "exportStep" in channel
    assert "addGeometry" in channel
    assert "/v1/run" in channel
    agent_control = (
        REPOSITORY_ROOT / "src" / "Mod" / "VibeCAD" / "VibeCADAgentControl.py"
    ).read_text(encoding="utf-8")
    assert "_pick_clickable_qt_action" in agent_control
    assert "_command_is_active" in agent_control
    assert "_named_command_runner" in agent_control
    assert "command_active" in agent_control
    assert '"kind": "dialog"' in workflows
    assert '"text": "OK"' in workflows
    assert "Std_New" in workflows
    assert "Std_Export" not in workflows
    assert "pyautogui" not in channel
    assert "SetCursorPos" not in channel
    assert "SendInput" not in channel
    assert "accept_design_task" in workflows
    assert "accept_design_task" in channel
    assert "QDialogButtonBox" in channel
    assert "DesignBodyPublication" in channel
    assert "DesignBodyPublication" in workflows


def test_workflow_harness_does_not_turn_the_tour_into_a_test() -> None:
    tour = TOUR_SCRIPT.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")
    assert "tour_remains_demo" in harness
    assert "workflow-harness" not in tour.lower()
    assert "TYPESAFE_API_KEY" not in tour
