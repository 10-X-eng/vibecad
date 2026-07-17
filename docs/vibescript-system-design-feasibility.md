# VibeScript system-design architecture

## Scope

VibeScript is the selected scripted component-modeling engine for VibeCAD. When
selected, its tools accompany the native tools for the active supported
workbench. This document defines the durable model boundary, downstream
reference behavior, execution/threading contract, and remaining product-scale
limits as implemented on 2026-07-17.

The important distinction is between:

- **private implementation history**, which a script may replace on every run;
- **published product objects**, whose FreeCAD identities remain stable; and
- **downstream semantic contracts**, which describe what a consumer means to
  reference instead of trusting a transient `FaceN` or `EdgeN` name.

## 1. Workbench surface

The selected Part Design scripted engine is the source of truth. VibeScript is
surfaced only when that selected engine is `vibescript`.

- Part Design retains its existing VibeScript-only scripted surface.
- Part, Assembly, FEM, TechDraw, CAM, Material, Spreadsheet, Mesh, Surface, and
  other registered user workbenches retain their native pack and additionally
  receive the VibeScript tools.
- BIM excludes VibeScript by default. `VibeScriptOnBIMEnabled` is the explicit
  opt-in preference.
- Test, no-workbench, and unknown surfaces do not receive VibeScript.
- Selecting build123d, OpenSCAD, or native Part Design removes VibeScript from
  the other workbench surfaces.

Every provider turn receives one validated turn-start snapshot. Native tools
may coexist with at most one scripted engine. ChatGPT subscription and API-key
providers use the same surface rule. The declared schema is frozen for the
turn, while every attempted call is re-authorized against the live workbench
and edit state. A declared tool that is no longer live is rejected; a newly
available tool is usable on the next turn.

The VibeScript model summary and interface context follow the actual surfaced
capability. Context is not injected merely because Part Design once selected an
engine, and it is not duplicated when Part Design already supplies it.

## 2. Durable model boundary

One VibeScript model has a persistent 32-character `model_id`, source,
parameters, expected output keys, working revision, and accepted revision. The
revision is derived from source, parameters, and expected outputs, and updates
use an expected-revision guard.

The accepted document representation has five roles:

1. A stable `App::Part` model root.
2. A stable parameter object mirroring accepted numeric values for inspection
   and downstream expressions. VibeScript source and persisted parameters remain
   the geometry authority; changing the mirror alone does not regenerate output.
3. One stable, top-level `App::Link` publication per output key.
4. One stable private `Part::Feature` shape target per output key inside the
   model root.
5. A private implementation subtree created by the current script execution.

Script execution may delete and recreate the private implementation subtree.
External workbench objects must consume the stable publications, never private
implementation objects. Each public link resolves the exact private shape target
through its stable model root. On update, the target receives the new detached
shape while both target and publication retain their FreeCAD identities, output
key, placement in the model coordinate system, presentation, and
material-facing properties. Added output keys create a link/target pair;
removed keys are handled by the accepted output contract rather than silently
reassigned.

Legacy VibeScript models are migrated into this boundary. Existing downstream
links are retargeted to the stable publication during migration, and migration
fails instead of deleting an old object when a reference cannot be preserved.

### Output contract

The script must return a non-empty `result` dictionary whose ordered keys match
`expected_outputs`. Each output must be created by the current execution, have
a valid shape, and contain exactly one solid. A model may publish up to 64
outputs. Separate physical components therefore remain separate named outputs;
an assembly or compound is not smuggled through as one component.

## 3. Downstream reference contract

Stable object identity solves whole-object links such as `App::Link`, materials,
and view sources that point to a publication. It does not make topological
subelement names stable. A regenerated shape may legitimately assign `Face7`
to a different face.

VibeCAD therefore treats subelement references in two classes.

### Managed semantic references

The native tool records enough intent to resolve the reference again against
the new publication revision:

- Assembly joint attachments record published interfaces and component intent.
- FEM constraints record their geometric selection and model dependencies.
- TechDraw dimensions record their source/interface selection and are marked
  stale for an explicit projection refresh because TechDraw recompute is
  thread-affine.
- CAM operations record the source/interface selection used by the operation.
- Native Part fillets and chamfers record either a declared published interface
  or a count-guarded geometric query.

During publication, worker-safe managed references are resolved against the new
outputs. The operation reports exactly what was rebound and what was deferred.
TechDraw projections, FEM results, and CAM toolpaths are marked stale and must
be regenerated; VibeCAD does not claim old drawing, numerical, or manufacturing
results remain valid after geometry changes.

### Unmanaged references

If an external object holds a Face/Edge/Vertex reference into a regenerating
model and has no semantic contract, regeneration is rejected before mutation.
The result lists the unsafe consumers. VibeCAD does not guess, retain a stale
`FaceN`, or quietly sever the reference.

If a managed selector no longer resolves uniquely, regeneration fails and the
publication transaction is rolled back. Geometric change is allowed; silent
semantic drift is not.

## 4. Part workbench behavior

PartWorkbench receives its native Part tools plus VibeScript whenever
VibeScript is selected. This supports a practical workflow in which VibeScript
publishes durable component solids and native Part operations consume them.
Publications remain top-level links rather than children of the scripted
`App::Part`, so native Part features can consume them without creating illegal
out-of-scope links into another container.

For native Part fillet/chamfer consumers:

- direct scripted publications require a named published interface;
- a derived Part feature may use a count-guarded geometric query;
- exact `EdgeN` and unbounded `all_edges` selections are rejected when the
  operation depends on regenerating scripted geometry; and
- the selector is resolved again before the native feature recomputes.

Native Part/PartDesign carrier objects are traversed in dependency order from
the regenerated publication and recomputed through FreeCAD's worker queue.
Validation checks that each expected carrier
still exists, has a non-null valid shape, and preserves its preflight shape
class. Failure rolls back the publication and downstream rebind transactions.
For derived fillet/chamfer chains, count-guarded edge selection is resolved on
the provider worker after the source carrier recomputes; only the resolved
native links are applied on the document owner thread.

Thread-affine features are never forced through that worker. The strict
`Document.recomputeAsync()` API rejects an unsafe target and has no synchronous
caller-thread or GUI-thread fallback.

## 5. Execution and UI-thread contract

VibeScript source never executes in the GUI process.

1. The document owner thread captures only bounded identity metadata: active
   document, project scope, native VibeScript roots, current revisions, and
   immutable output shape handles. Provider context uses a topology-free revision
   token and bounded object summaries rather than a whole-document structural
   hash. It performs no VibeScript artifact I/O or broad geometry traversal.
2. The provider worker reads and writes project artifacts, validates source,
   resolves the sidecar executable, and prepares a revisioned candidate.
3. A windowless, headless `FreeCADCmd --safe-mode` sidecar creates and validates
   geometry in a temporary document.
4. The sidecar exports exact BREP plus a structured result manifest.
5. The provider worker imports detached BREP shapes.
   Published-interface predicates are resolved against those detached shapes;
   no face/edge traversal is deferred to the GUI thread.
6. The document owner thread performs a bounded publication transaction: stable
   shape-handle assignment, parameter synchronization, dependency rebinding,
   and stale marking. It does not deep-copy or revalidate accepted geometry.
7. Eligible native Part recomputes run on FreeCAD's recompute worker. The
   provider worker waits and advances the commit protocol without blocking the
   Qt event loop. Final OpenCascade shape-validity checks run against detached
   shape handles on the provider worker before the owner thread finalizes the
   transaction.
8. Only after native recompute and detached validation succeed are accepted
   source and manifest artifacts persisted on the provider worker. The final
   owner-thread continuation confirms the document did not change, then closes
   the rollback window. Artifact failure rolls back the native publication.

The sidecar is cancellable and bounded by timeout and memory limits. Its output
uses temporary files rather than unread pipes, so verbose source cannot deadlock
on a full OS pipe buffer. Windows children use `CREATE_NO_WINDOW`.

Document lifecycle calls from VibeScript source are rejected. Source cannot
create, open, close, save, restore, or replace the user's live FreeCAD document.

The model-code editor uses the same production lifecycle as AI-authored
VibeScript. Model lists, source inspection, legacy shape facts, source reversion,
candidate persistence, and artifact deletion run on background workers. The
editor does not execute a second in-process preview implementation and does not
compute a whole-document structural hash while loading a model.

Some work must remain on the document owner thread because FreeCAD document
mutation is thread-affine. The contract is deliberately narrow: no provider
request, scripted geometry generation, external-process wait, FEM solve, CAM
generation, TechDraw projection, or full document recompute is performed there
as part of VibeScript publication. Dependency discovery starts from FreeCAD's
native inbound-link graph instead of scanning every property in the document.
The atomic live object update scales with the affected dependency closure, not
the entire product, and does not execute geometry construction or validation.

Model deletion captures identity and dependency state, performs one bounded
native transaction, atomically quarantines artifacts on the provider worker,
and undoes the native deletion if that quarantine step fails. Failure to purge
an already quarantined tree is reported as cleanup debt rather than restoring a
model with partially deleted source. Deletion does not force a synchronous
whole-document recompute.

## 6. Failure and rollback behavior

Preparation, isolated execution, transfer validation, publication, semantic
rebind, and native recompute are distinct failure stages. A failed candidate is
recorded for diagnosis, while accepted source and geometry remain authoritative.

Publication records an exact pre-update document snapshot for the affected
scope and uses FreeCAD transactions for object/reference mutations. The
snapshot retains immutable pre-update shape handles instead of copying geometry
on the UI thread. If semantic rebinding or native Part validation fails after
publication, VibeCAD undoes its transactions, restores those shapes and prior
clean/touched state, and reports whether rollback completed. It does not
continue on invalid geometry.

Cancellation during the native refresh follows the same rollback path.
Artifact-persistence and deletion failures also use this explicit continuation
and rollback protocol; a committed native state is never treated as accepted
merely because worker-side file handling failed.

## 7. What this architecture now supports

- Revising a scripted component without replacing the publication objects used
  by Assembly, TechDraw, FEM, CAM, materials, or native Part operations.
- Creating or updating VibeScript geometry while working directly in Part,
  Assembly, FEM, TechDraw, CAM, and other supported workbenches.
- Rebinding worker-safe semantic subelement consumers, explicitly deferring
  thread-affine projections, or rejecting the update when intent can no longer
  be proven.
- Marking derived analysis/manufacturing data stale rather than presenting it
  as current.
- Saving, reopening, and migrating documents while preserving model ids,
  publication ids, parameters, contracts, and project artifacts.

## 8. Remaining limits

This work fixes regeneration identity and supported downstream reconciliation.
It does not turn one FreeCAD document into a complete enterprise product model.

- A semantic contract exists only for the explicitly supported native tools.
  Arbitrary third-party or manually authored subelement consumers fail closed.
- Topological changes can make a previously valid interface ambiguous or absent;
  the correct result is a rejected update requiring an intentional interface
  revision.
- FEM meshes/results and CAM paths are invalidated, not automatically regenerated.
- Thread-affine native features cannot participate in asynchronous Part refresh;
  they are rejected instead of freezing the UI.
- Product-level requirements traceability, BOM/configuration management,
  multi-document component versioning, and cross-document dependency pinning
  remain separate architectural work.
- Very large live-document publication transactions need measured performance
  data; the current boundary removes the dominant geometry/recompute stalls but
  does not claim owner-thread mutation has zero cost.

## 9. Verification

The implementation is covered at three levels:

- Provider/surface/engine tests verify mixed workbench surfaces, BIM opt-in,
  single-engine enforcement, subscription snapshot integrity, sidecar process
  bounds, and VibeScript lifecycle behavior.
- Native FreeCAD probes verify atomic multi-object Part recompute queuing,
  `RecomputePending`, completed shape updates, and fail-closed rejection of a
  thread-affine TechDraw target.
- A real FreeCAD integration creates and regenerates a VibeScript model consumed
  by Assembly, FEM, TechDraw, CAM, and native Part operations; it verifies stable
  publication identities, semantic rebinding, stale propagation, save/reopen,
  legacy migration, source failure rollback, and invalid native Part-result
  rollback.

The acceptance standard is not merely that a new solid appears. The document
must remain internally coherent through update, failure, cancellation, and
reopen, and no heavy VibeScript work may be shifted onto the UI thread.
