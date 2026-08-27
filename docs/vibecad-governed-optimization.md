# VibeCAD governed optimization

This tranche adds a bounded optimization control layer that composes existing
CAD mutation owners and durable Analysis workflows. It is not an optimizer
provider, does not hold a live document object, and cannot mutate or publish a
design by itself.

## Authority boundary

An optimization definition binds the exact source document UID, revision and
digest; the exact workflow-definition digest; typed variables and their owning
mutation domains; objectives and constraints; finite candidate, workflow, time,
cost and concurrency budgets; explicit exceptional-result treatment; and a
human-authorized publish-once policy.

`enumerate-v1` is deliberately small and independently checkable. It normalizes
numeric values as exact decimals, rejects duplicate normalized values, computes
the Cartesian search space before execution, refuses spaces above the declared
budget, and derives each immutable candidate identity from the definition and
values. Candidate payloads are mutation proposals grouped by their existing
domain owner. They are never accepted document geometry.

## Durable evaluation and ranking

`OptimizationRunStore` atomically persists candidates and references to child
workflow runs. It uses an inter-process writer lock, staged fsync and atomic
replacement. On restart, running candidates become interrupted and
indeterminate; no result is silently treated as complete. Workflow starts are
bounded by the declared run budget.

Ranking is deterministic: current successful candidates are ordered feasible
before infeasible, then lexicographically by declared objective direction, then
by candidate ID. Failed, cancelled, stale, interrupted, unevaluated, missing-
metric and otherwise indeterminate candidates follow the definition's explicit
`exclude` or `rank_last` treatment. Constraints never disappear into an
objective score.

## Selection and publication

Selection is a separate operation. It requires a current, successful, feasible
candidate; an exact observed source revision and digest; and a human
authorization identity. Publication creates an inert intent bound to that
selection and a receipt. Replaying the same receipt is idempotent; any different
receipt or selection is refused. The owning CAD publication coordinator remains
responsible for applying an authorized proposal on the document thread.

## Evidence and remaining integration

The contract tests independently enumerate a six-candidate design, cover
normalization and duplicate rejection, candidate and workflow budgets,
deterministic ranking, all exceptional-result treatments, injected durable-write
failure, restart recovery, stale-source rejection, human authorization and
publish-once replay. Packaging tests require the public facade in source,
build-tree and installed-tree deployments.

G6 remains partial until a real design mutation owner prepares candidate
branches, G5 submits their real Analysis workflows, resource/time/cost usage is
accounted from runtime evidence, the existing G3 publication coordinator
consumes the selection intent, and the bounded acceptance design passes in an
installed VibeCAD deployment.
