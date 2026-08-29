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
`version.json`, prints the current version/build and Git revision, then installs
or rebuilds `vibecad` inside this checkout's
`package\rattler-build\.pixi\envs\default` environment. It launches only the GUI
executable found inside that environment. If an interrupted first build left an
incomplete Pixi environment, the launcher recovers it instead of treating an
empty environment directory as a usable build.

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

When the application starts through this launcher, the status bar shows:

```text
VibeCAD DEV • <commit>
```

and the main-window title receives the same development marker. This makes a
development session visibly different from an installed release build.

## Control-ready contract

The development launcher gives this checkout its own ignored agent-control
directory:

```text
<repo>\.vibecad-dev\agent\
```

The bearer token and `endpoint.json` live there. This prevents a developer or
desktop agent from accidentally discovering a separately installed VibeCAD
session through the normal per-user endpoint. The launcher prints the visible
GUI PID and endpoint path, then waits for an authenticated `/v1/status` response
whose `channel` is `vibecad-agent-control` and whose `gui_up` value is true.
Success therefore means both of the following are true:

1. the current checkout's visible GUI process started; and
2. that GUI's authenticated, loopback-only control channel is ready.

The token is read from its file and is never printed or requested from a human.
The HTTP service binds only to `127.0.0.1`; this launcher does not expose a LAN
or Internet development server. See
[vibecad-agent-control.md](vibecad-agent-control.md) for the route schemas and
security contract.

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
semantic index, geometry, selected/menu-visible postcondition, Qt input method,
and `physical_cursor_control: none`. A person may continue to move their own
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

`-SkipRebuild` does not allow the script to fall back to an installed VibeCAD.
If the repo-local environment or executable is missing, launch fails.
It still waits for the checkout-scoped authenticated control channel, so a
successful reopen remains directly controllable and observable.

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
