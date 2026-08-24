# SPDX-License-Identifier: LGPL-2.1-or-later

"""Application bootstrap for the VibeCAD 3D Print module."""


def _schedule_print_ui() -> None:
    """Install the ribbon tab after the main window exists.

    InitGui QTimer.singleShot(0) can fire before VibeCAD's ribbon is built.
    Repeat the install from here as well when the GUI is already up.
    """

    try:
        import FreeCAD as App

        if not getattr(App, "GuiUp", False):
            return
        from PySide import QtCore

        def _boot() -> None:
            try:
                import PrintInstallUI

                PrintInstallUI.install_with_retry()
            except Exception:
                pass

        for delay_ms in (500, 2000, 5000):
            QtCore.QTimer.singleShot(delay_ms, _boot)
    except Exception:
        pass


_schedule_print_ui()

