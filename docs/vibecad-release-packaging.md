# VibeCAD Release and Update Operations

This document is the operating contract for official VibeCAD packages and
updates. Official releases are identified only by the product version and
monotonic build number in `version.json`; commit hashes and calendar dates are
not part of the public package, tag, release, or update identity.

## Release identity

For this example:

```json
{
  "version_major": 26,
  "version_minor": 3,
  "version_patch": 1,
  "version_suffix": "RC3",
  "build_version": 0
}
```

the canonical values are:

| Purpose | Value |
| --- | --- |
| Display version | `26.3.1-RC3 (Build 0)` |
| Release tag | `v26.3.1-RC3-build0` |
| Release title | `VibeCAD 26.3.1-RC3 (Build 0)` |
| Artifact basename | `VibeCAD-26.3.1-RC3-build0` |
| Update channel | `preview` |

An empty `version_suffix` selects the `stable` channel. Any suffix selects the
`preview` channel. Within one semantic version, a larger `build_version` is a
newer release. A final version ranks after its prereleases.

Before building a candidate, edit only `version.json`, then synchronize and
validate every downstream version consumer:

```powershell
package/rattler-build/.pixi/envs/default/python.exe src/Tools/sync_version.py
package/rattler-build/.pixi/envs/default/python.exe src/Tools/sync_version.py --check
```

Every published version/build pair is immutable and may be used only once. If
packaging or dependencies change after publication without changing the product
version, increment `build_version`. Never replace a tag or release asset.

## Build and publish workflow

`.github/workflows/vibecad-release.yml` is the only workflow that publishes a
VibeCAD release. It is manually dispatched and defaults to validation-only.
The workflow uses the same Rattler bundle entry points used by local builds.

The workflow:

1. Resolves canonical metadata from `version.json` and verifies synchronization.
2. Pins every job to the same source commit.
3. Builds the Linux AppImage and Debian package and the Windows installer.
4. Requires embedded Authenticode signatures on production Windows binaries.
5. verifies package SHA-256 files and generates the strict update manifest.
6. Creates GitHub build-provenance attestations.
7. Creates a draft release, uploads the complete asset set, and publishes it
   only after all gates pass.
8. Dispatches signed-channel promotion after the immutable release exists.

Publishing is allowed only from the current `main` commit. The canonical tag
and release must not already exist, and GitHub release immutability must be
enabled for the repository. Windows builds always produce the installer; the
portable archive is not part of validation or release builds.

Validation builds may use any `source_ref` with `publish_release=false`. An
official release uses `source_ref=main` and `publish_release=true`.

Required GitHub Actions configuration:

| Name | Purpose |
| --- | --- |
| `AZURE_CLIENT_ID` | OIDC identity used by Azure login |
| `AZURE_TENANT_ID` | Azure tenant for code signing |
| `AZURE_SUBSCRIPTION_ID` | Azure subscription for code signing |
| `WINDOWS_AZURE_ENDPOINT` | Trusted Signing endpoint |
| `WINDOWS_AZURE_SIGNING_ACCOUNT` | Trusted Signing account |
| `WINDOWS_AZURE_CERTIFICATE_PROFILE` | Trusted Signing certificate profile |
| `VIBECAD_UPDATES_TOKEN` | Fine-grained token allowed to dispatch the update metadata repository and the promotion workflow |

Production signing is fail-closed: selecting publication without valid signing
configuration, access to `10-X-eng/vibecad-updates`, or verifiable embedded
signatures fails the workflow before a release is published. Publication also
requires the ceremony-produced `src/Mod/VibeCAD/update-trust/root.json`; a
development build without that root never falls back to unsigned discovery.

## Release assets

The canonical asset set is:

- `VibeCAD-<version>-build<build>-Linux-<arch>.AppImage`
- `VibeCAD-<version>-build<build>-Linux-<arch>.AppImage.zsync`
- `VibeCAD-<version>-build<build>-Linux-<arch>.deb`
- `VibeCAD-<version>-build<build>-Windows-x86_64-installer.exe`
- one `-SHA256.txt` file for every package
- `VibeCAD-update-<version>-build<build>.json` and its checksum

The update manifest contains the version, build, channel, canonical GitHub
release links, and the exact size and SHA-256 of each native package. It never
contains a source hash as release identity.

## Signed update metadata

GitHub Releases stores the packages, but clients do not trust the GitHub API as
the update authority. VibeCAD uses The Update Framework (TUF) to protect update
discovery against a compromised account, token, CDN, or stale metadata replay.

The dedicated `10-X-eng/vibecad-updates` repository owns the TUF repository and
publishes it with GitHub Pages at:

```text
https://10-x-eng.github.io/vibecad-updates/metadata/
https://10-x-eng.github.io/vibecad-updates/targets/
```

It should be bootstrapped from the maintained TUF-on-CI repository template.
The update repository handles the `vibecad-release-published` repository
dispatch sent by `.github/workflows/vibecad-promote-update.yml`. The payload is:

```json
{
  "release_tag": "v26.3.1-RC3-build0",
  "channel": "preview",
  "manifest_name": "VibeCAD-update-26.3.1-RC3-build0.json",
  "manifest_sha256": "<64 lowercase hex characters>",
  "manifest_url": "https://github.com/10-X-eng/vibecad/releases/download/..."
}
```

The handler downloads the immutable release manifest, verifies the dispatched
digest, and publishes it as `channels/preview.json` or
`channels/stable.json`. TUF snapshot and timestamp metadata then commit the
new channel state. Stable and preview should use separately delegated target
roles so either channel can be frozen or revoked independently.

The initial root ceremony is deliberately not automated in this repository:

1. Generate root keys on offline, access-controlled systems.
2. Require a threshold greater than one for root metadata.
3. Configure online timestamp/snapshot and delegated stable/preview signing
   through the update repository, ideally using short-lived OIDC identities.
4. Record owner, backup, rotation, expiry, and emergency-revocation procedures.
5. Copy only the signed public `root.json` to
   `src/Mod/VibeCAD/update-trust/root.json`.
6. Build and validate a candidate containing that root before publishing the
   first update-enabled release.

Private keys must never enter this repository or a VibeCAD package. A missing,
expired, or invalid root or metadata chain fails closed; there is no unsigned
GitHub fallback.

## Application update flow

The Update Center is available from VibeCAD's Help menu and Updates preference
page. By default, VibeCAD checks once every 24 hours on a background thread. A
manual check can always be requested when updates are enabled.

The client:

1. Loads user preferences or an authoritative machine policy.
2. Selects `stable` or `preview` from the installed version or policy.
3. Refreshes and verifies the complete TUF metadata chain.
4. Parses the strict signed channel manifest and compares version/build values.
5. Selects only the native Windows installer or Linux AppImage for the current
   architecture.
6. Resumes interrupted downloads with HTTP Range and ETag state.
7. Verifies signed size and SHA-256; Windows also requires a valid embedded
   Authenticode signature.
8. Stages installation on explicit request or, when policy allows, on exit.

Windows updates run the installer silently after VibeCAD exits. The installer
waits for the process to stop, renames the old installation to a sibling
last-known-good tree, installs into a clean directory, imports the updater in
`freecadcmd`, and starts the new GUI. The GUI commits a health receipt after it
survives startup. A failed install, import, crash, or health timeout restores
the previous installation and registry state. One healthy Windows rollback
tree is retained until the next update or uninstall.

AppImage updates copy the verified package beside the running AppImage, atomically
swap the files after exit, perform a command-line import check, and start the new
GUI. A failed check, crash, or health timeout atomically restores the previous
AppImage. A successful health receipt removes its rollback copy.

Updater state, partial downloads, and receipts live in the `updates` child of
the user application-data directory reported by VibeCAD. Command-line test
environments without the application runtime fall back to `~/.vibecad/updates`.

## Enterprise policy

Administrators may deploy a strict JSON policy at:

- Windows: `%ProgramData%\VibeCAD\update-policy.json`
- Linux: `/etc/vibecad/update-policy.json`

Tests and managed launchers may override the path with
`VIBECAD_UPDATE_POLICY_FILE`. A present but malformed managed policy disables
updates rather than falling back to user settings. Unknown fields are rejected.

Example:

```json
{
  "enabled": true,
  "automatic_checks": true,
  "channel": "stable",
  "check_interval_hours": 8,
  "automatic_download": true,
  "install_on_exit": true,
  "metadata_base_url": "https://updates.example.com/metadata/",
  "target_base_url": "https://updates.example.com/targets/",
  "trusted_root": "C:\\ProgramData\\VibeCAD\\update-root.json"
}
```

All fields are optional. `channel` is `auto`, `stable`, or `preview`; the check
interval must be at least one hour. Metadata and target overrides must be HTTPS
URLs without credentials. Managed controls are visible but read-only in the UI.

## Release runbook

Before publication:

1. Choose the semantic version/suffix and a never-before-published build number.
2. Synchronize versions and run the focused unit, workflow, shell, and local
   package validation suites.
3. Run the release workflow with `publish_release=false` and smoke-test the
   downloaded Windows installer and Linux AppImage artifacts.
4. Confirm Azure signing, release immutability, update-repository dispatch, TUF
   role thresholds/expiry, and the packaged root.
5. Merge the exact candidate to `main` and run the workflow with publication
   enabled.
6. Confirm every release asset/checksum/attestation and the signed stable or
   preview channel before announcing the release.
7. Test discovery, resumable download, healthy install, and forced rollback from
   an older supported package.

Only after the first replacement release completes this runbook should the old
nightly and date/SHA GitHub releases and tags be deleted. Record the deleted tag
and release list first, preserve any required historical packages, and never
delete or rewrite the validated replacement release.

## Local Windows package validation

From PowerShell:

```powershell
Set-Location package/rattler-build
$env:BUILD_TAG = & .\.pixi\envs\default\python.exe ..\..\src\Tools\resolve_release_artifact_name.py ..\.. --component release-tag
$env:MAKE_INSTALLER = "true"
$env:MAKE_PORTABLE_ARCHIVE = "false"
$env:WINDOWS_SIGN_RELEASE = "0"
pixi run -e package create_bundle
```

Local packages are intentionally unsigned and cannot pass the production
publication gate. They are suitable for local feature and installer-flow smoke
tests only.
