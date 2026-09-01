# SPDX-License-Identifier: LGPL-2.1-or-later
"""Ribbon surface: Catalog and Import only; Cache stays on the menu."""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import InstallUI  # noqa: E402
import Commands  # noqa: E402


class TestMcMasterRibbon(unittest.TestCase):
    def test_workbench_registers_when_initgui_has_no_file_global(self):
        registered = []
        messages = []

        class DummyWorkbench:
            pass

        freecad = ModuleType("FreeCAD")
        freecad.getResourceDir = lambda: "C:/VibeCAD/"
        freecad.Console = SimpleNamespace(
            PrintMessage=messages.append,
            PrintError=lambda _message: None,
            PrintWarning=lambda _message: None,
        )
        gui = ModuleType("FreeCADGui")
        gui.addWorkbench = registered.append
        qtcore = SimpleNamespace(
            QTimer=SimpleNamespace(singleShot=lambda _delay, _callback: None)
        )
        pyside = ModuleType("PySide")
        pyside.QtCore = qtcore
        install_ui = ModuleType("InstallUI")
        install_ui.install_with_retry = lambda: None
        namespace = {
            "__builtins__": __builtins__,
            "Workbench": DummyWorkbench,
            "Log": lambda _message: None,
            "Msg": lambda _message: None,
        }

        with mock.patch.dict(
            sys.modules,
            {
                "FreeCAD": freecad,
                "FreeCADGui": gui,
                "PySide": pyside,
                "InstallUI": install_ui,
            },
        ):
            source = (ROOT / "InitGui.py").read_text(encoding="utf-8")
            exec(compile(source, str(ROOT / "InitGui.py"), "exec"), namespace)

        self.assertEqual(len(registered), 1)
        self.assertEqual(
            os.path.normpath(registered[0].Icon),
            os.path.normpath(
                "C:/VibeCAD/Mod/McMasterInsert/icons/mcmaster-workbench.svg"
            ),
        )
        self.assertEqual(messages, [])

    def test_module_discovery_and_successful_ui_install_are_quiet(self):
        messages = []
        freecad = ModuleType("FreeCAD")
        freecad.Console = SimpleNamespace(PrintMessage=messages.append)

        class Signal:
            @staticmethod
            def connect(_callback):
                return None

        class Timer:
            def __init__(self):
                self.timeout = Signal()

            @staticmethod
            def setInterval(_interval):
                return None

            @staticmethod
            def stop():
                return None

            @staticmethod
            def start():
                return None

        pyside = ModuleType("PySide")
        pyside.QtCore = SimpleNamespace(QTimer=Timer)
        successful = {"menu": True, "ribbon_tab": True, "ribbon_hook": True}

        with mock.patch.dict(sys.modules, {"FreeCAD": freecad, "PySide": pyside}):
            source = (ROOT / "Init.py").read_text(encoding="utf-8")
            exec(compile(source, str(ROOT / "Init.py"), "exec"), {})
            with mock.patch.object(InstallUI, "install_once", return_value=successful):
                InstallUI._timer = None
                InstallUI.install_with_retry(max_tries=1, interval_ms=1)
                InstallUI._timer = None

        self.assertEqual(messages, [])

    def test_ribbon_buttons_are_catalog_and_import(self):
        labels = tuple(label for label, _command, _icon in InstallUI.BUTTONS)
        self.assertEqual(labels, ("Catalog", "Import"))
        self.assertNotIn("Cache", labels)

    def test_ribbon_does_not_run_open_cache(self):
        commands = tuple(command for _label, command, _icon in InstallUI.BUTTONS)
        self.assertNotIn("McMaster_OpenCache", commands)

    def test_cache_remains_a_registered_menu_command(self):
        self.assertIn("McMaster_OpenCache", Commands.COMMANDS)
        self.assertIn("McMaster_BrowseCatalog", Commands.COMMANDS)
        self.assertIn("McMaster_ImportFile", Commands.COMMANDS)


if __name__ == "__main__":
    unittest.main()
