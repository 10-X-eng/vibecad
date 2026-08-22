# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI acceptance for full-width, theme-native BOM spreadsheet presentation."""

from __future__ import annotations

import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtWidgets

import Assembly  # noqa: F401 - registers Assembly document objects
import AssemblyGui  # noqa: F401 - registers Assembly view providers
import SpreadsheetGui  # noqa: F401 - registers spreadsheet MDI views


def _process_events(rounds: int = 24) -> None:
    for _index in range(rounds):
        Gui.updateGui()
        QtWidgets.QApplication.processEvents(QtCore.QEventLoop.AllEvents, 25)


def _run() -> None:
    application = QtWidgets.QApplication.instance()
    document = None
    initial_theme = ""
    exit_code = 1
    try:
        main_window = Gui.getMainWindow()
        main_window.resize(1440, 900)
        main_window.show()
        theme_preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/MainWindow"
        )
        initial_theme = theme_preferences.GetString("AppearanceMode", "Dark")

        document = App.newDocument("VibeCADBOMPresentation")
        Gui.activateView("Gui::View3DInventor", True)
        _process_events()

        browser_host = main_window.findChild(
            QtWidgets.QWidget,
            "VibeCADModelBrowserHost",
        )
        assert browser_host is not None and browser_host.isVisible()

        bom = document.addObject("Assembly::BomObject", "BOM")
        bom.autoGenerate = False
        for cell, value in (
            ("A1", "Index"),
            ("B1", "Part"),
            ("C1", "Quantity"),
            ("A2", "1"),
            ("B2", "Drive housing"),
            ("C2", "1"),
        ):
            bom.set(cell, value)
        document.recompute()

        bom.ViewObject.showSheetMdi()
        _process_events()
        assert not browser_host.isVisible(), (
            "The permanent model browser still covers the active BOM spreadsheet."
        )

        formula_bar = main_window.findChild(
            QtWidgets.QFrame,
            "spreadsheetFormulaBar",
        )
        footer = main_window.findChild(
            QtWidgets.QFrame,
            "spreadsheetFooter",
        )
        assert formula_bar is not None, "The spreadsheet still has the legacy unframed editor row."
        assert footer is not None, "The spreadsheet still has the legacy loose zoom controls."
        margins = formula_bar.layout().contentsMargins()
        assert margins.left() >= 8 and margins.top() >= 8

        theme_button = main_window.findChild(
            QtWidgets.QToolButton,
            "VibeCADThemeToggle",
        )
        assert theme_button is not None
        theme_button.click()
        _process_events()
        assert theme_preferences.GetString("AppearanceMode", "") != initial_theme
        assert not browser_host.isVisible()
        assert formula_bar.isVisible() and footer.isVisible()
        theme_button.click()
        _process_events()
        assert theme_preferences.GetString("AppearanceMode", "") == initial_theme

        Gui.activateView("Gui::View3DInventor", False)
        _process_events()
        assert browser_host.isVisible(), (
            "The model browser did not return when the 3D view became active."
        )

        print(
            "VIBECAD_BOM_SPREADSHEET_PRESENTATION_GUI_OK "
            "full_width=true browser_restored=true formula_bar=true footer=true",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc()
    finally:
        if document is not None and document.Name in App.listDocuments():
            App.closeDocument(document.Name)
        if initial_theme:
            preferences = App.ParamGet(
                "User parameter:BaseApp/Preferences/MainWindow"
            )
            if preferences.GetString("AppearanceMode", "") != initial_theme:
                button = Gui.getMainWindow().findChild(
                    QtWidgets.QToolButton,
                    "VibeCADThemeToggle",
                )
                if button is not None:
                    button.click()
                    _process_events()
        application.exit(exit_code)


QtCore.QTimer.singleShot(0, _run)
