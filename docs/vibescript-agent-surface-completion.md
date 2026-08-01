# VibeScript agent-surface completion

This ledger covers general platform issues found during real agent use. It
excludes geometry choices specific to the jet-engine request. Existing public
calls remain supported; new capability uses the same source/revision graph
model.

## Done means

- [x] `vibescript.read_source` supports exact line ranges and bounded diagnostics
  without hiding the complete-source read used before an edit.
- [x] `vibescript.read_api` supports named and grouped reads, with discoverable
  group names and exact callable contracts.
- [x] A saved program can be built again without changing its source, and a
  validated unpublished candidate has an explicit recovery action.
- [x] Failed and cancelled candidates report a concise cause, current phase or
  operation, the last completed graph operation when one exists, available
  phase/feature timings, retained-output state, cancellation source, and limits.
- [x] Material cards are searchable from every surface that can assign them.
- [x] Part Design documents exact constraint kinds/forms, selector types and
  return shape, fastener options, semantic-interface schemas/examples, display
  modes, `doc`, first-feature behavior, and `DomainValue` categories.
- [x] Part Design sketches support the existing principal planes, a correctly
  named parallel offset, explicit arbitrary placement, and stable attachment;
  `z_offset_mm` remains a compatibility alias.
- [x] Axis/origin inputs use one documented origin-plus-direction convention;
  legacy axis shorthands remain supported and are explained.
- [x] Geometry checks cover real shape bounds, distance/clearance, interference,
  wall thickness, and mass properties rather than helper geometry arithmetic.
- [x] Assembly linked instances, hierarchy, joints, motion, and Part Design
  interface handoff are obvious from the Model and Assembly API descriptions;
  no duplicate B-rep instancing API is added to Part Design.
- [x] Assembly discovers stable public outputs rather than implementation Bodies,
  states each reference's rigid-motion boundary, exposes exact connector frames,
  and enumerates every accepted native joint kind and its required parameters.
- [x] Part Design regeneration treats the native operation's output map as the
  ownership authority, repairs stale Body ownership tags deterministically, and
  reports every conflicting legacy Body by name instead of guessing.
- [x] Semantic-interface names are local to each published output, so reusable
  names such as `RotationAxis` work across parts while legacy unique-name reads
  remain compatible.
- [x] Failed publication aborts its transaction before restoring presentation;
  presentation targets are reacquired by native object identity so Link view
  properties cannot be restored through stale GUI wrappers.
- [x] Every workbench exposes the same workbench-neutral VibeScript source
  lifecycle. Assembly turns inject a compact copy-ready component inventory;
  catalog search filters the retained turn snapshot only when needed and offers
  explicit provider-byte-safe compact pagination for complete inventories.
- [x] Focused tests, worker integration tests, and the release build pass.

## Implementation order

1. Focused source/API reads, build recovery, material search, and diagnostics.
2. Exact API contracts and typed graph inspection.
3. Sketch placement/attachment and consistent axes.
4. Geometry-derived verification and Assembly handoff validation.
5. Full verification and documentation closeout.

## Deliberate compatibility

- Existing tool names, call signatures, result keys, and accepted source remain.
- Workbench-qualified lifecycle tools remain callable compatibility aliases;
  the operating model sees only the canonical workbench-neutral names.
- New read filters are optional; a full read remains available.
- `z_offset_mm` and existing axis strings remain accepted while clearer forms are
  added.
- `api.measure` remains valid while new geometry-derived quantities are added.

## Verification

Verified on 2026-08-01 with the release build, 550 Python contract tests, full
native Part Design and Assembly lifecycle gates, focused App and TechDraw C++
tests, and live GUI lifecycle gates for Part Design, Assembly, CAM, Drawing,
FEM, Material, Inspection, Mesh, MeshPart, Points, Reverse Engineering, Robot,
and the generic domain publisher. Part Design additionally covers stale output
ownership repair, repeated local interface names, and post-abort Link
presentation restoration.
