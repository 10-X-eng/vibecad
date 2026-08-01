# VibeCAD Runtime Verification

The VibeCAD assistant authors through VibeScript only. Native FreeCAD commands
remain human ribbon actions.

## Provider surface

- Every supported workbench exposes `vibescript.read_source`,
  `vibescript.read_api`, and
  `vibescript.edit_source` as the normal existing-source path, plus the active
  domain's explicit create, input-only, reconfigure, and delete operations.
- The surface may include only the focused read tools owned by the active
  workbench; human ribbon mutation commands are never provider-callable.
- The turn packet lists the editable VibeScript programs owned by the active
  workbench. Each entry includes its stable source ID, current revision, label,
  and the exact read/edit tool names.
- Source reads and edits are per program. `read_source` returns complete source;
  `edit_source` accepts complete replacement source under the current revision
  guard.
- `read_api` returns only the active workbench's VibeScript API.
- Provider dispatch rejects undeclared tools and stale workbench or surface
  snapshots.

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
