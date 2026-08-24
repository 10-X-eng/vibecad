# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import PrintInstallUI


ROOT = Path(__file__).resolve().parents[1]


class _FakeTabs:
    def __init__(self, items: list[tuple[str, str]] | None = None) -> None:
        self.texts: list[str] = []
        self.data: list[str] = []
        for text, data in items or []:
            self.texts.append(text)
            self.data.append(data)
        self.blocked = False

    def count(self) -> int:
        return len(self.texts)

    def tabText(self, index: int) -> str:
        return self.texts[index]

    def tabData(self, index: int) -> str:
        return self.data[index]

    def insertTab(self, index: int, text: str) -> None:
        self.texts.insert(index, text)
        self.data.insert(index, "")

    def setTabData(self, index: int, data: str) -> None:
        self.data[index] = data

    def blockSignals(self, value: bool) -> None:
        self.blocked = value


class _FakeWindow:
    def __init__(self, tabs: _FakeTabs | None = None, page: object | None = None) -> None:
        self.tabs = tabs
        self.page = page

    def findChild(self, _type, name: str):
        if name == PrintInstallUI.RIBBON_TABS:
            return self.tabs
        if name == PrintInstallUI.RIBBON_PAGE:
            return self.page
        return None


def test_initgui_installs_ribbon_overlay_because_vibecad_hides_workbenches() -> None:
    source = (ROOT / "InitGui.py").read_text(encoding="utf-8")

    assert "PrintInstallUI.install_with_retry" in source
    assert "import InstallUI" not in source
    assert "VibeCAD hides the classic workbench combo" in source
    assert "for _delay_ms in (0, 250, 750, 1500, 3000, 6000)" in source


def test_init_py_reschedules_ribbon_install_after_gui_is_up() -> None:
    source = (ROOT / "Init.py").read_text(encoding="utf-8")

    assert "PrintInstallUI.install_with_retry" in source
    assert "(500, 2000, 5000)" in source


def test_preferences_page_lists_prusaslicer_bambu_and_orca() -> None:
    source = (ROOT / "PrintSetupDialog.py").read_text(encoding="utf-8")

    assert '("PrusaSlicer", "prusaslicer")' in source
    assert '("Bambu Studio", "bambustudio")' in source
    assert '("OrcaSlicer", "orcaslicer")' in source
    assert 'addRow("Slicer"' in source


def test_ribbon_tab_is_added_when_the_native_cpp_domain_is_missing() -> None:
    PrintInstallUI._injected_print_tab = False
    tabs = _FakeTabs([("Model", "PartDesignWorkbench")])
    gui = SimpleNamespace(getMainWindow=lambda: _FakeWindow(tabs))

    assert PrintInstallUI.install_ribbon_tab(gui, SimpleNamespace()) is True
    assert tabs.texts[-1] == "3D Print"
    assert tabs.data[-1] == "VibeCADPrintWorkbench"


def test_ribbon_tab_is_inserted_after_aero_not_after_mcmaster() -> None:
    PrintInstallUI._injected_print_tab = False
    tabs = _FakeTabs(
        [
            ("Model", "PartDesignWorkbench"),
            ("Aero", "VibeCADAeroWorkbench"),
            ("McMaster", "McMasterWorkbench"),
        ]
    )
    gui = SimpleNamespace(getMainWindow=lambda: _FakeWindow(tabs))

    assert PrintInstallUI.install_ribbon_tab(gui, SimpleNamespace()) is True
    assert tabs.texts == [
        "Model",
        "Aero",
        "3D Print",
        "McMaster",
    ]


def test_native_3d_print_tab_does_not_install_an_overlay_hook() -> None:
    PrintInstallUI._injected_print_tab = False
    tabs = _FakeTabs(
        [
            ("Model", "PartDesignWorkbench"),
            ("3D Print", "VibeCADPrintWorkbench"),
        ]
    )
    connected = []
    tabs.currentChanged = SimpleNamespace(connect=connected.append)
    tabs.currentIndex = lambda: 0
    gui = SimpleNamespace(getMainWindow=lambda: _FakeWindow(tabs))

    assert PrintInstallUI.install_ribbon_tab(gui, SimpleNamespace()) is True
    assert PrintInstallUI._injected_print_tab is False
    assert (
        PrintInstallUI.hook_ribbon_tab_changes(
            gui, SimpleNamespace(), SimpleNamespace(), SimpleNamespace()
        )
        is True
    )
    assert connected == []


def test_ribbon_tab_is_idempotent_when_native_3d_print_already_exists() -> None:
    PrintInstallUI._injected_print_tab = False
    tabs = _FakeTabs(
        [
            ("Model", "PartDesignWorkbench"),
            ("3D Print", "VibeCADPrintWorkbench"),
        ]
    )
    gui = SimpleNamespace(getMainWindow=lambda: _FakeWindow(tabs))

    assert PrintInstallUI.install_ribbon_tab(gui, SimpleNamespace()) is True
    assert tabs.texts.count("3D Print") == 1


def test_print_install_module_is_not_shadowed_by_mcmaster_installui(
    monkeypatch,
) -> None:
    """McMasterInsert also exports InstallUI; Print must use a unique name."""

    captured = {}

    def fake_mcmaster_retry() -> None:
        captured["mcmaster"] = True

    monkeypatch.setitem(
        __import__("sys").modules,
        "InstallUI",
        SimpleNamespace(install_with_retry=fake_mcmaster_retry),
    )
    source = (ROOT / "InitGui.py").read_text(encoding="utf-8")
    assert "import PrintInstallUI" in source
    assert "import InstallUI" not in source
    assert "PrintInstallUI.install_with_retry" in source
