# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI release gate for the 3D Print workbench's persistent panel."""

from __future__ import annotations

import sys
import traceback

import FreeCADGui as Gui
from PySide import QtCore, QtWidgets


def _process_events() -> None:
    QtWidgets.QApplication.processEvents()


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    exit_code = 1
    try:
        import PrintPanel

        class EmptyBackend:
            def discover(self, _override=""):
                return ()

        PrintPanel.VibeCADPrint.PrusaSlicerBackend = EmptyBackend

        assert Gui.activateWorkbench("VibeCADPrintWorkbench")
        _process_events()
        main_window = Gui.getMainWindow()
        dock = main_window.findChild(
            QtWidgets.QDockWidget,
            PrintPanel.DOCK_NAME,
        )
        assert dock is not None
        assert dock.isVisible()
        assert dock.toggleViewAction().data() == PrintPanel.DOCK_NAME
        assert isinstance(dock.widget(), PrintPanel.PrintPanelWidget)
        assert dock.widget().findChild(
            QtWidgets.QScrollArea,
            "VibeCADPrintPanelScroll",
        ) is not None

        assert Gui.activateWorkbench("PartDesignWorkbench")
        _process_events()
        assert not dock.isVisible()

        assert Gui.activateWorkbench("VibeCADPrintWorkbench")
        _process_events()
        assert dock.isVisible()
        print("VIBECAD_PRINT_PANEL_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
