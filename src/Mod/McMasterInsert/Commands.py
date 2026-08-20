# SPDX-License-Identifier: LGPL-2.1-or-later
"""Workbench commands for McMaster-Carr."""

from __future__ import annotations

from pathlib import Path

ICON_DIR = Path(__file__).resolve().parent / "icons"
ICON = str(ICON_DIR / "mcmaster-workbench.svg")


class BrowseCatalogCommand:
    def GetResources(self):
        return {
            "Pixmap": str(ICON_DIR / "catalog.svg"),
            "MenuText": "Browse Catalog",
            "ToolTip": "Open the McMaster-Carr catalog and auto-import 3-D STEP.",
        }

    def IsActive(self):
        return True

    def Activated(self):
        try:
            from McMasterInsert import run

            run()
        except Exception as exc:
            import FreeCAD as App

            App.Console.PrintError(f"McMaster Catalog failed: {exc}\n")


class ImportFileCommand:
    def GetResources(self):
        return {
            "Pixmap": str(ICON_DIR / "import.svg"),
            "MenuText": "Import CAD File",
            "ToolTip": "Import a McMaster STEP/IGES/SAT file you already downloaded.",
        }

    def IsActive(self):
        return True

    def Activated(self):
        from McMasterInsert import import_file

        import_file()


class OpenCacheCommand:
    def GetResources(self):
        return {
            "Pixmap": str(ICON_DIR / "cache.svg"),
            "MenuText": "Open Cache Folder",
            "ToolTip": "Open the local McMaster CAD cache.",
        }

    def IsActive(self):
        return True

    def Activated(self):
        from McMasterInsert import open_cache

        open_cache()


COMMANDS = (
    "McMaster_BrowseCatalog",
    "McMaster_ImportFile",
    "McMaster_OpenCache",
)


def register(gui=None) -> None:
    if gui is None:
        import FreeCADGui as gui  # type: ignore[no-redef]
    existing = set(gui.listCommands())
    mapping = {
        "McMaster_BrowseCatalog": BrowseCatalogCommand,
        "McMaster_ImportFile": ImportFileCommand,
        "McMaster_OpenCache": OpenCacheCommand,
    }
    for name, cls in mapping.items():
        if name not in existing:
            gui.addCommand(name, cls())
