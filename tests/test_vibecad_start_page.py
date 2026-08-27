# SPDX-License-Identifier: LGPL-2.1-or-later
"""Source contracts for VibeCAD's branded, backwards-compatible Start page."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
START_GUI = ROOT / "src" / "Mod" / "Start" / "Gui"
START_VIEW_CPP = START_GUI / "StartView.cpp"
FIRST_START_CPP = START_GUI / "FirstStartWidget.cpp"
NEW_FILE_BUTTON_CPP = START_GUI / "NewFileButton.cpp"
FILE_CARD_VIEW_CPP = START_GUI / "FileCardView.cpp"
GENERAL_SETTINGS_CPP = START_GUI / "GeneralSettingsWidget.cpp"
VIBECAD_GUI_PY = ROOT / "src" / "Mod" / "VibeCAD" / "VibeCADGui.py"


def test_vibecad_start_page_keeps_the_existing_freecad_capabilities() -> None:
    source = START_VIEW_CPP.read_text(encoding="utf-8")

    for capability in (
        "newEmptyFile",
        "newPartDesignFile",
        "openExistingFile",
        "newAssemblyFile",
        "newDraftFile",
        "configureRecentFilesListWidget",
        "configureExamplesListWidget",
        "configureCustomFolderListWidget",
        'GetBool("ShowExamples", true)',
        'GetASCII("CustomFolder", "")',
        'GetBool("ShowOnStartup", true)',
        'GetBool("FirstStart2024", true)',
    ):
        assert capability in source


def test_start_page_guides_users_into_the_real_vibecad_ai_flows() -> None:
    start_source = START_VIEW_CPP.read_text(encoding="utf-8")
    first_start_source = FIRST_START_CPP.read_text(encoding="utf-8")
    vibecad_gui_source = VIBECAD_GUI_PY.read_text(encoding="utf-8")

    assert 'setObjectName(QLatin1String("VibeCADStartHero"))' in start_source
    assert 'Gui.addCommand("VibeCAD_OpenPreferences", OpenPreferencesCommand())' in vibecad_gui_source
    assert 'Gui.addCommand("VibeCAD_OpenAssistant", OpenAssistantCommand())' in vibecad_gui_source
    assert "def open_preferences(page_name: str = \"VibeCAD\") -> None:" in vibecad_gui_source
    assert "def open_assistant() -> None:" in vibecad_gui_source
    assert 'VibeCADGui.open_preferences(\\"VibeCAD\\")' in start_source
    assert "VibeCADGui.open_assistant()" in start_source
    assert "GeneralSettingsWidget" in first_start_source
    assert "ThemeSelectorWidget" in first_start_source


def test_start_page_hit_targets_are_explicit_and_mouse_safe() -> None:
    start_source = START_VIEW_CPP.read_text(encoding="utf-8")
    button_source = NEW_FILE_BUTTON_CPP.read_text(encoding="utf-8")

    assert 'setProperty("vibecadUsesModelBrowser", false)' in start_source

    for object_name in (
        "VibeCADNewFile",
        "VibeCADOpenFile",
        "VibeCADParametricBody",
        "VibeCADAssembly",
        "VibeCADDraft",
        "RecentFilesList",
        "ExamplesList",
        "CustomFolderList",
    ):
        assert object_name in start_source

    assert "Qt::WA_TransparentForMouseEvents" in button_source


def test_start_page_layouts_shrink_without_losing_existing_controls() -> None:
    start_source = START_VIEW_CPP.read_text(encoding="utf-8")
    first_start_source = FIRST_START_CPP.read_text(encoding="utf-8")
    file_card_source = FILE_CARD_VIEW_CPP.read_text(encoding="utf-8")
    general_source = GENERAL_SETTINGS_CPP.read_text(encoding="utf-8")

    assert "QSizePolicy::Policy::Expanding, QSizePolicy::Policy::Preferred" in file_card_source
    assert "new QVBoxLayout(hero)" in start_source
    assert "new QVBoxLayout(aiCard)" in first_start_source
    assert "new QGridLayout(this)" in general_source
    for control in (
        "_languageComboBox",
        "_unitSystemComboBox",
        "_navigationStyleComboBox",
    ):
        assert control in general_source
