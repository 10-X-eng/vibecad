# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live regression for completed progress operations not becoming cancellations."""

from __future__ import annotations

import sys
import time
import traceback

import FreeCADGui as Gui
from PySide import QtCore, QtWidgets


def _slow_success() -> str:
    time.sleep(0.35)
    return "prepared"


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    exit_code = 1
    try:
        import PrintSetupDialog

        result = PrintSetupDialog.run_with_progress(
            Gui.getMainWindow(),
            "Preparing a slicer project…",
            _slow_success,
        )
        assert result == "prepared"
        print("VIBECAD_PRINT_PROGRESS_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
