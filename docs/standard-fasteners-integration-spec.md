# Bundled Standard Fasteners Integration Specification

Status: Implemented in source; local release gates passing

Scope: Standard fastener packaging, native objects, GUI access, VibeScript, and
Assembly interoperability

Related specification: [Assembly and Mechanism Integration](assembly-mechanism-integration-spec.md)

## Implementation record

The source implementation was completed on 2026-07-24 with these fixed
decisions:

- bundled dependency: FreeCAD Fasteners Workbench 0.5.64;
- pinned revision: `79a06dc067b57ebc89532be835704eb2af5da96c`;
- source: `https://github.com/shaise/FreeCAD_FastenersWB`;
- license boundary: the separately distributed Fasteners module remains
  GPL-2.0-or-later; VibeCAD's headless adapter remains LGPL;
- default real-thread setting: `model_thread=False`;
- real-thread limits: 64 mm nominal diameter, 250 mm axial length, 512
  turns, and 32 real-thread objects per document;
- validated shared catalog: 222 standards, 3,556 nominal-size rows, and 5,316
  exact boundary resolutions;
- full upstream workbench: all 224 upstream standards remain bundled and
  visible.

The release matrix found 19 incomplete or generator-failing nominal rows among
the 222 supported shared-catalog standards. It also found that every published
row of `ISO8733` and `ISO8735` fails to create one valid solid in the pinned
generator. Those exact rows are excluded from VibeCAD selectors and VibeScript
with direct diagnostics; they are not silently substituted. The unmodified
upstream workbench and source remain present so upstream behavior is neither
hidden nor rewritten.

Local verification generated representative geometry for all 222 supported
standards and compared `model_thread=False` and `True` BREP for every supported
threaded component family. All 12 families produced materially different
topology with real threads. The matrix also resolved every supported nominal
row and boundary length and passed native boolean-toggle/save/reopen/edit,
Part Design publication, Assembly occurrence/BOM, and GUI checkbox/workbench/
toolbar tests. Supported release-package artifacts still require their normal
platform packaging matrix before shipment.

## 1. Purpose

VibeCAD must ship a dependable catalog of standard fasteners. A person or an AI
must be able to request a known bolt, screw, nut, washer, or related standard
component by its engineering designation instead of reconstructing it from
generic solids.

The implementation must expose one authoritative catalog and generator through:

- the bundled Fasteners workbench;
- the Part Design GUI;
- the Assembly GUI;
- Part Design VibeScript;
- Assembly VibeScript;
- bill-of-materials and mechanism-verification services.

Those surfaces must be adapters over the same component definition. They must
not contain independent copies of standards tables or independent geometry
implementations.

## 2. Product decisions

The following decisions are part of this specification:

1. The Fasteners workbench is installed, enabled, and shown in the normal
   workbench selector in every supported VibeCAD package. It is usable without
   Addon Manager or network access.
2. The full bundled workbench remains available. Part Design and Assembly also
   expose the relevant insertion commands so users do not have to change
   workbenches for common operations.
3. Standard fasteners are native, parametric document objects with cached
   geometry. They are not anonymous meshes or one-time imported BREP files.
4. VibeScript uses standard names and engineering dimensions. It does not
   expose internal generator class names or ask the model to draw a standard
   fastener.
5. `model_thread` is one boolean mapped directly to the native Fasteners
   `Thread` property. It defaults to `False`; `True` generates real helical
   geometry and is materially more expensive.
6. Standard identity, dimensions, material, appearance, and BOM identity are
   kept separate. Changing a color must not change which fastener the object
   represents.
7. Fits and contacts are evaluated from standard-component semantics, not only
   from tessellated or lightweight geometry.

## 3. Goals

- Make standard hardware available in a clean VibeCAD installation.
- Give the AI a small, obvious, deterministic API for choosing hardware.
- Reject invalid standard/size/length combinations with useful allowed values.
- Preserve editable parameters through save, reopen, recompute, copy, and
  Assembly use.
- Make fasteners ordinary Assembly occurrences with stable connectors and BOM
  metadata.
- Derive compatible clearance, close-fit, tapped, countersunk, and counterbored
  holes from the selected standard where the catalog contains the necessary
  values.
- Keep release builds reproducible and usable offline.
- Preserve the existing Part Design and Assembly public surfaces.

## 4. Non-goals

- Generating a new standards database from prose or model knowledge.
- Claiming that every catalog choice is appropriate for a load case.
- Performing bolt preload, fatigue, joint-slip, or torque analysis in the
  initial implementation.
- Replacing VibeCAD material cards with fastener-specific material handling.
- Copying the Fasteners workbench's standards tables into VibeCAD source.
- Making real thread geometry the default.
- Silently substituting a nearby standard when a requested item does not exist.

## 5. Original current-state problem

Before this implementation, the upstream FreeCAD Fasteners workbench was
normally an add-on. It has its own
catalog, generators, commands, icons, translations, and recompute behavior.
VibeCAD did not bundle that module or expose a first-class standard component
in VibeScript. Consequently, an AI could spend tokens creating an
inferior approximation of a commodity item, and the resulting object lacked a
reliable standard identity for Assembly and BOM use.

This is a packaging, object-model, API, and validation problem. Copying a few
toolbar actions into Part Design would address only the visible symptom.

## 6. Source, licensing, and provenance

The initial upstream candidate is
[FreeCAD_FastenersWB](https://github.com/shaise/FreeCAD_FastenersWB), currently
published under GPL-2.0-or-later. VibeCAD's release owner must approve the
distribution and license boundary before the module is added to a release.
This specification is not legal advice.

The integration must:

- pin an exact reviewed upstream commit rather than downloading the current
  branch during a build;
- retain upstream copyright, license, source URL, and version information;
- keep the GPL module visibly separable from VibeCAD's LGPL source;
- record the pinned revision in VibeCAD's source and binary provenance;
- include the corresponding source in the distribution process where the
  license requires it;
- run the repository's license and notice checks against every packaged
  artifact.

VibeCAD-specific catalog adapters should be new VibeCAD code that calls the
published generator interface. GPL implementation code must not be copied into
an LGPL VibeCAD module. If upstream changes are required, VibeCAD should pin a
maintained fork with reviewable commits instead of carrying an opaque patch
applied during packaging.

If the owner rejects the GPL distribution boundary, the fallback is a separate
clean-room, license-compatible standard-component project. That is a different,
substantially larger effort and is not an interchangeable implementation detail.

## 7. Packaging architecture

### 7.1 Source layout

The selected Fasteners source should be tracked as a pinned repository
dependency, following the repository's existing third-party dependency pattern.
A VibeCAD-owned CMake/install adapter must install it as a normal FreeCAD module
under `Mod/Fasteners`.

The build must never:

- fetch the module from the network as part of a normal build;
- depend on a developer's Addon Manager state;
- modify files in an already installed VibeCAD tree;
- select a branch or tag whose target can move;
- omit the module from one supported package format without failing packaging.

### 7.2 Release contents

Every supported local and distributable build must contain:

- module initialization files;
- the complete supported catalog and generator;
- command implementations;
- icons and other UI resources;
- translations included by the pinned upstream revision;
- license, notice, version, and source-provenance files;
- VibeCAD's catalog adapter and tests.

The release manifest must report the bundled Fasteners revision. The application
About/diagnostics data should report it as well so a saved object's generator
can be identified from a support report.

### 7.3 Startup behavior

Missing or unloadable bundled Fasteners code is a release defect. Startup must
report one direct diagnostic containing the module path and import failure.
Part Design, Assembly, and VibeScript commands that depend on the catalog must
then be disabled explicitly; they must not fall back to approximating hardware.

## 8. Shared standard-component service

VibeCAD must add one service between the domain adapters and the bundled
generator. The service owns validation and canonical identity; it does not own
a second copy of the geometry formulas.

The service is responsible for:

- listing supported families and standards;
- deterministic catalog search;
- resolving a user-facing designation to one canonical parameter set;
- returning allowed diameters, lengths, pitches, and variants;
- constructing or recomputing geometry through the pinned generator;
- exposing standard hole, head-bearing, thread-axis, and shank interfaces;
- creating stable BOM and document metadata;
- reporting the catalog and generator versions used;
- applying explicit resource limits to expensive real-thread generation.

The service must be callable without loading GUI code. Headless VibeScript,
tests, and release validation must use exactly the same catalog as the GUI.

### 8.1 Canonical identity

Each component definition must have a stable canonical key containing at least:

- catalog identifier and schema version;
- standard designation;
- component family;
- nominal thread designation;
- length when the family has a length;
- pitch or pitch series when selectable;
- head, drive, tip, and other catalog variants when applicable;
- whether real thread geometry is generated (`model_thread`).

Labels are not identity. A user may rename `ISO 4762 M6 x 20` to `Motor mount
bolt` without losing its standard identity.

### 8.2 Catalog query

The AI-facing environment should provide one read-only catalog query,
conceptually:

```text
fastener_catalog.search(
    query,
    family=None,
    standard=None,
    nominal_thread=None,
    length_mm=None,
    limit=...
)
```

Results must be bounded, sorted deterministically, and contain canonical values
that can be passed directly to VibeScript. A no-match result must return the
nearest valid dimensions as data while still reporting that the requested item
does not exist. It must not silently select one of them.

The query description should be short: use it to find an exact published
standard component and its allowed dimensions. It is not a mechanical-design
advisor.

## 9. Native document object

A generated fastener must be a parametric FreeCAD feature with:

- the canonical identity properties from section 8.1;
- individual editable engineering parameters;
- a generated `Shape`;
- generator and catalog version properties;
- the native Fasteners `Thread` boolean property;
- standard interface metadata;
- material and appearance assignments using the existing VibeCAD contracts;
- BOM part number, description, and quantity identity;
- a clear error state if a later recompute cannot resolve its stored values.

Recompute must use the stored canonical values. A catalog update must not
silently replace a saved designation or migrate it to a different size. Any
required data migration must be versioned, explicit, tested, and dual-readable
for its support period.

The cached shape must be saved in the FCStd file. On a VibeCAD installation the
object must remain editable and recomputable. A generic FreeCAD installation
without the bundled module should still be able to display the last saved shape,
although parametric editing cannot be promised there.

No saved object may contain an absolute path to a developer checkout, add-on
directory, catalog file, or temporary BREP.

## 10. GUI integration

### 10.1 Fasteners workbench

The bundled workbench must remain visible in the normal workbench selector and
available for its complete supported catalog-oriented UI. VibeCAD must not
reduce it to a hidden dependency after shipping it.

### 10.2 Part Design

Part Design gets a `Standard Components` command group backed by the shared
service. The initial group includes:

- insert standard fastener;
- change standard or dimensions;
- toggle real thread geometry;
- create a matching standard hole;
- place or attach the component using a named interface.

The resulting object must follow VibeCAD's consolidated tree rules. It must not
create an unexpected legacy Part workbench container or place one object in two
`GeoFeatureGroup` owners.

### 10.3 Assembly

Assembly gets insertion/search commands that create normal Assembly
occurrences. A standard fastener is not a special visual-only object: it can be
placed, constrained, patterned, counted, hidden, exploded, and included in a
BOM using the same occurrence model as any authored part.

The complete Assembly behavior is defined in
[Assembly and Mechanism Integration](assembly-mechanism-integration-spec.md).

### 10.4 Icons and translations

Every shipped command must have a valid icon at every size expected by the
VibeCAD UI. VibeCAD-owned integrated commands must use VibeCAD's icon style;
upstream workbench commands may retain their licensed upstream resources.
Packaging tests must detect missing resource paths. User-visible VibeCAD strings
must be translatable.

## 11. VibeScript contract

The API is additive. Existing Part Design and Assembly functions, output types,
and saved programs remain valid.

### 11.1 Part Design

Part Design should add one obvious constructor:

```python
bolt = api.fastener(
    standard="ISO 4762",
    nominal_thread="M6",
    length_mm=20,
    model_thread=False,
    label="Motor mount bolt"
)
```

The returned value is a parametric feature accepted by the existing
`api.body(...)` publication path. Parameters that do not apply to a family are
omitted, not populated with meaningless defaults.

`model_thread` is strictly boolean. `False` generates the lightweight
unthreaded envelope and `True` generates real helical thread geometry. It maps
directly to the native Fasteners `Thread` property. There is no string-valued
representation mode and no compatibility alias for one.

Part Design should also add a derived-hole operation:

```python
part = api.fastener_hole(
    base=part,
    profile=locations,
    fastener=bolt,
    purpose="clearance",
    fit="normal",
    through_all=True,
    label="M6 normal-clearance holes"
)
```

`purpose` initially supports `clearance`, `tapped`, `counterbore`, and
`countersink` only where the catalog has the required data. The existing
`api.hole(...)` remains unchanged. The derived operation stores the resolved
dimensions so the result can be audited.

### 11.2 Assembly

Assembly should expose the same selection terms while returning a normal
`component_link` occurrence:

```python
bolt = api.fastener(
    standard="ISO 4762",
    nominal_thread="M6",
    length_mm=20,
    placement=[0, 0, 0],
    model_thread=False,
    label="Motor mount bolt"
)
```

The occurrence is valid wherever the current Assembly API accepts a component:
connectors, joints, assembly graphs, exploded views, simulation, and BOM. It
must expose named interfaces such as the thread axis, head bearing face, shank,
and tip without requiring topology names such as `Face12`.

Part Design and Assembly may return different domain value types because only
one VibeScript domain is active at a time. They must resolve the same arguments
to the same canonical component definition.

### 11.3 Validation and errors

The API must validate values before geometry generation. An error must name:

- the rejected argument;
- the requested value;
- the governing standard/family;
- the valid values or the catalog query needed to obtain them.

Examples of hard failures include an unsupported standard, invalid diameter,
length unavailable for the selected diameter, real threads exceeding a
declared resource limit, or a derived hole type not defined for the component.
There is no automatic nearest-size substitution.

## 12. Standard interfaces, fits, and contacts

Each family must expose the named interfaces that are physically meaningful for
that family. Examples include:

- thread or shank axis;
- head bearing plane;
- under-head plane;
- tip plane or point;
- nut bearing planes;
- washer bearing planes;
- nominal shank and thread envelopes.

Interface definitions belong to the shared catalog adapter, not to the
Part Design or Assembly adapter.

Fit checks must distinguish:

- clearance or close-fit shank in a hole;
- threaded engagement;
- head/countersink or head/counterbore seating;
- bearing-face contact;
- deliberate interference represented by real thread geometry.

A standard threaded engagement must not create a false mechanism collision when
catalog semantics establish a valid threaded engagement. Conversely, semantic
compatibility must not hide a collision between unrelated portions of the
components.

## 13. BOM and material behavior

A BOM row for a standard component must be grouped by canonical component
identity plus deliberate procurement distinctions such as material, coating,
and finish. Label and placement are not grouping keys.

The initial implementation may leave mass unknown when the selected material
does not define density. It must not invent density. When density is available,
mass is computed from the generated shape and recorded with the material source.

Appearance remains independent of material. Existing VibeCAD material and
appearance APIs apply without a second fastener-specific color system.

## 14. Security, determinism, and performance

- Catalog lookup and generation run locally with no network access.
- Input strings are catalog values, not module, class, file, or expression
  names that can be imported or evaluated.
- Generated geometry is deterministic for a pinned generator and canonical
  parameters.
- Real-thread generation has explicit time, memory, and object-count limits.
- Repeated identical definitions may share immutable generated-shape cache data,
  but document objects and Assembly occurrences retain independent placement and
  identity.
- Cache keys include generator revision and all geometry-affecting parameters.
- A cache miss or failure cannot return geometry from a different key.

## 15. Compatibility requirements

The owner explicitly rejected the unshipped three-state string design on
2026-07-24. It is removed rather than deprecated: no alias, parser, or
migration is provided. The released contract is the native
`model_thread: bool` described above.

- No existing public VibeScript function is removed, renamed, or given a
  different default.
- Existing Part Design `api.hole(...)` behavior is unchanged.
- Existing Assembly `api.component(...)` continues to accept stable document
  references exactly as it does now.
- Existing Assembly output types and result documents remain readable.
- Fastener metadata is versioned and unknown future fields are ignored where
  safe.
- The Fasteners module may not take ownership of an object already owned by a
  Part Design Body or Assembly group.
- Documents created before this integration open without migration.

Any later removal, rename, or incompatible schema change requires the explicit
owner approval described in `AGENTS.md`.

## 16. Delivery sequence

Each phase is a separate merge-ready change. A later phase must not be hidden
inside an earlier packaging change.

### Phase 1: dependency and release packaging

- Approve the source and license boundary.
- Pin the reviewed source revision.
- Add deterministic CMake/install packaging.
- Include resources, translations, notices, and provenance.
- Add headless import and GUI workbench package smoke tests.

Exit condition: a clean offline VibeCAD build can open the Fasteners workbench
and create, save, reopen, and recompute representative upstream objects.

### Phase 2: shared catalog and native VibeCAD object

- Add the headless catalog adapter and canonical identity.
- Add deterministic query and validation.
- Add the VibeCAD parametric object and named interfaces.
- Add material, appearance, and BOM metadata integration.

Exit condition: every published catalog key can be resolved and its
lightweight geometry generated through the shared service.

### Phase 3: Part Design integration

- Add the GUI command group.
- Add `api.fastener(...)`.
- Add `api.fastener_hole(...)`.
- Verify Body ownership, tree grouping, source embedding, and recompute.

Exit condition: an accepted VibeScript program creates an editable standard
component and derived hole that survive save/reopen and input changes.

### Phase 4: Assembly integration

- Add native occurrence insertion and named connectors.
- Add Assembly `api.fastener(...)`.
- Add BOM grouping, explode, solve, and simulation coverage.
- Add semantic fit/contact handling to mechanism verification.

Exit condition: a standard fastener behaves like a normal Assembly occurrence
without being recreated as generic geometry.

### Phase 5: catalog expansion and hardening

- Enable additional upstream families only after their validation matrix passes.
- Add performance baselines and real-thread limits per family.
- Complete packaged-platform and translation coverage.

No phase may claim support for a family that is omitted from the release test
matrix.

## 17. Verification and release gates

### 17.1 Catalog tests

- Enumerate every supported canonical catalog key.
- Generate the lightweight geometry for every key in sharded tests.
- Generate real threads for every thread family and a boundary sample of sizes,
  pitches, and lengths.
- Confirm every rejected dimension returns deterministic valid alternatives.
- Confirm search ordering and canonical keys do not depend on locale.

### 17.2 Document tests

- Create, save, close, reopen, recompute, edit, and save again.
- Copy between documents and relink in Assembly.
- Confirm the cached shape displays when the generator module is intentionally
  unavailable.
- Confirm no absolute source, build, home, or temporary path is stored.
- Confirm older metadata fixtures remain readable.

### 17.3 VibeScript tests

- Part Design creation and publication.
- Derived clearance, tapped, counterbore, and countersink holes.
- Invalid standards, sizes, and family-specific arguments.
- Material and appearance preservation.
- Accepted-result rollback when generation or validation fails.
- Source and inputs embedded in FCStd and available after reopening.

### 17.4 Assembly tests

- Insert, constrain, solve, pattern, suppress, explode, and count.
- Nested rigid and flexible assembly occurrences.
- Named connector stability after fastener parameter changes.
- BOM grouping across labels and placements.
- Valid threaded/clearance fits and real non-fastener collisions.

### 17.5 Packaging tests

- Clean offline build from the pinned source tree.
- Local development package and every supported release artifact.
- Headless module import and GUI workbench activation.
- All command icons and resource paths.
- License, notice, source-provenance, and package-manifest contents.

The release is blocked if a packaged build omits the module, cannot load its
catalog, produces a missing-resource command, or depends on Addon Manager.

## 18. Recorded implementation decisions

The implementation record at the start of this document fixes the distribution
boundary, pinned revision, supported catalog matrix, native thread boolean, and
real-thread limits. The public contracts are:

- `vibecad-fastener-catalog-v1`;
- `vibecad-standard-fastener-v1`;
- `vibecad-standard-component-interfaces-v1`;
- `vibecad-fastener-hole-v1`;
- Part Design `api.fastener(...)` and `api.fastener_hole(...)`;
- Assembly `api.fastener(...)`;
- read-only `fastener_catalog.search`.

Changing or removing those contracts requires the compatibility and owner
approval process in `AGENTS.md`.

## 19. Definition of done

This effort is complete only when:

- every supported VibeCAD package contains the pinned Fasteners module and works
  offline;
- Part Design, Assembly, and VibeScript all resolve hardware through one shared
  catalog service;
- no supported standard fastener must be reconstructed from primitive geometry;
- supported catalog variants pass the declared exhaustive generation tests;
- objects remain editable in VibeCAD and visible from their cached shape in
  generic FreeCAD;
- named interfaces, fit semantics, material, appearance, and BOM identity
  survive save/reopen and recompute;
- Assembly can solve, simulate, explode, and count standard occurrences;
- invalid catalog requests fail directly without substitution;
- license, source, and revision provenance is present in every release artifact.
