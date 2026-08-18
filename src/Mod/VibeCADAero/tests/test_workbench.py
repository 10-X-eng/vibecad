# SPDX-License-Identifier: LGPL-2.1-or-later

"""Workbench stays loadable when optional aero pip packages are absent."""

from __future__ import annotations

from pathlib import Path
import ast

ROOT = Path(__file__).resolve().parents[1]


def _top_level_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in tree.body:
        if isinstance(node, ast.Import):
            names.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module.split(".")[0])
    return names


def test_init_modules_do_not_import_optional_solvers():
    forbidden = {"neuralfoil", "aerosandbox", "jsbsim"}
    for name in (
        "Init.py",
        "InitGui.py",
        "AeroCommandLoader.py",
        "Commands.py",
        "VibeCADAero.py",
        "AeroIcons.py",
        "AeroWorkspace.py",
    ):
        imported = _top_level_imports(ROOT / name)
        assert imported.isdisjoint(forbidden), f"{name} imports {imported & forbidden}"


def test_initgui_registers_aero_workbench_without_executing_solvers():
    source = (ROOT / "InitGui.py").read_text(encoding="utf-8")
    assert "class VibeCADAeroWorkbench" in source
    assert 'MenuText = "Aero"' in source
    assert "AeroIcons.aero_icon_path" in source
    assert "Gui.addWorkbench" in source
    assert "neuralfoil" not in source
    assert "aerosandbox" not in source
    assert "jsbsim" not in source


def test_commands_cover_analyze_section_vlm_jsbsim_and_report():
    source = (ROOT / "Commands.py").read_text(encoding="utf-8")
    for command in (
        "VibeCADAero_Analyze",
        "VibeCADAero_Section",
        "VibeCADAero_VLM",
        "VibeCADAero_ExportJSBSim",
        "VibeCADAero_Report",
    ):
        assert command in source


def test_public_helper_is_import_path_for_agent_control():
    source = (ROOT / "VibeCADAero.py").read_text(encoding="utf-8")
    assert "def run_analyze" in source
    assert "def run_section" in source
    assert "def run_vlm" in source
    assert "def export_jsbsim" in source


def test_cmake_installs_mod_vibecadaero():
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    parent = (ROOT.parent / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "add_subdirectory(VibeCADAero)" in parent
    assert "Mod/VibeCADAero" in cmake
    assert "InitGui.py" in cmake
    assert "data/e63.dat" in cmake
    assert "icons/vibecad-aero-analyze.svg" in cmake
    assert "AeroWorkspace.py" in cmake
    assert "AeroIcons.py" in cmake
    assert "AeroCommandLoader.py" in cmake


def test_requirements_list_optional_pip_packages():
    text = (ROOT / "requirements-aero.txt").read_text(encoding="utf-8")
    assert "neuralfoil" in text
    assert "aerosandbox" in text
    assert "jsbsim" in text
    assert "python.exe" in text
    assert "wheel" not in text.lower() or "do not vendor" in text.lower()
