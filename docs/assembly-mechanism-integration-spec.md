# Assembly and Mechanism Integration Specification

Status: Draft

Scope: Native Assembly ownership, complete Assembly product surface, shared
mechanism evaluation, and Part Design verification

Related specification:
[Bundled Standard Fasteners](standard-fasteners-integration-spec.md)

Cross-domain roadmap:
[VibeCAD governed engineering roadmap](vibecad-governed-engineering-roadmap.md)

The governed engineering roadmap extends this existing Assembly owner; it does
not replace it. Its graph, interface, validation, inference, sequencing,
service, and Robot-projection milestones must preserve the ownership and
evidence boundaries defined here, and this specification remains authoritative
for detailed Assembly semantics.

## 1. Purpose

VibeCAD must let an AI author a part and determine, with precisely stated
evidence, whether that part can participate in its intended mechanism.
Requiring the AI to leave Part Design and manually recreate context in Assembly
is not a sufficient product experience. Copying a small second assembly system
into Part Design would be worse: it would produce two object models, two
solvers, and results that disagree.

Assembly is therefore the authoritative mechanism domain. It must retain and
expand its useful authoring, hierarchy, solving, motion, simulation, reporting,
and BOM capabilities. Part Design receives a compact verification facade backed
by the same native Assembly engine and the same evaluation service. It does not
receive a substitute solver or a restricted fork of Assembly.

## 2. Product decisions

1. Native FreeCAD Assembly objects and the native Assembly solver remain the
   source of truth for occurrence graphs, connectors, joints, solved placements,
   and kinematic state.
2. The Assembly workbench remains a complete first-class workbench. It is not
   reduced to a hidden service for Part Design.
3. Collision, interference, clearance, contact policy, and motion evidence live
   in a shared mechanical-evaluation service below both VibeScript domains.
4. Assembly exposes the complete persistent mechanism-authoring surface. Part
   Design exposes mechanism verification against a transient scenario or an
   existing Assembly without publishing hidden Assembly objects into the part
   document.
5. Part modeling is not reimplemented in Assembly. Assembly references authored
   parts and standard components.
6. Assembly modeling is not reimplemented in Part Design. A Part Design check
   is normalized to the same scenario consumed by Assembly.
7. A result may say `pass`, `fail`, or `indeterminate`. Lack of a discovered
   collision is not automatically proof of collision-free motion.
8. VibeCAD reports exactly what was evaluated. It must not use the unqualified
   statement “this mechanism works.”

## 3. Goals

- Preserve and expose all useful native Assembly behavior.
- Give Assembly VibeScript a complete, coherent mechanism API.
- Let Part Design VibeScript validate a candidate feature in its real mechanical
  context before publication.
- Support rigid and flexible nested subassemblies without flattening their
  identity.
- Produce structured degrees-of-freedom, conflict, redundancy, residual, motion,
  collision, clearance, and fit evidence.
- Use standard fasteners as catalog components rather than modeled
  approximations.
- Keep accepted geometry and assembly state intact when a candidate solve or
  verification fails.
- Save enough deterministic engineering evidence to explain a result after
  reopening a document.
- Improve native FreeCAD interfaces where current document- or GUI-oriented
  behavior is not safe for headless evaluation.

## 4. Non-goals

- Putting the Part Design feature-authoring API into the Assembly VibeScript
  pack.
- Putting all Assembly authoring functions into the Part Design VibeScript pack.
- Replacing the FreeCAD Assembly solver with a Python solver.
- Treating frame sampling alone as proof of continuous collision-free motion.
- Finite-element stress, fatigue, thermal, fluid, or manufacturing analysis.
- Claiming force, torque, friction, wear, lubrication, or control-system
  adequacy from kinematics alone.
- Supporting deformable-body collision in the initial implementation.
- Restoring or replaying AI tool traces. Saved evidence is an engineering
  report, not an agent execution history.

## 5. Current state

The existing Assembly VibeScript pack already exposes:

- `component`;
- `instances`;
- `fastener`;
- `connector`;
- `joint`;
- `assembly`;
- `solve`;
- `mechanism_check`;
- `motion`;
- `simulation`;
- `exploded_view`;
- `bill_of_materials`.

It supports native component links, rigid and flexible subassemblies, semantic
interfaces, all native joint kinds, solver diagnostics, simulated placement
frames, exploded views, and BOM publication.

The supported native joint set is:

- fixed;
- revolute;
- cylindrical;
- slider;
- ball;
- distance;
- parallel;
- perpendicular;
- angle;
- rack-pinion;
- screw;
- gears;
- belt.

`mechanism_check` performs exact static collision, clearance, and declared
contact evaluation at the native solved state. It does not certify motion.
The current simulation path does not perform continuous-motion certification.
Part Design checks currently evaluate measurements only. VibeScript
intentionally activates exactly one workbench pack, so Part Design source
cannot call the Assembly pack directly.

These are sound boundaries to build on. The missing capability is a shared
evaluation layer and a deliberately small Part Design adapter, not a union of
the two public packs.

## 6. Required architecture

```text
Assembly GUI and VibeScript       Part Design GUI and VibeScript
            |                                  |
    complete authoring API              verification facade
            |                                  |
            +------- normalized scenario ------+
                               |
                 shared mechanism evaluation
        solve / motion / collision / clearance / evidence
                               |
               native FreeCAD Assembly and Part
          occurrence graph / joints / solver / exact BREP
```

The dependency direction is downward. Shared evaluation code must not import a
Part Design worker, and Assembly code must not depend on Part Design VibeScript.

### 6.1 Capability ownership

| Capability | Native FreeCAD | Shared evaluation | Assembly | Part Design |
|---|---|---|---|---|
| Persistent occurrence graph | Owns | Reads | Authors and publishes | References only |
| Connectors/JCS | Owns native frame | Resolves | Authors and publishes | Supplies semantic candidate interfaces |
| Joints and limits | Owns | Normalizes and evaluates | Authors all supported kinds | Declares only within a verification scenario |
| Constraint solve | Owns | Calls and reports | Full use | Verification use |
| Motion definitions | Stores/evaluates native form | Normalizes and bounds | Authors, simulates, publishes | Supplies a declared check range |
| Collision/clearance | Exact Part/OCCT primitives | Owns orchestration and evidence | Full analysis and reports | Required checks |
| Standard fasteners/fits | Stores native objects | Resolves catalog semantics | Inserts, constrains, counts | Creates parts/holes and verifies fit |
| Exploded views | Owns placements/objects | No duplicate | Authors and publishes | No |
| BOM | Owns document identities | Aggregates canonical data | Authors and publishes | Supplies part identity |
| Part feature history | Native Part Design owns | Reads final candidate shape | References published parts | Authors and publishes |

### 6.2 Normalized mechanism scenario

Both domain adapters must produce one versioned internal scenario. The scenario
contains:

- stable component IDs;
- source-document references or transient candidate features;
- initial placements and grounded state;
- rigid/flexible hierarchy and stable occurrence paths;
- named semantic interfaces;
- joints, parameters, limits, and suppression state;
- one or more declared motion drivers and ranges;
- pair-specific contact and clearance policy;
- requested assertions and numerical tolerances.

The schema is an internal shared contract first. If it is later persisted or
made public, it must receive an explicit version and compatibility tests before
release.

## 7. Complete Assembly product surface

Assembly must not be confined to the minimum needed by a Part Design check. Its
supported product surface is the following.

### 7.1 Components and occurrences

- Link authored VibeCAD or compatible FreeCAD parts by stable document and
  object identity.
- Insert bundled standard components, including fasteners.
- Place, ground, suppress, duplicate, and pattern occurrences.
- Preserve occurrence-specific placement while sharing part definition.
- Support nested rigid and flexible subassemblies.
- Address an occurrence by a stable path independent of labels and tree order.
- Replace an occurrence's source while retaining compatible semantic interfaces.

### 7.2 Connectors and semantic interfaces

- Component origin and datum frames.
- Published part interfaces such as axis, plane, point, bearing face, thread
  axis, shaft seat, and mounting pattern.
- Exact topology references only for immutable snapshots.
- Offset connector frames.
- Stable connector identity after ordinary parametric recompute.
- Clear invalidation when a required semantic interface is removed.

An authored, regenerating component must not depend on `Face7` or `Edge12` when
a named engineering interface can be published.

### 7.3 Joints and constraints

- All 13 currently supported native joint kinds.
- Joint-specific dimensional and angular parameters.
- Limits where the native joint supports them.
- Suppression and reactivation.
- Joint offsets and connector orientation.
- Coupled mechanisms such as gears, racks, screws, and belts.
- Closed-loop mechanisms.
- Structured malformed, conflicting, and redundant-constraint diagnostics.

No existing joint kind is removed or hidden as part of this effort.

### 7.4 Solve and degrees of freedom

Every solve returns a structured report containing:

- solved, failed, or indeterminate status;
- remaining degrees of freedom;
- malformed joints;
- conflicting constraints;
- redundant constraints;
- numerical residuals and tolerances;
- affected component and joint IDs;
- solved placements;
- solver version and elapsed time;
- cancellation or resource-limit state.

An Assembly remains useful when intentionally underconstrained. The API must
distinguish “valid with two remaining degrees of freedom” from “failed to
solve.”

### 7.5 Motion and simulation

- Drive supported translational and rotational joint parameters.
- Declare exact start, end, and optional intermediate positions.
- Support multiple coordinated drivers where the solver can evaluate them.
- Solve every requested state with diagnostics.
- Retain deterministic frame placements for playback.
- Compute component motion envelopes.
- Support nested flexible subassembly motion.
- Report discontinuities, unsolved states, and limit violations.

Simulation playback is a view of evaluated placements. It is not by itself a
collision or dynamics result.

### 7.6 Interference, clearance, and contact

- Static interference at an exact solved state.
- Minimum clearance at an exact solved state.
- Declared-range collision checks.
- Declared-range minimum-clearance checks.
- Allowed contact, ignored pair, required contact, and prohibited contact
  policies.
- First failing state, affected occurrence pair, overlap or clearance, and
  witness geometry.
- Semantic treatment of fastener/hole and other declared fits.
- Explicit `indeterminate` when continuous safety cannot be certified.

### 7.7 Standard components and fit

Assembly uses the service defined in
[Bundled Standard Fasteners](standard-fasteners-integration-spec.md). Standard
components expose native connectors, canonical BOM identity, and fit semantics.
They remain ordinary occurrences for solve, visibility, pattern, explode, and
BOM behavior.

### 7.8 Engineering properties and reports

- Part number, revision, description, and quantity identity.
- Material, density source, mass, center of mass, and inertia when enough data
  exists.
- Kinematic verification reports.
- Collision and clearance reports.
- Exploded views.
- BOM tables.
- Native Assembly interchange/export only where round-trip behavior is tested.

Unknown mass or inertia stays unknown. VibeCAD must not invent a default mass to
make a report look complete.

### 7.9 Later dynamics extension

Kinematics and geometry cannot establish actuator torque, impact behavior,
friction, spring response, or structural adequacy. A later dynamics phase may
add gravity, inertial properties, springs, contacts, friction, and prescribed
loads, but only through a named, validated physics engine. It must consume the
same occurrence and joint graph rather than create a third assembly model.

Until that extension exists, product language must say `kinematically verified`
or name the exact passed requirements.

## 8. Assembly VibeScript contract

All existing functions and output types remain supported. The additive static
evaluation operation is:

```python
verification = api.mechanism_check(
    mechanism,
    requirements=[
        {
            "type": "collision_free",
            "first": carriage,
            "second": stop,
            "tolerance_mm": 0.01
        },
        {
            "type": "minimum_clearance",
            "first": carriage,
            "second": frame,
            "minimum_mm": 0.25,
            "tolerance_mm": 0.01
        }
    ],
    contacts=[
        {
            "first": cam,
            "second": follower,
            "policy": "required",
            "first_interface": "working_surface",
            "second_interface": "contact_face",
            "tolerance_mm": 0.01
        },
        {
            "first": cover,
            "second": guard,
            "policy": "ignored",
            "reason": "Nonphysical presentation geometry"
        }
    ],
    label="Solved-state verification"
)
```

`assembly` is the exact `api.assembly(...)` value. `first` and `second` are the
exact component values already included in that assembly; labels and strings
are not component references. Every unordered pair may appear only once across
`requirements` and `contacts`.

The supported requirement types are:

- `collision_free`: requires `first`, `second`, and a positive
  `tolerance_mm`;
- `minimum_clearance`: additionally requires a nonnegative `minimum_mm`.

The supported contact policies are:

- `prohibited`: touching within `tolerance_mm` or overlap fails;
- `clearance`: additionally requires `minimum_clearance_mm`;
- `allowed`: names `first_interface` and `second_interface`; separation
  passes, but any contact must be confined to those semantic interfaces;
- `required`: names both semantic interfaces and requires confined contact
  within `tolerance_mm`;
- `ignored`: requires a nonempty `reason` and performs no geometry evaluation.

No pair, fit, exemption, interface, or tolerance is inferred. At least one
declaration must be evaluated, and each list is bounded to 64 entries. The
initial implementation rejects checks that address a flexible top-level
subassembly because component-level static geometry cannot honestly certify its
internally solved occurrence state.

The returned `mechanism_verification` output is evaluated after native solve.
The host independently reloads the authenticated source BREPs and recomputes
the result before publication. Its persisted
`vibecad-mechanism-verification-report-v1` contains scenario, solve-report, and
check hashes; an overall `pass`, `fail`, or `indeterminate` verdict; individual
declaration results; exact OCCT distance, overlap, interface, and witness
evidence; the first failure; and an explicit scope:
`static_solved_state`, `explicit_only`, `declared_per_pair`, and
`motion_certified=False`.

Publication creates a stable report object under the native Assembly's
`Verification` group. Invalid or rejected candidate updates preserve the
previously accepted report, and save/reopen restores the portable report
without replaying the VibeScript. A requirement verdict of `fail` or
`indeterminate` is itself a valid Assembly report and remains inspectable; it
does not become an unsupported claim that the mechanism passed.

A later motion-certification extension must remain additive to this operation
and normalize through the same mechanism-evaluation boundary. It must not
silently broaden a static report or create independent collision and clearance
engines.

API descriptions must use direct engineering language. For example:

> Evaluate explicit static collision, clearance, and contact requirements at
> the native solved Assembly state.

Descriptions must not contain a recommended modeling workflow or tell the model
to call unrelated inspection tools.

## 9. Part Design verification facade

A later Part Design phase receives an additive `api.mechanism_check(...)` value
accepted by the existing `checks=` argument of `api.body(...)`. Existing
measurement checks remain unchanged. This facade is not part of the shipped
Assembly static contract described in Section 8.

### 9.1 Existing-Assembly mode

The preferred production use replaces one occurrence in an authenticated
Assembly snapshot with the candidate feature:

```python
check = api.mechanism_check(
    assembly=inputs["assembly_reference"],
    replace={
        "occurrence_path": "Latch/Lever",
        "feature": lever
    },
    motions=[
        {
            "joint": "Latch/Pivot",
            "from": 0,
            "to": 65,
            "unit": "degree"
        }
    ],
    requirements=[
        {"type": "degrees_of_freedom", "equals": 1},
        {
            "type": "collision_free",
            "first": "Latch/Lever",
            "second": "Latch/Stop",
            "tolerance_mm": 0.01
        },
        {
            "type": "minimum_clearance",
            "first": "Latch/Lever",
            "second": "Latch/Housing",
            "minimum_mm": 0.2,
            "tolerance_mm": 0.01
        }
    ],
    label="Lever mechanism check"
)

result = {
    "Lever": api.body(
        lever,
        interfaces=lever_interfaces,
        checks=[check],
        label="Latch lever"
    )
}
```

The reference is the same authenticated `document_uid`/`object_name` form used
by Assembly, plus a stable occurrence path. Labels and filesystem paths are not
references.

### 9.2 Inline-scenario mode

For a new design without a saved Assembly, Part Design may supply a transient
scenario:

```python
check = api.mechanism_check(
    scenario={
        "components": [
            {
                "id": "housing",
                "source": inputs["housing_reference"],
                "grounded": True
            },
            {
                "id": "candidate",
                "feature": lever,
                "interfaces": lever_interfaces
            }
        ],
        "joints": [
            {
                "id": "pivot",
                "kind": "revolute",
                "first": {
                    "component": "housing",
                    "interface": "pivot_axis"
                },
                "second": {
                    "component": "candidate",
                    "interface": "pivot_axis"
                },
                "angle_limits_degrees": [0, 65]
            }
        ],
        "grounded_component": "housing"
    },
    motions=[
        {
            "joint": "pivot",
            "from": 0,
            "to": 65,
            "unit": "degree"
        }
    ],
    requirements=[
        {"type": "degrees_of_freedom", "equals": 1},
        {
            "type": "collision_free",
            "first": "candidate",
            "second": "housing",
            "tolerance_mm": 0.01
        }
    ]
)
```

This schema is not a second solver. The adapter validates it, converts it to the
normalized scenario, constructs a transient native Assembly in the isolated
worker, and calls the same evaluation service as the Assembly domain.

Exactly one of `assembly` or `scenario` is supplied. Inline mode supports the
complete native joint set rather than a hand-picked “simple mechanism” subset.
Its persistent equivalent is still an Assembly document.

### 9.3 Graph and publication rules

- A check references the final candidate feature, not the `api.body(...)`
  publication wrapper. This prevents a dependency cycle.
- Candidate interface definitions may be assigned to a variable and passed to
  both the check and `api.body(...)`.
- A successful check publishes the Part Design Body and a compact report.
- A failed or indeterminate required check rejects the candidate and preserves
  the previously accepted live document.
- Transient Assembly components, joints, and frames never enter the Part Design
  tree.
- The existing Part Design result output types do not gain Assembly outputs.
- The Part Design VibeScript pack does not import or expose Assembly authoring
  functions.

### 9.4 GUI behavior

The Part Design GUI may offer `Validate in Mechanism`, which selects an existing
Assembly/occurrence or creates an inline scenario. Results appear under the
document's type-grouped `Verification` section, not under individual features
and not as hidden Assembly groups.

Reopening a document displays the saved model immediately. Cached verification
status appears with it. A stale report may be recomputed explicitly or in a
bounded background task, but report refresh must not delay shape restoration.

## 10. References and identity

Every persistent reference uses:

- source document UID;
- source object stable name or UUID;
- stable occurrence path for nested Assembly context;
- semantic interface name;
- source revision/content signature where available.

Tree row, label, object index, `FaceN`, and absolute filename are not sufficient
persistent identity.

Exact topology references are allowed only for an authenticated immutable input
snapshot. Parametric sources must publish semantic interfaces. If an interface
cannot be resolved after recompute, the joint or check becomes explicitly
invalid rather than attaching to a nearby face.

## 11. Solve and motion evaluation

### 11.1 Isolated evaluation

Candidate evaluation runs outside the live document:

1. Authenticate and snapshot every referenced input.
2. Materialize the candidate and source components in an isolated worker.
3. Construct a transient native Assembly or clone the referenced Assembly graph.
4. Substitute the candidate occurrence when requested.
5. Solve the initial state and every required driven state.
6. Evaluate requirements and produce a versioned report.
7. Publish only after every required verdict is `pass`.

Cancellation, timeout, worker crash, or incomplete evidence produces
`indeterminate`; it never publishes a positive claim.

### 11.2 Determinism

A report records:

- normalized scenario hash;
- source object and shape signatures;
- native solver and geometry-kernel versions;
- catalog versions for standard components;
- units and tolerances;
- driver ranges;
- exact requirement set;
- result and evidence schema version.

The same inputs and engine versions must produce the same verdict and equivalent
placements within the recorded numerical tolerance.

### 11.3 Multiple solutions and discontinuities

If a constrained state has multiple valid branches, the evaluator must either:

- continue from the explicitly selected initial branch and prove continuity; or
- report ambiguity and require a branch selection.

It must not jump between solver branches to make a motion appear continuous.
Unsolved points, discontinuities, singular configurations, and joint-limit
violations are named in the result.

## 12. Collision and clearance evaluation

### 12.1 Static states

For each solved state:

1. Use transformed bounding volumes for broad-phase pair rejection.
2. Use exact BREP distance for remaining clearance pairs.
3. Use exact BREP common/intersection for suspected overlap.
4. Classify separated, touching within tolerance, or overlapping.
5. Apply the declared contact policy.

Meshes may accelerate visualization or broad phase. They are not the final
authority for an exact pass.

Evidence for a failure contains occurrence IDs, driver state, overlap volume or
minimum distance, tolerance, and witness points/subshapes where the kernel can
provide them.

### 12.2 Continuous declared motion

Checking a fixed number of frames can find collisions but cannot prove that
none occurred between frames. Continuous certification therefore uses adaptive,
conservative interval evaluation:

1. Solve and exactly evaluate the interval endpoints and required interior
   states.
2. Compute a conservative upper bound on each occurrence's possible movement
   over the driver interval.
3. Certify an interval clear only when exact separation is greater than the
   maximum relative movement bound plus the declared tolerance.
4. Otherwise subdivide the interval and repeat.
5. Report `fail` when exact overlap is found.
6. Report `indeterminate` when the evaluator reaches its resolution, time, or
   solver limit without a proof.

Rigid translational and rotational bounds may be derived from joint ranges and
component bounding radii. Compound and closed-loop motion requires a
solver-backed conservative bound. If the native solver cannot supply a valid
bound for a configuration, VibeCAD must not label sampled frames as continuous
certification.

An API request may explicitly ask for `sampled` analysis for fast feedback. Its
report must say `sampled`, list the states evaluated, and be ineligible to
satisfy a requirement that asks for continuous collision-free motion.

### 12.3 Contact policy

Static evaluation is explicit-only: an undeclared pair is not evaluated and no
default tolerance exists. Callers can declare:

- `prohibited`: touching or overlap fails;
- `clearance`: separation below a named threshold fails;
- `allowed`: contact is permitted only on named interfaces and within a
  declared motion range;
- `required`: named interfaces must contact within tolerance in a declared
  state or range;
- `ignored`: the pair is excluded with an explicit reason.

`allowed` is not a blanket collision exemption. Overlap away from the declared
interfaces still fails.

Threaded engagement, clearance holes, bearings, gears, and other standard fits
can provide semantic contact policy. The exact remaining geometry is still
checked for unintended interference.

## 13. Evidence, persistence, and staleness

A saved verification consists of:

- a small document-visible summary;
- versioned requirement verdicts and diagnostics;
- input and engine signatures;
- optional included JSON/binary artifacts for placements or witness geometry.

Large artifacts should use FreeCAD's included-file property mechanism so the
FCStd remains portable. Reports must not refer to temporary files.

The report contains no hidden model prompt, chat transcript, AI tool trace, or
restorable execution trace. Reopening a document restores native geometry and
the last engineering result; it never replays tools.

A report becomes stale when a referenced shape, interface, occurrence graph,
joint, driver, requirement, tolerance, solver, or relevant catalog version
changes. Stale is different from failed. The saved result remains inspectable
but cannot be presented as current.

## 14. Native FreeCAD changes

VibeCAD should use current native behavior through an adapter first, then move
generally useful facilities into FreeCAD's native Assembly and Part layers.
The goal is to improve the authoritative engine, not permanently reproduce it
inside VibeCAD Python.

### 14.1 Required integration boundary

The first implementation requires a headless adapter capable of:

- constructing and cloning native Assembly graphs;
- creating rigid and flexible occurrences;
- resolving native JCS/connectors;
- creating all native joint kinds;
- setting limits, suppression, offsets, and drivers;
- solving without GUI commands;
- reading placements and current diagnostics;
- evaluating simulation states deterministically;
- cancelling or terminating bounded worker work.

Where current APIs expose diagnostics through mutable `last...` state, the
adapter may read that state for compatibility. It must normalize it immediately
and must not expose those implementation details as VibeScript contract.

### 14.2 Additive native hardening

The following should be implemented in native FreeCAD interfaces as small,
additive changes:

1. **Structured solve result:** add a C++/Python result object containing status,
   DoF, conflicts, redundancies, malformed joints, residuals, placements, and
   timing. Keep existing `solve()` behavior working.
2. **Non-mutating evaluation session:** evaluate a graph or snapshot and return
   placements without committing intermediate states to the live document.
3. **Stable occurrence identity:** expose UUID-backed paths for nested rigid and
   flexible links independent of labels and row order.
4. **Semantic connector identity:** make named connector/JCS resolution a
   machine-readable native contract.
5. **Typed motion input/output:** accept driver definitions and return placement
   frames and solve diagnostics without creating GUI-oriented FeaturePython
   properties as the transport.
6. **Cancellation and deadlines:** propagate cancellation through solve and
   simulation work.
7. **Batch shape evaluation:** add or generalize a native Part service for
   transformed broad phase, exact distance, exact common, and witness results.
8. **Mass and inertia:** derive values from declared material and shape with an
   explicit unknown state.
9. **Versioned persistence:** provide stable schemas for motion and verification
   artifacts without changing existing document properties in place.

These changes belong in `Assembly` or a shared `Part`/application service when
they are generally useful. They must not be buried in
`vibescript_partdesign_worker.py`.

### 14.3 Compatibility during upstreaming

- Existing native methods remain present.
- New result objects and evaluation sessions are opt-in until characterized.
- VibeCAD keeps a thin adapter for supported FreeCAD revisions.
- Native changes have direct C++ and Python tests independent of VibeScript.
- No document-property migration occurs without a versioned dual-read path.

## 15. Document tree and user experience

### 15.1 Assembly documents

The Assembly tree should organize by engineering type:

- Components;
- Joints;
- Motions;
- Verification;
- Exploded Views;
- Reports/BOM.

Nested component structure appears under Components. Joints do not own or hide
the components they reference. Motions and verification reports do not duplicate
component objects.

Standard fasteners appear as component occurrences and may be grouped or
patterned without losing individual occurrence paths.

### 15.2 Part Design documents

No transient mechanism graph is shown. The published part remains organized by
Sketches, Features, Bodies, Materials, and Verification according to VibeCAD's
type-grouped tree model. A verification item links to its scenario/report but
does not become the parent of sketches or features.

### 15.3 Failure presentation

A failed report starts with one direct sentence, for example:

> Collision-free travel failed at 37.2 degrees: Lever and Housing overlap by
> 1.84 mm3.

Details then show the exact requirement, pair, state, tolerance, and witness.
Solver diagnostics are not collapsed into a generic “build failed.”

## 16. Transactions, rollback, and concurrency

- Part Design verification occurs in the existing isolated candidate worker.
- Failed, indeterminate, cancelled, or timed-out checks do not mutate the live
  document.
- Assembly evaluation uses a snapshot or explicit transaction and restores its
  initial placements after preview.
- Publishing a new accepted Assembly graph and its reports is one document
  transaction.
- Inputs are reauthenticated immediately before publish to prevent accepting a
  result for stale source geometry.
- Parallel evaluations use immutable snapshots and separate workers.
- Cache entries are content-addressed and never reused across different shape,
  scenario, solver, or tolerance signatures.

## 17. Error and verdict contract

The top-level verdict is:

- `pass`: every required assertion was proven for the declared scope;
- `fail`: at least one required assertion has definite contrary evidence;
- `indeterminate`: the requested claim could not be proved or disproved within
  solver, geometry, time, or resolution limits.

Each requirement reports its own verdict and evidence. A required
`indeterminate` result rejects a Part Design candidate just like a failed check,
but the UI must preserve the distinction.

Errors identify stable components, joints, drivers, and interfaces. They do not
expose only an internal object index or Python exception. Worker logs may retain
the underlying exception for diagnostics.

## 18. Compatibility requirements

- All existing Assembly VibeScript functions remain registered and callable.
- All 13 existing joint kinds retain their current names and semantics.
- Existing Assembly output types and saved results remain readable.
- Existing Part Design `api.measure(...)` checks remain unchanged.
- Existing Part Design output types and result schema remain unchanged.
- Exactly one VibeScript workbench pack remains active; packs are not unioned.
- Existing Assembly and Part Design documents open without automatic structural
  migration.
- New scenario and report schemas are versioned from their first persisted
  release.
- Old reports remain viewable even if they must be marked stale.

Any incompatible change to a tool schema, document property, output type, or
default requires the explicit owner approval described in `AGENTS.md`.

## 19. Delivery sequence

Each phase is independently reviewable, buildable, and shippable. The work
should be tackled one phase at a time.

### Phase 1: characterize and isolate the existing Assembly engine

- Add characterization tests for every current Assembly API operation.
- Cover all joint kinds, rigid/flexible hierarchy, solve diagnostics,
  simulation, explode, BOM, save/reopen, and rollback.
- Extract a shared headless mechanism-engine boundary from the current Assembly
  worker without changing public behavior.
- Define the internal normalized scenario and solve-report versions.

Exit condition: current Assembly behavior passes through the new boundary with
no public API or document change.

### Phase 2: exact static interference and clearance

- Extend the native geometry worker or shared Part service with bounded batch
  transformed-shape evaluation.
- Add broad phase, exact distance, exact common, tolerance, and witness results.
- Add contact policies and report persistence.
- Expose evaluation in Assembly first.

Exit condition: Assembly can prove or disprove declared static collision and
clearance requirements with exact evidence.

Implementation status: complete for rigid component-level solved-state
evaluation. The public `mechanism_check` contract, all five contact policies,
host-side exact recomputation, stable native publication, rollback, and
save/reopen persistence are covered by automated tests. Continuous motion and
flexible-subassembly certification remain Phase 3 work and are not implied by a
static pass.

### Phase 3: declared-motion certification

- Add typed driver evaluation and structured state diagnostics.
- Add conservative motion bounds and adaptive interval subdivision.
- Distinguish sampled, continuous-pass, continuous-fail, and indeterminate.
- Add nested flexible-subassembly and closed-loop coverage.

Exit condition: Assembly never presents sampled no-collision results as a
continuous proof.

### Phase 4: complete Assembly engineering surface

- Add verification UI and VibeScript publication.
- Integrate standard fasteners, named fits, material mass properties, motion
  envelopes, exploded views, and BOM reporting.
- Organize the Assembly tree by components, joints, motions, verification, and
  reports.

This phase depends on the relevant deliverables in
[Bundled Standard Fasteners](standard-fasteners-integration-spec.md), but the
collision and motion foundation does not.

### Phase 5: Part Design verification facade

- Extend Part Design check dispatch without changing measurement checks.
- Add existing-Assembly replacement mode.
- Add complete inline-scenario mode.
- Store compact portable reports under Verification.
- Preserve accepted-document rollback and immediate reopen display.

Exit condition: a candidate Part Design feature can be rejected or accepted from
the same engine and requirements used in a persistent Assembly.

### Phase 6: native FreeCAD hardening

- Add the structured native solve result and non-mutating evaluation session.
- Add stable occurrence and connector identities.
- Add typed simulation and cancellation interfaces.
- Move generally useful batch geometry behavior into the appropriate native
  module.
- Remove no compatibility adapter until a separately approved deprecation
  period is complete.

### Phase 7: optional dynamics

- Select and validate a physics engine.
- Define loads, actuators, springs, contact, and friction contracts.
- Reuse native occurrence, joint, material, and motion identity.
- Keep kinematic and dynamic claims visibly distinct.

This is a later specification and is not required to ship the kinematic
verification system.

## 20. Verification and release gates

### 20.1 Native and adapter tests

- Every native joint kind, parameter, and supported limit.
- Grounded, underconstrained, overconstrained, conflicting, redundant, and
  malformed graphs.
- Rigid and flexible nested subassemblies.
- Stable occurrence paths across save/reopen, rename, reorder, and recompute.
- Connector invalidation rather than topology misattachment.
- Cancellation, deadlines, worker failure, and transaction restoration.

### 20.2 Reference mechanisms

The suite includes deterministic engineering fixtures for:

- revolute hinge;
- slider and cylindrical guide;
- four-bar linkage;
- slider-crank;
- gear pair;
- rack and pinion;
- screw-nut motion;
- belt drive;
- ball-joint chain;
- closed-loop singularity;
- rigid and flexible nested subassemblies;
- latch with required cam contact;
- fastener, clearance-hole, and threaded engagement;
- deliberate collision;
- near-contact below tolerance;
- a motion that is clear at sampled frames but collides between them;
- an interval that must return `indeterminate`.

Each fixture records expected DoF, driven endpoints, and precise requirements.
Golden placements use declared tolerances rather than screen images.

### 20.3 Persistence tests

- Save, close, reopen, and display geometry before reevaluation.
- Cached current, stale, failed, and indeterminate reports.
- Included report artifacts with no temporary or absolute paths.
- Old Assembly and Part Design fixtures.
- No prompt, conversation, or AI tool trace restored from the document.

### 20.4 VibeScript tests

- Existing Assembly source remains valid without edits.
- Every new requirement and contact policy.
- Existing-Assembly candidate replacement.
- Complete inline scenario with every joint kind.
- Part Design publication only after required checks pass.
- Accepted geometry preservation after fail, indeterminate, cancellation, crash,
  and source-authentication change.
- Concise, accurate pack and function descriptions.

### 20.5 Accuracy and performance

- Exact static cases are compared with independently constructed OCCT results.
- Continuous-motion fixtures exercise interval proof, collision, and
  indeterminate paths.
- Numerical tolerances and units are explicit in every result.
- Large occurrence graphs have bounded broad-phase behavior.
- Time and memory limits are tested rather than assumed.
- Headless and GUI results use the same engine and normalized scenario.

The release is blocked by a false `pass`, live-document mutation from a failed
candidate, missing native joint coverage, stale-reference misattachment, or a
sampled result presented as continuous proof.

## 21. Approval gates and unresolved decisions

The owner approved the initial additive Assembly static contract:

1. `api.mechanism_check(assembly, *, requirements=(), contacts=(), label="")`
   and the exact schemas documented in Section 8.
2. `vibecad-mechanism-static-check-v1` and
   `vibecad-mechanism-verification-report-v1`.
3. No inferred or default static tolerance; every evaluated pair declares its
   tolerance.
4. The initial `prohibited`, `clearance`, `allowed`, `required`, and `ignored`
   contact policies.
5. Explicit-pair, rigid component-level, solved-state evaluation with
   `motion_certified=False`.

The owner must separately approve before public implementation:

1. Continuous-analysis tolerances, resource limits, and any additions to the
   `mechanism_check` signature.
2. Native FreeCAD changes to upstream versus maintain in VibeCAD adapters.
3. Whether verification automatically refreshes in the background or only on
   explicit request.
4. The Part Design facade's final public schema and publication behavior.
5. Any future dynamics engine and the claims it is allowed to publish.

These are approval points for public contracts and behavior. They do not justify
duplicating the Assembly engine while a decision is pending.

## 22. Definition of done

This effort is complete when:

- Assembly retains all current authoring capabilities and adds explicit
  collision, clearance, contact, fit, and motion evidence;
- every supported native joint and rigid/flexible hierarchy path is exercised by
  automated tests;
- Part Design can verify a candidate in an existing or transient complete
  mechanism without publishing hidden Assembly objects;
- both domains normalize to one scenario and call one solver/evaluation stack;
- continuous `pass` is issued only with conservative interval evidence;
- failed or indeterminate candidates leave accepted live geometry unchanged;
- standard fasteners participate as parametric occurrences with named
  interfaces and BOM identity;
- save/reopen immediately restores native geometry and portable engineering
  reports without replaying AI tools;
- solver, collision, and report results identify exact scope and never make an
  unsupported claim that a mechanism simply “works”;
- no existing Part Design or Assembly public API has been removed or changed
  incompatibly.
