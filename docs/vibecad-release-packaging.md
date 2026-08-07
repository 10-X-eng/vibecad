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
4. Verifies every package checksum and generates the strict update manifest.
5. Creates GitHub build-provenance attestations.
6. Creates a draft release, uploads the complete asset set, and publishes it
   only after all gates pass.

Publishing is allowed only from the current `main` commit. The canonical tag
and release must not already exist, and GitHub release immutability must be
enabled for the repository. Windows builds always produce the installer; the
portable archive is not part of validation or release builds.

Validation builds may use any `source_ref` with `publish_release=false`. An
official release uses `source_ref=main` and `publish_release=true`.

No additional repository token, external update repository, Microsoft account,
or code-signing certificate is required. Publishing uses the workflow's scoped
GitHub token. Production publication is fail-closed on the current `main`
commit, version synchronization, unique tag and release identity, package
checksums, and the canonical update manifest. Repository owners must keep
GitHub's immutable-releases setting enabled; the standard workflow token cannot
read repository-administration settings, while GitHub enforces that setting on
the release once it is published.

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

## GitHub Release update authority

Official VibeCAD updates use this repository's GitHub Releases as the single
default publication and discovery service. No second repository, GitHub Pages
site, signing ceremony, or promotion job is part of the standard release flow.

The client lists published releases from `10-X-eng/vibecad` and accepts only
canonical tags of the form `v<version>-build<build>`. Stable builds must be
published as non-prereleases with an empty version suffix; preview builds must
be published as prereleases with a version suffix. The client compares semantic
version and build number, never a commit hash or date, and selects the newest
release in the configured channel.

Every accepted release must contain the canonical update manifest and its
SHA-256 checksum. The client verifies the repository, release tag, channel,
canonical download URLs, and GitHub-reported asset sizes against that manifest.
It then verifies the manifest checksum and the selected package's exact size
and SHA-256 before installation. Any mismatch fails closed.

An enterprise can opt into its own TUF service through managed policy by
providing `metadata_base_url` and `target_base_url`, plus either `trusted_root`
or a public root packaged by that managed distribution. That path is optional,
has no VibeCAD-hosted default, and fails closed if the URL pair or trust root is
missing. It does not change the normal GitHub Releases flow.

## Application update flow

The Update Center is available from VibeCAD's Help menu and Updates preference
page. By default, VibeCAD checks once every 24 hours on a background thread. A
manual check can always be requested when updates are enabled.

The client:

1. Loads user preferences or an authoritative machine policy.
2. Selects `stable` or `preview` from the installed version or policy.
3. Reads official GitHub Releases, or an explicitly configured enterprise TUF
   service.
4. Selects a canonical release and verifies its manifest, checksum, channel,
   URLs, sizes, and version/build identity.
5. Selects only the native Windows installer or Linux AppImage for the current
   architecture.
6. Resumes interrupted downloads with HTTP Range and ETag state.
7. Verifies the authorized size and SHA-256 before using the package.
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
interval must be at least one hour. The TUF URL fields are an all-or-nothing
enterprise override and must be HTTPS URLs without credentials. `trusted_root`
must identify the administrator-provided public root metadata unless the managed
distribution packages one. Managed controls are visible but read-only in the UI.

## Release runbook

Before publication:

1. Choose the semantic version/suffix and a never-before-published build number.
2. Synchronize versions and run the focused unit, workflow, shell, and local
   package validation suites.
3. Run the release workflow with `publish_release=false` and smoke-test the
   downloaded Windows installer and Linux AppImage artifacts.
4. Confirm release immutability and inspect the canonical manifest, checksums,
   package names, and build-provenance attestations.
5. Merge the exact candidate to `main` and run the workflow with publication
   enabled.
6. Confirm every release asset, checksum, manifest, and attestation before
   announcing the release.
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
pixi run -e package create_bundle
```

Local and published packages are authorized by the canonical GitHub Release
manifest and checksum plus their exact size and SHA-256. No Microsoft account,
Authenticode certificate, external update repository, or GitHub Pages site is
required to build, test, publish, download, or install an update.
