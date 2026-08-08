# SPDX-License-Identifier: LGPL-2.1-or-later

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string]$CurrentInstaller,

    [Parameter(Mandatory = $true)]
    [string]$Repository,

    [Parameter(Mandatory = $true)]
    [string]$ReleaseVersion,

    [Parameter(Mandatory = $true)]
    [int]$Build,

    [string]$PreviousInstaller = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Invoke-Installer {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,

        [Parameter(Mandatory = $true)]
        [string[]]$Arguments
    )

    $process = Start-Process -FilePath $Path -ArgumentList $Arguments -PassThru -Wait
    if ($process.ExitCode -ne 0) {
        throw "Installer '$Path' exited with code $($process.ExitCode)."
    }
}

$current = (Resolve-Path -LiteralPath $CurrentInstaller).Path
$majorMinorPatch = ($ReleaseVersion -split "-", 2)[0]
$versionParts = $majorMinorPatch.Split(".")
if ($versionParts.Count -ne 3) {
    throw "Release version '$ReleaseVersion' does not contain major.minor.patch."
}
$seriesKey = "$($versionParts[0])$($versionParts[1])$($versionParts[2])"
$appKey = "HKCU:\SOFTWARE\VibeCAD$seriesKey"
$uninstallKey = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\VibeCAD$seriesKey"

if ((Test-Path -LiteralPath $appKey) -or (Test-Path -LiteralPath $uninstallKey)) {
    throw "The clean-upgrade smoke test requires a fresh Windows user registry."
}

$testRoot = Join-Path $env:RUNNER_TEMP "VibeCAD-clean-upgrade-$([guid]::NewGuid().ToString('N'))"
$downloadRoot = Join-Path $testRoot "previous"
New-Item -ItemType Directory -Path $downloadRoot -Force | Out-Null

try {
    if (-not $PreviousInstaller) {
        $releases = gh release list --repo $Repository --limit 100 --json tagName,isDraft | ConvertFrom-Json
        $candidates = foreach ($release in $releases) {
            if ($release.isDraft -or $release.tagName -notmatch '^v(.+)-build(\d+)$') {
                continue
            }
            if ($Matches[1] -eq $ReleaseVersion -and [int]$Matches[2] -lt $Build) {
                [pscustomobject]@{
                    Tag = $release.tagName
                    Build = [int]$Matches[2]
                }
            }
        }
        $previousRelease = $candidates | Sort-Object Build -Descending | Select-Object -First 1
        if (-not $previousRelease) {
            Write-Host "No earlier published build of $ReleaseVersion exists; clean-upgrade smoke test is not applicable."
            exit 0
        }
        $asset = "VibeCAD-$ReleaseVersion-build$($previousRelease.Build)-Windows-x86_64-installer.exe"
        gh release download $previousRelease.Tag --repo $Repository --pattern $asset --dir $downloadRoot
        if ($LASTEXITCODE -ne 0) {
            throw "Could not download $asset from $($previousRelease.Tag)."
        }
        $PreviousInstaller = Join-Path $downloadRoot $asset
    }

    $previous = (Resolve-Path -LiteralPath $PreviousInstaller).Path
    Invoke-Installer -Path $previous -Arguments @("/S", "/CurrentUser")

    $installed = Get-ItemProperty -LiteralPath $appKey
    $installRoot = [string]$installed.'(default)'
    if (-not $installRoot -or -not (Test-Path -LiteralPath $installRoot)) {
        throw "The previous installer did not register a valid installation root."
    }
    $oldBuild = $installed.Build
    if ([int]$oldBuild -ge $Build) {
        throw "Previous installer has Build $oldBuild; expected a build lower than $Build."
    }

    $marker = Join-Path $installRoot "must-not-survive-clean-upgrade.txt"
    Set-Content -LiteralPath $marker -Value "stale program file" -Encoding ascii

    # This deliberately exercises a normal, manually launched silent installer:
    # no /VIBECADUPDATE flag and no explicit destination are supplied.
    Invoke-Installer -Path $current -Arguments @("/S", "/CurrentUser")

    $backupRoot = "$installRoot.vibecad-rollback"
    if (Test-Path -LiteralPath $marker) {
        throw "The old installation was overlaid; its stale marker remains in the live tree."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $backupRoot "must-not-survive-clean-upgrade.txt"))) {
        throw "The prior installation was not retained as the rollback tree."
    }
    if (-not (Test-Path -LiteralPath (Join-Path $installRoot "bin\VibeCAD.exe"))) {
        throw "The replacement installation is missing bin\VibeCAD.exe."
    }

    $identity = Get-ItemProperty -LiteralPath $appKey
    if ($identity.ReleaseVersion -ne $ReleaseVersion -or [int]$identity.Build -ne $Build) {
        throw "Installed identity is '$($identity.ReleaseVersion)' Build $($identity.Build), expected '$ReleaseVersion' Build $Build."
    }
    if (-not $identity.UpdateVersion) {
        throw "The sortable UpdateVersion registry identity was not written."
    }

    Write-Host "Clean Windows upgrade passed: Build $oldBuild -> Build $Build"
}
finally {
    $registeredRoot = ""
    if (Test-Path -LiteralPath $appKey) {
        $registeredRoot = [string](Get-ItemProperty -LiteralPath $appKey).'(default)'
    }
    $uninstaller = if ($registeredRoot) { Join-Path $registeredRoot "Uninstall-VibeCAD.exe" } else { "" }
    if ($uninstaller -and (Test-Path -LiteralPath $uninstaller)) {
        try {
            Invoke-Installer -Path $uninstaller -Arguments @("/S", "/CurrentUser")
        }
        catch {
            Write-Warning $_
        }
    }
    if (Test-Path -LiteralPath $appKey) {
        Remove-Item -LiteralPath $appKey -Recurse -Force
    }
    if (Test-Path -LiteralPath $uninstallKey) {
        Remove-Item -LiteralPath $uninstallKey -Recurse -Force
    }
    if (Test-Path -LiteralPath $testRoot) {
        Remove-Item -LiteralPath $testRoot -Recurse -Force
    }
}
