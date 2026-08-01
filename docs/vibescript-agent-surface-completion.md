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
- [x] Focused tests, worker integration tests, and the release build pass.

## Implementation order

1. Focused source/API reads, build recovery, material search, and diagnostics.
2. Exact API contracts and typed graph inspection.
3. Sketch placement/attachment and consistent axes.
4. Geometry-derived verification and Assembly handoff validation.
5. Full verification and documentation closeout.

## Deliberate compatibility

- Existing tool names, call signatures, result keys, and accepted source remain.
- New read filters are optional; a full read remains available.
- `z_offset_mm` and existing axis strings remain accepted while clearer forms are
  added.
- `api.measure` remains valid while new geometry-derived quantities are added.

## Verification

Verified on 2026-07-31 with the release build, 534 Python contract tests, the
focused App and TechDraw C++ tests, and live GUI lifecycle gates for Part
Design, Assembly, CAM, Drawing, FEM, Material, Inspection, Mesh, MeshPart,
Points, Reverse Engineering, Robot, and the generic domain publisher.
