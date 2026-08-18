# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from dataclasses import replace

import pytest

from VibeCADAuthoringModePolicy import (
    AuthoringModeEnvironment,
    requires_take_manual_control_confirmation,
    resolve_authoring_mode_selector,
    validate_human_mode_request,
)


def _environment(**changes) -> AuthoringModeEnvironment:
    baseline = AuthoringModeEnvironment(
        current_mode="vibescript",
        document_available=True,
        internal_agent_enabled=True,
        run_active=False,
        transaction_open=False,
        task_or_edit_active=False,
        recompute_active=False,
        unresolved_editor_work=False,
        native_available=True,
        native_unavailable_reason="",
        vibescript_return_safe=True,
    )
    return replace(baseline, **changes)


def test_ready_selector_offers_exactly_native_and_vibescript_transition() -> None:
    state = resolve_authoring_mode_selector(_environment())

    assert state.current_mode == "vibescript"
    assert state.selector_enabled is True
    assert state.native_enabled is True
    assert state.vibescript_enabled is True
    assert validate_human_mode_request(state, "native") == "native"


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("document_available", "Create or open"),
        ("internal_agent_enabled", "external MCP"),
        ("run_active", "assistant run"),
        ("transaction_open", "transaction"),
        ("task_or_edit_active", "task or contextual edit"),
        ("recompute_active", "recompute"),
        ("unresolved_editor_work", "editor work"),
    ),
)
def test_each_host_blocker_disables_the_whole_selector(
    field: str,
    message: str,
) -> None:
    disabled_value = (
        False
        if field in {"document_available", "internal_agent_enabled"}
        else True
    )
    state = resolve_authoring_mode_selector(
        _environment(**{field: disabled_value})
    )

    assert state.selector_enabled is False
    assert message in state.selector_reason
    with pytest.raises(RuntimeError, match=message):
        validate_human_mode_request(state, "native")


def test_incomplete_ribbon_disables_native_without_hiding_current_mode() -> None:
    state = resolve_authoring_mode_selector(
        _environment(
            native_available=False,
            native_unavailable_reason="Model Native tools are incomplete.",
        )
    )

    assert state.selector_enabled is False
    assert state.native_enabled is False
    assert state.vibescript_enabled is True
    assert state.native_reason == "Model Native tools are incomplete."


def test_unsaved_document_guides_save_without_misreporting_ribbon_state() -> None:
    state = resolve_authoring_mode_selector(
        _environment(
            document_saved=False,
            document_save_reason="Save this VibeCAD document to enable VibeCAD.",
            native_available=False,
            native_unavailable_reason="Native mode is not complete for the active ribbon.",
        )
    )

    assert state.selector_enabled is True
    assert state.native_enabled is True
    assert state.native_reason == "Save this VibeCAD document to enable VibeCAD."
    assert state.selector_reason == "Save this VibeCAD document to enable VibeCAD."
    assert validate_human_mode_request(state, "native") == "native"


def test_native_changes_block_silent_vibescript_return() -> None:
    state = resolve_authoring_mode_selector(
        _environment(
            current_mode="native",
            vibescript_return_safe=False,
        )
    )

    assert state.selector_enabled is False
    assert state.native_enabled is True
    assert state.vibescript_enabled is False
    with pytest.raises(RuntimeError, match="not represented"):
        validate_human_mode_request(state, "vibescript")


def test_manual_control_confirmation_is_one_way_and_explicit() -> None:
    assert requires_take_manual_control_confirmation("vibescript", "native") is True
    assert requires_take_manual_control_confirmation("native", "vibescript") is False
    assert requires_take_manual_control_confirmation("native", "native") is False
