# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
import re

from VibeCADNativeApplicationManifest import (
    APPLICATION_ACTIONS,
    APPLICATION_ACTIONS_BY_ID,
    APPLICATION_STRIP_WIDGET_IDS,
    ASSISTANT_CHROME_IDS,
    DEBUGGER_CONTROL_IDS,
    human_only_ui_control,
    provider_application_capabilities,
)


ROOT = Path(__file__).resolve().parents[4]


def _source(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_new_and_open_require_human_authorization_and_are_not_provider_tools() -> None:
    assert APPLICATION_ACTIONS_BY_ID["Std_New"].authority == "user_authorized"
    assert APPLICATION_ACTIONS_BY_ID["Std_Open"].authority == "user_authorized"
    assert APPLICATION_ACTIONS_BY_ID["Std_New"].capability_name is None
    assert APPLICATION_ACTIONS_BY_ID["Std_Open"].capability_name is None


def test_only_guarded_save_and_run_local_undo_are_provider_capabilities() -> None:
    assert provider_application_capabilities() == (
        "document.save",
        "document.undo",
    )
    assert APPLICATION_ACTIONS_BY_ID["Std_Redo"].authority == "human_only"
    assert all(
        APPLICATION_ACTIONS_BY_ID[action].authority == "human_only"
        for action in (
            "document_tab.activate",
            "document_tab.close",
            "document_tab.reorder",
        )
    )


def test_every_application_strip_command_and_widget_is_explicitly_inventoried() -> None:
    source = _between(
        _source("src/Gui/VibeCADRibbon.cpp"),
        "void buildApplicationStrip(QVBoxLayout* rootLayout)",
        "void scheduleDocumentTabsSync()",
    )
    pairs = set(
        re.findall(
            r"addCommandButton\([^;]*?QStringLiteral\(\"([^\"]+)\"\)\s*,\s*"
            r"QStringLiteral\(\"([^\"]+)\"\)",
            source,
            re.DOTALL,
        )
    )
    assert pairs == {
        ("Std_Open", "VibeCADRibbonOpen"),
        ("Std_Save", "VibeCADRibbonSave"),
        ("Std_Undo", "VibeCADRibbonUndo"),
        ("Std_Redo", "VibeCADRibbonRedo"),
        ("Std_New", "VibeCADRibbonNew"),
        ("VibeCAD_OpenAssistant", "VibeCADRibbonAssistant"),
        ("VibeCAD_OpenPreferences", "VibeCADRibbonSettings"),
        ("VibeCAD_CheckForUpdates", "VibeCADRibbonCheckForUpdates"),
    }
    widget_ids = set(re.findall(r"setObjectName\(QStringLiteral\(\"([^\"]+)\"\)\)", source))
    assert widget_ids == APPLICATION_STRIP_WIDGET_IDS
    classified_controls = {value.control_id for value in APPLICATION_ACTIONS}
    assert {control for _command, control in pairs} <= classified_controls


def test_assistant_and_debugger_chrome_are_exactly_human_only() -> None:
    source = _source("src/Mod/VibeCAD/VibeCADGui.py")
    assistant = _between(source, "def _build_panel_widget():", "def _ensure_panel_content")
    debugger = _between(
        source,
        "def _build_context_debug_widget():",
        "def _sync_context_debug_polling",
    )
    assert set(re.findall(r'setObjectName\("([^\"]+)"\)', assistant)) == ASSISTANT_CHROME_IDS
    assert set(re.findall(r'setObjectName\("([^\"]+)"\)', debugger)) == DEBUGGER_CONTROL_IDS
    assert all(human_only_ui_control(value) for value in ASSISTANT_CHROME_IDS)
    assert all(human_only_ui_control(value) for value in DEBUGGER_CONTROL_IDS)


def test_application_policy_has_no_duplicate_or_unbounded_result_shape() -> None:
    assert len(APPLICATION_ACTIONS_BY_ID) == len(APPLICATION_ACTIONS)
    assert all(
        set(value.summary())
        == {"action_id", "control_id", "authority", "capability_name"}
        for value in APPLICATION_ACTIONS
    )
