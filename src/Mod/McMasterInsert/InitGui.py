# SPDX-License-Identifier: LGPL-2.1-or-later
"""McMaster-Carr workbench for VibeCAD.

VibeCAD does not show extra workbenches on its ribbon selector. This file
still registers a real Python workbench, then InstallUI pins Catalog/Import
onto a toolbar, the McMaster-Carr menu, and the ribbon.
"""

import os

import FreeCAD
import FreeCADGui as Gui


class McMasterWorkbench(Workbench):
    """Browse McMaster-Carr and import 3-D CAD."""

    MenuText = "McMaster-Carr"
    ToolTip = "Browse the McMaster-Carr catalog and import 3-D STEP"

    def __init__(self):
        self.__class__.Icon = os.path.join(
            os.path.dirname(__file__),
            "icons",
            "mcmaster-workbench.svg",
        )

    def Initialize(self):
        import Commands
        import InstallUI

        Commands.register(Gui)
        self.appendToolbar(
            "McMaster-Carr",
            ["McMaster_BrowseCatalog", "McMaster_ImportFile"],
        )
        self.appendMenu("McMaster-Carr", list(Commands.COMMANDS))
        InstallUI.install_with_retry()
        Log("Loading McMaster-Carr workbench... done\n")

    def Activated(self):
        try:
            import InstallUI

            InstallUI.install_once()
        except Exception:
            pass
        Msg("McMasterWorkbench::Activated()\n")

    def Deactivated(self):
        Msg("McMasterWorkbench::Deactivated()\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


try:
    Gui.addWorkbench(McMasterWorkbench())
    FreeCAD.Console.PrintMessage("McMaster-Carr workbench registered\n")
except Exception as exc:
    FreeCAD.Console.PrintError(f"McMaster-Carr workbench failed to register: {exc}\n")

try:
    from PySide import QtCore
    import InstallUI

    QtCore.QTimer.singleShot(0, InstallUI.install_with_retry)
except Exception as exc:
    FreeCAD.Console.PrintWarning(f"McMaster-Carr UI install deferred: {exc}\n")
