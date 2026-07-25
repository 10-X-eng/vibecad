# SPDX-License-Identifier: LGPL-2.1-or-later

"""Live GUI release gate for the VibeCAD ribbon and two-mode appearance."""

from __future__ import annotations

import os
import sys
import traceback

import FreeCAD as App
import FreeCADGui as Gui
from PySide import QtCore, QtGui, QtWidgets


def _visible_main_window_toolbars(main_window):
    return [
        toolbar
        for toolbar in main_window.findChildren(QtWidgets.QToolBar)
        if toolbar.isVisible()
        and (
            main_window.toolBarArea(toolbar) != QtCore.Qt.NoToolBarArea
            or toolbar.parentWidget() is main_window
        )
    ]


def _process_events():
    application = QtWidgets.QApplication.instance()
    application.processEvents()
    event_loop = QtCore.QEventLoop()
    QtCore.QTimer.singleShot(100, event_loop.quit)
    event_loop.exec()
    application.processEvents()


def _key_click(widget, key):
    application = QtWidgets.QApplication.instance()
    application.sendEvent(
        widget,
        QtGui.QKeyEvent(QtCore.QEvent.KeyPress, key, QtCore.Qt.NoModifier),
    )
    application.sendEvent(
        widget,
        QtGui.QKeyEvent(QtCore.QEvent.KeyRelease, key, QtCore.Qt.NoModifier),
    )


def _assert_visible_inside(widget, ancestor):
    assert widget is not None and widget.isVisible()
    top_left = widget.mapTo(ancestor, QtCore.QPoint(0, 0))
    bottom_right = top_left + QtCore.QPoint(
        max(0, widget.width() - 1), max(0, widget.height() - 1)
    )
    assert ancestor.rect().contains(top_left)
    assert ancestor.rect().contains(bottom_right)


def _run():
    application = QtWidgets.QApplication.instance()
    main_window = Gui.getMainWindow()
    document = None
    initial_mode = None
    sentinel = App.ParamGet(
        "User parameter:BaseApp/Preferences/Mod/Sketcher/VibeCADRibbonSmoke"
    )
    retired_theme_customization = App.ParamGet(
        "User parameter:BaseApp/Preferences/Themes"
    )

    try:
        main_window.resize(1440, 900)
        main_window.show()
        _process_events()

        ribbon = main_window.findChild(
            QtWidgets.QToolBar, "VibeCADRibbonToolBar"
        )
        root = main_window.findChild(QtWidgets.QWidget, "VibeCADRibbon")
        tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
        theme_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADThemeToggle"
        )
        search = main_window.findChild(
            QtWidgets.QLineEdit, "VibeCADCommandSearch"
        )
        assistant_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADRibbonAssistant"
        )
        settings_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADRibbonSettings"
        )
        assert ribbon is not None and ribbon.isVisible()
        assert root is not None and root.isVisible()
        assert tabs is not None
        assert theme_button is not None
        assert search is not None and search.completer() is not None
        _assert_visible_inside(assistant_button, root)
        _assert_visible_inside(settings_button, root)
        assert assistant_button.text() == "Assistant"
        assert settings_button.text() == "Settings"
        assert (
            assistant_button.defaultAction().property("VibeCADCommandId")
            == "VibeCAD_OpenAssistant"
        )
        assert (
            settings_button.defaultAction().property("VibeCADCommandId")
            == "VibeCAD_OpenPreferences"
        )
        assert not main_window.menuBar().isVisible()
        assert _visible_main_window_toolbars(main_window) == [ribbon]

        expected_tabs = [
            "Model",
            "Assemble",
            "Inspect",
            "Analyze",
            "Manufacture",
            "Drawing",
        ]
        assert [tabs.tabText(index) for index in range(tabs.count())] == (
            expected_tabs
        )
        theme_selector = main_window.findChild(
            QtWidgets.QWidget, "ThemeSelectorWidget"
        )
        assert theme_selector is not None
        assert sorted(
            button.text()
            for button in theme_selector.findChildren(QtWidgets.QToolButton)
        ) == ["Dark", "Light"]
        assert "more themes" not in " ".join(
            label.text()
            for label in theme_selector.findChildren(QtWidgets.QLabel)
        ).lower()

        completion_model = search.completer().model()
        completion_values = [
            str(
                completion_model.data(
                    completion_model.index(row, 0), QtCore.Qt.DisplayRole
                )
            )
            for row in range(completion_model.rowCount())
        ]
        assert any("Std_New" in value for value in completion_values)
        assert any("PartDesign_Body" in value for value in completion_values)

        theme_parameters = App.ParamGet(
            "User parameter:BaseApp/Preferences/MainWindow"
        )
        initial_mode = theme_parameters.GetString("AppearanceMode", "Dark")
        sentinel.SetInt("UnrelatedPreference", 8472)
        retired_theme_customization.SetUnsigned(
            "ThemeAccentColor1", 0xFF00FFFF
        )
        retired_theme_customization.SetUnsigned(
            "ThemeAccentColor2", 0x00FFFFFF
        )
        retired_theme_customization.SetUnsigned(
            "ThemeAccentColor3", 0x0000FFFF
        )
        theme_button.click()
        _process_events()
        switched_mode = theme_parameters.GetString("AppearanceMode", "")
        assert switched_mode in {"Light", "Dark"}
        assert switched_mode != initial_mode
        assert theme_parameters.GetString("Theme", "") == switched_mode
        assert theme_parameters.GetString("StyleSheet", "") == (
            "VibeLight.qss"
            if switched_mode == "Light"
            else "VibeDark.qss"
        )
        assert [
            button.text()
            for button in theme_selector.findChildren(QtWidgets.QToolButton)
            if button.isChecked()
        ] == [switched_mode]
        assert sentinel.GetInt("UnrelatedPreference", 0) == 8472
        assert not any(
            name.startswith("ThemeAccentColor")
            for name in retired_theme_customization.GetUnsigneds()
        )
        switched_screenshot = os.environ.get(
            "VIBECAD_RIBBON_SWITCHED_SCREENSHOT"
        )
        if switched_screenshot:
            screen = main_window.screen() or application.primaryScreen()
            assert screen.grabWindow(main_window.winId()).save(
                switched_screenshot
            )
        theme_button.click()
        _process_events()
        assert theme_parameters.GetString("AppearanceMode", "") == initial_mode

        for index in range(tabs.count()):
            tabs.setCurrentIndex(index)
            _process_events()
            workbench = str(tabs.tabData(index))
            assert Gui.activeWorkbench().name() == workbench
            assert main_window.findChildren(
                QtWidgets.QFrame, "VibeCADRibbonGroup_View"
            )
            for label in main_window.findChildren(
                QtWidgets.QLabel, "VibeCADRibbonGroupTitle"
            ):
                assert not any(
                    term in label.text()
                    for term in (
                        "Part Design",
                        "PartDesign",
                        "TechDraw",
                        "Sketcher",
                        "Workbench",
                    )
                )
            assert _visible_main_window_toolbars(main_window) == [ribbon]
        assert assistant_button.text() == "Assistant"
        assert settings_button.text() == "Settings"

        main_window.resize(850, 760)
        _process_events()
        assert (
            assistant_button.toolButtonStyle()
            == QtCore.Qt.ToolButtonIconOnly
        )
        assert (
            settings_button.toolButtonStyle()
            == QtCore.Qt.ToolButtonIconOnly
        )
        _assert_visible_inside(search, root)
        _assert_visible_inside(assistant_button, root)
        _assert_visible_inside(settings_button, root)
        saw_collapsed_group = False
        for index in range(tabs.count()):
            tabs.setCurrentIndex(index)
            _process_events()
            page = main_window.findChild(
                QtWidgets.QWidget, "VibeCADRibbonPage"
            )
            groups = [
                group
                for group in page.findChildren(QtWidgets.QFrame)
                if group.property("ribbonGroup")
            ]
            visible_groups = [group for group in groups if group.isVisible()]
            hidden_groups = [group for group in groups if not group.isVisible()]
            saw_collapsed_group = saw_collapsed_group or any(
                bool(group.property("collapsed"))
                for group in visible_groups
            )
            for group in visible_groups:
                _assert_visible_inside(group, page)
            overflow = page.findChild(
                QtWidgets.QToolButton, "VibeCADRibbonPageMore"
            )
            assert overflow is not None
            assert overflow.isVisible() == bool(hidden_groups)
            if hidden_groups:
                assert len(overflow.menu().actions()) == len(hidden_groups)
                _assert_visible_inside(overflow, page)
        assert saw_collapsed_group
        extension = ribbon.findChild(
            QtWidgets.QToolButton, "qt_toolbar_ext_button"
        )
        assert extension is None or not extension.isVisible()

        main_window.resize(1440, 900)
        _process_events()
        document = App.newDocument("VibeCADRibbonSmoke")
        sketch = document.addObject("Sketcher::SketchObject", "RibbonSketch")
        document.recompute()
        Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert any(
            tabs.tabText(index) == "Sketch" for index in range(tabs.count())
        )
        assert main_window.findChildren(
            QtWidgets.QFrame, "VibeCADRibbonGroup_Geometry"
        )
        Gui.activeDocument().resetEdit()
        _process_events()
        assert all(
            tabs.tabText(index) != "Sketch" for index in range(tabs.count())
        )

        _key_click(main_window, QtCore.Qt.Key_F10)
        _process_events()
        assert main_window.menuBar().isVisible()
        _key_click(main_window, QtCore.Qt.Key_F10)
        _process_events()
        assert not main_window.menuBar().isVisible()
        assert QtWidgets.QApplication.activePopupWidget() is None

        preferences_check = {}

        def inspect_preferences_dialog():
            dialog = None
            try:
                dialog = next(
                    (
                        candidate
                        for candidate in application.topLevelWidgets()
                        if isinstance(candidate, QtWidgets.QDialog)
                        and candidate.isVisible()
                        and candidate.findChild(
                            QtWidgets.QComboBox, "themesCombobox"
                        )
                        is not None
                    ),
                    None,
                )
                assert dialog is not None
                theme_combo = dialog.findChild(
                    QtWidgets.QComboBox, "themesCombobox"
                )
                assert [
                    theme_combo.itemText(index)
                    for index in range(theme_combo.count())
                ] == ["Light", "Dark"]
                for removed_object in (
                    "ImportConfig",
                    "SaveNewPreferencePack",
                    "ManagePreferencePacks",
                    "RevertToSavedConfig",
                    "moreThemesLabel",
                    "ThemeAccentColor1",
                    "ThemeAccentColor2",
                    "ThemeAccentColor3",
                    "StyleSheets",
                    "OverlayStyleSheets",
                    "themeEditorButton",
                ):
                    assert (
                        dialog.findChild(QtWidgets.QWidget, removed_object)
                        is None
                    )
                preferences_check["ok"] = True
            except Exception:
                preferences_check["error"] = traceback.format_exc()
            finally:
                if dialog is None:
                    dialog = application.activeModalWidget()
                if isinstance(dialog, QtWidgets.QDialog):
                    dialog.reject()

        QtCore.QTimer.singleShot(500, inspect_preferences_dialog)
        Gui.runCommand("Std_DlgPreferences")
        assert preferences_check.get("ok"), preferences_check.get("error")
        _process_events()
        assert _visible_main_window_toolbars(main_window) == [ribbon]
        assert not main_window.menuBar().isVisible()

        screenshot_path = os.environ.get("VIBECAD_RIBBON_SCREENSHOT")
        if screenshot_path:
            screen = main_window.screen() or application.primaryScreen()
            assert screen.grabWindow(main_window.winId()).save(screenshot_path)

        print(
            "VIBECAD_RIBBON_THEME_GUI_OK "
            f"tabs={tabs.count()} mode={initial_mode}",
            flush=True,
        )
        exit_code = 0
    except Exception:
        traceback.print_exc(file=sys.__stderr__)
        exit_code = 1
    finally:
        sentinel.RemInt("UnrelatedPreference")
        for name in (
            "ThemeAccentColor1",
            "ThemeAccentColor2",
            "ThemeAccentColor3",
        ):
            retired_theme_customization.RemUnsigned(name)
        if initial_mode in {"Light", "Dark"}:
            current = main_window.findChild(
                QtWidgets.QToolButton, "VibeCADThemeToggle"
            )
            parameters = App.ParamGet(
                "User parameter:BaseApp/Preferences/MainWindow"
            )
            if (
                current is not None
                and parameters.GetString("AppearanceMode", "") != initial_mode
            ):
                current.click()
                _process_events()
        if document is not None:
            if Gui.activeDocument() and Gui.activeDocument().getInEdit():
                Gui.activeDocument().resetEdit()
            App.closeDocument(document.Name)
        application.exit(exit_code)


QtCore.QTimer.singleShot(1200, _run)
