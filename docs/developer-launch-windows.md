# Launch the current VibeCAD checkout on Windows

Use the repo-root development launcher when you want to test the code in this
checkout without starting the VibeCAD copy installed through the normal Windows
installer.

## Normal use

Double-click:

```text
Launch-VibeCAD-Dev.cmd
```

or run:

```powershell
.\Launch-VibeCAD-Dev.ps1
```

The launcher reads `version.json`, prints the current version/build and Git
revision, then installs or rebuilds `vibecad` inside this checkout's
`package\rattler-build\.pixi\envs\default` environment. It launches only the GUI
executable found inside that environment.

It never searches the Start menu or `Program Files`.

When the application starts through this launcher, the status bar shows:

```text
VibeCAD DEV • <commit>
```

and the main-window title receives the same development marker. This makes a
development session visibly different from an installed release build.

## Reopen without rebuilding

When the checkout has not changed and you only need to reopen the existing local
development build:

```powershell
.\Launch-VibeCAD-Dev.ps1 -SkipRebuild
```

`-SkipRebuild` does not allow the script to fall back to an installed VibeCAD.
If the repo-local environment or executable is missing, launch fails.

## Requirements

- Windows
- Git
- Pixi
- the normal VibeCAD Windows build dependencies required by the Pixi package

The launcher looks for `pixi.exe` on `PATH` and then at
`%USERPROFILE%\.pixi\bin\pixi.exe`.

## What this launcher is not

This is a developer/test entry point. It does not replace the packaged
root-level `VibeCAD.exe` or the Windows installer produced by the release
bundling pipeline.
