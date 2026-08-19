# Native complex-design workflow

Native can build a product larger than one assistant turn. Each turn must leave a
verified, editable result in the document. The next turn reads that document, so
progress does not depend on chat history and can resume after reopening the file or
moving it to another machine.

## Customer flow

1. Create or open a document, select Native, and attach any reference material.
2. State the complete outcome in ordinary engineering language. For example:

   `Make me a realistic parametric two-spool high-bypass jet engine. Create all sketches and parts, assemble the static structure and both spools, verify it, and produce a drawing and BOM.`

3. VibeCAD completes and verifies durable design phases. When another kind of CAD
   work is required, it changes the active work between provider turns and resumes
   with that exact tool surface. The visible ribbon always shows what is active,
   and the customer can stop or redirect the run.
4. When a bounded run ends at a verified milestone, reply:

   `Continue the jet-engine design from the current document.`

5. Repeat until VibeCAD reports the final document checks, drawing, and BOM.
   Opening or leaving a Sketch and moving between product domains both continue
   with a newly frozen tool surface; a surface never changes inside one provider
   turn.

The follow-up contains no tool names or modeling instructions. The current
parameters, sketches, Bodies, Components, joints, and checks are the durable source
of truth.

## Jet-engine phases

### 1. Master definition

Create named engine parameters and fully constrained master sketches for the
engine axis, overall envelope, axial stations, annular flowpath boundaries, shaft
diameters, bearing stations, and rotor-row locations. Record any engineering
assumptions as parameters. Verify constraints and envelopes before creating solid
geometry.

### 2. Static structure

Create editable Components and Bodies for the inlet, nacelle, cases, bypass and
core ducts, splitter, combustor structure, bearing supports, and exhaust. Build
annular parts from their master profiles and preserve the master references.

### 3. Rotating structure

Create separate low- and high-pressure shaft, disk, and coupling Components. The
shafts must remain concentric and physically separate. Create and verify each fan,
compressor, and turbine blade master from appropriate curved and tapered sections
before patterning it into a row.

### 4. Flowpath population

Create representative rotor and stator rows, liners, vanes, and supports. Reuse
verified masters where the design calls for repeated geometry, while retaining
distinct component identities and quantities required by the assembly and BOM.

### 5. Assembly

Place the static structure, low spool, and high spool in an assembly. Fix the
static structure and constrain each spool to its intended independent rotation
without axial travel. Solve the assembly and inspect its remaining freedoms.

### 6. Verification

Check solid validity, expected solid and occurrence counts, overall dimensions,
flowpath clearances, concentric shaft clearances, and unintended interference.
Correct the document rather than explaining away a failed check.

### 7. Deliverables

Create the requested assembly and component views, dimensions, sections, balloons,
and an expandable BOM. Verify that drawing identities, balloon item numbers, and
BOM quantities agree with the final assembly.

## Completion contract

The design is complete only when the document contains:

- fully constrained master and component sketches;
- editable feature histories for every modeled part;
- correctly identified Components and occurrences;
- a solved assembly with only the intended freedoms;
- passing geometry, clearance, and interference checks;
- the requested drawing and expandable BOM.

A screenshot or assistant message is not completion evidence. The saved FCStd
document and its measured state are authoritative.
