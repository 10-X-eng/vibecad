# SPDX-License-Identifier: LGPL-2.1-or-later
"""Ribbon surface: Catalog and Import only; Cache stays on the menu."""

from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import InstallUI  # noqa: E402
import Commands  # noqa: E402


class TestMcMasterRibbon(unittest.TestCase):
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
