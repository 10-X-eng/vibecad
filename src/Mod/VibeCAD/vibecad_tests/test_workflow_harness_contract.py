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
    assert "pyautogui" not in channel
    assert "SetCursorPos" not in channel
    assert "SendInput" not in channel


def test_workflow_harness_does_not_turn_the_tour_into_a_test() -> None:
    tour = TOUR_SCRIPT.read_text(encoding="utf-8")
    harness = HARNESS.read_text(encoding="utf-8")
    assert "tour_remains_demo" in harness
    assert "workflow-harness" not in tour.lower()
    assert "TYPESAFE_API_KEY" not in tour
