# SPDX-License-Identifier: LGPL-2.1-or-later

"""Two-process release gate for persisted 3D Print dock placement."""

from __future__ import annotations

import os
import sys
import traceback

import FreeCADGui as Gui
from PySide import QtCore, QtWidgets


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    exit_code = 1
    main = None
    try:
        import PrintPanel

        stage = os.environ.get("VIBECAD_PRINT_PERSISTENCE_STAGE", "")
        assert stage in {"write", "read"}
        assert Gui.activateWorkbench("VibeCADPrintWorkbench")
        QtWidgets.QApplication.processEvents()
        main = Gui.getMainWindow()
        dock = main.findChild(QtWidgets.QDockWidget, PrintPanel.DOCK_NAME)
        assert dock is not None
        if stage == "write":
            main.addDockWidget(QtCore.Qt.LeftDockWidgetArea, dock)
            QtWidgets.QApplication.processEvents()
            assert main.dockWidgetArea(dock) == QtCore.Qt.LeftDockWidgetArea
            print("VIBECAD_PRINT_PANEL_POSITION_SAVED", flush=True)
        else:
            assert main.dockWidgetArea(dock) == QtCore.Qt.LeftDockWidgetArea
            print("VIBECAD_PRINT_PANEL_POSITION_RESTORED", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if exit_code == 0 and main is not None:
            main.close()
            QtWidgets.QApplication.processEvents()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
