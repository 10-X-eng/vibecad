# Host Analysis Runtime — Architectural Decision Record

This document records decisions that are easy to blur during a large refactor.

| Decision | Canonical outcome | Why |
|---|---|---|
| Who owns job identity/lifecycle? | VibeCAD host | execution mechanics are cross-domain infrastructure |
| Who owns physics/case meaning? | domain adapter (FEM/Aero/etc.) | prevents host from encoding solver/domain assumptions |
| Who owns where a job runs? | compute provider | local/Kaggle/remote is orthogonal to physics |
| Who owns CAD mutation? | existing Native mutation authority | preserves document transaction/receipt semantics |
| Does submission authorize later CAD mutation? | no | durable jobs retain provenance, not standing mutation permission |
| How is durable publication authorized? | fresh host publication authorization against exact completed job + exact current document | avoids serialized/stale Native authority and unsafe reattachment |
| Is the original NativeCallTicket reused after restart? | no as authority | it may be retained only as inert provenance; live publication authority is reacquired |
| Who owns qualification? | domain evidence/qualification | process success is not engineering validation |
| Is a CFD job a Native preview? | no | long-running evidence production is not a CAD mutation preview |
| Does Aero own a scheduler? | no | `AeroJobStore` is transitional/reference only |
| Is NativeBackground deleted? | no during migration | it is a compatibility/public orchestration facade and seed |
| Is detached FEM rewritten? | no | generic mechanics are extracted behind existing behavior |
| Is FEM state genericized? | no | solver state/History/result semantics remain FEM-owned |
| Is persistence part of first extraction? | no | data/crash risk is isolated after in-memory parity |
| Is concurrency expanded during extraction? | no | preserve one active job/document first |
| Are public Native schemas renamed? | no | additive evolution only after compatibility is proven |
| Are old Python modules deleted immediately? | no | thin facades/re-exports prevent downstream breakage |
| How is parity proven? | characterization/golden traces + result/receipt parity | unit tests of new code alone are insufficient |
| How is shadow testing done? | observation only, one actual solver run | prevents duplicate compute/result mutation |
| How are stale successful results treated? | immutable historical/quarantined evidence | stale is not failed |
| How is Save-As handled? | path is informational; dependency identity governs | prevents path/label from becoming authority |
| How are restarts handled? | reconnect/orphan/await-source states based on evidence | never infer success from leftover files |
| What happens when compute succeeds but source is closed? | `AWAITING_SOURCE` | solver success is preserved without mutating another document |
| Can Aero use domain-scoped currentness immediately in FEM extraction? | no | preserve existing FEM ticket/global-revision semantics first; evolve durable publication separately |
| Are component license restrictions runtime concepts? | no | host runtime contains no use-purpose/license policing |
| Are correctness bug fixes hidden in extraction? | no | characterize/fix/re-baseline separately so parity and rollback remain meaningful |

## Non-negotiable dependency direction

```text
Native mutation authority         Evidence/qualification
          ▲                                ▲
          │ publication draft              │ domain result
          │                                │
       Host Analysis Runtime ──────── artifact/provenance
          ▲                 ▲
          │                 │
      FEM adapter        Aero adapter
          ▲                 ▲
      FEM domain        VibeCADAero

Host Analysis Runtime ──> Compute Provider (Local/Kaggle/remote)
```

No arrow may be reversed merely for convenience.
