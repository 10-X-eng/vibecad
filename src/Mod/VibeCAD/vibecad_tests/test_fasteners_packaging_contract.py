# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

import inspect
import json
from pathlib import Path
import subprocess
from types import SimpleNamespace
import xml.etree.ElementTree as ET

import pytest


REPO = Path(__file__).resolve().parents[4]
VIBECAD = REPO / "src" / "Mod" / "VibeCAD"
FASTENERS = REPO / "src" / "Mod" / "Fasteners"
PINNED_REVISION = "9a09ec46bf5bff87231fce007e1da53610b30854"
PINNED_SOURCE = "https://github.com/10-X-eng/FreeCAD_FastenersWB"


def test_fasteners_is_an_exact_pinned_dependency_with_provenance() -> None:
    modules = (REPO / ".gitmodules").read_text(encoding="utf-8")
    assert "[submodule \"src/Mod/Fasteners\"]" in modules
    assert f"{PINNED_SOURCE}.git" in modules
    provenance = json.loads(
        (VIBECAD / "fasteners-provenance.json").read_text(encoding="utf-8")
    )
    assert provenance == {
        "name": "FreeCAD Fasteners Workbench",
        "module": "Fasteners",
        "version": "0.5.64",
        "revision": PINNED_REVISION,
        "source": PINNED_SOURCE,
        "license": "GPL-2.0-or-later",
        "license_file": "../Fasteners/LICENSE",
    }
    gitlink = subprocess.run(
        [
            "git",
            "-C",
            str(REPO),
            "ls-files",
            "--stage",
            "src/Mod/Fasteners",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.split()
    assert gitlink[:2] == ["160000", PINNED_REVISION]
    assert (FASTENERS / "LICENSE").is_file()
    assert (FASTENERS / "Init.py").is_file()
    assert (FASTENERS / "InitGui.py").is_file()


def test_build_packages_the_complete_module_without_repository_noise() -> None:
    options = (
        REPO
        / "cMake"
        / "FreeCAD_Helpers"
        / "InitializeFreeCADBuildOptions.cmake"
    ).read_text(encoding="utf-8")
    module_cmake = (REPO / "src" / "Mod" / "CMakeLists.txt").read_text(
        encoding="utf-8"
    )
    build_script = (REPO / "tools" / "build_vibecad.sh").read_text(
        encoding="utf-8"
    )
    assert 'option(BUILD_FASTENERS "Bundle the pinned FreeCAD Fasteners workbench" ON)' in options
    assert "fc_target_copy_resource(FastenersModule" in module_cmake
    assert 'PATTERN ".git*" EXCLUDE' in module_cmake
    assert 'PATTERN "__pycache__" EXCLUDE' in module_cmake
    assert 'PATTERN "*.pyc" EXCLUDE' in module_cmake
    assert "src/Mod/Fasteners" in build_script


def test_integrated_commands_have_packaged_valid_icons() -> None:
    cmake = (VIBECAD / "CMakeLists.txt").read_text(encoding="utf-8")
    gui = (VIBECAD / "VibeCADFastenersGui.py").read_text(encoding="utf-8")
    expected = {
        "VibeCAD_InsertStandardFastener": "vibecad-fastener-insert.svg",
        "VibeCAD_EditStandardFastener": "vibecad-fastener-edit.svg",
        "VibeCAD_CreateMatchingFastenerHole": "vibecad-fastener-hole.svg",
        "VibeCAD_AttachStandardFastener": "vibecad-fastener-attach.svg",
    }
    for command, icon in expected.items():
        assert command in gui
        assert icon in gui
        assert icon in cmake
        root = ET.parse(VIBECAD / icon).getroot()
        assert root.tag.endswith("svg")
        assert root.attrib["viewBox"] == "0 0 64 64"


def test_part_design_and_assembly_expose_the_integrated_group() -> None:
    part_design = (
        REPO / "src" / "Mod" / "PartDesign" / "Gui" / "Workbench.cpp"
    ).read_text(encoding="utf-8")
    assembly = (
        REPO / "src" / "Mod" / "Assembly" / "InitGui.py"
    ).read_text(encoding="utf-8")
    assert 'setCommand("Standard Components")' in part_design
    for command in (
        "VibeCAD_InsertStandardFastener",
        "VibeCAD_EditStandardFastener",
        "VibeCAD_CreateMatchingFastenerHole",
        "VibeCAD_AttachStandardFastener",
    ):
        assert command in part_design
    assert '"VibeCAD_InsertStandardFastener"' in assembly
    assert '"VibeCAD_EditStandardFastener"' in assembly
    assert '"Standard Components"' in assembly


def test_fastener_apis_expose_one_native_thread_boolean() -> None:
    from VibeCADFasteners import resolve_fastener
    from vibescript_assembly_api import AssemblyDomainAPI
    from vibescript_partdesign_api import PartDesignDomainAPI

    assert tuple(inspect.signature(resolve_fastener).parameters) == (
        "standard",
        "nominal_thread",
        "length_mm",
        "model_thread",
        "left_handed",
        "options",
    )
    for method in (
        PartDesignDomainAPI.fastener,
        AssemblyDomainAPI.fastener,
    ):
        parameter = inspect.signature(method).parameters["model_thread"]
        assert parameter.annotation in {bool, "bool"}
        assert parameter.default is True


def test_catalog_rejects_a_shadowing_user_installed_module(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    import VibeCADFasteners as fasteners

    bundled = tmp_path / "bundled" / "Fasteners"
    bundled.mkdir(parents=True)
    (bundled / "ScrewMaker.py").write_text("", encoding="utf-8")
    shadow = tmp_path / "user" / "Fasteners"
    shadow.mkdir(parents=True)
    modules = {
        name: SimpleNamespace(__file__=str(shadow / f"{name}.py"))
        for name in (
            "ScrewMaker",
            "FastenersCmd",
            "FastenerBase",
            "FSAliases",
            "FSutils",
            "FsFunctions",
        )
    }
    modules["ScrewMaker"].screwTables = {}

    monkeypatch.setattr(fasteners, "_UPSTREAM", None)
    monkeypatch.setitem(
        fasteners.sys.modules,
        "screw_maker",
        SimpleNamespace(__file__=str(shadow / "screw_maker.py")),
    )
    monkeypatch.setattr(fasteners, "_fasteners_root", lambda: bundled)
    monkeypatch.setattr(
        fasteners.importlib,
        "import_module",
        lambda name: modules[name],
    )
    with pytest.raises(
        fasteners.FastenerCatalogError,
        match="shadowing user-installed Fasteners workbench",
    ):
        fasteners._load_upstream()
