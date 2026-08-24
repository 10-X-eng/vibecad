# SPDX-License-Identifier: LGPL-2.1-or-later

"""GUI bootstrap for VibeCAD's additive 3D Print workbench.

VibeCAD hides the classic workbench combo. Register the Python workbench,
then PrintInstallUI pins a 3D Print ribbon tab on app builds that predate the
compiled ribbon domain.
"""

import PrintIcons


class VibeCADPrintWorkbench(Workbench):
    """External slicer profile selection and explicit 3MF handoff."""

    MenuText = "3D Print"
    ToolTip = "Prepare selected CAD objects and open them in an external slicer"

    def __init__(self):
        self.__class__.Icon = PrintIcons.icon_path("open")

    def Initialize(self):
        import PrintCommandLoader
        import PrintInstallUI
        import PrintPanel

        PrintCommandLoader.ensure_commands_registered()
        PrintPanel.ensure_panel_registered()
        send_commands = [
            "VibeCADPrint_OpenInPrusaSlicer",
            "VibeCADPrint_Save3MF",
        ]
        setup_commands = ["VibeCADPrint_Setup"]
        self.appendToolbar("Send", send_commands)
        self.appendToolbar("Setup", setup_commands)
        self.appendMenu("3D Print", send_commands + setup_commands)
        PrintInstallUI.install_with_retry()
        Log("Loading VibeCAD 3D Print workbench... done\n")

    def Activated(self):
        import PrintPanel

        Msg("VibeCADPrintWorkbench::Activated()\n")
        PrintPanel.show_panel()

    def Deactivated(self):
        import PrintPanel

        PrintPanel.hide_panel()
        Msg("VibeCADPrintWorkbench::Deactivated()\n")

    def GetClassName(self):
        return "Gui::PythonWorkbench"


import PrintSetupDialog

Gui.addPreferencePage(PrintSetupDialog.VibeCADPrintPreferencesPage, "VibeCAD")
try:
    Gui.addWorkbench(VibeCADPrintWorkbench())
except Exception as exc:
    try:
        Log(f"3D Print workbench register failed: {exc}\n")
    except Exception:
        pass


def _boot_print_ui():
    try:
        import PrintInstallUI

        PrintInstallUI.install_with_retry()
    except Exception as exc:
        try:
            Log(f"3D Print UI install failed: {exc}\n")
        except Exception:
            pass


try:
    from PySide import QtCore

    for _delay_ms in (0, 250, 750, 1500, 3000, 6000):
        QtCore.QTimer.singleShot(_delay_ms, _boot_print_ui)
except Exception as exc:
    try:
        Log(f"3D Print UI install deferred: {exc}\n")
    except Exception:
        pass

