# Upstream SheetMetal

Imported from https://github.com/shaise/FreeCAD_SheetMetal at
`d99e89ada00c9de85243878536c52ede1bcb8953` (0.8.23, 2026-09-13).
Original authorship, license, module names, and command IDs are retained.
Repository hosting/editor configuration is not imported. See LICENSE and the
copyright notices in each source file for attribution.

VibeCAD additions:

- CMake build/install integration with runtime icons, panels, and translations.
- Optional document-owner arguments for material lookup and generated sketches;
  existing callers retain their active-document defaults.
- Defer worker-produced view changes to the GUI using the existing native frame
  dispatcher and verify document/object identity before applying them.
- Legacy sketch fallback updates only its owned target, resets partial Draft
  placement, and retains the sketch identity. Sew recovery uses the solid owner.
- Native regression tests for cross-document Unfold operations and GUI ownership.
- Additive `SheetMetalMapping.unfold_with_mapping` API retaining upstream
  planar/cylindrical correspondence and allowance-aware inverse point maps.
  Legacy `unfold` still returns the same two lists and keeps its error policy;
  the new editable mapping path rejects incomplete bend maps.
- Additive `SheetMetalEditGeometry` preparation and through-cut operations shared
  by folded and flat geometry. Curved cuts are split by sheet region and mapped
  using rational curves in cylinder UV space. Wire validation and offset solids
  follow the upstream bending approach; no existing feature proxy is replaced.
- Additive `SheetMetalEditable` feature retaining an upstream source reference,
  versioned circular-cut history, allowance/material inputs, and both derived
  solids. Native async recompute, Undo/Redo, persistence, invalidation, and failed
  feature repair are exercised in isolated document tests.
- Native Sketcher profiles in that same definition, using upstream planar-face
  attachment and shared bend-cut geometry. Stable native links support constraint
  edits, folded/flat handle mapping, save/reopen, recursive copy, and repair of
  missing/open profiles. Existing circular operations remain available.

These compatibility changes were first tested in local SheetMetal commits
`118247e` and `d5e1efe`; they are not represented as changes already accepted by
upstream. This directory is the development copy for the VibeCAD integration,
not an externally published fork. Subsequent SheetMetal feature changes must
retain these compatibility paths and attribution.

The V2 unfolder requires NetworkX, included in VibeCAD's Python requirements.
