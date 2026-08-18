# SPDX-License-Identifier: LGPL-2.1-or-later

"""Workbench stays loadable when optional aero pip packages are absent."""

from __future__ import annotations

from pathlib import Path
import ast
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
REPO = ROOT.parents[2]


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
        "AeroRepair.py",
    ):
        imported = _top_level_imports(ROOT / name)
        assert imported.isdisjoint(forbidden), f"{name} imports {imported & forbidden}"


def test_initgui_registers_aero_workbench_without_executing_solvers():
    source = (ROOT / "InitGui.py").read_text(encoding="utf-8")
    assert "class VibeCADAeroWorkbench" in source
    assert 'MenuText = "Aero"' in source
    assert "FreeCAD.getHomePath()" in source
    assert "Mod/VibeCADAero/icons/vibecad-aero-analyze.svg" in source
    assert "Gui.addWorkbench" in source
    assert "neuralfoil" not in source
    assert "aerosandbox" not in source
    assert "jsbsim" not in source
    assert "show_workspace" not in source


def test_initgui_supports_freecad_separate_global_and_local_namespaces():
    source_path = ROOT / "InitGui.py"
    registered = []

    class FakeWorkbench:
        pass

    globals_namespace = {
        "__builtins__": __builtins__,
        "__file__": str(source_path),
        "Workbench": FakeWorkbench,
        "FreeCAD": SimpleNamespace(getHomePath=lambda: "C:/VibeCAD/"),
        "Gui": SimpleNamespace(addWorkbench=registered.append),
        "Log": lambda _message: None,
        "Msg": lambda _message: None,
    }

    exec(
        compile(source_path.read_text(encoding="utf-8"), str(source_path), "exec"),
        globals_namespace,
        {},
    )

    assert len(registered) == 1
    assert registered[0].Icon == (
        "C:/VibeCAD/Mod/VibeCADAero/icons/vibecad-aero-analyze.svg"
    )


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
    assert "def format_analyze_report" in source
    assert "_append_in_app_conversation" in source
    assert '{"source": "aero"}' in source


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
    assert "AeroRepair.py" in cmake
    assert "test_repair.py" in cmake


def test_requirements_pin_bundled_aero_runtime_without_numpy_2():
    requirements = [
        line.strip()
        for line in (ROOT / "requirements-aero.txt")
        .read_text(encoding="utf-8")
        .splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    ]
    assert requirements == [
        "numpy>=1.26,<2",
        "casadi==3.7.2",
        "neuralfoil==0.3.3",
        "aerosandbox==4.2.8",
        "jsbsim==1.3.1",
    ]


def test_compiled_casadi_runtime_is_conda_managed_on_every_platform():
    recipe = (
        REPO / "package" / "rattler-build" / "recipe.yaml"
    ).read_text(encoding="utf-8")
    run_requirements = recipe.split("\n  run:\n", 1)[1].split("\n    - if:", 1)[0]
    assert "\n    - casadi ==3.7.2\n" in run_requirements


def test_readme_says_release_builds_bundle_aero_dependencies():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")
    assert "Release builds install these dependencies automatically" in readme
    assert "Optional packages" not in readme


def test_shared_release_installer_installs_and_imports_aero_dependencies():
    script = (
        REPO / "package" / "rattler-build" / "scripts" / "install_vibecad_provider_deps.sh"
    ).read_text(encoding="utf-8")
    assert "src/Mod/VibeCADAero/requirements-aero.txt" in script
    assert '-r "${aero_requirements}"' in script
    for module in ("numpy", "casadi", "neuralfoil", "aerosandbox", "jsbsim"):
        assert f'"{module}"' in script
    assert "NumPy 2" in script


def test_local_release_smoke_checks_aero_dependencies_inside_freecad():
    script = (
        REPO / "package" / "rattler-build" / "scripts" / "build_vibecad_local_release.sh"
    ).read_text(encoding="utf-8")
    for module in ("numpy", "casadi", "neuralfoil", "aerosandbox", "jsbsim"):
        assert module in script


def test_release_cache_keys_include_aero_requirements():
    workflows = {
        "vibecad-release.yml": 3,
        "vibecad-windows-installer.yml": 1,
        "vibecad-macos.yml": 1,
    }
    for name, expected_count in workflows.items():
        source = (REPO / ".github" / "workflows" / name).read_text(encoding="utf-8")
        assert source.count("'src/Mod/VibeCADAero/requirements-aero.txt'") == expected_count
