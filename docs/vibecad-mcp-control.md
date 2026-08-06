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

The MCP client receives the active workbench's exact VibeScript tool contracts,
plus:

- `vibecad.read_workbench` lists the active and available workbenches.
- `vibecad.switch_workbench` activates one exact listed workbench.

Changing workbenches changes the MCP tool list. CAD calls use the same schema
validation, source revisions, transactions, cancellation, and document-thread
execution as the built-in agent. MCP calls are serialized, so two external
requests cannot mutate the document concurrently.

The MCP client supplies the model and reasoning. VibeCAD does not start an AI
provider for MCP requests. Tools whose purpose is to invoke an additional
internal model report `PROVIDER_CALL_DISABLED` in MCP mode.
