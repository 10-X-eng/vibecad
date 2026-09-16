# SendCutSend Presets (FreeCAD)

Companion [FreeCAD](https://www.freecad.org/) workbench for [SheetMetal](https://github.com/shaise/FreeCAD_SheetMetal) that applies **SendCutSend** bend specs (and your own custom presets) to flanges and Unfold material sheets.

Bend numbers come from SendCutSend’s public [bending calculator](https://sendcutsend.com/bending-calculator/) (snapshot in `data/sendcutsend_bends.json`, retrieved 2026-09-05). Re-check their docs when tables change. **Not** an official SendCutSend product.

## Features

- **SendCutSend library** — material / thickness → radius, K-factor, bend deduction, relief, min flange, die, corner relief
- **My custom** — save shop-specific presets to user data (`SendCutSendPresets/custom_bends.json`)
- **Apply to bends** — radius + K on SheetMetal bend features (Body selection expands to bends)
- **Material sheets** — `material_SCS_*` / `material_Custom_*` for Unfold
- **SheetMetal defaults** — `defaultRadius`, `defaultKFactor`, `manualKFactor`, `kFactorStandard`
- **Unfold sync** — writes Manual KFactor + Material Sheet onto Unfold features for Data-panel consistency
- **Apply-before-Unfold** — remembers last Apply and auto-syncs when you create Unfold later
- **SheetMetal toolbar** — injects **Bend Presets (SCS + Custom)** at runtime (no SheetMetal fork)

## Requirements

- FreeCAD ≥ 0.21 (tested on 1.1)
- [SheetMetal](https://github.com/shaise/FreeCAD_SheetMetal) workbench installed

## Install

### From zip (manual)

1. Unzip so you have a folder named `SendCutSendPresets` containing `InitGui.py`, `package.xml`, etc.
2. Copy that folder into a FreeCAD `Mod` directory, e.g.:
   - Windows: `%APPDATA%\FreeCAD\Mod\`
   - Linux: `~/.local/share/FreeCAD/Mod/`
   - FreeCAD 1.1 may also use `v1-1\Mod` or `v26-3\Mod` — place it next to `sheetmetal`
3. Restart FreeCAD

### Addon Manager (after published)

Search **SendCutSend Presets** once listed in FreeCAD-addons / Addon Manager.

## Usage

1. Model a part in **Sheet Metal**.
2. Open **Bend Presets (SCS + Custom)** from the SheetMetal toolbar (or the **SendCutSend** workbench).
3. Choose **Preset source**: SendCutSend library or My custom.
4. Pick material + thickness (or edit/save a custom preset).
5. **Apply all (bends + sheet + defaults)** — or use the individual actions.
6. Unfold; material sheet should appear in Unfold’s Material Definition Sheet list. Manual K-Factor may stay grayed out while a sheet is selected (SheetMetal UI); the Data panel KFactor is synced to match.

Tip: SheetMetal **Engineering UX Mode** (`Edit → Preferences → SheetMetal`) helps avoid silent K defaults.

## Architecture (for reviewers)

| Module | Role |
|--------|------|
| `InitGui.py` | Registers workbench; starts SheetMetal integration + Unfold observer |
| `SCSCommand.py` | Standalone SCS library dialog (test bed) |
| `CustomPresets.py` | Unified SCS + Custom UI used on SheetMetal |
| `bend_actions.py` | Apply / material sheet / defaults / Unfold sync |
| `pending_unfold.py` | Document observer: Apply-before-Unfold auto-sync |
| `sheetmetal_integration.py` | Runtime toolbar/menu inject into SheetMetal |
| `data/sendcutsend_bends.json` | Snapshotted public bend table |

This addon **does not patch** SheetMetal’s `InitGui.py`. Integration is optional and non-invasive.

## Demo idea (~60s)

1. SheetMetal base + flange at a published thickness (e.g. 5052 / 0.063").
2. Bend Presets → that material/thickness → Apply all.
3. Unfold → select `material_SCS_…` → show flat pattern + Data panel K.

## License

MIT — see [LICENSE](LICENSE).

- SheetMetal remains LGPL-2.1.
- SendCutSend manufacturing data remains theirs; attribute the public calculator if you redistribute the JSON.

## Contributing / PR checklist

- [ ] Bump `version` in `package.xml` and `metadata.txt`
- [ ] Update `CHANGELOG.md`
- [ ] Keep SheetMetal integration runtime-only (no edits under `Mod/sheetmetal`)
- [ ] Re-verify `data/sendcutsend_bends.json` against the public calculator when refreshing numbers
- [ ] Test: Apply with Unfold present; Apply then create Unfold; Body-only selection; custom save/load
