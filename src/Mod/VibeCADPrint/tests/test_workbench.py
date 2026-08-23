# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
import sys
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]


def test_initgui_registers_first_class_print_workbench_and_preferences() -> None:
    source = (ROOT / "InitGui.py").read_text(encoding="utf-8")

    assert "class VibeCADPrintWorkbench" in source
    assert 'MenuText = "3D Print"' in source
    assert 'self.appendToolbar("Send"' in source
    assert 'self.appendToolbar("Setup"' in source
    assert (
        'Gui.addPreferencePage(PrintSetupDialog.VibeCADPrintPreferencesPage, "VibeCAD")'
        in source
    )
    assert "Gui.addWorkbench(VibeCADPrintWorkbench())" in source


def test_workbench_commands_are_registered_without_global_commands_collision(
    monkeypatch,
) -> None:
    registered = {}
    gui = SimpleNamespace(
        addCommand=lambda name, command: registered.__setitem__(name, command),
        listCommands=lambda: list(registered),
    )
    generic_commands = SimpleNamespace(owner="another workbench")
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    monkeypatch.setitem(sys.modules, "Commands", generic_commands)
    monkeypatch.delitem(sys.modules, "PrintCommandLoader", raising=False)
    monkeypatch.delitem(sys.modules, "_vibecad_print_commands", raising=False)

    import PrintCommandLoader

    PrintCommandLoader.ensure_commands_registered(gui=gui)

    assert set(registered) == {
        "VibeCADPrint_OpenInPrusaSlicer",
        "VibeCADPrint_Save3MF",
        "VibeCADPrint_Setup",
    }
    assert sys.modules["Commands"] is generic_commands


def test_commands_have_specific_labels_tooltips_and_repo_icons(monkeypatch) -> None:
    registered = {}
    gui = SimpleNamespace(
        addCommand=lambda name, command: registered.__setitem__(name, command),
        listCommands=lambda: list(registered),
    )
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    monkeypatch.delitem(sys.modules, "_vibecad_print_commands", raising=False)

    import PrintCommandLoader

    PrintCommandLoader.ensure_commands_registered(gui=gui)
    resources = {name: command.GetResources() for name, command in registered.items()}

    assert resources["VibeCADPrint_OpenInPrusaSlicer"]["MenuText"] == "Print"
    assert "selected" in resources["VibeCADPrint_OpenInPrusaSlicer"]["ToolTip"].lower()
    assert resources["VibeCADPrint_Save3MF"]["MenuText"] == "Save 3MF"
    assert resources["VibeCADPrint_Setup"]["MenuText"] == "Print Setup"
    assert all(Path(value["Pixmap"]).is_file() for value in resources.values())


def test_cmake_installs_module_tests_and_icons() -> None:
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    parent = (ROOT.parent / "CMakeLists.txt").read_text(encoding="utf-8")

    assert "add_subdirectory(VibeCADPrint)" in parent
    for filename in (
        "InitGui.py",
        "VibeCADPrint.py",
        "PrintPreferences.py",
        "PrintPanel.py",
        "PrintSetupDialog.py",
        "Commands.py",
        "PrintCommandLoader.py",
    ):
        assert filename in cmake
    assert "icons/vibecad-print-open.svg" in cmake
    assert "tests/test_backend.py" in cmake
    assert "tests/qt_selection_integration.py" in cmake
    assert "tests/qt_panel_persistence_integration.py" in cmake
    assert "tests/qt_progress_integration.py" in cmake
    assert "Mod/VibeCADPrint/icons" in cmake


def test_native_ribbon_owns_dedicated_3d_print_domain() -> None:
    ribbon = (REPO / "src/Gui/VibeCADRibbon.cpp").read_text(encoding="utf-8")
    integration = (
        REPO / "src/Mod/VibeCAD/vibecad_tests/qt_ribbon_theme_integration.py"
    ).read_text(encoding="utf-8")

    assert "std::array<DomainDefinition, 9>" in ribbon
    assert '{"3D Print", "VibeCADPrintWorkbench", "print"}' in ribbon
    assert '"VibeCADPrintWorkbench": "print"' in integration
    assert '"VibeCADPrint_OpenInPrusaSlicer"' in integration
    assert '"VibeCADPrint_Save3MF"' in integration
    assert '"VibeCADPrint_Setup"' in integration


def test_setup_dialog_exposes_guided_detection_profiles_and_placement() -> None:
    source = (ROOT / "PrintSetupDialog.py").read_text(encoding="utf-8")

    for text in (
        "Auto-detect",
        "Locate",
        "Open PrusaSlicer",
        "Download",
        "Retry",
        "Printer profile",
        "Print profile",
        "Auto-arrange",
        "Ensure on bed",
        "Open without profiles",
    ):
        assert text in source
    assert "ThreadPoolExecutor" in source
    assert "one material profile for every extruder" in source


def test_print_workbench_registers_and_opens_persistent_panel() -> None:
    init_gui = (ROOT / "InitGui.py").read_text(encoding="utf-8")
    commands = (ROOT / "Commands.py").read_text(encoding="utf-8")
    panel = (ROOT / "PrintPanel.py").read_text(encoding="utf-8")

    assert "PrintPanel.ensure_panel_registered()" in init_gui
    assert "PrintPanel.show_panel()" in init_gui
    assert "PrintPanel.hide_panel()" in init_gui
    assert "PrintPanel.open_setup_dialog" in commands
    assert "PrintPanel.show_panel(refresh=False)" in commands
    resolver = commands.split("def _resolve_handoff_configuration", 1)[1].split(
        "def _managed_cache_directory", 1
    )[0]
    assert "choose_print_setup" not in resolver
    assert 'DOCK_NAME = "VibeCADPrintPanel"' in panel
    assert "main.addDockWindow(contents, DOCK_NAME" in panel
    assert 'QPushButton("Print"' in panel
    assert 'QPushButton("Export 3MF…"' in panel
    assert "Selections are saved automatically" not in panel
    assert "self.output_location.hide()" in panel
    assert 'QGroupBox("Objects to be sent"' in panel
    assert "object_checkboxes" in panel
    assert 'setObjectName("VibeCADPrintSelectionSummary")' in panel
    assert "ScrollBarAlwaysOff" in panel
    assert "Auto-arrange" in panel
    assert "Ensure on bed" in panel


def test_print_and_setup_commands_use_separate_daily_and_configuration_surfaces(
    monkeypatch,
) -> None:
    registered = {}
    gui = SimpleNamespace(
        addCommand=lambda name, command: registered.__setitem__(name, command),
        listCommands=lambda: list(registered),
        getMainWindow=lambda: "main-window",
    )
    calls = []
    daily_panel = SimpleNamespace(print_selected=lambda: calls.append("print"))
    print_panel = SimpleNamespace(
        show_panel=lambda *, refresh: calls.append(("show", refresh)) or daily_panel,
        open_setup_dialog=lambda *, parent: calls.append(("setup", parent)),
    )
    monkeypatch.setitem(sys.modules, "FreeCADGui", gui)
    monkeypatch.setitem(sys.modules, "PrintPanel", print_panel)
    monkeypatch.delitem(sys.modules, "_vibecad_print_commands", raising=False)

    import PrintCommandLoader

    PrintCommandLoader.ensure_commands_registered(gui=gui)
    registered["VibeCADPrint_OpenInPrusaSlicer"].Activated()
    registered["VibeCADPrint_Setup"].Activated()

    assert calls == [("show", False), "print", ("setup", "main-window")]
