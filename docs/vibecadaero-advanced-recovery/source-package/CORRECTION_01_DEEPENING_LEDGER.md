# Correction 01 Deepening Ledger

| ID | Finding / decision | Disposition | Consequence |
|---|---|---|---|
| D01 | Host-owned generic Analysis Runtime remains target | CANONICAL | One runtime serves FEM first, Aero second. |
| D02 | FEM state/input identity is domain-specific | CANONICAL HARD BOUNDARY | Never move its semantic hashing into generic runtime. |
| D03 | `analyze.solver_execution` is stable FEM public seam | CANONICAL | Generic runtime sits underneath existing binding/runtime. |
| D04 | `native.job` is existing lifecycle control seam | CANONICAL COMPATIBILITY | Preserve status/cancel contract during migration. |
| D05 | Current cancellation has a source-level check→commit race window | CANONICAL HAZARD | Stress-test/reproduce, fix atomically in a dedicated correctness slice, then re-baseline before extraction. |
| D06 | Current process stop controls direct parent only | CANONICAL SOURCE FACT / RISK | Characterize descendant behavior per platform; if leakage is confirmed, harden process-tree ownership in a dedicated slice before provider extraction. |
| D07 | Current background manager is in-memory | CANONICAL CURRENT FACT | Durable recovery is new functionality, not existing parity. |
| D08 | Prepared FEM request may hold live FreeCAD objects | CANONICAL CURRENT FACT | Split durable descriptor from ephemeral runtime handles. |
| D09 | Exact document UID/revision revalidation is correctness-critical | CANONICAL | Preserve initial FEM behavior exactly. |
| D10 | A same-name reopened document is not sufficient identity | CANONICAL | No stale auto-attachment. |
| D11 | Local job cannot be honestly resumed after host restart without reattach proof | CANONICAL | Mark interrupted/failed; keep artifacts; explicit resubmit. |
| D12 | Remote provider may reconnect only with authoritative external ID | CANONICAL | Reconnect never bypasses document/domain currentness. |
| D13 | Persistence stores inert descriptors, never Python/FreeCAD executable objects | CANONICAL | Versioned durable schema. |
| D14 | Cleanup must be effect-idempotent | CANONICAL | Safe under cancel/failure/recovery/shutdown convergence. |
| D15 | Initial one-job-per-document behavior is compatibility policy | MERGED | Preserve first; generalize only later with explicit locking design. |
| D16 | Existing parent-only process cleanup should be preserved exactly | SUPERSEDED | User-visible cancel semantics preserved, implementation hardened to owned process tree. |
| D17 | Existing upstream tests already prove all required parity cases | SUPERSEDED | Missing cases must be added as characterization tests before extraction. |
| D18 | `interrupted` should immediately become a new public job status | SUPERSEDED | Map to compatible `failed` + structured failure kind until schema versioning. |
| D19 | Persistence must be built before runtime extraction | SUPERSEDED | First prove boundary/parity; add durability after stable host core. |
| D20 | Current main drift invalidates this correction | SUPERSEDED | Drift checked; boundary unchanged at observed `24fe48b…`; fresh freeze still mandatory before writes. |

| D21 | FEM input writer preparation occurs before background submit | CANONICAL SOURCE FACT | Preserve current thread boundary during extraction; worker receives sealed request. |

| D22 | Durable jobs need publication authority distinct from submission | **CANONICAL** | Persist inert submission/publication provenance, never live Native authority; reacquire fresh publication authorization after exact source/currentness validation. |
| D23 | Current FEM original-ticket/global-revision semantics | **PRESERVE DURING EXTRACTION** | Do not weaken while extracting generic process/job infrastructure. Any later FEM invalidation change is separate. |
| D24 | `Document.Uid` already provides host document identity seam | **CANONICAL / CHARACTERIZE** | Do not invent second ID; test Save/SaveAs/copy/reopen/collision behavior before durable auto-reattach. |
| D25 | Current public job API is `analyze.solver_execution/run` + `native.job status/cancel` | **SOURCE-VERIFIED** | Do not invent start/clear or rename during migration. |
