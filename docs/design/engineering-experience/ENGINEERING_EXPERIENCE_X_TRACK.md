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
  FEM, Aero, Manufacture, Assembly and Robot. Remaining X1 work is integration
  with real domain result adapters and the Qt Engineering dock, GUI/accessibility
  tests, and installed application acceptance.
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
  evidence, and enforces `not_proven_toolpath`. It cannot choose destinations,
  write files, mutate the Job, or certify manufacturability. CAMotics/simulation
  projections, Qt pages, A/B behavior, stale/restart and installed acceptance
  remain.
- **X8: partial Assembly evidence foundation.** The Native Assembly simulation
  state hash and bounded solver diagnostics can now be projected without
  creating a second graph. Counts, eligible joints and authored simulation
  summaries remain source-owned; continuous-motion certification is false and
  joint, sequence and service proposal collections are deliberately empty.
  Stable persisted interface/occurrence identity, flexible/closed-loop/contact
  evidence, Qt overlays and save/reopen/currentness acceptance remain.
- **Compatibility boundary after X8 foundation:** the multi-variant
  `manufacture.post` wire contract retains its explicit `operation`
  discriminator for singleton provider projections, while single-purpose
  capabilities remain compact. Native workspace, Sketch edit and root Assembly
  activation now route through the installed host-surface authority owner;
  Native domain modules retain authorization, document-thread dispatch and
  exact post-state verification rather than directly invoking raw GUI surface
  methods.

## First PR-sized implementation sequence

1. Complete X1 real domain adapters and the non-authoritative shared shell over
   the already-landed common engineering envelope.
2. Connect the X2 durable activity/artifact/publication projection to the Qt
   activity views; leave unintegrated durability visibly partial.
3. Connect the X5 workflow projection to the Qt graph/activity views.
4. Connect the X6 candidate/ranking projection to the Qt Compare view.
5. Wire the G7 post runtime to durable G2/G5 records and the X7 evidence
   projection, then extend the same owner-preserving pattern to CAMotics and
   simulation. Manufacture does not receive a disconnected generic result UI.
6. Advance X3/X4 and X8-X12 only with their owning G closure slices.

This order reflects the current repository, where G1, the G2 core, G4 census,
G5 core and G6 core exist but remain partial at their real integration gates.

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
