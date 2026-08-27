# VibeCAD Native authority-policy census

**Audited execution branch:** `roadmap/native-authority-census`, derived from
the production `build_native_capability_registry()` assembly.

The executable census is owned by `VibeCADNativeAuthorityPolicy.py`. It does
not copy capability definitions or dispatch mutations. It projects one policy
record from every frozen registry definition/variant and fails on duplicate or
unclassified operation identities. The production registry remains the source
owner for schemas, action IDs, surfaces, target types, transaction behavior,
background requirements, implementations, and dispatch.

The reconciled census contains 740 records: 738 registered capability
operations plus `/v1/prompt` and `/v1/run`.

| Primary authority policy | Count |
| --- | ---: |
| Safe immediate mutation under the existing Native transaction owner | 552 |
| Read only | 75 |
| Presentation/view change | 48 |
| Explicit confirmation required for background execution | 31 |
| Export to a human-authorized destination | 18 |
| Propose/apply preview required | 14 |
| External side effect (`/v1/prompt`) | 1 |
| Privileged compatibility execution (`/v1/run`) | 1 |

Every record also carries its reason, mutation owner, transaction behavior,
currentness inputs, effect evidence, rollback boundary, and test owner. The
census includes Model, Sketch, Assembly, Analyze, Manufacture, Drawing, Robot,
Aero, Native/background, inspection, parameters, document, component,
workspace, presentation, mesh, and local-agent families.

This closes operation classification, not all preview-evidence work. Follow-on
domain tranches may add bounded affected-object identities, parameter and
dependency diffs, geometry/placement summaries, interference/toolpath/output
summaries, and resource estimates. Those additions must continue through the
existing mutation implementation and may not silently upgrade an operation's
authority class.

