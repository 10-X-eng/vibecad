# SPDX-License-Identifier: LGPL-2.1-or-later

"""Offscreen regression check for the styled Print Setup dialog geometry."""

from __future__ import annotations

import os
from pathlib import Path
import sys


TESTS = Path(__file__).resolve().parent
MODULE = TESTS.parent
REPO = next(
    parent
    for parent in MODULE.parents
    if (parent / "src" / "Gui" / "Stylesheets" / "defaults.qss").is_file()
)
if str(MODULE) not in sys.path:
    sys.path.insert(0, str(MODULE))

from PySide import QtCore, QtWidgets  # noqa: E402

import PrintPreferences  # noqa: E402
import PrintSetupDialog  # noqa: E402


def _interval(widget, dialog, core=QtCore) -> tuple[int, int]:
    top = widget.mapTo(dialog, core.QPoint(0, 0)).y()
    return top, top + widget.height()


def main() -> None:
    widgets = QtWidgets
    app = widgets.QApplication.instance() or widgets.QApplication([])
    setup_module = PrintSetupDialog
    styles = REPO / "src" / "Gui" / "Stylesheets"
    app.setStyleSheet(
        (styles / "defaults.qss").read_text(encoding="utf-8")
        + "\n"
        + (styles / "VibeDark.qss").read_text(encoding="utf-8")
    )

    PrintPreferences.load_confirmed_setup = lambda **_kwargs: None
    PrintPreferences.executable_override = lambda **_kwargs: ""
    real_single_shot = setup_module.QtCore.QTimer.singleShot
    setup_module.QtCore.QTimer.singleShot = lambda *_args: None

    dialog = setup_module.PrintSetupDialog(
        parent=None,
        backend=object(),
        open_after_save=True,
    )
    setup_module.QtCore.QTimer.singleShot = real_single_shot
    dialog.show()
    app.processEvents()

    screen = dialog.screen() or app.primaryScreen()
    available = screen.availableGeometry()
    assert dialog.width() >= 900
    assert dialog.height() <= available.height() - 48
    scroll = dialog.findChild(widgets.QScrollArea, "VibeCADPrintSetupScroll")
    assert scroll is not None

    executable = dialog.findChild(widgets.QGroupBox, "VibeCADPrintExecutableGroup")
    profiles = dialog.findChild(widgets.QGroupBox, "VibeCADPrintProfilesGroup")
    placement = dialog.findChild(widgets.QGroupBox, "VibeCADPrintPlacementGroup")
    storage = dialog.findChild(widgets.QGroupBox, "VibeCADPrintStorageGroup")
    assert all((executable, profiles, placement, storage))
    assert _interval(executable, dialog)[0] == _interval(profiles, dialog)[0]
    assert _interval(placement, dialog)[0] == _interval(storage, dialog)[0]

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
    assert dialog.height() <= available.height() - 48

    screenshot = os.environ.get("VIBECAD_PRINT_SETUP_SCREENSHOT", "")
    if screenshot:
        assert dialog.grab().save(screenshot)

    dialog.close()


if __name__ == "__main__":
    main()
