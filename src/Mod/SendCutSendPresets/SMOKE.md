# Smoke tests — SendCutSendPresets

## Automated (no FreeCAD GUI)

From this Mod directory:

```bash
python -m unittest tests.test_smoke -v
```

Covers: bend JSON shape, naming helpers, min-flange warn rule.

## FreeCADCmd (optional)

With FreeCADCmd on PATH and this Mod on `sys.path` / installed under `Mod/`:

```bash
FreeCADCmd -c "import json,os; p=os.path.join(os.path.dirname(__file__) if '__file__' in dir() else '.', 'data','sendcutsend_bends.json'); print('skip if needed')"
```

Preferred one-liner after install into a FreeCAD Mod path:

```bash
FreeCADCmd /path/to/Mod/SendCutSendPresets/tests/freecadcmd_smoke.py
```

(`tests/freecadcmd_smoke.py` creates a FeaturePython with KFactor and verifies setattr 0.4→0.5.)

## Manual GUI (SheetMetal required)

1. Restart FreeCAD/VibeCAD with SheetMetal + SendCutSendPresets installed.
2. SheetMetal toolbar: open **Bend Presets (SCS + Custom)**.
3. Source **SendCutSend library** → 5052 Aluminum / 0.063" → **Apply all**.
4. Confirm bends get radius/K; material sheet `material_SCS_5052_063` appears.
5. Unfold → pick that sheet → Data panel KFactor synced (or Apply again after Unfold).
6. Apply all *before* Unfold on a new part → create Unfold → Report shows auto-sync.
7. Switch to **My custom**, save a preset, Apply — sheet prefix `material_Custom_*`.

## vibecad CONTRIBUTING / AI-TDD note

- Unit tests above are the red/green-capable automated layer (data + pure rules).
- Full Apply/Unfold GUI depends on SheetMetal + Qt; covered by FreeCADCmd smoke + manual checklist rather than CI GUI automation in this PR.
