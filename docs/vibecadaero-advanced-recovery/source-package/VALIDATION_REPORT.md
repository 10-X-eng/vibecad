# Validation Report — Pass 03 Correction 01

## Final clean reference validation

From `proposed_overlay/`:

```text
python -m compileall -q .    PASS
pytest -q                    45 passed in 0.56s
```

No caller-supplied `PYTHONPATH` was required. Runtime caches are removed from the packaged tree after validation.

## What the 45 tests establish

The pure-Python/reference suite covers the existing Aero contracts from Pass 03 plus the deepened host-runtime migration invariants, including:

- Aero CFD case/frame/result contracts;
- one-time informational `I understand.` acknowledgement behavior;
- host evidence/geometry-readiness distinctions;
- solver qualification and deterministic routing reference semantics;
- Native revision/repair bridge reference semantics;
- detached input hashing/stale-result invariants;
- FluidX3D vendoring/default-vs-external reference behavior without product-wide license policing;
- dynamic stall, strip theory, unsteady and 6-DOF reference models;
- atomic cancellation-versus-publication ownership proof;
- durable publication descriptor/currentness proof: missing source waits, stale dependencies remain stale, current output without fresh host authorization waits, and an existing receipt prevents duplicate publication;
- documentation integrity for single execution authority, restart/Save-As handling, exact public Native job surface, FEM preparation-before-background-submit, `Document.Uid`, and no serialized standing Native mutation authority.

## Source-verified migration findings

The correction is anchored to immutable upstream `df07a5e82ec2fb31515e10b33822253d69d496ff`. The reviewed source establishes:

- `analyze.solver_execution` operation `run` creates the FEM background job;
- `native.job` exposes `status` and `cancel`, not a generic start/clear API;
- FEM request/input preparation occurs before `NativeBackgroundManager.submit()`; the worker runs an already-prepared detached request;
- current FEM publication preserves exact solver-state, History and result-retention checks and publishes through Native mutation machinery;
- the submission-time `NativeCallTicket` carries exact document UID and expected structural revision and is checked again by `NativeMutationRunner`;
- VibeCAD's exact document identity seam is `Document.Uid` via `document_uid(document)`;
- current cancellation has a narrow check-to-commit phase window that must be characterized/fixed as an isolated correctness change if reproduced;
- current process stop code directly owns the launched `Popen`; descendant process-tree behavior requires platform characterization rather than assumption.

## Architectural validation outcome

Correction 01 now specifies a non-destructive strangler migration rather than a big-bang scheduler rewrite:

1. characterize the existing FEM/Native Background behavior;
2. isolate lifecycle/process correctness fixes from architectural movement;
3. introduce neutral host contracts behind current facades;
4. extract local process/artifact/runtime mechanics without moving FEM engineering meaning;
5. migrate FEM one solver family at a time and prove observational parity;
6. stabilize before adding persistence;
7. persist inert job/artifact/submission/publication descriptors, never live FreeCAD/Native authority;
8. add a separate durable publication coordinator with exact source rebind, domain currentness and fresh Native publication authorization;
9. make Aero the second production client only after those gates pass;
10. add remote providers and retire duplicate internals only after import/call-graph/rollback audits.

## Not executed / not claimed

This package does **not** claim:

- live FreeCAD integration of the new host Analysis Runtime;
- actual FEM A/B parity after an upstream refactor;
- supported-platform descendant process-tree cleanup;
- SQLite persistence/recovery implementation;
- durable publication coordinator integration;
- OpenFOAM/CfdOF live solve;
- FluidX3D bridge build/live solve;
- Kaggle live job;
- upstream CI/release packaging.

Those remain gated implementation work after a fresh upstream freeze and explicit authorization.

## Upstream drift at finalization

Live `main` was rechecked and remained `24fe48bb3fdcb84b558d34e23fedb0988ee4e548`, four commits ahead of the Pass-03 design anchor. The observed delta remains limited to Native preview ribbon/UI/CMake registration and does not modify the reviewed Background/FEM execution boundary. The correction therefore remains anchored to `df07a5e…`; implementation still requires a new freeze/reconciliation.

## Upstream writes

**NONE.**
