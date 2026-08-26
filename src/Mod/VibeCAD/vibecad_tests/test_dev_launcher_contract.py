# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
POWERSHELL = REPO_ROOT / "Launch-VibeCAD-Dev.ps1"
CMD = REPO_ROOT / "Launch-VibeCAD-Dev.cmd"
INIT_GUI = REPO_ROOT / "src" / "Mod" / "VibeCAD" / "InitGui.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_windows_dev_launcher_is_repo_local_and_rebuilds_current_checkout():
    script = _text(POWERSHELL)

    assert "$RepoRoot = (Resolve-Path $PSScriptRoot).Path" in script
    assert r"package\rattler-build" in script
    assert r".pixi\envs\default" in script
    assert "pixi reinstall -e default vibecad" in script
    assert "[switch]$SkipRebuild" in script
    assert r"Library\bin\VibeCAD.exe" in script
    assert r"Library\bin\freecad.exe" in script
    assert "Refusing to launch an executable outside this checkout's Pixi environment." in script

    assert r"C:\Program Files" not in script
    assert "$env:ProgramFiles" not in script
    assert "$env:ProgramFiles(x86)" not in script
    assert "Get-StartApps" not in script


def test_windows_dev_launcher_sets_visible_identity_environment():
    script = _text(POWERSHELL)

    assert '$env:VIBECAD_DEV_MODE = "1"' in script
    assert "$env:VIBECAD_DEV_SOURCE_SHA = $GitSha" in script
    assert "$env:VIBECAD_DEV_SOURCE_ROOT = $RepoRoot" in script


def test_double_click_cmd_only_delegates_to_repo_root_powershell_launcher():
    script = _text(CMD)

    assert 'cd /d "%~dp0"' in script
    assert '"%~dp0Launch-VibeCAD-Dev.ps1"' in script
    assert "Program Files" not in script


def test_gui_bootstrap_consumes_dev_identity_without_affecting_normal_launches():
    source = _text(INIT_GUI)

    assert 'os.environ.get("VIBECAD_DEV_MODE")' in source
    assert 'os.environ.get("VIBECAD_DEV_SOURCE_SHA")' in source
    assert "VibeCADDevelopmentIdentity" in source
    assert "VibeCAD DEV" in source
    assert "QtCore.QTimer.singleShot(0, _setup_development_identity)" in source
