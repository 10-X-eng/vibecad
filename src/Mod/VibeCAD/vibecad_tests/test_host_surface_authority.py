# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import VibeCADHostSurfaceAuthority as authority


def test_authority_activates_exact_workbench_and_drains_bounded_events(
    monkeypatch,
) -> None:
    activated = []
    updated = []
    processed = []
    gui = SimpleNamespace(
        activateWorkbench=activated.append,
        updateGui=lambda: updated.append(True),
    )
    qt_core = SimpleNamespace(QEventLoop=SimpleNamespace(AllEvents="all"))
    qt_widgets = SimpleNamespace(
        QApplication=SimpleNamespace(
            processEvents=lambda mode, duration: processed.append((mode, duration))
        )
    )
    monkeypatch.setitem(__import__("sys").modules, "FreeCADGui", gui)
    monkeypatch.setitem(
        __import__("sys").modules,
        "PySide",
        SimpleNamespace(QtCore=qt_core, QtWidgets=qt_widgets),
    )

    authority.activate_authorized_workbench("AssemblyWorkbench")

    assert activated == ["AssemblyWorkbench"]
    assert len(updated) == 8
    assert processed == [("all", 25)] * 8


def test_authority_enters_edit_mode_for_exact_object_name() -> None:
    entered = []
    gui_document = SimpleNamespace(setEdit=lambda name: entered.append(name) or True)

    assert authority.enter_authorized_edit_mode(gui_document, "Sketch001") is True
    assert entered == ["Sketch001"]


def test_authority_owner_is_registered_in_installed_script_set() -> None:
    cmake = Path(__file__).resolve().parents[1] / "CMakeLists.txt"

    assert "VibeCADHostSurfaceAuthority.py" in cmake.read_text(encoding="utf-8")
