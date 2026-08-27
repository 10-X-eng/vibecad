# VibeCAD Engineering Experience X-track

## Canonical role

The X-track is the human-facing projection of the governed-engineering G-track.
It is not a new owner, workbench, result model, scheduler, renderer, preview
controller, publication path, Manufacture model, Assembly graph, or Robot task
stack. Every X milestone is gated by and consumes the corresponding G contracts.

## Dependency and delivery matrix

| X milestone | Prerequisite | Exact initial owners/files | Bounded deliverable | Acceptance gate |
| --- | --- | --- | --- | --- |
| X0 — target and inventory | G0 | this directory; `VibeDark.qss`; `VibeLight.qss`; `VibeCADRibbon.cpp`; current domain GUI modules | Preserved north star, component/color/workspace specs, current GUI map, shell boundaries | Source hashes match; every depicted component classified; no capability claim inferred from image |
| X1 — common engineering presentation | G1 | `tool_impl/engineering_contracts.py`; new presentation-only view models under `src/Mod/VibeCAD`; existing domain adapters | Render result, metric, finding, provenance, execution, verification, currentness, publication and claim ceiling without flattening domain payload | Structural/FEM, Native, Aero, Manufacture, Assembly and Robot fixtures; all four result-state axes vary independently; installed imports |
| X2 — durable activity/artifact UI | G2 | Analysis persistence/artifact/publication facades; contextual Engineering dock | Attempt history, recovery state, artifact descriptors, retention/currentness decisions and publication receipts | Real restart/recovery display; exact identity; corrupt/missing/quarantined artifacts; no session-only state labeled durable |
| X3 — remote execution UI | G3 | provider contracts and activity page | Reconnect, polling/events, auth-expiry, cancel and transfer status for the selected real provider | One real restart/reconnect; duplicate/out-of-order events do not duplicate UI lifecycle or publication |
| X4 — governed preview/evidence UI | G4 | Native authority policy; existing preview controller; Finding/Preview layers | Policy-specific preview/confirmation/export treatment, affected identities, bounded diffs/evidence, expiry and fresh apply state | Representative operation per authority class; stale/authority-drift rejection; no accepted mutation from presentation |
| X5 — workflow UI | G5 | `analysis_workflow.py`; Activity/Workflow pages | Definition/run/node graph, deterministic readiness, retries, blocked/skipped/cancelled/interrupted state and publication eligibility | Real five-stage FEM workflow with injected failure/restart at every edge; UI exactly follows durable store |
| X6 — optimization UI | G6 | `governed_optimization.py`; Compare page | Candidate table, objectives/constraints, findings, rank, provenance branch, selected proposal and human publication gate | Independently enumerable design; duplicates/failures/stale candidates/restart; no candidate geometry silently accepted |
| X7 — Manufacture evidence UI | G7 | Native Manufacture Job/Inspect/Post/CAMotics/Simulation owners; Manufacture pages | Exact Job/toolpath/simulation/output evidence, progress, hashes, currentness, human destination/publication state | Existing CAM behavior A/B; real detached task; stale refusal; one receipt-bound attachment; no generic CAM authority |
| X8 — Assembly evidence UI | G8 | existing Assembly graph/interface/mechanism owners; Interface/Motion layers | Stable occurrence/interface/joint identity, compatibility/fit, diagnostics, sampled/continuous motion and flexible/closed-loop evidence | Save/reopen/rename/reorder/source replacement; representative rigid/flexible/closed-loop documents; no second graph |
| X9 — joint proposals | G9 | Assembly proposal contracts; connector/interface overlays | Ranked propose-only joint candidates with geometry/interface/compatibility evidence and explicit accept/reject | Bounded candidates; ambiguity and no-candidate fixtures; acceptance routes through Assembly owner only |
| X10 — assembly sequence | G10 | sequence contracts; Sequence/Motion/Finding layers | Step list, current/next/completed/blocked emphasis, access/collision evidence and sampled-versus-continuous verdict | Independently solvable fixtures, no-solution case, current graph checks, no continuous claim from samples |
| X11 — service/disassembly | G11 | service contracts; Service pages | Target, removal set, reverse sequence, tool/access assumptions, uncertainty and service claim ceiling | Verified target fixture and no-solution/uncertain cases; no shop-feasibility overclaim |
| X12 — Robot task handoff | G12 | Assembly-step projection and existing Robot owners; Task/Frame/Validation layers | Unit/frame/tool/TCP/force/torque/tolerance-explicit task view and downstream validation state | Frame/unit round trips; stale step, unsupported tool and unreachable handoff; trajectory/export traceability |

## Current implementation status

- **X0: documented.** The source material is preserved with hashes and the
  target, component, color, workspace, ownership and dependency contracts are
  repository-native.
- **X1: partial contract foundation.** `tool_impl/engineering_experience.py` and
  the installed `VibeCADEngineeringExperience.py` facade project the existing
  G1 envelope into bounded presentation metrics/fields, preserve exact common
  identities, findings, artifacts, provenance and opaque domain payload, expose
  execution/verification/currentness/publication as independent axes, and
  declare an inert no-authority surface. Cross-domain fixtures cover Native,
  FEM, Aero, Manufacture, Assembly and Robot. EVS-01 now gives the existing
  Analyze Results browser an accessible Engineering-shell identity and matching
  light/dark result-card styling while retaining every existing OpenFOAM
  control and presentation route. EVS-02 now adds an installed, bounded adapter
  facade over the existing legacy FEM, VTK and OpenFOAM metadata readers. It
  projects exact semantic, unit, association, component and available-range
  evidence without importing FreeCAD, reading/copying field arrays or taking
  presentation ownership; unknown units and ranges remain explicitly
  unavailable. The first EVS-03 browser slice now enumerates every real result
  accepted by the existing Native Analyze result-state owner, renders those
  bounded descriptors in the existing Analyze shell, preserves all flow
  controls, and labels governance axes unavailable when no G1 envelope exists.
  Remaining X1 work is attachment of exact G1 envelopes/status axes, richer
  result-card interaction and full owner-routed presentation acceptance,
  accessibility coverage, and installed application acceptance.
- **X2: partial durable-view foundation.** The installed facade projects
  validated G2 records, attempts, artifacts, currentness evaluations,
  publication evidence and restart dispositions with exact Analysis identity
  and explicit no-recovery/no-publication authority. Real Qt views,
  corrupt/missing/quarantined artifact presentation and restart acceptance
  remain.
- **X5: partial workflow-view foundation.** Validated G5 run records are
  projected into deterministic node summaries with exact attempts, outcomes,
  receipt references and state counts. The projection cannot schedule, retry,
  cancel or publish. Qt graph/activity views and five-stage failure/restart GUI
  acceptance remain.
- **X6: partial optimization-view foundation.** Validated G6 run records and
  the store's precomputed ranking are projected together, including inert
  mutation proposals, metrics, findings, constraints, currentness, selection
  and publication evidence. The projection cannot rank, select, mutate or
  publish. Compare UI, provenance branches and restart/staleness GUI acceptance
  remain.
- **X7: partial Manufacture evidence foundation.** A bounded projection now
  accepts only the existing Native Manufacture post owner's committed output.
  The post runtime now creates its exact durable G2 Analysis attempt and
  single-node G5 workflow run, pins authorized output descriptors, records
  path-free human authorization evidence and returns exact projection
  references. The projection rejects mismatched identities and unadmitted
  artifacts, retains Job/postprocessor/output hashes and unchanged-document
  evidence, and enforces `not_proven_toolpath`. The same exact linkage now
  projects CAMotics program/surface facts, live GL presentation evidence and a
  retained simulation Mesh/stock-removal summary under the stricter
  `simulation_evidence_only` ceiling. It cannot choose destinations, replace
  the Native simulation owners, mutate the Job, or certify manufacturability.
  Qt pages, A/B behavior, stale/restart and installed acceptance remain.
- **X8: partial Assembly identity/evidence foundation.** New Native-authored
  assemblies, joint groups, occurrences, regular joints and published
  interface LCS objects receive write-once versioned UUIDs, with connector
  identity derived from joint identity and explicit side. The Native Assembly
  simulation state hash and bounded solver diagnostics can be projected without
  creating a second graph. Counts, eligible joints and authored simulation
  summaries remain source-owned; continuous-motion certification is false and
  joint, sequence and service proposal collections are deliberately empty.
  Legacy identity migration, real save/reopen/rename/reorder/source-replacement
  proof, flexible/closed-loop/contact evidence, continuous-motion certification,
  Qt overlays and currentness acceptance remain.
- **Compatibility boundary after X8 foundation:** the multi-variant
  `manufacture.post` wire contract retains its explicit `operation`
  discriminator for singleton provider projections, while single-purpose
  capabilities remain compact. Native workspace, Sketch edit and root Assembly
  activation now route through the installed host-surface authority owner;
  Native domain modules retain authorization, document-thread dispatch and
  exact post-state verification rather than directly invoking raw GUI surface
  methods.

## First PR-sized implementation sequence

The 2026-08-27 pivot makes the Analyze workspace the next visible product
surface. The supplied EVS-01 through EVS-09 labels are retained as PR-sized
Analyze delivery slices nested inside the canonical X-track; they are not a
replacement roadmap. EVS is not a new solver, result owner, renderer,
workbench or authority layer. The implementation sequence is:

1. **EVS-01 — design system and inert Analyze shell (X0/X1).** Extend the
   existing light/dark themes with semantic engineering selectors and create
   the non-authoritative contextual shell. Preserve every current Analyze and
   OpenFOAM action. No solver or presentation behavior changes.
2. **EVS-02 — bounded field registry and real adapters (X1).** Add canonical
   field descriptors and adapters over the existing legacy FEM, VTK and
   OpenFOAM state readers. Preserve domain payloads and arrays with their
   owners. Prove semantic, unit, association, component and range mapping with
   structural, flow and thermal fixtures plus installed imports. **Implemented
   as a bounded contract slice:** source-tree tests and explicit CMake/facade
   registration are present; an actual configured build/install-tree run
   remains part of repository packaging acceptance.
3. **EVS-03 — unified Results browser (X1/X2).** Put field selection, result
   cards, independent status axes, bounded metrics and provenance into the
   shared shell while retaining all current OpenFOAM controls and behavior.
   **Partial:** real field discovery/cards and explicit unavailable-state
   rendering are present; G1 envelope attachment, metrics/provenance views and
   installed GUI acceptance remain.
4. **EVS-04 — owner-routed viewport presentation (X1).** Route structural and
   flow field selection through the existing FEM/VTK/domain presentation
   owners. Never copy field arrays or create a second scientific renderer.
   **Partial:** human selection now performs a fresh result-state check and
   delegates supported legacy FEM fields to `femresult.resultpresentation`, VTK
   fields to the existing pipeline ViewObject, and flow fields to the existing
   OpenFOAM owner with rollback/refusal boundaries. Real installed GUI coverage,
   ambiguous same-name point/cell selection and unsupported legacy fields remain.
5. **EVS-05 — legend and colormap view state (X1).** Add named units, exact
   min/max, auto/manual/clamped range and a validated colormap registry.
   Palette choice remains presentation state and cannot alter engineering
   values, verdicts or currentness. **Partial contract foundation:** validated
   field selection, palette, range, legend, deformation and overlay state now
   exists independently of engineering data; owner capability/application and
   installed GUI acceptance remain.
6. **EVS-06 — deformation and scoped technical presets (X1).** Expose existing
   deformation scale, mesh-edge and undeformed-outline capabilities through
   scoped VibeCAD view state; do not globally rewrite user preferences. The
   bounded per-view state exists and the Analyze browser exposes deformation
   scale only for legacy FEM results whose existing Fem presenter owns that
   capability. Mesh-edge/undeformed controls, scoped presets and installed GUI
   acceptance remain.
7. **EVS-07 — engineering charts (X1/X5/X6).** Reuse the existing table,
   histogram and line-plot owners for real convergence, history and comparison
   series with declared axes/units. No cosmetic data. **Partial owner-backed
   slice:** the Analyze browser now discovers existing FEM post table,
   histogram and line-plot owners, projects bounded sample/range/axis/unit
   descriptors without copying value arrays, and invokes only the owner's
   rendering action after an exact streaming table-state freshness check.
   Dedicated convergence/history/comparison associations, governed G1/G2/G5
   series and installed GUI acceptance remain.
8. **EVS-08 — durable Analysis activity dashboard (X2/X5).** Connect exact G2
   attempts, artifacts, recovery/publication state and G5 workflow nodes to
   the shell. Progress and counters come only from durable owners. The G2/G5
   stores now provide bounded fail-closed discovery by exact document and
   Analysis identities. **Partial owner-backed dashboard:** the Analyze shell
   now projects and displays exact durable Analysis/workflow identities,
   lifecycle state, attempt/artifact counts and timestamps for the active
   document. Expandable owner-backed rows now expose every bounded attempt,
   admitted artifact, restart disposition, currentness record, publication
   evidence axis and workflow-node state carried by that same snapshot. Empty
   and failed discovery remain explicit. A debounced directory-event watcher
   re-arms as durable storage appears and refreshes the visible active-document
   projection after atomic record changes; it does not poll lifecycle state.
   Installed GUI acceptance remains.
9. **EVS-09 — result comparison (X6).** Present baseline/candidate metrics and
   field differences only when exact comparable sources and the owning
   ranking/result contracts exist; selection and publication remain gated.
   **Partial exact-source comparison:** the Analyze shell now compares normalized
   metrics only when IDs, units and qualifiers match, and field extrema only
   when semantic, association, components, units and presentation match. Both
   owner-state SHA-256 identities are required. Pointwise differences remain
   explicitly unavailable until a shared mesh/array owner exists; optimization
   ranking integration and installed GUI acceptance remain.

EVS-01 through EVS-09 are individually reviewable and must retain compatibility
with the existing Analyze UI. X3/X4, X7 and X8-X12 continue in parallel or
afterward only with their owning G slices. In particular, the current G8/G9
Assembly stack supplies identity, interface, semantic-geometry, live-scenario
and explicit coupling-evidence foundations; it does not authorize fabricated
Assembly overlays or mark X8/X9 complete.

## Shared invariants

- Presentation never grants mutation, execution, publication or export
  authority.
- Domain payloads remain lossless and domain-owned.
- UI state is explicitly separate from engineering state.
- Scientific palettes and governance status colors are independent.
- No value, chart, thumbnail, progress state or “verified/current/published”
  badge is fabricated.
- Historical/stale evidence may remain visible only with exact state labels.
- Large result arrays remain in owning data/rendering systems; shared contracts
  carry bounded descriptors and summaries.
- Accessibility, keyboard navigation, light/dark themes, localization-safe
  labels, DPI scaling and installed packaging are acceptance concerns, not
  polish deferred indefinitely.
