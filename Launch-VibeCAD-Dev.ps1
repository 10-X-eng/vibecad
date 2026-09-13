# SPDX-License-Identifier: LGPL-2.1-or-later

param(
    [switch]$SkipRebuild,
    [switch]$ReleaseAttestation,
    [ValidateRange(10, 600)]
    [int]$ControlReadyTimeoutSeconds = 120
)

$ErrorActionPreference = "Stop"

$RepoRoot = (Resolve-Path $PSScriptRoot).Path
$PackageRoot = Join-Path $RepoRoot "package\rattler-build"
$VersionFile = Join-Path $RepoRoot "version.json"
$AttestationRoot = Join-Path $RepoRoot ".vibecad-dev\attestations"

if ($SkipRebuild -and $ReleaseAttestation) {
    throw "ReleaseAttestation cannot be combined with SkipRebuild; rebuild the exact checkout before requesting release evidence."
}

function Get-VibeCADSha256 {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "Cannot hash missing file: $Path"
    }
    $Stream = [System.IO.File]::OpenRead($Path)
    try {
        $Hasher = [System.Security.Cryptography.SHA256]::Create()
        try {
            $HashBytes = $Hasher.ComputeHash($Stream)
        }
        finally {
            $Hasher.Dispose()
        }
    }
    finally {
        $Stream.Dispose()
    }
    return ([System.BitConverter]::ToString($HashBytes)).Replace("-", "").ToLowerInvariant()
}

function Get-VibeCADUtcTimestamp {
    return [datetime]::UtcNow.ToString("yyyy-MM-ddTHH:mm:ss.fffffffZ")
}

function Write-VibeCADAttestation {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Root,
        [Parameter(Mandatory = $true)]
        [string]$Prefix,
        [Parameter(Mandatory = $true)]
        [System.Collections.IDictionary]$Payload
    )

    $null = New-Item -ItemType Directory -Path $Root -Force
    $ResolvedRoot = (Resolve-Path -LiteralPath $Root).Path
    for ($Attempt = 0; $Attempt -lt 20; $Attempt++) {
        $Timestamp = [datetime]::UtcNow.ToString("yyyyMMddTHHmmssfffffffZ")
        $Nonce = [guid]::NewGuid().ToString("N")
        $Path = Join-Path $ResolvedRoot "$Prefix-$Timestamp-$Nonce.json"
        $Payload["attestation_path"] = $Path
        $Json = ($Payload | ConvertTo-Json -Depth 12) + "`n"
        $Bytes = [System.Text.UTF8Encoding]::new($false).GetBytes($Json)
        $Stream = $null
        try {
            $Stream = [System.IO.File]::Open(
                $Path,
                [System.IO.FileMode]::CreateNew,
                [System.IO.FileAccess]::Write,
                [System.IO.FileShare]::None
            )
            $Stream.Write($Bytes, 0, $Bytes.Length)
            $Stream.Flush($true)
            $Stream.Dispose()
            $Stream = $null
            return [pscustomobject]@{
                Path = $Path
                Sha256 = Get-VibeCADSha256 -Path $Path
            }
        }
        catch [System.IO.IOException] {
            if ($null -ne $Stream) {
                $Stream.Dispose()
                $Stream = $null
            }
            if (Test-Path -LiteralPath $Path) {
                continue
            }
            throw
        }
        finally {
            if ($null -ne $Stream) {
                $Stream.Dispose()
            }
        }
    }
    throw "Could not allocate a collision-safe $Prefix attestation path."
}

function Resolve-VibeCADInstalledModuleRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvironmentRoot
    )

    $Candidates = @(
        (Join-Path $EnvironmentRoot "Library\Mod\VibeCAD"),
        (Join-Path $EnvironmentRoot "Mod\VibeCAD")
    )
    foreach ($Candidate in $Candidates) {
        $Complete = $true
        foreach ($Name in @("InitGui.py", "VibeCADAgentControl.py", "VibeCADAgentCli.py", "VibeCADGui.py")) {
            if (-not (Test-Path -LiteralPath (Join-Path $Candidate $Name) -PathType Leaf)) {
                $Complete = $false
                break
            }
        }
        if ($Complete) {
            return (Resolve-Path -LiteralPath $Candidate).Path
        }
    }
    throw "The rebuilt Pixi environment does not contain the complete installed VibeCAD runtime module set."
}

function Get-VibeCADModuleIdentities {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot,
        [Parameter(Mandatory = $true)]
        [string]$InstalledModuleRoot
    )

    $Specs = @(
        [ordered]@{ name = "InitGui.py"; source = "src\Mod\VibeCAD\InitGui.py"; installed = "InitGui.py" },
        [ordered]@{ name = "VibeCADAgentControl.py"; source = "src\Mod\VibeCAD\VibeCADAgentControl.py"; installed = "VibeCADAgentControl.py" },
        [ordered]@{ name = "VibeCADAgentCli.py"; source = "src\Mod\VibeCAD\VibeCADAgentCli.py"; installed = "VibeCADAgentCli.py" },
        [ordered]@{ name = "VibeCADGui.py"; source = "src\Mod\VibeCAD\VibeCADGui.py"; installed = "VibeCADGui.py" },
        [ordered]@{ name = "Invoke-VibeCAD-VisibleTour.ps1"; source = "Invoke-VibeCAD-VisibleTour.ps1"; installed = $null },
        [ordered]@{ name = "Launch-VibeCAD-Dev.ps1"; source = "Launch-VibeCAD-Dev.ps1"; installed = $null }
    )
    $Identities = @()
    foreach ($Spec in $Specs) {
        $SourcePath = (Resolve-Path -LiteralPath (Join-Path $RepositoryRoot $Spec.source)).Path
        $SourceSha256 = Get-VibeCADSha256 -Path $SourcePath
        $InstalledPath = $null
        $InstalledSha256 = $null
        if ($null -ne $Spec.installed) {
            $InstalledPath = (Resolve-Path -LiteralPath (Join-Path $InstalledModuleRoot $Spec.installed)).Path
            $InstalledSha256 = Get-VibeCADSha256 -Path $InstalledPath
            if ($SourceSha256 -ne $InstalledSha256) {
                throw "The installed $($Spec.name) does not exactly match the current checkout source."
            }
        }
        $Identities += [ordered]@{
            name = $Spec.name
            source_path = $SourcePath
            source_sha256 = $SourceSha256
            installed_path = $InstalledPath
            installed_sha256 = $InstalledSha256
        }
    }
    return $Identities
}

function Test-VibeCADPathWithinRoot {
    param(
        [Parameter(Mandatory = $true)]
        [string]$ResolvedRoot,
        [Parameter(Mandatory = $true)]
        [string]$ResolvedPath
    )

    $CanonicalRoot = [System.IO.Path]::GetFullPath($ResolvedRoot).TrimEnd(
        [char[]]@('\', '/')
    )
    $CanonicalPath = [System.IO.Path]::GetFullPath($ResolvedPath)
    $RootPrefix = $CanonicalRoot + [System.IO.Path]::DirectorySeparatorChar
    return (
        $CanonicalPath.Equals(
            $CanonicalRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        $CanonicalPath.StartsWith(
            $RootPrefix,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )
}

function Move-VibeCADLocalBuildStagingAside {
    param(
        [Parameter(Mandatory = $true)]
        [string]$PackageRoot
    )

    if (-not (Test-Path -LiteralPath $PackageRoot -PathType Container)) {
        throw "The repository package root does not exist: $PackageRoot"
    }
    $ResolvedPackageRoot = (Resolve-Path -LiteralPath $PackageRoot).Path
    $PixiRoot = Join-Path $ResolvedPackageRoot ".pixi"
    if (-not (Test-Path -LiteralPath $PixiRoot -PathType Container)) {
        return $null
    }
    $PixiRoot = (Resolve-Path -LiteralPath $PixiRoot).Path
    $PixiRootItem = Get-Item -LiteralPath $PixiRoot -Force
    if (
        $PixiRoot.Equals(
            $ResolvedPackageRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not (Test-VibeCADPathWithinRoot `
            -ResolvedRoot $ResolvedPackageRoot `
            -ResolvedPath $PixiRoot) -or
        ($PixiRootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) `
            -ne 0
    ) {
        throw "The repo-local Pixi root resolved outside the repository package root or through a reparse point: $PixiRoot"
    }

    $LocalBuildRoot = Join-Path $PixiRoot "bld"
    if (-not (Test-Path -LiteralPath $LocalBuildRoot)) {
        return $null
    }
    $LocalBuildRoot = (Resolve-Path -LiteralPath $LocalBuildRoot).Path
    $LocalBuildRootItem = Get-Item -LiteralPath $LocalBuildRoot -Force
    if (
        $LocalBuildRoot.Equals(
            $PixiRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not (Test-VibeCADPathWithinRoot `
            -ResolvedRoot $PixiRoot `
            -ResolvedPath $LocalBuildRoot) -or
        ($LocalBuildRootItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) `
            -ne 0
    ) {
        throw "The repo-local Pixi build staging path resolved outside the repository package root or through a reparse point: $LocalBuildRoot"
    }

    $QuarantineName = "vibecad-build-staging-quarantine-{0}-{1}" -f `
        [DateTime]::UtcNow.ToString("yyyyMMddTHHmmssfffffffZ"), `
        [guid]::NewGuid().ToString("N")
    $QuarantinePath = [System.IO.Path]::GetFullPath(
        (Join-Path $PixiRoot $QuarantineName)
    )
    if (
        $QuarantinePath.Equals(
            $PixiRoot,
            [System.StringComparison]::OrdinalIgnoreCase
        ) -or
        -not (Test-VibeCADPathWithinRoot `
            -ResolvedRoot $PixiRoot `
            -ResolvedPath $QuarantinePath) -or
        (Test-Path -LiteralPath $QuarantinePath)
    ) {
        throw "The repo-local Pixi build-staging quarantine destination is not a new child of the verified Pixi root: $QuarantinePath"
    }

    $AttemptLimit = 5
    for ($Attempt = 1; $Attempt -le $AttemptLimit; $Attempt += 1) {
        try {
            if (
                -not (Test-Path -LiteralPath $LocalBuildRoot) -and
                (Test-Path -LiteralPath $QuarantinePath -PathType Container)
            ) {
                return $QuarantinePath
            }
            Move-Item -LiteralPath $LocalBuildRoot `
                -Destination $QuarantinePath `
                -ErrorAction Stop
            if (
                (Test-Path -LiteralPath $LocalBuildRoot) -or
                -not (Test-Path -LiteralPath $QuarantinePath -PathType Container)
            ) {
                throw "The repo-local Pixi build staging directory was not atomically detached from the active build path."
            }
            return $QuarantinePath
        }
        catch {
            if ($Attempt -ge $AttemptLimit) {
                throw "Could not safely detach repo-local Pixi build staging after $AttemptLimit attempts: $($_.Exception.Message)"
            }
            Start-Sleep -Milliseconds (250 * $Attempt)
        }
    }
}

function Resolve-VibeCADQtLaunchRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvironmentRoot
    )

    if (-not (Test-Path -LiteralPath $EnvironmentRoot -PathType Container)) {
        return [pscustomobject]@{
            Complete = $false
            Diagnostic = "The checkout Pixi environment does not exist: $EnvironmentRoot"
        }
    }

    $ResolvedEnvironmentRoot = (Resolve-Path -LiteralPath $EnvironmentRoot).Path
    $Layouts = @(
        [ordered]@{
            QtMajor = 6
            PluginRelativePath = "Library\lib\qt6\plugins\platforms\qwindows.dll"
            DllDirectoryRelativePath = "Library\bin"
            RequiredDlls = @("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll")
        },
        [ordered]@{
            QtMajor = 6
            PluginRelativePath = "Library\lib\qt6\plugins\platforms\qwindows.dll"
            DllDirectoryRelativePath = "Library\lib\qt6\bin"
            RequiredDlls = @("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll")
        },
        [ordered]@{
            QtMajor = 6
            PluginRelativePath = "Library\plugins\platforms\qwindows.dll"
            DllDirectoryRelativePath = "Library\bin"
            RequiredDlls = @("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll")
        },
        [ordered]@{
            QtMajor = 6
            PluginRelativePath = "plugins\platforms\qwindows.dll"
            DllDirectoryRelativePath = "bin"
            RequiredDlls = @("Qt6Core.dll", "Qt6Gui.dll", "Qt6Widgets.dll")
        }
    )
    $FoundPlugin = $false
    $IncompleteRuntimeDetails = @()
    $ExpectedPluginPaths = @()

    foreach ($Layout in $Layouts) {
        $PluginCandidate = Join-Path $ResolvedEnvironmentRoot $Layout.PluginRelativePath
        if ($PluginCandidate -notin $ExpectedPluginPaths) {
            $ExpectedPluginPaths += $PluginCandidate
        }
        if (-not (Test-Path -LiteralPath $PluginCandidate -PathType Leaf)) {
            continue
        }
        $FoundPlugin = $true
        $ResolvedQWindows = (Resolve-Path -LiteralPath $PluginCandidate).Path
        if (-not (Test-VibeCADPathWithinRoot `
            -ResolvedRoot $ResolvedEnvironmentRoot `
            -ResolvedPath $ResolvedQWindows)) {
            return [pscustomobject]@{
                Complete = $false
                Diagnostic = "The Qt platform plugin resolved outside the checkout Pixi environment: $ResolvedQWindows"
            }
        }

        $PlatformsDirectory = (Split-Path -Parent $ResolvedQWindows)
        $PluginRoot = (Split-Path -Parent $PlatformsDirectory)
        if (
            -not (Test-VibeCADPathWithinRoot `
                -ResolvedRoot $ResolvedEnvironmentRoot `
                -ResolvedPath $PlatformsDirectory) -or
            -not (Test-VibeCADPathWithinRoot `
                -ResolvedRoot $ResolvedEnvironmentRoot `
                -ResolvedPath $PluginRoot)
        ) {
            return [pscustomobject]@{
                Complete = $false
                Diagnostic = "The Qt plugin directories resolved outside the checkout Pixi environment: $PluginRoot"
            }
        }

        $DllDirectoryCandidate = Join-Path `
            $ResolvedEnvironmentRoot `
            $Layout.DllDirectoryRelativePath
        if (-not (Test-Path -LiteralPath $DllDirectoryCandidate -PathType Container)) {
            $IncompleteRuntimeDetails += "Qt$($Layout.QtMajor) plugin $ResolvedQWindows requires missing DLL directory $DllDirectoryCandidate"
            continue
        }
        $ResolvedDllDirectory = (Resolve-Path -LiteralPath $DllDirectoryCandidate).Path
        if (-not (Test-VibeCADPathWithinRoot `
            -ResolvedRoot $ResolvedEnvironmentRoot `
            -ResolvedPath $ResolvedDllDirectory)) {
            return [pscustomobject]@{
                Complete = $false
                Diagnostic = "The Qt DLL directory resolved outside the checkout Pixi environment: $ResolvedDllDirectory"
            }
        }

        $ResolvedQtDlls = @()
        $MissingQtDlls = @()
        foreach ($DllName in $Layout.RequiredDlls) {
            $DllCandidate = Join-Path $ResolvedDllDirectory $DllName
            if (-not (Test-Path -LiteralPath $DllCandidate -PathType Leaf)) {
                $MissingQtDlls += $DllCandidate
                continue
            }
            $ResolvedDll = (Resolve-Path -LiteralPath $DllCandidate).Path
            if (-not (Test-VibeCADPathWithinRoot `
                -ResolvedRoot $ResolvedEnvironmentRoot `
                -ResolvedPath $ResolvedDll)) {
                return [pscustomobject]@{
                    Complete = $false
                    Diagnostic = "A required Qt DLL resolved outside the checkout Pixi environment: $ResolvedDll"
                }
            }
            $ResolvedQtDlls += $ResolvedDll
        }
        if ($MissingQtDlls.Count -gt 0) {
            $IncompleteRuntimeDetails += "Qt$($Layout.QtMajor) plugin $ResolvedQWindows is missing: $($MissingQtDlls -join ', ')"
            continue
        }

        return [pscustomobject]@{
            Complete = $true
            Diagnostic = $null
            QtMajor = [int]$Layout.QtMajor
            QtPluginRoot = $PluginRoot
            QtPlatformsDirectory = $PlatformsDirectory
            QWindowsPath = $ResolvedQWindows
            QtDllDirectory = $ResolvedDllDirectory
            QtDllPaths = @($ResolvedQtDlls)
        }
    }

    if (-not $FoundPlugin) {
        return [pscustomobject]@{
            Complete = $false
            Diagnostic = "No supported repo-local qwindows.dll was found. Expected one of: $($ExpectedPluginPaths -join ', ')"
        }
    }
    return [pscustomobject]@{
        Complete = $false
        Diagnostic = "A repo-local qwindows.dll was found, but its matching Qt DLL runtime is incomplete. $($IncompleteRuntimeDetails -join '; ')"
    }
}

function Get-VibeCADLaunchRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [string]$EnvironmentRoot
    )

    if (-not (Test-Path -LiteralPath $EnvironmentRoot -PathType Container)) {
        return [pscustomobject]@{
            Complete = $false
            Diagnostic = "The checkout Pixi environment does not exist: $EnvironmentRoot"
        }
    }
    $ResolvedEnvironmentRoot = (Resolve-Path -LiteralPath $EnvironmentRoot).Path
    $ExecutableCandidates = @(
        (Join-Path $ResolvedEnvironmentRoot "Library\bin\VibeCAD.exe"),
        (Join-Path $ResolvedEnvironmentRoot "Library\bin\freecad.exe")
    )
    $ResolvedExecutable = $null
    foreach ($ExecutableCandidate in $ExecutableCandidates) {
        if (-not (Test-Path -LiteralPath $ExecutableCandidate -PathType Leaf)) {
            continue
        }
        $Candidate = (Resolve-Path -LiteralPath $ExecutableCandidate).Path
        if (-not (Test-VibeCADPathWithinRoot `
            -ResolvedRoot $ResolvedEnvironmentRoot `
            -ResolvedPath $Candidate)) {
            return [pscustomobject]@{
                Complete = $false
                Diagnostic = "The GUI executable resolved outside the checkout Pixi environment: $Candidate"
            }
        }
        $ResolvedExecutable = $Candidate
        break
    }
    if (-not $ResolvedExecutable) {
        return [pscustomobject]@{
            Complete = $false
            Diagnostic = "No repo-local VibeCAD GUI executable was found. Expected one of: $($ExecutableCandidates -join ', ')"
        }
    }

    $QtRuntime = Resolve-VibeCADQtLaunchRuntime -EnvironmentRoot $ResolvedEnvironmentRoot
    if (-not $QtRuntime.Complete) {
        return [pscustomobject]@{
            Complete = $false
            Diagnostic = $QtRuntime.Diagnostic
        }
    }
    return [pscustomobject]@{
        Complete = $true
        Diagnostic = $null
        EnvironmentRoot = $ResolvedEnvironmentRoot
        ExecutablePath = $ResolvedExecutable
        QtMajor = $QtRuntime.QtMajor
        QtPluginRoot = $QtRuntime.QtPluginRoot
        QtPlatformsDirectory = $QtRuntime.QtPlatformsDirectory
        QWindowsPath = $QtRuntime.QWindowsPath
        QtDllDirectory = $QtRuntime.QtDllDirectory
        QtDllPaths = @($QtRuntime.QtDllPaths)
    }
}

function Get-VibeCADQtRuntimeIdentity {
    param(
        [Parameter(Mandatory = $true)]
        [object]$LaunchRuntime
    )

    if (-not $LaunchRuntime.Complete) {
        throw "Cannot identify an incomplete Qt launch runtime. $($LaunchRuntime.Diagnostic)"
    }
    $Dlls = @(
        foreach ($DllPath in $LaunchRuntime.QtDllPaths) {
            [ordered]@{
                name = Split-Path -Leaf $DllPath
                path = $DllPath
                sha256 = Get-VibeCADSha256 -Path $DllPath
            }
        }
    )
    return [ordered]@{
        qt_major = [int]$LaunchRuntime.QtMajor
        plugin_root = $LaunchRuntime.QtPluginRoot
        platforms_directory = $LaunchRuntime.QtPlatformsDirectory
        qwindows_path = $LaunchRuntime.QWindowsPath
        qwindows_sha256 = Get-VibeCADSha256 -Path $LaunchRuntime.QWindowsPath
        dll_directory = $LaunchRuntime.QtDllDirectory
        dlls = $Dlls
    }
}

function Test-VibeCADQtPlatformRuntime {
    param(
        [Parameter(Mandatory = $true)]
        [object]$LaunchRuntime
    )

    $CheckedAtUtc = Get-VibeCADUtcTimestamp
    if (-not $LaunchRuntime.Complete) {
        return [pscustomobject]@{
            Complete = $false
            Diagnostic = "Cannot load-probe an incomplete Qt launch runtime. $($LaunchRuntime.Diagnostic)"
            CheckedAtUtc = $CheckedAtUtc
            Platform = $null
            PythonExecutable = $null
            PythonSha256 = $null
        }
    }

    $PythonCandidate = Join-Path $LaunchRuntime.EnvironmentRoot "python.exe"
    if (-not (Test-Path -LiteralPath $PythonCandidate -PathType Leaf)) {
        return [pscustomobject]@{
            Complete = $false
            Diagnostic = "The repo-local Python executable required for the Qt platform load probe is missing: $PythonCandidate"
            CheckedAtUtc = $CheckedAtUtc
            Platform = $null
            PythonExecutable = $PythonCandidate
            PythonSha256 = $null
        }
    }
    $PythonExecutable = (Resolve-Path -LiteralPath $PythonCandidate).Path
    if (-not (Test-VibeCADPathWithinRoot `
        -ResolvedRoot $LaunchRuntime.EnvironmentRoot `
        -ResolvedPath $PythonExecutable)) {
        return [pscustomobject]@{
            Complete = $false
            Diagnostic = "The Qt platform load-probe Python executable resolved outside the checkout Pixi environment: $PythonExecutable"
            CheckedAtUtc = $CheckedAtUtc
            Platform = $null
            PythonExecutable = $PythonExecutable
            PythonSha256 = $null
        }
    }

    $ProbeCode = @'
import ctypes
from ctypes import wintypes
import json
import os

from PySide6 import QtGui, QtWidgets

app = QtWidgets.QApplication(["VibeCADQtPlatformProbe"])
platform_name = str(QtGui.QGuiApplication.platformName()).strip().lower()
if platform_name != "windows":
    raise RuntimeError(f"Expected the Windows Qt platform plugin, got {platform_name!r}")

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
kernel32.GetModuleHandleW.restype = wintypes.HMODULE
kernel32.GetModuleFileNameW.argtypes = [
    wintypes.HMODULE,
    wintypes.LPWSTR,
    wintypes.DWORD,
]
kernel32.GetModuleFileNameW.restype = wintypes.DWORD
module_handle = kernel32.GetModuleHandleW("qwindows.dll")
if not module_handle:
    raise ctypes.WinError(ctypes.get_last_error())
module_path_buffer = ctypes.create_unicode_buffer(32768)
module_path_length = kernel32.GetModuleFileNameW(
    module_handle,
    module_path_buffer,
    len(module_path_buffer),
)
if not module_path_length or module_path_length >= len(module_path_buffer):
    raise ctypes.WinError(ctypes.get_last_error())
loaded_qwindows_path = os.path.normcase(os.path.realpath(module_path_buffer.value))
expected_qwindows_path = os.path.normcase(
    os.path.realpath(os.environ["VIBECAD_EXPECTED_QWINDOWS"])
)
if loaded_qwindows_path != expected_qwindows_path:
    raise RuntimeError(
        "Qt loaded an unexpected qwindows.dll: "
        f"{loaded_qwindows_path!r} != {expected_qwindows_path!r}"
    )
app.quit()
print(
    json.dumps(
        {
            "platform": platform_name,
            "loaded_qwindows_path": loaded_qwindows_path,
        },
        separators=(",", ":"),
    )
)
'@

    $EnvironmentNames = @(
        "QT_PLUGIN_PATH",
        "QT_QPA_PLATFORM_PLUGIN_PATH",
        "QT_QPA_PLATFORM",
        "QT_FORCE_STDERR_LOGGING",
        "VIBECAD_EXPECTED_QWINDOWS",
        "PYTHONNOUSERSITE",
        "PATH"
    )
    $PreviousEnvironment = @{}
    foreach ($Name in $EnvironmentNames) {
        $EnvironmentPath = "Env:$Name"
        $Present = Test-Path -LiteralPath $EnvironmentPath
        $PreviousEnvironment[$Name] = [pscustomobject]@{
            Present = $Present
            Value = if ($Present) { (Get-Item -LiteralPath $EnvironmentPath).Value } else { $null }
        }
    }

    $ProbeOutput = @()
    $ProbeExitCode = -1
    $ProbeInvocationError = $null
    try {
        $env:QT_PLUGIN_PATH = $LaunchRuntime.QtPluginRoot
        $env:QT_QPA_PLATFORM_PLUGIN_PATH = $LaunchRuntime.QtPlatformsDirectory
        $env:QT_QPA_PLATFORM = "windows"
        $env:QT_FORCE_STDERR_LOGGING = "1"
        $env:VIBECAD_EXPECTED_QWINDOWS = $LaunchRuntime.QWindowsPath
        $env:PYTHONNOUSERSITE = "1"
        $env:PATH = @(
            $LaunchRuntime.QtDllDirectory,
            $LaunchRuntime.EnvironmentRoot,
            (Join-Path $LaunchRuntime.EnvironmentRoot "Library\bin"),
            (Join-Path $LaunchRuntime.EnvironmentRoot "Scripts"),
            $PreviousEnvironment["PATH"].Value
        ) -join ";"

        $ProbeOutput = @($ProbeCode | & $PythonExecutable - 2>&1)
        $ProbeExitCode = $LASTEXITCODE
    }
    catch {
        $ProbeInvocationError = $_.Exception.Message
        $ProbeOutput = @($ProbeOutput) + @($_.ToString())
    }
    finally {
        foreach ($Name in $EnvironmentNames) {
            $EnvironmentPath = "Env:$Name"
            $Previous = $PreviousEnvironment[$Name]
            if ($Previous.Present) {
                Set-Item -LiteralPath $EnvironmentPath -Value $Previous.Value
            }
            else {
                Remove-Item -LiteralPath $EnvironmentPath -ErrorAction SilentlyContinue
            }
        }
    }

    $OutputLines = @(
        $ProbeOutput |
            ForEach-Object { ([string]$_).Trim() } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    $ProbeResult = $null
    if ($OutputLines.Count -gt 0 -and $null -eq $ProbeInvocationError) {
        try {
            $ProbeResult = $OutputLines[$OutputLines.Count - 1] | ConvertFrom-Json
        }
        catch {
            $ProbeInvocationError = "The Qt load probe did not return its required JSON result: $($_.Exception.Message)"
        }
    }
    $Platform = if ($null -ne $ProbeResult) {
        ([string]$ProbeResult.platform).Trim().ToLowerInvariant()
    }
    else {
        $null
    }
    $LoadedQWindowsPath = if ($null -ne $ProbeResult) {
        ([string]$ProbeResult.loaded_qwindows_path).Trim()
    }
    else {
        $null
    }
    $ExpectedQWindowsPath = $LaunchRuntime.QWindowsPath
    $LoadedExpectedQWindows = (
        -not [string]::IsNullOrWhiteSpace($LoadedQWindowsPath) -and
        $LoadedQWindowsPath.Equals(
            $ExpectedQWindowsPath,
            [System.StringComparison]::OrdinalIgnoreCase
        )
    )
    if (
        $ProbeExitCode -ne 0 -or
        $null -ne $ProbeInvocationError -or
        $Platform -ne "windows" -or
        -not $LoadedExpectedQWindows
    ) {
        $Detail = ($OutputLines | Select-Object -Last 20) -join " | "
        if ([string]::IsNullOrWhiteSpace($Detail)) {
            $Detail = if ($ProbeInvocationError) { $ProbeInvocationError } else { "No diagnostic output was produced." }
        }
        return [pscustomobject]@{
            Complete = $false
            Diagnostic = "The repo-local Qt Windows platform plugin failed its QApplication load probe (exit $ProbeExitCode). $Detail"
            CheckedAtUtc = $CheckedAtUtc
            Platform = $Platform
            PythonExecutable = $PythonExecutable
            PythonSha256 = Get-VibeCADSha256 -Path $PythonExecutable
            LoadedQWindowsPath = $LoadedQWindowsPath
            LoadedQWindowsSha256 = $null
        }
    }

    return [pscustomobject]@{
        Complete = $true
        Diagnostic = $null
        CheckedAtUtc = $CheckedAtUtc
        Platform = $Platform
        PythonExecutable = $PythonExecutable
        PythonSha256 = Get-VibeCADSha256 -Path $PythonExecutable
        LoadedQWindowsPath = $LoadedQWindowsPath
        LoadedQWindowsSha256 = Get-VibeCADSha256 -Path $LoadedQWindowsPath
    }
}

function Assert-VibeCADReleaseCheckoutClean {
    param(
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot
    )

    $GitStatus = @(& git -C $RepositoryRoot status --porcelain=v2 --untracked-files=all --ignore-submodules=none 2>&1)
    $GitStatusExitCode = $LASTEXITCODE
    if ($GitStatusExitCode -ne 0) {
        $Detail = ($GitStatus | ForEach-Object { [string]$_ }) -join "`n"
        throw "ReleaseAttestation could not verify the exact Git checkout state. git status exited with $GitStatusExitCode. $Detail"
    }
    $DirtyEntries = @(
        $GitStatus |
            ForEach-Object { [string]$_ } |
            Where-Object { -not [string]::IsNullOrWhiteSpace($_) }
    )
    if ($DirtyEntries.Count -gt 0) {
        $Preview = ($DirtyEntries | Select-Object -First 20) -join "`n"
        throw "ReleaseAttestation requires an exact clean Git checkout, including submodule dirt. git status reported:`n$Preview"
    }
    return Get-VibeCADUtcTimestamp
}

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
import importlib.util
import importlib.metadata
import json
import os
import sys

from packaging.requirements import Requirement

for module_name in (
    "PySide6",
    "anthropic",
    "keyring",
    "jsonschema",
    "mcp",
    "mcp_types",
    "openai",
    "tuf",
    "numpy",
    "casadi",
    "neuralfoil",
    "aerosandbox",
    "jsbsim",
):
    importlib.import_module(module_name)

if importlib.util.find_spec("agents") is not None:
    raise RuntimeError("The retired OpenAI Agents SDK is present in this runtime.")

requirement_paths = json.loads(os.environ["VIBECAD_RUNTIME_REQUIREMENTS"])
for requirement_path in requirement_paths:
    with open(requirement_path, encoding="utf-8") as requirement_file:
        for raw_line in requirement_file:
            requirement_text = raw_line.split("#", 1)[0].strip()
            if not requirement_text:
                continue
            requirement = Requirement(requirement_text)
            if requirement.marker is not None and not requirement.marker.evaluate():
                continue
            installed_version = importlib.metadata.version(requirement.name)
            if requirement.specifier and installed_version not in requirement.specifier:
                raise RuntimeError(
                    f"{requirement.name} {installed_version} does not satisfy "
                    f"{requirement.specifier}."
                )

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
        [string]$PythonExecutable,
        [switch]$EmitDiagnostic
    )

    if (-not (Test-Path $PythonExecutable)) {
        return $false
    }

    $RuntimeProbe = Get-VibeCADPythonRuntimeProbe
    $RuntimeProbeOutput = @()
    $RuntimeProbeExitCode = -1
    $PreviousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $RuntimeProbeOutput = @($RuntimeProbe | & $PythonExecutable - 2>&1)
        $RuntimeProbeExitCode = $LASTEXITCODE
    }
    catch {
        $RuntimeProbeOutput = @($RuntimeProbeOutput) + @($_.ToString())
        if ($null -ne $LASTEXITCODE) {
            $RuntimeProbeExitCode = $LASTEXITCODE
        }
    }
    finally {
        $ErrorActionPreference = $PreviousErrorActionPreference
    }
    if ($EmitDiagnostic) {
        foreach ($Line in $RuntimeProbeOutput) {
            Write-Host ([string]$Line)
        }
    }
    return $RuntimeProbeExitCode -eq 0
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
    & $PythonExecutable -m pip uninstall --yes openai-agents
    if ($LASTEXITCODE -ne 0) {
        throw "Could not remove the retired OpenAI Agents SDK from the repo-local environment."
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
        [int]$TimeoutSeconds,
        [Parameter(Mandatory = $false)]
        [AllowNull()]
        [object]$ExpectedRuntimeIdentity
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
                    if ($Endpoint.fail_closed -isnot [bool] -or -not [bool]$Endpoint.fail_closed) {
                        throw "The development endpoint is not running in fail-closed mode."
                    }
                    if ($Endpoint.process_id -ne $GuiProcess.Id) {
                        throw "The agent endpoint belongs to PID $($Endpoint.process_id), not launched GUI PID $($GuiProcess.Id)."
                    }
if ([string]::IsNullOrWhiteSpace([string]$Endpoint.server_instance_id)) {
    throw "The agent endpoint does not include a random per-server-start instance ID."
}
                    if ([string]::IsNullOrWhiteSpace([string]$Endpoint.server_started_at_utc)) {
                        throw "The agent endpoint has no server start timestamp."
                    }

                    $TokenPath = [string]$Endpoint.token_path
                    if (-not (Test-Path $TokenPath)) {
                        throw "The agent token file is not ready."
                    }

                    $ResolvedAgentHome = (Resolve-Path $AgentHome).Path.TrimEnd('\')
                    $ResolvedTokenPath = (Resolve-Path $TokenPath).Path
                    $ExpectedTokenPath = [System.IO.Path]::GetFullPath(
                        (Join-Path $ResolvedAgentHome "token")
                    )
                    if (-not $ResolvedTokenPath.Equals(
                        $ExpectedTokenPath,
                        [System.StringComparison]::OrdinalIgnoreCase
                    )) {
                        throw "The endpoint token path is not the exact checkout-scoped token path."
                    }

                    $Token = (Get-Content $ResolvedTokenPath -Raw).Trim()
                    $Headers = @{ "Authorization" = "Bearer $Token" }
                    $Status = Invoke-RestMethod -Uri "$($Endpoint.base_url)/v1/status" `
                        -Method Get `
                        -Headers $Headers `
                        -TimeoutSec 5
                    if ($Status.process_id -ne $GuiProcess.Id) {
                        throw "The authenticated status belongs to PID $($Status.process_id), not launched GUI PID $($GuiProcess.Id)."
                    }
                    if ($Status.fail_closed -isnot [bool] -or -not [bool]$Status.fail_closed) {
                        throw "The authenticated development status is not fail-closed."
                    }
                    if ($Endpoint.server_instance_id -ne $Status.server_instance_id) {
                        throw "The endpoint and authenticated status report different server instances."
                    }
                    if ($Endpoint.server_started_at_utc -ne $Status.server_started_at_utc) {
                        throw "The endpoint and authenticated status report different server start times."
                    }
                    $EndpointIdentityJson = $Endpoint.runtime_identity | ConvertTo-Json -Compress -Depth 12
                    $StatusIdentityJson = $Status.runtime_identity | ConvertTo-Json -Compress -Depth 12
                    if ($EndpointIdentityJson -ne $StatusIdentityJson) {
                        throw "The endpoint and authenticated status report different runtime identities."
                    }
                    if ($null -eq $ExpectedRuntimeIdentity) {
                        if ($null -ne $Endpoint.runtime_identity -or $null -ne $Status.runtime_identity) {
                            throw "An unattested SkipRebuild launch reported a release runtime identity."
                        }
                    }
                    else {
                        $ExpectedIdentityJson = $ExpectedRuntimeIdentity | ConvertTo-Json -Compress -Depth 12
                        if ($EndpointIdentityJson -ne $ExpectedIdentityJson) {
                            throw "The live runtime identity does not match this launch's exact attestations."
                        }
                        if ($Status.runtime_identity.schema -ne "vibecad.dev-runtime-identity.v1") {
                            throw "The live runtime identity schema is not vibecad.dev-runtime-identity.v1."
                        }
                    }
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

$GitRepositoryRoot = (& git -C $RepoRoot rev-parse --show-toplevel).Trim()
if ($LASTEXITCODE -ne 0 -or -not $GitRepositoryRoot) {
    throw "Could not determine the canonical VibeCAD repository root."
}
$GitRepositoryRoot = (Resolve-Path -LiteralPath $GitRepositoryRoot).Path
if (-not $GitRepositoryRoot.Equals($RepoRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Launch-VibeCAD-Dev.ps1 must run from the canonical root of its own Git checkout."
}
$RepoRoot = $GitRepositoryRoot

$GitModules = Join-Path $RepoRoot ".gitmodules"
if (Test-Path $GitModules) {
    Write-Host "Initializing this checkout's pinned Git submodules..."
    git -C $RepoRoot submodule update --init --recursive
    if ($LASTEXITCODE -ne 0) {
        throw "Could not initialize the checkout's pinned Git submodules."
    }
}

$ReleaseEvidence = [ordered]@{
    asserted = [bool]$ReleaseAttestation
    clean_checkout = $null
    submodule_dirt_checked = $null
    git_status_mode = $null
    cold_build_asserted = [bool]$ReleaseAttestation
    pre_build_environment_present = $false
    pre_build_runtime_complete = $false
    environment_absent_before_install = $null
    pre_build_checked_at_utc = $null
    environment_cleaned_at_utc = $null
    build_cache_cleaned_at_utc = $null
    local_build_staging_reset_at_utc = $null
    local_build_staging_quarantine_path = $null
    pre_receipt_checked_at_utc = $null
}
if ($ReleaseAttestation) {
    $ReleaseEvidence["pre_build_checked_at_utc"] = Assert-VibeCADReleaseCheckoutClean -RepositoryRoot $RepoRoot
    $ReleaseEvidence["clean_checkout"] = $true
    $ReleaseEvidence["submodule_dirt_checked"] = $true
    $ReleaseEvidence["git_status_mode"] = "--porcelain=v2 --untracked-files=all --ignore-submodules=none"
}

$Version = Get-Content $VersionFile -Raw | ConvertFrom-Json
$VersionText = "{0}.{1}.{2}{3}" -f `
    $Version.version_major, `
    $Version.version_minor, `
    $Version.version_patch, `
    $Version.version_suffix

$GitCommit = (& git -C $RepoRoot rev-parse --verify HEAD).Trim()
if ($LASTEXITCODE -ne 0 -or $GitCommit -notmatch '^[0-9a-fA-F]{40,64}$') {
    throw "Could not determine the current VibeCAD Git revision."
}
$GitCommit = $GitCommit.ToLowerInvariant()
$GitTree = (& git -C $RepoRoot rev-parse --verify "HEAD^{tree}").Trim()
if ($LASTEXITCODE -ne 0 -or $GitTree -notmatch '^[0-9a-fA-F]{40,64}$') {
    throw "Could not determine the current VibeCAD Git tree."
}
$GitTree = $GitTree.ToLowerInvariant()

$Pixi = Resolve-VibeCADPixi
$EnvRoot = Join-Path $PackageRoot ".pixi\envs\default"
$AgentHome = Join-Path $RepoRoot ".vibecad-dev\agent"
$AgentEndpoint = Join-Path $AgentHome "endpoint.json"
$PythonUserBase = Join-Path $RepoRoot ".vibecad-dev\python-user"
$LaunchRuntime = Get-VibeCADLaunchRuntime -EnvironmentRoot $EnvRoot
$ReleaseEvidence["pre_build_environment_present"] = [bool](Test-Path -LiteralPath $EnvRoot -PathType Container)
$ReleaseEvidence["pre_build_runtime_complete"] = [bool]$LaunchRuntime.Complete
$InitialQtProbe = $null
if ($LaunchRuntime.Complete) {
    $InitialQtProbe = Test-VibeCADQtPlatformRuntime -LaunchRuntime $LaunchRuntime
    if (-not $InitialQtProbe.Complete) {
        $LaunchRuntime = [pscustomobject]@{
            Complete = $false
            Diagnostic = $InitialQtProbe.Diagnostic
        }
    }
}

Write-Host ""
Write-Host "==============================================="
Write-Host " VibeCAD DEVELOPMENT"
Write-Host " Version: $VersionText"
Write-Host " Build:   $($Version.build_version)"
Write-Host " Commit:  $GitCommit"
Write-Host " Tree:    $GitTree"
Write-Host "==============================================="
Write-Host ""
Write-Host "Repository: $RepoRoot"
Write-Host "Pixi:       $Pixi"
Write-Host ""

Push-Location $PackageRoot
try {
    if ($ReleaseAttestation) {
        Write-Host "Preparing an exact cold repo-local release-attestation build..."
        if (Test-Path -LiteralPath $EnvRoot) {
            & $Pixi clean -e default
            if ($LASTEXITCODE -ne 0) {
                throw "pixi clean -e default failed while preparing the release-attestation build."
            }
        }
        if (Test-Path -LiteralPath $EnvRoot) {
            throw "ReleaseAttestation could not remove the existing Pixi environment before installation."
        }
        $ReleaseEvidence["environment_absent_before_install"] = $true
        $ReleaseEvidence["environment_cleaned_at_utc"] = Get-VibeCADUtcTimestamp
        & $Pixi clean --build
        if ($LASTEXITCODE -ne 0) {
            throw "pixi clean --build failed while preparing the release-attestation build."
        }
        $ReleaseEvidence["build_cache_cleaned_at_utc"] = Get-VibeCADUtcTimestamp
        $StagingQuarantinePath = Move-VibeCADLocalBuildStagingAside -PackageRoot $PackageRoot
        $ReleaseEvidence["local_build_staging_reset_at_utc"] = Get-VibeCADUtcTimestamp
        $ReleaseEvidence["local_build_staging_quarantine_path"] = $StagingQuarantinePath
        & $Pixi install -e default --frozen
        if ($LASTEXITCODE -ne 0) {
            throw "pixi install -e default failed."
        }
        $BuildAction = "pixi-install"
    }
    elseif (-not $LaunchRuntime.Complete) {
        if ($SkipRebuild) {
            throw "SkipRebuild requested, but the repo-local VibeCAD launch runtime is incomplete. $($LaunchRuntime.Diagnostic)"
        }
        if (Test-Path $EnvRoot) {
            Write-Host "Recovering an incomplete repo-local VibeCAD development environment..."
            Write-Host "Reason: $($LaunchRuntime.Diagnostic)"
            & $Pixi clean -e default
            if ($LASTEXITCODE -ne 0) {
                throw "pixi clean -e default failed while recovering the development environment."
            }
        }
        & $Pixi clean --build
        if ($LASTEXITCODE -ne 0) {
            throw "pixi clean --build failed while recovering the development environment."
        }
        $StagingQuarantinePath = Move-VibeCADLocalBuildStagingAside -PackageRoot $PackageRoot
        $ReleaseEvidence["local_build_staging_reset_at_utc"] = Get-VibeCADUtcTimestamp
        $ReleaseEvidence["local_build_staging_quarantine_path"] = $StagingQuarantinePath
        Write-Host "Creating the repo-local VibeCAD development environment..."
        & $Pixi install -e default --frozen
        if ($LASTEXITCODE -ne 0) {
            throw "pixi install -e default failed."
        }
        $BuildAction = "pixi-install"
    }
    elseif (-not $SkipRebuild) {
        Write-Host "Rebuilding this checkout into the repo-local VibeCAD environment..."
        & $Pixi reinstall -e default vibecad --frozen
        if ($LASTEXITCODE -ne 0) {
            throw "pixi reinstall -e default vibecad failed."
        }
        $BuildAction = "pixi-reinstall"
    }
    else {
        Write-Host "SkipRebuild requested; launching the existing repo-local development environment."
        $BuildAction = "skip-rebuild"
    }
}
finally {
    Pop-Location
}

$LaunchRuntime = Get-VibeCADLaunchRuntime -EnvironmentRoot $EnvRoot
if (-not $LaunchRuntime.Complete) {
    throw @"
The development build did not produce a complete repo-local VibeCAD launch runtime.

$($LaunchRuntime.Diagnostic)

The launcher will not fall back to an external executable, Qt plugin, or Qt DLL directory.
"@
}
$PostBuildQtProbe = Test-VibeCADQtPlatformRuntime -LaunchRuntime $LaunchRuntime
if (-not $PostBuildQtProbe.Complete) {
    throw @"
The development build produced Qt files, but the repo-local Windows platform plugin could not be initialized.

$($PostBuildQtProbe.Diagnostic)

VibeCAD was not started. The launcher will not substitute an external Qt runtime.
"@
}
$Executable = $LaunchRuntime.ExecutablePath

$PythonExecutable = Join-Path $EnvRoot "python.exe"
$RuntimeRequirementFiles = @(
    (Join-Path $RepoRoot "src\Mod\VibeCAD\requirements.txt"),
    (Join-Path $RepoRoot "src\Mod\VibeCADAero\requirements-aero.txt")
)
foreach ($RequirementFile in $RuntimeRequirementFiles) {
    if (-not (Test-Path -LiteralPath $RequirementFile -PathType Leaf)) {
        throw "Required VibeCAD runtime manifest was not found: $RequirementFile"
    }
}
$env:VIBECAD_RUNTIME_REQUIREMENTS = ConvertTo-Json `
    -InputObject $RuntimeRequirementFiles `
    -Compress
$null = New-Item -ItemType Directory -Path $PythonUserBase -Force
$env:PYTHONNOUSERSITE = "1"
$env:PYTHONUSERBASE = $PythonUserBase
if (-not (Test-VibeCADPythonRuntime -PythonExecutable $PythonExecutable)) {
    Install-VibeCADPythonRuntime `
        -PythonExecutable $PythonExecutable `
        -RepoRoot $RepoRoot
}
if (-not (Test-VibeCADPythonRuntime `
    -PythonExecutable $PythonExecutable `
    -EmitDiagnostic)) {
    throw "The repo-local VibeCAD Python runtime is incomplete after installation."
}

$ResolvedEnvRoot = $LaunchRuntime.EnvironmentRoot.TrimEnd([char[]]@('\', '/'))
$ResolvedExecutable = $LaunchRuntime.ExecutablePath
if (-not $ResolvedExecutable.StartsWith($ResolvedEnvRoot + '\', [System.StringComparison]::OrdinalIgnoreCase)) {
    throw "Refusing to launch an executable outside this checkout's Pixi environment."
}
$env:QT_PLUGIN_PATH = $LaunchRuntime.QtPluginRoot
$env:QT_QPA_PLATFORM_PLUGIN_PATH = $LaunchRuntime.QtPlatformsDirectory
$env:QT_QPA_PLATFORM = "windows"
$env:QT_FORCE_STDERR_LOGGING = "1"
$ExecutableSha256 = Get-VibeCADSha256 -Path $ResolvedExecutable
$QtRuntimeIdentity = Get-VibeCADQtRuntimeIdentity -LaunchRuntime $LaunchRuntime
$QtPlatformProbeEvidence = [ordered]@{
    complete = [bool]$PostBuildQtProbe.Complete
    checked_at_utc = $PostBuildQtProbe.CheckedAtUtc
    platform = $PostBuildQtProbe.Platform
    python_executable = $PostBuildQtProbe.PythonExecutable
    python_sha256 = $PostBuildQtProbe.PythonSha256
    loaded_qwindows_path = $PostBuildQtProbe.LoadedQWindowsPath
    loaded_qwindows_sha256 = $PostBuildQtProbe.LoadedQWindowsSha256
}

$AttestationEnvironmentNames = @(
    "VIBECAD_DEV_ATTESTATION_REQUIRED",
    "VIBECAD_DEV_BUILD_ATTESTATION",
    "VIBECAD_DEV_BUILD_ATTESTATION_SHA256",
    "VIBECAD_DEV_LAUNCH_ATTESTATION",
    "VIBECAD_DEV_LAUNCH_ATTESTATION_SHA256"
)
foreach ($Name in $AttestationEnvironmentNames) {
    Remove-Item -LiteralPath "Env:$Name" -ErrorAction SilentlyContinue
}

$BuildAttestation = $null
$LaunchAttestation = $null
$ExpectedRuntimeIdentity = $null
if (-not $SkipRebuild) {
    $InstalledModuleRoot = Resolve-VibeCADInstalledModuleRoot -EnvironmentRoot $ResolvedEnvRoot
    $Modules = @(Get-VibeCADModuleIdentities `
        -RepositoryRoot $RepoRoot `
        -InstalledModuleRoot $InstalledModuleRoot)
    if ($ReleaseAttestation) {
        $ReleaseEvidence["pre_receipt_checked_at_utc"] = Assert-VibeCADReleaseCheckoutClean -RepositoryRoot $RepoRoot
        $ReleaseEvidence["clean_checkout"] = $true
    }
    $BuildPayload = [ordered]@{
        schema = "vibecad.dev-build-attestation.v1"
        attestation_path = $null
        created_at_utc = Get-VibeCADUtcTimestamp
        repository_root = $RepoRoot
        commit = $GitCommit
        tree = $GitTree
        build_action = $BuildAction
        executable_path = $ResolvedExecutable
        executable_sha256 = $ExecutableSha256
        qt_runtime = $QtRuntimeIdentity
        qt_platform_probe = $QtPlatformProbeEvidence
        release_evidence = $ReleaseEvidence
        modules = $Modules
    }
    $BuildAttestation = Write-VibeCADAttestation `
        -Root $AttestationRoot `
        -Prefix "build" `
        -Payload $BuildPayload

    $LaunchId = [guid]::NewGuid().ToString("N")
    $LaunchPayload = [ordered]@{
        schema = "vibecad.dev-launch-attestation.v1"
        attestation_path = $null
        created_at_utc = Get-VibeCADUtcTimestamp
        launch_id = $LaunchId
        launcher_process_id = $PID
        repository_root = $RepoRoot
        commit = $GitCommit
        tree = $GitTree
        executable_path = $ResolvedExecutable
        executable_sha256 = $ExecutableSha256
        build_attestation_path = $BuildAttestation.Path
        build_attestation_sha256 = $BuildAttestation.Sha256
        qt_runtime = $QtRuntimeIdentity
        qt_platform_probe = $QtPlatformProbeEvidence
        release_evidence = $ReleaseEvidence
        modules = $Modules
    }
    $LaunchAttestation = Write-VibeCADAttestation `
        -Root $AttestationRoot `
        -Prefix "launch" `
        -Payload $LaunchPayload

    $ExpectedRuntimeModules = @(
        foreach ($Module in $Modules) {
            [ordered]@{
                name = $Module.name
                source_path = $Module.source_path
                source_sha256 = $Module.source_sha256
                runtime_path = $Module.installed_path
                runtime_sha256 = $Module.installed_sha256
            }
        }
    )
    $ExpectedRuntimeIdentity = [ordered]@{
        schema = "vibecad.dev-runtime-identity.v1"
        repository_root = $RepoRoot
        commit = $GitCommit
        tree = $GitTree
        executable_path = $ResolvedExecutable
        executable_sha256 = $ExecutableSha256
        build_attestation_path = $BuildAttestation.Path
        build_attestation_sha256 = $BuildAttestation.Sha256
        launch_attestation_path = $LaunchAttestation.Path
        launch_attestation_sha256 = $LaunchAttestation.Sha256
        qt_runtime = $QtRuntimeIdentity
        qt_platform_probe = $QtPlatformProbeEvidence
        release_evidence = $ReleaseEvidence
        qt_process = [ordered]@{
            platform = "windows"
            loaded_qwindows_path = $QtRuntimeIdentity.qwindows_path
            loaded_qwindows_sha256 = $QtRuntimeIdentity.qwindows_sha256
        }
        modules = $ExpectedRuntimeModules
    }
}

$env:VIBECAD_DEV_MODE = "1"
$env:VIBECAD_DEV_SOURCE_SHA = $GitCommit
$env:VIBECAD_DEV_SOURCE_TREE = $GitTree
$env:VIBECAD_DEV_SOURCE_ROOT = $RepoRoot
$env:VIBECAD_AGENT_HOME = $AgentHome
$env:VIBECAD_DEV_ATTESTATION_REQUIRED = if ($null -ne $ExpectedRuntimeIdentity) { "1" } else { $null }
if ($null -ne $ExpectedRuntimeIdentity) {
    $env:VIBECAD_DEV_BUILD_ATTESTATION = $BuildAttestation.Path
    $env:VIBECAD_DEV_BUILD_ATTESTATION_SHA256 = $BuildAttestation.Sha256
    $env:VIBECAD_DEV_LAUNCH_ATTESTATION = $LaunchAttestation.Path
    $env:VIBECAD_DEV_LAUNCH_ATTESTATION_SHA256 = $LaunchAttestation.Sha256
}
$env:FC_PYTHONHOME = $ResolvedEnvRoot
$env:PATH = @(
    $LaunchRuntime.QtDllDirectory,
    $ResolvedEnvRoot,
    (Join-Path $ResolvedEnvRoot "Library\bin"),
    (Join-Path $ResolvedEnvRoot "Scripts"),
    $env:PATH
) -join ";"

$FinalLaunchRuntime = Get-VibeCADLaunchRuntime -EnvironmentRoot $ResolvedEnvRoot
if (-not $FinalLaunchRuntime.Complete) {
    throw "The repo-local VibeCAD launch runtime became incomplete before Start-Process. $($FinalLaunchRuntime.Diagnostic)"
}
if (-not $FinalLaunchRuntime.ExecutablePath.Equals(
    $ResolvedExecutable,
    [System.StringComparison]::OrdinalIgnoreCase
)) {
    throw "The repo-local GUI executable changed before Start-Process."
}
$FinalQtRuntimeIdentity = Get-VibeCADQtRuntimeIdentity -LaunchRuntime $FinalLaunchRuntime
$ExpectedQtIdentityJson = $QtRuntimeIdentity | ConvertTo-Json -Compress -Depth 8
$FinalQtIdentityJson = $FinalQtRuntimeIdentity | ConvertTo-Json -Compress -Depth 8
if ($ExpectedQtIdentityJson -ne $FinalQtIdentityJson) {
    throw "The repo-local Qt platform runtime changed before Start-Process."
}
$LaunchRuntime = $FinalLaunchRuntime
$env:QT_PLUGIN_PATH = $LaunchRuntime.QtPluginRoot
$env:QT_QPA_PLATFORM_PLUGIN_PATH = $LaunchRuntime.QtPlatformsDirectory
$env:QT_QPA_PLATFORM = "windows"
$env:QT_FORCE_STDERR_LOGGING = "1"
$FinalQtProbe = Test-VibeCADQtPlatformRuntime -LaunchRuntime $LaunchRuntime
if (-not $FinalQtProbe.Complete) {
    throw "The repo-local Qt Windows platform plugin failed its final prelaunch load probe. $($FinalQtProbe.Diagnostic)"
}

$PostProbeLaunchRuntime = Get-VibeCADLaunchRuntime -EnvironmentRoot $ResolvedEnvRoot
if (-not $PostProbeLaunchRuntime.Complete) {
    throw "The repo-local VibeCAD launch runtime became incomplete after the final Qt load probe. $($PostProbeLaunchRuntime.Diagnostic)"
}
$PostProbeQtRuntimeIdentity = Get-VibeCADQtRuntimeIdentity -LaunchRuntime $PostProbeLaunchRuntime
$PostProbeQtIdentityJson = $PostProbeQtRuntimeIdentity | ConvertTo-Json -Compress -Depth 8
if ($ExpectedQtIdentityJson -ne $PostProbeQtIdentityJson) {
    throw "The repo-local Qt platform runtime changed during the final load probe."
}
$FinalExecutableSha256 = Get-VibeCADSha256 -Path $PostProbeLaunchRuntime.ExecutablePath
if ($FinalExecutableSha256 -ne $ExecutableSha256) {
    throw "The repo-local GUI executable changed during final prelaunch validation."
}
$LaunchRuntime = $PostProbeLaunchRuntime

Write-Host ""
Write-Host "Launching CURRENT CHECKOUT:"
Write-Host "  $ResolvedExecutable"
if ($null -ne $ExpectedRuntimeIdentity) {
    Write-Host "Build attestation:  $($BuildAttestation.Path)"
    Write-Host "Launch attestation: $($LaunchAttestation.Path)"
}
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
    -TimeoutSeconds $ControlReadyTimeoutSeconds `
    -ExpectedRuntimeIdentity $ExpectedRuntimeIdentity

Write-Host "Agent control ready: $($ControlStatus.endpoint.base_url)"
