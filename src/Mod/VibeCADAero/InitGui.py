# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI bootstrap for the Aero workbench (internal name: VibeCADAero)."""


class VibeCADAeroWorkbench(Workbench):
    """First-class in-app aerodynamics workbench."""

    MenuText = "Aero"
    ToolTip = "NeuralFoil section, AeroSandbox VLM/AeroBuildup, momentum hover, JSBSim"

    def __init__(self):
        self.__class__.Icon = (
            FreeCAD.getHomePath()
            + "Mod/VibeCADAero/icons/vibecad-aero-analyze.svg"
        )

    def Initialize(self):
        import AeroCommandLoader

        AeroCommandLoader.ensure_commands_registered()
        commands = [
            "VibeCADAero_Analyze",
            "VibeCADAero_Section",
            "VibeCADAero_VLM",
            "VibeCADAero_ExportJSBSim",
            "VibeCADAero_Report",
            "VibeCADAero_ProposeRepairs",
            "VibeCADAero_ApplyRepairs",
            "VibeCADAero_FlightCard",
        ]
        self.appendToolbar("Aero", commands)
        self.appendMenu("Aero", commands)
        Log("Loading VibeCAD Aero workbench... done\n")

    def Activated(self):
        Msg("VibeCADAeroWorkbench::Activated()\n")

    def Deactivated(self):
        Msg("VibeCADAeroWorkbench::Deactivated()\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(VibeCADAeroWorkbench())
