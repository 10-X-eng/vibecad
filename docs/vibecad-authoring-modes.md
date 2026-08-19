# VibeCAD authoring modes

VibeCAD offers two separate AI authoring systems. The human selects the system
in the Assistant header and selects the initial working domain with the VibeCAD
ribbon. The AI cannot change authoring systems. Native may move to another CAD
work domain between provider turns while completing the current build obligation;
the human can stop or redirect that work at any time.

## Native

Native edits ordinary CAD documents directly. At the start of each turn,
VibeCAD freezes the current ribbon, document identity, exact tool schemas, and
structural revision. Only complete capability families required by that ribbon
are declared to the provider. Model, Sketch, Assemble, Mesh, Analyze,
Manufacture, Drawing, and Parameters therefore remain separate, focused tool
surfaces.

Every mutation uses exact object or subelement targets, explicit units, one
verified transaction, and a concise receipt. The dispatcher revalidates the
chosen operation's original closed schema even when the provider receives a
compact multi-operation schema. Expensive mesh, solver, CAM, Drawing, and file
operations prepare work away from the UI thread and reauthorize the document
before commit.

A ribbon change invalidates the old surface. Whether the human changes the ribbon
or Native requests the next kind of CAD work, the next provider turn receives a
newly frozen surface containing only that ribbon's tools.

## VibeScript

VibeScript owns a durable source program, inputs, diagnostics, and accepted
outputs. The active workbench selects one source-backed domain and its exact
API. Candidate programs run in an isolated worker; only validated results are
published into the live document.

VibeScript remains the authority for its published outputs. Native direct edits
are not backpropagated into source. To edit those outputs manually, the human
must explicitly take manual Native control; returning to scripted authority
requires an explicit human decision and regeneration from source.

## External MCP control

External MCP control uses the same selected authoring mode and frozen ribbon
surface as the built-in assistant. `vibecad.read_workbench` is informational.
Native CAD work may change only through the published between-turn transition;
authoring mode remains a human choice. Any transition invalidates the old tool
surface before the next turn.

## Migration from the retired Native surface

The retired global direct-tool/workbench-pack surface is not compatible with
this architecture. Old Native conversations, tool names, compatibility aliases,
verbose result shapes, and provider-controlled workbench switching are not
carried forward. Start a new Native conversation, select the intended ribbon,
and use the newly declared focused tools. Existing CAD documents and human
ribbon commands remain supported; VibeScript tools and source-backed documents
are unchanged.
