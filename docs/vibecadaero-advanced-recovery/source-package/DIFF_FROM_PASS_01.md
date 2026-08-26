# Diff From Reconciliation Pass 01

## Baseline movement

| | Pass 01 | Pass 02 |
| --- | --- | --- |
| VibeCAD SHA | `b10005fa18f218d1c7bcb5880a3689a890af5628` | `d0a933e40005b4affe9303f27d1eae5cd36eb030` |
| VibeCAD delta | — | 35 commits ahead / 0 behind |
| FluidX3D pin | `8986874...` | unchanged |
| CfdOF pin | `a90f60c...` | unchanged |
| Pure overlay tests | correction history drifted among 6/9/10 | 14 reproducible tests |

## Upstream architectural change

Pass 01 saw Native preview/apply emerging. Pass 02 freezes it as a broad host platform seam. The delta adds/extends preview/apply for revolve, helix, loft, sweep, fillet/chamfer, Boolean cut/join/intersect, scale, linear/circular/mirror patterns, thickness, draft, holes and history deletion, plus dispatcher-visible pending previews.

This changes the Aero integration target:

- CAD-changing Aero authorization should converge on host Native revision/preview/receipt semantics.
- Aero keeps its geometry fingerprint as engineering identity.
- long-running CFD jobs use a separate durable Aero job lifecycle.

## New live gap discovered

Current `/v1/aero` directly calls `VibeCADAero.propose_repairs()` / `apply_repairs()` without supplying their optional `native_revision`. Pass 02 makes threading host revision into this path an early implementation task.

## New upstream bug/hazard recorded

The host preview store remains an unbounded dictionary. Consumed/stale previews remain stored, while persistence exports receipts but not outstanding previews. Pass 02 records finite cleanup/restart semantics as an upstream correctness task.

## New reference modules

Pass 02 adds:

- `AeroNativeBridge.py` — host Native revision + Aero geometry attachment decision;
- `AeroJobStore.py` — durable long-running job lifecycle/reference persistence;
- `AeroRouting.py` — deterministic/explainable auto routing;
- `AeroQualification.py` — versioned benchmark/envelope qualification evidence;
- `tests/conftest.py` — self-contained bare-pytest import path.

## Plan expansion made explicit

Pass 02 formalizes previously under-specified accepted scope:

- full job UI/lifecycle;
- interactive CFD/FluidX3D field viewer;
- Kaggle telemetry-based resource forecasting;
- high-Re FluidX3D qualification matrix;
- OpenFOAM physics template ladder;
- moving/rotating bodies;
- propulsion-airframe interaction;
- complete 6-DOF force-provider contract;
- FSI/aeroelasticity;
- artifact/cache lifecycle;
- solver qualification records;
- executable refinement/knowledge loop.

## Product-information corrections fully merged

Pass 02 removes correction-document drift from the canonical surface:

- VibeCAD/VibeCADAero is not characterized as non-commercial because of FluidX3D.
- FluidX3D terms remain component-specific.
- no software purpose/compliance gates are planned.
- no invented “commercial Aero profile.”
- one first-Aero-entry informational notice only.
- exact checkbox: **“I understand.”**
- one local unversioned acknowledgement bit; normally never shown again.
- no change to CAD design ownership/licensing from that notice.

The old correction sequence is retained only in `history/PASS_01_CORRECTION_HISTORY.md`.

## Validation correction

Pass 01's archived test outputs and prose drifted across correction passes, and bare `pytest -q` required hidden import-path setup. Pass 02 requires the package itself to be directly testable. Current Pass 02 result: **14 passed** with bare `pytest -q`.
