# VibeCAD MCP control

VibeCAD can be controlled either by its built-in agent or by one external MCP
client. The modes are mutually exclusive: enabling MCP stops and disables the
built-in agent; disabling MCP shuts the local server down before the built-in
agent becomes available again.

## Connect a client

1. Open **Edit → Preferences → VibeCAD**.
2. Enable **External MCP control** and apply the preferences.
3. Wait for **MCP state** to show `mcp` and **MCP connection** to show
   `listening`.
4. Select **Copy connection JSON** and paste that configuration into the MCP
   client.

The server uses Streamable HTTP at `http://127.0.0.1:8765/mcp`. It accepts
connections only on the local machine and every request requires the generated
bearer token in the copied configuration. Preferences displays the complete
token and provides buttons to copy either the token or the complete connection
JSON. The token is stored in the operating system credential store—the same
secure storage used for VibeCAD API keys—and remains unchanged across MCP and
VibeCAD restarts. **Regenerate bearer token** is the only normal action that
rotates it; connected clients must then be updated.

## Tool behavior

The MCP client receives the exact frozen tool contracts for the authoring mode
and VibeCAD ribbon already selected by the human. In VibeScript mode these are
the active workbench's source-backed tools. In Native mode they are the complete
capability families for the current ribbon.

`vibecad.read_workbench` reports the active human-selected ribbon/workbench.
It is read-only. There is no MCP tool for changing the workbench, ribbon, or
authoring mode.

When the human changes ribbons, the prior frozen MCP surface becomes stale and
the client must begin its next turn from the newly listed tools. CAD calls use
the same exact schemas, revisions, transactions, cancellation, and
document-thread execution as the built-in agent. MCP calls are serialized, so
two external requests cannot mutate the document concurrently.

The MCP client supplies the model and reasoning. VibeCAD does not start an AI
provider for MCP requests. Tools whose purpose is to invoke an additional
internal model report `PROVIDER_CALL_DISABLED` in MCP mode.
