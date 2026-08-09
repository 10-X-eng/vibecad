# SPDX-License-Identifier: LGPL-2.1-or-later

"""Regression gate for catchable off-thread FreeCADGui errors."""

from __future__ import annotations

import sys
import threading
import traceback

import FreeCADGui as Gui
from PySide import QtCore, QtWidgets


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    result: dict[str, object] = {}

    def worker() -> None:
        try:
            Gui.getMainWindow()
        except BaseException as exc:
            result["exception"] = exc
            result["traceback"] = traceback.format_exc()
        else:
            result["returned"] = True

    thread = threading.Thread(
        target=worker,
        name="VibeCAD-test-off-thread-gui-api",
        daemon=True,
    )
    thread.start()

    def inspect() -> None:
        if thread.is_alive():
            return
        try:
            assert result.get("returned") is not True
            exception = result.get("exception")
            assert isinstance(exception, RuntimeError), result.get("traceback")
            assert "may only be used from the main thread" in str(exception)
            assert Gui.getMainWindow() is not None
            print("VIBECAD_GUI_API_THREAD_GUARD_OK catchable-runtime-error", flush=True)
            application.exit(0)
        except BaseException:
            traceback.print_exc(file=sys.__stderr__)
            application.exit(1)

    poll = QtCore.QTimer(application)
    poll.timeout.connect(inspect)
    poll.start(20)
    QtCore.QTimer.singleShot(5000, lambda: application.exit(1))


QtCore.QTimer.singleShot(1000, _run)
