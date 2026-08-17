# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI bootstrap for the Aero workbench (internal name: VibeCADAero)."""


class VibeCADAeroWorkbench(Workbench):
    """First-class in-app aerodynamics workbench."""

    MenuText = "Aero"
    ToolTip = "NeuralFoil section, AeroSandbox VLM/AeroBuildup, momentum hover, JSBSim"
    Icon = """
        /* XPM */
        static const char * vibecad_aero_wb_xpm[] = {
        "16 16 3 1",
        "  c None",
        ". c #1B4F72",
        "+ c #5DADE2",
        "                ",
        "                ",
        "         ++     ",
        "       ++++     ",
        "     +++++.     ",
        "   +++++..      ",
        " +++++...       ",
        "+++++...        ",
        " +++...         ",
        "  ++..          ",
        "   +.           ",
        "                ",
        "  ..........    ",
        "                ",
        "                ",
        "                "};
        """

    def Initialize(self):
        import Commands

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

    def Deactivated(self):
        Msg("VibeCADAeroWorkbench::Deactivated()\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(VibeCADAeroWorkbench())
