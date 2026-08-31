# SPDX-License-Identifier: LGPL-2.1-or-later

"""Explicit policy for application-strip and assistant UI controls."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ApplicationAuthority = Literal["provider", "user_authorized", "human_only"]


@dataclass(frozen=True, slots=True)
class ApplicationActionPolicy:
    action_id: str
    control_id: str
    authority: ApplicationAuthority
    capability_name: str | None = None

    def __post_init__(self) -> None:
        if self.authority == "provider" and not self.capability_name:
            raise ValueError("Provider application actions require a capability name")
        if self.authority != "provider" and self.capability_name is not None:
            raise ValueError("Non-provider application actions cannot advertise tools")

    def summary(self) -> dict[str, str | None]:
        return {
            "action_id": self.action_id,
            "control_id": self.control_id,
            "authority": self.authority,
            "capability_name": self.capability_name,
        }


APPLICATION_ACTIONS = (
    ApplicationActionPolicy("Std_New", "VibeCADRibbonNew", "user_authorized"),
    ApplicationActionPolicy("Std_Open", "VibeCADRibbonOpen", "user_authorized"),
    ApplicationActionPolicy(
        "Std_Save", "VibeCADRibbonSave", "provider", "document.save"
    ),
    ApplicationActionPolicy(
        "Std_Undo", "VibeCADRibbonUndo", "provider", "document.undo"
    ),
    ApplicationActionPolicy("Std_Redo", "VibeCADRibbonRedo", "human_only"),
    ApplicationActionPolicy(
        "document_tab.activate", "VibeCADDocumentTabs", "human_only"
    ),
    ApplicationActionPolicy(
        "document_tab.close", "VibeCADDocumentTabs", "human_only"
    ),
    ApplicationActionPolicy(
        "document_tab.reorder", "VibeCADDocumentTabs", "human_only"
    ),
    ApplicationActionPolicy("application.menu", "VibeCADAppButton", "human_only"),
    ApplicationActionPolicy(
        "application.command_search", "VibeCADRibbonSearch", "human_only"
    ),
    ApplicationActionPolicy(
        "application.theme", "VibeCADThemeToggle", "human_only"
    ),
    ApplicationActionPolicy(
        "application.assistant", "VibeCADRibbonAssistant", "human_only"
    ),
    ApplicationActionPolicy(
        "application.preferences", "VibeCADRibbonSettings", "human_only"
    ),
    ApplicationActionPolicy(
        "application.check_for_updates",
        "VibeCADRibbonCheckForUpdates",
        "human_only",
    ),
)

APPLICATION_ACTIONS_BY_ID = {value.action_id: value for value in APPLICATION_ACTIONS}
if len(APPLICATION_ACTIONS_BY_ID) != len(APPLICATION_ACTIONS):
    raise RuntimeError("Application action policy contains duplicate IDs")

APPLICATION_STRIP_WIDGET_IDS = frozenset(
    {
        "VibeCADApplicationStrip",
        "VibeCADLeadingTools",
        "VibeCADAppButton",
        "VibeCADRibbonSeparator",
        "VibeCADDocumentTabs",
        "VibeCADRibbonSearch",
        "VibeCADCommandSearchMenu",
        "VibeCADCommandSearch",
        "VibeCADTrailingTools",
        "VibeCADThemeToggle",
    }
)

ASSISTANT_CHROME_IDS = frozenset(
    {
        "VibePanelRoot",
        "VibeSessionRecoveryBanner",
        "VibeSessionRecoveryText",
        "VibeSessionRecoveryRestore",
        "VibeSessionRecoveryDiscard",
        "VibeSessionRecoveryDraftTimer",
        "VibeContentSplitter",
        "VibeConversationPanel",
        "VibeConversationHeader",
        "VibeConversationSelector",
        "VibeAuthoringMode",
        "VibeNewConversation",
        "VibeConversation",
        "VibeThinking",
        "VibeLowerPanel",
        "VibeQuestionPanel",
        "VibeQuestionList",
        "VibeViewStatus",
        "VibeStatusLine",
        "VibeComposer",
        "VibeReferenceChips",
        "VibePrompt",
        "VibeComposerButtons",
        "VibeAttachView",
        "VibeAttachImage",
        "VibePromptStarters",
        "VibePromptStarterMenu",
        "VibeSend",
        "VibeStop",
    }
)

DEBUGGER_CONTROL_IDS = frozenset(
    {
        "VibeContextDebugRoot",
        "VibeContextDebugControls",
        "VibeContextDebugCapture",
        "VibeContextDebugRefresh",
        "VibeContextDebugCopy",
        "VibeContextDebugOpenFile",
        "VibeContextDebugOpenFolder",
        "VibeContextDebugJson",
        "VibeContextDebugStatus",
        "VibeContextDebugPollTimer",
    }
)


def provider_application_capabilities() -> tuple[str, ...]:
    return tuple(
        value.capability_name
        for value in APPLICATION_ACTIONS
        if value.authority == "provider" and value.capability_name is not None
    )


def human_only_ui_control(control_id: str) -> bool:
    clean = str(control_id or "").strip()
    if clean in ASSISTANT_CHROME_IDS or clean in DEBUGGER_CONTROL_IDS:
        return True
    matches = [value for value in APPLICATION_ACTIONS if value.control_id == clean]
    return bool(matches and all(value.authority == "human_only" for value in matches))
