# SPDX-License-Identifier: LGPL-2.1-or-later

"""Guardrails for the user-facing VibeCAD product identity."""

from __future__ import annotations

import runpy
import sys
import xml.etree.ElementTree as ET
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[4]


def _source(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


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


def test_vibecad_docks_are_registered_before_native_window_restore() -> None:
    gui = _source("src/Mod/VibeCAD/VibeCADGui.py")
    init_gui = _source("src/Mod/VibeCAD/InitGui.py")
    startup = _source("src/Gui/StartupProcess.cpp")

    assert "QtCore.QTimer.singleShot(0, apply_context_debug_preferences)" not in gui
    assert "QtCore.QTimer.singleShot(0, ensure_scripted_model_editor_registered)" not in gui
    assert "ensure_scripted_model_editor_registered()" in gui
    assert "QtCore.QTimer.singleShot(0, _register_startup_assistant)" not in init_gui
    assert "_register_startup_assistant()" in init_gui
    assert "register_startup_assistant()" in init_gui
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


def test_vibecad_docks_use_native_workbench_declarations(monkeypatch) -> None:
    import VibeCADGui as panel

    monkeypatch.setattr(
        panel.Gui,
        "activeWorkbench",
        lambda: SimpleNamespace(name=lambda: "AssemblyWorkbench"),
        raising=False,
    )
    monkeypatch.setattr(panel, "get_tool_pack", lambda _name: object())
    monkeypatch.setattr(panel, "_find_context_debug_dock", lambda: object())

    assert panel._VibeCADDockWorkbenchManipulator().modifyDockWindows() == [
        {
            "add": panel.DOCK_NAME,
            "area": "right",
            "visible": True,
            "tabbed": True,
        },
        {
            "add": panel.MODEL_CODE_DOCK_NAME,
            "area": "right",
            "visible": False,
            "tabbed": True,
        },
        {
            "add": panel.CONTEXT_DEBUG_DOCK_NAME,
            "area": "bottom",
            "visible": True,
            "tabbed": True,
        },
    ]

    monkeypatch.setattr(panel, "get_tool_pack", lambda _name: None)
    assert panel._VibeCADDockWorkbenchManipulator().modifyDockWindows() == []


def test_vibecad_installs_native_dock_manipulator_once(monkeypatch) -> None:
    import VibeCADGui as panel

    installed: list[object] = []
    monkeypatch.setattr(panel, "_dock_workbench_manipulator", None)
    monkeypatch.setattr(
        panel.Gui,
        "addWorkbenchManipulator",
        installed.append,
        raising=False,
    )

    panel._ensure_dock_workbench_manipulator()
    panel._ensure_dock_workbench_manipulator()

    assert len(installed) == 1
    assert installed[0] is panel._dock_workbench_manipulator


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

    registration = binding.split(
        "Py::Object MainWindowPy::registerDockWindow", 1
    )[1].split("Py::Object MainWindowPy::removeDockWindow", 1)[0]
    assert "dwm->registerDockWindow(name, widget)" in registration
    assert "dwm->addDockWindow" not in registration
    assert "dw->show()" not in registration
    assert "widget->show()" not in registration
    assert "addDockWindow(dockName.constData(), jt.value(), it.pos)" in manager


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
    assert 'version_name="VibeCAD_${BUILD_TAG}-Windows-$(uname -m)"' in bundle_script
    assert 'rm -rf -- "${copy_dir}" "${version_name}" ".nsis_tmp"' in bundle_script
    assert "add_executable(VibeCADPortableLauncher WIN32" in main_cmake
    assert "add_executable(VibeCADCmdPortableLauncher" in main_cmake
    assert 'L"bin\\\\VibeCAD.exe"' in launcher_source
    assert "CreateProcessW" in launcher_source


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


def test_vibecad_preferences_keep_user_workbenches_enabled() -> None:
    config = ROOT / "src/Gui/PreferencePacks/VibeCAD Preferences/VibeCAD Preferences.cfg"
    root = ET.parse(config).getroot()
    workbench_group = next(
        group
        for group in root.iter("FCParamGroup")
        if group.get("Name") == "Workbenches"
    )
    disabled_value = next(
        child
        for child in workbench_group
        if child.tag == "FCText" and child.get("Name") == "Disabled"
    )
    disabled = set(filter(None, (disabled_value.text or "").split(",")))
    user_workbenches = {
        "PartDesignWorkbench",
        "SketcherWorkbench",
        "PartWorkbench",
        "DraftWorkbench",
        "SurfaceWorkbench",
        "AssemblyWorkbench",
        "SpreadsheetWorkbench",
        "MaterialWorkbench",
        "BIMWorkbench",
        "MeshWorkbench",
        "MeshPartWorkbench",
        "PointsWorkbench",
        "ReverseEngineeringWorkbench",
        "InspectionWorkbench",
        "RobotWorkbench",
        "FemWorkbench",
        "CAMWorkbench",
        "TechDrawWorkbench",
    }

    assert disabled == {"TestWorkbench", "NoneWorkbench"}
    assert disabled.isdisjoint(user_workbenches)


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
        register_startup_assistant=lambda: startup_events.append("assistant"),
    )
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtCore=qt_core))
    monkeypatch.setitem(sys.modules, "VibeCADGui", gui)

    namespace = runpy.run_path(str(ROOT / "src/Mod/VibeCAD/InitGui.py"))
    assert preferences.disabled == "TestWorkbench,NoneWorkbench"
    assert startup_events == [
        "commands",
        "assistant",
        "scheduled:_setup_always_on_grid",
    ]

    preferences.disabled = "MaterialWorkbench,TestWorkbench,NoneWorkbench,CustomWorkbench"
    assert namespace["_restore_vibecad_disabled_workbenches"]() is False
    assert preferences.disabled == (
        "MaterialWorkbench,TestWorkbench,NoneWorkbench,CustomWorkbench"
    )

    preferences.disabled = (
        "NoneWorkbench,OpenSCADWorkbench,RobotWorkbench,InspectionWorkbench,"
        "ReverseEngineeringWorkbench,PointsWorkbench,MaterialWorkbench,TestWorkbench"
    )
    assert namespace["_restore_vibecad_disabled_workbenches"]() is True
    assert preferences.disabled == "TestWorkbench,NoneWorkbench"
