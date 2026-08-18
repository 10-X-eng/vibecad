# SPDX-License-Identifier: LGPL-2.1-or-later

"""Analyze and sibling Aero commands use a real repo-relative drone SVG."""

from __future__ import annotations

from pathlib import Path
import ast
import re

ROOT = Path(__file__).resolve().parents[1]
ICON = ROOT / "icons" / "vibecad-aero-analyze.svg"


def _string_constants(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    return [node.value for node in ast.walk(tree) if isinstance(node, ast.Constant) and isinstance(node.value, str)]


def test_drone_svg_exists_and_matches_specified_artwork():
    assert ICON.is_file()
    text = ICON.read_text(encoding="utf-8")
    assert 'aria-label="VibeCAD Aero drone"' in text
    assert 'viewBox="0 0 64 64"' in text
    assert "#4dabf7" in text
    assert "#74c0fc" in text
    assert "<image" not in text.lower()
    assert "C:\\Users" not in text
    assert not ICON.suffix.lower() == ".png"


def test_commands_point_at_repo_relative_svg_not_theme_or_windows_path():
    source = (ROOT / "Commands.py").read_text(encoding="utf-8")
    icons = (ROOT / "AeroIcons.py").read_text(encoding="utf-8")
    assert "utilities-system-monitor" not in source
    assert "C:\\Users" not in source
    assert "C:/Users" not in source
    assert "AeroIcons.aero_icon_path" in source
    assert "vibecad-aero-analyze.svg" in icons
    assert "icons" in icons
    constants = _string_constants(ROOT / "Commands.py")
    assert not any(re.match(r"^[A-Za-z]:\\", value) for value in constants)
    for command in (
        "VibeCADAero_Analyze",
        "VibeCADAero_Section",
        "VibeCADAero_VLM",
        "VibeCADAero_ExportJSBSim",
        "VibeCADAero_Report",
    ):
        assert command in source


def test_icon_path_helper_resolves_to_workbench_file():
    import AeroIcons

    resolved = Path(AeroIcons.aero_icon_path()).resolve()
    module_dir = Path(AeroIcons.__file__).resolve().parent
    assert resolved == ICON.resolve()
    assert resolved.is_file()
    assert resolved.relative_to(module_dir) == Path("icons") / AeroIcons.ICON_FILENAME


def test_cmake_installs_icon_file():
    cmake = (ROOT / "CMakeLists.txt").read_text(encoding="utf-8")
    assert "icons/vibecad-aero-analyze.svg" in cmake
    assert "Mod/VibeCADAero/icons" in cmake
