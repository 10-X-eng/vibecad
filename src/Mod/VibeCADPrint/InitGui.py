# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI bootstrap for VibeCAD's additive 3D Print workbench."""


class VibeCADPrintWorkbench(Workbench):
    """PrusaSlicer profile selection and explicit 3MF handoff."""

    MenuText = "3D Print"
    ToolTip = "Prepare selected CAD objects and open them in PrusaSlicer"

    def __init__(self):
        self.__class__.Icon = (
            FreeCAD.getHomePath() + "Mod/VibeCADPrint/icons/vibecad-print-open.svg"
        )

    def Initialize(self):
        import PrintCommandLoader

        PrintCommandLoader.ensure_commands_registered()
        send_commands = [
            "VibeCADPrint_OpenInPrusaSlicer",
            "VibeCADPrint_Save3MF",
        ]
        setup_commands = ["VibeCADPrint_Setup"]
        self.appendToolbar("Send", send_commands)
        self.appendToolbar("Setup", setup_commands)
        self.appendMenu("3D Print", send_commands + setup_commands)
        Log("Loading VibeCAD 3D Print workbench... done\n")

    def Activated(self):
        Msg("VibeCADPrintWorkbench::Activated()\n")

    def Deactivated(self):
        Msg("VibeCADPrintWorkbench::Deactivated()\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


import PrintSetupDialog

Gui.addPreferencePage(PrintSetupDialog.VibeCADPrintPreferencesPage, "VibeCAD")
Gui.addWorkbench(VibeCADPrintWorkbench())
