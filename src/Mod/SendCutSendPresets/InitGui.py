# -*- coding: utf-8 -*-
# SendCutSend Presets workbench GUI registration
# SPDX-License-Identifier: MIT
#
# FreeCAD execs InitGui with SEPARATE globals/locals. Class bodies only see
# globals (Workbench, Gui, FreeCAD are injected there). Promote our names.

import os
import sys
import FreeCAD


def _mod_dir():
    root = FreeCAD.getUserAppDataDir()
    rels = (
        os.path.join("Mod", "SendCutSendPresets"),
        os.path.join("v1-1", "Mod", "SendCutSendPresets"),
        os.path.join("v26-3", "Mod", "SendCutSendPresets"),
        os.path.join("v1-1", "v26-3", "Mod", "SendCutSendPresets"),
    )
    for rel in rels:
        path = os.path.join(root, rel)
        if os.path.isfile(os.path.join(path, "SCSCommand.py")):
            return path
    return os.path.join(root, "Mod", "SendCutSendPresets")


SCS_MOD_DIR = _mod_dir()
SCS_ICON = os.path.join(SCS_MOD_DIR, "resources", "icons", "SCS_Presets.svg")

# Critical: make names visible to class body under FreeCAD's exec(g, l)
globals().update(locals())


class SendCutSendPresetsWorkbench(Workbench):
    """SendCutSend bend presets for SheetMetal (test bed)."""

    MenuText = "SendCutSend"
    ToolTip = "SendCutSend + custom bend presets (testing workbench)"
    Icon = SCS_ICON

    def Initialize(self):
        mod = SCS_MOD_DIR
        if mod and mod not in sys.path:
            sys.path.insert(0, mod)
        import SCSCommand  # registers SCS_ShowPresets
        import CustomPresets  # registers SCS_ShowCustomPresets

        # Keep BOTH tools here for testing
        cmds = ["SCS_ShowPresets", "SCS_ShowCustomPresets"]
        self.appendToolbar("SendCutSend", cmds)
        self.appendMenu("SendCutSend", cmds)

        # Also expose Bend Presets on SheetMetal via runtime integration
        try:
            import sheetmetal_integration
            sheetmetal_integration.setup()
            import pending_unfold
            pending_unfold.setup()
        except Exception as exc:
            FreeCAD.Console.PrintWarning(
                "[SendCutSendPresets] SheetMetal integration failed: %s\n" % exc
            )

    def Activated(self):
        return

    def Deactivated(self):
        return

    def GetClassName(self):
        return "Gui::PythonWorkbench"


Gui.addWorkbench(SendCutSendPresetsWorkbench())

# Also start SheetMetal integration even if user never opens SendCutSend WB
try:
    if SCS_MOD_DIR and SCS_MOD_DIR not in sys.path:
        sys.path.insert(0, SCS_MOD_DIR)
    import sheetmetal_integration as _scs_sm_int
    _scs_sm_int.setup()
    import pending_unfold as _scs_pending
    _scs_pending.setup()
except Exception:
    pass
