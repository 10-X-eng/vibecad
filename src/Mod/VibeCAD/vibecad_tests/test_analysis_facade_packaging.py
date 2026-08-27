# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import json
import os
from pathlib import Path
import re
import subprocess
import sys

import pytest


VIBECAD_DIR = Path(__file__).resolve().parents[1]
CMAKE_LISTS = VIBECAD_DIR / "CMakeLists.txt"
PUBLIC_GOVERNED_RUNTIME_MODULES = (
    "VibeCADAnalysisRuntime.py",
    "VibeCADAnalysisContracts.py",
    "VibeCADAnalysisArtifacts.py",
    "VibeCADAnalysisProviders.py",
    "VibeCADAnalysisLocalProvider.py",
    "VibeCADAnalysisPersistence.py",
    "VibeCADAnalysisPublication.py",
    "VibeCADEngineeringContracts.py",
    "VibeCADEngineeringExperience.py",
    "VibeCADNativeAuthorityPolicy.py",
    "VibeCADAnalysisWorkflow.py",
    "VibeCADGovernedOptimization.py",
    "VibeCADNativeAssemblyIdentity.py",
    "VibeCADAssemblyPlanning.py",
    "VibeCADNativeManufactureGovernance.py",
    "VibeCADNativeManufactureCamoticsRuntime.py",
    "VibeCADNativeManufacturePostRuntime.py",
    "VibeCADNativeManufactureSimulationRuntime.py",
    "VibeCADNativeManufactureSimulationResultRuntime.py",
)
PACKAGED_TREE_ENVIRONMENT = (
    ("VIBECAD_BUILD_MODULE_DIR", "CMake build tree"),
    ("VIBECAD_INSTALL_MODULE_DIR", "installed tree"),
)


def assert_ci_packaged_facade_deployments() -> None:
    """Exercise the real CMake build and install trees in the existing CI suite."""
    workspace = os.environ.get("GITHUB_WORKSPACE")
    if not workspace:
        pytest.skip("The authoritative CMake deployment check runs in GitHub Actions.")

    workspace_dir = Path(workspace).resolve()
    build_dir = workspace_dir / "build" / "release"
    build_module_dir = build_dir / "Mod" / "VibeCAD"
    vibecad_binary_dir = build_dir / "src" / "Mod" / "VibeCAD"
    install_prefix = workspace_dir / "build" / "install-smoke"
    install_module_dir = install_prefix / "Mod" / "VibeCAD"

    completed = subprocess.run(
        [
            "cmake",
            "--install",
            str(vibecad_binary_dir),
            "--prefix",
            str(install_prefix),
            "--component",
            "Unspecified",
        ],
        cwd=workspace_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        "Could not create the isolated VibeCAD default-component install tree.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    _assert_isolated_facade_imports(build_module_dir, "CMake build tree")
    _assert_isolated_facade_imports(install_module_dir, "CMake installed tree")


def _registered_vibecad_scripts() -> set[str]:
    text = CMAKE_LISTS.read_text(encoding="utf-8")
    match = re.search(
        r"(?ms)^set\(VibeCAD_Scripts\s*$\n(?P<body>.*?)^\)\s*$",
        text,
    )
    assert match is not None, "Could not find the explicit VibeCAD_Scripts list."
    return set(
        re.findall(
            r"(?m)^\s*([A-Za-z0-9_./+-]+\.py)\s*$",
            match.group("body"),
        )
    )


def _install_rule_containing(needle: str) -> str:
    text = CMAKE_LISTS.read_text(encoding="utf-8")
    rules = re.findall(r"(?ms)^\s*install\(\s*$.*?^\s*\)\s*$", text)
    matches = [rule for rule in rules if needle in rule]
    assert len(matches) == 1, (
        f"Expected one VibeCAD install rule containing {needle!r}, found {len(matches)}."
    )
    return matches[0]


def _assert_isolated_facade_imports(module_dir: Path, deployment: str) -> None:
    module_dir = module_dir.resolve()
    assert module_dir.is_dir(), f"Missing {deployment} module directory: {module_dir}"

    missing = [
        name
        for name in PUBLIC_GOVERNED_RUNTIME_MODULES
        if not (module_dir / name).is_file()
    ]
    assert missing == [], f"{deployment} omitted governed runtime modules: {missing}"
    assert (module_dir / "tool_impl").is_dir(), (
        f"{deployment} omitted the installed tool_impl package: {module_dir}"
    )

    probe = r"""
import importlib
import json
from pathlib import Path
import sys

module_dir = Path(sys.argv[1]).resolve()
facades = json.loads(sys.argv[2])
sys.path.insert(0, str(module_dir))
loaded = {}
for filename in facades:
    module_name = filename.removesuffix(".py")
    module = importlib.import_module(module_name)
    module_path = Path(module.__file__).resolve()
    try:
        module_path.relative_to(module_dir)
    except ValueError as exc:
        raise RuntimeError(
            f"{module_name} leaked from {module_path}, outside {module_dir}"
        ) from exc
    loaded[module_name] = str(module_path)
print(json.dumps(loaded, sort_keys=True))
"""
    environment = os.environ.copy()
    environment.pop("PYTHONPATH", None)
    environment.pop("PYTHONHOME", None)
    completed = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-c",
            probe,
            str(module_dir),
            json.dumps(PUBLIC_GOVERNED_RUNTIME_MODULES),
        ],
        cwd=module_dir.parent,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert completed.returncode == 0, (
        f"Could not import public Analysis facades from the isolated {deployment}.\n"
        f"stdout:\n{completed.stdout}\n"
        f"stderr:\n{completed.stderr}"
    )
    loaded = json.loads(completed.stdout)
    assert set(loaded) == {
        name.removesuffix(".py") for name in PUBLIC_GOVERNED_RUNTIME_MODULES
    }


def test_public_governed_runtime_modules_are_registered_for_copy_and_install() -> None:
    registered = _registered_vibecad_scripts()
    missing = [
        name for name in PUBLIC_GOVERNED_RUNTIME_MODULES if name not in registered
    ]
    assert missing == [], (
        "Public governed runtime modules must be registered in VibeCAD_Scripts so CMake "
        f"copies and installs them; missing: {missing}"
    )


def test_vibecad_python_install_rules_retain_the_default_component() -> None:
    for needle in (
        "${VibeCAD_Scripts}",
        "${VibeCAD_UpdateTrustFiles}",
        "DIRECTORY\n        tool_impl",
    ):
        rule = _install_rule_containing(needle)
        assert not re.search(r"(?m)^\s*COMPONENT(?:\s+\S+|\s*$)", rule), (
            "Existing downstream packaging uses --component Unspecified, so the "
            f"install rule containing {needle!r} must remain in CMake's default component."
        )


@pytest.mark.parametrize(("environment_name", "deployment"), PACKAGED_TREE_ENVIRONMENT)
def test_public_governed_runtime_modules_import_from_isolated_packaged_tree(
    environment_name: str,
    deployment: str,
) -> None:
    configured = os.environ.get(environment_name)
    if not configured:
        pytest.skip(f"{environment_name} is supplied by the packaging integration job.")
    _assert_isolated_facade_imports(Path(configured), deployment)
