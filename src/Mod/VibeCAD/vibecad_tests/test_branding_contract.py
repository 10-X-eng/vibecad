# SPDX-License-Identifier: LGPL-2.1-or-later

"""Guardrails for the user-facing VibeCAD product identity."""

from __future__ import annotations

import runpy
import sys
import unittest
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[4]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


class TestVibeCADNativePanelStartup(unittest.TestCase):
    """Exercise the real FreeCAD dock lifecycle when run by the GUI test runner."""

    WORKBENCHES = (
        "PartDesignWorkbench",
        "FastenersWorkbench",
        "SketcherWorkbench",
        "DraftWorkbench",
        "SurfaceWorkbench",
        "AssemblyWorkbench",
        "SpreadsheetWorkbench",
        "MaterialWorkbench",
        "MeshWorkbench",
        "MeshPartWorkbench",
        "PointsWorkbench",
        "ReverseEngineeringWorkbench",
        "InspectionWorkbench",
        "RobotWorkbench",
        "FemWorkbench",
        "CAMWorkbench",
        "TechDrawWorkbench",
    )

    def test_every_supported_workbench_creates_vibecad_panels(self) -> None:
        import FreeCAD as App

        if not App.GuiUp:
            self.skipTest("FreeCAD GUI mode is required")

        import FreeCADGui as Gui
        from PySide import QtCore, QtWidgets

        main_window = Gui.getMainWindow()
        self.assertIsNotNone(main_window)
        for workbench in self.WORKBENCHES:
            with self.subTest(workbench=workbench):
                Gui.activateWorkbench(workbench)
                self.assertEqual(Gui.activeWorkbench().name(), workbench)
                panel_module = sys.modules.get("VibeCADGui")
                self.assertIsNotNone(
                    panel_module,
                    f"{workbench} did not initialize the shared VibeCAD GUI module",
                )
                self.assertIsNotNone(
                    getattr(panel_module, "_registered_assistant_widget", None),
                    f"{workbench} did not register the assistant dock content",
                )
                assistant = main_window.findChild(
                    QtWidgets.QDockWidget, "VibeCADAssistantPanel"
                )
                editor = main_window.findChild(
                    QtWidgets.QDockWidget, "VibeCADScriptedModelPanel"
                )
                self.assertIsNotNone(assistant)
                self.assertIsNotNone(assistant.widget())
                self.assertIsNotNone(editor)
                self.assertIsNotNone(editor.widget())
                self.assertTrue(assistant.toggleViewAction().isVisible())
                self.assertTrue(editor.toggleViewAction().isVisible())
                self.assertFalse(assistant.isHidden())
                self.assertEqual(
                    main_window.dockWidgetArea(assistant),
                    QtCore.Qt.RightDockWidgetArea,
                )


class TestVibeCADResponsiveAssistant(unittest.TestCase):
    """Exercise the compact composer against the real Qt layout engine."""

    def test_crash_recovery_is_non_modal_and_draft_autosave_is_debounced(
        self,
    ) -> None:
        import FreeCAD as App

        if not App.GuiUp:
            self.skipTest("FreeCAD GUI mode is required")

        from PySide import QtCore, QtWidgets

        import VibeCADGui

        application = QtWidgets.QApplication.instance()
        self.assertIsNotNone(application)
        root = VibeCADGui._build_panel_widget()
        try:
            banner = root.findChild(QtWidgets.QFrame, "VibeSessionRecoveryBanner")
            label = root.findChild(QtWidgets.QLabel, "VibeSessionRecoveryText")
            restore = root.findChild(
                QtWidgets.QPushButton, "VibeSessionRecoveryRestore"
            )
            discard = root.findChild(
                QtWidgets.QPushButton, "VibeSessionRecoveryDiscard"
            )
            timer = root.findChild(
                QtCore.QTimer, "VibeSessionRecoveryDraftTimer"
            )

            self.assertIsNotNone(banner)
            self.assertFalse(banner.isVisible())
            self.assertIsNotNone(label)
            self.assertEqual(restore.text(), "Restore")
            self.assertEqual(discard.text(), "Discard")
            self.assertTrue(timer.isSingleShot())
            self.assertEqual(timer.interval(), 500)
        finally:
            root.close()
            root.deleteLater()
            application.processEvents()

    def test_ctrl_enter_sends_while_plain_enter_edits(self) -> None:
        import FreeCAD as App

        if not App.GuiUp:
            self.skipTest("FreeCAD GUI mode is required")

        from PySide import QtCore, QtGui, QtWidgets

        import VibeCADGui

        application = QtWidgets.QApplication.instance()
        self.assertIsNotNone(application)
        original_submit = VibeCADGui._run_prompt_from_panel
        submitted: list[str] = []
        root = None
        try:
            VibeCADGui._run_prompt_from_panel = lambda: submitted.append(
                prompt.toPlainText()
            )
            root = VibeCADGui._build_panel_widget()
            prompt = root.findChild(QtWidgets.QPlainTextEdit, "VibePrompt")
            self.assertIsNotNone(prompt)
            self.assertTrue(prompt.property("VibeSubmitFilterInstalled"))
            send_button = root.findChild(QtWidgets.QPushButton, "VibeSend")
            self.assertIsNotNone(send_button)
            self.assertIn("Ctrl+Enter", send_button.toolTip())
            prompt.setPlainText("Build the mounting bracket")

            ctrl_enter = QtGui.QKeyEvent(
                QtCore.QEvent.KeyPress,
                QtCore.Qt.Key_Return,
                QtCore.Qt.ControlModifier,
            )
            application.sendEvent(prompt, ctrl_enter)
            self.assertEqual(submitted, ["Build the mounting bracket"])
            self.assertEqual(prompt.toPlainText(), "Build the mounting bracket")

            plain_enter = QtGui.QKeyEvent(
                QtCore.QEvent.KeyPress,
                QtCore.Qt.Key_Return,
                QtCore.Qt.NoModifier,
            )
            application.sendEvent(prompt, plain_enter)
            self.assertEqual(submitted, ["Build the mounting bracket"])
            self.assertIn("\n", prompt.toPlainText())
        finally:
            VibeCADGui._run_prompt_from_panel = original_submit
            if root is not None:
                root.close()
                root.deleteLater()
                application.processEvents()

    def test_transcript_turn_starts_outside_prior_markdown_list(self) -> None:
        import FreeCAD as App

        if not App.GuiUp:
            self.skipTest("FreeCAD GUI mode is required")

        from PySide import QtWidgets

        import VibeCADGui

        output = QtWidgets.QTextBrowser()
        VibeCADGui._append_transcript_block(
            output,
            VibeCADGui._transcript_block_html(
                "VibeCAD:\nFinished:\n\n- Added slots\n- Added fillets"
            ),
        )
        VibeCADGui._append_transcript_block(
            output,
            VibeCADGui._transcript_block_html("User:\nMake the slots longer"),
        )

        self.assertIn("Added fillets\n\nUser:", output.toPlainText())
        rendered = output.document().toHtml()
        user_position = rendered.index("User:</span>")
        self.assertGreater(
            rendered.rfind("</ul>", 0, user_position),
            rendered.rfind("<ul", 0, user_position),
        )

    def test_narrow_composer_uses_distinct_icons_without_words(self) -> None:
        import FreeCAD as App

        if not App.GuiUp:
            self.skipTest("FreeCAD GUI mode is required")

        from PySide import QtWidgets

        import VibeCADGui

        application = QtWidgets.QApplication.instance()
        self.assertIsNotNone(application)
        root = VibeCADGui._build_panel_widget()
        try:
            self.assertIsNone(root.findChild(QtWidgets.QLabel, "VibeProviderIdentity"))
            root.resize(414, 800)
            root.show()
            application.processEvents()
            composer = root.findChild(
                QtWidgets.QWidget,
                "VibeComposerButtons",
            )
            self.assertIsNotNone(composer)
            self.assertIsNone(
                root.findChild(QtWidgets.QComboBox, "VibeInteractionMode")
            )
            self.assertLess(
                composer.width(),
                VibeCADGui._COMPOSER_ICON_ONLY_BREAKPOINT,
            )
            buttons = {
                name: root.findChild(QtWidgets.QPushButton, name)
                for name in (
                    "VibeAttachView",
                    "VibeAttachImage",
                    "VibeSend",
                    "VibeStop",
                )
            }
            self.assertTrue(all(button is not None for button in buttons.values()))
            for button in buttons.values():
                self.assertEqual(button.text(), "")
                self.assertTrue(button.property("VibeCompactMode"))
                self.assertFalse(button.icon().isNull())
                self.assertTrue(button.toolTip())
                self.assertTrue(button.accessibleName())
            self.assertNotEqual(
                buttons["VibeAttachView"].icon().cacheKey(),
                buttons["VibeAttachImage"].icon().cacheKey(),
            )

            # Removing the mode selector leaves enough room for all four
            # actions at the width from the reported dock screenshot.
            root.resize(520, 800)
            application.processEvents()
            self.assertGreaterEqual(
                composer.width(),
                int(composer.property("VibeFullLabelRequiredWidth")),
            )
            self.assertEqual(buttons["VibeAttachView"].text(), "Attach View")
            self.assertEqual(buttons["VibeAttachImage"].text(), "Attach Image")
            self.assertEqual(buttons["VibeSend"].text(), "Send")
            self.assertEqual(buttons["VibeStop"].text(), "Stop")

            root.resize(760, 800)
            application.processEvents()
            full_label_width = int(composer.property("VibeFullLabelRequiredWidth"))
            self.assertGreaterEqual(
                composer.width(),
                full_label_width,
            )
            self.assertEqual(buttons["VibeAttachView"].text(), "Attach View")
            self.assertEqual(buttons["VibeAttachImage"].text(), "Attach Image")
            self.assertEqual(buttons["VibeSend"].text(), "Send")
            self.assertEqual(buttons["VibeStop"].text(), "Stop")

            # A standalone top-level QWidget will not resize below its own
            # current minimumSizeHint. Constrain the composer itself to
            # exercise the exact responsive boundary used inside a dock.
            composer.setFixedWidth(full_label_width - 1)
            VibeCADGui._update_composer_button_presentation(
                composer,
                busy=False,
            )
            application.processEvents()
            self.assertLess(composer.width(), full_label_width)
            for button in buttons.values():
                self.assertEqual(button.text(), "")
                self.assertTrue(button.property("VibeCompactMode"))
            self.assertEqual(
                len({button.width() for button in buttons.values()}),
                1,
            )

            VibeCADGui._update_composer_button_presentation(
                composer,
                busy=True,
            )
            self.assertEqual(buttons["VibeSend"].text(), "")
            self.assertEqual(buttons["VibeSend"].accessibleName(), "Steer")

            composer.setMinimumWidth(0)
            composer.setMaximumWidth(16777215)
            root.resize(760, 800)
            application.processEvents()
            VibeCADGui._update_composer_button_presentation(
                composer,
                busy=True,
            )
            self.assertEqual(buttons["VibeSend"].text(), "Steer")
        finally:
            root.close()
            root.deleteLater()
            application.processEvents()


class TestVibeCADRibbonChrome(unittest.TestCase):
    """Validate the live task-oriented chrome, not only its source contract."""

    def test_ribbon_replaces_workbench_chrome_without_removing_native_actions(
        self,
    ) -> None:
        import FreeCAD as App

        if not App.GuiUp:
            self.skipTest("FreeCAD GUI mode is required")

        import FreeCADGui as Gui
        from PySide import QtCore, QtGui, QtWidgets

        application = QtWidgets.QApplication.instance()
        main_window = Gui.getMainWindow()
        self.assertIsNotNone(application)
        self.assertIsNotNone(main_window)
        application.processEvents()

        ribbon = main_window.findChild(QtWidgets.QWidget, "VibeCADRibbon")
        toolbar = main_window.findChild(QtWidgets.QToolBar, "VibeCADRibbonToolBar")
        tabs = main_window.findChild(QtWidgets.QTabBar, "VibeCADRibbonTabs")
        search = main_window.findChild(QtWidgets.QLineEdit, "VibeCADCommandSearch")
        app_button = main_window.findChild(QtWidgets.QToolButton, "VibeCADAppButton")
        theme_toggle = main_window.findChild(
            QtWidgets.QToolButton, "VibeCADThemeToggle"
        )
        full_menu_action = main_window.findChild(
            QtGui.QAction, "VibeCADShowFullMenuBarAction"
        )

        self.assertIsNotNone(ribbon)
        self.assertIsNotNone(toolbar)
        self.assertIsNotNone(tabs)
        self.assertIsNotNone(search)
        self.assertIsNotNone(app_button)
        self.assertIsNotNone(theme_toggle)
        self.assertIsNotNone(full_menu_action)
        self.assertTrue(toolbar.isVisible())
        self.assertFalse(toolbar.toggleViewAction().isVisible())
        self.assertFalse(main_window.menuBar().isVisible())

        chrome_preferences = App.ParamGet(
            "User parameter:BaseApp/Preferences/VibeCAD/Chrome"
        )
        self.assertFalse(full_menu_action.isChecked())
        full_menu_action.setChecked(True)
        application.processEvents()
        self.assertTrue(main_window.menuBar().isVisible())
        self.assertTrue(chrome_preferences.GetBool("ShowFullMenuBar", False))
        full_menu_action.setChecked(False)
        application.processEvents()
        self.assertFalse(main_window.menuBar().isVisible())
        self.assertFalse(chrome_preferences.GetBool("ShowFullMenuBar", True))

        main_window.menuBar().show()
        application.processEvents()
        self.assertFalse(main_window.menuBar().isVisible())
        Gui.activateWorkbench("PartDesignWorkbench")
        application.processEvents()
        self.assertFalse(main_window.menuBar().isVisible())
        self.assertFalse(full_menu_action.isChecked())
        self.assertEqual(
            [tabs.tabText(index) for index in range(tabs.count())],
            [
                "Model",
                "Assemble",
                "Mesh",
                "Analyze",
                "Manufacture",
                "Drawing",
                "Parameters",
            ],
        )

        for candidate in main_window.findChildren(QtWidgets.QToolBar):
            if candidate is toolbar:
                continue
            if (
                main_window.toolBarArea(candidate)
                != QtCore.Qt.ToolBarArea.NoToolBarArea
            ):
                self.assertFalse(
                    candidate.isVisible(),
                    f"native toolbar remained visible: {candidate.objectName()}",
                )


def test_windows_installer_uses_vibecad_identity() -> None:
    installer = _source("package/WindowsInstaller/FreeCAD-installer.nsi")
    declarations = _source("package/WindowsInstaller/include/declarations.nsh")

    assert '!define APP_NAME "VibeCAD"' in installer
    assert '!define APP_RUN "bin\\VibeCAD.exe"' in declarations
    assert '!define BIN_FREECAD "VibeCAD.exe"' in declarations
    assert '!define SETUP_ICON "icons\\VibeCAD.ico"' in declarations
    assert '!define APP_NAME "FreeCAD"' not in installer + declarations


def test_runtime_branding_resources_are_registered() -> None:
    main_gui = _source("src/Main/MainGui.cpp")
    resources = _source("src/Gui/Icons/resource.qrc")

    assert 'Config()["ExeName"] = "VibeCAD"' in main_gui
    assert 'Config()["AppIcon"] = "vibecad"' in main_gui
    assert 'Config()["SplashScreen"] = "vibecadsplash"' in main_gui
    for asset in (
        "vibecad.svg",
        "vibecadabout.png",
        "vibecadaboutdev.png",
        "vibecadsplash.png",
        "vibecadsplash_2x.png",
    ):
        assert f"<file>{asset}</file>" in resources
        assert (ROOT / "src" / "Gui" / "Icons" / asset).is_file()


def test_every_runtime_entry_point_uses_only_the_vibecad_config_namespace() -> None:
    for relative_path in (
        "src/Main/MainGui.cpp",
        "src/Main/MainCmd.cpp",
        "src/Main/MainPy.cpp",
    ):
        source = _source(relative_path)
        assert 'Config()["ExeName"] = "VibeCAD"' in source
        assert 'Config()["ExeVendor"] = "VibeCAD"' in source
        assert 'Config()["AppDataSkipVendor"] = "true"' in source
        assert 'Config()["ExeName"] = "FreeCAD"' not in source
        assert 'Config()["ExeVendor"] = "FreeCAD"' not in source


def test_fresh_gui_profiles_initialize_the_native_tree_before_main_window_construction() -> (
    None
):
    main_gui = _source("src/Main/MainGui.cpp")

    defaults = main_gui.split("static void initializeVibeCADDockDefaults()", 1)[1]
    defaults = defaults.split("int main(", 1)[0]
    assert 'GetGroup("TreeView")' in defaults
    assert 'GetGroup("PropertyView")' in defaults
    assert 'GetGroup("ComboView")' in defaults
    assert defaults.count("hasBoolParameter(") == 3
    assert 'treeView->SetBool("Enabled", true)' in defaults
    assert 'propertyView->SetBool("Enabled", false)' in defaults
    assert 'comboView->SetBool("Enabled", false)' in defaults

    startup = main_gui.split("App::Application::init(argc", 1)[1]
    assert startup.index("initializeVibeCADDockDefaults();") < startup.index(
        "Gui::Application::initApplication();"
    )


def test_vibecad_docks_are_registered_before_native_window_restore() -> None:
    gui = _source("src/Mod/VibeCAD/VibeCADGui.py")
    init_gui = _source("src/Mod/VibeCAD/InitGui.py")
    freecad_gui_init = _source("src/Gui/FreeCADGuiInit.py")
    startup = _source("src/Gui/StartupProcess.cpp")

    assert "QtCore.QTimer.singleShot(0, apply_context_debug_preferences)" not in gui
    assert (
        "QtCore.QTimer.singleShot(0, ensure_scripted_model_editor_registered)"
        not in gui
    )
    assert "ensure_scripted_model_editor_registered()" in gui
    assert "QtCore.QTimer.singleShot(0, _register_startup_assistant)" not in init_gui
    assert "VibeCADGui.ensure_commands_registered()" in init_gui
    shared_initialization = gui.split("def ensure_commands_registered()", 1)[1]
    assert shared_initialization.index("register_startup_assistant()") < (
        shared_initialization.index("ensure_scripted_model_editor_registered()")
    )
    assert freecad_gui_init.index("InitApplications()") < freecad_gui_init.index(
        'Gui.activateWorkbench("NoneWorkbench")'
    )
    execute = startup.split("void StartupPostProcess::execute()", 1)[1].split("}", 1)[0]
    assert execute.index("showMainWindow();") < execute.index("activateWorkbench();")
    show = startup.split("void StartupPostProcess::showMainWindow()", 1)[1].split(
        "void StartupPostProcess::activateWorkbench()", 1
    )[0]
    assert "Application::runInitGuiScript();" in show
    activate = startup.split("void StartupPostProcess::activateWorkbench()", 1)[1]
    assert activate.index("guiApp.activateWorkbench(start.c_str());") < activate.index(
        "mainWindow->loadWindowSettings();"
    )


def test_vibecad_docks_use_native_standard_workbench_declarations() -> None:
    workbench = _source("src/Gui/Workbench.cpp")
    setup = workbench.split(
        "DockWindowItems* StdWorkbench::setupDockWindows() const", 1
    )[1]
    setup = setup.split("return root;", 1)[0]

    assert 'root->addDockWidget("Std_TaskView"' in setup
    assert '"VibeCADAssistantPanel"' in setup
    assert '"VibeCADScriptedModelPanel"' in setup
    assert '"VibeCADContextDebugPanel"' in setup
    assert "Gui::DockWindowOption::VisibleTabbed" in setup
    assert "Gui::DockWindowOption::HiddenTabbed" in setup
    context_debug_options = setup.rsplit('"VibeCADContextDebugPanel"', 1)[1].split(
        ");", 1
    )[0]
    assert "Gui::DockWindowOption::HiddenTabbed" in context_debug_options
    assert "Gui::DockWindowOption::VisibleTabbed" not in context_debug_options

    for relative_path in ROOT.glob("src/Mod/*/InitGui.py"):
        if relative_path.parent.name == "VibeCAD":
            continue
        source = relative_path.read_text(encoding="utf-8")
        assert "register_ai_commands_for_workbench" not in source
        assert "import VibeCADGui" not in source


def test_vibecad_connects_one_global_workbench_activation_signal(monkeypatch) -> None:
    import VibeCADGui as panel

    connected: list[object] = []
    main_window = SimpleNamespace(
        workbenchActivated=SimpleNamespace(connect=connected.append)
    )
    monkeypatch.setattr(panel, "_workbench_activation_connected", False)
    monkeypatch.setattr(panel.Gui, "getMainWindow", lambda: main_window, raising=False)

    panel._connect_workbench_activation()
    panel._connect_workbench_activation()

    assert connected == [panel._on_workbench_activated]


def test_vibecad_docks_do_not_run_a_manual_layout_restore(monkeypatch) -> None:
    import VibeCADGui as panel

    class _ToggleAction:
        def __init__(self) -> None:
            self.visible = False

        def setVisible(self, visible: bool) -> None:
            self.visible = bool(visible)

    class _Dock:
        def __init__(self) -> None:
            self.toggle_action = _ToggleAction()

        def toggleViewAction(self) -> _ToggleAction:
            return self.toggle_action

    class _MainWindow:
        def __init__(self) -> None:
            self.added: list[tuple[object, str, str]] = []

        def addDockWindow(self, widget: object, name: str, area: str) -> _Dock:
            self.added.append((widget, name, area))
            return _Dock()

    main_window = _MainWindow()
    monkeypatch.setattr(panel.Gui, "getMainWindow", lambda: main_window, raising=False)

    assistant_widget = object()
    context_widget = object()
    assistant = panel._register_native_dock(assistant_widget)
    context_dock = panel._register_context_debug_dock(context_widget)

    assert main_window.added == [
        (assistant_widget, panel.DOCK_NAME, "right"),
        (context_widget, panel.CONTEXT_DEBUG_DOCK_NAME, "bottom"),
    ]
    assert assistant.toggle_action.visible is True
    assert context_dock.toggle_action.visible is True


def test_startup_docks_register_content_without_showing_windows(monkeypatch) -> None:
    import VibeCADGui as panel

    class _Widget:
        def __init__(self) -> None:
            self.minimum_width = 0

        def setMinimumWidth(self, width: int) -> None:
            self.minimum_width = int(width)

    class _MainWindow:
        def __init__(self) -> None:
            self.registered: list[tuple[object, str]] = []

        def registerDockWindow(self, widget: object, name: str) -> None:
            self.registered.append((widget, name))

    main_window = _MainWindow()
    widget = _Widget()
    monkeypatch.setattr(panel, "_registered_assistant_widget", None)
    monkeypatch.setattr(panel, "_find_dock", lambda: None)
    monkeypatch.setattr(panel, "_build_panel_widget", lambda: widget)
    monkeypatch.setattr(panel.Gui, "getMainWindow", lambda: main_window, raising=False)

    assert panel.register_startup_assistant() is widget
    assert main_window.registered == [(widget, panel.DOCK_NAME)]
    assert widget.minimum_width == 300


def test_hidden_context_debug_dock_performs_no_gui_thread_polling(monkeypatch) -> None:
    import VibeCADGui as panel

    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtCore=SimpleNamespace(QTimer=object)),
    )

    class _Timer:
        def __init__(self) -> None:
            self.active = True
            self.stop_calls = 0

        def stop(self) -> None:
            self.active = False
            self.stop_calls += 1

        def start(self) -> None:
            self.active = True

        def isActive(self) -> bool:
            return self.active

    class _Dock:
        def __init__(self, timer: _Timer) -> None:
            self.timer = timer

        def findChild(self, _kind: object, name: str) -> _Timer | None:
            return self.timer if name == "VibeContextDebugPollTimer" else None

    timer = _Timer()
    refreshed: list[object] = []
    monkeypatch.setattr(
        panel,
        "_context_debug_settings",
        lambda: SimpleNamespace(context_debug_enabled=True),
    )
    monkeypatch.setattr(
        panel,
        "_refresh_context_debug_viewer",
        lambda dock: refreshed.append(dock),
    )

    panel._sync_context_debug_polling(_Dock(timer), False)

    assert timer.stop_calls == 1
    assert refreshed == []


def test_enabling_context_debug_does_not_open_the_viewer(monkeypatch) -> None:
    import VibeCADGui as panel

    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtCore=SimpleNamespace(QTimer=object)),
    )
    shown: list[bool] = []
    monkeypatch.setattr(
        panel,
        "_context_debug_settings",
        lambda: SimpleNamespace(context_debug_enabled=True),
    )
    monkeypatch.setattr(panel, "_find_context_debug_dock", lambda: None)
    monkeypatch.setattr(panel, "show_context_debugger", lambda: shown.append(True))

    panel.apply_context_debug_preferences()

    assert shown == []


def test_mcp_settings_have_a_dedicated_vibecad_preference_page() -> None:
    preferences = _source("src/Mod/VibeCAD/VibeCADPreferences.py")
    gui = _source("src/Mod/VibeCAD/VibeCADGui.py")
    main_page = preferences.split("class VibeCADPreferencesPage:", 1)[1].split(
        "class VibeCADMCPPreferencesPage:", 1
    )[0]
    mcp_page = preferences.split("class VibeCADMCPPreferencesPage:", 1)[1].split(
        "class VibeCADPromptStartersPreferencesPage:", 1
    )[0]

    assert 'setWindowTitle("MCP")' in mcp_page
    assert 'setObjectName("VibeCADPrefMCPEnabled")' in mcp_page
    assert 'setObjectName("VibeCADPrefMCPEnabled")' not in main_page
    assert "mcp_enabled=persisted.mcp_enabled" in main_page
    registration = gui.split("def ensure_preferences_registered()", 1)[1].split(
        "def ensure_commands_registered()", 1
    )[0]
    assert "VibeCADPreferences.VibeCADMCPPreferencesPage" in registration


def test_python_workbench_dock_declarations_reach_dock_window_manager() -> None:
    source = _source("src/Gui/WorkbenchManipulatorPython.cpp")

    handler = source.split(
        "void WorkbenchManipulatorPython::tryModifyDockWindows(\n"
        "    const Py::Dict& dict,",
        1,
    )[1]
    assert 'const std::string add("add");' in handler
    assert 'QStringLiteral("left")' in handler
    assert 'QStringLiteral("right")' in handler
    assert 'QStringLiteral("top")' in handler
    assert 'QStringLiteral("bottom")' in handler
    assert "DockWindowOption::VisibleTabbed" in handler
    assert "DockWindowOption::HiddenTabbed" in handler
    assert "dockWindow->addDockWidget" in handler


def test_python_dock_registration_defers_creation_to_workbench_setup() -> None:
    binding = _source("src/Gui/MainWindowPy.cpp")
    manager = _source("src/Gui/DockWindowManager.cpp")

    registration = binding.split("Py::Object MainWindowPy::registerDockWindow", 1)[
        1
    ].split("Py::Object MainWindowPy::removeDockWindow", 1)[0]
    assert "dwm->registerDockWindow(name, widget)" in registration
    assert "dwm->addDockWindow" not in registration
    assert "dw->show()" not in registration
    assert "widget->show()" not in registration
    assert "addDockWindow(dockName.constData(), jt.value(), it.pos)" in manager


def test_native_late_docks_consume_saved_entry_before_default_placement() -> None:
    source = _source("src/Gui/DockWindowManager.cpp")
    addition = source.split("QDockWidget* DockWindowManager::addDockWindow", 1)[
        1
    ].split("QWidget* DockWindowManager::getDockWindow", 1)[0]

    assert addition.index("dw->setObjectName") < addition.index(
        "mw->restoreDockWidget(dw)"
    )
    assert addition.index("dw->toggleViewAction()->setData") < addition.index(
        "mw->restoreDockWidget(dw)"
    )
    assert addition.index("mw->restoreDockWidget(dw)") < addition.index(
        "mw->addDockWidget(pos, dw)"
    )


def test_overlaid_dock_visibility_has_one_requested_state_owner() -> None:
    source = _source("src/Gui/DockWindowManager.cpp")
    main_window = _source("src/Gui/MainWindow.cpp")
    overlay = _source("src/Gui/OverlayManager.cpp")
    overlay_widgets = _source("src/Gui/OverlayWidgets.cpp")
    tree = _source("src/Gui/Tree.cpp")
    addition = source.split("QDockWidget* DockWindowManager::addDockWindow", 1)[
        1
    ].split("QWidget* DockWindowManager::getDockWindow", 1)[0]
    persistence = addition.split(
        "connect(dw->toggleViewAction(), &QAction::triggered", 1
    )[1]
    save = source.split("void DockWindowManager::saveState()", 1)[1].split(
        "void DockWindowManager::loadState()", 1
    )[0]

    # Effective QWidget visibility and overlay splitter presentation are not
    # user intent. The dock manager owns the requested state and sends the
    # exact checked value through one presentation API.
    assert "[this, dw](bool checked)" in persistence
    assert "d->_requestedVisibility.insert(dockName, checked)" in persistence
    assert "SetBool(encodedName.constData(), checked)" in persistence
    assert "applyRequestedVisibility(dw, checked)" in persistence
    assert "requested.value()" in save
    assert "isHidden()" not in save
    assert "isChecked()" not in save
    assert "dw->isVisible()" not in save
    assert "setDockWidgetVisible(dock, visible)" in source
    assert "applyRequestedPresentation(OverlayTabWidget* tabWidget)" in overlay
    assert "manager->isVisibilityRequested(dock)" in overlay
    assert "_persistentDocks" not in overlay
    assert "setDockWidgetPersistent" not in overlay

    presentation = overlay.split(
        "bool setDockWidgetVisible(QDockWidget* dock, bool visible)", 1
    )[1].split("bool isDockRequestedVisible", 1)[0]
    assert "sizes[index] = 0" in presentation
    assert "dock->hide()" in presentation
    assert "dock->toggleViewAction()->setChecked(false)" in presentation
    assert "dock->show()" in presentation
    assert "dock->toggleViewAction()->setChecked(true)" in presentation
    # OverlayManager keeps its public direct-initialization behavior for
    # unmanaged docks, but must defer managed docks to the requested-state
    # owner above.
    direct_initialization = overlay.split("void OverlayManager::initDockWidget", 1)[
        1
    ].split("bool OverlayManager::setDockWidgetVisible", 1)[0]
    direct_toggle = overlay.split("void OverlayManager::onToggleDockWidget", 1)[
        1
    ].split("void OverlayManager::onDockVisibleChange", 1)[0]
    assert "dw->toggleViewAction()" in direct_initialization
    assert "&OverlayManager::onToggleDockWidget" in direct_initialization
    assert "DockWindowManager::instance()->managesDockWidget(dock)" in direct_toggle
    assert "splitter->widget(i)->setVisible(presented)" in overlay_widgets
    assert "if (count() && !hasPresentedWidget)" in overlay_widgets
    assert overlay_widgets.index(
        "OverlayManager::instance()->applyRequestedPresentation(this)"
    ) < overlay_widgets.index("if (count() && !hasPresentedWidget)")
    overlay_persistence = overlay_widgets.split(
        "void OverlayTabWidget::saveTabs()", 1
    )[1].split("void OverlayTabWidget::onTabMoved", 1)[0]
    assert "applyRequestedPresentation(this)" in overlay_persistence
    assert "isDockRequestedVisible(dock)" in overlay_persistence
    assert "os2 << persistedSize" in overlay_persistence

    # The Model browser is permanent viewport chrome, not a dock presentation.
    assert 'QStringLiteral("VibeCADViewportCanvas")' in main_window
    assert 'QStringLiteral("VibeCADModelBrowserHost")' in main_window
    assert 'QStringLiteral("VibeCADModelBrowserResizeHandle")' in main_window
    assert "modelBrowserMinimumWidth = 288" in main_window
    assert 'modelBrowserWidthPreference = "ModelBrowserWidth"' in main_window
    assert "browserPreferences->GetInt(" in main_window
    assert "browserPreferences->SetInt(modelBrowserWidthPreference, browserWidth)" in main_window
    assert "workspaceLayout->addWidget(d->viewportCanvas, 1)" in main_window
    assert "setModelBrowserVisible" in main_window
    assert 'view->property("vibecadUsesModelBrowser")' in main_window
    assert "browserHost->show()" in main_window
    permanent = source.split("void presentPermanentModelBrowser", 1)[1].split(
        "}  // namespace", 1
    )[0]
    assert "getMainWindow()->removeDockWidget(dock)" in permanent
    assert "dock->setParent(host)" in permanent
    assert "dock->setFeatures(QDockWidget::NoDockWidgetFeatures)" in permanent
    assert "toggle->setEnabled(false)" in permanent
    assert "toggle->setVisible(false)" in permanent
    assert "host->show()" not in permanent
    assert "dock->show()" in permanent
    assert "VibeCADModelBrowserHost" in tree
    assert "widget.setUpdatesEnabled(false)" in tree


def test_corrupt_duplicate_dock_state_is_repaired_after_startup() -> None:
    source = _source("src/Gui/MainWindow.cpp")
    load = source.split("void MainWindow::loadWindowSettings()", 1)[1].split(
        "bool MainWindow::isRestoringWindowState()", 1
    )[0]

    assert "QTimer::singleShot(0" in load
    assert (
        "DockWindowManager::repairDuplicateDockState(this, duplicateRepairState)"
        in load
    )
    assert 'SetASCII("MainWindowState", saveState().toBase64().constData())' in load


def test_windows_bundle_creates_branded_executable() -> None:
    bundle_script = _source("package/rattler-build/windows/create_bundle.sh")
    main_cmake = _source("src/Main/CMakeLists.txt")
    launcher_source = _source("src/Main/VibeCADPortableLauncher.cpp")

    assert '"${copy_dir}/bin/VibeCAD.exe"' in bundle_script
    assert '[[ ! -x "${copy_dir}/bin/VibeCAD.exe" ]]' in bundle_script
    assert '"${copy_dir}/VibeCAD.exe"' in bundle_script
    assert "VibeCADPortableLauncher.exe" in bundle_script
    assert "VibeCADCmdPortableLauncher.exe" in bundle_script
    assert '"$SIGN_DIR/FreeCADCmd.exe" --safe-mode --version' in bundle_script
    assert "shimgen.exe" not in bundle_script
    assert "resolve_release_artifact_name.py" in bundle_script
    assert 'version_name="${artifact_base}-Windows-$(uname -m)"' in bundle_script
    assert 'version_name="VibeCAD_${BUILD_TAG}' not in bundle_script
    assert 'rm -rf -- "${copy_dir}" "${version_name}" ".nsis_tmp"' in bundle_script
    assert "add_executable(VibeCADPortableLauncher WIN32" in main_cmake
    assert "add_executable(VibeCADCmdPortableLauncher" in main_cmake
    assert 'L"bin\\\\VibeCAD.exe"' in launcher_source
    assert "CreateProcessW" in launcher_source


def test_every_release_bundle_keeps_and_executes_the_geometry_worker() -> None:
    windows = _source("package/rattler-build/windows/create_bundle.sh")
    linux = _source("package/rattler-build/linux/create_bundle.sh")
    macos = _source("package/rattler-build/osx/create_bundle.sh")
    local = _source("package/rattler-build/scripts/build_vibecad_local_release.sh")
    geometry = _source("src/Mod/VibeCAD/VibeCADGeometry.py")

    assert (
        'copy_matching_files "${conda_env}/Library/bin" '
        '"VibeCADGeometryWorker.exe" "${copy_dir}/bin"'
    ) in windows
    assert 'cp ${conda_env}/bin_tmp/VibeCADGeometryWorker ${conda_env}/bin/' in linux
    assert (
        'cp "${conda_env}/bin_tmp/VibeCADGeometryWorker" "${conda_env}/bin/"'
    ) in macos
    for release_script in (windows, linux, macos, local):
        assert "from VibeCADGeometry import runtime_execution_smoke" in release_script
    assert "def runtime_execution_smoke()" in geometry


def test_release_packages_share_canonical_artifact_basename() -> None:
    linux_bundle = _source("package/rattler-build/linux/create_bundle.sh")
    macos_bundle = _source("package/rattler-build/osx/create_bundle.sh")
    windows_bundle = _source("package/rattler-build/windows/create_bundle.sh")
    release_workflow = _source(".github/workflows/vibecad-release.yml")
    macos_workflow = _source(".github/workflows/vibecad-macos.yml")
    deb_builder = _source("package/linux/build_deb_from_appdir.sh")

    for bundle_script, platform_suffix in (
        (linux_bundle, 'version_name="${artifact_base}-Linux-$(uname -m)"'),
        (
            macos_bundle,
            'version_name="${artifact_base}-macOS${deploy_target%%.*}-$(uname -m)"',
        ),
        (windows_bundle, 'version_name="${artifact_base}-Windows-$(uname -m)"'),
    ):
        assert "resolve_release_artifact_name.py" in bundle_script
        assert platform_suffix in bundle_script
        assert 'version_name="VibeCAD_${BUILD_TAG}' not in bundle_script

    assert '--version "${release_version}"' in release_workflow
    assert '--artifact-basename "${artifact_basename}"' in release_workflow
    assert (
        'deb_path="$output_dir/${artifact_basename}-Linux-${deb_arch}.deb"'
        in deb_builder
    )
    assert 'sha256sum "$deb_filename" > "${deb_filename}-SHA256.txt"' in deb_builder
    assert 'sha256sum "$deb_path" > "${deb_path}-SHA256.txt"' not in deb_builder
    assert "package/rattler-build/osx/VibeCAD-*.dmg" in macos_workflow
    assert "package/rattler-build/osx/VibeCAD_*.dmg" not in macos_workflow


def test_update_ui_uses_the_vibecad_bar_and_keeps_preferences_for_settings() -> None:
    update_gui = _source("src/Mod/VibeCAD/VibeCADUpdateGui.py")
    preferences = update_gui.split("class VibeCADUpdatePreferencesPage", 1)[1].split(
        "class UpdateCenterDialog", 1
    )[0]

    assert '_COMMAND_NAME = "VibeCAD_CheckForUpdates"' in update_gui
    assert '_LEGACY_COMMAND_NAME = "VibeCAD_UpdateCenter"' in update_gui
    assert '_ICON = "view-refresh.svg"' in update_gui
    assert '"MenuText": "Check for Updates"' in update_gui
    assert 'action.setProperty("VibeCADCheckForUpdates", True)' in update_gui
    assert "def _find_help_menu(main_window: Any)" in update_gui
    assert "for menu_action in menu_bar.actions():" in update_gui
    assert "main_window.workbenchActivated.connect(_schedule_help_menu_action)" in update_gui
    assert "for delay in (0, 250, 1000, 5000):" in update_gui
    assert "Open Update Center" not in update_gui
    assert "QPushButton" not in preferences
    assert 'self.setWindowTitle("VibeCAD Updates")' in update_gui
    assert 'buttons.addButton("Download update"' in update_gui
    assert "show_check_for_updates(check_now=False)" in update_gui


def test_assistant_panel_uses_vibecad_product_name() -> None:
    panel_source = _source("src/Mod/VibeCAD/VibeCADGui.py")
    core_source = _source("src/Mod/VibeCAD/VibeCADCore.py")
    product_copy = panel_source + core_source

    for stale_copy in (
        "Create and save a FreeCAD document to enable VibeCAD.",
        "Save this FreeCAD document to enable VibeCAD.",
        "Looking at the current FreeCAD document...",
        "Summarize the current FreeCAD context.",
    ):
        assert stale_copy not in product_copy
    assert "Create and save a VibeCAD document to enable VibeCAD." in core_source
    assert "Looking at the current VibeCAD document..." in panel_source


def test_vibecad_ships_exactly_two_constrained_appearance_profiles() -> None:
    themes = ROOT / "src/Gui/Themes"
    configs = sorted(themes.glob("*.cfg"))
    assert [path.name for path in configs] == ["Dark.cfg", "Light.cfg"]

    schemas: dict[str, set[tuple[str, str, str]]] = {}
    forbidden_keys = {
        "AutoloadModule",
        "AutoRemoveRedundants",
        "BackgroundAutoloadModules",
        "Disabled",
        "EnablePreselection",
        "EnableSelection",
        "LastModule",
        "LockToolBars",
        "MainWindowState",
        "Ordered",
        "RefineModel",
        "SectionView",
        "ShowOnStartup",
        "ToolbarIconSize",
        "UseVBO",
    }
    required_main_window_keys = {
        "AppearanceMode",
        "Theme",
        "QtStyle",
        "StyleSheet",
        "OverlayActiveStyleSheet",
        "ThemeStyleParametersFile",
    }

    for config in configs:
        root = ET.parse(config).getroot()
        schema: set[tuple[str, str, str]] = set()

        def collect(group: ET.Element, path: tuple[str, ...]) -> None:
            name = group.get("Name")
            next_path = path + ((name,) if name else ())
            for child in group:
                if child.tag == "FCParamGroup":
                    collect(child, next_path)
                else:
                    key = child.get("Name", "")
                    assert key not in forbidden_keys
                    schema.add(("/".join(next_path), child.tag, key))

        for group in root.findall("FCParamGroup"):
            collect(group, ())
        schemas[config.stem] = schema

        main_window = next(
            group
            for group in root.iter("FCParamGroup")
            if group.get("Name") == "MainWindow"
        )
        assert {child.get("Name") for child in main_window} == required_main_window_keys
        values = {child.get("Name"): child.text for child in main_window}
        assert values["AppearanceMode"] == config.stem
        assert values["Theme"] == config.stem
        assert values["QtStyle"] == "Fusion"
        assert values["StyleSheet"] == f"Vibe{config.stem}.qss"
        assert values["OverlayActiveStyleSheet"] == f"Vibe{config.stem}_Overlay.qss"
        assert values["ThemeStyleParametersFile"] == (
            f"qss:parameters/{config.stem}.yaml"
        )

    assert schemas["Light"] == schemas["Dark"]


def test_vibecad_does_not_expose_the_legacy_workbench_preferences_page() -> None:
    resource = _source("src/Gui/resource.cpp")

    assert "PrefPageProducer<DlgSettingsWorkbenchesImp>" not in resource


def test_dark_theme_uses_onshape_style_sketcher_color_roles() -> None:
    root = ET.parse(ROOT / "src/Gui/Themes/Dark.cfg").getroot()

    def values_at(*names: str) -> dict[str, int]:
        group = root
        for name in names:
            group = next(
                child
                for child in group
                if child.tag == "FCParamGroup" and child.get("Name") == name
            )
        return {
            child.get("Name", ""): int(child.get("Value", "0"))
            for child in group
            if child.tag == "FCUInt"
        }

    view = values_at("Root", "BaseApp", "Preferences", "View")
    expected_view_colors = {
        "SketchEdgeColor": "ADB5BD",
        "SketchVertexColor": "ADB5BD",
        "EditedEdgeColor": "4DABF7",
        "EditedVertexColor": "4DABF7",
        "ConstructionColor": "74C0FC",
        "ExternalColor": "DA77F2",
        "InvalidSketchColor": "FF6B6B",
        "FullyConstrainedColor": "F1F3F5",
        "InternalAlignedGeoColor": "74C0FC",
        "FullyConstraintElementColor": "F1F3F5",
        "FullyConstraintConstructionElementColor": "CED4DA",
        "FullyConstraintInternalAlignmentColor": "CED4DA",
        "FullyConstraintConstructionPointColor": "F1F3F5",
        "ConstrainedIcoColor": "FF6B6B",
        "NonDrivingConstrDimColor": "ADB5BD",
        "ConstrainedDimColor": "FF6B6B",
        "ExprBasedConstrDimColor": "FFA94D",
        "DeactivatedConstrDimColor": "868E96",
        "CursorTextColor": "DEE2E6",
        "CursorCrosshairColor": "F1F3F5",
        "CreateLineColor": "4DABF7",
        "HighlightColor": "FFE066",
        "SelectionColor": "FCC419",
        "PreselectionSelectedColor": "FFD43B",
    }
    assert {
        key: value
        for key, value in view.items()
        if key in expected_view_colors
    } == {
        key: int(rgb + "FF", 16) for key, rgb in expected_view_colors.items()
    }

    sketcher_grid = values_at(
        "Root", "BaseApp", "Preferences", "Mod", "Sketcher", "General"
    )
    assert sketcher_grid["GridLineColor"] == int("343A40FF", 16)
    assert sketcher_grid["GridDivLineColor"] == int("495057FF", 16)

    techdraw_colors = values_at(
        "Root", "BaseApp", "Preferences", "Mod", "TechDraw", "Colors"
    )
    techdraw_dimensions = values_at(
        "Root", "BaseApp", "Preferences", "Mod", "TechDraw", "Dimensions"
    )
    assert techdraw_colors["PageColor"] == int("F1F3F5FF", 16)
    assert techdraw_dimensions["Color"] == int("212529FF", 16)

    sketcher_view = next(
        group
        for group in root.iter("FCParamGroup")
        if group.get("Name") == "View"
        and any(child.get("Name") == "EdgePattern" for child in group)
    )
    sketcher_view_values = {
        child.get("Name", ""): int(child.get("Value", "0"))
        for child in sketcher_view
    }
    assert sketcher_view_values["SelectionWidthIncrease"] == 1

    light_root = ET.parse(ROOT / "src/Gui/Themes/Light.cfg").getroot()
    light_sketcher_view = light_root.find(".//FCParamGroup[@Name='Sketcher']/FCParamGroup[@Name='View']")
    assert light_sketcher_view is not None
    light_sketcher_view_values = {
        child.attrib["Name"]: int(child.attrib["Value"])
        for child in light_sketcher_view
    }
    assert light_sketcher_view_values["SelectionWidthIncrease"] == 1


def test_vibecad_removes_theme_and_preference_pack_escape_hatches() -> None:
    gui_cmake = _source("src/Gui/CMakeLists.txt")
    stylesheet_cmake = _source("src/Gui/Stylesheets/CMakeLists.txt")
    general_ui = _source("src/Gui/PreferencePages/DlgSettingsGeneral.ui")
    advanced_ui = _source("src/Gui/PreferencePages/DlgSettingsUI.ui")
    startup = _source("src/Gui/StartupProcess.cpp")
    start_selector = _source("src/Mod/Start/Gui/ThemeSelectorWidget.cpp")
    start_cmake = _source("src/Mod/Start/Gui/CMakeLists.txt")
    start_resources = _source("src/Mod/Start/Gui/Resources/Start.qrc")

    assert "add_subdirectory(PreferencePacks)" not in gui_cmake
    assert "add_subdirectory(Themes)" in gui_cmake
    assert not any((ROOT / "src/Gui/PreferencePacks").rglob("*.*"))
    assert '"FreeCAD.qss"' not in stylesheet_cmake
    assert '"overlay/Freecad Overlay.qss"' not in stylesheet_cmake
    assert "FILE(GLOB Images_Files2" not in stylesheet_cmake
    assert "${Images_Files2}" not in stylesheet_cmake
    assert not (ROOT / "src/Gui/Stylesheets/FreeCAD.qss").exists()
    assert not (ROOT / "src/Gui/Stylesheets/images_classic").exists()
    assert not (ROOT / "src/Gui/Stylesheets/overlay/Freecad Overlay.qss").exists()
    assert "parameters/Dark.yaml" in stylesheet_cmake
    assert "parameters/Light.yaml" in stylesheet_cmake
    assert "ThemeAccentColor" not in _source("src/Gui/Stylesheets/parameters/Dark.yaml")
    assert "ThemeAccentColor" not in _source(
        "src/Gui/Stylesheets/parameters/Light.yaml"
    )

    for removed_surface in (
        "Preference Packs",
        "moreThemesLabel",
        "Import Configuration",
        "Theme Customization",
        "Accent color",
        "Style sheet (advanced)",
        "Open Theme Editor",
    ):
        assert removed_surface not in general_ui + advanced_ui

    assert "prefPackManager()->apply" not in startup
    assert "themeManager()->applyCurrent(false)" in startup
    assert "prefPackManager" not in start_selector
    assert "Theme::Classic" not in start_selector
    assert "FreeCAD Classic" not in start_selector
    assert "Std_AddonMgr" not in start_selector
    assert 'tr("Light")' in start_selector
    assert 'tr("Dark")' in start_selector
    assert "ThemeManager::Mode::Light" in start_selector
    assert "ThemeManager::Mode::Dark" in start_selector
    assert "Theme_thumbnail_classic.png" not in start_cmake + start_resources


def test_vibecad_ribbon_has_explicit_domains_and_legacy_fallback() -> None:
    ribbon = _source("src/Gui/VibeCADRibbon.cpp")
    startup = _source("src/Gui/StartupProcess.cpp")
    vibecad_gui_startup = _source("src/Mod/VibeCAD/InitGui.py")
    mesh_workbench = _source("src/Mod/Mesh/Gui/Workbench.cpp")

    for label, workbench, surface in (
        ("Model", "PartDesignWorkbench", "model"),
        ("Assemble", "AssemblyWorkbench", "assemble"),
        ("Mesh", "MeshWorkbench", "mesh"),
        ("Analyze", "FemWorkbench", "analyze"),
        ("Manufacture", "CAMWorkbench", "manufacture"),
        ("Drawing", "TechDrawWorkbench", "drawing"),
        ("Parameters", "SpreadsheetWorkbench", "parameters"),
        ("Aero", "VibeCADAeroWorkbench", "aero"),
    ):
        assert f'{{"{label}", "{workbench}", "{surface}"}}' in ribbon

    compact_ribbon = "".join(ribbon.split())
    for workbench in (
        "SurfaceWorkbench",
        "PointsWorkbench",
        "ReverseEngineeringWorkbench",
        "RobotWorkbench",
    ):
        assert f'appendComposed("{workbench}"' in compact_ribbon
    assert 'appendComposed("DraftWorkbench"' not in compact_ribbon

    assert "initializeWorkbench(const char* name)" in _source("src/Gui/Application.cpp")

    for object_name in (
        "VibeCADRibbonToolBar",
        "VibeCADRibbon",
        "VibeCADAppButton",
        "VibeCADDocumentTabs",
        "VibeCADLeadingTools",
        "VibeCADTrailingTools",
        "VibeCADRibbonGroupMenu",
        "VibeCADRibbonSearch",
        "VibeCADRibbonCheckForUpdates",
        "VibeCADCommandSearch",
        "VibeCADThemeToggle",
        "VibeCADRibbonTabs",
    ):
        assert f'QStringLiteral("{object_name}")' in ribbon
    assert 'QStringLiteral("VibeCADAeroWorkspaceHost")' not in ribbon

    assert "workbench->getToolbarItems()" in ribbon
    assert "command->getAction()->action()" in ribbon
    assert "Qt::Key_Alt" not in ribbon
    assert "Qt::Key_F10" not in ribbon
    assert "mainWindow->menuBar()->setVisible(legacyMenuVisible);" in ribbon
    assert 'showFullMenuBarPreference = "ShowFullMenuBar"' in ribbon
    assert "GetBool(showFullMenuBarPreference, false)" in ribbon
    assert "SetBool(showFullMenuBarPreference, visible)" in ribbon
    assert "bool legacyMenuVisible = false;" in ribbon
    assert "sourceDocumentTabs->hide();" in ribbon
    assert "documentTabs->setTabsClosable(true);" in ribbon
    assert "groupMenu->setPopupMode(QToolButton::InstantPopup);" in ribbon
    assert "sharedInspectionCommands()" in ribbon
    assert 'addGroup(QObject::tr("Inspect"), std::move(entries));' in ribbon
    assert '("InspectionGui", "MeshPartGui", "PartGui")' in vibecad_gui_startup
    assert 'convert->setCommand("Mesh Convert")' in mesh_workbench
    assert '<< "MeshPart_ShapeFromMesh"' in mesh_workbench
    assert '<< "MeshPart_CurveOnMesh"' in mesh_workbench
    assert 'QStringLiteral("VibeCAD_OpenPreferences")' in ribbon
    assert 'QStringLiteral("VibeCAD_CheckForUpdates")' in ribbon
    assert "VibeCADRibbon::install(mainWindow);" in startup


def test_vibecad_migrates_its_obsolete_background_autoload_before_use() -> None:
    startup = _source("src/Gui/StartupProcess.cpp")
    activation = startup.split("void StartupPostProcess::activateWorkbench()", 1)[
        1
    ].split("void StartupPostProcess::setStyleSheet()", 1)[0]
    assert activation.index("migrateVibeCADBackgroundAutoload(wb);") < activation.index(
        "autoloadModules(wb);"
    )
    migration = startup.split(
        "void StartupPostProcess::migrateVibeCADBackgroundAutoload", 1
    )[1].split("void StartupPostProcess::autoloadModules", 1)[0]
    assert 'migrationKey = "VibeCADBackgroundAutoloadModules2026"' in migration
    assert 'general->RemoveASCII("BackgroundAutoloadModules");' in migration


def test_vibecad_bootstrap_repairs_only_vibecad_disabled_lists(monkeypatch) -> None:
    class ParameterGroup:
        def __init__(self, disabled: str) -> None:
            self.disabled = disabled

        def GetString(self, name: str, default: str) -> str:
            assert name == "Disabled"
            return self.disabled or default

        def SetString(self, name: str, value: str) -> None:
            assert name == "Disabled"
            self.disabled = value

    preferences = ParameterGroup(
        "InspectionWorkbench,MaterialWorkbench,PointsWorkbench,"
        "ReverseEngineeringWorkbench,RobotWorkbench,TestWorkbench,NoneWorkbench"
    )
    app = SimpleNamespace(
        Console=SimpleNamespace(PrintWarning=lambda _message: None),
        ParamGet=lambda _path: preferences,
    )
    startup_events: list[str] = []
    qt_core = SimpleNamespace(
        QTimer=SimpleNamespace(
            singleShot=lambda _delay, callback: startup_events.append(
                f"scheduled:{callback.__name__}"
            )
        )
    )
    gui = SimpleNamespace(
        ensure_commands_registered=lambda: startup_events.append("commands"),
    )
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtCore=qt_core))
    monkeypatch.setitem(sys.modules, "VibeCADGui", gui)
    monkeypatch.setitem(
        sys.modules,
        "VibeCADAnalyzeStudyGui",
        SimpleNamespace(ensure_command_registered=lambda: None),
    )

    namespace = runpy.run_path(str(ROOT / "src/Mod/VibeCAD/InitGui.py"))
    assert preferences.disabled == "TestWorkbench,NoneWorkbench"
    assert startup_events == [
        "commands",
        "scheduled:_setup_development_identity",
        "scheduled:_setup_always_on_grid",
        "scheduled:_setup_agent_control",
        "scheduled:_setup_aero_ribbon",
    ]

    preferences.disabled = (
        "MaterialWorkbench,TestWorkbench,NoneWorkbench,CustomWorkbench"
    )
    assert namespace["_restore_vibecad_disabled_workbenches"]() is False
    assert preferences.disabled == (
        "MaterialWorkbench,TestWorkbench,NoneWorkbench,CustomWorkbench"
    )

    preferences.disabled = (
        "InspectionWorkbench,MaterialWorkbench,OpenSCADWorkbench,"
        "PointsWorkbench,ReverseEngineeringWorkbench,RobotWorkbench,"
        "TestWorkbench,NoneWorkbench"
    )
    assert namespace["_restore_vibecad_disabled_workbenches"]() is True
    assert preferences.disabled == "TestWorkbench,NoneWorkbench"


def test_vibecad_bootstrap_helpers_survive_freecad_exec_namespace(monkeypatch) -> None:
    """InitGui helpers must not rely on names held only in exec() locals."""

    class ParameterGroup:
        def GetString(self, _name: str, default: str) -> str:
            return default

        def SetString(self, _name: str, _value: str) -> None:
            pass

        def GetBool(self, _name: str, default: bool) -> bool:
            return default

        def SetBool(self, _name: str, _value: bool) -> None:
            pass

    warnings: list[str] = []
    startup_events: list[str] = []
    app = SimpleNamespace(
        Console=SimpleNamespace(PrintWarning=warnings.append),
        ParamGet=lambda _path: ParameterGroup(),
    )
    qt_core = SimpleNamespace(
        QTimer=SimpleNamespace(
            singleShot=lambda _delay, callback: startup_events.append(
                f"scheduled:{callback.__name__}"
            )
        )
    )
    fasteners = SimpleNamespace(require_available=lambda: None)
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtCore=qt_core))
    monkeypatch.setitem(
        sys.modules,
        "VibeCADGui",
        SimpleNamespace(
            ensure_commands_registered=lambda: startup_events.append("assistant")
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADAnalyzeStudyGui",
        SimpleNamespace(ensure_command_registered=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADFasteners",
        fasteners,
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADFastenersGui",
        SimpleNamespace(
            ensure_commands_registered=lambda: startup_events.append("fasteners")
        ),
    )

    init_gui = ROOT / "src/Mod/VibeCAD/InitGui.py"
    loader_globals = {"App": app}
    loader_locals = {}
    exec(
        compile(init_gui.read_bytes(), str(init_gui), "exec"),
        loader_globals,
        loader_locals,
    )

    assert "assistant" in startup_events
    assert "fasteners" in startup_events
    assert "scheduled:_setup_always_on_grid" in startup_events
    assert "scheduled:_setup_agent_control" in startup_events
    assert "scheduled:_setup_aero_ribbon" in startup_events
    assert not any("GUI bootstrap failed" in warning for warning in warnings)
    assert any("ribbon extension" in warning for warning in warnings)

    def fail_catalog() -> None:
        raise RuntimeError("catalog unavailable")

    fasteners.require_available = fail_catalog
    assert loader_locals["_check_bundled_fasteners"]() is False
    assert any("catalog unavailable" in warning for warning in warnings)


def test_setup_agent_control_invokes_local_vibecadgui_import(monkeypatch) -> None:
    """The deferred callback must import VibeCADGui in its own body.

    FreeCAD exec()s InitGui.py with separate globals and locals. A module-level
    import is not visible to nested functions, so removing the local import
    raises NameError and the server never starts.
    """

    class ParameterGroup:
        def GetString(self, _name: str, default: str) -> str:
            return default

        def SetString(self, _name: str, _value: str) -> None:
            pass

        def GetBool(self, _name: str, default: bool) -> bool:
            return default

        def SetBool(self, _name: str, _value: bool) -> None:
            pass

    started: list[dict] = []
    fail_closed_started: list[dict] = []
    warnings: list[str] = []
    dispatch = object()
    app = SimpleNamespace(
        Console=SimpleNamespace(PrintWarning=warnings.append),
        ParamGet=lambda _path: ParameterGroup(),
    )
    qt_core = SimpleNamespace(
        QTimer=SimpleNamespace(singleShot=lambda _delay, _callback: None)
    )
    qt_widgets = SimpleNamespace(
        QApplication=SimpleNamespace(instance=lambda: None)
    )
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(
        sys.modules,
        "PySide",
        SimpleNamespace(QtCore=qt_core, QtWidgets=qt_widgets),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADGui",
        SimpleNamespace(
            ensure_commands_registered=lambda: None,
            _dispatch_to_document_thread=dispatch,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADAnalyzeStudyGui",
        SimpleNamespace(ensure_command_registered=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADAgentControl",
        SimpleNamespace(
            ensure_server_started=lambda **kwargs: started.append(kwargs),
            ensure_fail_closed_server_started=lambda **kwargs: fail_closed_started.append(
                kwargs
            ),
            shutdown_server=lambda **_kwargs: None,
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADAnalyzeStudyGui",
        SimpleNamespace(ensure_command_registered=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADFasteners",
        SimpleNamespace(require_available=lambda: None),
    )
    monkeypatch.setitem(
        sys.modules,
        "VibeCADFastenersGui",
        SimpleNamespace(ensure_commands_registered=lambda: None),
    )

    init_gui = ROOT / "src/Mod/VibeCAD/InitGui.py"
    loader_globals = {"App": app}
    loader_locals = {}
    exec(
        compile(init_gui.read_bytes(), str(init_gui), "exec"),
        loader_globals,
        loader_locals,
    )

    monkeypatch.delenv("VIBECAD_DEV_MODE", raising=False)
    loader_locals["_setup_agent_control"]()

    assert started == [{"document_thread_dispatch": dispatch}]
    assert fail_closed_started == []
    assert not any("failed to start" in warning for warning in warnings)

    monkeypatch.setenv("VIBECAD_DEV_MODE", "true")
    loader_locals["_setup_agent_control"]()
    assert started == [
        {"document_thread_dispatch": dispatch},
        {"document_thread_dispatch": dispatch},
    ]
    assert fail_closed_started == []

    monkeypatch.setenv("VIBECAD_DEV_MODE", "1")
    loader_locals["_setup_agent_control"]()
    assert started == [
        {"document_thread_dispatch": dispatch},
        {"document_thread_dispatch": dispatch},
    ]
    assert fail_closed_started == [{"document_thread_dispatch": dispatch}]


def test_vibecad_bootstrap_migrates_removed_bim_preferences(monkeypatch) -> None:
    class ParameterGroup:
        def __init__(self, values=None) -> None:
            self.values = dict(values or {})

        def GetString(self, name: str, default: str) -> str:
            return str(self.values.get(name, default))

        def SetString(self, name: str, value: str) -> None:
            self.values[name] = value

        def GetBool(self, name: str, default: bool) -> bool:
            return bool(self.values.get(name, default))

        def SetBool(self, name: str, value: bool) -> None:
            self.values[name] = value

    workbenches = ParameterGroup(
        {
            "Ordered": "PartWorkbench,BIMWorkbench,DraftWorkbench",
            "Disabled": "TestWorkbench,BIMWorkbench,NoneWorkbench",
        }
    )
    general = ParameterGroup(
        {
            "BackgroundAutoloadModules": "BIMWorkbench,PartWorkbench",
            "AutoloadModule": "BIMWorkbench",
            "LastModule": "BIMWorkbench",
        }
    )
    migration = ParameterGroup()
    groups = {
        "User parameter:BaseApp/Preferences/Workbenches": workbenches,
        "User parameter:BaseApp/Preferences/General": general,
        "User parameter:BaseApp/Preferences/Migration": migration,
    }
    warnings: list[str] = []
    app = SimpleNamespace(
        Console=SimpleNamespace(PrintWarning=warnings.append),
        ParamGet=groups.__getitem__,
    )
    qt_core = SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda *_args: None))
    gui = SimpleNamespace(ensure_commands_registered=lambda: None)
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtCore=qt_core))
    monkeypatch.setitem(sys.modules, "VibeCADGui", gui)

    init_gui = ROOT / "src/Mod/VibeCAD/InitGui.py"
    loader_globals = {"App": app}
    loader_locals = {}
    exec(
        compile(init_gui.read_bytes(), str(init_gui), "exec"),
        loader_globals,
        loader_locals,
    )

    assert workbenches.values["Ordered"] == "PartDesignWorkbench,DraftWorkbench"
    assert workbenches.values["Disabled"] == "TestWorkbench,NoneWorkbench"
    assert general.values["BackgroundAutoloadModules"] == "PartDesignWorkbench"
    assert general.values["AutoloadModule"] == "PartDesignWorkbench"
    assert general.values["LastModule"] == "PartDesignWorkbench"
    assert migration.values["VibeCADRemovedArchitectureWorkbench2026"] is True
    assert migration.values["VibeCADConsolidatedPartWorkbench2026"] is True
    assert any("retired architecture workbench" in warning for warning in warnings)
    assert any("Part workbench references" in warning for warning in warnings)


def test_vibecad_bootstrap_migrates_removed_openscad_preferences(monkeypatch) -> None:
    class ParameterGroup:
        def __init__(self, values=None) -> None:
            self.values = dict(values or {})

        def GetString(self, name: str, default: str) -> str:
            return str(self.values.get(name, default))

        def SetString(self, name: str, value: str) -> None:
            self.values[name] = value

        def GetBool(self, name: str, default: bool) -> bool:
            return bool(self.values.get(name, default))

        def SetBool(self, name: str, value: bool) -> None:
            self.values[name] = value

    workbenches = ParameterGroup(
        {
            "Ordered": "PartDesignWorkbench,OpenSCADWorkbench,MeshWorkbench",
            "Disabled": "TestWorkbench,OpenSCADWorkbench,NoneWorkbench",
        }
    )
    general = ParameterGroup(
        {
            "BackgroundAutoloadModules": (
                "OpenSCADWorkbench,MeshWorkbench,PartDesignWorkbench"
            ),
            "AutoloadModule": "OpenSCADWorkbench",
            "LastModule": "OpenSCADWorkbench",
        }
    )
    migration = ParameterGroup()
    groups = {
        "User parameter:BaseApp/Preferences/Workbenches": workbenches,
        "User parameter:BaseApp/Preferences/General": general,
        "User parameter:BaseApp/Preferences/Migration": migration,
    }
    warnings: list[str] = []
    app = SimpleNamespace(
        Console=SimpleNamespace(PrintWarning=warnings.append),
        ParamGet=groups.__getitem__,
    )
    qt_core = SimpleNamespace(QTimer=SimpleNamespace(singleShot=lambda *_args: None))
    gui = SimpleNamespace(ensure_commands_registered=lambda: None)
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtCore=qt_core))
    monkeypatch.setitem(sys.modules, "VibeCADGui", gui)

    init_gui = ROOT / "src/Mod/VibeCAD/InitGui.py"
    loader_globals = {"App": app}
    loader_locals = {}
    exec(
        compile(init_gui.read_bytes(), str(init_gui), "exec"),
        loader_globals,
        loader_locals,
    )

    assert workbenches.values["Ordered"] == ("PartDesignWorkbench,MeshWorkbench")
    assert workbenches.values["Disabled"] == "TestWorkbench,NoneWorkbench"
    assert general.values["BackgroundAutoloadModules"] == (
        "MeshWorkbench,PartDesignWorkbench"
    )
    assert general.values["AutoloadModule"] == "MeshWorkbench"
    assert general.values["LastModule"] == "MeshWorkbench"
    assert migration.values["VibeCADRemovedOpenSCADWorkbench2026"] is True
    assert any(
        "OpenSCAD workbench references to Mesh" in warning for warning in warnings
    )

    workbenches.values["Ordered"] = "OpenSCADWorkbench,PartDesignWorkbench"
    assert loader_locals["_migrate_removed_openscad_workbench"]() is False
    assert workbenches.values["Ordered"] == ("OpenSCADWorkbench,PartDesignWorkbench")
