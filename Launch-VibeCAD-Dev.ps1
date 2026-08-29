# SPDX-License-Identifier: LGPL-2.1-or-later

param(
    [switch]$SkipRebuild,
    [ValidateRange(10, 600)]
    [int]$ControlReadyTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path $PSScriptRoot).Path
$PackageRoot = Join-Path $RepoRoot "package\rattler-build"
$VersionFile = Join-Path $RepoRoot "version.json"

function Resolve-VibeCADPixi {
    $command = Get-Command pixi.exe -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }

    $userInstall = Join-Path $HOME ".pixi\bin\pixi.exe"
    if (Test-Path $userInstall) {
        return $userInstall
    }

    throw "Pixi was not found. Install Pixi and reopen this launcher."
}

function Get-VibeCADPythonRuntimeProbe {
    return @'
import importlib
import sys

for module_name in (
    "PySide6",
    "anthropic",
    "keyring",
    "jsonschema",
    "mcp",
    "mcp_types",
    "tuf",
    "numpy",
    "casadi",
    "neuralfoil",
    "aerosandbox",
    "jsbsim",
):
    importlib.import_module(module_name)

numpy = importlib.import_module("numpy")
if int(numpy.__version__.split(".", 1)[0]) >= 2:
    raise RuntimeError(
        f"NumPy 2 is not compatible with this VibeCAD runtime: {numpy.__version__}"
    )

if sys.platform == "win32":
    importlib.import_module("keyring.backends.Windows")
'@
}

function Test-VibeCADPythonRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExecutable
    )

    if (-not (Test-Path $PythonExecutable)) {
        return $false
    }

    & $PythonExecutable -c (Get-VibeCADPythonRuntimeProbe) *> $null
    return $LASTEXITCODE -eq 0
}

function Install-VibeCADPythonRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PythonExecutable,
        [Parameter(Mandatory = $true)]
        [string]$RepoRoot
    )

    $Requirements = Join-Path $RepoRoot "src\Mod\VibeCAD\requirements.txt"
    $AeroRequirements = Join-Path $RepoRoot "src\Mod\VibeCADAero\requirements-aero.txt"
    foreach ($RequirementsFile in @($Requirements, $AeroRequirements)) {
        if (-not (Test-Path $RequirementsFile)) {
            throw "Required VibeCAD runtime manifest was not found: $RequirementsFile"
        }
    }

    Write-Host "Installing the checkout's pinned VibeCAD Python and Aero runtime..."
    & $PythonExecutable -m pip uninstall --yes openai openai-agents
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove retired direct OpenAI SDK packages from the repo-local environment."
    }

    & $PythonExecutable -m pip install `
        --disable-pip-version-check `
        --upgrade `
        --prefer-binary `
        -r $Requirements `
        -r $AeroRequirements
    if ($LASTEXITCODE -ne 0) {
        throw "Could not install the pinned VibeCAD Python and Aero runtime."
    }

    & $PythonExecutable -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "The repo-local VibeCAD Python runtime has dependency conflicts."
    }
}

function Wait-VibeCADAgentControl {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$GuiProcess,
        [Parameter(Mandatory = $true)]
        [string]$EndpointPath,
        [Parameter(Mandatory = $true)]
        [string]$AgentHome,
        [Parameter(Mandatory = $true)]
        [datetime]$LaunchStartedAtUtc,
        [Parameter(Mandatory = $true)]
        [int]$TimeoutSeconds
    )

    $DeadlineUtc = [datetime]::UtcNow.AddSeconds($TimeoutSeconds)
    $LastReadinessError = $null

    while ([datetime]::UtcNow -lt $DeadlineUtc) {
        $GuiProcess.Refresh()
        if ($GuiProcess.HasExited) {
            throw "The repo-local VibeCAD GUI exited before agent control became ready."
        }

        if (Test-Path $EndpointPath) {
            $EndpointInfo = Get-Item $EndpointPath
            if ($EndpointInfo.LastWriteTimeUtc -ge $LaunchStartedAtUtc) {
                try {
                    $Endpoint = Get-Content $EndpointPath -Raw | ConvertFrom-Json
                    $BaseUrl = [uri]([string]$Endpoint.base_url)
                    if ($BaseUrl.Scheme -ne "http" -or $BaseUrl.Host -ne "127.0.0.1") {
                        throw "The agent endpoint is not a 127.0.0.1 HTTP endpoint."
                    }

                    $TokenPath = [string]$Endpoint.token_path
                    if (-not (Test-Path $TokenPath)) {
                        throw "The agent token file is not ready."
                    }

                    $ResolvedAgentHome = (Resolve-Path $AgentHome).Path.TrimEnd('\')
                    $ResolvedTokenPath = (Resolve-Path $TokenPath).Path
                    if (-not $ResolvedTokenPath.StartsWith(
                        $ResolvedAgentHome + '\',
                        [System.StringComparison]::OrdinalIgnoreCase
                    )) {
                        throw "The agent token is outside this checkout's control directory."
                    }

                    $Token = (Get-Content $ResolvedTokenPath -Raw).Trim()
                    $Headers = @{ "Authorization" = "Bearer $Token" }
                    $Status = Invoke-RestMethod -Uri "$($Endpoint.base_url)/v1/status" `
                        -Method Get `
                        -Headers $Headers `
                        -TimeoutSec 5
                    if (
                        $Status.ok -and
                        $Status.channel -eq "vibecad-agent-control" -and
                        $Status.gui_up
                    ) {
                        return $Status
                    }
                    $LastReadinessError = "The status route did not report a ready GUI."
                }
                catch {
                    $LastReadinessError = $_.Exception.Message
                }
            }
        }

        Start-Sleep -Milliseconds 500
    }

    $Detail = if ($LastReadinessError) { " Last error: $LastReadinessError" } else { "" }
    throw "VibeCAD GUI PID $($GuiProcess.Id) started, but agent control did not become ready within $TimeoutSeconds seconds. The GUI was left running for inspection.$Detail"
}

if (-not (Test-Path $VersionFile)) {
    throw "version.json was not found. Launch-VibeCAD-Dev.ps1 must remain in the VibeCAD repository root."
}

if (-not (Test-Path $PackageRoot)) {
    throw "package\rattler-build was not found. This does not look like a complete VibeCAD checkout."
}

$GitModules = Join-Path $RepoRoot ".gitmodules"
if (Test-Path $GitModules) {
    Write-Host "Initializing this checkout's pinned Git submodules..."
    git -C $RepoRoot submodule update --init --recursive
    if ($LASTEXITCODE -ne 0) {
        throw "Could not initialize the checkout's pinned Git submodules."
    }
}

$Version = Get-Content $VersionFile -Raw | ConvertFrom-Json
$VersionText = "{0}.{1}.{2}{3}" -f `
    $Version.version_major, `
    $Version.version_minor, `
    $Version.version_patch, `
    $Version.version_suffix

$GitSha = (& git -C $RepoRoot rev-parse --short=12 HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or -not $GitSha) {
    throw "Could not determine the current VibeCAD Git revision."
}

$Pixi = Resolve-VibeCADPixi
$EnvRoot = Join-Path $PackageRoot ".pixi\envs\default"
$AgentHome = Join-Path $RepoRoot ".vibecad-dev\agent"
$AgentEndpoint = Join-Path $AgentHome "endpoint.json"
$PythonUserBase = Join-Path $RepoRoot ".vibecad-dev\python-user"
$Candidates = @(
    (Join-Path $EnvRoot "Library\bin\VibeCAD.exe"),
    (Join-Path $EnvRoot "Library\bin\freecad.exe")
)
$LaunchableExecutable = $Candidates |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1

Write-Host ""
Write-Host "==============================================="
Write-Host " VibeCAD DEVELOPMENT"
Write-Host " Version: $VersionText"
Write-Host " Build:   $($Version.build_version)"
Write-Host " Commit:  $GitSha"
Write-Host "==============================================="
Write-Host ""
Write-Host "Repository: $RepoRoot"
Write-Host "Pixi:       $Pixi"
Write-Host ""

Push-Location $PackageRoot
try {
    if (-not $LaunchableExecutable) {
        if (Test-Path $EnvRoot) {
            Write-Host "Recovering an incomplete repo-local VibeCAD development environment..."
            & $Pixi clean -e default
            if ($LASTEXITCODE -ne 0) {
                throw "pixi clean -e default failed while recovering the development environment."
            }
        }
        & $Pixi clean --build
        if ($LASTEXITCODE -ne 0) {
            throw "pixi clean --build failed while recovering the development environment."
        }
        Write-Host "Creating the repo-local VibeCAD development environment..."
        & $Pixi install -e default --frozen
        if ($LASTEXITCODE -ne 0) {
            throw "pixi install -e default failed."
        }
    }
    elseif (-not $SkipRebuild) {
        Write-Host "Rebuilding this checkout into the repo-local VibeCAD environment..."
        & $Pixi reinstall -e default vibecad --frozen
        if ($LASTEXITCODE -ne 0) {
            throw "pixi reinstall -e default vibecad failed."
        }
    }
    else {
        Write-Host "SkipRebuild requested; launching the existing repo-local development environment."
    }
}
finally {
    Pop-Location
}

$Executable = $Candidates |
    Where-Object { Test-Path $_ } |
    Select-Object -First 1

if (-not $Executable) {
    throw @"
The repo-local VibeCAD executable was not found.

Expected one of:
$($Candidates -join "`n")

The development build did not produce a launchable GUI.
"@
}

$PythonExecutable = Join-Path $EnvRoot "python.exe"
$null = New-Item -ItemType Directory -Path $PythonUserBase -Force
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONUSERBASE = $PythonUserBase
if (-not (Test-VibeCADPythonRuntime -PythonExecutable $PythonExecutable)) {
    Install-VibeCADPythonRuntime `
        -PythonExecutable $PythonExecutable `
        -RepoRoot $RepoRoot
}
if (-not (Test-VibeCADPythonRuntime -PythonExecutable $PythonExecutable)) {
    & $PythonExecutable -c (Get-VibeCADPythonRuntimeProbe)
    throw "The repo-local VibeCAD Python runtime is incomplete after installation."
}

$ResolvedEnvRoot = (Resolve-Path $EnvRoot).Path.TrimEnd('\')
$ResolvedExecutable = (Resolve-Path $Executable).Path
if (-not $ResolvedExecutable.StartsWith($ResolvedEnvRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to launch an executable outside this checkout's Pixi environment."
}

$env:VIBECAD_DEV_MODE = "1"
$env:VIBECAD_DEV_SOURCE_SHA = $GitSha
$env:VIBECAD_DEV_SOURCE_ROOT = $RepoRoot
$env:VIBECAD_AGENT_HOME = $AgentHome
$env:FC_PYTHONHOME = $ResolvedEnvRoot
$env:PATH = @(
    $ResolvedEnvRoot,
    (Join-Path $ResolvedEnvRoot "Library\bin"),
    (Join-Path $ResolvedEnvRoot "Scripts"),
    $env:PATH
) -join ";"

Write-Host ""
Write-Host "Launching CURRENT CHECKOUT:"
Write-Host "  $ResolvedExecutable"
Write-Host ""
Write-Host "The installed Start-menu / Program Files VibeCAD is not used by this launcher."
Write-Host ""

$LaunchStartedAtUtc = [datetime]::UtcNow
$GuiProcess = Start-Process `
    -FilePath $ResolvedExecutable `
    -WorkingDirectory $RepoRoot `
    -PassThru

Write-Host "Visible GUI PID: $($GuiProcess.Id)"
Write-Host "Agent endpoint: $AgentEndpoint"

$ControlStatus = Wait-VibeCADAgentControl `
    -GuiProcess $GuiProcess `
    -EndpointPath $AgentEndpoint `
    -AgentHome $AgentHome `
    -LaunchStartedAtUtc $LaunchStartedAtUtc `
    -TimeoutSeconds $ControlReadyTimeoutSeconds

Write-Host "Agent control ready: $($ControlStatus.endpoint.base_url)"
