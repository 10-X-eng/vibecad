# SPDX-License-Identifier: LGPL-2.1-or-later

"""Offscreen regression check for the styled Print Setup dialog geometry."""

from __future__ import annotations

from pathlib import Path
import sys


TESTS = Path(__file__).resolve().parent
MODULE = TESTS.parent
REPO = MODULE.parents[2]
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from PySide import QtCore, QtWidgets  # noqa: E402

import PrintPreferences  # noqa: E402
import PrintSetupDialog  # noqa: E402


def _interval(widget, dialog) -> tuple[int, int]:
    top = widget.mapTo(dialog, QtCore.QPoint(0, 0)).y()
    return top, top + widget.height()


def main() -> None:
    app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    styles = REPO / "src" / "Gui" / "Stylesheets"
    app.setStyleSheet(
        (styles / "defaults.qss").read_text(encoding="utf-8")
        + "\n"
        + (styles / "VibeDark.qss").read_text(encoding="utf-8")
    )

    PrintPreferences.load_confirmed_setup = lambda **_kwargs: None
    PrintPreferences.executable_override = lambda **_kwargs: ""
    real_single_shot = PrintSetupDialog.QtCore.QTimer.singleShot
    PrintSetupDialog.QtCore.QTimer.singleShot = lambda *_args: None

    dialog = PrintSetupDialog.PrintSetupDialog(
        parent=None,
        backend=object(),
        open_after_save=True,
    )
    PrintSetupDialog.QtCore.QTimer.singleShot = real_single_shot
    dialog.show()
    app.processEvents()

    assert dialog.height() >= dialog.sizeHint().height(), (
        f"Print Setup opened at {dialog.height()} px, below its styled "
        f"{dialog.sizeHint().height()} px size hint"
    )

    rows = [dialog.printer_combo, dialog.bed_details, dialog.print_combo]
    for upper, lower in zip(rows, rows[1:]):
        assert _interval(upper, dialog)[1] <= _interval(lower, dialog)[0], (
            f"{type(upper).__name__} overlaps {type(lower).__name__}"
        )

    dialog._set_status(
        "PrusaSlicer profile query failed with status 1. stdout: diagnostic "
        "timestamp and details. stderr: Configuration was not found; this is "
        "intentionally long enough to wrap onto multiple lines."
    )
    app.processEvents()
    assert dialog.height() >= dialog.sizeHint().height()

    dialog.close()


if __name__ == "__main__":
    main()
