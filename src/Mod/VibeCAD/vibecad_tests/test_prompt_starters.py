# SPDX-License-Identifier: LGPL-2.1-or-later

"""Behavioral coverage for the global prompt-starter library."""

from __future__ import annotations

import json

import pytest

from VibeCADPromptStarters import (
    BUILTIN_PROMPT_STARTERS,
    PROMPT_STARTERS_SCHEMA,
    create_custom_prompt_starter,
    load_custom_prompt_starters,
    save_custom_prompt_starters,
)


def test_builtin_starters_capture_design_inputs_without_hidden_execution() -> None:
    assert len(BUILTIN_PROMPT_STARTERS) == 9
    assert {starter.category for starter in BUILTIN_PROMPT_STARTERS} == {
        "New Part",
        "Modify",
        "3D Print",
        "CNC",
        "Assembly",
        "Enclosure",
        "Sheet Metal",
        "Review",
    }
    assert all("[" in starter.content for starter in BUILTIN_PROMPT_STARTERS)


def test_visual_review_starter_requires_multi_view_inspection() -> None:
    starter = next(
        item
        for item in BUILTIN_PROMPT_STARTERS
        if item.starter_id == "builtin:visual-review"
    )
    assert starter.name == "Check how this looks from all sides"
    assert starter.category == "Review"
    assert "isometric" in starter.content
    assert "front" in starter.content
    assert "top" in starter.content
    assert "presentation_only" in starter.content
    assert "needs_measurement" in starter.content
    assert "Do not modify" in starter.content


def test_custom_starter_round_trip_preserves_identity_and_content(tmp_path) -> None:
    path = tmp_path / "prompt-starters.json"
    starter = create_custom_prompt_starter(
        name="Shop fixture",
        category="CNC",
        content="Design a fixture for: [part and operation]",
    )

    save_custom_prompt_starters([starter], path)

    assert load_custom_prompt_starters(path) == (starter,)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["schema"] == PROMPT_STARTERS_SCHEMA
    assert payload["starters"] == [starter.custom_record()]


def test_invalid_custom_library_fails_without_being_rewritten(tmp_path) -> None:
    path = tmp_path / "prompt-starters.json"
    invalid = '{"schema":"wrong","version":1,"starters":[]}'
    path.write_text(invalid, encoding="utf-8")

    with pytest.raises(RuntimeError, match="unsupported schema"):
        load_custom_prompt_starters(path)

    assert path.read_text(encoding="utf-8") == invalid


def test_duplicate_custom_names_are_rejected(tmp_path) -> None:
    path = tmp_path / "prompt-starters.json"
    first = create_custom_prompt_starter(
        name="Bracket",
        category="New Part",
        content="Create [bracket A]",
    )
    second = create_custom_prompt_starter(
        name="bracket",
        category="Modify",
        content="Modify [bracket B]",
    )

    with pytest.raises(ValueError, match="Duplicate custom prompt starter name"):
        save_custom_prompt_starters([first, second], path)

    assert not path.exists()
