# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI bootstrap for the Aero workbench (internal name: VibeCADAero)."""

import AeroIcons


class VibeCADAeroWorkbench(Workbench):
    """First-class in-app aerodynamics workbench."""

    MenuText = "Aero"
    ToolTip = "NeuralFoil section, AeroSandbox VLM/AeroBuildup, momentum hover, JSBSim"
    Icon = AeroIcons.aero_icon_path()

    def Initialize(self):
        import AeroCommandLoader

        AeroCommandLoader.ensure_commands_registered()
        commands = [
            "VibeCADAero_Analyze",
            "VibeCADAero_Section",
            "VibeCADAero_VLM",
            "VibeCADAero_ExportJSBSim",
            "VibeCADAero_Report",
        ]
        self.appendToolbar("Aero", commands)
        self.appendMenu("Aero", commands)
        Log("Loading VibeCAD Aero workbench... done\n")

    def Activated(self):
        Msg("VibeCADAeroWorkbench::Activated()\n")
        try:
            import AeroWorkspace

            AeroWorkspace.show_workspace()
        except Exception as exc:
            Msg(f"VibeCAD Aero workspace unavailable: {exc}\n")

    def Deactivated(self):
        try:
            import AeroWorkspace

            AeroWorkspace.hide_workspace()
        except Exception:
            pass
        Msg("VibeCADAeroWorkbench::Deactivated()\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(VibeCADAeroWorkbench())
