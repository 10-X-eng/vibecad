# VibeCAD durable workflow DAG

The current roadmap execution stack implements the first domain-neutral G5
workflow layer in `VibeCADAnalysisWorkflow.py` and
`tool_impl/analysis_workflow.py`.

Definitions and runs are separate. A definition contains at most 128 nodes and
512 edges. Nodes declare stable IDs, domain adapters, inputs, outputs,
upstream state requirements, deterministic all-clause conditions, failure and
cancellation policy, retry limit, retention and publication policy, resource
class, concurrency group, and fan-out bound. Validation rejects cycles,
missing nodes, duplicate identities, undeclared edge ports, unbounded fan-out,
unknown policies, live condition objects, and incompatible graph structure.

Run records contain the definition digest, child Analysis IDs, node-attempt
history, bounded outcomes, publication receipt references, lifecycle events,
and terminal state. They never contain provider objects, processes, callbacks,
live documents, or child artifact bytes. Writes use an inter-process owner,
staged fsync, and atomic replacement with fault points before staging, after
staging, before replacement, and after replacement.

The scheduler computes deterministic topological and ready-node order. It
refuses downstream launch after cancellation and requires declared upstream
execution, currentness, publication, and output state. Restart marks local
running nodes interrupted; retry uses a new Analysis ID and attempt within the
declared limit. Workflow cancellation marks pending nodes cancelled and
running nodes cancellation-requested; late completion cannot reopen them.
Publish-once nodes require and retain one publication receipt identity.

The bounded summary includes only workflow/run identity and child state,
Analysis ID, and receipt reference. It does not copy child artifacts.

The first benchmark is geometry -> mesh -> solve -> postprocess -> verify.
Tests inject failure at every node, restart during execution, faults at every
pre-replacement persistence boundary, rejected stale/failed/unpublished
upstream state, retry exhaustion, cancellation/late completion, deterministic
condition skipping, competing writers, and publication receipt enforcement.

Remaining G5 work is production wiring to real G2 Analysis submissions and
domain adapters, explicit provider reconnect handling for child jobs, broader
failure-policy execution, installed-tree and real application-data acceptance,
and a real local five-stage FEM benchmark rather than contract-level child IDs.

