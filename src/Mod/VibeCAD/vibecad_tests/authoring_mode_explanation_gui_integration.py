# SPDX-License-Identifier: LGPL-2.1-or-later

"""Run with the portable GUI launcher to check the real mode-switch dialog."""

import traceback

import FreeCAD as App
from PySide import QtCore, QtWidgets
import VibeCADGui


def run():
    application = QtWidgets.QApplication.instance()
    errors = []
    timer = QtCore.QTimer()

    def answer():
        dialog = application.activeModalWidget()
        if not isinstance(dialog, QtWidgets.QMessageBox):
            return
        timer.stop()
        try:
            assert dialog.defaultButton() == dialog.button(QtWidgets.QMessageBox.Cancel)
            assert dialog.escapeButton() == dialog.button(QtWidgets.QMessageBox.Cancel)
            assert "You and the AI" in dialog.informativeText()
            assert "code is kept" in dialog.informativeText()
            assert "epoch" not in dialog.informativeText()
            if accept:
                button = next(b for b in dialog.buttons() if b.text() == "Switch to Native")
                button.click()
            else:
                dialog.reject()  # Same rejection path as Escape/window close.
        except Exception:
            errors.append(traceback.format_exc())
            dialog.reject()

    timer.timeout.connect(answer)
    try:
        for accept in (False, True):
            timer.start(100)
            result = VibeCADGui._confirm_take_manual_control()
            assert not errors, "\n".join(errors)
            assert result is accept
        App.Console.PrintMessage("AUTHORING_MODE_DIALOG PASS: Cancel, Accept, default and escape\n")
        application.exit(0)
    except Exception:
        App.Console.PrintError(traceback.format_exc())
        application.exit(1)
    finally:
        timer.stop()


QtCore.QTimer.singleShot(1000, run)
