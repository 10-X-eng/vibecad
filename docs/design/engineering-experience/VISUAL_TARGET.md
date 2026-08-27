# Engineering Experience visual target

## Status and authority

The supplied image is the VibeCAD Engineering Experience visual north star. It
sets quality, information-density, hierarchy, and interaction intent. It is not
a literal screenshot of implemented behavior, a field fixture, or permission to
display invented values. Every visible engineering value, status, finding,
progress state, provenance row, chart, and thumbnail must come from an owning
domain contract and must retain its claim ceiling.

The target is broader than a polished FEA application: one coherent visual
language must project governed state from CAD, Analysis, workflows,
optimization, Manufacture, Assembly, service planning, and Robot while those
domains retain authority.

## Reference decomposition

| Region | Intended meaning | Authoritative source | Prohibited shortcut |
| --- | --- | --- | --- |
| Top application/ribbon shell | Domain navigation and commands | Existing VibeCAD ribbon/workbench ownership | Creating a separate visualization workbench |
| Left model tree | Document objects, connections, mesh, studies, results | Native document and domain owners | Reconstructing a shadow document graph |
| Center viewport | CAD plus composable engineering presentation layers | Native/VTK/FEM/domain presentation owners | Building a second scientific renderer |
| Scientific legend | Field label, unit, numeric range and scale | Exact selected field projection | Unlabeled decorative rainbow |
| Right Engineering dock | Contextual overview/results/findings/activity/provenance/workflow/compare pages | G1-G12 contracts and domain extensions | Flattening all domains into one lossy result model |
| Result cards | Metric plus independent execution, verification, currentness and publication axes | Common result envelope plus domain payload | One ambiguous green/red “status” |
| Activity list | Real durable attempts, node progress and recovery state | G2/G5 | Fake aggregate counters or session state labeled durable |
| Convergence/chart area | Real bounded series and declared axes/units | Domain artifact/result adapter | Cosmetic chart data |
| Workflow panel | Real DAG node lifecycle and eligibility | G5 | UI-owned workflow state |
| Findings panel | Stable finding, severity/verdict, evidence, currentness, remediation, claim ceiling | G1/domain verifier | Treating a high field value as a failure finding |
| Provenance panel | Exact source/result/attempt/artifact/publication identities | G1/G2 provenance and receipts | Labels or paths used as identity |
| Thumbnails | Derived immutable visualization artifacts | Published result plus exact rendering parameters | Loose screenshots presented as current evidence |

## Visual acceptance principles

- Dark neutral surfaces dominate; saturated colors carry bounded signal.
- Scientific magnitude and governance status use distinct visual channels.
- A red field region means high within the displayed scale, not automatically
  unsafe, failed, or blocking.
- Stale historical results may remain viewable but must be visibly stale and
  must never present as current.
- Every number has a unit or an explicit dimensionless designation.
- Every viewport field has a named scale, exact range mode, and range values.
- Every action that can alter accepted state remains routed through its owning
  preview/confirmation/publication authority.
- Compact density must not erase provenance, uncertainty, currentness, or claim
  ceilings.

## Target questions

From any displayed engineering item, the user must be able to answer: what it
is; which exact source revision and computation produced it; whether it is
current; whether and how it was verified; whether it was published; which
evidence supports it; and what claim is permitted.

