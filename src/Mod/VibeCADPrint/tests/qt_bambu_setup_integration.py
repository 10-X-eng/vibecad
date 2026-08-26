# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live contract for the shared setup dialog's selected slicer identity."""

from __future__ import annotations

from pathlib import Path
import sys
import traceback
from types import SimpleNamespace


TESTS = Path(__file__).resolve().parent
MODULE = TESTS.parent
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from PySide import QtCore, QtWidgets  # noqa: E402

import PrintSetupDialog  # noqa: E402


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    exit_code = 1
    dialog = None
    try:
        loaded = []
        PrintSetupDialog.PrintPreferences.load_confirmed_setup = (
            lambda *, backend_id="prusaslicer": loaded.append(backend_id) or None
        )
        PrintSetupDialog.PrintPreferences.executable_override = (
            lambda *, backend_id="prusaslicer": ""
        )
        PrintSetupDialog.PrintPreferences.load_handoff_storage = lambda: SimpleNamespace(
            mode="managed", directory=""
        )
        backend = SimpleNamespace(
            backend_id="bambustudio",
            display_name="Bambu Studio",
        )
        real_single_shot = PrintSetupDialog.QtCore.QTimer.singleShot
        PrintSetupDialog.QtCore.QTimer.singleShot = lambda *_args: None
        try:
            dialog = PrintSetupDialog.PrintSetupDialog(
                parent=None,
                backend=backend,
                open_after_save=False,
            )
        finally:
            PrintSetupDialog.QtCore.QTimer.singleShot = real_single_shot

        titles = {group.title() for group in dialog.findChildren(QtWidgets.QGroupBox)}
        labels = " ".join(
            label.text() for label in dialog.findChildren(QtWidgets.QLabel)
        )
        assert loaded == ["bambustudio"]
        assert "Bambu Studio" in titles
        assert dialog.open_slicer_button.text() == "Open Bambu Studio"
        assert "Bambu Studio" in labels
        assert "Bambu Studio" in dialog.auto_arrange.toolTip()
        print("VIBECAD_PRINT_BAMBU_SETUP_OK", flush=True)
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
    finally:
        if dialog is not None:
            dialog.close()
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
