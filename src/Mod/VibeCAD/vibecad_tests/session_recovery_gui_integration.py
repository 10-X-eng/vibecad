# SPDX-License-Identifier: LGPL-2.1-or-later

"""Clean-profile GUI acceptance gate for crash-session recovery."""

from __future__ import annotations

import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import VibeCADGui as VibeGui
from VibeCADCore import get_service
from VibeCADPreferences import preferences


def _process_events(rounds: int = 16) -> None:
    for _ in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    exit_code = 1
    try:
        preferences().SetString("NewDocumentAuthoringMode", "native")
        Gui.activateWorkbench("PartDesignWorkbench")
        document = App.newDocument("VibeCADCrashRecovery")
        VibeGui._show_panel()
        _process_events()

        service = get_service()
        history = service.conversation_history()
        prompt_text = "Add two reviewed mounting holes."
        prepared = service.prepare_session_recovery("running", prompt_text)
        service.persist_prepared_session_recovery(prepared)

        main_window = Gui.getMainWindow()
        banner = main_window.findChild(
            QtWidgets.QFrame,
            "VibeSessionRecoveryBanner",
        )
        label = main_window.findChild(
            QtWidgets.QLabel,
            "VibeSessionRecoveryText",
        )
        restore = main_window.findChild(
            QtWidgets.QPushButton,
            "VibeSessionRecoveryRestore",
        )
        prompt = main_window.findChild(QtWidgets.QPlainTextEdit, "VibePrompt")
        assert banner is not None and label is not None
        assert restore is not None and prompt is not None

        object_count = len(document.Objects)
        VibeGui._refresh_session_recovery(VibeGui._find_dock())
        _process_events()
        assert not banner.isHidden()
        assert "not replay" in label.text().lower()
        assert prompt.toPlainText() == ""

        restore.click()
        _process_events()
        assert banner.isHidden()
        assert prompt.toPlainText() == prompt_text
        assert prompt.hasFocus()
        assert len(document.Objects) == object_count
        assert VibeGui._assistant_run_controller.snapshot()["active"] is False
        assert service.conversation_history()["conversation_id"] == history[
            "conversation_id"
        ]

        service.discard_session_recovery()
        print("VIBECAD_SESSION_RECOVERY_GUI_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1000, _run)
