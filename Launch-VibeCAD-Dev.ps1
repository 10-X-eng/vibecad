param(
    [switch]$SkipRebuild
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

if (-not (Test-Path $VersionFile)) {
    throw "version.json was not found. Launch-VibeCAD-Dev.ps1 must remain in the VibeCAD repository root."
}

if (-not (Test-Path $PackageRoot)) {
    throw "package\rattler-build was not found. This does not look like a complete VibeCAD checkout."
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
    if (-not (Test-Path $EnvRoot)) {
        Write-Host "Creating the repo-local VibeCAD development environment..."
        & $Pixi install -e default
        if ($LASTEXITCODE -ne 0) {
            throw "pixi install -e default failed."
        }
    }
    elseif (-not $SkipRebuild) {
        Write-Host "Rebuilding this checkout into the repo-local VibeCAD environment..."
        & $Pixi reinstall -e default vibecad
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

$Candidates = @(
    (Join-Path $EnvRoot "Library\bin\VibeCAD.exe"),
    (Join-Path $EnvRoot "Library\bin\freecad.exe")
)

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

$ResolvedEnvRoot = (Resolve-Path $EnvRoot).Path.TrimEnd('\')
$ResolvedExecutable = (Resolve-Path $Executable).Path
if (-not $ResolvedExecutable.StartsWith($ResolvedEnvRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to launch an executable outside this checkout's Pixi environment."
}

$env:VIBECAD_DEV_MODE = "1"
$env:VIBECAD_DEV_SOURCE_SHA = $GitSha
$env:VIBECAD_DEV_SOURCE_ROOT = $RepoRoot

Write-Host ""
Write-Host "Launching CURRENT CHECKOUT:"
Write-Host "  $ResolvedExecutable"
Write-Host ""
Write-Host "The installed Start-menu / Program Files VibeCAD is not used by this launcher."
Write-Host ""

Start-Process -FilePath $ResolvedExecutable -WorkingDirectory $RepoRoot
