# VibeCAD Runtime Verification

The VibeCAD assistant has two explicit, human-selected authoring modes:

- **VibeScript** owns source-backed programs and publishes validated outputs.
- **Native** edits ordinary CAD state through the exact complete tool families
  on the current human-selected VibeCAD ribbon.

Neither the built-in provider nor an external MCP client can switch the active
workbench, ribbon, or authoring mode. A human change invalidates the frozen
turn and the next turn receives the newly resolved surface.

## Provider surface

- In VibeScript mode every supported workbench exposes `vibescript.read_source`,
  `vibescript.read_api`, and
  `vibescript.edit_source` as the normal existing-source path, plus the active
  domain's explicit create, input-only, reconfigure, and delete operations.
- In Native mode the surface contains only fully implemented capability
  families required by the current ribbon. Any missing definition,
  implementation, exact target, or schema budget fails the whole surface
  closed before provider launch.
- The turn packet lists the editable VibeScript programs owned by the active
  workbench. Each entry includes its stable source ID, current revision, label,
  and the exact read/edit tool names.
- Source reads and edits are per program. `read_source` returns complete source;
  `edit_source` accepts complete replacement source under the current revision
  guard.
- `read_api` returns only the active workbench's VibeScript API.
- Provider dispatch rejects undeclared tools and stale workbench or surface
  snapshots.

The cross-ribbon Codex acceptance gate retains one saved document and one
conversation while the human UI selects each permanent ribbon plus Sketch
setup and Sketch edit. On every turn it traverses the production Codex adapter
and document-thread bridge, invokes the declared `state.read` tool from a
nested app-server callback, and verifies that the returned domain matches the
newly selected surface. This is the executable inter-turn tool-swap contract;
tools never change during a turn. Its opt-in live mode applies the same checks
to the configured ChatGPT Codex provider and additionally requires exactly one
read-only state call with no mutation calls on every surface.

## Native execution

Native freezes the human-selected ribbon, ordered provider schemas, document
identity, and structural revision at turn start. Every call is validated first
against its compact provider contract and then against the selected operation's
original closed schema. Exact target hashes and the live ribbon are rechecked
before a transaction or background job starts. Immediate mutations create one
verified transaction receipt; expensive preparation runs detached from the UI
thread and commits only after document-thread reauthorization. Results are
concise, while opt-in debug capture retains full bounded diagnostics.

Native never backpropagates direct edits into VibeScript source. A document
with active VibeScript authority must be deliberately transferred to manual
Native control by the human.

## VibeScript execution

VibeScript candidates execute in a windowless isolated worker with bounded
time and memory. The worker receives immutable document metadata, validated
inputs, and only the selected workbench API. Imports, arbitrary filesystem or
network access, GUI access, and unrestricted document mutation are rejected.

The live document receives only validated native outputs. Publication uses
stable program/output identities, revision guards, document-thread dispatch,
transaction rollback, and explicit restoration when native rollback is
incomplete. Failed and not-yet-published programs remain in the assistant's
editable-source index even when they have no live outputs. Their source and
diagnostics remain readable without replacing accepted geometry.

Accepted source, input schema, inputs, expected outputs, and editor drafts are
embedded in the FreeCAD document so source-backed models remain portable across
computers. Save/reopen tests verify that accepted geometry renders immediately
and that the editor can recover the owning program without an external source
file.

## Packaged runtime checks

Release bundles verify:

- the command-line application starts;
- required Python dependencies and the platform keyring backend import from the
  bundle;
- the provider subprocess starts with the required process model;
- the bundled Codex app server starts and reports its version; and
- the VibeScript worker and representative workbench integrations pass their
  source, validation, publication, rollback, save/reopen, and deletion tests.

The local release builder runs the same command-line, dependency, provider, and
Codex smoke checks against the completed release tree.
