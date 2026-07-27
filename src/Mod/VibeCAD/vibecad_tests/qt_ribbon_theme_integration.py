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


def _group_commands(group):
    group_menu = group.findChild(
        QtWidgets.QToolButton, "VibeCADRibbonGroupMenu"
    )
    assert group_menu is not None and group_menu.menu() is not None
    return {
        str(action.property("VibeCADCommandId"))
        for action in group_menu.menu().actions()
        if action.property("VibeCADCommandId")
    }


def _page_group_labels(page):
    labels = []
    for index in range(page.layout().count()):
        widget = page.layout().itemAt(index).widget()
        if widget is None or not widget.property("ribbonGroup"):
            continue
        group_menu = widget.findChild(
            QtWidgets.QToolButton, "VibeCADRibbonGroupMenu"
        )
        assert group_menu is not None
        labels.append(group_menu.text())
    return labels


def _run():
    application = QtWidgets.QApplication.instance()
    main_window = Gui.getMainWindow()
    document = None
    secondary_document = None
    secondary_name = None
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
        document_tabs = main_window.findChild(
            QtWidgets.QTabBar, "VibeCADDocumentTabs"
        )
        source_document_tabs = main_window.findChild(
            QtWidgets.QTabBar, "mdiAreaTabBar"
        )
        theme_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADThemeToggle"
        )
        search_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADRibbonSearch"
        )
        new_document_button = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADRibbonNew"
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
        assert document_tabs is not None and document_tabs.isVisible()
        assert source_document_tabs is not None
        assert not source_document_tabs.isVisible()
        assert source_document_tabs.minimumHeight() == 0
        assert source_document_tabs.maximumHeight() == 0
        assert document_tabs.mapTo(root, QtCore.QPoint()).y() < tabs.mapTo(
            root, QtCore.QPoint()
        ).y()
        assert document_tabs.tabsClosable()
        assert document_tabs.isMovable()
        assert theme_button is not None
        assert search_button is not None and search_button.isVisible()
        assert new_document_button is not None and new_document_button.isVisible()
        assert search is not None and search.completer() is not None
        _assert_visible_inside(assistant_button, root)
        _assert_visible_inside(settings_button, root)
        _assert_visible_inside(document_tabs, root)
        _assert_visible_inside(search_button, root)
        _assert_visible_inside(new_document_button, root)
        assert (
            assistant_button.toolButtonStyle()
            == QtCore.Qt.ToolButtonIconOnly
        )
        assert (
            settings_button.toolButtonStyle()
            == QtCore.Qt.ToolButtonIconOnly
        )
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
            "Mesh",
            "Analyze",
            "Manufacture",
            "Drawing",
        ]
        assert [tabs.tabText(index) for index in range(tabs.count())] == (
            expected_tabs
        )
        tabs.setCurrentIndex(0)
        _process_events()
        structure_group = main_window.findChild(
            QtWidgets.QFrame, "VibeCADRibbonGroup_Structure"
        )
        assert structure_group is not None and structure_group.isVisible()
        structure_commands = {
            str(button.defaultAction().property("VibeCADCommandId"))
            for button in structure_group.findChildren(QtWidgets.QToolButton)
            if button.property("ribbonCommand")
            and button.defaultAction() is not None
        }
        assert "PartDesign_CompSketches" in structure_commands
        sketch_tools = next(
            button
            for button in structure_group.findChildren(QtWidgets.QToolButton)
            if button.property("ribbonCommand")
            and button.defaultAction() is not None
            and button.defaultAction().property("VibeCADCommandId")
            == "PartDesign_CompSketches"
        )
        assert sketch_tools.menu() is not None
        sketch_tool_labels = {
            action.text().replace("&", "") for action in sketch_tools.menu().actions()
        }
        assert {"New Sketch", "Attach Sketch", "Edit Sketch"}.issubset(
            sketch_tool_labels
        )
        model_page = main_window.findChild(
            QtWidgets.QWidget, "VibeCADRibbonPage"
        )
        model_group_labels = []
        for layout_index in range(model_page.layout().count()):
            widget = model_page.layout().itemAt(layout_index).widget()
            if widget is None or not widget.property("ribbonGroup"):
                continue
            group_menu = widget.findChild(
                QtWidgets.QToolButton, "VibeCADRibbonGroupMenu"
            )
            assert group_menu is not None
            model_group_labels.append(group_menu.text())
        assert model_group_labels == [
            "VIEW",
            "STRUCTURE",
            "SOLIDS",
            "FINISH",
            "TRANSFORM",
            "GEOMETRY",
            "MODIFY",
            "INSPECT",
            "FASTENERS",
        ]

        Gui.activateWorkbench("SketcherWorkbench")
        _process_events()
        assert Gui.activeWorkbench().name() == "SketcherWorkbench"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs + ["Sketch"]
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert all(tabs.isTabEnabled(index) for index in range(tabs.count()))
        sketch_setup_page = main_window.findChild(
            QtWidgets.QWidget, "VibeCADRibbonPage"
        )
        assert _page_group_labels(sketch_setup_page) == [
            "VIEW",
            "SKETCH",
            "INSPECT",
        ]

        tabs.setCurrentIndex(0)
        _process_events()
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Model"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs

        theme_selector = main_window.findChild(
            QtWidgets.QWidget, "ThemeSelectorWidget"
        )
        if theme_selector is not None:
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
        if theme_selector is not None:
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
            inspect_group = main_window.findChild(
                QtWidgets.QFrame, "VibeCADRibbonGroup_Inspect"
            )
            assert inspect_group is not None
            inspect_commands = _group_commands(inspect_group)
            assert inspect_commands == {
                "Std_Measure",
                "Std_MassProperties",
                "Inspection_VisualInspection",
                "Inspection_InspectElement",
                "Part_CheckGeometry",
            }, inspect_commands
            group_menus = main_window.findChildren(
                QtWidgets.QToolButton, "VibeCADRibbonGroupMenu"
            )
            assert group_menus
            page = main_window.findChild(
                QtWidgets.QWidget, "VibeCADRibbonPage"
            )
            page_groups = [
                group
                for group in page.findChildren(QtWidgets.QFrame)
                if group.property("ribbonGroup")
            ]
            visible_page_groups = [
                group for group in page_groups if group.isVisible()
            ]
            hidden_page_groups = [
                group for group in page_groups if not group.isVisible()
            ]
            for group in visible_page_groups:
                _assert_visible_inside(group, page)
            page_overflow = page.findChild(
                QtWidgets.QToolButton, "VibeCADRibbonPageMore"
            )
            assert page_overflow is not None
            assert page_overflow.isVisible() == bool(hidden_page_groups)
            if page_overflow.isVisible():
                _assert_visible_inside(page_overflow, page)
            for group_menu in group_menus:
                assert group_menu.menu() is not None
                assert group_menu.y() >= group_menu.parentWidget().height() // 2
                assert len(group_menu.text().split()) == 1
                assert not any(
                    term.lower() in group_menu.text().lower()
                    for term in (
                        "Part Design",
                        "PartDesign",
                        "TechDraw",
                        "Sketcher",
                        "Workbench",
                    )
                )
            if workbench == "MeshWorkbench":
                mesh_group_labels = [
                    item.widget()
                    .findChild(
                        QtWidgets.QToolButton, "VibeCADRibbonGroupMenu"
                    )
                    .text()
                    for item_index in range(page.layout().count())
                    if (item := page.layout().itemAt(item_index)).widget()
                    is not None
                    and item.widget().property("ribbonGroup")
                ]
                assert mesh_group_labels == [
                    "VIEW",
                    "TOOLS",
                    "CONVERT",
                    "MODIFY",
                    "BOOLEAN",
                    "CUT",
                    "SEGMENT",
                    "ANALYZE",
                    "INSPECT",
                ]
                tools_group = main_window.findChild(
                    QtWidgets.QFrame, "VibeCADRibbonGroup_Tools"
                )
                assert {
                    "Mesh_Import",
                    "Mesh_Export",
                    "Mesh_BuildRegularSolid",
                }.issubset(_group_commands(tools_group))
                convert_group = main_window.findChild(
                    QtWidgets.QFrame, "VibeCADRibbonGroup_Convert"
                )
                assert {
                    "Mesh_FromPartShape",
                    "Part_ShapeFromMesh",
                    "MeshPart_CurveOnMesh",
                }.issubset(_group_commands(convert_group))
                conversion_actions = {
                    str(action.property("VibeCADCommandId")): action
                    for action in convert_group.findChild(
                        QtWidgets.QToolButton,
                        "VibeCADRibbonGroupMenu",
                    )
                    .menu()
                    .actions()
                    if action.property("VibeCADCommandId")
                }
                for command_name in (
                    "Mesh_FromPartShape",
                    "Part_ShapeFromMesh",
                    "MeshPart_CurveOnMesh",
                ):
                    assert not conversion_actions[command_name].icon().isNull()
                mesh_screenshot_path = os.environ.get(
                    "VIBECAD_RIBBON_MESH_SCREENSHOT"
                )
                if mesh_screenshot_path:
                    screen = main_window.screen() or application.primaryScreen()
                    assert screen.grabWindow(main_window.winId()).save(
                        mesh_screenshot_path
                    )
            assert _visible_main_window_toolbars(main_window) == [ribbon]

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
        assert not search.isVisible()
        _assert_visible_inside(search_button, root)
        _assert_visible_inside(document_tabs, root)
        _assert_visible_inside(assistant_button, root)
        _assert_visible_inside(settings_button, root)
        assert not source_document_tabs.isVisible()
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
        tabs.setCurrentIndex(0)
        _process_events()
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        document = App.newDocument("VibeCADRibbonSmoke")
        _process_events()
        assert document_tabs.count() == source_document_tabs.count()
        assert any(
            "VibeCADRibbonSmoke" in document_tabs.tabText(index)
            for index in range(document_tabs.count())
        )
        assert not source_document_tabs.isVisible()
        mdi_area = main_window.findChild(QtWidgets.QMdiArea)
        assert mdi_area is not None
        assert source_document_tabs.height() == 0
        assert (
            mdi_area.contentsRect().bottom()
            - mdi_area.viewport().geometry().bottom()
            <= 1
        )
        secondary_document = App.newDocument("VibeCADRibbonSecond")
        secondary_name = secondary_document.Name
        secondary_label = secondary_document.Label
        _process_events()
        assert document_tabs.count() == source_document_tabs.count()
        assert any(
            secondary_label in document_tabs.tabText(index)
            for index in range(document_tabs.count())
        )
        primary_tab = next(
            index
            for index in range(document_tabs.count())
            if "VibeCADRibbonSmoke" in document_tabs.tabText(index)
        )
        document_tabs.setCurrentIndex(primary_tab)
        _process_events()
        assert App.ActiveDocument.Name == document.Name
        secondary_tab = next(
            index
            for index in range(document_tabs.count())
            if secondary_label in document_tabs.tabText(index)
        )

        def discard_secondary_document():
            dialog = application.activeModalWidget()
            if isinstance(dialog, QtWidgets.QMessageBox):
                discard = dialog.button(QtWidgets.QMessageBox.Discard)
                if discard is not None:
                    discard.click()
                else:
                    dialog.reject()

        QtCore.QTimer.singleShot(250, discard_secondary_document)
        document_tabs.tabCloseRequested.emit(secondary_tab)
        _process_events()
        assert secondary_name not in App.listDocuments()
        secondary_document = None
        _process_events()
        assert document_tabs.count() == source_document_tabs.count()
        assert not any(
            secondary_label in document_tabs.tabText(index)
            for index in range(document_tabs.count())
        )
        assert not source_document_tabs.isVisible()
        sketch = document.addObject("Sketcher::SketchObject", "RibbonSketch")
        document.recompute()
        Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        assert Gui.activeWorkbench().name() == "SketcherWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs + ["Sketch"]
        assert all(
            not tabs.isTabEnabled(index)
            for index in range(tabs.count())
            if tabs.tabText(index) != "Sketch"
        )
        assert tabs.isTabEnabled(tabs.currentIndex())
        sketch_page = main_window.findChild(
            QtWidgets.QWidget, "VibeCADRibbonPage"
        )
        assert _page_group_labels(sketch_page) == [
            "VIEW",
            "FINISH",
            "GEOMETRY",
            "CONSTRAINTS",
            "MODIFY",
            "B-SPLINE",
            "VISUAL",
        ]
        finish_group = main_window.findChild(
            QtWidgets.QFrame, "VibeCADRibbonGroup_Finish"
        )
        assert {
            "Sketcher_LeaveSketch",
            "Sketcher_CancelSketch",
        }.issubset(_group_commands(finish_group))

        Gui.runCommand("Sketcher_LeaveSketch")
        _process_events()
        assert Gui.activeDocument().getInEdit() is None
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Model"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs
        assert all(tabs.isTabEnabled(index) for index in range(tabs.count()))

        Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        Gui.runCommand("Sketcher_CancelSketch")
        _process_events()
        assert Gui.activeDocument().getInEdit() is None
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Model"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs
        assert all(tabs.isTabEnabled(index) for index in range(tabs.count()))

        Gui.activateWorkbench("SketcherWorkbench")
        _process_events()
        Gui.activeDocument().setEdit(sketch.Name)
        _process_events()
        assert Gui.activeWorkbench().name() == "SketcherWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert all(
            not tabs.isTabEnabled(index)
            for index in range(tabs.count())
            if tabs.tabText(index) != "Sketch"
        )
        Gui.runCommand("Sketcher_LeaveSketch")
        _process_events()
        assert Gui.activeDocument().getInEdit() is None
        assert Gui.activeWorkbench().name() == "SketcherWorkbench"
        assert tabs.tabText(tabs.currentIndex()) == "Sketch"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs + ["Sketch"]
        assert all(tabs.isTabEnabled(index) for index in range(tabs.count()))
        assert _page_group_labels(
            main_window.findChild(QtWidgets.QWidget, "VibeCADRibbonPage")
        ) == ["VIEW", "SKETCH", "INSPECT"]

        tabs.setCurrentIndex(0)
        _process_events()
        assert Gui.activeWorkbench().name() == "PartDesignWorkbench"
        assert [
            tabs.tabText(index) for index in range(tabs.count())
        ] == expected_tabs

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

        tabs.setCurrentIndex(0)
        _process_events()
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
        if secondary_document is not None:
            App.closeDocument(secondary_document.Name)
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
