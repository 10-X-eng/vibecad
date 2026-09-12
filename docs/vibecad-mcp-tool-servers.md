# VibeCAD MCP tool servers

The built-in VibeCAD agent can call tools from MCP servers that you register in
Preferences. VibeCAD is the MCP **client** in this mode: it starts or connects
to each server, reads its tool list, and offers those tools to the active
provider (ChatGPT/Codex, Grok, OpenAI-compatible endpoints, Anthropic, or
Gemini) beside the normal VibeCAD CAD tools.

This is different from [External MCP control](vibecad-mcp-control.md), where an
outside MCP client drives VibeCAD and the built-in agent is disabled. Tool
servers extend the built-in agent; they never replace it.

## Register a server

1. Open **Edit → Preferences → VibeCAD → MCP**.
2. Under **External MCP tool servers**, choose a preset or select **Add** and
   fill in the fields:
   - **Name**: how the server appears to the agent. Tools are exposed as
     `mcp_<name>.<tool>`, so a server named `cua-driver` provides
     `mcp_cua_driver.screenshot`.
   - **Transport**: `stdio` runs a local command; `http` connects to a
     Streamable HTTP endpoint.
   - **Command** and **Arguments** for `stdio`, or **URL** and **HTTP headers**
     for `http`.
   - **Environment**: extra `NAME=value` lines for a `stdio` command. Values
     such as `${MY_TOKEN}` are read from the VibeCAD process environment when
     the server starts, so secrets do not need to be written into preferences.
   - **Tools**: an optional comma-separated allowlist when a server advertises
     more tools than the agent needs.
   - **Tool timeout**: how long one call may run before VibeCAD reports
     `MCP_TOOL_TIMEOUT` to the model.
3. Select **Test connection** to start the server and list its tools.
4. Select **Apply** or **OK**.

Registrations are stored as JSON under the `MCPToolServers` preference key.
The same list also accepts the `mcpServers` object format used by other MCP
clients when it is pasted into that preference.

### Presets

- **Add cua-driver** registers `cua-driver mcp`, the [Cua Driver](https://cua.ai/cua-driver)
  desktop automation server. Install it first with the command from its
  documentation. On Linux the agent can then target a browser window, take
  screenshots, and click without moving your cursor.
- **Add browser (Playwright)** registers the official Playwright MCP server
  through `npx -y @playwright/mcp@latest`. Node.js must be installed.
- **Add project folder…** registers the reference filesystem server for one
  folder, so the agent can read datasheets, downloaded models, and BOM files.

## What the agent sees

- External tools are declared after the frozen VibeCAD tool surface and use the
  `mcp_<server>` namespace. The CAD surface, its digests, and its authorization
  checks are unchanged.
- A short system-instruction section lists the connected servers and tells the
  model that external tools never edit the CAD document, that their output is
  untrusted data, and that failures must be reported plainly.
- Tool results keep text and structured content. Image results, such as a
  screenshot, are saved under `~/.vibecad/mcp-tool-servers/images` and shown to
  the model through the same path as viewport captures.
- Failures use the normal VibeCAD tool-failure contract with the codes
  `MCP_TOOL_ERROR`, `MCP_TOOL_TIMEOUT`, `MCP_TOOL_CALL_FAILED`, and
  `MCP_SERVER_UNAVAILABLE`.

Servers connect on the first turn that needs them and stay connected for the
rest of the VibeCAD session. A server that fails to start is skipped for one
minute and reported in the assistant panel; the CAD turn continues without it.
Standard error from `stdio` servers is written to
`~/.vibecad/mcp-tool-servers/logs/<server>.stderr.log`.

## Example: find a model online, download it, import it

With the browser and project-folder presets registered (or cua-driver driving a
signed-in browser), a request such as *"find an L bracket on GrabCAD, download
the STL into my project folder, and import it"* runs like this:

1. The agent searches with the browser tools (`mcp_playwright.browser_navigate`,
   `browser_snapshot`, `browser_click`) and opens the model page.
2. It downloads the file. Playwright saves downloads into the folder given by
   `--output-dir`; point that at the same folder as the project-folder server.
3. It confirms the file with `mcp_project_files.list_directory`.
4. It imports the mesh through the Mesh ribbon's native `mesh.io` tool
   (`import_mesh` accepts STL, OBJ, 3MF, PLY, and related formats) and then
   continues with VibeCAD's own tools to position or link the imported object.
   STEP and IGES files are imported through **File → Import** today; the agent
   works with the imported part afterwards.

GrabCAD requires a signed-in account to download. Use a browser profile that is
already signed in (`--user-data-dir` for Playwright, or cua-driver targeting
your normal browser window) rather than storing credentials in VibeCAD.

## Security notes

- A `stdio` registration runs the command you entered with your user account.
  Register only servers you trust, exactly as their documentation describes.
- HTTP headers and environment values are stored in plain preferences unless
  you use `${NAME}` references to the process environment.
- External tool output is delivered to the model as data. VibeCAD does not
  execute instructions found in that output, and the system instructions tell
  the model not to either.
