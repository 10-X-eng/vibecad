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


def test_windows_dev_launcher_uses_standalone_hash_provider_before_runtime_probe():
    script = _text(POWERSHELL)

    hash_helper = "function Get-VibeCADSha256"
    runtime_probe = "function Test-VibeCADQtPlatformRuntime"

    assert "[System.Security.Cryptography.SHA256]::Create()" in script
    assert "[System.IO.File]::OpenRead($Path)" in script
    assert "[System.BitConverter]::ToString($HashBytes)" in script
    assert "Get-FileHash" not in script
    assert "Import-Module Microsoft.PowerShell.Utility" not in script
    assert script.index(hash_helper) < script.index(runtime_probe)


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

    assert "$LaunchRuntime = Get-VibeCADLaunchRuntime" in script
    assert "if (-not $LaunchRuntime.Complete)" in script
    assert "pixi clean --build" in script
    assert "Recovering an incomplete repo-local VibeCAD development environment" in script


def test_windows_dev_launcher_requires_repo_local_qwindows_and_matching_qt_dlls():
    script = _text(POWERSHELL)

    assert "function Resolve-VibeCADQtLaunchRuntime" in script
    assert "function Get-VibeCADLaunchRuntime" in script
    for path in (
        r"Library\lib\qt6\plugins\platforms\qwindows.dll",
        r"Library\plugins\platforms\qwindows.dll",
        r"plugins\platforms\qwindows.dll",
    ):
        assert path in script
    for dll in (
        "Qt6Core.dll",
        "Qt6Gui.dll",
        "Qt6Widgets.dll",
    ):
        assert dll in script
    assert "qt5" not in script.lower()
    assert "Qt5" not in script
    assert "No supported repo-local qwindows.dll was found" in script
    assert "matching Qt DLL runtime is incomplete" in script
    assert "Get-VibeCADQtRuntimeIdentity" in script
    assert script.count("qt_runtime = $QtRuntimeIdentity") == 3


def test_windows_dev_launcher_load_probes_qwindows_for_recovery_and_prelaunch():
    script = _text(POWERSHELL)

    assert "function Test-VibeCADQtPlatformRuntime" in script
    assert "from PySide6 import QtGui, QtWidgets" in script
    assert 'platform_name != "windows"' in script
    assert '$env:QT_QPA_PLATFORM = "windows"' in script
    assert '$env:QT_FORCE_STDERR_LOGGING = "1"' in script
    assert "VIBECAD_EXPECTED_QWINDOWS" in script
    assert "GetModuleHandleW" in script
    assert "GetModuleFileNameW" in script
    assert "loaded_qwindows_path" in script
    assert script.count("Test-VibeCADQtPlatformRuntime -LaunchRuntime") >= 3
    initial_probe = "$InitialQtProbe = Test-VibeCADQtPlatformRuntime"
    recovery_gate = "if (-not $LaunchRuntime.Complete)"
    final_probe = "$FinalQtProbe = Test-VibeCADQtPlatformRuntime"
    initial_probe_index = script.index(initial_probe)
    assert initial_probe_index < script.index(recovery_gate, initial_probe_index)
    assert script.rindex(final_probe) < script.index("$GuiProcess = Start-Process")
    assert script.count("qt_platform_probe = $QtPlatformProbeEvidence") == 3
    assert "qt_process = [ordered]@{" in script
    assert "loaded_qwindows_path = $QtRuntimeIdentity.qwindows_path" in script
    assert "loaded_qwindows_sha256 = $QtRuntimeIdentity.qwindows_sha256" in script


def test_windows_dev_launcher_streams_qt_probe_over_stdin():
    script = _text(POWERSHELL)

    assert '$ProbeOutput = @($ProbeCode | & $PythonExecutable - 2>&1)' in script
    assert "& $PythonExecutable -c $ProbeCode" not in script


def test_windows_dev_launcher_streams_python_runtime_probe_over_stdin():
    script = _text(POWERSHELL)

    runtime_probe_start = script.index("function Get-VibeCADPythonRuntimeProbe")
    runtime_probe_end = script.index("function Install-VibeCADPythonRuntime")
    runtime_probe = script[runtime_probe_start:runtime_probe_end]

    assert "$RuntimeProbe = Get-VibeCADPythonRuntimeProbe" in runtime_probe
    assert "$RuntimeProbe | & $PythonExecutable -" in runtime_probe
    assert "& $PythonExecutable -c" not in runtime_probe
    assert "& $PythonExecutable -c (Get-VibeCADPythonRuntimeProbe)" not in script


def test_windows_dev_launcher_rejects_qwindows_resolving_outside_env_root():
    script = _text(POWERSHELL)

    assert "function Test-VibeCADPathWithinRoot" in script
    assert "Test-VibeCADPathWithinRoot `" in script
    assert "resolved outside the checkout Pixi environment" in script
    qt_resolver = script[
        script.index("function Resolve-VibeCADQtLaunchRuntime") : script.index(
            "function Get-VibeCADLaunchRuntime"
        )
    ]
    assert "Join-Path $ResolvedEnvironmentRoot" in qt_resolver
    assert "Get-ChildItem" not in qt_resolver
    assert "$env:QT_PLUGIN_PATH" not in qt_resolver
    assert "$env:QT_QPA_PLATFORM_PLUGIN_PATH" not in qt_resolver


def test_windows_dev_launcher_refuses_incomplete_skip_rebuild_before_launch():
    script = _text(POWERSHELL)

    guard = "if (-not $LaunchRuntime.Complete)"
    refusal = (
        "SkipRebuild requested, but the repo-local VibeCAD launch runtime is incomplete"
    )
    assert guard in script
    assert refusal in script
    assert script.index(guard) < script.index("$GuiProcess = Start-Process")
    assert script.index(refusal) < script.index("$GuiProcess = Start-Process")


def test_windows_dev_launcher_exports_only_exact_repo_local_qt_plugin_paths():
    script = _text(POWERSHELL)

    plugin_export = "$env:QT_PLUGIN_PATH = $LaunchRuntime.QtPluginRoot"
    platform_export = (
        "$env:QT_QPA_PLATFORM_PLUGIN_PATH = $LaunchRuntime.QtPlatformsDirectory"
    )
    assert plugin_export in script
    assert platform_export in script
    assert "$LaunchRuntime.QtDllDirectory" in script
    assert script.index(plugin_export) < script.index("$GuiProcess = Start-Process")
    assert script.index(platform_export) < script.index("$GuiProcess = Start-Process")
    final_validation = (
        "$FinalLaunchRuntime = Get-VibeCADLaunchRuntime -EnvironmentRoot "
        "$ResolvedEnvRoot"
    )
    assert final_validation in script
    assert script.index(final_validation) < script.rindex(plugin_export)
    assert script.rindex(plugin_export) < script.index("$GuiProcess = Start-Process")
    assert script.rindex(platform_export) < script.index("$GuiProcess = Start-Process")
    assert '$env:QT_PLUGIN_PATH +=' not in script
    assert '$env:QT_QPA_PLATFORM_PLUGIN_PATH +=' not in script


def test_windows_dev_launcher_rehashes_executable_after_final_probe_before_launch():
    script = _text(POWERSHELL)

    expected_hash = "$ExecutableSha256 = Get-VibeCADSha256 -Path $ResolvedExecutable"
    final_hash = (
        "$FinalExecutableSha256 = Get-VibeCADSha256 -Path "
        "$PostProbeLaunchRuntime.ExecutablePath"
    )
    launch = "$GuiProcess = Start-Process"

    assert expected_hash in script
    assert final_hash in script
    assert "The repo-local GUI executable changed during final prelaunch validation." in script
    assert script.index(expected_hash) < script.index(final_hash) < script.index(launch)


def test_release_attestation_requires_clean_superproject_and_submodules():
    script = _text(POWERSHELL)

    assert "function Assert-VibeCADReleaseCheckoutClean" in script
    assert "status --porcelain=v2 --untracked-files=all --ignore-submodules=none" in script
    assert "ReleaseAttestation requires an exact clean Git checkout" in script
    pre_build = (
        '$ReleaseEvidence["pre_build_checked_at_utc"] = '
        'Assert-VibeCADReleaseCheckoutClean -RepositoryRoot $RepoRoot'
    )
    pre_receipt = (
        '$ReleaseEvidence["pre_receipt_checked_at_utc"] = '
        'Assert-VibeCADReleaseCheckoutClean -RepositoryRoot $RepoRoot'
    )
    assert pre_build in script
    assert pre_receipt in script
    assert script.index("submodule update --init --recursive") < script.index(pre_build)
    assert script.index(pre_build) < script.index("& $Pixi install -e default --frozen")
    assert script.index(pre_receipt) < script.index("$BuildPayload = [ordered]@{")
    assert script.count("release_evidence = $ReleaseEvidence") == 3
    assert 'asserted = [bool]$ReleaseAttestation' in script
    assert '$ReleaseEvidence["clean_checkout"] = $true' in script
    assert '$ReleaseEvidence["submodule_dirt_checked"] = $true' in script
    assert "includes_submodule_dirt" not in script
    assert 'clean_checkout = $null' in script


def test_release_attestation_forces_and_records_a_cold_pixi_build():
    script = _text(POWERSHELL)

    assert 'cold_build_asserted = [bool]$ReleaseAttestation' in script
    assert "pre_build_environment_present" in script
    assert "pre_build_runtime_complete" in script
    assert "environment_absent_before_install" in script
    assert 'if ($ReleaseAttestation) {' in script
    assert "Preparing an exact cold repo-local release-attestation build" in script
    assert "ReleaseAttestation could not remove the existing Pixi environment" in script
    assert '$BuildAction = "pixi-install"' in script
    assert script.index(
        "Preparing an exact cold repo-local release-attestation build"
    ) < script.index("& $Pixi install -e default --frozen")


def test_cold_build_safely_detaches_repo_local_pixi_staging_before_install():
    script = _text(POWERSHELL)

    helper_start = script.index("function Move-VibeCADLocalBuildStagingAside")
    helper_end = script.index("function Resolve-VibeCADQtLaunchRuntime")
    helper = script[helper_start:helper_end]

    assert 'Join-Path $ResolvedPackageRoot ".pixi"' in helper
    assert 'Join-Path $PixiRoot "bld"' in helper
    assert "Test-VibeCADPathWithinRoot" in helper
    assert "[System.IO.FileAttributes]::ReparsePoint" in helper
    assert "resolved outside the repository package root" in helper
    assert "vibecad-build-staging-quarantine-" in helper
    assert '[guid]::NewGuid().ToString("N")' in helper
    assert "Move-Item -LiteralPath $LocalBuildRoot" in helper
    assert "-Destination $QuarantinePath" in helper
    assert "Remove-Item -LiteralPath $LocalBuildRoot -Recurse" not in helper

    detach = "Move-VibeCADLocalBuildStagingAside -PackageRoot $PackageRoot"
    assert script.count(detach) == 2
    release_start = script.index("if ($ReleaseAttestation) {")
    recovery_start = script.index("elseif (-not $LaunchRuntime.Complete)", release_start)
    normal_rebuild_start = script.index("elseif (-not $SkipRebuild)", recovery_start)
    release_block = script[release_start:recovery_start]
    recovery_block = script[recovery_start:normal_rebuild_start]
    for block in (release_block, recovery_block):
        assert block.index("& $Pixi clean --build") < block.index(detach)
        assert block.index(detach) < block.index("& $Pixi install -e default --frozen")
    assert "local_build_staging_reset_at_utc" in release_block
    assert "local_build_staging_quarantine_path" in release_block


def test_windows_dev_launcher_sets_visible_identity_environment():
    script = _text(POWERSHELL)

    assert '$env:VIBECAD_DEV_MODE = "1"' in script
    assert "$env:VIBECAD_DEV_SOURCE_SHA = $GitCommit" in script
    assert "$env:VIBECAD_DEV_SOURCE_TREE = $GitTree" in script
    assert "$env:VIBECAD_DEV_SOURCE_ROOT = $RepoRoot" in script
    assert "rev-parse --verify HEAD" in script
    assert 'rev-parse --verify "HEAD^{tree}"' in script
    assert "rev-parse --short" not in script


def test_windows_dev_launcher_writes_collision_safe_build_and_launch_attestations():
    script = _text(POWERSHELL)

    assert "vibecad.dev-build-attestation.v1" in script
    assert "vibecad.dev-launch-attestation.v1" in script
    assert "function Write-VibeCADAttestation" in script
    assert "[System.IO.FileMode]::CreateNew" in script
    assert r'$AttestationRoot = Join-Path $RepoRoot ".vibecad-dev\attestations"' in script
    assert "[guid]::NewGuid().ToString(\"N\")" in script
    assert "repository_root" in script
    assert "commit" in script
    assert "tree" in script
    assert "executable_sha256" in script
    for name in (
        "InitGui.py",
        "VibeCADAgentControl.py",
        "VibeCADAgentCli.py",
        "VibeCADGui.py",
        "Invoke-VibeCAD-VisibleTour.ps1",
        "Launch-VibeCAD-Dev.ps1",
    ):
        assert name in script


def test_windows_dev_launcher_refuses_skip_rebuild_in_release_attestation_mode():
    script = _text(POWERSHELL)

    assert "[switch]$ReleaseAttestation" in script
    guard = "if ($SkipRebuild -and $ReleaseAttestation)"
    assert guard in script
    assert "ReleaseAttestation cannot be combined with SkipRebuild" in script
    assert script.index(guard) < script.index("submodule update --init --recursive")


def test_windows_dev_launcher_exports_exact_attestation_contract():
    script = _text(POWERSHELL)

    for environment_name in (
        "VIBECAD_DEV_ATTESTATION_REQUIRED",
        "VIBECAD_DEV_BUILD_ATTESTATION",
        "VIBECAD_DEV_BUILD_ATTESTATION_SHA256",
        "VIBECAD_DEV_LAUNCH_ATTESTATION",
        "VIBECAD_DEV_LAUNCH_ATTESTATION_SHA256",
    ):
        assert environment_name in script
    assert "$Endpoint.process_id -ne $GuiProcess.Id" in script
    assert "$Status.process_id -ne $GuiProcess.Id" in script
    assert "$Endpoint.server_instance_id -ne $Status.server_instance_id" in script
    assert "vibecad.dev-runtime-identity.v1" in script
    assert "build_attestation_path" in script
    assert "launch_attestation_path" in script


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
    assert "$Endpoint.fail_closed" in script
    assert "$Status.fail_closed" in script
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
    assert '"openai",' in script
    assert 'importlib.util.find_spec("agents")' in script
    assert "VIBECAD_RUNTIME_REQUIREMENTS" in script
    assert "Requirement(requirement_text)" in script
    assert "installed_version not in requirement.specifier" in script
    assert "pip uninstall --yes openai openai-agents" not in script
    assert "pip uninstall --yes openai-agents" in script
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
    assert "development_runtime_identity" in script
    assert "VibeCADDevelopmentIdentity" in script
    assert "VibeCAD DEV" in script
    assert "QtCore.QTimer.singleShot(0, _setup_development_identity)" in script
