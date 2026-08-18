# SPDX-License-Identifier: LGPL-2.1-or-later

"""Pure human-selector policy for Native/VibeScript authority changes."""

from __future__ import annotations

from dataclasses import dataclass

from VibeCADAuthoringMode import AUTHORING_MODES, normalize_authoring_mode


@dataclass(frozen=True, slots=True)
class AuthoringModeEnvironment:
    current_mode: str
    document_available: bool
    internal_agent_enabled: bool
    run_active: bool
    transaction_open: bool
    task_or_edit_active: bool
    recompute_active: bool
    unresolved_editor_work: bool
    native_available: bool
    native_unavailable_reason: str
    vibescript_return_safe: bool


@dataclass(frozen=True, slots=True)
class AuthoringModeSelectorState:
    current_mode: str
    selector_enabled: bool
    selector_reason: str
    native_enabled: bool
    native_reason: str
    vibescript_enabled: bool
    vibescript_reason: str

    def target_enabled(self, mode: str) -> bool:
        target = normalize_authoring_mode(mode)
        return self.native_enabled if target == "native" else self.vibescript_enabled

    def target_reason(self, mode: str) -> str:
        target = normalize_authoring_mode(mode)
        return self.native_reason if target == "native" else self.vibescript_reason


def _common_blocker(environment: AuthoringModeEnvironment) -> str:
    if not environment.document_available:
        return "Create or open a document before selecting authoring authority."
    if not environment.internal_agent_enabled:
        return "Authoring authority cannot change while external MCP control is active."
    if environment.run_active:
        return "Wait for the active assistant run to finish."
    if environment.transaction_open:
        return "Finish or cancel the open document transaction first."
    if environment.task_or_edit_active:
        return "Finish or cancel the active task or contextual edit first."
    if environment.recompute_active:
        return "Wait for the document recompute to finish."
    if environment.unresolved_editor_work:
        return "Resolve or discard the pending VibeScript editor work first."
    return ""


def resolve_authoring_mode_selector(
    environment: AuthoringModeEnvironment,
) -> AuthoringModeSelectorState:
    if not isinstance(environment, AuthoringModeEnvironment):
        raise TypeError("environment must be an AuthoringModeEnvironment")
    current = normalize_authoring_mode(environment.current_mode)
    blocker = _common_blocker(environment)
    native_blocker = ""
    if not environment.native_available:
        native_blocker = str(environment.native_unavailable_reason or "").strip() or (
            "Native mode is not complete for the active ribbon."
        )
    native_reason = native_blocker
    vibescript_reason = ""
    if current == "native" and not environment.vibescript_return_safe:
        vibescript_reason = (
            "Native changes are not represented by VibeScript source. Discard the "
            "Native epoch or create a new VibeScript source first."
        )
    native_enabled = not blocker and (current == "native" or not native_blocker)
    vibescript_enabled = not blocker and (
        current == "vibescript" or not vibescript_reason
    )
    alternate_enabled = (
        native_enabled if current == "vibescript" else vibescript_enabled
    )
    selector_reason = blocker or (
        native_reason if current == "vibescript" else vibescript_reason
    )
    return AuthoringModeSelectorState(
        current_mode=current,
        selector_enabled=bool(not blocker and alternate_enabled),
        selector_reason=selector_reason,
        native_enabled=native_enabled,
        native_reason=native_reason,
        vibescript_enabled=vibescript_enabled,
        vibescript_reason=vibescript_reason,
    )


def requires_take_manual_control_confirmation(
    current_mode: str,
    requested_mode: str,
) -> bool:
    current = normalize_authoring_mode(current_mode)
    requested = normalize_authoring_mode(requested_mode)
    return current == "vibescript" and requested == "native"


def validate_human_mode_request(
    state: AuthoringModeSelectorState,
    requested_mode: str,
) -> str:
    if not isinstance(state, AuthoringModeSelectorState):
        raise TypeError("state must be an AuthoringModeSelectorState")
    requested = normalize_authoring_mode(requested_mode)
    if requested not in AUTHORING_MODES:
        raise AssertionError("unreachable authoring mode")
    if requested == state.current_mode:
        return requested
    if not state.selector_enabled or not state.target_enabled(requested):
        raise RuntimeError(
            state.target_reason(requested)
            or state.selector_reason
            or "Authoring authority cannot change right now."
        )
    return requested
