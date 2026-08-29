# SPDX-License-Identifier: LGPL-2.1-or-later

from __future__ import annotations

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[4]
POWERSHELL = REPO_ROOT / "Launch-VibeCAD-Dev.ps1"
CMD = REPO_ROOT / "Launch-VibeCAD-Dev.cmd"
ONE_CLICK_CMD = REPO_ROOT / "RUN-VIBECAD-DEV.cmd"
INIT_GUI = REPO_ROOT / "src" / "Mod" / "VibeCAD" / "InitGui.py"


def _text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def test_windows_dev_launcher_is_repo_local_and_rebuilds_current_checkout():
    script = _text(POWERSHELL)

    assert "$RepoRoot = (Resolve-Path $PSScriptRoot).Path" in script
    assert r"package\rattler-build" in script
    assert r".pixi\envs\default" in script
    assert "pixi reinstall -e default vibecad" in script
    assert "& $Pixi reinstall -e default vibecad --frozen" in script
    assert "& $Pixi install -e default --frozen" in script
    assert "[switch]$SkipRebuild" in script
    assert r"Library\bin\VibeCAD.exe" in script
    assert r"Library\bin\freecad.exe" in script
    assert "Refusing to launch an executable outside this checkout's Pixi environment." in script

    assert r"C:\Program Files" not in script
    assert "$env:ProgramFiles" not in script
    assert "$env:ProgramFiles(x86)" not in script
    assert "Get-StartApps" not in script


def test_windows_dev_launcher_initializes_pinned_submodules_before_pixi_build():
    script = _text(POWERSHELL)

    submodule_update = "git -C $RepoRoot submodule update --init --recursive"
    pixi_install = "pixi install -e default"

    assert submodule_update in script
    assert "Could not initialize the checkout's pinned Git submodules." in script
    assert script.index(submodule_update) < script.index(pixi_install)


def test_windows_dev_launcher_recovers_an_incomplete_repo_local_environment():
    script = _text(POWERSHELL)

    assert "$LaunchableExecutable" in script
    assert "if (-not $LaunchableExecutable)" in script
    assert "pixi clean --build" in script
    assert "Recovering an incomplete repo-local VibeCAD development environment" in script


def test_windows_dev_launcher_sets_visible_identity_environment():
    script = _text(POWERSHELL)

    assert '$env:VIBECAD_DEV_MODE = "1"' in script
    assert "$env:VIBECAD_DEV_SOURCE_SHA = $GitSha" in script
    assert "$env:VIBECAD_DEV_SOURCE_ROOT = $RepoRoot" in script


def test_windows_dev_launcher_scopes_agent_control_to_this_checkout():
    script = _text(POWERSHELL)

    assert r'$AgentHome = Join-Path $RepoRoot ".vibecad-dev\agent"' in script
    assert "$env:VIBECAD_AGENT_HOME = $AgentHome" in script
    assert "$GuiProcess = Start-Process" in script
    assert "-FilePath $ResolvedExecutable" in script
    assert "-PassThru" in script
    assert "Visible GUI PID:" in script
    assert "Agent endpoint:" in script
    assert 'Join-Path $AgentHome "endpoint.json"' in script


def test_windows_dev_launcher_waits_for_authenticated_agent_control_readiness():
    script = _text(POWERSHELL)

    assert "$ControlReadyTimeoutSeconds = 120" in script
    assert "function Wait-VibeCADAgentControl" in script
    assert "$EndpointInfo.LastWriteTimeUtc -ge $LaunchStartedAtUtc" in script
    assert '"Authorization" = "Bearer $Token"' in script
    assert 'Invoke-RestMethod -Uri "$($Endpoint.base_url)/v1/status"' in script
    assert '$Status.channel -eq "vibecad-agent-control"' in script
    assert "$Status.gui_up" in script
    assert "Agent control ready:" in script
    assert "Wait-VibeCADAgentControl `" in script


def test_windows_dev_launcher_prepares_the_embedded_python_runtime():
    script = _text(POWERSHELL)

    assert "function Test-VibeCADPythonRuntime" in script
    assert "function Install-VibeCADPythonRuntime" in script
    assert r"src\Mod\VibeCAD\requirements.txt" in script
    assert r"src\Mod\VibeCADAero\requirements-aero.txt" in script
    assert "PySide6" in script
    assert "jsonschema" in script
    assert "mcp_types" in script
    assert 'python.exe' in script
    assert '$env:PYTHONNOUSERSITE = "1"' in script
    assert '$env:FC_PYTHONHOME = $ResolvedEnvRoot' in script
    assert r"Library\bin" in script


def test_windows_dev_launcher_quarantines_python_user_site_packages():
    script = _text(POWERSHELL)

    assert r'$PythonUserBase = Join-Path $RepoRoot ".vibecad-dev\python-user"' in script
    assert "New-Item -ItemType Directory -Path $PythonUserBase -Force" in script
    assert '$env:PYTHONUSERBASE = $PythonUserBase' in script
    assert script.index('$env:PYTHONUSERBASE = $PythonUserBase') < script.index(
        "$GuiProcess = Start-Process"
    )


def test_double_click_cmd_only_delegates_to_repo_root_powershell_launcher():
    script = _text(CMD)

    assert 'cd /d "%~dp0"' in script
    assert '"%~dp0Launch-VibeCAD-Dev.ps1"' in script
    assert "Program Files" not in script


def test_prominent_one_click_entry_point_delegates_to_the_canonical_launcher():
    script = _text(ONE_CLICK_CMD)

    assert 'cd /d "%~dp0"' in script
    assert 'call "%~dp0Launch-VibeCAD-Dev.cmd" %*' in script
    assert "Program Files" not in script


def test_gui_bootstrap_consumes_dev_identity_without_affecting_normal_launches():
    script = _text(INIT_GUI)

    assert 'os.environ.get("VIBECAD_DEV_MODE")' in script
    assert 'os.environ.get("VIBECAD_DEV_SOURCE_SHA")' in script
    assert "VibeCADDevelopmentIdentity" in script
    assert "VibeCAD DEV" in script
    assert "QtCore.QTimer.singleShot(0, _setup_development_identity)" in script
