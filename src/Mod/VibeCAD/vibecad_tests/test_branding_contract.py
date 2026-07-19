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


def test_vibecad_bootstrap_repairs_only_legacy_disabled_lists(monkeypatch) -> None:
    class ParameterGroup:
        def __init__(self, disabled: str) -> None:
            self.disabled = disabled

        def GetString(self, name: str, default: str) -> str:
            assert name == "Disabled"
            return self.disabled or default

        def SetString(self, name: str, value: str) -> None:
            assert name == "Disabled"
            self.disabled = value

    legacy = ParameterGroup(
        "InspectionWorkbench,MaterialWorkbench,PointsWorkbench,"
        "ReverseEngineeringWorkbench,RobotWorkbench,TestWorkbench,NoneWorkbench"
    )
    app = SimpleNamespace(
        Console=SimpleNamespace(PrintWarning=lambda _message: None),
        ParamGet=lambda _path: legacy,
    )
    qt_core = SimpleNamespace(
        QTimer=SimpleNamespace(singleShot=lambda _delay, _callback: None)
    )
    gui = SimpleNamespace(ensure_commands_registered=lambda: None)
    monkeypatch.setitem(sys.modules, "FreeCAD", app)
    monkeypatch.setitem(sys.modules, "PySide", SimpleNamespace(QtCore=qt_core))
    monkeypatch.setitem(sys.modules, "VibeCADGui", gui)

    namespace = runpy.run_path(str(ROOT / "src/Mod/VibeCAD/InitGui.py"))
    assert legacy.disabled == "TestWorkbench,NoneWorkbench"

    legacy.disabled = "MaterialWorkbench,TestWorkbench,NoneWorkbench,CustomWorkbench"
    assert namespace["_restore_legacy_disabled_workbenches"]() is False
    assert legacy.disabled == (
        "MaterialWorkbench,TestWorkbench,NoneWorkbench,CustomWorkbench"
    )

    legacy.disabled = (
        "NoneWorkbench,OpenSCADWorkbench,RobotWorkbench,InspectionWorkbench,"
        "ReverseEngineeringWorkbench,PointsWorkbench,MaterialWorkbench,TestWorkbench"
    )
    assert namespace["_restore_legacy_disabled_workbenches"]() is True
    assert legacy.disabled == "TestWorkbench,NoneWorkbench"
