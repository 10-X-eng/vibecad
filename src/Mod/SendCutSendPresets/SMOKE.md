# Smoke tests — SendCutSendPresets

## Automated (no FreeCAD GUI)

From the repository root, using Python with pytest installed:

```bash
python -m pytest -q src/Mod/SendCutSendPresets/tests
```

Covers data, naming, installed-module discovery, document ownership, undo
cleanup, one recompute per action, and pending Unfold lifecycle callbacks.

## Isolated GUI regression tests

Use a separate VibeCAD process with a private profile and temporary directory.
Install SheetMetal and its `networkx` dependency in that test environment. Add
`Mod/SendCutSendPresets` and its `tests` directory to the test process's
`sys.path`, then run:

```python
import unittest
suite = unittest.defaultTestLoader.loadTestsFromName(
    "gui_preset_update.TestPresetDocument"
)
result = unittest.TextTestRunner(verbosity=2).run(suite)
assert result.wasSuccessful()
```

The suite creates and closes synthetic documents. It checks one-step Undo,
SheetMetal's actual material-table reader, deferred Unfold creation, and a real
bent profile with the current Unfold engine. It must not run in a user's testing
instance. Run it against the built/installed module after a strict Release build:

```bash
cmake --build <strict-release-build-directory> --parallel 12
```

Configure with `CMAKE_BUILD_TYPE=Release`, `FREECAD_WARN_ERROR=ON`, and all
`FREECAD_USE_SANITIZER_*` options off for this performance test.

## Manual GUI (SheetMetal required)

1. Restart FreeCAD/VibeCAD with SheetMetal + SendCutSendPresets installed.
2. SheetMetal toolbar: open **Bend Presets (SCS + Custom)**.
3. Source **SendCutSend library** → 5052 Aluminum / 0.063" → **Apply all**.
4. Confirm bends get radius/K; material sheet `material_SCS_5052_063` appears.
5. Unfold → pick that sheet → Data panel KFactor synced (or Apply again after Unfold).
6. Apply all *before* Unfold on a new part → create Unfold → Report shows auto-sync.
7. Switch to **My custom**, save a preset, Apply — sheet prefix `material_Custom_*`.
8. Undo cancels any remembered pending preset for that document. Apply again
   before creating another Unfold if the preset is still wanted.

## Verification evidence

Regression tests are added before fixes. The GUI suite supplements the unit
suite because native mutation leases, queued recompute, and document observers
cannot be verified by mocks alone. PR validation records the exact commands,
red/green results, build configuration, and SheetMetal revision tested.
