# Changelog

## 0.2.0 — 2026-09-05

### Added
- Unified **Bend Presets (SCS + Custom)** dialog for SheetMetal (source dropdown).
- Full SendCutSend public bend table + user JSON custom presets.
- Apply to bends, create Unfold material sheets, set SheetMetal defaults.
- Min-flange warnings on Apply.
- Body selection expands to Bend features.
- Unfold K-factor / Material Sheet sync (name-safe FreeCAD object fetch).
- **Pending Unfold sync**: Apply before Unfold remembers preset and auto-applies when Unfold is created.
- Runtime injection into SheetMetal toolbar/menu (no SheetMetal source fork required).

### Notes
- Flat-pattern math uses the material sheet K when selected; Manual K-Factor spin stays grayed out by SheetMetal design — this addon syncs the property for display consistency.
- Bend table retrieved from SendCutSend public calculator (2026-09-05); re-verify if they update.

## 0.1.0 — 2026-09-05

- Initial SendCutSend presets workbench + custom presets + packaging.
