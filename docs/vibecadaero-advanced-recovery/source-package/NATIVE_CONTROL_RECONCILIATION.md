# Native Control Reconciliation — Pass 03 Correction 01

## Frozen state

- Pass 02: `d0a933e40005b4affe9303f27d1eae5cd36eb030`
- Pass 03: `df07a5e82ec2fb31515e10b33822253d69d496ff`
- Delta: 41 commits / 50 files

## Decision

VibeCAD's Native preview system is now the canonical host authority for reversible CAD mutation proposals. VibeCADAero must not build a second general-purpose preview UI, preview dispatcher, reject/apply broker, user-intent-preservation mechanism, or raw-exec mutation path.

## Host capabilities now available

The current host has:

- per-document structural revision;
- preview IDs bound to an expected revision;
- stale rejection;
- one-shot consumption/rejection;
- pending-preview listing;
- dispatcher-backed apply of the stored proposal arguments;
- in-app apply/reject commands;
- default-off automatic apply;
- preservation checks for `user_explicit` intent;
- mutation receipts and bounded verified-result memory;
- external agent Native sessions with explicit held-session status and idle closure.

## What Aero should do immediately

### A. Thread the actual host revision into the existing Aero repair seam

`VibeCADAero.propose_repairs()` and `apply_repairs()` already accept `native_revision`; the external `/v1/aero` route still calls them without the host revision. Resolve the active document UID/current structural revision from the same `native_document_state_store()` used by Native dispatch and pass it to both operations.

### B. Preserve the Aero geometry fingerprint

`AeroPreview.geometry_revision()` remains useful as an engineering identity. Native revision answers “did the host document structurally change?”; the Aero fingerprint answers “is the resolved aerodynamic geometry/configuration exactly the same?” They are complementary, not duplicates.

### C. Converge CAD-changing Aero repairs onto host Native preview authority

Once `aero.*` mutations can be represented on the frozen Native/tool surface without distorting Aero semantics, use host propose/apply/reject for mutation authorization. The Aero payload should remain domain-specific evidence; the host owns authorization and revision checks.

### D. Preserve user-explicit intent

Current host preview application can assert that `user_explicit` intent rows remain unchanged. Aero repair application should participate in this invariant. A stability repair may change CAD/configuration only in the fields explicitly represented by the accepted proposal; it must not silently rewrite user-stated targets/preferences.

## What Aero must NOT do

- Do not make a CFD solve a Native preview.
- Do not bind long CFD jobs to the 300-second held Native agent-session lifetime.
- Do not use `/v1/run` to bypass Aero/Native mutation authority.
- Do not reconstruct preview arguments from UI state when the host stores the proposal.
- Do not infer that successful apply makes a solver model qualified.

## Long-running CFD relationship to Native state

The **host Analysis Runtime job** captures generic job/artifact/provider identity, while the Aero adapter contributes domain dependencies including:

- document UID;
- host Native revision where relevant;
- Aero geometry revision/hash;
- case hash;
- frame/reference/config dependencies;
- frozen solver-input/mesh hash.

The target does not make global Native revision the only future CFD currentness rule; Aero contributes the precise relevant dependency set. Current FEM exactness behavior is preserved during host extraction.

The compute may run after the Native session closes. When the result returns:

- exact captured state → eligible to attach as current Aero evidence;
- changed host/geometry/case state → preserve as stale historical evidence, never silently replace current evidence.

## Remaining host preview-store issue

Current preview records live in a plain dictionary. Consume/reject marks records but does not establish a finite retention bound, and persisted Native-state export is receipt-oriented rather than outstanding-preview restoration. Do not make durable CFD lifecycle depend on this store. A host cleanup/restart policy is still desirable as a general Native correctness improvement.


## Correction 01 relationship to host job migration

Native preview authority and the Analysis Job Runtime remain separate host mechanisms. Preview authorizes CAD mutation. Analysis jobs own long-running evidence-producing execution. When a current analysis result is published into the document, publication returns to document-thread Native mutation authority; the job worker never mutates FreeCAD directly.
