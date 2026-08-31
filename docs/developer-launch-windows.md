# Launch the current VibeCAD checkout on Windows

Use the repo-root development launcher when you want to test the code in this
checkout without starting the VibeCAD copy installed through the normal Windows
installer.

## Normal use

Double-click the prominent one-click entry point:

```text
RUN-VIBECAD-DEV.cmd
```

`RUN-VIBECAD-DEV.cmd` delegates to the canonical launcher:

```text
Launch-VibeCAD-Dev.cmd
```

or run:

```powershell
.\Launch-VibeCAD-Dev.ps1
```

The launcher initializes the checkout's pinned Git submodules, reads
`version.json`, prints the current version/build, full Git commit, and full Git
tree, then installs
or rebuilds `vibecad` inside this checkout's
`package\rattler-build\.pixi\envs\default` environment. It launches only when
that environment contains both the repo-local GUI executable and a complete
Windows Qt platform runtime. If an interrupted first build left an incomplete
Pixi environment, the launcher recovers it instead of treating an executable or
an empty environment directory as a usable build.

Install and rebuild commands use Pixi's frozen mode. The committed multi-platform
lockfile is therefore an input to the developer run, not a generated workspace
change.

It never searches the Start menu or `Program Files`.

The first launch is a full native VibeCAD/FreeCAD build and can take a long time.
Keep the visible PowerShell window open. Later launches reuse the repo-local
environment and compiler cache, so normal source/test iterations are
incremental.

The launcher also quarantines Python's per-user site-packages under the ignored
checkout directory `.vibecad-dev\python-user`. This is deliberate even though
`PYTHONNOUSERSITE=1` is set: embedded FreeCAD Python can still add its computed
user site. Redirecting `PYTHONUSERBASE` prevents a user-wide VTK, NumPy, or
other wheel from shadowing the versions in the checkout's Pixi environment.

## Native Qt launch completeness

Finding `VibeCAD.exe` or `freecad.exe` is not sufficient. Before launch, the
script also requires a canonical `qwindows.dll` and matching Qt DLL directory
inside the same checkout Pixi environment. Supported Pixi/Conda layouts include:

```text
Library\lib\qt6\plugins\platforms\qwindows.dll
Library\plugins\platforms\qwindows.dll
plugins\platforms\qwindows.dll
```

The selected layout must use the checkout's locked Qt 6 runtime and contain
`Qt6Core.dll`, `Qt6Gui.dll`, and `Qt6Widgets.dll`. The package recipe explicitly
uses PySide6/Qt 6 and excludes Qt 5, so the launcher does not accept a Qt 5
plugin as a substitute. Every resolved executable, plugin directory, platform
plugin, DLL directory, and required DLL must remain beneath the canonical Pixi
environment root. The launcher neither searches for nor accepts a system, user,
`Program Files`, or inherited-environment plugin as a fallback.

Immediately before `Start-Process`, the launcher revalidates and re-hashes that
runtime, places the exact repo-local Qt DLL directory first on `PATH`, and
overwrites these variables with one canonical repo-local path each:

```text
QT_PLUGIN_PATH=<canonical plugins root>
QT_QPA_PLATFORM_PLUGIN_PATH=<canonical platforms directory>
```

External values are not appended. This prevents direct native launch from
selecting a different Qt platform-plugin tree or producing the common “no Qt
platform plugin could be initialized” failure because a partial environment was
mistaken for a complete one.

When the application starts through this launcher, the status bar shows:

```text
VibeCAD DEV • <commit>
```

and the main-window title receives the same development marker. This makes a
development session visibly different from an installed release build.

For an ordinary launch (one without `-SkipRebuild`), that marker is taken from
the checkout-derived full commit and is cross-checked inside the running process
against the launch attestations before the marker is accepted. There is no
command-line argument for supplying a different release identity.

## Build and launch attestations

Every launch that rebuilds creates two collision-safe JSON receipts under the
ignored checkout directory:

```text
<repo>\.vibecad-dev\attestations\
```

The files use high-resolution UTC time plus a random GUID in their names and are
opened with create-new semantics. An existing receipt is never overwritten.

The build receipt uses `vibecad.dev-build-attestation.v1`; the launch receipt
uses `vibecad.dev-launch-attestation.v1`. Together they bind:

- the canonical repository root;
- the full checkout commit and tree IDs;
- the exact repo-local GUI executable path and SHA-256;
- the canonical Qt plugin root, platforms directory, `qwindows.dll` path/hash,
  matching Qt DLL directory, and required Qt DLL paths/hashes;
- a successful, repo-local PySide6 `QApplication` load probe whose reported Qt
  platform is exactly `windows` and whose loaded `qwindows.dll` is the exact
  attested plugin path, including the loaded plugin path/hash, probe Python
  path/hash, and UTC check time;
- source and installed paths and SHA-256 values for `InitGui.py`,
  `VibeCADAgentControl.py`, `VibeCADAgentCli.py`, and `VibeCADGui.py` (the installed copies must be
  byte-for-byte equal to the checkout sources);
- source paths and SHA-256 values for `Launch-VibeCAD-Dev.ps1` and
  `Invoke-VibeCAD-VisibleTour.ps1`; and
- the exact build-receipt path/hash carried forward by the launch receipt; and
- the `release_evidence` assertion and clean-check result described below.

The launcher passes only the resulting receipt paths and hashes to VibeCAD. The
running process independently reads the checkout's canonical Git root, full
`HEAD`, and `HEAD^{tree}`, then reopens and hashes the actual receipts,
executable, checkout sources, installed modules, Qt 6 plugin, probe Python, and
required Qt DLLs. It also asks Windows for the `qwindows.dll` loaded by the
actual visible VibeCAD process and requires that path/hash to equal the receipt
and helper-probe identity. It refuses control-channel startup if a receipt is
missing, partial, modified, internally inconsistent, if any installed module is
stale, or if the GUI process loaded a different platform plugin.

For release-evidence automation, use the explicit guard form:

```powershell
.\Launch-VibeCAD-Dev.ps1 -ReleaseAttestation
```

All rebuilt launches produce actual-file attestations, but
`-ReleaseAttestation` adds both a stricter clean-checkout assertion and an exact
cold Pixi build. After pinned submodules are initialized and before any Pixi
build action, the launcher runs Git porcelain-v2 status with all untracked files and
`--ignore-submodules=none`. Any superproject change, untracked file, changed
submodule revision, modified submodule content, or untracked submodule content
fails closed. The same check runs again immediately before receipt creation so
the build cannot silently dirty the asserted checkout.

Release-attestation mode records whether the environment and complete launch
runtime existed before the build, removes the named checkout-local `default`
environment if present, proves it is absent, clears Pixi's build cache, and then
runs a frozen `pixi install`. Failure to remove the environment or clear the
build cache stops before receipt creation or GUI launch. Normal one-click use
remains incremental and continues to reinstall only the VibeCAD package when a
complete environment already exists.

Both receipts contain a `release_evidence` object. In release-attestation mode
it records `asserted: true`, `clean_checkout: true`,
`submodule_dirt_checked: true`, `cold_build_asserted: true`, the exact status
mode, the pre-build environment/runtime state, proof that the environment was
absent before install, and the clean/environment/build-cache/pre-receipt check
times. The running GUI requires this complete object to match in both receipts
and validates every claimed gate before publishing its runtime identity. For
an ordinary rebuilt launch it records `asserted: false` and does not claim a
clean checkout. Combining `-ReleaseAttestation` with `-SkipRebuild` remains
rejected before submodule, build, or launch work begins. The switch does not
allow a caller to choose a commit, tree, repository, executable, or release
label.

### Qt platform load probe

The launcher does not treat the presence of `qwindows.dll` as proof that Qt can
use it. It runs the checkout's own `python.exe`, imports PySide6, creates a
non-windowed `QApplication`, and requires `QGuiApplication.platformName()` to be
exactly `windows`. The probe is run before deciding whether recovery is needed,
again after the install or rebuild, and once more immediately before the visible
GUI process starts. It pins `QT_PLUGIN_PATH`,
`QT_QPA_PLATFORM_PLUGIN_PATH`, `QT_QPA_PLATFORM=windows`, the matching Qt DLL
directory, and the repo-local environment while probing. After Qt initializes,
the probe asks Windows for the loaded `qwindows.dll` module path and requires it
to equal the already resolved and hashed repo-local plugin exactly; loading a
different Qt plugin from another search path is a failure.

The probe creates no top-level window and does not send physical mouse or
keyboard input. `QT_FORCE_STDERR_LOGGING=1` routes Qt diagnostics to the launcher
instead of relying on a broken GUI error dialog. A failed existing runtime is
cleanly rebuilt during a normal launch; `-SkipRebuild` refuses it. If the
post-build or final probe fails, VibeCAD is not started and no external Qt files
are substituted.

## Control-ready contract

The development launcher gives this checkout its own ignored agent-control
directory:

```text
<repo>\.vibecad-dev\agent\
```

The bearer token and `endpoint.json` live there. In development mode, the agent
home must be exactly this checkout-derived path; an arbitrary
`VIBECAD_AGENT_HOME` is rejected before a directory is created. On Windows the
checkout-scoped directory, token, and endpoint receive a protected,
current-user-only ACL. Development-mode startup fails closed if that ACL cannot
be applied and read back exactly. Normal installed startup preserves its
best-effort file-permission behavior and does not replace an arbitrary existing
directory DACL. This prevents
a developer or desktop agent from accidentally discovering a separately
installed VibeCAD session through the normal per-user endpoint. The launcher
prints the visible GUI PID and endpoint path, then waits for an authenticated
`/v1/status` response whose `channel` is `vibecad-agent-control` and whose
`gui_up` value is true. It
also requires the endpoint and status to agree on the launched PID, a random
per-server-start instance ID, server start time, exact token path, and complete
runtime identity.
Success therefore means all of the following are true:

1. the current checkout's visible GUI process started;
2. that exact PID owns the authenticated, loopback-only control channel; and
3. for rebuilt launches, the process-derived runtime identity exactly matches
   this invocation's build and launch receipts, including the `qwindows.dll`
   loaded by that same visible GUI process.

The token is read from its file and is never printed or requested from a human.
The HTTP service binds only to `127.0.0.1`; this launcher does not expose a LAN
or Internet development server. See
[vibecad-agent-control.md](vibecad-agent-control.md) for the route schemas and
security contract.

That checkout separation is a credential-discovery boundary, not a filesystem
sandbox. Once authenticated, the tester can open and save files wherever the
VibeCAD process already has access, and the explicitly authorized `/v1/run`
compatibility route executes in that same process authority. Use bounded routes
where available and authorize file targets and compatibility scripts just as
you would for a local developer tool.

The launcher sets the literal opt-in `VIBECAD_DEV_MODE=1`. That makes this
checkout use the fail-closed server entry point: a callable Qt document-thread
dispatcher must exist before the endpoint starts, operations are serialized,
and restore-state checks happen before document access. Normal installed startup
and existing integrations keep the original compatibility entry point and
defaults; development-mode safety does not silently replace those behaviors.

## Required observable development loop

Use this loop for every user-visible feature, regression fix, and development
checkpoint:

1. Start `RUN-VIBECAD-DEV.cmd` (or the PowerShell launcher) from the exact
   checkout under test.
2. Confirm the visible `VibeCAD DEV • <commit>` title/status marker and the
   repo-local executable path printed by the launcher.
3. Read this checkout's `endpoint.json` and token, then require an authenticated
   ready status before issuing test actions.
4. Drive the real, visible application through the narrowest authoritative
   surface:
   - `/v1/open`, `/v1/save`, `/v1/save-as`, `/v1/close`,
     `/v1/preferences`, and `/v1/screenshot` for application workflows and
     evidence;
   - `/v1/ui/ribbon`, `/v1/ui/menus`, and `/v1/ui/click` for exact semantic UI
     inspection and in-process activation that does not control the Windows
     cursor;
   - `/v1/aero` for Aero workflows;
   - `/v1/run` only for explicitly authorized local compatibility scripts;
   - the repo's Qt GUI harness when a test specifically requires widget-level
     interaction.
5. Capture the visible window through `/v1/screenshot` before and after the
   action. Keep the same GUI window visible so a person can watch the change as
   it happens.
6. Preserve route results, screenshots, file round-trip artifacts, tour
   receipts, and focused automated-test output as the checkpoint evidence.
7. Do not call the checkpoint complete merely because a unit test passed. The
   visible application run and the relevant artifact/receipt checks must also
   pass. Leave the GUI running for human inspection unless a restart, crash, or
   shutdown path is itself under test.

The Python `/v1/run` route retains privileged local compatibility execution.
Its source-text checks are not a security sandbox or an authority boundary.
Only run source the developer has authorized, and use a bounded domain route
such as `/v1/aero` when one exists instead of bypassing that contract merely to
make a GUI demonstration pass.

## Watchable cyan-cursor tour

After the launcher reports `Agent control ready`, run:

```powershell
.\Invoke-VibeCAD-VisibleTour.ps1
```

The script creates a small, click-through, non-activating overlay containing
only a plain cyan pointer. It moves that overlay between geometry reported by
the live `QMenuBar` and `VibeCADRibbonTabs`. Activation is sent to the exact Qt
object through authenticated `/v1/ui/click`; the script contains no
`SetCursorPos`, `SendInput`, or equivalent Windows pointer injection. There is
no label, sign, circle, or halo. The built-in operator path also contains no
physical keyboard injection, input blocking, cursor confinement, input-thread
attachment, or foreground-window activation. If the exact validated VibeCAD
window is minimized, it is shown with `SW_SHOWNOACTIVATE` so restoration does
not take keyboard focus.

Each top-level menu is shown only for a short, bounded Qt preview while the cyan
pointer is visibly pressed. The popup is then closed before the route returns;
the previous focus and menu-bar action are restored, and the active window and
popup state are verified unchanged. If a human already has a popup open, the
tester fails busy rather than closing or replacing it. Ribbon activation uses
the same focus/window restoration contract.

With no `-Targets` argument, the script discovers the exact running checkout's
currently visible, enabled top-level menus and enabled ribbon tabs, then tours
that live semantic inventory. It does not assume that a feature-specific tab or
menu exists. To run a focused tour:

```powershell
.\Invoke-VibeCAD-VisibleTour.ps1 -Targets @(
    'menu:File',
    'menu:Tools',
    'menu:Macro',
    'ribbon:Aero',
    'ribbon:Model'
)
```

Each successful run writes an ignored JSON receipt under
`.vibecad-dev\tours`. The receipt binds every target to the exact process ID,
semantic index, geometry, selected or visibly-observed menu activation,
restored interaction postcondition, Qt input method, and
`physical_cursor_control: none`. A person may continue to move their own
physical mouse during the tour; that independent movement is sampled but is
never blocked or redirected.

Receipts use the versioned `vibecad.visible-operator-receipt.v1` schema. The
complete payload, including its absolute `receipt_path`, is finalized once; the
same serialized JSON is returned to the caller and preserved on disk. Default
names combine a high-resolution UTC timestamp with a random nonce.
Writes use create-new semantics, so a concurrent collision or an explicit
`-ReceiptPath` that already exists fails rather than replacing earlier evidence.

## Reopen without rebuilding

When the checkout has not changed and you only need to reopen the existing local
development build:

```powershell
.\Launch-VibeCAD-Dev.ps1 -SkipRebuild
```

`-SkipRebuild` does not allow the script to fall back to an installed VibeCAD or
external Qt installation. If the repo-local environment, GUI executable,
`qwindows.dll`, matching Qt DLL directory, or required Qt DLLs are missing,
launch fails with the incomplete component named before `Start-Process`. It
still waits for the checkout-scoped authenticated control channel, so a
successful reopen remains directly controllable and observable.

`-SkipRebuild` deliberately does **not** create or export build/launch
attestations, and the endpoint/status `runtime_identity` must remain `null` for
that compatibility path. The visible marker still names the commit selected by
the launcher, but a skipped rebuild is not evidence that the existing binary or
installed modules were produced from that commit. Do not use it for release
acceptance. `-SkipRebuild -ReleaseAttestation` is refused.

## Requirements

- Windows
- Git
- Pixi
- the normal VibeCAD Windows build dependencies required by the Pixi package

The launcher looks for `pixi.exe` on `PATH` and then at
`%USERPROFILE%\.pixi\bin\pixi.exe`.

Git submodules do not have to be initialized by a separate manual step; the
launcher initializes the revisions pinned by this checkout before invoking
Pixi.

## What this launcher is not

This is a developer/test entry point. It does not replace the packaged
root-level `VibeCAD.exe` or the Windows installer produced by the release
bundling pipeline.
