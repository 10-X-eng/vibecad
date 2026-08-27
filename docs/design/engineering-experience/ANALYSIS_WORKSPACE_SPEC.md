# Analyze workspace specification

## Purpose

Modernize the existing Analyze surface into the first concrete projection of
the Engineering Experience layer while preserving existing FEM, VTK and
OpenFOAM behavior.

## Information architecture

- Existing ribbon/domain commands remain authoritative.
- The model tree continues to display document-owned Studies, Mesh and Results.
- The viewport presents the selected exact result field through the existing
  FEM/VTK/domain presentation path.
- The Engineering dock offers Overview, Results, Findings, Activity,
  Provenance, Workflow and Compare pages only when their backing contracts are
  available.
- Domain extensions provide structural, flow, thermal, electromagnetic and
  later coupled-analysis field categories without hard-coding the shell to CFD.

## Result-card contract

A result card may show a bounded summary metric, but must separately show:

- execution status;
- verification verdict;
- currentness;
- publication state; and
- domain claim ceiling where material.

Scientific red does not imply a failed verification. A successful result can be
stale. A verified result can be unpublished. A historical publication does not
become current after geometry changes.

## Field and legend contract

The field selector consumes a bounded field projection from the exact domain
result. It includes stable field reference, label, semantic, point/cell/object
association, component count, unit, numeric range and scalar/vector/tensor
presentation. Large VTK/FEM arrays remain with their renderer/data owner.

The legend always shows field, unit, minimum, maximum, range mode and scientific
notation where needed. Manual range, clamp, logarithmic display, colormap,
deformation factor, mesh edges and undeformed outline are scoped view state and
must not rewrite solver/result state.

## Progressive acceptance

- X0/X1: shell and common contract rendering with fixture-backed state axes.
- X2: durable attempt/activity/artifact/publication views with restart evidence.
- X5: real geometry-mesh-solve-postprocess-verify node progression.
- Structural acceptance: selecting a real published Von Mises/displacement
  field changes the viewport through the existing presentation owner and stale
  source is visibly rejected or historical.
- Flow acceptance: pressure/velocity/turbulence and existing flow metrics remain
  behavior-compatible after moving under the shared shell.
- Performance targets are measured, not assumed: ordinary panel refresh under
  100 ms and field switch under 150 ms on declared fixtures; large-data work is
  asynchronous when measurement requires it.

Screenshot comparison alone is never acceptance. Contract, GUI-object,
installed-tree and real domain integration tests are required in proportion to
the tranche.

