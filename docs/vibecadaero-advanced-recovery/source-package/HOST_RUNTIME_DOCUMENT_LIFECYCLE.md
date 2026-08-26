# Host Analysis Runtime — CAD Document Lifecycle Contract

## Principle

A completed computation is not authority to mutate whichever FreeCAD document happens to be open later. VibeCAD already has an exact document identity seam: `document_uid(document)` reads `Document.Uid`. Initial FEM migration preserves the existing strict `NativeRuntimeContext`/ticket behavior. Durable Aero jobs later rebind the exact source using host identity plus domain currentness and fresh publication authority.

Do not invent a second document-ID system merely for the runtime extraction. First characterize the host identity that already exists.

## Source-verified current FEM behavior

Current FEM job submission captures a live `NativeRuntimeContext` and original `NativeCallTicket`. Its commit callback later calls `run_immediate_mutation`, whose `NativeMutationRunner` requires the same document UID, a live document, active/reauthorized host context and the ticket's original expected structural revision. This is intentionally fail-closed.

**Migration rule:** retain that behavior exactly while process/job mechanics are extracted. Do not use durable Aero requirements as a reason to loosen FEM in the same change.

## Save / Save As / Save Copy characterization matrix

Before durable automatic reattachment is enabled, test real FreeCAD behavior for `Document.Uid` under:

- ordinary Save;
- Save As on the same live document;
- Save Copy / copy-on-disk then reopen;
- explicit document clone/duplicate operations if supported;
- close/reopen;
- recovery/autosave restoration;
- import into a new document;
- two copies opened simultaneously;
- duplicated labels and paths.

For each case record whether UID persists, changes, or can collide. Path/label/content similarity is never sufficient authority. If host identity is ambiguous, publication stays `AWAITING_SOURCE` until a safely revalidated explicit binding is made.

## Current document stays open and exact

### FEM compatibility phase

Publication proceeds only if the existing `NativeRuntimeContext`, original ticket/global revision, FEM exact solver state, exact History tuple and result-retention preference checks pass.

### Durable Aero target

Publication proceeds only after:

1. exact persisted job/submission/output identity validation;
2. exact source `document_uid` rebind;
3. domain target resolution;
4. `CurrentnessReport` against frozen Aero dependencies;
5. fresh Native publication authorization;
6. atomic/idempotent Native transaction and receipt.

## Active document switched

Initial FEM remains strict: do not silently publish if its current host guard rejects the context.

Durable Aero execution may continue because immutable solver work is not CAD mutation. When the computation completes, publication waits for the exact source to become safely publishable. It must never attach to whichever document happens to be active.

## Original document closed

Detached compute may continue if provider/shutdown policy supports it. Completed immutable outputs become `AWAITING_SOURCE`, not `FAILED`. No same-name or same-path substitution is allowed.

## Document closed and reopened

Treat reopening as a new binding event. Resolve `Document.Uid`, then revalidate domain dependencies. If identity/currentness cannot be proven, preserve the result as unattached evidence.

## Document changed while solve runs

Execution success is independent from publication currentness. Relevant dependency drift produces `STALE`/`QUARANTINED`. Unrelated edits should eventually be permitted for Aero only when the domain-scoped dependency model proves they do not affect the solved case. Initial FEM remains globally strict until separately changed.

## Source targets deleted/restructured

Publication does not heuristically recreate ownership. Missing/ambiguous targets produce stale/quarantined or awaiting-source disposition. Domain adapters own rebind/currentness meaning; the host does not infer FEM/Aero topology.

## Application shutdown/restart

Persist only inert job/provenance/artifact/publication descriptors. Never serialize live FreeCAD objects, `NativeRuntimeContext`, `NativeCallTicket` as executable authority, callbacks, Qt objects or transactions. On restart, recover/reconnect execution first. Publication is separately rebound and freshly authorized later.

## Publication receipt

One successful publication produces a stable receipt sufficient to identify the exact job/attempt/output manifest, document UID, publication adapter/recipe, domain currentness evidence, Native mutation receipt and created/changed result graph. Duplicate callbacks/reconnect/UI retries return that receipt rather than publishing twice.

See `HOST_ANALYSIS_RUNTIME_PUBLICATION_AUTHORITY.md` and `HOST_ANALYSIS_RUNTIME_IDENTITY_PERSISTENCE_AND_RECOVERY.md`.
